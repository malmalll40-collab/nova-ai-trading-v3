from datetime import date
import json
import requests
import pytest
import research_krx_collection as rc

def test_plan_pilot_required_and_resume_skips_stored():
    days=(date(2026,11,19),rc.PILOT,date(2026,11,25))
    assert rc.plan(days,set(),rc.BATCH_MODE)==[(s,rc.PILOT) for s in rc.ORDER]
    saved={(s,rc.PILOT.isoformat()) for s in rc.ORDER}
    assert rc.plan(days,saved,rc.PILOT_MODE)==[]
    assert rc.plan(days,saved,rc.BATCH_MODE)==[(s,d) for d in (days[2],days[0]) for s in rc.ORDER]
    saved.add((rc.ORDER[0],days[2].isoformat()))
    assert (rc.ORDER[0],days[2]) not in rc.plan(days,saved,rc.BATCH_MODE)

def test_batch_cap_and_bad_plan():
    from datetime import timedelta
    days=tuple(rc.PILOT+timedelta(days=i) for i in range(30))
    saved={(s,rc.PILOT.isoformat()) for s in rc.ORDER}
    assert len(rc.plan(days,saved,rc.BATCH_MODE))==12
    with pytest.raises(ValueError):rc.plan(days,saved,'unbounded')

class Response:
    def __init__(self,payload,status=200):
        self.status_code=status;self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def iter_content(self,chunk_size):yield json.dumps(self.payload).encode()

class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,url,**kw):
        self.calls.append((url,kw))
        return self.responses.pop(0)

def test_fixed_index_and_stock_endpoints_and_no_redirect():
    s=Session([Response({'OutBlock_1':[{'fixture':1}]}) for _ in rc.ORDER])
    for service in rc.ORDER:
        assert rc.read_one(s,'fixture',service,rc.PILOT)[0]=='success'
    assert [u for u,k in s.calls]==[rc.URLS[x] for x in rc.ORDER]
    assert '/idx/' in s.calls[0][0] and '/sto/' in s.calls[2][0]
    assert all(k['allow_redirects'] is False and k['params']=={'basDd':'20261120'} for u,k in s.calls)

def test_provider_auth_and_empty_never_return_payload():
    s=Session([Response({'respCode':'401','OutBlock_1':[{'fixture':1}]}),
        Response({'OutBlock_1':[]}),Response({},429)])
    assert [rc.read_one(s,'fixture',rc.ORDER[0],rc.PILOT) for _ in range(3)]==[
        ('auth_denied',None),('no_data',None),('rate_limited',None)]

def test_network_error_sanitized():
    class Broken:
        def get(self,*a,**kw):raise requests.Timeout('secret fixture')
    assert rc.read_one(Broken(),'fixture',rc.ORDER[0],rc.PILOT)==('NETWORK_ERROR',None)

class Store:
    sessions=(date(2026,11,19),rc.PILOT)
    def __init__(self):self.saved=set();self.held=set();self.calls=[]
    def setup(self):pass
    def status(self):return {'stored_rows':len(self.saved)},self.saved,self.held
    def claim(self,s,d):
        self.calls.append((s,d))
        if self.held:return False
        self.held.add((s,d.isoformat()));return True
    def fail(self,s,d):pass
    def save(self,s,d,payload,authorized):
        assert authorized(s,d) and not authorized('arbitrary',d)
        self.held.remove((s,d.isoformat()));self.saved.add((s,d.isoformat()))
        return len(rc.SERVICES[s])

def run(store,session,mode=rc.PILOT_MODE,ready=lambda:True):
    logs=[]
    rc.collect(store,session,'fixture',mode,ready,lambda n:False,lambda name,data:logs.append((name,data)))
    return logs

def test_pilot_stores_only_once_and_logs_counts():
    store=Store()
    s=Session([Response({'OutBlock_1':[{'fixture':'secret'}]}) for _ in rc.ORDER])
    logs=run(store,s)
    assert len(store.saved)==4 and len(s.calls)==4 and not store.held
    assert sum(d.get('new_rows',0) for _,d in logs)==22
    assert 'secret' not in json.dumps(logs)
    run(store,s)
    assert len(s.calls)==4

