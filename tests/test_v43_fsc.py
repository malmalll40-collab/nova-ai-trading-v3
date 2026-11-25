"""Mocked FSC stock quote tests: no live key, no invented recommendations."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import pytest
from fastapi.testclient import TestClient

import public_market_data as fsc
import market_scanner as scanner
import server


def item(code='005930',name='삼성전자',date='20261109',market='KOSPI'):
    return dict(basDt=date,srtnCd=code,itmsNm=name,mrktCtg=market,clpr='75,000',
                trqu='80,000',trPrc='11,000,000,000',mrktTotAmt='900,000,000,000',fltRt='2.2')

def payload(items, total=None, code='00'):
    return {'response':{'header':{'resultCode':code,'resultMsg':'NORMAL_SERVICE'},
                        'body':{'totalCount':len(items) if total is None else total,'items':{'item':items}}}}

class Resp:
    status_code=200
    def __init__(self, body, status=200):self.body=body;self.status_code=status
    def json(self):return self.body
class Session:
    def __init__(self,pages):self.pages=list(pages);self.calls=[]
    def get(self,url,params,timeout,headers):
        assert url==fsc.STOCK_URL and url.startswith('https://')
        assert params['serviceKey']=='secret+string' and params['resultType']=='json'
        assert headers['Accept']=='application/json'
        self.calls.append(params.copy())
        return self.pages.pop(0)


def test_fsc_parses_and_counts_dates_and_symbols():
    r,total,count=fsc.parse_fsc_page(payload([item(),item('086520','에코프로',market='KOSDAQ')]),'20261109')
    assert (total,count)==(2,2)
    assert [x['symbol'] for x in r]==['005930.KS','086520.KQ']
    assert r[0]['turnover_krw']==11_000_000_000
    assert r[1]['close']==75000


def test_missing_and_wrong_date_fail_closed():
    with pytest.raises(scanner.MarketScanError):fsc.parse_fsc_page({'no':'response'},'20261109')
    with pytest.raises(scanner.MarketScanError) as e:
        fsc.parse_fsc_page(payload([item(date='20261110')]),'20261109')
    assert e.value.code=='fsc_date_mismatch'
    with pytest.raises(scanner.MarketScanError):fsc.parse_fsc_page(payload([item()],code='30'),'20261109')


def test_fsc_pagination_full_universe_and_cached_by_date(monkeypatch):
    monkeypatch.setattr(fsc,'PAGE_SIZE',2)
    sess=Session([Resp(payload([item(),item('086520','에코프로',market='KOSDAQ')],3)),
                  Resp(payload([item('000660','SK하이닉스')],3))])
    cli=fsc.FSCStockClient('secret%2Bstring',session=sess)
    a=cli.fetch_day('20261109','kospi')
    b=cli.fetch_day('20261109','kosdaq')
    assert len(a)==2 and len(b)==1 and len(sess.calls)==2
    assert sess.calls[0]['pageNo']==1 and sess.calls[1]['pageNo']==2


def test_partial_data_and_http_failures_never_produce_recommendations(monkeypatch):
    monkeypatch.setattr(fsc,'PAGE_SIZE',2)
    sess=Session([Resp(payload([item(),item('000660','SK하이닉스')],4)),Resp(payload([],4))])
    with pytest.raises(scanner.MarketScanError) as e:fsc.FSCStockClient('secret+string',sess).fetch_day('20261109','kospi')
    assert e.value.code=='fsc_incomplete'
    sess=Session([Resp(payload([item()],1),401)])
    with pytest.raises(scanner.MarketScanError) as e:fsc.FSCStockClient('secret+string',sess).fetch_day('20261109','kospi')
    assert e.value.code=='fsc_auth' and 'secret' not in str(e.value)


def test_tplusone_cutoff_and_weekend():
    tz=ZoneInfo('Asia/Seoul')
    assert next(fsc.published_dates(datetime(2026,11,10,12,0,tzinfo=tz)))=='20261106'
    assert next(fsc.published_dates(datetime(2026,11,10,14,0,tzinfo=tz)))=='20261109'
    assert next(fsc.published_dates(datetime(2026,11,9,14,0,tzinfo=tz)))=='20261106'
    assert next(fsc.published_dates(datetime(2026,11,9,10,0,tzinfo=tz)))=='20261105'


def test_scan_fsc_uses_same_existing_nova_but_excludes_bulk_vendor_data():
    class Fake:
        source_label=fsc.FSCStockClient.source_label
        def candidate_dates(self, now):return ['20261109']
        def fetch_day(self,date,market):
            return [dict(symbol=('005930.KS' if market=='kospi' else '086520.KQ'),
                         name=('삼성전자' if market=='kospi' else '에코프로'),market=market,
                         close=75000,volume=100000,turnover_krw=11_000_000_000,
                         market_cap_krw=900_000_000_000,change_pct=2.2)]
    r=scanner.run_market_scan(market='all',api_key='not-used',client=Fake(),
        analyze=lambda sym:{'final_actionable_signal':'HOLD'},
        now=datetime(2026,11,10,14,0,tzinfo=fsc.KST))
    assert r['data_source_id']=='fsc' and r['total_market_rows']==2
    assert r['recommended_count']==0 and r['deep_target']==2


def test_scan_routes_prefer_fsc_and_require_independent_key(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear();monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('FSC_STOCK_API_KEY',raising=False)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    a=c.get('/api/recommendations/status').json()
    assert a['fsc_configured'] is False and a['krx_configured'] is False
    r=c.post('/api/recommendations/run',json={'market':'all','source':'fsc'})
    assert r.status_code==503 and r.json()['detail']['code']=='fsc_not_configured'
    assert c.post('/api/recommendations/run',json={'market':'all','source':'foo'}).status_code==422
    called=[]
    def fake_run(**kw):
        called.append(kw)
        return {'data_date':'20261109','recommended_count':0,'marketwide':True}
    monkeypatch.setattr(server,'run_market_scan',fake_run)
    monkeypatch.setenv('FSC_STOCK_API_KEY','private-data-key')
    monkeypatch.setenv('KRX_API_KEY','private-krx-key')
    with server.SCAN_LOCK:server.SCAN_STATE.clear();server.SCAN_STATE['status']='idle'
    res=c.post('/api/recommendations/run',json={'market':'all','source':'auto'})
    assert res.status_code==200
    server.SCAN_JOB.join(3)
    assert called[0]['client'].__class__ is fsc.FSCStockClient
    assert 'private-data-key' not in c.get('/api/recommendations/status').text


def test_auth_required_for_fsc_runs(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','X'*22)
    monkeypatch.setenv('FSC_STOCK_API_KEY','private-data-key')
    c=TestClient(server.app)
    assert c.post('/api/recommendations/run',json={'source':'fsc','market':'all'}).status_code==401


def test_ui_offline_and_provenance():
    html=(server.ASSETS/'index.html').read_text()
    js=(server.ASSETS/'recommendations.js').read_text()
    assert 'FSC_STOCK_API_KEY' in html and 'recommendSource' in html
    assert 'T+1' in html and 'data_source' in js
    assert 'source:' in js
    assert 'nova3-shell-v45' in (server.ASSETS/'sw.js').read_text()
