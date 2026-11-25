"""Read-only regular KRX session indicator for polling decisions.

exchange_calendars publishes the session list, but certain exceptional exchange
announcements can arrive after the packaged calendar (e.g. exam-day hours).
An explicit known 2026 exception is applied. This does NOT cover NXT trading
hours, pre-market, after-market, halts, or API entitlements.
"""
from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo
from krx_calendar_verified import get_krx_calendar, CalendarUnavailable

KST = ZoneInfo('Asia/Seoul')
# KRX publicly announced the 2026-11-19 regular session as 10:00–16:30.
SPECIAL_HOURS = {'2026-11-19': ('10:00', '16:30', '2027학년도 대학수학능력시험일 정규장 특별 운영')}


def krx_session(now: datetime | None = None) -> dict:
    dt = now or datetime.now(KST)
    if dt.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    dt = dt.astimezone(KST)
    day = dt.date().isoformat()
    result = {
        'as_of': dt.isoformat(timespec='seconds'), 'exchange': 'KRX',
        'market_scope': '코스피·코스닥 정규장', 'day': day,
        'status': 'UNKNOWN', 'is_open': False,
        'open_kst': None, 'close_kst': None, 'schedule_source': 'unavailable',
        'reason': '거래일 달력을 확인할 수 없어 자동 갱신을 보류합니다.',
        'calendar_error_code': None, 'calendar_verified': False,
    }
    try:
        calendar = get_krx_calendar(dt.date())
        is_session = bool(calendar.is_session(day))
    except CalendarUnavailable as exc:
        result['calendar_error_code'] = exc.code
        result['reason'] += ' (진단코드: ' + exc.code + ')'
        return result
    except Exception:
        result['calendar_error_code'] = 'XKRX_SESSION_CHECK_FAILED'
        result['reason'] += ' (진단코드: XKRX_SESSION_CHECK_FAILED)'
        return result
    result['calendar_verified'] = True
    result['schedule_source'] = 'exchange_calendars XKRX + 확인된 특별 일정'
    if not is_session:
        result.update(status='CLOSED', reason='KRX 정규장 휴장일입니다.')
        return result
    opening, closing, note = SPECIAL_HOURS.get(day, ('09:00','15:30','정규 거래일'))
    minutes = dt.hour * 60 + dt.minute
    start = int(opening[:2]) * 60 + int(opening[3:])
    end = int(closing[:2]) * 60 + int(closing[3:])
    is_open = start <= minutes < end
    result.update(status='OPEN' if is_open else 'CLOSED', is_open=is_open,
                  open_kst=opening, close_kst=closing, reason=note if is_open else f'{opening}~{closing} 정규장 시간 밖입니다. {note}')
    return result


def chart_probe_allowed(now: datetime, schedule: dict, *, provisional: bool = False) -> bool:
    """Permit read-only KIS probe, not a verified session or trading permission.

    Unknown calendar: bounded expected hours only; caller must independently
    require KIS same-date positive-volume data. Known holidays never probe.
    """
    dt = now.astimezone(KST)
    if schedule.get('calendar_verified'):
        if not schedule.get('open_kst') or not schedule.get('close_kst'):
            return False
        return (time.fromisoformat(schedule['open_kst']) <= dt.time() < time(18)) if provisional else bool(schedule.get('is_open'))
    if dt.weekday() >= 5:
        return False
    opening, closing, _ = SPECIAL_HOURS.get(dt.date().isoformat(), ('09:00','15:30','예상 시간'))
    return time.fromisoformat(opening) <= dt.time() < (time(18) if provisional else time.fromisoformat(closing))
