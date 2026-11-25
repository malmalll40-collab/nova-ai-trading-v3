"""Conservative W/M context from *completed daily* OHLCV, never a trading signal.

Only entirely previous calendar weeks/months are included: the most recent
calendar bucket is excluded until the calendar has moved to a new bucket.
During historical-as-of scans, the last observed daily bar defines the open
bucket, so future bars cannot leak into candidate assessment.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from engine import clean_bars
from higher_timeframes import grouped_ohlcv, bucket

KST = ZoneInfo('Asia/Seoul')


def summarize_higher_trends(daily: pd.DataFrame, symbol: str, *, now: datetime | None = None,
                            historical: bool = False) -> dict:
    """Research-only snapshot; cannot be used as approval or ranking criteria.

    Weekly: EMA(8) / EMA(20), monthly EMA(3) / EMA(6).  These are explanatory,
    unoptimized reference indicators, NOT independently backtested predictors.
    """
    now = now or datetime.now(KST)
    daily = clean_bars(daily)
    last_day = pd.Timestamp(daily.index[-1]).date() if not daily.empty else None
    reference_day = last_day if historical else now.astimezone(KST).date()
    result = {
        'research_only': True, 'affects_signal': False, 'affects_rank': False,
        'basis': '완료 일봉을 거래소 달력상 종료된 주·월 단위로 집계한 참고 추세. 현재 진행 중인 기간은 제외.',
        'last_completed_daily': last_day.isoformat() if last_day else None,
        'weekly': None, 'monthly': None,
    }
    for target, timeframe, fast, slow, required in (
        ('weekly', '1w', 8, 20, 25), ('monthly', '1mo', 3, 6, 12)
    ):
        context = {
            'state': 'INSUFFICIENT', 'label': '자료 부족', 'period_start': None,
            'samples': 0, 'close': None, 'ema_short': None, 'ema_long': None,
            'description': f'완료된 {"주" if target == "weekly" else "월"}봉이 {required}개 이상 필요합니다.'
        }
        result[target] = context
        if daily.empty:
            continue
        periods = grouped_ohlcv(daily, timeframe, symbol)
        if reference_day is not None:
            current_bucket = bucket(reference_day, timeframe)
            periods = periods.loc[periods.index.date < current_bucket].copy()
        context['samples'] = len(periods)
        if len(periods) < required:
            continue
        close = periods['Close'].astype(float)
        short = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
        long = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
        current, slow_now = float(short.iloc[-1]), float(long.iloc[-1])
        previous = float(short.iloc[-2])
        price = float(close.iloc[-1])
        if price > current > slow_now and current > previous:
            state, label = 'UP', '상승 흐름'
        elif price < current < slow_now and current < previous:
            state, label = 'DOWN', '약세 흐름'
        else:
            state, label = 'NEUTRAL', '혼조·중립'
        context.update(state=state, label=label,
            period_start=periods.index[-1].date().isoformat(),
            close=round(price, 4), ema_short=round(current, 4),
            ema_long=round(slow_now, 4),
            description=f'완료봉 {len(periods)}개 · EMA{fast}/{slow} 기준 · 상승 확률 아님')
    return result
