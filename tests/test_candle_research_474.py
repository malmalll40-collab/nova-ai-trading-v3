from __future__ import annotations
from dataclasses import replace
from datetime import date
import numpy as np
import pandas as pd

from engine import Config, feature_frame, decide
from candle_research import build_candle_research
from core_comparison import _core_at, _reference


def sample_frames(n=225):
    idx=pd.bdate_range('2025-10-01',periods=n)
    a=np.arange(n,dtype=float)
    close=100+.17*a+2.8*np.sin(a/8)
    op=close*(1-.001*np.cos(a/4))
    bars=pd.DataFrame({'Open':op,'High':np.maximum(op,close)+2.2,
        'Low':np.minimum(op,close)-2.2,'Close':close,
        'Volume':15000+8000*(1+np.sin(a/6))},index=idx)
    index=pd.DataFrame({'Close':2600+2*a+5*np.sin(a/11)},index=idx)
    return feature_frame(bars,'스윙'),index


def test_historical_marker_and_vote_detail_match_independent_engine():
    f,index=sample_frames()
    cfg=Config.for_mode('스윙')
    data=build_candle_research(f,cfg,benchmark=index,source='kis')
    assert data['status']=='READY' and data['modifies_live_signal'] is False
    assert 10 < len(data['observations']) <= 125
    rows=data['observations']
    assert all(len(r['vote_details'])==13 for r in rows)
    assert rows[-1]['outcome_status']=='PENDING'
    assert rows[-6]['outcome_status']=='EVALUATED'
    assert rows[-6]['next_open']==round(float(f['Open'].iloc[-5]),3)
    sample=rows[-8];i=next(i for i,x in enumerate(f.index) if x.date().isoformat()==sample['date'])
    decision=decide(f,cfg,i)
    assert sample['baseline']==decision.signal
    assert sample['buy_votes']==decision.buy_votes
    assert [v['direction'] for v in sample['vote_details']]==[
        'BUY' if v.direction==1 else 'SELL' if v.direction==-1 else 'HOLD' for v in decision.votes]
    market,_=_reference(index)
    core=_core_at(f,i,market)
    assert sample['five_core']==(core['signal'] if core['status']=='READY' else None)
    assert sample['five_core_checks']==core.get('checks',[])


def test_future_bars_cannot_change_past_votes():
    f,index=sample_frames()
    cfg=Config.for_mode('스윙')
    original=build_candle_research(f,cfg,benchmark=index,source='yahoo')
    modified=f.copy()
    modified.loc[modified.index[-1],'Close']*=1.4
    modified.loc[modified.index[-1],'High']*=1.4
    result=build_candle_research(modified,cfg,benchmark=index,source='yahoo')
    by_day={r['date']:r for r in result['observations']}
    a=original['observations'][-10]
    b=by_day[a['date']]
    assert a['vote_details']==b['vote_details']
    assert a['baseline']==b['baseline']
    assert a['five_core']==b['five_core']
    assert a['outcome_status']==b['outcome_status']


def test_missing_benchmark_does_not_invent_5core_or_block_baseline():
    f,_=sample_frames()
    result=build_candle_research(f,Config.for_mode('스윙'),benchmark=None,source='kis')
    assert result['status']=='READY'
    assert result['market_reference_status']=='UNAVAILABLE'
    assert all(r['five_core'] is None for r in result['observations'])
    assert all(r['five_core_checks']==[] for r in result['observations'])
    assert all(r['baseline'] in ('BUY','SELL','HOLD') for r in result['observations'])


def test_non_swing_or_demo_does_not_create_history():
    f,index=sample_frames()
    for cfg,kw in [(Config.for_mode('1분'),{}),(Config.for_mode('스윙'),{'is_demo':True}),
                   (Config.for_mode('스윙'),{'asof_date':'20261120'})]:
        a=build_candle_research(f,cfg,benchmark=index,source='kis',**kw)
        assert a['status']=='NOT_APPLICABLE'
        assert not a['observations']


def test_role_results_do_not_present_risk_veto_or_tiny_samples_as_independent_alpha():
    f,index=sample_frames()
    r=build_candle_research(f,Config.for_mode('스윙'),benchmark=index,source='kis')
    assert len(r['role_statistics'])==13
    assert r['role_statistics'][10]['kind']=='derived'
    assert r['role_statistics'][11]['kind']=='derived'
    assert r['role_statistics'][12]['kind']=='risk_veto'
    assert r['role_statistics'][12]['directional_samples']==0
    for x in r['role_statistics']:
        if x['directional_samples']<10:assert x['directional_hit_pct'] is None


def test_last_five_completed_sessions_wait_for_outcomes():
    f,index=sample_frames()
    r=build_candle_research(f,Config.for_mode('스윙'),benchmark=index,source='kis')
    assert [x['outcome_status'] for x in r['observations'][-5:]] == ['PENDING']*5
    assert all(x['price_change_pct_t5'] is None and x['next_open'] is None for x in r['observations'][-5:])
