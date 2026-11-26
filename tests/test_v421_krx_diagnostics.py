"""NOVA v4.2.1 KRX production-only diagnostics; no real network or credentials."""
import json
from datetime import datetime
from fastapi.testclient import TestClient
import pytest
import requests
import market_scanner as ms
import server

class FakeResponse:
    def __init__(self, status=200, data=None, json_error=False):
        self.status_code=status
        self.data=data
        self.json_error=json_error
    def json(self):
        if self.json_error:
            raise ValueError('non JSON body')
        return self.data

class FakeSession:
    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]
    def get(self,url,*,params,headers,timeout,allow_redirects=None):
        self.calls.append((url,params,headers,timeout))
        assert url in ms.URLS.values() and '/sample/' not in url
        assert params == {'basDd':'20261109'}
        assert headers['AUTH_KEY']=='private-test-secret-krx-key'
        assert headers['Accept']=='application/json'
        return self.responses.pop(0)

@pytest.mark.parametrize('http,payload,error_code,state',[
    (401,{'respCode':'401','respMsg':'Unauthorized Key'},'unauthorized_key','auth_denied'),
    (200,{'respCode':'401','respMsg':'Unauthorized API Call'},'unauthorized_api_call','auth_denied'),
    (403,{'respCode':'403','respMsg':'Forbidden'},'forbidden','auth_denied'),
    (429,{'respCode':'429','respMsg':'Too Many Requests'},'provider_rate_limited','rate_limited'),
    (200,{'respCode':'999','respMsg':'Custom vendor error'},'provider_rejected','provider_error'),
    (200,{'respCode':'0','OutBlock_1':[]},'no_published_data','no_data'),
    (200,{'OutBlock_1':[{'ISU_CD':'005930'}]},'ok','success'),
    (200,None,'provider_format','provider_error'),
])
def test_classification_never_echoes_key(http,payload,error_code,state):
    r=ms.classify_krx_response(http,payload,'kospi')
    assert r['error_code']==error_code and r['state']==state
    assert r['api_id']=='stk_bydd_trd' and r['request_type']=='GET'
    assert r['auth_header_sent'] is True
    assert 'private-test-secret' not in json.dumps(r)
    assert 'OutBlock_1' not in r


def test_auth_error_takes_precedence_even_with_rows():
    r=ms.classify_krx_response(200,{'respCode':'401','respMsg':'Unauthorized API Call',
       'OutBlock_1':[{'ISU_CD':'005930'}]},'kosdaq')
    assert r['state']=='auth_denied' and r['record_count'] is None


def test_diag_calls_both_production_endpoints_and_returns_no_secret():
    secret='private-test-secret-krx-key'
    sess=FakeSession([FakeResponse(200,{'respCode':'401','respMsg':'Unauthorized API Call'}),
        FakeResponse(200,{'OutBlock_1':[{'ISU_CD':'005930'},{'ISU_CD':'000660'}]})])
    diag=ms.diagnose_connection(secret,'20261109',['kospi','kosdaq'],session=sess)
    assert diag['configured'] is True and diag['date']=='20261109'
    assert diag['results'][0]['state']=='auth_denied'
    assert diag['results'][1]['state']=='success'
    assert diag['results'][1]['record_count']==2
    assert diag['results'][1]['api_id']=='ksq_bydd_trd'
    assert [c[0] for c in sess.calls]==[ms.URLS['kospi'],ms.URLS['kosdaq']]
    assert secret not in json.dumps(diag,ensure_ascii=False)


def test_provider_secret_string_never_echoed_in_error():
    secret='private-test-secret-krx-key'
    sess=FakeSession([FakeResponse(401,{'respCode':'401','respMsg':'Unauthorized Key: '+secret})])
    diag=ms.diagnose_connection(secret,'20261109',['kospi'],session=sess)
    assert secret not in json.dumps(diag)
    assert diag['results'][0]['state']=='auth_denied'


def test_network_error_is_safe():
    class BadSession:
        def get(self,*a,**kw):
            raise requests.Timeout('very-private-secret in request url')
    d=ms.diagnose_connection('private-test-secret-krx-key','20261109',['kospi'],session=BadSession())
    assert d['results'][0]['state']=='network_error'
    assert 'very-private-secret' not in json.dumps(d)


def test_scanner_stops_on_json_401_without_retrying_dates():
    sess=FakeSession([FakeResponse(200,{'respCode':'401','respMsg':'Unauthorized API Call'})])
    with pytest.raises(ms.MarketScanError) as err:
        ms.run_market_scan(market='kospi',api_key='private-test-secret-krx-key',
          analyze=lambda x:None, now=datetime(2026,11,10,10,0,tzinfo=ms.KST),
          client=ms.KRXClient('private-test-secret-krx-key',session=sess))
    assert err.value.code=='not_approved'
    assert len(sess.calls)==1, 'do not make 10 retry calls on authorization failure'


def test_scanner_stops_on_rate_limit():
    sess=FakeSession([FakeResponse(429,{'respCode':'429'})])
    with pytest.raises(ms.MarketScanError) as err:
        ms.run_market_scan(market='kospi',api_key='private-test-secret-krx-key',
          analyze=lambda x:None, now=datetime(2026,11,10,10,0,tzinfo=ms.KST),
          client=ms.KRXClient('private-test-secret-krx-key',session=sess))
    assert err.value.code=='provider_rate_limited'
    assert len(sess.calls)==1


