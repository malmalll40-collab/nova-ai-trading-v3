"""Verified exchange-calendar access, with bounded-range recovery and safe diagnostics.

Never infer that a weekday is open.  A missing calendar always fails closed.
"""
from __future__ import annotations

from datetime import date, timedelta
from threading import RLock

_CALENDAR_LOCK = RLock()


class CalendarUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def get_krx_calendar(day: date):
    """Serialize provider initialization, including the bounded fallback."""
    with _CALENDAR_LOCK:
        return _load_krx_calendar(day)


def _load_krx_calendar(day: date):
    try:
        import exchange_calendars as ec
    except (ImportError, OSError):
        raise CalendarUnavailable('XKRX_IMPORT_UNAVAILABLE') from None

    try:
        cal = ec.get_calendar('XKRX')
        if cal.first_session.date() <= day <= cal.last_session.date():
            return cal
    except Exception:
        # A previous cached calendar can be outside its usable range or fail
        # initialization. Retry with an explicit, bounded date range.
        pass
    try:
        cal = ec.get_calendar('XKRX', start=f'{day.year-1}-01-01', end=f'{day.year+1}-12-31')
    except Exception:
        raise CalendarUnavailable('XKRX_LOAD_FAILED') from None
    try:
        if not (cal.first_session.date() <= day <= cal.last_session.date()):
            raise CalendarUnavailable('XKRX_DATE_OUT_OF_RANGE')
        # Query session state here to detect unusable calendars before consumers.
        cal.is_session(day.isoformat())
    except CalendarUnavailable:
        raise
    except Exception:
        raise CalendarUnavailable('XKRX_SESSION_CHECK_FAILED') from None
    return cal


def previous_completed_date(calendar, day: date, is_session: bool, after_close: bool) -> date:
    try:
        if is_session and after_close:
            return day
        # direction='previous' is inclusive on sessions; for an open day,
        # previous_session is needed, not date_to_session(..., 'previous').
        at_or_prior = calendar.date_to_session(day.isoformat(), direction='previous')
        return (calendar.previous_session(at_or_prior).date() if is_session
                else at_or_prior.date())
    except Exception:
        raise CalendarUnavailable('XKRX_PREVIOUS_SESSION_FAILED') from None

def get_krx_session_dates(start: date, end: date):
    """Use the same initialization lock for bounded historical calendar reads.

    A cached calendar may have a narrower range; never substitute weekdays.
    Retry initialization once under the lock, with identical bounds.
    """
    if type(start) is not date or type(end) is not date or not 0 <= (end-start).days <= 1200:
        raise CalendarUnavailable('XKRX_RANGE_INVALID')
    with _CALENDAR_LOCK:
        try:
            import exchange_calendars as ec
        except (ImportError,OSError):
            raise CalendarUnavailable('XKRX_IMPORT_UNAVAILABLE') from None
        for _ in range(2):
            try:
                cal=ec.get_calendar('XKRX',start=(start-timedelta(days=10)).isoformat(),
                                    end=(end+timedelta(days=10)).isoformat())
                if cal.first_session.date()>start or cal.last_session.date()<end:
                    raise ValueError('RANGE_NOT_COVERED')
                days=tuple(x.date() for x in cal.sessions_in_range(start.isoformat(),end.isoformat()))
                if not days or tuple(sorted(set(days)))!=days or any(not start<=d<=end for d in days):
                    raise ValueError('INVALID_SESSIONS')
                return days
            except Exception:
                pass
        raise CalendarUnavailable('XKRX_HISTORY_LOAD_FAILED') from None
