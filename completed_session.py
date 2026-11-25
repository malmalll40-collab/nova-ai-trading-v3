"""Strict, read-only KRX completed-session freshness for *live swing* analysis.

This is deliberately separate from historic FSC/KRX as-of scans; no synthetic
market sessions are created.  Fail closed when exchange calendar is unavailable.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from market_sessions import SPECIAL_HOURS
from krx_calendar_verified import get_krx_calendar, CalendarUnavailable, previous_completed_date

KST = ZoneInfo('Asia/Seoul')


def completed_swing_bars(bars: pd.DataFrame, *, now: datetime | None = None) -> tuple[pd.DataFrame, dict]:
    moment = now or datetime.now(KST)
    if moment.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    moment = moment.astimezone(KST)
    result = {'state': 'CALENDAR_UNKNOWN', 'stale': True, 'expected_completed_session': None,
              'latest_completed_session': None, 'session_gap': None, 'excluded_unfinished': 0,
              'reason': 'KRX 거래일 달력을 확인할 수 없어 신호를 보류합니다.',
              'calendar_error_code': None, 'calendar_verified': False}
    if bars is None or bars.empty:
        return bars, {**result, 'state': 'NO_COMPLETED_BARS', 'reason': '완료 일봉이 없습니다.'}
    try:
        today = moment.date()
        cal = get_krx_calendar(today)
        is_session = bool(cal.is_session(today.isoformat()))
        _, close, _ = SPECIAL_HOURS.get(today.isoformat(), ('09:00', '15:30', '정규 거래일'))
        cutoff_minutes = int(close[:2]) * 60 + int(close[3:])
        expected = previous_completed_date(cal, today, is_session,
                                           moment.hour*60+moment.minute>=cutoff_minutes)
        result['calendar_verified'] = True
        # The exchange-local date of a daily bar is its label, not an intraday tick timestamp.
        labels = [pd.Timestamp(x).date() for x in bars.index]
        keep = [d <= expected for d in labels]
        filtered = bars.iloc[[i for i, allowed in enumerate(keep) if allowed]].copy()
        skipped = len(bars) - len(filtered)
        result.update(expected_completed_session=expected.isoformat(), excluded_unfinished=skipped)
        if filtered.empty:
            result.update(state='NO_COMPLETED_BARS', reason='확정된 정규장 일봉이 없습니다.')
            return filtered, result
        latest = max(pd.Timestamp(x).date() for x in filtered.index)
        result['latest_completed_session'] = latest.isoformat()
        if not bool(cal.is_session(latest.isoformat())):
            result.update(state='NON_SESSION_BAR', reason='마지막 완료봉 날짜가 KRX 거래일이 아닙니다.')
            return filtered, result
        remaining = cal.sessions_in_range(latest.isoformat(), expected.isoformat())
        gap = max(0, len(remaining) - 1)
        result['session_gap'] = gap
        if any(d > moment.date() for d in labels):
            result.update(state='FUTURE_BAR', reason='미래 날짜의 봉이 포함되어 현재 신호를 보류합니다.')
        elif gap:
            result.update(state='BEHIND_SESSION', reason=f'KRX 마지막 완료 거래일보다 {gap}거래일 이전 데이터입니다.')
        else:
            result.update(state='CURRENT_COMPLETED_SESSION', stale=False,
                          reason='KRX 최신 완료 거래일과 분석 완료봉 날짜가 일치합니다.')
        return filtered, result
    except Exception as exc:
        result['calendar_error_code'] = exc.code if isinstance(exc, CalendarUnavailable) else 'XKRX_SESSION_CHECK_FAILED'
        result['reason'] += ' (진단코드: ' + result['calendar_error_code'] + ')'
        # Unknown session calendar is not permission to use an open/current-day
        # candle. Exclude the current date conservatively, even after close,
        # and block all actionable signals until calendar verification returns.
        try:
            valid = [pd.Timestamp(x).date() < moment.date() for x in bars.index]
            safe = bars.iloc[[i for i, allowed in enumerate(valid) if allowed]].copy()
            result['excluded_unfinished'] = len(bars)-len(safe)
            return safe, result
        except Exception:
            return bars.iloc[:0].copy(), result