def test_route_requires_login_and_never_returns_secret(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','a-secure-access-token-long')
    monkeypatch.setenv('KRX_API_KEY','private-test-secret-krx-key')
    assert c.post('/api/recommendations/diagnose').status_code==401
    def fake_diagnose(key,date,markets):
        assert key=='private-test-secret-krx-key'
        assert markets==['kospi','kosdaq']
        return {'configured':True,'date':date,'results':[{'market':'kospi','http_status':401,'state':'auth_denied'}]}
    monkeypatch.setattr(server,'diagnose_connection',fake_diagnose)
    r=c.post('/api/recommendations/diagnose',headers={'Authorization':'Bearer a-secure-access-token-long'})
    assert r.status_code==200 and r.headers['Cache-Control']=='no-store'
    assert r.json()['configured'] is True
    assert 'private-test-secret' not in r.text


def test_route_no_key_returns_explicit_unconfigured(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    r=c.post('/api/recommendations/diagnose')
    assert r.status_code==200 and r.json()['configured'] is False
    assert r.json()['results']==[]


def test_mobile_ui_diagnostic_button_is_read_only_and_safe():
    h=(server.ASSETS/'index.html').read_text(encoding='utf8')
    j=(server.ASSETS/'recommendations.js').read_text(encoding='utf8')
    assert 'id="recommendDiagnose"' in h and 'id="recommendDiagnosis"' in h
    assert '/api/recommendations/diagnose' in j
    assert 'itemNode.textContent' in j and 'out.append(itemNode)' in j
    assert server.app.openapi_url is None


def test_manual_date_and_automatic_previous_weekday():
    now=datetime(2026,11,27,7,50,tzinfo=ms.KST)
    assert ms.diagnostic_dates(now)==('20261126','20261125')
    assert ms.diagnostic_dates(now,'2026-11-20')==('20261120',)

def test_manual_date_rejects_future_weekend_invalid_and_unsupported():
    now=datetime(2026,11,27,7,50,tzinfo=ms.KST)
    for day in ('2026-11-27','2026-11-22','2026-02-30','2009-12-31','20261125'):
        with pytest.raises(ValueError):ms.diagnostic_dates(now,day)

def test_bounded_probe_only_retries_empty_market():
    calls=[]
    def probe(key,day,markets):
        calls.append((day,list(markets)))
        rows=[{'market':m,'state':'no_data' if day=='20261126' and m=='kosdaq' else 'success'} for m in markets]
        return {'configured':True,'results':rows}
    result=ms.diagnose_bounded('fixture',('20261126','20261125'),probe=probe)
    assert calls==[('20261126',['kospi','kosdaq']),('20261125',['kosdaq'])]
    assert [r['date'] for r in result['results']]==['20261126','20261126','20261125']
    assert result['checked_dates']==['20261126','20261125']

def test_bounded_probe_stops_on_error_or_malformed_result():
    for state in ('auth_denied','network_error','rate_limited','provider_error'):
        calls=[]
        def probe(key,day,markets):
            calls.append(day)
            return {'configured':True,'results':[{'market':'kospi','state':state},{'market':'kosdaq','state':'no_data'}]}
        r=ms.diagnose_bounded('fixture',('20261126','20261125'),probe=probe)
        assert calls==['20261126'] and len(r['results'])==2

def test_bounded_probe_manual_single_date_and_maximum_calls():
    calls=[]
    def probe(key,day,markets):
        calls.extend((day,m) for m in markets)
        return {'configured':True,'results':[{'market':m,'state':'no_data'} for m in markets]}
    assert ms.diagnose_bounded('fixture',('20261120',),probe=probe)['checked_dates']==['20261120']
    assert len(calls)==2
    calls.clear()
    ms.diagnose_bounded('fixture',('20261126','20261125'),probe=probe)
    assert len(calls)==4
    with pytest.raises(ValueError):ms.diagnose_bounded('fixture',('20261126','20261125','20261124'),probe=probe)
    assert len(calls)==4

def test_explicit_date_route_and_invalid_date_do_not_reach_provider(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KRX_API_KEY','private-test-secret-krx-key')
    calls=[]
    def probe(key,day,markets):
        calls.append((day,markets))
        return {'configured':True,'results':[{'market':m,'state':'no_data'} for m in markets]}
    monkeypatch.setattr(server,'diagnose_connection',probe)
    r=c.post('/api/recommendations/diagnose?basis_date=2026-11-20')
    assert r.status_code==200 and r.json()['checked_dates']==['20261120']
    assert calls==[('20261120',['kospi','kosdaq'])]
    assert c.post('/api/recommendations/diagnose?basis_date=not-a-date').status_code==400
    assert len(calls)==1

def test_route_busy_guard_and_release(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KRX_API_KEY','private-test-secret-krx-key')
    monkeypatch.setattr(server,'diagnose_connection',lambda *a:pytest.fail('must not dispatch'))
    assert server.KRX_DIAG_LOCK.acquire(False)
    try:assert c.post('/api/recommendations/diagnose').status_code==409
    finally:server.KRX_DIAG_LOCK.release()
    assert not server.KRX_DIAG_LOCK.locked()

def test_date_ui_has_explicit_empty_result_and_date_labels():
    h=(server.ASSETS/'index.html').read_text(encoding='utf8')
    j=(server.ASSETS/'recommendations.js').read_text(encoding='utf8')
    assert 'type="date" id="recommendDiagnoseDate"' in h
    assert '?basis_date='+'' in j and 'encodeURIComponent(date)' in j
    assert 'item.date||d.date' in j and '빈 응답' in j
