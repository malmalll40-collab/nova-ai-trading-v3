from datetime import datetime, timezone, timedelta
import pytest
from collection_gate import inspect_gate
from market_sessions import krx_session

NOW = datetime(2026, 11, 25, 9, 48, tzinfo=timezone.utc)
DAY = '2026-11-25'
GOOD = {'calendar_verified': True, 'day': DAY, 'close_kst': '15:30', 'open_kst': '09:00', 'is_open': False, 'status': 'CLOSED'}

@pytest.mark.parametrize('changes,reason', [({}, 'PERMITTED'), ({'calendar_verified': False}, 'CALENDAR_UNVERIFIED'), ({'day': '2026-11-24'}, 'CALENDAR_DATE_MISMATCH'), ({'is_open': True}, 'MARKET_NOT_CLOSED'), ({'close_kst': None, 'open_kst': None}, 'HOLIDAY'), ({'close_kst': None}, 'SESSION_UNVERIFIED')])
def test_gate(changes, reason):
    assert inspect_gate(DAY, NOW, lambda _: {**GOOD, **changes})['reason'] == reason

def test_time_and_errors():
    assert inspect_gate(DAY, NOW-timedelta(hours=1), lambda _: GOOD)['reason'] == 'BEFORE_WINDOW'
    assert inspect_gate(DAY, NOW+timedelta(days=1), lambda _: GOOD)['reason'] == 'DATE_MISMATCH'
    assert inspect_gate(DAY, NOW, lambda _: {'calendar_verified': False, 'calendar_error_code': 'secret'})['calendar_error_code'] is None
    assert inspect_gate(DAY, NOW, lambda _: {'calendar_verified': False, 'calendar_error_code': 'XKRX_LOAD_FAILED'})['calendar_error_code'] == 'XKRX_LOAD_FAILED'
    def broken(_): raise RuntimeError('secret')
    assert inspect_gate(DAY, NOW, broken)['reason'] == 'CALENDAR_UNVERIFIED'

def test_real_local_calendar():
    assert inspect_gate(DAY, NOW, krx_session)['reason'] == 'PERMITTED'
