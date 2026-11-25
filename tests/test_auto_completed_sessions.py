"""New NOVA KIS-first swing analysis: no look-ahead, no fabricated freshness."""
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
import pandas as pd
import pytest
from data_sources import MarketData
from completed_session import completed_swing_bars
import server

KST=ZoneInfo('Asia/Seoul')


def frame(ending: str, n: int=170):
    idx=pd.bdate_range(end=ending,periods=n)
    prices=[75000+i*20 for i in range(len(idx))]
    return pd.DataFrame({'Open':prices,'High':[p+100 for p in prices],
                         'Low':[p-100 for p in prices], 'Close':prices,
                         'Volume':[12000]*len(prices)},index=idx)


def market(ending: str, provider: str):
    bars=frame(ending)
    return MarketData(bars,provider,'005930.KS','스윙',float(bars.Close.iloc[-1]),
                      ending,datetime(2026,11,23,18,tzinfo=KST).isoformat(),
                      '모의 데이터','모의 데이터 · 실거래 아님','KRW')


@pytest.mark.parametrize('when,ending,expected,stale,gap',[
    ('2026-11-20T18:00:00+09:00','2026-11-20','2026-11-20',False,0),
    ('2026-11-22T18:00:00+09:00','2026-11-20','2026-11-20',False,0),
    ('2026-11-23T09:30:00+09:00','2026-11-20','2026-11-20',False,0),
    ('2026-11-23T15:29:00+09:00','2026-11-20','2026-11-20',False,0),
    ('2026-11-23T15:31:00+09:00','2026-11-20','2026-11-23',True,1),
    ('2026-11-23T18:00:00+09:00','2026-11-23','2026-11-23',False,0),
    ('2026-11-23T18:00:00+09:00','2026-11-20','2026-11-23',True,1),
    ('2026-11-19T16:00:00+09:00','2026-11-18','2026-11-18',False,0),
    ('2026-11-19T16:31:00+09:00','2026-11-18','2026-11-19',True,1),
    ('2026-10-09T18:00:00+09:00','2026-10-08','2026-10-08',False,0),
])
def test_krx_session_aware_freshness(when,ending,expected,stale,gap):
    bars=frame(ending)
    finished, state=completed_swing_bars(bars,now=datetime.fromisoformat(when))
    assert state['expected_completed_session']==expected
    assert state['latest_completed_session']==ending
    assert state['stale'] is stale
    assert state['session_gap']==gap
    assert len(finished)==len(bars)


def test_current_partial_daily_is_removed_before_indicators():
    source=frame('2026-11-23')
    completed,info=completed_swing_bars(source,now=datetime(2026,11,23,11,40,tzinfo=KST))
    assert completed.index[-1].date().isoformat()=='2026-11-20'
    assert info['excluded_unfinished']==1
    assert info['state']=='CURRENT_COMPLETED_SESSION'
    # Exam-day exception closes at 16:30, not 15:30.
    exam=frame('2026-11-19')
    clipped,v=completed_swing_bars(exam,now=datetime(2026,11,19,16,0,tzinfo=KST))
    assert clipped.index[-1].date().isoformat()=='2026-11-18'
    assert v['excluded_unfinished']==1 and not v['stale']
    complete,v=completed_swing_bars(exam,now=datetime(2026,11,19,16,31,tzinfo=KST))
    assert complete.index[-1].date().isoformat()=='2026-11-19'


def test_future_provider_bar_blocks_even_if_truncated():
    future=frame('2026-11-24')
    cut,state=completed_swing_bars(future,now=datetime(2026,11,23,18,tzinfo=KST))
    assert cut.index[-1].date().isoformat()=='2026-11-23'
    assert state['state']=='FUTURE_BAR' and state['stale']


def test_calendar_unknown_blocks_signal(monkeypatch):
    import exchange_calendars
    monkeypatch.setattr(exchange_calendars,'get_calendar',lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    _,data=completed_swing_bars(frame('2026-11-23'),now=datetime(2026,11,23,18,tzinfo=KST))
    assert data['stale'] and data['state']=='CALENDAR_UNKNOWN'


def prepare(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','fake_app_key')
    monkeypatch.setenv('KIS_APP_SECRET','fake_app_secret')
    monkeypatch.setattr(server,'_get_financial_state',lambda *args:('UNKNOWN','모의 재무',None))
    monkeypatch.setattr(server,'completed_swing_bars',lambda bars:completed_swing_bars(bars,now=datetime(2026,11,23,18,tzinfo=KST)))


def test_auto_prefers_kis_and_reports_real_source(monkeypatch):
    prepare(monkeypatch)
    kis=Mock(return_value=market('2026-11-23','KIS MOCK'))
    monkeypatch.setattr(server,'kis_data',kis)
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a,**kw:None)
    result=server._payload('005930.KS','스윙','auto',15)
    assert result['source']=='kis' and result['requested_source']=='auto'
    assert result['completed_session']['state']=='CURRENT_COMPLETED_SESSION'
    assert not result['is_stale'] and result['source_fallback_reason'] is None
    assert kis.call_count==1


def test_auto_failure_does_not_fallback(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(server,'kis_data',Mock(side_effect=RuntimeError('upstream error')))
    for source in ('auto','kis'):
        with pytest.raises(RuntimeError,match='upstream error'):
            server._payload('005930.KS','스윙',source,15)


def test_asof_scan_uses_kis_no_future_bars(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(server,'kis_data',Mock(return_value=market('2026-11-23','KIS MOCK')))
    result=server._payload('005930.KS','스윙','auto',15,asof_date='20261120')
    assert result['source']=='kis' and result['quote'] is None
    assert result['last_completed_bar'][:10]=='2026-11-20'


def test_auto_intraday_uses_kis(monkeypatch):
    prepare(monkeypatch)
    import data_sources
    demo=data_sources.demo_data('5분');demo.symbol='005930.KS';demo.synthetic=False
    kis=Mock(return_value=demo);monkeypatch.setattr(server,'kis_data',kis)
    result=server._payload('005930.KS','5분','auto',15)
    assert result['source']=='kis' and result['requested_source']=='auto'
    kis.assert_called_once()


def test_release_ui_label_and_cache():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    assert 'value="yahoo"' not in (base/'static/index.html').read_text()
    js=(base/'static/app.js').read_text()
    assert "source:'auto'" in js
    assert "config.source='kis'" in js
    assert 'requested_source||latest.source' in js
    assert 'KRX 최신 완료 거래일' in js
    assert 'session-aware-auto-analysis' in (base/'static/sw.js').read_text()
