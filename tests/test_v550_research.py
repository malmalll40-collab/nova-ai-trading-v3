from datetime import timedelta
import pandas as pd
import pytest
from nova_v550_research import candidate_flags,probability,change_metrics,historical_probability
from nova_v541_core import CANDIDATES
import core550_evidence as ev
from tests.test_v541_core_adaptive import obs,NOW


def flags(**overrides):
    d=dict(base_buy=False,regime='range',market_regime='range',market_vol=.015,checks={'risk':True},close=100,ema20=99,ema50=101,atr=3,volume_ratio=1.6,rsi=60,adx=22,previous_adx=19,directional=True,slope=.01,previous_slope=-.01,breakout=True,rel_strength=.04)
    return candidate_flags(**{**d,**overrides})

@pytest.mark.parametrize('changes',[{'regime':'down'},{'market_regime':'down'},{'market_vol':.05},{'checks':{'risk':False}},{'adx':float('nan')}])
def test_risk_veto(changes):assert not any(flags(**changes).values())

def test_regime_candidates():
    assert all(flags().values());assert not any(flags(breakout=False).values())
    assert flags(previous_slope=.005)=={'range_guard':True,'early_turn':False,'regime_switch':True}
    assert all(flags(base_buy=True,regime='strong_up').values())
    assert not any(flags(rel_strength=-.01).values())

def test_opportunity_accounting():
    assert change_metrics({1:1,2:0,3:1,4:0},{1:'BUY',2:'BUY'},{3:'BUY',4:'BUY'})==dict(added_positive=1,added_negative=1,blocked_positive=1,blocked_negative=1)

def test_probability_requires_samples_and_shrinks():
    assert probability([('a',1)]*39,'a') is None
    assert probability([('a',1)]*9+[('b',0)]*40,'a') is None
    p=probability([('a',1)]*20+[('b',0)]*20,'a');assert p['naive']==.5 and .5<p['candidate']<1

def test_historical_prob_strictly_prior(monkeypatch):
    import nova_v550_research as m
    seen=[]
    def capture(train,g):
        rows=list(train);seen.append(len(rows));return None
    monkeypatch.setattr(m,'probability',capture)
    pts={i:{'regime':'range','signals':{'core':'BUY'}} for i in range(50)}
    m.historical_probability(pts,{i:(None,1,0.1) for i in pts},5)
    assert seen[5]==0 and seen[6]==1 and seen[-1]==44

def observation(now):
    day=now.date().isoformat();a=obs(day);a['core541_current'].update(hybrid_signal='BUY',candidates=dict.fromkeys(CANDIDATES,'BUY'),subregime='up_transition');return a

def test_prospective_isolated_immutable(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    now=NOW+timedelta(days=1);a=observation(now);assert ev.record(a,now);a['chart']['close']=[999];assert not ev.record(a,now)
    assert ev.snapshot(a,now.replace(hour=17)) is None
    with ev.connection() as (db,kind):
        rows=ev._read(db.cursor(),kind,'005930.KS');assert rows[0]['close']==110 and rows[0]['probability'] is None
        assert set(rows[0]['signals'])==set(ev.KEYS)
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='nova_core542_observations'").fetchall()

def test_prospective_missing_and_actual_outcome(monkeypatch):
    import core541_evidence as old
    class Cal:
        def sessions_in_range(self,a,b):return pd.bdate_range(a,b)
    monkeypatch.setattr(old,'get_krx_calendar',lambda _:Cal())
    dates=[NOW+timedelta(days=i) for i in (1,2,8)];a,b,c=[ev.snapshot(observation(d),d) for d in dates]
    assert ev.summarize([a,b],dates[-1])['rows'][0]['state']=='MISSING_VERIFIED_BARS'
    r=ev.summarize([a,b,c],dates[-1]);assert r['completed']==1 and r['metrics']['regime_switch']['positive']==1 and r['probability']['samples']==0
    assert r['strategy_adoption']=='HOLD'

def test_forecast_excludes_current_exit_and_freezes_cost(monkeypatch):
    now=NOW+timedelta(days=2);s=ev.snapshot(observation(now),now);old=ev.snapshot(observation(NOW),NOW)
    monkeypatch.setattr(ev,'_outcomes',lambda r,n:[{'state':'COMPLETE','day':old['day'],'exit_day':s['day'],'net_positive':True}])
    monkeypatch.setattr(ev,'probability',lambda train,g:list(train))
    assert ev.forecast(s,[old],now)==[]

def test_api_disabled(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False);monkeypatch.delenv('NOVA_CORE541_EVIDENCE_ENABLED',raising=False)
    r=TestClient(server.app).get('/api/research/core550/evidence?symbol=005930.KS');assert r.status_code==200 and r.json()['status']=='DISABLED'
    assert server.healthz()['v550_live_adoption'] is False
