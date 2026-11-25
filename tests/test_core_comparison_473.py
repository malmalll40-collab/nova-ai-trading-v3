from __future__ import annotations
import numpy as np
import pandas as pd
from engine import Config, feature_frame, decide
from core_comparison import compare, _core_at, _evaluate


def sample_frames(size=225):
    dates=pd.bdate_range('2025-10-01',periods=size)
    n=np.arange(size,dtype=float)
    # fixed deterministic synthetic prices; not an empirical market test
    close=100+0.17*n+2.8*np.sin(n/8)
    open_=close*(1-0.001*np.cos(n/4))
    high=np.maximum(open_,close)+2.2
    low=np.minimum(open_,close)-2.2
    volume=15000+8000*(1+np.sin(n/6))
    data=pd.DataFrame({'Open':open_,'High':high,'Low':low,'Close':close,'Volume':volume},index=dates)
    ref=pd.DataFrame({'Close':2600+2*n+5*np.sin(n/11)},index=dates)
    return feature_frame(data,'스윙'),ref


def test_equal_period_and_no_model_adoption():
    features,index=sample_frames()
    result=compare(features,Config.for_mode('스윙'),benchmark=index,source='kis')
    assert result['status']=='READY'
    assert result['research_only'] and not result['modifies_live_signal'] and not result['comparison_validated']
    assert result['formation']['eligible_sessions']+result['validation']['eligible_sessions']==result['eligible_sessions']
    assert result['validation']['period_start']>result['formation']['period_end']
    for phase in ['formation','validation']:
        assert result[phase]['baseline']['completed_trades']>=0
        assert result[phase]['five_core']['completed_trades']>=0
        assert result[phase]['baseline']['portfolio_mdd_pct']<=0


def test_missing_or_malformed_market_data_fail_closed():
    features,ref=sample_frames()
    missing=compare(features,Config.for_mode('스윙'),benchmark=None,source='kis')
    assert missing['status']=='UNAVAILABLE'
    broken=ref.copy();broken.iloc[-1,0]=np.nan
    assert compare(features,Config.for_mode('스윙'),benchmark=broken,source='kis')['status']=='UNAVAILABLE'
    assert compare(features,Config.for_mode('1분'),benchmark=ref,source='kis')['status']=='NOT_APPLICABLE'
    assert compare(features,Config.for_mode('스윙'),benchmark=ref,source='kis',asof_date='20260101')['status']=='NOT_APPLICABLE'


def test_no_future_index_lookahead():
    features,ref=sample_frames()
    i=180
    base=dict(zip([pd.Timestamp(d).date().isoformat() for d in ref.index],ref.Close))
    before=_core_at(features,i,base)
    # perturb current session index and all later dates: signal must be unchanged
    for d in ref.index[i:]:base[d.date().isoformat()]=1_000_000
    after=_core_at(features,i,base)
    assert before==after
    assert before['status']=='READY'
    # missing a prior reference date blocks buy rather than forward-filling
    base.pop(features.index[i-4].date().isoformat())
    assert _core_at(features,i,base)['status']=='UNAVAILABLE'


def test_original_signal_unchanged_after_comparison():
    f,ref=sample_frames()
    cfg=Config.for_mode('스윙')
    first=decide(f,cfg).signal
    original=f.copy(deep=True)
    compare(f,cfg,benchmark=ref,source='kis')
    assert first==decide(f,cfg).signal
    pd.testing.assert_frame_equal(original,f)


def test_next_open_execution_not_same_close():
    f,_=sample_frames()
    i=150
    sig={i:'BUY'}
    original=_evaluate(f,sig,[i],15,i,i+6)
    assert original['completed_trades']==1
    changed=f.copy();changed.iloc[i+1,changed.columns.get_loc('Open')]*=1.4
    altered=_evaluate(changed,sig,[i],15,i,i+6)
    assert altered['mean_trade_net_pct'] < original['mean_trade_net_pct']


def test_non_overlapping_positions_and_no_sell_shorts():
    f,_=sample_frames()
    indexes=list(range(140,155))
    signals={i:'BUY' for i in indexes}
    signals[153]='SELL'
    result=_evaluate(f,signals,indexes,15,140,156)
    assert result['completed_trades']<=3
    assert result['overlap_skipped']>=5
    assert result['buy_opportunities']==14


def test_ui_and_api_expose_readonly_comparison():
    from pathlib import Path
    import server
    import inspect
    html=(Path(server.ASSETS)/'index.html').read_text()
    js=(Path(server.ASSETS)/'app.js').read_text()
    assert 'coreComparisonPanel' in html and 'coreComparisonStats' in html
    assert 'function renderCoreComparison' in js
    assert "'core_comparison_research':core_comparison" in inspect.getsource(server._payload)
    assert server.healthz()['core_live_adoption'] is False
