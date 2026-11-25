"""Nasdaq UI/endpoints retired while preserving KOSPI/KOSDAQ and demo preview."""
from pathlib import Path
from fastapi.testclient import TestClient
import server


def test_search_rejects_retired_nasdaq_and_preserves_korean(monkeypatch):
    c = TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN', raising=False)
    monkeypatch.setattr(server, '_limit', lambda _: None)
    assert c.get('/api/search', params={'q':'Apple','market':'nasdaq'}).status_code == 422
    assert c.get('/api/search', params={'q':'엔비디아','market':'us'}).status_code == 422
    monkeypatch.setattr(server,'search_stocks',lambda q: {'query':q,'results':[{'symbol':'005930.KS','name':'삼성전자'}]})
    assert c.get('/api/search',params={'q':'삼성전자','market':'kr'}).json()['results'][0]['symbol']=='005930.KS'


def test_retired_us_quotes_charts_analysis_and_news(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(server,'_limit',lambda _:None)
    for symbol in ('AAPL','NVDA','TSLA'):
        assert c.get('/api/analysis',params={'symbol':symbol}).status_code==422
        assert c.get('/api/chart/history',params={'symbol':symbol,'timeframe':'1d'}).status_code==422
        assert c.post('/api/watch/scan',json={'symbols':[symbol],'modes':['스윙']}).status_code==422
        assert c.get('/api/news',params={'symbol':symbol}).status_code==422
    assert c.post('/api/recommendations/run',json={'market':'nasdaq','source':'auto'}).status_code==422
    assert c.post('/api/journal/evaluate',json={'records':[{'symbol':'AAPL'}]}).status_code==422
    assert c.post('/api/recommendations/run',json={'market':'kospi','source':'nasdaq_public_watchlist'}).status_code==422


def test_no_us_ui_and_pwa_old_cache_invalidation():
    root=server.ASSETS
    html=(root/'index.html').read_text()
    js=(root/'app.js').read_text()
    rec=(root/'recommendations.js').read_text()
    sw=(root/'sw.js').read_text()
    assert 'value="nasdaq"' not in html
    assert 'data-symbol="AAPL"' not in html
    assert 'data-symbol="NVDA"' not in html
    assert 'value="AAPL"' not in html
    assert 'AAPL' not in (html+rec)
    assert '나스닥' not in (html+rec)
    assert '005930.KS' in html and '086520.KQ' in html
    assert "const m='kr'" in js
    assert "^\\d{6}\\.(KS|KQ)$" in js
    assert 'korea-only' in sw
    assert not (server.BASE/'nasdaq_universe.py').exists()


def test_feature_flags_explicitly_disable_nasdaq():
    d=TestClient(server.app).get('/healthz').json()
    assert d['nasdaq_search'] is False
    assert d['nasdaq_watchlist_scan'] is False
    assert d['us_tickers_enabled'] is False
    assert d['korean_market_only'] is True
    assert d['orders_enabled'] is False
    assert d['investor_flows_naver_fallback'] is True
