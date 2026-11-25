"""Verified exchange-calendar access, with bounded-range recovery and safe diagnostics.

Never infer that a weekday is open.  A missing calendar always fails closed.
"""
from __future__ import annotations

from datetime import date


class CalendarUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def get_krx_calendar(day: date):
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
