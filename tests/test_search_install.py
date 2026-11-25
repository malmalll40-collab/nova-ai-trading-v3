from pathlib import Path
from fastapi.testclient import TestClient
import pytest
import stock_lookup
import server

client = TestClient(server.app)


def test_naver_kosdaq_and_kospi(monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self):
            return {'result': {'items': [
                {'code':'086520','name':'에코프로','typeName':'코스닥'},
                {'code':'005930','name':'삼성전자','typeName':'코스피'},
                {'code':'006999','name':'이름없는시장','typeName':'국내주식'}
            ]}}
    monkeypatch.setattr(stock_lookup.requests, 'get', lambda *args,**kwargs: Resp())
    data = stock_lookup.search_stocks('에코프로')
    assert data['limited'] is False
    assert [x['symbol'] for x in data['results']] == ['086520.KQ','005930.KS']
    assert all(x['market'] in ('코스닥','코스피') for x in data['results'])


def test_fallback_limits_and_unknown_names(monkeypatch):
    def offline(*args,**kwargs): raise stock_lookup.requests.Timeout()
    monkeypatch.setattr(stock_lookup.requests, 'get', offline)
    data=stock_lookup.search_stocks('에코프로')
    assert data['limited'] is True
    assert {x['symbol'] for x in data['results']} == {'086520.KQ','247540.KQ'}
    empty=stock_lookup.search_stocks('절대로존재하지않는회사')
    assert empty['results']==[] and empty['limited']


def test_api_search_is_authenticated_and_rate_limited(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','x'*18)
    assert client.get('/api/search', params={'q':'에코프로'}).status_code == 401
    assert client.get('/api/search', params={'q':''}, headers={'Authorization':'Bearer '+'x'*18}).status_code == 422
    monkeypatch.setattr(server,'search_stocks',lambda q:{'query':q,'results':[{'name':'에코프로','code':'086520','symbol':'086520.KQ','market':'코스닥'}], 'limited':False})
    r=client.get('/api/search',params={'q':'에코프로'},headers={'Authorization':'Bearer '+'x'*18})
    assert r.status_code==200 and r.json()['results'][0]['symbol']=='086520.KQ'
    assert r.headers.get('cache-control') == 'no-store'


def test_pwa_manifest_home_install_ui():
    h=client.get('/').text
    assert '종목명' in h
    assert 'searchSuggestions' in h
    assert 'copyInstallLink' in h
    assert 'ChatGPT 등 앱 내부 브라우저' in h
    m=client.get('/manifest.webmanifest').json()
    assert m['start_url']=='/' and m['display']=='standalone'
    assert client.get('/static/icons/icon-192.png').status_code==200
    assert client.get('/static/icons/icon-512.png').status_code==200
    assert 'nova3-shell-v45' in client.get('/sw.js').text


def test_search_input_validation():
    for bad in ('abc<script>', 'a'*41, ''):
        with pytest.raises(ValueError):stock_lookup.search_stocks(bad)


def test_no_yahoo_secondary_name_lookup(monkeypatch):
    class Resp:
        def __init__(self,domain):self.domain=domain
        def raise_for_status(self):pass
        def json(self):
            if self.domain=='naver': return {'result': {'items': []}}
            return {'quotes':[{'symbol':'086520.KQ','shortname':'Ecopro','quoteType':'EQUITY'},
                              {'symbol':'TSLA','shortname':'Tesla','quoteType':'EQUITY'}]}
    monkeypatch.setattr(stock_lookup.requests,'get',lambda url, **kwargs:Resp('naver' if 'naver' in url else 'yahoo'))
    r=stock_lookup.search_stocks('ecopro')
    assert r['limited'] and not r['source'].startswith('Yahoo')
    assert all(x['symbol'].endswith(('.KS','.KQ')) for x in r['results'])
