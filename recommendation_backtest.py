"""NOVA 4.4: historical replication of the *first-stage* EOD market screen.

This is deliberately NOT a reconstruction of final NOVA BUY decisions.  The
live final gate requires point-in-time DART and technical/holdout information
that cannot be reconstructed faithfully from today's snapshots.

For signal day T, FSC publishes T's daily snapshot only after 13:00 KST on
T+1 (next trading business day). For conservative executable simulation, enter
at the NEXT market session's opening price (T+2), with KIS adjusted OHLC.
A holding period of N sessions exits at session N close (entry date included).
No transaction execution. Incomplete data are not counted as losses or wins.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from market_scanner import MarketScanError, MAX_DEEP, screen
from public_market_data import FSCStockClient

KST = ZoneInfo('Asia/Seoul')
HORIZONS = (1, 3, 5, 10)
ALLOWED_WEEKS = (4, 8)
COST_BPS_PER_SIDE = 15


def anchor_days(now: datetime, weeks: int) -> list[str]:
    """Deliberately end 40 calendar days ago; no recent incomplete outcomes."""
    if weeks not in ALLOWED_WEEKS:
        raise ValueError('지원 기간은 과거 4주 또는 8주입니다.')
    today = now.astimezone(KST).date()
    out = []
    for n in range(weeks):
        day = today - timedelta(days=40 + 7*n)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        out.append(day.strftime('%Y%m%d'))
    return sorted(out)


def _normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('시세 없음')
    if not {'Open','Close'}.issubset(frame.columns):
        raise ValueError('시가 또는 종가 누락')
    data = frame[['Open','Close']].copy()
    dt = pd.DatetimeIndex(pd.to_datetime(data.index))
    # Convert timezone-aware timestamps to exchange-local session dates.
    if dt.tz is not None:
        dt = dt.tz_convert(KST).tz_localize(None)
    data.index = pd.Index(dt.date)
    data = data[~data.index.duplicated(keep='last')].sort_index()
    data = data.apply(pd.to_numeric, errors='coerce')
    return data


def trade_outcome(frame: pd.DataFrame, signal_day: str, cost_bps: int = COST_BPS_PER_SIDE) -> dict:
    """Evaluate future OHLC only, never use it to select the candidate."""
    if not 0 <= cost_bps <= 100:
        raise ValueError('편도 비용은 0~100bp 이내여야 합니다.')
    original_date = date.fromisoformat(signal_day[:4]+'-'+signal_day[4:6]+'-'+signal_day[6:])
    frame = _normalize_bars(frame)
    days = list(frame.index)
    first_future = bisect.bisect_right(days, original_date)
    # The first trading day after T is the publication day T+1 at ~13:00.
    # Its open precedes publication; the *second* future trading day is entry.
    entry_position = first_future + 1
    if entry_position >= len(days):
        raise ValueError('공개 이후 진입 가능일을 확인할 수 없습니다.')
    publication_day = days[first_future]
    entry_day = days[entry_position]
    if (publication_day-original_date).days > 8 or (entry_day-publication_day).days > 8:
        raise ValueError('가격 이력의 거래일 간격이 비정상적으로 깁니다.')
    entry = float(frame.iloc[entry_position]['Open'])
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError('진입 시가가 유효하지 않습니다.')
    returns = {}
    exits = {}
    for horizon in HORIZONS:
        pos = entry_position+horizon-1
        if pos >= len(days):
            returns[horizon] = None
            continue
        close = float(frame.iloc[pos]['Close'])
        if not math.isfinite(close) or close <= 0:
            returns[horizon] = None
            continue
        # Round-trip 2 * fee: as percentages, 1 bp = 0.01 percentage points.
        returns[horizon] = round((close/entry-1.0)*100 - 2*cost_bps/100.0, 5)
        exits[horizon] = str(days[pos])
    return {'entry_date':str(entry_day), 'publication_date':str(publication_day),
            'net_pct':returns, 'exit_dates':exits}


def _stats(rows: list[dict], horizon: int) -> dict:
    vals = [float(r['net_pct'][horizon]) for r in rows
            if r.get('net_pct', {}).get(horizon) is not None]
    if not vals:
        return {'samples':0, 'hit_pct':None,'mean_net_pct':None,
                'median_net_pct':None,'best_net_pct':None,'worst_net_pct':None}
    s = pd.Series(vals, dtype='float64')
    return {'samples':len(vals), 'hit_pct':round(100*(s>0).mean(),2),
            'mean_net_pct':round(float(s.mean()),3),
            'median_net_pct':round(float(s.median()),3),
            'best_net_pct':round(float(s.max()),3),
            'worst_net_pct':round(float(s.min()),3)}


def kis_history(symbol: str) -> pd.DataFrame:
    """Daily adjusted historical bars, read-only. Provider can rate-limit."""
    import os
    from data_sources import kis_data
    return kis_data(symbol,'스윙',os.environ['KIS_APP_KEY'],os.environ['KIS_APP_SECRET']).bars


def run_screen_history(*, market: str, api_key: str, weeks: int = 4,
                       cost_bps: int = COST_BPS_PER_SIDE,
                       now: datetime|None = None,
                       progress: Callable[[dict], None]|None = None,
                       client: FSCStockClient|None = None,
                       load_bars: Callable[[str], pd.DataFrame]|None = None,
                       test_dates: list[str]|None = None) -> dict:
    if market not in ('all', 'kospi', 'kosdaq'):
        raise ValueError('시장 값이 유효하지 않습니다.')
    if weeks not in ALLOWED_WEEKS or not 0 <= cost_bps <= 100:
        raise ValueError('백테스트 기간 또는 비용 값이 유효하지 않습니다.')
    client = client or FSCStockClient(api_key)
    load_bars = load_bars or kis_history
    now = now or datetime.now(tz=KST)
    selected_dates = test_dates if test_dates is not None else anchor_days(now,weeks)
    markets = ('kospi','kosdaq') if market=='all' else (market,)
    prices = {}
    signals = []
    coverage = []
    problems = Counter()
    limit=MAX_DEEP
    for idx, proposed in enumerate(selected_dates, start=1):
        if progress:
            progress({'phase':'loading','done':idx-1,'total':len(selected_dates),
                      'message':f'과거 스냅샷 {idx}/{len(selected_dates)} 조회 중'})
        # Up to 3 earlier weekdays if the proposed date was a market holiday.
        day = date.fromisoformat(proposed[:4]+'-'+proposed[4:6]+'-'+proposed[6:])
        scanned = None
        actual = None
        for trial in range(4):
            d = day-timedelta(days=trial)
            if d.weekday() >= 5:
                continue
            key = d.strftime('%Y%m%d')
            sets = [client.fetch_day(key, m) for m in markets]
            if all(sets):
                actual = key
                scanned = [r for part in sets for r in part]
                break
        if scanned is None:
            raise MarketScanError('historical_gap',f'{proposed}: 전체 시장의 과거 거래일 자료를 확보하지 못했습니다.')
        passing, excluded = screen(scanned)
        chosen = passing[:limit]
        coverage.append({'snapshot_date':actual,'market_rows':len(scanned),
                         'screen_passed':len(passing),
                         'selected':len(chosen),'excluded':excluded})
        for rank, candidate in enumerate(chosen, start=1):
            symbol = candidate['symbol']
            if progress:
                progress({'phase':'prices','done':idx,'total':len(selected_dates),
                          'message':f'{idx}/{len(selected_dates)}개 과거 기준일 · 종목 {rank}/{len(chosen)} 시세 평가 중'})
            if symbol not in prices:
                try:
                    prices[symbol] = _normalize_bars(load_bars(symbol))
                except Exception:
                    # Provider errors may embed signed URLs, never include error message.
                    prices[symbol] = None
            item = {'date':actual,'market':candidate['market'],'symbol':symbol,
                    'name':candidate['name'],'rank':rank,'score':candidate['screen_score'],
                    'entry_date':None,'net_pct':{h:None for h in HORIZONS}}
            try:
                if prices[symbol] is None:
                    raise ValueError('시세 없음')
                out=trade_outcome(prices[symbol],actual,cost_bps)
                item.update(out)
                if any(out['net_pct'][h] is None for h in HORIZONS):
                    problems['incomplete_future'] += 1
            except (ValueError,TypeError,KeyError,IndexError):
                problems['unverifiable_price'] += 1
            signals.append(item)

    if not coverage:
        raise MarketScanError('historical_empty','과거 시장 자료가 없습니다.')
    n = len(signals)
    verified = sum(1 for s in signals if s.get('net_pct',{}).get(10) is not None)
    summaries = {str(h):_stats(signals,h) for h in HORIZONS}
    by_market = {m:{str(h):_stats([s for s in signals if s['market']==m],h)
                     for h in HORIZONS} for m in markets}
    by_date = [{ 'date':d['snapshot_date'],'selected':d['selected'],
                 'session_5':_stats([s for s in signals if s['date']==d['snapshot_date']],5)}
               for d in coverage]
    state = 'complete' if verified==n and n>0 else ('incomplete' if n else 'no_candidates')
    return {
        'status':state,'scope':'first_stage_only', 'full_nova_strategy_backtested':False,
        'method':'금융위원회 전체 일별 스냅샷 → v4.3 동일 1차 유동성·등락·가격 선별 → 상위 8개 → KIS 수정주가 사후 평가',
        'time_rule':'T 종가로 선별, 공식 T+1 오후 1시 이후 공개, T+2 시가 가상 진입. h거래일 보유 후 마지막 봉 종가 가상청산.',
        'source':'금융위원회 일별 시세(T+1) + KIS 수정 OHLC',
        'market':market,'snapshot_dates':[d['snapshot_date'] for d in coverage],
        'weeks':weeks,'analyzed_weeks':len(coverage),'deep_limit':limit,
        'trades_total':n,'fully_verified':verified,
        'complete_coverage':state=='complete',
        'cost_bps_per_side':cost_bps,'cost_bps_roundtrip':2*cost_bps,
        'markets_included':list(markets),'coverage':coverage,
        'summary':summaries,'by_market':by_market,'by_date':by_date,
        'failures':dict(problems),
        'warning':'이는 현재 NOVA의 최종 안정형·재무·시점별 과거 검증을 포함한 매수추천이 아니라 1차 후보 선별 기준의 과거 성과입니다. KIS 시세 누락·상장폐지·수정주가로 인한 편향이 남을 수 있습니다. 과거 검증은 미래 적중 확률이 아닙니다.',
        'portfolio_mdd':None,'portfolio_return':None,
        'storage':'서버 메모리(재시작 시 소멸)','realtime':False,
    }
