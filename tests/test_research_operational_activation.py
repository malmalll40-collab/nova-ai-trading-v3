from datetime import datetime,timezone
from types import SimpleNamespace
import threading
import pytest
from nova_v578_kis_telemetry import LocalTelemetry
from nova_v579_admission import ResearchBlocked
from research_kis_transport_gate import SharedRequestGate
from research_operational_activation import eligible,worker,summarize

class Ledger:
    def __init__(self):self.claims=[];self.finished=[]
    def claim(self,kind,limit):
        self.claims.append((kind,limit));return len(self.claims)
    def mark_research_dispatched(self,lease):return True
    def finish(self,lease):self.finished.append(lease);return True
    def status(self,limit):return {'unfinished_foreground':0,'unfinished_research':0,'completion_buffered':0,'policy_cap':limit}

def state():
    from ops_preflight import JOBS
    return {'quiet_window':True,'calendar_verified':True,'jobs':dict.fromkeys(JOBS,'idle'),
            'window_overflow':False,'kis_load':dict.fromkeys(('foreground_active','physical_active','research_active','recent_provider_errors'),0)}

def test_operational_permit_is_gate_bound_and_not_provider_evidence():
    ledger=Ledger();gate=SharedRequestGate(ledger,3,operational_policy=True)
    telemetry=LocalTelemetry(shared_gate=gate)
    with telemetry.research_attempt(gate.reserve()):pass
    assert ledger.claims==[('research',3)] and ledger.finished==[1]
    assert not telemetry.snapshot()['verified']
    other=SharedRequestGate(Ledger(),3,operational_policy=True)
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_UNVERIFIED'):
        with telemetry.research_attempt(other.reserve()):pytest.fail('must block')

def test_operational_permit_stale_and_foreground_priority():
    t=[0.];gate=SharedRequestGate(Ledger(),3,operational_policy=True,clock=lambda:t[0])
    telemetry=LocalTelemetry(shared_gate=gate);permit=gate.reserve();t[0]=6
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_UNVERIFIED'):
        with telemetry.research_attempt(permit):pass
    with telemetry.foreground():
        with pytest.raises(ResearchBlocked,match='FOREGROUND_BUSY'):
            with telemetry.research_attempt(gate.reserve()):pass

def test_shared_install_refuses_after_dispatch():
    telemetry=LocalTelemetry()
    with telemetry.physical():pass
    with pytest.raises(RuntimeError,match='SHARED_GATE_INSTALL_UNSAFE'):
        telemetry.install_shared_gate(SharedRequestGate(Ledger(),3))

def test_guard_ignores_prior_research_but_not_foreground():
    s=state();s['kis_load']['physical_attempts_60s']=12
    assert eligible(s)
    s['kis_load']['foreground_active']=1
    assert not eligible(s)

def test_worker_requires_drain_and_session():
    calls=[]
    worker(threading.Event(),lambda:{},None,None,'fake',wait=lambda n:False,
           client_factory=lambda:pytest.fail('must not connect'),emit=lambda e,v:calls.append(v))
    assert calls[0]['reason']=='DRAIN_OR_WINDOW_BUSY'
    calls.clear()
    worker(threading.Event(),state,None,None,'fake',wait=lambda n:False,
           session_probe=lambda d:{},client_factory=lambda:pytest.fail('must not connect'),emit=lambda e,v:calls.append(v))
    assert calls[0]['reason']=='DIRECT_SESSION_UNAVAILABLE'

def test_worker_resumes_and_stops_on_no_progress():
    calls=[];results=iter([{'status':'BLOCKED','coverage':{'stored_rows':100},'collection':{'new_rows':100}},
                            {'status':'BLOCKED','coverage':{'stored_rows':100},'collection':{'new_rows':0}}])
    def runner(*args,**kwargs):
        assert kwargs['allow_collection'] and kwargs['session_mode_confirmed']
        assert kwargs['coordinator'].available()
        return next(results)
    worker(threading.Event(),state,None,SharedRequestGate(Ledger(),3,operational_policy=True),'fake',
           wait=lambda n:False,session_probe=lambda d:{'direct_connection_status':'READ_OK','session_probe_verified':True},
           client_factory=lambda:SimpleNamespace(session=SimpleNamespace(close=lambda:None)),runner=runner,
           emit=lambda e,v:calls.append(v) if e=='research_collection_pass' else None)
    assert [v['new_rows'] for v in calls]==[100,0]
    assert all(v['strategy_adoption']=='HOLD' and not v['orders_enabled'] for v in calls)

