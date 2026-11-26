"""v4.1 OHLCV aggregation, privacy, auth, and UI regression coverage."""
from nova_release import APP_VERSION
import pandas as pd
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from pathlib import Path
from higher_timeframes import chart_data, bucket, fetch_long_daily, grouped_ohlcv
import higher_timeframes
import server

KST=ZoneInfo('Asia/Seoul')

def sample():
    ix=pd.DatetimeIndex(['2024-12-30','2024-12-31','2025-01-02','2025-01-06','2025-02-03'])
    return pd.DataFrame({'Open':[10,12,15,18,20],
                         'High':[14,16,19,21,27],
                         'Low':[8,11,13,17,19],
                         'Close':[12,15,17,20,25],
                         'Volume':[100,200,300,400,500]}, index=ix)


def test_calendar_groups_do_not_blend_years_and_aggregate_volume():
    d=sample()
    weeks=grouped_ohlcv(d,'1w','005930.KS')
    assert list(weeks.index.strftime('%Y-%m-%d'))==['2024-12-30','2025-01-06','2025-02-03']
    assert weeks.iloc[0][['Open','High','Low','Close','Volume']].tolist()==[10,19,8,17,600]
    months=grouped_ohlcv(d,'1mo','005930.KS')
    assert list(months.index.strftime('%Y-%m-%d'))==['2024-12-01','2025-01-01','2025-02-01']
    assert months.iloc[1][['Open','High','Low','Close','Volume']].tolist()==[15,21,13,20,700]
    years=grouped_ohlcv(d,'1y','005930.KS')
    assert list(years.index.strftime('%Y-%m-%d'))==['2024-01-01','2025-01-01']
    assert years.iloc[1][['Open','High','Low','Close','Volume']].tolist()==[15,27,13,25,1200]


def test_week_starts_monday_even_on_new_year():
    assert bucket(pd.Timestamp('2026-01-01').date(),'1w').isoformat()=='2025-12-29'
    assert bucket(pd.Timestamp('2026-01-01').date(),'1y').isoformat()=='2026-01-01'


def test_exchange_local_date_over_utc_timezone_boundary():
    # US close is in UTC on the following calendar day; group in New York.
    ix=pd.DatetimeIndex([pd.Timestamp('2025-01-02T22:00:00+00:00'),pd.Timestamp('2025-01-03T22:00:00+00:00')])
    d=pd.DataFrame({'Open':[10,11],'High':[13,14],'Low':[9,10],'Close':[11,12],'Volume':[10,20]},index=ix)
    out=grouped_ohlcv(d,'1w','TSLA')
    assert out.index[0].strftime('%Y-%m-%d')=='2024-12-30'
    assert out.iloc[0]['Volume']==30


def test_partial_current_period_marked_but_not_used_for_signals():
    d=sample()
    now=datetime(2025,2,5,12,tzinfo=KST)
    r=chart_data(d,'1mo','005930.KS','yahoo',now=now)
    assert r['chart']['partial']==[False,False,True]
    assert r['partial_last']
    assert r['chart']['close'][-1]==25
    assert r['chart']['ema20'][-1] is None
    assert r['chart']['ema50'][-1] is None
    # A completed month remains complete after the next month begins.
    r2=chart_data(d,'1mo','005930.KS','yahoo',now=datetime(2025,3,1,tzinfo=KST))
    assert r2['partial_last'] is False


def test_short_history_fails_instead_of_fabricating_years():
    d=sample().iloc[:2]
    with pytest.raises(ValueError,match='자료가 1개'):
        chart_data(d,'1y','005930.KS','kis')


def test_demo_yearly_data_and_ema_are_not_fabricated():
    d=fetch_long_daily('DEMO','demo')
    y=chart_data(d,'1y','DEMO','demo',synthetic=True)
    assert y['count']>=20
    assert y['synthetic']
    assert y['chart']['ema20'][0] is None
    assert y['chart']['ema50'][-1] is None
    assert y['chart']['dates'][-1].endswith('01-01')


