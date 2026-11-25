from datetime import datetime, timedelta, timezone
from prospective_collection import run, permitted

NOW = datetime(2026, 11, 25, 9, 15, tzinfo=timezone.utc)
DAY = '2026-11-25'
SYMS = ['005930.KS', '086520.KQ']

def cal(now):
    return {'calendar_verified': True, 'day': now.date().isoformat(), 'close_kst': '15:30', 'is_open': False}

def test_window():
    assert permitted(DAY, NOW, cal)
    assert not permitted(DAY, NOW-timedelta(hours=1), cal)
    assert not permitted(DAY, NOW+timedelta(days=1), cal)
    assert not permitted(DAY, NOW, lambda n: {'calendar_verified': False})

def test_resume_skips_immutable_existing():
    calls = []
    status, rows = run(DAY, SYMS, fetch=lambda s: calls.append(s) or s,
        record=lambda s, **kw: 'SAVED', existing=lambda d: {SYMS[0]: 'EXISTS'},
        now=lambda: NOW, calendar=cal, progress=lambda r: None)
    assert calls == [SYMS[1]] and status == 'complete'
    assert rows == {SYMS[0]: 'EXISTS', SYMS[1]: 'SAVED'}

def test_partial_does_not_promote_price_only():
    status, rows = run(DAY, SYMS, fetch=lambda s: s,
        record=lambda s, **kw: 'UNVERIFIED', existing=lambda d: {SYMS[0]: 'PRICE_ONLY'},
        now=lambda: NOW, calendar=cal, progress=lambda r: None)
    assert status == 'partial' and rows == {SYMS[0]: 'PRICE_ONLY', SYMS[1]: 'UNVERIFIED'}

def test_midnight_fetch_not_recorded():
    times = iter([NOW, NOW, NOW + timedelta(days=1)])
    calls = []
    status, rows = run(DAY, SYMS, fetch=lambda s: s,
        record=lambda s, **kw: calls.append(s), existing=lambda d: {},
        now=lambda: next(times), calendar=cal, progress=lambda r: None)
    assert status == 'partial' and rows == {} and calls == []

def test_exception_remains_partial():
    def broken(s):
        raise RuntimeError('private token')
    status, rows = run(DAY, SYMS, fetch=broken, record=lambda s, **kw: 'SAVED',
        existing=lambda d: {}, now=lambda: NOW, calendar=cal, progress=lambda r: None)
    assert status == 'partial' and rows == dict.fromkeys(SYMS, 'ERROR')
