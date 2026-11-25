from datetime import datetime, timedelta
import pandas as pd
import pytest
from nova_v552_research import select,signal_audit
import analysis_chart_live as live
from market_sessions import KST
from tests.test_v550_research import observation,NOW
import core552_evidence as ev
import core550_evidence as old

@pytest.mark.parametrize('regime,route,buy',[('strong_up','core',True),('mild_up','rs_hybrid',False),('sideways','rs_required',True),('up_transition','rs_hybrid',False),('down','risk_veto',False)])
def test_router(regime,route,buy):
    flags,actual=select(candidates={'rs_required':True,'transition_soft':False},core=True,subregime=regime,market_regime='range',risk=True,market_gate=True)
    assert actual==route and flags['regime_router']==buy and not flags['rs_hybrid']

def test_intersection_and_veto():
    kw=dict(candidates={'rs_required':True,'transition_soft':True},core=True,subregime='up_transition',market_regime='range',risk=True,market_gate=True)
    assert select(**kw)[0]=={'rs_hybrid':True,'regime_router':True}
    for gate in ('risk','market_gate'):
        assert not select(**{**kw,gate:False})[0]['regime_router']
    assert not select(**{**kw,'market_regime':'down'})[0]['regime_router']

def test_audit_distinguishes_no_trade_and_identical():
    r=signal_audit([{'a':'BUY','b':'BUY','c':'HOLD','d':'HOLD'},dict.fromkeys('abcd','HOLD')]);p={(x['a'],x['b']):x for x in r['pairs']}
    assert p['a','b']['identical_observed'] and p['a','b']['jaccard_pct']==100
    assert p['c','d']['jaccard_pct'] is None and not p['c','d']['identical_observed']
    assert p['a','c']['only_a']==1 and p['a','c']['jaccard_pct']==0

def bars():
    dates=pd.to_datetime(['2026-11-24 09:00','2026-11-25 08:59','2026-11-25 09:00','2026-11-25 09:01','2026-11-25 09:02','2026-11-25 09:06','2026-11-25 16:00']).tz_localize(KST)
    return pd.DataFrame({'Open':[100]*7,'High':[105]*7,'Low':[99]*7,'Close':[102]*7,'Volume':[10]*7},index=dates)
UNKNOWN={'calendar_verified':False,'status':'UNKNOWN'}
@pytest.mark.parametrize('mode,n,vol',[('1분',3,10),('5분',1,30),('15분',1,30)])
def test_intraday_actual_rows(mode,n,vol):
    r=live.minute_rows(bars(),mode,datetime(2026,11,25,9,2,30,tzinfo=KST),UNKNOWN)
    assert len(r)==n and r[-1]['bar']['volume']==vol and r[-1]['partial']
    assert all(x['date'].startswith('2026-11-25T09:') for x in r)

def test_holiday_no_rows():
    assert live.minute_rows(bars(),'1분',datetime(2026,11,25,10,tzinfo=KST),{'calendar_verified':True})==[]

def test_swing_live_reuses_working_daily_snapshot(monkeypatch):
    monkeypatch.setattr(live,'krx_session',lambda *_:UNKNOWN)
    monkeypatch.setattr('higher_timeframes.latest_kis_period',lambda *a,**k:{'available':True,'date':'2026-11-25','bar':dict(open=100,high=105,low=99,close=102,volume=100)})
    r=live.fetch('005930.KS','스윙','key','secret',datetime(2026,11,25,10,tzinfo=KST))
    assert r['available'] and r['rows'][0]['partial'] and r['display_only']

def test_version_ledger_isolation(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    now=NOW+timedelta(days=1);a=observation(now);assert ev.record(a,now);assert old.record(a,now);assert not ev.record(a,now)
    with ev.connection() as (db,kind):
        rows=ev._read(db.cursor(),kind,'005930.KS');legacy=old._read(db.cursor(),kind,'005930.KS')
    assert rows[0]['version']=='5.5.2' and 'rs_hybrid' in rows[0]['signals'];assert legacy[0]['version']=='5.5.0' and 'rs_hybrid' not in legacy[0]['signals']
    assert ev.snapshot(a,now.replace(hour=17)) is None

def test_prospective_outcomes_and_nonoverlap(monkeypatch):
    import core541_evidence as e
    class Cal:
        def sessions_in_range(self,a,b):return pd.bdate_range(a,b)
    monkeypatch.setattr(e,'get_krx_calendar',lambda _:Cal())
    dates=[NOW+timedelta(days=i) for i in (1,2,8)];rows=[ev.snapshot(observation(d),d) for d in dates]
    r=ev.summarize(rows,dates[-1]);assert r['completed']==1 and r['nonoverlap']['samples']==1 and r['metrics']['rs_hybrid']['positive']==1
    assert r['strategy_adoption']=='HOLD' and r['validation_state']=='COLLECTING'

def test_old_range_and_turn_not_logically_equivalent():
    from tests.test_v550_research import flags
    # ADX [18,20): early turn can qualify when range guard does not.
    r=flags(adx=19,previous_adx=18)
    assert r=={'range_guard':False,'early_turn':True,'regime_switch':True}
    # Prior slope already positive: range guard may qualify without an early turn.
    r=flags(previous_slope=.005)
    assert r['range_guard'] and not r['early_turn']

def test_new_forward_cost_frozen():
    a=observation(NOW);a['cost_bps_one_side']=30
    assert ev.snapshot(a,NOW) is None

def test_analysis_chart_api_contract(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv('KIS_APP_KEY','test');monkeypatch.setenv('KIS_APP_SECRET','test');monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(live,'fetch',lambda s,m,*a:{'symbol':s,'mode':m,'display_only':True,'rows':[]})
    server.CACHE.clear();server.RATE.clear()
    c=TestClient(server.app)
    assert c.get('/api/chart/analysis-live?symbol=005930.KS&mode=스윙').json()['display_only']
    assert c.get('/api/chart/analysis-live?symbol=005930.KS&mode=bad').status_code==422
    assert c.get('/api/research/core552/evidence?symbol=005930.KS').status_code==200
    assert server.healthz()['v552_live_adoption'] is False
    server.CACHE.clear();server.RATE.clear()
