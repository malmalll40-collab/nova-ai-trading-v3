from datetime import datetime, timezone, timedelta
from ops_preflight import snapshot, JOBS, COUNTERS

NOW = datetime(2026,11,26,20,16,tzinfo=timezone(timedelta(hours=9)))
SCHEDULE = {'calendar_verified': True, 'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'}
LOAD = {**dict.fromkeys(COUNTERS,0),'scope':'PROCESS_LOCAL','window_overflow':False}
J = dict.fromkeys(JOBS,'idle')

def test_local_clear_is_not_authorization():
    r = snapshot(NOW,SCHEDULE,LOAD,J)
    assert r['local_preflight']=='LOCAL_CLEAR'
    assert not r['execution_authorized'] and not r['account_quota_verified']
    assert not r['orders_enabled'] and not r['shared_admission_verified']

def test_market_window_blocks():
    assert snapshot(NOW.replace(hour=19),SCHEDULE,LOAD,J)['local_preflight']=='BLOCKED'

def test_unverified_calendar_blocks():
    assert snapshot(NOW,dict(SCHEDULE,calendar_verified=False),LOAD,J)['local_preflight']=='BLOCKED'

def test_busy_and_missing_jobs_block():
    assert snapshot(NOW,SCHEDULE,LOAD,dict(J,scan_pattern='running'))['local_preflight']=='BLOCKED'
    assert snapshot(NOW,SCHEDULE,LOAD,{})['local_preflight']=='BLOCKED'

def test_recent_requests_or_errors_block():
    for k in COUNTERS:
        assert snapshot(NOW,SCHEDULE,dict(LOAD,**{k:1}),J)['local_preflight']=='BLOCKED'

def test_missing_or_invalid_telemetry_blocks():
    for load in ({},dict(LOAD,foreground_active=False),dict(LOAD,window_overflow=True),dict(LOAD,scope='UNKNOWN')):
        assert snapshot(NOW,SCHEDULE,load,J)['local_preflight']=='BLOCKED'
