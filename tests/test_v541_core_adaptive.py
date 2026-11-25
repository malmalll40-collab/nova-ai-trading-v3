import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from engine import feature_frame,Config
from tests.test_core_comparison_473 import sample_frames
from core_comparison import _reference, _core_at
from nova_v541_core import point,indicators,study,batch_summary,_rule,NAMES
from nova_v54_accuracy import analyze_accuracy
from core541_evidence import snapshot,record,report,outcomes


def test_core_matches_existing_and_no_lookahead():
    f,b=sample_frames(400);market,_=_reference(b);i=300
    a=point(f,i,market);assert a['status']=='READY'
    assert a['signals']['core']==_core_at(f,i,market)['signal']
    raw=f[['Open','High','Low','Close','Volume']].copy();raw.iloc[i+1:,:4]*=3
    changed=feature_frame(raw,'스윙'); changed_market=dict(market)
    for t in b.index[i:]:changed_market[t.date().isoformat()]=1e6
    assert a==point(changed,i,changed_market)
    assert a==point(f.iloc[:i+1],i,market)


def test_opportunities_exact_and_missing_market():
    f,b=sample_frames(400);cfg=Config.for_mode('스윙')
    r=analyze_accuracy(f,cfg,source='kis',benchmark=b);a=r['core_focus'];assert a['status']=='READY'
    assert a['strategies']['core']['portfolio']['portfolio_return_pct']==r['strategies']['five_core']['portfolio']['portfolio_return_pct']
    assert sum(x['samples'] for x in a['regimes'].values())==a['samples']==r['paired_samples']
    assert sum(x['samples'] for x in a['periods'])==a['samples']
    assert sum(x['samples'] for x in a['market_regimes'].values())==a['samples']
    for k in ('strength','adaptive'):
        assert a['changes'][k]['added_signals']==0
        assert a['strategies'][k]['opportunities']['buy_signals']+a['changes'][k]['blocked_positive']+a['changes'][k]['blocked_negative']==a['strategies']['core']['opportunities']['buy_signals']
    assert len(a['ablations'])==5 and a['strategy_adoption']=='HOLD'
    assert analyze_accuracy(f,cfg,source='kis')['core_focus']['status']=='UNAVAILABLE'
    z=batch_summary([{'v54_accuracy':r},{'v54_accuracy':r},{}]);assert z['ready_stocks']==2 and z['groups'][0]['stocks']==2


def test_optional_factor_removal_keeps_other_branch():
    z=dict(market=True,trend=True,risk=True,strength=True,entry=False)
    assert _rule(z) and not _rule(z,'strength') and _rule(z,'entry')
    z['risk']=False;assert not _rule(z) and _rule(z,'risk')


def test_adaptive_no_buy_in_down_or_range_without_breakout(monkeypatch):
    f,b=sample_frames(400);market,_=_reference(b);i=300
    import nova_v541_core as m
    monkeypatch.setattr(m,'_core_at',lambda *a:{'signal':'BUY','status':'READY','checks':[{'key':k,'pass':True} for k in m.FACTORS]})
    ind=indicators(f);ind.loc[f.index[i],['adx','slope','width','width_median']]=[10,.01,.01,.1]
    f.loc[f.index[i],'p_high20']=f.Close.iloc[i]*2
    p=point(f,i,market,ind);assert p['regime']=='range' and p['signals']['adaptive']=='HOLD'
    f.loc[f.index[i],'p_high20']=f.Close.iloc[i]*.99;f.loc[f.index[i],'vol_ratio']=2
    ind.loc[f.index[i],['plus_di','minus_di']]=[35,10]
    assert point(f,i,market,ind)['signals']['adaptive']=='BUY'
    ind.loc[f.index[i],'slope']=-.01;f.loc[f.index[i],'ema50']=f.Close.iloc[i]*2
    assert point(f,i,market,ind)['regime']=='down' and point(f,i,market,ind)['signals']['adaptive']=='HOLD'

KST=ZoneInfo('Asia/Seoul');NOW=datetime(2026,11,24,19,tzinfo=KST)
def obs(day='2026-11-24'):
    return {'symbol':'005930.KS','mode':'스윙','source':'kis','is_stale':False,'last_completed_bar':day+'T00:00:00','cost_bps_one_side':15,'daily_integrity':{'verified':True,'regular_volume_verified':True,'verification_basis':'KIS_REGULAR_MINUTE','latest_date':day},'completed_session':{'calendar_verified':True,'expected_completed_session':day},'core541_current':{'status':'READY','day':day,'signals':{k:'BUY' for k in NAMES},'regime':'range'},'chart':{'dates':[day],'open':[100],'close':[110]}}
def test_prospective_immutable_opt_in(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'obs.sqlite'));monkeypatch.delenv('NOVA_CORE541_EVIDENCE_ENABLED',raising=False)
    a=obs();assert snapshot(a,NOW);assert not record(a,NOW)
    monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1');assert record(a,NOW);a['chart']['close']=[999];assert not record(a,NOW)
    assert snapshot(a,NOW+timedelta(days=1)) is None and snapshot(a,NOW.replace(hour=17)) is None
    a['daily_integrity']['regular_volume_verified']=False;assert snapshot(a,NOW) is None
    assert report('005930.KS')['verified_days']==1

def test_prospective_requires_observed_entry_and_exit(monkeypatch):
    import core541_evidence as m
    class Calendar:
        def sessions_in_range(self,a,b):return pd.bdate_range(a,b)
    monkeypatch.setattr(m,'get_krx_calendar',lambda _:Calendar())
    a=snapshot(obs(),NOW);entry=snapshot(obs('2026-11-25'),NOW+timedelta(days=1));end=snapshot(obs('2026-12-01'),NOW+timedelta(days=7))
    r=outcomes([a,entry],NOW+timedelta(days=7));assert r['rows'][0]['state']=='MISSING_VERIFIED_BARS'
    r=outcomes([a,entry,end],NOW+timedelta(days=7));assert r['rows'][0]['state']=='COMPLETE' and r['rows'][0]['net_return_pct']>0


def test_ui_and_health():
    from pathlib import Path
    import server
    html=Path('static/index.html').read_text();js=Path('static/v5_dashboard.js').read_text()
    assert html.index('id="view-research"')<html.index('id="core541CurrentPanel"')<html.index('id="view-watchlist"')
    assert 'coreFocusPanel(box,data.core_focus)' in js and 'extended_core_study:symbols.length>5' in js
    assert server.healthz()['v541_core_adaptive_research'] and not server.healthz()['v541_core_live_adoption']