def test_failure_stops_and_held_blocks_restart():
    store=Store();s=Session([Response({'OutBlock_1':[]})])
    logs=run(store,s)
    assert len(s.calls)==1 and store.held and not store.saved
    logs=run(store,s)
    assert logs[-1][1]['reason']=='UNRESOLVED_CLAIM' and len(s.calls)==1

def test_window_change_after_claim_never_dispatches():
    store=Store();s=Session([]);states=iter([True,True,False])
    logs=run(store,s,ready=lambda:next(states))
    assert not s.calls and store.held and logs[-1][1]['reason']=='WINDOW_CHANGED_AFTER_CLAIM'

def test_schema_failure_keeps_claim():
    class Bad(Store):
        def save(self,*a,**kw):raise ValueError('KRX_COHORT_INCOMPLETE')
    store=Bad();s=Session([Response({'OutBlock_1':[{}]})])
    logs=run(store,s)
    assert store.held and not store.saved
    assert logs[-2][1]['status']=='KRX_COHORT_INCOMPLETE'

def test_default_disabled(monkeypatch):
    monkeypatch.delenv(rc.FLAG,raising=False)
    assert rc.start_if_enabled(None,None) is False


def test_pilot_independent_window_preserves_foreground_and_calendar_guards():
    from ops_preflight import JOBS
    s={'quiet_window':False,'calendar_verified':True,'krx_regular_status':'OPEN',
       'jobs':dict.fromkeys(JOBS,'idle'),'window_overflow':False,
       'kis_load':dict.fromkeys(('foreground_active','physical_active','research_active',
                                 'recent_provider_errors','physical_attempts_60s'),0)}
    assert rc.collection_eligible(s,rc.PILOT_MODE)
    assert not rc.collection_eligible(s,rc.BATCH_MODE)
    assert not rc.collection_eligible(s,'unknown')
    assert rc.collection_eligible({**s,'quiet_window':True},rc.BATCH_MODE)
    for patch in ({'calendar_verified':False},{'krx_regular_status':'UNKNOWN'},
                  {'window_overflow':True},{'jobs':{}},
                  {'jobs':{**s['jobs'],'scan_full':'running'}}):
        assert not rc.collection_eligible({**s,**patch},rc.PILOT_MODE)
    for k in s['kis_load']:
        for value in (1,None):
            assert not rc.collection_eligible({**s,'kis_load':{**s['kis_load'],k:value}},rc.PILOT_MODE)


def _idle_snapshot():
    from ops_preflight import JOBS
    return {'quiet_window':False,'calendar_verified':True,'krx_regular_status':'OPEN',
            'jobs':dict.fromkeys(JOBS,'idle'),'window_overflow':False,
            'kis_load':dict.fromkeys(('foreground_active','physical_active','research_active',
                                      'recent_provider_errors','physical_attempts_60s'),0)}

def test_pilot_admission_polls_local_state_until_idle():
    import threading
    good=_idle_snapshot()
    busy={**good,'kis_load':{**good['kis_load'],'physical_attempts_60s':1}}
    states=iter([busy,good]);waits=[];logs=[]
    assert rc.await_admission(threading.Event(),lambda:next(states),rc.PILOT_MODE,
           lambda n:waits.append(n) or False,lambda k,d:logs.append(d))
    assert waits==[30]
    assert logs[0]['reasons']==['PHYSICAL_ATTEMPTS_60S']
    assert logs[1]['status']=='READY'

def test_admission_bounded_and_batch_not_retried():
    import threading
    good=_idle_snapshot()
    busy={**good,'jobs':{**good['jobs'],'scan_full':'running'}}
    for mode,n in ((rc.PILOT_MODE,11),(rc.BATCH_MODE,1)):
        waits=[];logs=[]
        assert not rc.await_admission(threading.Event(),lambda:busy,mode,
               lambda t:waits.append(t) or False,lambda k,d:logs.append(d))
        assert len(logs)==n and waits==[30]*(n-1)
        assert all('JOB_BUSY_OR_UNKNOWN' in d['reasons'] for d in logs)
    assert not rc.await_admission(threading.Event(),lambda:busy,rc.PILOT_MODE,
                                  lambda n:True,lambda k,d:None)
