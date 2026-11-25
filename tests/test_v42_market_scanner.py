"""KRX full-market scan + screen + policy tests using ONLY mocked market data."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import pytest
from fastapi.testclient import TestClient
import market_scanner as ms
import server


def row(code='005930',name='삼성전자',date='20261109',turn='10,000,000,000',change='2.20'):
    return {'ISU_CD':code,'ISU_NM':name,'BAS_DD':date,'TDD_CLSPRC':'50,000',
      'ACC_TRDVOL':'200,000','ACC_TRDVAL':turn,'MKTCAP':'400,000,000,000',
      'FLUC_RT':change}


def test_exact_krx_codes_and_numeric_formats():
    d=ms.parse_rows({'OutBlock_1':[row(),row(code='KR7005930003'),row(code='bad'),row(date='20261108')]},'kospi','20261109')
    assert len(d)==2 and d[0]['symbol']=='005930.KS'
    assert d[0]['turnover_krw']==10_000_000_000
    assert d[0]['change_pct']==2.2
    assert ms.parse_rows({'OutBlock_1':[row(code='086520',name='에코프로')]},'kosdaq','20261109')[0]['symbol']=='086520.KQ'


def test_halted_stocks_counted_in_universe_but_never_recommended():
    halted=row('000001',name='거래정지회사');halted['TDD_CLSPRC']='-';halted['ACC_TRDVOL']='0'
    universe=ms.parse_rows({'OutBlock_1':[row(),halted]},'kospi','20261109')
    assert len(universe)==2
    screened,excluded=ms.screen(universe)
    assert len(screened)==1 and excluded['invalid_or_halted']==1


def test_provider_auth_error_not_empty_market():
    with pytest.raises(ms.MarketScanError) as exc:
        ms.parse_rows({'error':'Unauthorized API Call'},'kospi','20261109')
    assert exc.value.code=='provider_auth_or_format'
    with pytest.raises(ms.MarketScanError):
        ms.parse_rows([], 'kospi', '20261109')


def test_http_key_only_in_header_and_no_leak():
    class Response:
        status_code=401
    class Session:
        def get(self,url,params,headers,timeout):
            assert headers['AUTH_KEY']=='very-long-secret-krx-key'
            assert 'secret' not in url and 'secret' not in str(params)
            return Response()
    api=ms.KRXClient('very-long-secret-krx-key',session=Session())
    with pytest.raises(ms.MarketScanError) as exc:
        api.fetch_day('20261109','kospi')
    assert exc.value.code=='not_approved'
    assert 'very-long' not in str(exc.value)


def test_last_completed_krx_day_only():
    tz=ZoneInfo('Asia/Seoul')
    d=list(ms.completed_dates(datetime(2026,11,10,9,5,tzinfo=tz)))
    assert d[0]=='20261109'
    d=list(ms.completed_dates(datetime(2026,11,10,19,0,tzinfo=tz)))
    assert d[0]=='20261110'
    d=list(ms.completed_dates(datetime(2026,11,9,10,0,tzinfo=tz)))
    assert d[0]=='20261106'


def test_cheap_filter_tracks_each_exclusion_without_fake_trend():
    values=[{'symbol':f'{i:06}.KS','name':str(i),'market':'kospi','close':10000,
       'market_cap_krw':200_000_000_000,'turnover_krw':20_000_000_000,'change_pct':2.,'volume':100} for i in range(5)]
    values[1]['close']=500
    values[2]['turnover_krw']=500_000
    values[3]['change_pct']=25
    values.append(values[0].copy())
    results,excluded=ms.screen(values)
    assert len(results)==2
    assert excluded=={'duplicate':1,'invalid_or_halted':0,'illiquid':1,'price_or_cap':1,'extreme_move':1}
    assert all(0 <= x['screen_score'] <= 85 for x in results)


def sufficient_detail(date='2026-11-09'):
    return {'mode':'스윙','final_actionable_signal':'BUY','financial_gate':'PASS','signal_is_current':True,
      'last_completed_bar':date+'T00:00:00','quality_gate':{'strict_signal':'BUY','passed_count':5},
      'signal_performance':{'selected_validation_positive':True,'selected_active_now':True,
        'selected_name':'EMA 상승','selected_id':'02','min_validation':15,
        'roles':[{'id':'02','validation':{'samples':22,'hit_pct':59.1,'mean_net_pct':1.7,'wilson_lower_pct':38.1}}]},
      'long_risk_reward':1.7,'reference':10000,'long_stop_reference':9200,'long_target_reference':11360,
      'provider':'Yahoo Finance','is_demo':False,'is_stale':False}


def test_recommend_only_with_final_and_holdout_and_matching_date():
    s={'symbol':'005930.KS','name':'삼성전자','market':'kospi','close':10000,'change_pct':2.,
       'turnover_krw':20_000_000_000,'screen_score':70.}
    d=sufficient_detail()
    rec,why=ms.evaluate_candidate(s,d,'20261109')
    assert not why and rec['signal']=='BUY' and rec['validation']['samples']==22
    assert rec['not_probability'] is True
    for key,value in [('financial_gate','UNKNOWN'),('final_actionable_signal','HOLD'),
                      ('is_stale',True),('last_completed_bar','2026-11-08'),
                      ('long_risk_reward',1.0)]:
        x=dict(d,key=value) if False else dict(d)
        x[key]=value
        assert ms.evaluate_candidate(s,x,'20261109')[0] is None
    x=dict(d,signal_performance=dict(d['signal_performance'],selected_validation_positive=False))
    assert ms.evaluate_candidate(s,x,'20261109')[0] is None


def test_full_universe_and_max_48_deep_verified():
    class FakeKRX:
        def fetch_day(self,date,market):
            assert date=='20261109'
            return [{'symbol':f'{i:06}'+('.KS' if market=='kospi' else '.KQ'),
              'name':f'종목{i}','market':market,'close':10000.,'volume':100000,
              'turnover_krw':20_000_000_000+i*50000000,'market_cap_krw':500_000_000_000,
              'change_pct':2.} for i in range(15 if market=='kospi' else 10)]
    called=[]
    def analyze(symbol):
        called.append(symbol)
        return sufficient_detail()
    d=ms.run_market_scan(market='all',api_key='x'*20,analyze=analyze,
         now=datetime(2026,11,10,10,0,tzinfo=ms.KST),client=FakeKRX())
    assert d['total_market_rows']==25 and d['screen_passed']==25
    assert d['deep_target']==25 and len(called)==25 and d['recommended_count']==25
    assert d['marketwide'] and not d['realtime'] and len(d['markets_scanned'])==2
    assert '1차' in d['method']


def test_missing_market_is_not_silently_accepted_as_all():
    class Fake:
        def fetch_day(self,date,market):
            return [dict(symbol='005930.KS')] if market=='kospi' else []
    with pytest.raises(ms.MarketScanError) as e:
        ms.run_market_scan(market='all',api_key='x'*20,analyze=lambda _:None,
             now=datetime(2026,11,10,10,tzinfo=ms.KST),client=Fake())
    assert e.value.code=='no_published_data'


def test_missing_krx_key_returns_503_no_fabricated_results(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear();monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    assert c.get('/api/recommendations/status').json()['krx_configured'] is False
    r=c.post('/api/recommendations/run',json={'market':'all'})
    assert r.status_code==503 and r.json()['detail']['code']=='krx_not_configured'
    assert c.post('/api/recommendations/run',json={'market':'all','force_demo':True}).status_code==503


def test_recommendations_routes_reject_unauthorized_and_bad_market(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','T'*22)
    monkeypatch.setenv('KRX_API_KEY','K'*20)
    assert c.get('/api/recommendations/status').status_code==401
    assert c.post('/api/recommendations/run',json={'market':'all'}).status_code==401
    h={'Authorization':'Bearer '+'T'*22}
    assert c.post('/api/recommendations/run',json={'market':'us'},headers=h).status_code==422
    assert c.get('/api/recommendations/status',headers=h).status_code==200


def test_server_scan_job_and_status_cache_mocked(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KRX_API_KEY','K'*20)
    def fake_run(**kwargs):
        kwargs['progress']({'phase':'deep','done':1,'total':2,'message':'검사 중'})
        return {'data_date':'20261109','total_market_rows':2,'recommended_count':0,
                'recommendations':[],'marketwide':True,'market':'all'}
    monkeypatch.setattr(server,'run_market_scan',fake_run)
    with server.SCAN_LOCK:
        server.SCAN_STATE.clear();server.SCAN_STATE.update(status='idle')
    r=c.post('/api/recommendations/run',json={'market':'all'})
    assert r.status_code==200
    server.SCAN_JOB.join(2)
    j=c.get('/api/recommendations/status').json()
    assert j['status']=='complete' and j['result']['total_market_rows']==2
    old=j['job_id']
    assert c.post('/api/recommendations/run',json={'market':'all'}).json()['job_id']==old


def test_ui_accessibility_and_no_fake_results():
    h=(server.ASSETS/'index.html').read_text(encoding='utf8')
    j=(server.ASSETS/'recommendations.js').read_text(encoding='utf8')
    sw=(server.ASSETS/'sw.js').read_text(encoding='utf8')
    assert 'id="view-recommendations"' in h and 'data-view="recommendations"' in h
    assert h.count('class="nav-item')==9
    assert 'KRX_API_KEY' in h and '최대 8개' in h
    assert 'textContent' in j and 'NOVAScanner' in j
    assert '/static/recommendations.js' in sw and 'nova3-shell-v45' in sw
    assert server.app.openapi_url is None
