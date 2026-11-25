"""Domestic activity TOP20 + Naver official link: no guessed ranks, no buys."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
import pytest

import popular_stocks as pop
import server
from market_scanner import MarketScanError

KST=ZoneInfo('Asia/Seoul')


def row(i,market='kospi',turnover=10_000_000_000,volume=200_000,change=1):
    return {'symbol':f'{i:06d}'+('.KS' if market=='kospi' else '.KQ'),
            'name':f'종목{i}', 'market':market, 'close':10000,
            'turnover_krw':turnover, 'volume':volume,'change_pct':change}


def test_rankings_three_metrics_top20_and_markets():
    data=[row(i,turnover=(i+1)*1_000_000_000,volume=250000-i*10,change=i/10-2) for i in range(28)]
    data+= [row(100+i,'kosdaq',turnover=(50+i)*1_000_000_000,volume=1000+i,change=15-i) for i in range(7)]
    got=pop.rankings_from_rows(data,asof='20261111',provider='가상 공식 공급원',expected_date='20261111')
    assert got['marketwide'] and got['not_naver_ranking'] and got['not_buy_signal']
    assert got['universe_count']==35
    assert len(got['rankings']['all']['turnover'])==20
    assert got['rankings']['all']['turnover'][0]['symbol']=='000106.KQ'
    assert got['rankings']['kospi']['volume'][0]['symbol']=='000000.KS'
    assert got['rankings']['kosdaq']['gainers'][0]['symbol']=='000100.KQ'
    assert got['rankings']['all']['gainers'][0]['symbol']=='000100.KQ'
    assert all(i['actionable'] is False for i in got['rankings']['all']['turnover'])
    assert all('signal' not in i for i in got['rankings']['all']['turnover'])


def test_invalid_missing_or_zero_volume_rejected_and_no_invention():
    valid=row(123)
    items=[valid,dict(valid,symbol='NVDA'),dict(valid,volume=0,symbol='000234.KS'),
           dict(valid,symbol='000235.KS',market='kosdaq'),dict(valid,turnover_krw=None,symbol='000236.KS'),
           dict(valid,symbol='000237.KS',change_pct=float('nan'))]
    got=pop.rankings_from_rows(items,asof='20261111',provider='TEST')
    assert got['universe_count']==1
    assert got['rankings']['all']['volume'][0]['symbol']=='000123.KS'
    with pytest.raises(MarketScanError):pop.rankings_from_rows(items,asof='2026-11-11',provider='TEST')
    with pytest.raises(MarketScanError):pop.rankings_from_rows(items[1:],asof='20261111',provider='TEST')


def test_deterministic_order_equal_scores():
    a=pop.rankings_from_rows([row(2),row(1)],asof='20261111',provider='test')
    assert [r['symbol'] for r in a['rankings']['all']['turnover']]==['000001.KS','000002.KS']


def test_partial_market_snapshot_not_accepted():
    class Mock:
        def candidate_dates(self,now):return ['20261111']
        def fetch_day(self,date,market):return [row(1)] if market=='kospi' else []
    with pytest.raises(MarketScanError) as e:pop.fetch_popularity(source='fsc',key='unused',client=Mock(),now=datetime(2026,11,12,18,tzinfo=KST))
    assert e.value.code=='no_published_data'


def test_auth_error_fail_closed_no_fallback_to_fake_rank():
    class Mock:
        def candidate_dates(self,now):return ['20261111','20261110']
        def fetch_day(self,date,market):raise MarketScanError('fsc_auth','접근 권한 없음')
    with pytest.raises(MarketScanError) as e:pop.fetch_popularity(source='fsc',key='unused',client=Mock(),now=datetime(2026,11,12,18,tzinfo=KST))
    assert e.value.code=='fsc_auth'


def test_older_source_is_labeled_not_realtime():
    class Mock:
        source_label='시세 장마감 자료'
        def candidate_dates(self,now):return ['20261112','20261111']
        def fetch_day(self,date,market):
            if date=='20261112':raise MarketScanError('no_published_data','아직 미발행')
            return [row(1,market=market)]
    got=pop.fetch_popularity(source='fsc',key='unused',client=Mock(),now=datetime(2026,11,12,18,tzinfo=KST))
    assert got['snapshot_date_status']=='OLDER_THAN_EXPECTED'
    assert got['data_date']=='20261111' and got['not_realtime']


def test_old_snapshot_blocked():
    class Mock:
        def candidate_dates(self,now):return ['20261001']
        def fetch_day(self,date,market):return [row(1,market=market)]
    with pytest.raises(MarketScanError) as e:pop.fetch_popularity(source='krx',key='unused',client=Mock(),now=datetime(2026,11,12,18,tzinfo=KST))
    assert e.value.code=='stale_snapshot'


def test_popular_routes_not_public_when_token_required(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','x'*20)
    assert c.get('/api/popular/status').status_code==401
    assert c.post('/api/popular/run',json={'source':'auto'}).status_code==401
    headers={'Authorization':'Bearer '+'x'*20}
    assert c.get('/api/popular/status',headers=headers).status_code==200
    assert c.post('/api/popular/run',json={'source':'blah'},headers=headers).status_code==422
    monkeypatch.delenv('FSC_STOCK_API_KEY',raising=False)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    r=c.post('/api/popular/run',json={'source':'auto'},headers=headers)
    assert r.status_code==503 and r.json()['detail']['code']=='source_unavailable'
    assert 'secret' not in r.text


def test_popular_background_job_and_memory_reuse(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('FSC_STOCK_API_KEY','testkey')
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    results=[]
    def fake_fetch(*,source,key):
        results.append((source,key));return pop.rankings_from_rows([row(1),row(2,'kosdaq')],asof='20261111',provider='TEST')
    monkeypatch.setattr(server,'fetch_popularity',fake_fetch)
    with server.POPULAR_LOCK:server.POPULAR_STATE.clear();server.POPULAR_STATE.update(status='idle')
    a=c.post('/api/popular/run',json={'source':'auto'})
    assert a.status_code==200
    server.POPULAR_JOB.join(5)
    r=c.get('/api/popular/status').json()
    assert r['status']=='complete' and r['result']['not_buy_signal']
    assert r['result']['rankings']['all']['turnover']
    b=c.post('/api/popular/run',json={'source':'auto'})
    assert b.status_code==200 and len(results)==1
    assert 'testkey' not in str(b.json())
    with server.POPULAR_LOCK:server.POPULAR_STATE.clear();server.POPULAR_STATE.update(status='idle')


def test_popular_ui_naver_external_and_charts():
    root=Path(server.__file__).resolve().parent
    html=(root/'static/index.html').read_text()
    js=(root/'static/popular.js').read_text()
    appjs=(root/'static/app.js').read_text()
    css=(root/'static/styles.css').read_text()
    sw=(root/'static/sw.js').read_text()
    assert 'data-view="recommendations"' in html and 'id="popularPanel"' in html
    assert 'stock.naver.com/market/stock/kr/stocklist/top' in html
    assert 'rel="noopener noreferrer"' in html
    assert '/static/popular.js' in html and "'/static/popular.js'" in sw
    assert 'NOVAConnect?.openRecommendation' in js
    assert "['analyze','recommendations','backtest'" in appjs
    assert 'popular-results' in css
    assert not any(x in html.lower() for x in ['네이버 자동 스크래핑', '나스닥 검색'])
