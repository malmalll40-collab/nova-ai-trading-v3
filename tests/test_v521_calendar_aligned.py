"""NOVA v5.2.1: KRX diagnostics and comparable exit-policy research."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from engine import Config
from market_sessions import krx_session
from completed_session import completed_swing_bars
from nova_v521_aligned import compare_paired_exit_rules, simulate_paired
from tests.test_core_comparison_473 import sample_frames

KST=ZoneInfo('Asia/Seoul')


def test_krx_verified_2026_11_24_and_weekend():
    mid=krx_session(datetime(2026,11,24,10,30,tzinfo=KST))
    after=krx_session(datetime(2026,11,24,16,10,tzinfo=KST))
    weekend=krx_session(datetime(2026,11,22,10,30,tzinfo=KST))
    assert mid['calendar_verified'] and mid['status']=='OPEN'
    assert after['calendar_verified'] and after['status']=='CLOSED'
    assert weekend['calendar_verified'] and weekend['status']=='CLOSED'


def test_krx_load_failure_is_explained_and_fails_closed(monkeypatch):
    import exchange_calendars
    def failure(*args,**kwargs):
        raise RuntimeError('SECRET_PRIVATE_MESSAGE')
    monkeypatch.setattr(exchange_calendars,'get_calendar',failure)
    d=krx_session(datetime(2026,11,24,11,0,tzinfo=KST))
    assert d['status']=='UNKNOWN' and not d['is_open'] and not d['calendar_verified']
    assert d['calendar_error_code']=='XKRX_LOAD_FAILED'
    assert 'SECRET_PRIVATE_MESSAGE' not in str(d)
    f=pd.DataFrame({'Open':[1.,2.],'High':[2.,3.],'Low':[1.,2.],
                    'Close':[2.,3.],'Volume':[100,100]},index=pd.to_datetime(['2026-11-23','2026-11-24']))
    clean,st=completed_swing_bars(f,now=datetime(2026,11,24,17,tzinfo=KST))
    assert st['stale'] and st['state']=='CALENDAR_UNKNOWN'
    assert st['calendar_error_code']=='XKRX_LOAD_FAILED'
    assert clean.index[-1].date().isoformat()=='2026-11-23'


def test_calendar_bounded_range_retry(monkeypatch):
    import exchange_calendars
    original=exchange_calendars.get_calendar
    invoked=[]
    def adapter(name,**kwargs):
        invoked.append(bool(kwargs))
        if not kwargs:raise ValueError('global default range is stale')
        return original(name,**kwargs)
    monkeypatch.setattr(exchange_calendars,'get_calendar',adapter)
    result=krx_session(datetime(2026,11,24,11,0,tzinfo=KST))
    assert result['status']=='OPEN' and result['calendar_verified']
    assert invoked==[False,True]


def test_aligned_comparison_same_prices_and_signals_research_only():
    f, _=sample_frames(250)
    original=f.copy(deep=True)
    signals={180:'BUY',199:'BUY',218:'BUY'}
    cfg=Config.for_mode('스윙')
    a=compare_paired_exit_rules(f,signals,170,240,cfg)
    assert a['status']=='READY'
    assert a['research_only'] and a['same_validation_dates'] and a['same_entry_conditions']
    assert a['fixed_five']['completed_trades']==a['risk_exit']['completed_trades']==3
    assert all(not x['actual_fill'] for x in a['risk_exit']['trade_ledger'])
    # Better/worse exit is not assumed; this is diagnostic research only.
    pd.testing.assert_frame_equal(f,original)


def test_aligned_differs_when_intra_five_stop_hit():
    f,_=sample_frames(240)
    f=f.copy(deep=True)
    cfg=Config.for_mode('스윙')
    i=180
    entry=float(f['Open'].iloc[i+1]);atr=float(f['atr'].iloc[i]);stop=entry-atr*cfg.stop_atr
    f.iloc[i+2,f.columns.get_loc('Low')]=min(float(f['Low'].iloc[i+2]),stop-1)
    f.iloc[i+2,f.columns.get_loc('High')]=max(float(f['High'].iloc[i+2]),float(f['Close'].iloc[i+2]),float(f['Open'].iloc[i+2]))
    args={'f':f,'signals':{i:'BUY'},'start':170,'stop':205,'cost_bps':15,
          'stop_atr':cfg.stop_atr,'target_atr':cfg.target_atr}
    fixed=simulate_paired(mode='fixed_five',**args)
    risk=simulate_paired(mode='risk_exit',**args)
    assert fixed['completed_trades']==risk['completed_trades']==1
    assert risk['trade_ledger'][0]['exit_date']<fixed['trade_ledger'][0]['exit_date']
    assert risk['portfolio_return_pct']!=fixed['portfolio_return_pct']


def test_aligned_no_trade_not_mislabelled_safe():
    f,_=sample_frames(240)
    r=compare_paired_exit_rules(f,{},170,230,Config.for_mode('스윙'))
    assert r['fixed_five']['completed_trades']==0
    assert r['portfolio_return_difference_pp'] is None
    assert not r['risk_exit']['performance_assessable']


def test_v521_ui_and_batch_integration():
    import server
    assert server.healthz()['app_version']=='5.6.5'
    html=(server.ASSETS/'index.html').read_text()
    js=(server.ASSETS/'v5_dashboard.js').read_text()
    assert 'id="v521AlignedCards"' in html and 'id="v521FilterSummary"' in html
    assert 'renderFilterSummary' in js and 'renderAlignedResearch' in js
    assert 'aligned_backtest_comparison' in js


def test_paired_fixed_matches_existing_v5_research_baseline():
    from nova_v5_research import simulate_long_only
    f,_=sample_frames(245)
    cfg=Config.for_mode('스윙')
    signals={118:'BUY',128:'BUY',150:'BUY',179:'BUY'}
    old=simulate_long_only(f,signals,110,190,15,include_trades=True)
    new=simulate_paired(f,signals,110,190,15,mode='fixed_five',stop_atr=cfg.stop_atr,target_atr=cfg.target_atr)
    for key in ('completed_trades','portfolio_return_pct','portfolio_mdd_pct','win_rate_pct','buy_opportunities'):
        assert old[key]==new[key]