def test_foreground_wait_bounded_before_claim():
    t=[0.];db=Ledger()
    original=db.claim
    def claim(kind,limit):
        if t[0]<.5:return None
        return original(kind,limit)
    db.claim=claim
    gate=SharedRequestGate(db,3,operational_policy=True,clock=lambda:t[0],pause=lambda n:t.__setitem__(0,t[0]+n))
    with gate.slot('foreground') as start:start()
    assert t[0]==.5 and db.finished==[1]

def test_worker_stops_before_token_when_uncertain_shared_lease():
    calls=[]
    db=Ledger()
    db.status=lambda limit:{'unfinished_foreground':0,'unfinished_research':1,'completion_buffered':0,'policy_cap':limit}
    worker(threading.Event(),state,None,SharedRequestGate(db,3,operational_policy=True),'fake',
           wait=lambda n:False,session_probe=lambda d:{'direct_connection_status':'READ_OK','session_probe_verified':True},
           client_factory=lambda:pytest.fail('must not connect'),
           emit=lambda e,v:calls.append((e,v)))
    assert calls[-1][1]['reason']=='UNFINISHED_SHARED_RESERVATION'
    assert not db.claims and not db.finished

def test_worker_does_not_repeat_provider_failure_even_with_progress():
    calls=[]
    def runner(*a,**kw):
        calls.append('run')
        return {'status':'BLOCKED','coverage':{'stored_rows':150},
                'collection':{'new_rows':100,'reason':'RESEARCH_HTTP_FAILURE_429'}}
    worker(threading.Event(),state,None,SharedRequestGate(Ledger(),3,operational_policy=True),'fake',
           wait=lambda n:False,session_probe=lambda d:{'direct_connection_status':'READ_OK','session_probe_verified':True},
           client_factory=lambda:SimpleNamespace(session=SimpleNamespace(close=lambda:None)),runner=runner,
           emit=lambda e,v:calls.append(e))
    assert calls.count('run')==1 and calls[-1]=='research_collection_stop'

def test_legacy_unknown_reservation_is_never_recovered():
    calls=[]
    db=Ledger()
    db.status=lambda limit:{'unfinished_foreground':0,'unfinished_research':1,'legacy_unknown_research':1,
        'undispatched_research':0,'dispatched_unconfirmed_research':0,'completion_buffered':0,'policy_cap':limit}
    db.recover_undispatched_research=lambda limit:pytest.fail('legacy must not be recovered')
    worker(threading.Event(),state,None,SharedRequestGate(db,3,operational_policy=True),'fake',
           wait=lambda n:False,session_probe=lambda d:{'direct_connection_status':'READ_OK','session_probe_verified':True},
           client_factory=lambda:pytest.fail('must not connect'),
           emit=lambda e,v:calls.append((e,v)))
    assert calls[-1][1]['reason']=='UNFINISHED_SHARED_RESERVATION'

def test_worker_rechecks_recovered_reservation_before_token():
    calls=[]
    db=Ledger()
    statuses=iter([{'unfinished_foreground':0,'unfinished_research':1,'undispatched_research':1},
                   {'unfinished_foreground':0,'unfinished_research':0,'undispatched_research':0}])
    db.status=lambda limit:next(statuses)
    db.recover_undispatched_research=lambda limit:1
    worker(threading.Event(),state,None,SharedRequestGate(db,3,operational_policy=True),'fake',
           wait=lambda n:False,session_probe=lambda d:{'direct_connection_status':'READ_OK','session_probe_verified':True},
           client_factory=lambda:SimpleNamespace(session=SimpleNamespace(close=lambda:None)),
           runner=lambda *a,**kw:{'status':'BLOCKED','coverage':{'stored_rows':150},'collection':{'new_rows':0}},
           emit=lambda e,v:calls.append((e,v)))
    assert [e for e,v in calls]==['research_quota_probe','research_undispatched_recovery','research_quota_probe','research_collection_pass']
