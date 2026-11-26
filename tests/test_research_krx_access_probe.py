import json
import threading
import requests
import pytest
from research_krx_access_probe import check_one,worker,DAY,start_if_enabled,MARKETS
from research_krx_snapshot import SERVICES
from ops_preflight import JOBS

def state():
    return {'quiet_window':True,'calendar_verified':True,'jobs':dict.fromkeys(JOBS,'idle'),
        'window_overflow':False,'kis_load':dict.fromkeys(('foreground_active','physical_active','research_active','recent_provider_errors'),0)}
def payload():
    return {'OutBlock_1':[{'BAS_DD':'20261125','ISU_CD':s[:6],'MKT_NM':'KOSPI',
        'TDD_OPNPRC':'100','TDD_CLSPRC':'101','TDD_HGPRC':'102','TDD_LWPRC':'99',
        'ACC_TRDVOL':'1000','ACC_TRDVAL':'100000'} for s in SERVICES['stk_bydd_trd']]}
class Response:
    def __init__(self,p,status=200):self.p=p;self.status_code=status
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def iter_content(self,**kw):yield json.dumps(self.p).encode()
class Session:
    def __init__(self,p=None,status=200):self.p=p or payload();self.status=status;self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def get(self,url,**kw):
        self.calls.append((url,kw))
        return Response(self.p,self.status)
class Ledger:
    def __init__(self):self.claims=set()
    def initialize(self):pass
    def claim(self,market):
        if market in self.claims:return False
        self.claims.add(market);return True
def test_success_checks_real_schema_without_storing_or_leaking():
    s=Session();r=check_one(s,'SECRET_KEY','kospi')
    assert r['access_verified'] and r['schema_verified'] and r['selected_rows']==14
    assert r['stored_rows']==0 and not r['adjustment_verified']
    assert 'SECRET_KEY' not in json.dumps(r) and '100000' not in json.dumps(r)
    url,kw=s.calls[0]
    assert '/svc/apis/sto/stk_bydd_trd' in url and kw['allow_redirects'] is False and kw['params']=={'basDd':'20261125'}
def test_http_200_business_401_not_access():
    r=check_one(Session({'respCode':'401','respMsg':'Unauthorized API Call'}),'SECRET_KEY','kospi')
    assert r['status']=='auth_denied' and r['error_code']=='unauthorized_api_call' and not r['access_verified']
def test_wrong_date_not_schema():
    p=payload();p['OutBlock_1'][0]['BAS_DD']='20261124'
    r=check_one(Session(p),'SECRET_KEY','kospi')
    assert r['access_verified'] and not r['schema_verified'] and r['schema_reason']=='KRX_DATE_MISMATCH'
def test_index_not_approved_and_default_off(monkeypatch):
    monkeypatch.delenv('NOVA_KRX_ACCESS_PROBE',raising=False)
    assert start_if_enabled(threading.Event(),state) is False
    s=Session()
    with pytest.raises(ValueError):check_one(s,'SECRET_KEY','kospi_dd_trd')
    assert not s.calls
def test_attempts_once_across_worker_restarts():
    ledger=Ledger();s=Session({'respCode':'401','respMsg':'Unauthorized API Call'});events=[]
    for _ in range(2):
        worker(threading.Event(),state,'fake','SECRET_KEY',wait=lambda n:False,attempts=ledger,
               session_factory=lambda:s,emit=lambda e,v:events.append((e,v)))
    assert len(s.calls)==2 and ledger.claims==set(MARKETS)
    assert sum(e=='krx_access_probe_skip' for e,v in events)==2
def test_rate_limit_stops_second_service():
    ledger=Ledger();s=Session({'respCode':'429'},429)
    worker(threading.Event(),state,'fake','SECRET_KEY',wait=lambda n:False,attempts=ledger,
           session_factory=lambda:s,emit=lambda e,v:None)
    assert len(s.calls)==1 and ledger.claims=={'kospi'}
def test_busy_or_ledger_failure_never_sends():
    ledger=Ledger();s=Session()
    worker(threading.Event(),lambda:{},'fake','SECRET_KEY',wait=lambda n:False,attempts=ledger,
           session_factory=lambda:s,emit=lambda e,v:None)
    assert not s.calls and not ledger.claims
    ledger.claim=lambda m:(_ for _ in ()).throw(RuntimeError('SECRET'))
    worker(threading.Event(),state,'fake','SECRET_KEY',wait=lambda n:False,attempts=ledger,
           session_factory=lambda:s,emit=lambda e,v:None)
    assert not s.calls
