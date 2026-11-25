from datetime import datetime, timezone, timedelta
import pytest
from collection_audit import integrity_detail, retry_integrity_once
import daily_settlement_guard as guard

NOW=datetime(2026,11,25,10,tzinfo=timezone.utc)
DAY='2026-11-25'
SYM='086520.KQ'

def payload(errors=None):
    return {'symbol':SYM,'source':'kis','mode':'스윙','daily_integrity':{'state':'UNVERIFIED','signal_hold':True,'latest_date':DAY,'errors':errors if errors is not None else ['KIS_REGULAR_COVERAGE_INCOMPLETE']}}

def calendar(n):
    return {'day':n.date().isoformat(),'calendar_verified':True,'close_kst':'15:30','is_open':False}

def test_diagnostic_allowlist():
    p=payload(['KIS_REGULAR_QUERY_FAILED','secret',{'private':'x'}]);p['daily_integrity']['state']='private'
    assert integrity_detail(p)=={'state':'UNKNOWN','errors':['KIS_REGULAR_QUERY_FAILED'],'signal_hold':True}

@pytest.mark.parametrize('error',['KIS_REGULAR_QUERY_FAILED','KIS_REGULAR_COVERAGE_INCOMPLETE'])
def test_only_one_retry(error):
    calls=[]; original=payload([error]); new=payload()
    got,status=retry_integrity_once(SYM,DAY,original,fetch=lambda s:calls.append(('fetch',s)) or new,now=lambda:NOW,calendar=calendar,clear=lambda c,d:calls.append(('clear',c,d.isoformat())),wait=lambda:None)
    assert got is new and status=='REQUERY_DONE' and calls==[('clear','086520',DAY),('fetch',SYM)]

@pytest.mark.parametrize('error',['KIS_REGULAR_VOLUME_UNVERIFIED','KIS_SECONDARY_QUERY_FAILED'])
def test_no_other_gate_retry(error):
    original=payload([error]); fail=lambda *a:pytest.fail('unexpected retry')
    assert retry_integrity_once(SYM,DAY,original,fetch=fail,now=lambda:NOW,calendar=calendar,clear=fail,wait=fail)==(original,'NOT_NEEDED')

def test_midnight_and_exception_preserve_original():
    original=payload(); fail=lambda *a:pytest.fail('unexpected fetch')
    times=iter([NOW,NOW+timedelta(days=1)])
    assert retry_integrity_once(SYM,DAY,original,fetch=fail,now=lambda:next(times),calendar=calendar,clear=fail,wait=lambda:None)==(original,'WINDOW_BLOCKED')
    def broken(s):raise RuntimeError('private')
    assert retry_integrity_once(SYM,DAY,original,fetch=broken,now=lambda:NOW,calendar=calendar,clear=lambda *a:None,wait=lambda:None)==(original,'REQUERY_FAILED')

def test_negative_cache_only():
    key=('086520',DAY); other=('263750',DAY); d=NOW.date()
    with guard._REGULAR_LOCK:
        guard._REGULAR_CACHE[key]=(0,None);guard._REGULAR_CACHE[other]=(0,{'Open':1})
    try:
        assert guard.discard_failed_regular_reference('086520',d)
        assert key not in guard._REGULAR_CACHE
        assert not guard.discard_failed_regular_reference('263750',d)
        assert guard._REGULAR_CACHE[other][1]=={'Open':1}
    finally:
        guard._REGULAR_CACHE.pop(key,None);guard._REGULAR_CACHE.pop(other,None)
