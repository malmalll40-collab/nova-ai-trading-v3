"""Live-period display integrity: no stale bar -> fake market activity, no signal changes."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd
from fastapi.testclient import TestClient
import pytest
import server
import higher_timeframes as hf
from data_sources import KIS

KST=ZoneInfo('Asia/Seoul')


def make(days, prices=None):
    if prices is None: prices=list(range(100,100+len(days)))
    idx=pd.DatetimeIndex(pd.to_datetime(days))
    return pd.DataFrame({'Open':[v-1 for v in prices],'High':[v+2 for v in prices],
         'Low':[v-2 for v in prices],'Close':prices,'Volume':[1000+i*100 for i in range(len(days))]},index=idx)


def test_native_query_uses_d_w_m_y_and_correct_kis_path(monkeypatch):
    calls=[]
    api=KIS.__new__(KIS)
    def fake_get(path,trid,params):
        calls.append((path,trid,params))
        return {'output2':[{'stck_bsop_date':'20261120','stck_oprc':'100','stck_hgpr':'105','stck_lwpr':'98','stck_clpr':'103','acml_vol':'1000'},
                           {'stck_bsop_date':'20261119','stck_oprc':'99','stck_hgpr':'104','stck_lwpr':'97','stck_clpr':'101','acml_vol':'900'}]}
    monkeypatch.setattr(api,'get',fake_get)
    for period,code in [('1d','D'),('1w','W'),('1mo','M'),('1y','Y')]:
        d=api.period_bars('005930',period,now=datetime(2026,11,20,12,tzinfo=KST))
        assert len(d)==2
        path,trid,params=calls[-1]
        assert path.endswith('inquire-daily-itemchartprice')
        assert trid=='FHKST03010100'
        assert params['FID_PERIOD_DIV_CODE']==code
        assert params['FID_ORG_ADJ_PRC']=='0'
        assert params['FID_INPUT_ISCD']=='005930'
    with pytest.raises(ValueError): api.period_bars('TSLA','1y')
    with pytest.raises(ValueError): api.period_bars('005930','1h')


def test_current_day_kis_bar_is_partial_but_not_a_signal():
    d=make(['2026-11-19','2026-11-20'],[100,103])
    data=hf.chart_data(d,'1d','005930.KS','kis',now=datetime(2026,11,20,10,30,tzinfo=KST))
    assert data['chart']['partial']==[False,True]
    assert not any(field in data for field in ('signal','buy','probability','backtest'))


def test_market_outside_session_fails_closed_without_provider_request(monkeypatch):
    monkeypatch.setattr(hf,'fetch_kis_native_period',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not call provider')))
    for moment in [datetime(2026,11,20,8,59,tzinfo=KST),datetime(2026,11,20,15,30,tzinfo=KST),datetime(2026,11,22,12,tzinfo=KST)]:
        for per in ['1d','1w','1mo','1y']:
            r=hf.latest_kis_period('005930.KS',per,now=moment)
            assert r['available'] is False
            assert r['streaming'] is False


def test_native_period_current_snapshot_each_period(monkeypatch):
    day=['2026-11-18','2026-11-19','2026-11-20']
    d=make(day,[99,101,103])
    # Simulate KIS native monthly/weekly/yearly aggregate, NOT input daily bars.
    period_frames={'1d':d,'1w':make(['2026-11-13','2026-11-20'],[95,103]),
                   '1mo':make(['2026-10-31','2026-11-20'],[90,103]),
                   '1y':make(['2025-12-31','2026-11-20'],[89,103])}
    def fake(symbol,timeframe,*,recent_only=False,now=None):
        assert symbol=='005930.KS' and recent_only
        return period_frames[timeframe]
    monkeypatch.setattr(hf,'fetch_kis_native_period',fake)
    for period in ['1d','1w','1mo','1y']:
        r=hf.latest_kis_period('005930.KS',period,now=datetime(2026,11,20,10,30,tzinfo=KST))
        assert r['available'] is True
        assert r['partial'] is True
        assert r['streaming'] is False
        assert r['refresh_type']=='REST polling'
        assert r['trading_date']=='2026-11-20'
        assert r['bar']['close']==103
        assert r['bar']['high']==105
        assert r['bar']['volume']>=0
    assert hf.latest_kis_period('005930.KS','1w',now=datetime(2026,11,20,15,45,tzinfo=KST))['available'] is False


def test_stale_day_never_marked_live_even_if_same_week_or_month(monkeypatch):
    yesterday=make(['2026-11-17','2026-11-18','2026-11-19'],[101,102,104])
    monkeypatch.setattr(hf,'fetch_kis_native_period',lambda s,p,**kw:yesterday)
    for period in ['1d','1w','1mo','1y']:
        r=hf.latest_kis_period('005930.KS',period,now=datetime(2026,11,20,10,30,tzinfo=KST))
        assert r['available'] is False
        assert r['last_provider_trading_date']=='2026-11-19'


def test_inconsistent_day_and_week_close_blocks_provider_update(monkeypatch):
    today=make(['2026-11-19','2026-11-20'],[100,103])
    weekly=make(['2026-11-13','2026-11-20'],[95,101])
    monkeypatch.setattr(hf,'fetch_kis_native_period',lambda s,p,**kw:today if p=='1d' else weekly)
    r=hf.latest_kis_period('005930.KS','1w',now=datetime(2026,11,20,10,30,tzinfo=KST))
    assert r['available'] is False
    assert '동기화' in r['reason']


def test_live_chart_api_rejects_bad_input_and_requires_auth_and_creds(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','Z'*20)
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    server.CACHE.clear();server.RATE.clear()
    assert c.get('/api/chart/live?symbol=005930.KS&timeframe=1d').status_code==401
    h={'Authorization':'Bearer '+'Z'*20}
    assert c.get('/api/chart/live?symbol=TSLA&timeframe=1d',headers=h).status_code==422
    assert c.get('/api/chart/live?symbol=005930.KS&timeframe=1m',headers=h).status_code==422
    assert c.get('/api/chart/live?symbol=005930.KS&timeframe=1d',headers=h).status_code==503
    assert c.get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1y',headers=h).status_code==503


def test_live_api_does_not_call_buy_sell_analysis(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','test-key')
    monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    server.RATE.clear();server.CACHE.clear()
    monkeypatch.setattr(server,'latest_kis_period',lambda s,p:{'available':True,'date':'2026-11-20','timeframe':p,'retrieved_at':'2026-11-20T10:30:00+09:00','bar':{'open':100,'high':110,'low':90,'close':105,'volume':500},'streaming':False,'refresh_type':'REST polling'})
    monkeypatch.setattr(server,'_payload',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('signals touched')))
    c=TestClient(server.app)
    r=c.get('/api/chart/live?symbol=005930.KS&timeframe=1mo')
    assert r.status_code==200
    assert r.json()['bar']['close']==105
    assert r.headers['cache-control']=='no-store'
    assert 'signal' not in r.json()


def test_frontend_explains_snapshots_and_does_not_expose_keys():
    root=Path(server.ASSETS)
    js=(root/'candles.js').read_text()
    html=(root/'index.html').read_text()
    sw=(root/'sw.js').read_text()
    assert 'id="candleProvider"' in html
    assert 'id="candleLiveStatus"' in html
    assert '/api/chart/live?' in js
    assert '20000' in js and 'await marketSession()' in js and 'document.hidden' in js
    assert 'WebSocket 아님' in js and '실시간 체결가 아님' in html
    assert 'os.environ' not in js and 'openapi.koreainvestment.com' not in js
    assert "url.pathname.startsWith('/api/')" in sw