def test_long_chart_auth_input_and_error(monkeypatch):
    c=TestClient(server.app)
    server.CACHE.clear();server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','q'*20)
    assert c.get('/api/chart/history?symbol=005930.KS&timeframe=1w').status_code==401
    headers={'Authorization':'Bearer '+'q'*20}
    assert c.get('/api/chart/history?symbol=005930.KS&timeframe=bogus',headers=headers).status_code==422
    assert c.get('/api/chart/history?symbol=<script>&timeframe=1w',headers=headers).status_code==422
    assert c.get('/api/chart/history?symbol=TSLA&source=kis&timeframe=1w',headers=headers).status_code==422
    monkeypatch.setattr(server,'_kis_creds',lambda:('key','secret'))
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda symbol,period:sample())
    response=c.get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1w',headers=headers)
    assert response.status_code==200
    assert response.json()['chart']['close'][0]==17
    assert response.headers['cache-control']=='no-store'


def test_native_period_cache_separate_and_reused(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    server.CACHE.clear();server.RATE.clear()
    calls=[]
    def fake(symbol,source):
        calls.append((symbol,source));return sample()
    monkeypatch.setattr(server,'_kis_creds',lambda:('key','secret'))
    monkeypatch.setattr(server,'fetch_kis_native_period',fake)
    for frame in ['1w','1mo','1y']:
        r=c.get(f'/api/chart/history?symbol=005930.KS&timeframe={frame}')
        assert r.status_code==200, r.json()
        assert r.json()['timeframe']==frame
    assert len(calls)==3
    assert c.get('/api/chart/history?symbol=005930.KS&timeframe=1w').status_code==200
    assert len(calls)==3


def test_chart_isolated_from_trading_decisions(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    server.CACHE.clear();server.RATE.clear()
    monkeypatch.setattr(server,'_kis_creds',lambda:('key','secret'))
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda symbol,period:sample())
    monkeypatch.setattr(server,'_payload',lambda *a,**k: (_ for _ in ()).throw(AssertionError('chart must not call analysis')))
    assert c.get('/api/chart/history?symbol=005930.KS&timeframe=1w').status_code==200
    info=c.get('/healthz').json()
    assert info['app_version']==APP_VERSION
    assert info['higher_timeframe_charts'] and info['higher_timeframes_affect_signal'] is False


def test_static_controls_cache_and_copy_are_consistent():
    root=Path(server.ASSETS)
    html=(root/'index.html').read_text(encoding='utf8')
    js=(root/'candles.js').read_text(encoding='utf8')
    assert 'id="candleTimeframe"' in html
    for key in ['1w','1mo','1y','analysis']:
        assert f'value="{key}"' in html
    assert '차트 봉 주기' in html and '매수·매도 신호나 적중률을 변경하지 않습니다' in html
    assert '/api/chart/history' in js
    assert 'nova3-shell-v45' in (root/'sw.js').read_text(encoding='utf8')


def test_readonly_demo_api_yearly_returns_no_signals(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    server.CACHE.clear();server.RATE.clear()
    res=c.get('/api/chart/history?symbol=IGNORED&source=demo&timeframe=1y')
    assert res.status_code==200
    j=res.json()
    assert j['source']=='demo' and j['synthetic'] is True
    assert j['count']>=20
    assert 'signal' not in j and 'backtest' not in j and 'probability' not in j
    assert len(j['chart']['partial'])==len(j['chart']['dates'])
    assert c.post('/api/chart/history?source=demo&timeframe=1w').status_code==405


def test_demo_daily_long_data_matches_analysis_daily_price():
    from data_sources import demo_data
    day=demo_data('스윙').bars
    full=fetch_long_daily('DEMO','demo')
    assert full.index[-1]==day.index[-1]
    for col in ['Open','High','Low','Close','Volume']:
        assert full.iloc[-1][col]==pytest.approx(day.iloc[-1][col])


def test_favicon_and_keyboard_chart_accessibility():
    client=TestClient(server.app)
    r=client.get('/favicon.ico')
    assert r.status_code==200 and r.headers['content-type'].startswith('image/png')
    assert len(r.content)>100
    assert 'id="candleCanvas" class="candle-canvas" tabindex="0"' in (server.ASSETS/'index.html').read_text(encoding='utf8')
    js=(server.ASSETS/'candles.js').read_text(encoding='utf8')
    assert "e.key==='ArrowLeft'" in js and "e.key==='ArrowRight'" in js
