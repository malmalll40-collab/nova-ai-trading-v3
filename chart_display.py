"""Chart-only provisional daily bar; never used for decisions."""
from datetime import time
import math
import pandas as pd
from market_sessions import krx_session, KST, chart_probe_allowed
from daily_settlement_guard import SETTLEMENT_AFTER


def display_daily(raw, completed, now, *, schedule=None):
    now = now.astimezone(KST)
    result = completed.copy()
    meta = {'status': 'NOT_AVAILABLE', 'date': None, 'scope': 'KIS native J OHLCV; 정규장 확정봉 아님'}
    if raw.empty or now.time() >= SETTLEMENT_AFTER:
        return result, meta
    schedule = krx_session(now) if schedule is None else schedule
    if not chart_probe_allowed(now, schedule, provisional=True):
        return result, meta
    today = now.date()
    latest = pd.Timestamp(raw.index[-1])
    if latest.tz is not None:
        latest = latest.tz_convert(KST)
    if latest.date() != today or (not result.empty and pd.Timestamp(result.index[-1]).date() >= today):
        return result, meta
    try:
        o,h,l,c,v = (float(raw.iloc[-1][k]) for k in ('Open','High','Low','Close','Volume'))
    except (KeyError, TypeError, ValueError):
        return result, meta
    if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c,v)<=0 or h<max(o,c) or l>min(o,c):
        return result, meta
    result = pd.concat([result,raw.iloc[-1:].copy()])
    meta.update(status='PROVISIONAL', date=today.isoformat(), session_open=bool(schedule.get('is_open')), analysis_excluded=True, calendar_verified=bool(schedule.get('calendar_verified')), calendar_error_code=schedule.get('calendar_error_code'))
    return result, meta
