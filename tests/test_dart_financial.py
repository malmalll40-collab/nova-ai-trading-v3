"""Offline, deterministic tests. All financial response numbers are fabricated fixtures."""
import io
import json
import zipfile
from datetime import datetime, timezone, timedelta

import pytest

from dart_financial import DARTClient, DARTError, _parse_registry, parse_amount, _items_for_financial


class FakeResp:
    def __init__(self, body=None, content=b''):
        self.body=body or {}
        self.content=content
    def json(self):return self.body
    def raise_for_status(self):pass

class FakeSession:
    def __init__(self, registry):
        self.registry=registry
        self.calls=[]
        self.status='000'
    def get(self,url,params=None,timeout=None):
        self.calls.append((url,params,timeout))
        if url.endswith('corpCode.xml'):return FakeResp(content=self.registry)
        if url.endswith('company.json'):
            return FakeResp({'status':'000','corp_name':'테스트법인','corp_cls':'K'})
        if url.endswith('list.json'):
            return FakeResp({'status':'000','list':[{'rcept_no':'20260512000001','rcept_dt':'20260512','report_nm':'정기보고서'},
                                            {'rcept_no':'bad','rcept_dt':'20260512','report_nm':'INVALID'}]})
        if url.endswith('fnlttSinglAcntAll.json'):
            if self.status!='000':return FakeResp({'status':self.status})
            # All data here is made up and only used to test extraction.
            if params['fs_div']=='OFS':return FakeResp({'status':'013'})
            if params['reprt_code']=='11014' and params['bsns_year']=='2026':return FakeResp({'status':'013'})
            if params['reprt_code']=='11013' and params['bsns_year']=='2026':return FakeResp({'status':'013'})
            rows=[dict(sj_div=sect,account_id=account,account_nm=name,account_detail='-',thstrm_amount=val)
                  for sect,account,name,val in [
                      ('IS','ifrs-full_Revenue','매출액','230,000,000,000'),
                      ('IS','dart_OperatingIncomeLoss','영업이익','20,000,000,000'),
                      ('IS','ifrs-full_ProfitLoss','당기순이익','12,000,000,000'),
                      ('BS','ifrs-full_Assets','자산총계','450,000,000,000'),
                      ('BS','ifrs-full_Liabilities','부채총계','190,000,000,000'),
                      ('BS','ifrs-full_Equity','자본총계','260,000,000,000'),
                      ('CF','ifrs-full_CashFlowsFromUsedInOperatingActivities','영업활동현금흐름','18,000,000,000'),
                  ]]
            if params['bsns_year']=='2024':
                rows[0]['thstrm_amount']='200,000,000,000'
            return FakeResp({'status':'000','list':rows})
        raise AssertionError('unknown URL')


def registry_zip():
    xml='<?xml version="1.0" encoding="UTF-8"?><result><list><corp_code>00222222</corp_code><corp_name>테스트법인</corp_name><stock_code>123456</stock_code></list><list><corp_code>33333333</corp_code><stock_code> </stock_code></list></result>'.encode()
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('CORPCODE.xml',xml)
    return b.getvalue()


def client():
    fake=FakeSession(registry_zip())
    c=DARTClient('a'*40,session=fake,clock=lambda:datetime(2026,11,9,tzinfo=timezone(timedelta(hours=9))))
    return c,fake


def test_registry():
    assert _parse_registry(registry_zip())=={'123456':{'corp_code':'00222222','corp_name':'테스트법인'}}


@pytest.mark.parametrize(('raw','expect'), [('12,500',12500),('(1,250)',-1250),('△1,000',-1000),('-',None),('abc',None),('',None)])
def test_amount(raw,expect):assert parse_amount(raw)==expect


def test_all_metrics_and_disclosures():
    c,fake=client()
    result=c.overview('123456.KQ',years=3)
    assert result['company_name']=='테스트법인'
    assert result['market']=='코스닥'
    assert result['annual'][0]['year']==2025
    assert result['annual'][0]['fs_div']=='CFS'
    assert result['metrics']['revenue']==230_000_000_000
    assert result['metrics']['revenue_yoy_pct']==15.0
    assert result['metrics']['debt_ratio_pct']==73.08
    assert result['interim']['type']=='반기보고서'
    assert len(result['disclosures'])==1
    assert result['disclosures'][0]['url']=='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260512000001'
    assert all('crtfc_key' in kwargs for _,kwargs,_ in fake.calls)
    c.overview('123456.KQ',years=2)
    assert sum(url.endswith('corpCode.xml') for url,_,_ in fake.calls)==1


def test_no_key():
    with pytest.raises(DARTError,match='인증키'):
        DARTClient('')


def test_wrong_suffix_rejected_without_requests():
    c,fake=client()
    with pytest.raises(DARTError,match='시장 구분'):
        c.overview('123456.KS')
    with pytest.raises(DARTError,match='코스피'):
        c.overview('AAPL')
    assert not any('fnltt' in x[0] for x in fake.calls)


def test_no_financial_information():
    c,fake=client();fake.status='013'
    result=c.overview('123456.KQ',years=2)
    assert result['status']=='partial'
    assert result['metrics']=={}
    assert not result['annual']


def test_parse_restricts_sections_and_details():
    rows=[{'sj_div':'IS','account_id':'ifrs-full_Revenue','account_detail':'자본','thstrm_amount':'1'},
          {'sj_div':'BS','account_id':'ifrs-full_Revenue','thstrm_amount':'2'},
          {'sj_div':'IS','account_id':'ifrs-full_Revenue','account_detail':'-','thstrm_amount':'3'}]
    assert _items_for_financial(rows)['revenue']==3


def test_error_code_does_not_expose_key():
    c,fake=client(); fake.status='020'
    with pytest.raises(DARTError,match='요청 한도') as exc:
        c.overview('123456.KQ',years=2)
    assert 'a'*40 not in str(exc.value)


def test_fastapi_requires_auth_and_key(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','Z'*22)
    monkeypatch.delenv('OPENDART_API_KEY',raising=False)
    r=TestClient(server.app).get('/api/fundamentals?symbol=123456.KQ')
    assert r.status_code==401
    r=TestClient(server.app).get('/api/fundamentals?symbol=123456.KQ',headers={'Authorization':'Bearer '+'Z'*22})
    assert r.status_code==503
    assert r.json()['detail']['code']=='no_key'
    assert TestClient(server.app).get('/healthz').json()['app_version']=='5.7.0'


def test_fastapi_finance_fixture(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','Z'*22)
    c,_=client()
    monkeypatch.setattr(server,'get_client',lambda:c)
    r=TestClient(server.app).get('/api/fundamentals?symbol=123456.KQ',headers={'Authorization':'Bearer '+'Z'*22})
    assert r.status_code==200
    assert r.json()['metrics']['revenue']==230_000_000_000
    assert 'crtfc_key' not in r.text


def test_financial_tab_and_lazy_endpoint_present():
    from pathlib import Path
    import server
    h=Path(server.ASSETS/'index.html').read_text()
    js=Path(server.ASSETS/'app.js').read_text()
    css=Path(server.ASSETS/'styles.css').read_text()
    assert 'data-view="finance"' in h
    assert 'id="financeMetrics"' in h
    assert h.index('id="view-finance"') > h.index('id="results"')
    assert 'loadFinance()' in js and '/api/fundamentals?' in js
    assert '.finance-open #results' in css
