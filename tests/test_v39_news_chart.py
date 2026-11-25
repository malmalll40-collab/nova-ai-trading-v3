"""v3.9 regression coverage: real OHLC candle wire format, news auth, RSS and HUB fallback."""
import os
from datetime import timezone
from pathlib import Path
from unittest.mock import Mock
from fastapi.testclient import TestClient
import pytest
import news_sources
import server


def _fake_response(*, json_data=None, xml=None):
    r=Mock()
    r.raise_for_status.return_value=None
    r.status_code=200
    r.json.return_value=json_data
    r.content=xml or b''
    return r


def test_all_demo_modes_provide_ohlcv_and_emas():
    for mode in ('1분','5분','스윙'):
        result=server._payload('DEMO',mode,'demo',15)
        c=result['chart']
        assert len(c['dates'])==125
        for k in ('open','high','low','close','volume','ema20','ema50'):
            assert len(c[k])==125
        assert all(v>0 and lo<=min(op,cl)<=max(op,cl)<=hi for op,lo,cl,hi,v in
                   zip(c['open'],c['low'],c['close'],c['high'],c['volume']))
        assert result['backtest']['total_return_pct'] is not None


def test_stock_names_reject_untrusted_input():
    with pytest.raises(ValueError):news_sources.search_terms('','<script>')
    with pytest.raises(ValueError):news_sources.search_terms('217270.KQ')
    assert news_sources.search_terms('005930.KS')=='삼성전자'
    assert '테슬라' in news_sources.search_terms('TSLA')
    assert news_sources.search_terms('323990.KQ','박셀바이오')=='박셀바이오'


def test_naver_hub_normalization_dedup_and_html_escaping():
    data={'items':[
        {'title':'<b>삼성전자</b> 실적', 'originallink':'https://news.example/item', 'pubDate':'Tue, 10 Nov 2026 06:30:00 +0900'},
        {'title':'삼성전자 실적','link':'https://news.example/other'},
        {'title':'위험링크', 'link':'javascript:alert(1)'},
    ]}
    rows=news_sources.normalize_naver(data)
    assert len(rows)==1
    assert rows[0]['title']=='삼성전자 실적'
    assert rows[0]['link']=='https://news.example/item'
    assert rows[0]['published_at'].startswith('2026-11-09T21:30')


def test_news_fetch_official_hub_headers_without_exposing_keys(monkeypatch):
    monkeypatch.setenv('NAVER_NEWS_CLIENT_ID','abc123')
    monkeypatch.setenv('NAVER_NEWS_CLIENT_SECRET','top-secret')
    used=[]
    def fake(url,**kwargs):
        used.append((url,kwargs))
        return _fake_response(json_data={'items':[{'title':'뉴스 제목','link':'https://news.example/1'}]})
    monkeypatch.setattr(news_sources.requests,'get',fake)
    got=news_sources.fetch_news('005930.KS')
    assert got['provider_type']=='naver_api_hub' and len(got['items'])==1
    assert used[0][0]=='https://naverapihub.apigw.ntruss.com/search/v1/news'
    assert used[0][1]['headers']['X-NCP-APIGW-API-KEY']=='top-secret'
    assert 'top-secret' not in repr(got)


def test_google_news_rss_fallback_dedup(monkeypatch):
    monkeypatch.delenv('NAVER_NEWS_CLIENT_ID',raising=False)
    monkeypatch.delenv('NAVER_NEWS_CLIENT_SECRET',raising=False)
    sample=b'''<?xml version="1.0"?><rss><channel>
    <item><title>A headline</title><link>https://news.google.com/rss/articles/abc</link><source>Press</source><pubDate>Tue, 10 Nov 2026 02:00:00 GMT</pubDate></item>
    <item><title>A headline</title><link>https://news.google.com/rss/articles/abc</link></item>
    <item><title>Invalid</title><link>http://bad.example/</link></item>
    </channel></rss>'''
    seen=[]
    def fake(url,**kwargs):seen.append((url,kwargs));return _fake_response(xml=sample)
    monkeypatch.setattr(news_sources.requests,'get',fake)
    got=news_sources.fetch_news('005930.KS')
    assert got['provider_type']=='google_rss' and len(got['items'])==1
    assert seen[0][0]=='https://news.google.com/rss/search'
    assert got['items'][0]['publisher']=='Press'


def test_rss_xml_invalid_fails_closed():
    with pytest.raises(news_sources.NewsUnavailable):news_sources.normalize_rss(b'<rss><broken')


def test_news_api_requires_auth_and_is_read_only(monkeypatch):
    client=TestClient(server.app)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','a'*20)
    assert client.get('/api/news?symbol=005930.KS').status_code==401
    called=[]
    monkeypatch.setattr(server,'fetch_news',lambda symbol,name: called.append((symbol,name)) or {
        'query':'삼성전자','symbol':symbol,'provider':'mock','items':[],'retrieved_at':'2026-11-10T00:00:00Z'})
    server.CACHE.clear()
    r=client.get('/api/news?symbol=005930.KS',headers={'Authorization':'Bearer '+'a'*20})
    assert r.status_code==200
    assert r.json()['provider']=='mock'
    r=client.get('/api/news?symbol=005930.KS',headers={'Authorization':'Bearer '+'a'*20})
    assert r.status_code==200 and r.json()['cached'] is True
    assert len(called)==1
    assert client.post('/api/news?symbol=005930.KS',headers={'Authorization':'Bearer '+'a'*20}).status_code==405


def test_news_api_handles_provider_failure(monkeypatch):
    client=TestClient(server.app)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    def failure(symbol,name):raise news_sources.NewsUnavailable('공급원 제한')
    monkeypatch.setattr(server,'fetch_news',failure)
    server.CACHE.clear()
    r=client.get('/api/news?name=에코프로')
    assert r.status_code==503 and '공급원 제한' in r.json()['detail']


def test_new_shell_chart_scripts_news_view_and_no_news_in_trading_logic():
    h=Path(server.ASSETS/'index.html').read_text(encoding='utf-8')
    assert 'id="candleCanvas"' in h and 'id="candleDetails"' in h
    assert 'data-view="news"' in h and 'id="view-news"' in h
    assert 'id="newsSearchExternal"' in h
    assert h.count('class="nav-item')==9
    assert 'static/candles.js' in h and 'static/news.js' in h
    assert Path(server.ASSETS/'candles.js').exists()
    assert Path(server.ASSETS/'news.js').exists()
    health=TestClient(server.app).get('/healthz').json()
    assert health['stock_news'] and health['candlestick_chart'] and not health['news_affects_signal']
    assert not ('news' in server._payload('DEMO','스윙','demo',15)['final_reason'].casefold())


def test_news_api_bad_name_rejected(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    client=TestClient(server.app)
    server.CACHE.clear()
    assert client.get('/api/news?symbol=333333.KQ&name=%3Cscript%3E').status_code==422
