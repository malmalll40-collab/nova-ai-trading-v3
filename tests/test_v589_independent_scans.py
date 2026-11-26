"""Cancellation and per-profile job-state safety; no live provider calls."""
from datetime import datetime
import json
import threading
from types import SimpleNamespace
import pytest
import server
import market_scanner as scanner
from nova_v584_pattern_scan import KST

def setup(monkeypatch):
    states={'full':{'status':'idle'},'pattern':{'status':'idle'}}
    monkeypatch.setattr(server,'SCAN_PROFILES',states)
    monkeypatch.setattr(server,'SCAN_CANCEL',threading.Event())
    monkeypatch.setattr(server,'SCAN_PENDING',{})
    monkeypatch.setattr(server,'_authorize',lambda r:None)
    monkeypatch.setattr(server,'_limit',lambda r:None)
    monkeypatch.setattr(server,'evidence_backend',lambda:'disabled')
    monkeypatch.setenv('FSC_STOCK_API_KEY','fixture')
    monkeypatch.setattr(server,'BACKTEST_STATE',{'status':'idle'})
    return states

def test_cancel_requires_exact_active_job(monkeypatch):
    s=setup(monkeypatch)
    s['pattern'].update(status='running',job_id='p')
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_cancel(None,{'profile':'full','job_id':'p'})
    assert e.value.status_code==409
    with pytest.raises(server.HTTPException):
        server.recommendations_cancel(None,{'profile':'pattern','job_id':'wrong'})
    assert not server.SCAN_CANCEL.is_set()
    r=server.recommendations_cancel(None,{'profile':'pattern','job_id':'p'})
    assert json.loads(r.body)['status']=='cancelling'
    assert server.SCAN_CANCEL.is_set() and s['full']['status']=='idle'

def test_cancelled_worker_does_not_publish_or_persist(monkeypatch):
    s=setup(monkeypatch)
    s['full'].update(status='running',job_id='f')
    s['pattern'].update(status='complete',result={'preserve':True})
    def fake(**kw):
        server.SCAN_CANCEL.set()
        return {'deep_target':24}
    def forbidden(*a,**k):raise AssertionError('persistence')
    monkeypatch.setattr(server,'run_market_scan',fake)
    monkeypatch.setattr(server,'store_auto_scan',forbidden)
    monkeypatch.setattr(server,'capture_market_context',forbidden)
    server._scan_worker('kospi','f','krx','fixture',24,'full')
    assert s['full']['status']=='cancelled' and s['full']['result'] is None
    assert s['pattern']['result']=={'preserve':True}

def test_finalizing_blocks_cancellation_but_accepts_independent_queue(monkeypatch):
    s=setup(monkeypatch)
    s['full'].update(status='finalizing',job_id='f')
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_cancel(None,{'profile':'full','job_id':'f'})
    assert e.value.status_code==409 and not server.SCAN_CANCEL.is_set()
    r=server.recommendations_run(None,{'profile':'pattern'})
    assert json.loads(r.body)['status']=='queued'
    assert s['full']['status']=='finalizing' and 'pattern' in server.SCAN_PENDING
    assert not server.SCAN_CANCEL.is_set()

def test_checkpoint_stops_before_next_symbol(monkeypatch):
    rows=[{'symbol':'005930.KS','name':'a','market':'kospi','selection_lane':'유동성'},{'symbol':'000660.KS','name':'b','market':'kospi','selection_lane':'유동성'}]
    class Client:
        def candidate_dates(self,now):return ['20261125']
        def fetch_day(self,date,market):return rows
    monkeypatch.setattr(scanner,'screen',lambda x:(rows,{}))
    monkeypatch.setattr(scanner,'select_deep_candidates',lambda x,d:rows)
    calls=[]
    def analyze(s,d):calls.append(s);return {}
    def check():
        if calls:raise scanner.MarketScanError('cancelled','fixture')
    with pytest.raises(scanner.MarketScanError) as e:
        scanner.run_market_scan(market='kospi',api_key='',client=Client(),depth=24,analyze=lambda x:{},analyze_asof=analyze,pattern_only=True,check_cancel=check,now=datetime(2026,11,26,12,tzinfo=KST))
    assert e.value.code=='cancelled' and calls==['005930.KS']

def test_profile_cache_independent(monkeypatch):
    s=setup(monkeypatch)
    common={'status':'complete','market':'all','source':'fsc','depth':48,'completed_monotonic':server.time.monotonic()}
    s['full'].update(common,scan_profile='full',result={'marker':'full'})
    s['pattern'].update(common,scan_profile='pattern',result={'marker':'pattern'})
    for profile in ('full','pattern'):
        r=server.recommendations_run(None,{'profile':profile})
        assert json.loads(r.body)['result']['marker']==profile
