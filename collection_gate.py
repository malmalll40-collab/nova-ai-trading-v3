"""Fail-closed, safe diagnostics for the prospective collector."""
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
ERROR_CODES = frozenset(('XKRX_IMPORT_UNAVAILABLE', 'XKRX_LOAD_FAILED',
                         'XKRX_DATE_OUT_OF_RANGE', 'XKRX_SESSION_CHECK_FAILED',
                         'XKRX_PREVIOUS_SESSION_FAILED'))


def inspect_gate(day, now, calendar):
    local = now.astimezone(KST)
    info = {'day': local.date().isoformat(), 'hour_kst': local.hour,
            'reason': 'DATE_MISMATCH', 'calendar_error_code': None}
    if info['day'] != day:
        return info
    if local.hour < 18:
        return {**info, 'reason': 'BEFORE_WINDOW'}
    try:
        s = calendar(local)
        if s.get('calendar_verified') is not True:
            code = s.get('calendar_error_code')
            return {**info, 'reason': 'CALENDAR_UNVERIFIED',
                    'calendar_error_code': code if code in ERROR_CODES else None}
        if s.get('day') != day:
            return {**info, 'reason': 'CALENDAR_DATE_MISMATCH'}
        if s.get('is_open') is not False:
            return {**info, 'reason': 'MARKET_NOT_CLOSED'}
        if not s.get('close_kst'):
            reason = 'HOLIDAY' if s.get('status') == 'CLOSED' and not s.get('open_kst') else 'SESSION_UNVERIFIED'
            return {**info, 'reason': reason}
        return {**info, 'reason': 'PERMITTED'}
    except Exception:
        return {**info, 'reason': 'CALENDAR_UNVERIFIED'}
