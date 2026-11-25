"""15m analysis + closed-calendar weekly/monthly research invariants."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import data_sources as ds
import engine
import server
import trend_context
from quality_validation import HORIZON

KST=ZoneInfo('Asia/Seoul')


def sample_minutes(days=6):
    idx=pd.DatetimeIndex([
        pd.Timestamp('2026-11-11',tz=KST)+pd.Timedelta(days=n,hours=9,minutes=m)
        for n in range(days) for m in range(390)])
    px=np.arange(len(idx),dtype=float)+10000
    return pd.DataFrame({'Open':px,'High':px+2,'Low':px-2,'Close':px+1,'Volume':np.ones(len(idx))},index=idx)


def sample_daily(days=540):
    dates=pd.bdate_range(end='2026-11-20', periods=days)
    prices=np.arange(len(dates),dtype=float)+10000
    return pd.DataFrame({'Open':prices,'High':prices+3,'Low':prices-2,'Close':prices+1,'Volume':np.ones(len(dates))*10000},index=dates)


def test_15m_aggregation_and_never_carries_over_session():
    # Three distinct 390-minute exchange sessions, regardless of calendar weekend assumptions.
    bar=sample_minutes(3)
    aggregate=ds.aggregate_minutes(bar,15)
    assert len(aggregate)==3*26
    first=aggregate.iloc[0]
    assert first['Open']==bar.iloc[0]['Open']
    assert first['Close']==bar.iloc[14]['Close']
    assert first['High']==bar.iloc[:15]['High'].max()
    assert first['Low']==bar.iloc[:15]['Low'].min()
    assert first['Volume']==15
    assert aggregate.index[26]==bar.index[390]
    assert aggregate.iloc[26]['Volume']==15
    assert not any(aggregate.index.duplicated())


def test_15m_open_bar_excluded_but_prior_completed_retained():
    bars=ds.aggregate_minutes(sample_minutes(1),15)
    # 09:32 is in the 09:30-09:44 bucket: it must be excluded.
    now=datetime(2026,11,11,9,32,tzinfo=KST)
    observed=bars.loc[bars.index.date==now.date()].iloc[:3]
    complete=ds.discard_unfinished(observed,'15분','005930.KS',now)
    assert len(complete)==2
    assert complete.index[-1].hour==9 and complete.index[-1].minute==15


def test_15m_config_and_horizon_nonoptimized_defaults():
    cfg=engine.Config.for_mode('15분',15)
    assert cfg.mode=='15분' and cfg.max_hold_bars==20
    assert HORIZON['15분']==4
    assert ds.MINUTE_INTERVALS['15분']==15


def test_15m_api_demo_returns_completed_bars_and_no_swing_finance():
    r=TestClient(server.app).get('/api/analysis',params={'mode':'15분','source':'demo','symbol':'005930.KS'})
    assert r.status_code==200,r.text[:400]
    x=r.json()
    assert x['mode']=='15분' and x['is_demo']
    assert x['signal'] in ['BUY','SELL','HOLD']
    assert x['higher_timeframe_context'] is None
    assert x['financial_gate']=='NOT_APPLIED'
    assert x['signal_performance']['horizon_bars']==4
    assert x['quality_comparison']['horizon_bars']==4
    assert x['final_actionable_signal'] != 'BUY'  # DEMO never authorizes live BUY


def test_yahoo_15m_uses_supported_interval_and_no_claim_of_live(monkeypatch):
    observed={}
    class Ticker:
        def __init__(self,s):observed['ticker']=s
        def history(self,**kw):
            observed.update(kw)
            # Enough fully closed historical bars, all tz-aware.
            return ds.aggregate_minutes(sample_minutes(),15)
    import sys,types
    monkeypatch.setitem(sys.modules,'yfinance',types.SimpleNamespace(Ticker=Ticker))
    got=ds.yahoo_data('005930.KS','15분')
    assert (observed['interval'],observed['period'])==('15m','60d')
    assert got.interval=='15m' and len(got.bars)>65
    assert '실시간 보장 없음' in got.freshness


def test_kis_15m_aggregates_multiple_sessions(monkeypatch):
    calls=[]
    class DummyKis:
        def __init__(self,k,s):pass
        def quote(self,code):return 10400,'101500'
        def minutes(self,code,now,sessions=3):
            calls.append(sessions)
            return sample_minutes(6)
    monkeypatch.setattr(ds,'KIS',DummyKis)
    result=ds.kis_data('005930.KS','15분','test-key','test-secret')
    assert calls==[6]
    assert len(result.bars)>=65
    assert result.interval=='15분'


def test_week_and_month_omit_open_calendar_buckets():
    bars=sample_daily()
    # Input includes Friday 2026-11-20, when the week is not yet confirmed over at 14:20 KST.
    now=datetime(2026,11,20,14,20,tzinfo=KST)
    ctx=trend_context.summarize_higher_trends(bars,'005930.KS',now=now)
    assert ctx['research_only'] and not ctx['affects_signal'] and not ctx['affects_rank']
    assert ctx['weekly']['state'] in ('UP','DOWN','NEUTRAL')
    assert ctx['monthly']['state'] in ('UP','DOWN','NEUTRAL')
    for key in ('weekly','monthly'):
        assert ctx[key]['period_start'] is not None
    # The snapshot never includes today or a currently open week/month.
    assert ctx['weekly']['period_start']<'2026-11-16'
    assert ctx['monthly']['period_start']<'2026-11-01'
    # Changing this week's/month's daily candles must not alter either context.
    updated=bars.copy()
    updated.loc[updated.index>=pd.Timestamp('2026-11-16'),'Close']*=1.02
    updated.loc[updated.index>=pd.Timestamp('2026-11-16'),'High']*=1.03
    ctx_new=trend_context.summarize_higher_trends(updated,'005930.KS',now=now)
    for key in ('weekly','monthly'):
        assert ctx_new[key]['ema_short']==ctx[key]['ema_short']
        assert ctx_new[key]['ema_long']==ctx[key]['ema_long']


def test_context_historical_asof_does_not_use_future_time(monkeypatch):
    rows=sample_daily(510)
    observed=datetime(2030,6,30,tzinfo=KST)
    hist=trend_context.summarize_higher_trends(rows,'005930.KS',historical=True,now=observed)
    index_date=rows.index[-1].date()
    assert hist['last_completed_daily']==str(index_date)
    assert hist['weekly']['period_start']<str(trend_context.bucket(index_date,'1w'))
    assert hist['monthly']['period_start']<str(trend_context.bucket(index_date,'1mo'))


def test_trend_not_affect_existing_signal_result():
    bars=sample_daily()
    f=engine.feature_frame(bars,'스윙')
    before=engine.decide(f,engine.Config.for_mode('스윙')).signal
    trend_context.summarize_higher_trends(bars,'005930.KS')
    after=engine.decide(f,engine.Config.for_mode('스윙')).signal
    assert before==after


def test_four_modes_in_ui_and_optional_chart_periods():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'static/index.html').read_text()
    script=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
    for mode in ('1분','5분','15분','스윙'):
        assert f'data-mode="{mode}"' in html
        assert mode in script
    assert 'data-mode="주봉"' not in html
    assert 'data-mode="월봉"' not in html
    assert 'value="1w"' in html and 'value="1mo"' in html
    assert 'higherTrendPanel' in html and 'renderHigherTrend' in script


def test_kis_short_history_rejected_no_fake_backtest(monkeypatch):
    class DummyKis:
        def __init__(self,k,s):pass
        def quote(self,code):return 10100,'101500'
        def minutes(self,*a,**kw):return sample_minutes(2)
    monkeypatch.setattr(ds,'KIS',DummyKis)
    with pytest.raises(RuntimeError,match='65개 미만'):
        ds.kis_data('005930.KS','15분','x','y')
