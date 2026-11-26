from datetime import date,datetime
from types import SimpleNamespace
import threading
import sys
import pytest
import krx_calendar_verified as m
from research_history_status import probe

START=date(2023,11,26)
END=date(2026,11,25)

def calendar():
    return SimpleNamespace(first_session=datetime(2023,11,20),last_session=datetime(2026,12,1),
        sessions_in_range=lambda a,b:[datetime(2023,11,27),datetime(2026,11,25)])

def test_bounded_retry_preserves_range(monkeypatch):
    calls=[]
    def get(*args,**kw):
        calls.append((args,kw))
        if len(calls)==1:raise RuntimeError('transient')
        return calendar()
    monkeypatch.setitem(sys.modules,'exchange_calendars',SimpleNamespace(get_calendar=get))
    assert m.get_krx_session_dates(START,END)==(date(2023,11,27),END)
    assert len(calls)==2 and calls[0]==calls[1]
    assert calls[0][1]=={'start':'2023-11-16','end':'2026-12-05'}

def test_history_waits_for_shared_lock(monkeypatch):
    entered=threading.Event()
    attempted=threading.Event()
    result=[]
    def get(*args,**kw):
        entered.set()
        return calendar()
    monkeypatch.setitem(sys.modules,'exchange_calendars',SimpleNamespace(get_calendar=get))
    def worker():
        attempted.set()
        result.append(m.get_krx_session_dates(START,END))
    with m._CALENDAR_LOCK:
        t=threading.Thread(target=worker,daemon=True)
        t.start()
        assert attempted.wait(1)
        assert not entered.wait(.05)
    t.join(2)
    assert not t.is_alive() and result==[(date(2023,11,27),END)]

def test_calendar_failure_stays_closed_and_sanitized(monkeypatch):
    def fail(*args,**kw):raise RuntimeError('private provider details')
    monkeypatch.setitem(sys.modules,'exchange_calendars',SimpleNamespace(get_calendar=fail))
    r=probe('postgresql://fake',reader=lambda *a:pytest.fail('DB must not be touched'))
    assert r['reason']=='CALENDAR_UNAVAILABLE'
    assert r['calendar_error_code']=='XKRX_HISTORY_LOAD_FAILED'
    assert 'private' not in str(r) and not r['execution_authorized']

def test_invalid_range_rejected():
    with pytest.raises(m.CalendarUnavailable,match='XKRX_RANGE_INVALID'):
        m.get_krx_session_dates(END,START)
