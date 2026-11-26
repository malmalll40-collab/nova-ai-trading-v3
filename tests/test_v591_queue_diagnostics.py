"""Offline queue handoff, cancel isolation and strict zero diagnostics."""
import json
import threading
import pytest
import server
from market_scanner import assess_scan_outcome
from tests.test_v589_independent_scans import setup

def test_queue_handoff_and_duplicate(monkeypatch):
    s=setup(monkeypatch)
    starts=[]
    class FakeThread:
        def __init__(self,*,target,args,**kw):self.args=args
        def start(self):starts.append(self.args)
    monkeypatch.setattr(server.threading,'Thread',FakeThread)
    full=json.loads(server.recommendations_run(None,{'profile':'full','depth':60}).body)
    pattern=json.loads(server.recommendations_run(None,{'profile':'pattern','depth':24}).body)
    assert full['status']=='running' and pattern['status']=='queued'
    assert len(starts)==1 and starts[0][-1]=='full'
    assert json.loads(server.recommendations_run(None,{'profile':'pattern'}).body)['job_id']==pattern['job_id']
    monkeypatch.setattr(server,'run_market_scan',lambda **kw:{'deep_target':60,'deep_failed':0})
    server._scan_worker(*starts[0])
    assert s['full']['status']=='complete' and s['pattern']['status']=='running'
    assert len(starts)==2 and starts[1][-1]=='pattern' and starts[1][4]==24
    assert not server.SCAN_CANCEL.is_set()

def test_queued_cancel_does_not_cancel_other_worker(monkeypatch):
    s=setup(monkeypatch)
    s['full'].update(status='running',job_id='f')
    p=json.loads(server.recommendations_run(None,{'profile':'pattern'}).body)
    assert p['status']=='queued'
    r=json.loads(server.recommendations_cancel(None,{'profile':'pattern','job_id':p['job_id']}).body)
    assert r['status']=='cancelled' and s['full']['status']=='running'
    assert not server.SCAN_CANCEL.is_set() and not server.SCAN_PENDING

def test_running_cancel_hands_off_without_poisoning_next(monkeypatch):
    s=setup(monkeypatch)
    s['pattern'].update(status='running',job_id='p')
    f=json.loads(server.recommendations_run(None,{'profile':'full'}).body)
    assert f['status']=='queued'
    starts=[]
    class FakeThread:
        def __init__(self,*,target,args,**kw):self.args=args
        def start(self):starts.append(self.args)
    monkeypatch.setattr(server.threading,'Thread',FakeThread)
    server.recommendations_cancel(None,{'profile':'pattern','job_id':'p'})
    monkeypatch.setattr(server,'run_market_scan',lambda **kw:{'deep_target':24,'deep_failed':0})
    server._scan_worker('all','p','fsc','fixture',24,'pattern')
    assert s['pattern']['status']=='cancelled' and s['full']['status']=='running'
    assert len(starts)==1 and starts[0][-1]=='full' and not server.SCAN_CANCEL.is_set()

def fixture(**kwargs):
    return dict({'scan_profile':'full','deep_target':60,'deep_succeeded':60,'deep_failed':0,
        'recommended_count':0,'interest_count':0,'rejection_codes':{'TECHNICAL_NOT_BUY':60},
        'snapshot_date_status':'ON_SCHEDULE'},**kwargs)

@pytest.mark.parametrize('data,status',[
    (fixture(),'NO_STRICT_CANDIDATES'),
    (fixture(rejection_codes={'PRICE_STALE':60}),'DATA_LIMITED'),
    (fixture(deep_succeeded=59,deep_failed=1,rejection_codes={'DEEP_ANALYSIS_ERROR':1,'TECHNICAL_NOT_BUY':59}),'DATA_LIMITED'),
    (fixture(rejection_codes={'TECHNICAL_NOT_BUY':59}),'COUNT_MISMATCH'),
    (fixture(deep_target=0,deep_succeeded=0,rejection_codes={}),'NO_SCREENED_CANDIDATES'),
    (fixture(recommended_count=1,rejection_codes={'TECHNICAL_NOT_BUY':59}),'STRICT_CANDIDATES'),
])
def test_scan_outcome(data,status):
    assert assess_scan_outcome(data)['status']==status
