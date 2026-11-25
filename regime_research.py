"""NOVA v4.7: strictly prospective, READ-ONLY market-regime research.

The benchmark regime is calculated from a separately OBSERVED, completed index
history, strictly earlier than the observation's KST calendar day. Never infer
market context for older v4.6 records, never use a future candle for a signal,
and never feed experimental observations into actionable signals/orders.

This module uses only derived index statistics, not redistributable index OHLC.
Thresholds are fixed research heuristics, NOT an AI model, trading system,
calibrated probabilities, or proven forward-looking filters.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite, sqrt
from zoneinfo import ZoneInfo
import pandas as pd

KST = ZoneInfo('Asia/Seoul')
BENCHMARKS = {'^KS11':'KOSPI','^KQ11':'KOSDAQ'}
HORIZONS = (1,3,5,10)
VERSION = '4.7-research-heuristic-v1'
MIN_CLUSTERS = 12
MIN_OBSERVATIONS = 30


def unknown_context(benchmark: str, reason: str = 'not_available') -> dict:
    return {'status':'UNAVAILABLE','benchmark':benchmark,'name':BENCHMARKS[benchmark],
            'reason':reason,'rule_version':VERSION}


def _index_day(x):
    stamp = pd.Timestamp(x)
    if pd.isna(stamp):raise ValueError('invalid date')
    if stamp.tzinfo is not None:stamp=stamp.tz_convert(KST)
    return stamp.date()


def classify_benchmark(frame: pd.DataFrame, observed_at: datetime, benchmark: str) -> dict:
    """Compute a frozen observable regime using *prior-day-only* completed bars.

    All closes MUST have actually been fetched before persisting a new observation.
    The provider's historical release timestamps are not retroactively inferred.
    """
    if benchmark not in BENCHMARKS:raise ValueError('unknown benchmark')
    if observed_at.tzinfo is None:raise ValueError('timezone aware observation required')
    obs=observed_at.astimezone(KST)
    if not isinstance(frame,pd.DataFrame) or 'Close' not in frame:
        return unknown_context(benchmark,'history_missing')
    rows={}
    try:
        for idx,value in frame['Close'].items():
            day=_index_day(idx)
            if day>=obs.date():continue  # Never use the current day's partial or closing bar.
            price=float(value)
            if not isfinite(price) or price<=0:
                return unknown_context(benchmark,'invalid_close')
            if day in rows:return unknown_context(benchmark,'duplicate_dates')
            rows[day]=price
    except (TypeError,ValueError,OverflowError):
        return unknown_context(benchmark,'invalid_history')
    ordered=sorted(rows.items())
    if len(ordered)<61:return unknown_context(benchmark,'fewer_than_61_prior_bars')
    if (obs.date()-ordered[-1][0]).days>10:
        return unknown_context(benchmark,'stale_index')
    closes=[x[1] for x in ordered]
    try:
        ma20=sum(closes[-20:])/20
        ma60=sum(closes[-60:])/60
        mom20=(closes[-1]/closes[-21]-1)*100
        spread=(ma20/ma60-1)*100
        returns=[closes[i]/closes[i-1]-1 for i in range(len(closes)-20,len(closes))]
        avg=sum(returns)/len(returns)
        vol20=sqrt(sum((r-avg)**2 for r in returns)/(len(returns)-1))*100
        if not all(isfinite(v) for v in (ma20,ma60,mom20,spread,vol20)):
            raise ValueError('non-finite metrics')
    except (ValueError,ZeroDivisionError,OverflowError):
        return unknown_context(benchmark,'invalid_metrics')
    trend='UP' if spread>=1 and mom20>=2 else 'DOWN' if spread<=-1 and mom20<=-2 else 'SIDEWAYS'
    volatility='HIGH' if vol20>=1.8 else 'NORMAL'
    return {'status':'READY','benchmark':benchmark,'name':BENCHMARKS[benchmark],
            'asof':ordered[-1][0].isoformat(), 'observed_at':obs.isoformat(timespec='seconds'),
            'lag_calendar_days':(obs.date()-ordered[-1][0]).days,
            'trend':trend,'volatility':volatility,'ma20_over_ma60_pct':round(spread,3),
            'momentum20_pct':round(mom20,3),'daily_volatility20_pct':round(vol20,3),
            'history_bars':len(ordered),'rule_version':VERSION,
            'source_note':'KIS 지수 참고값 · 관찰 전일까지 완료된 봉만 사용 · 추정 확률 아님'}


def capture_market_context(*, now: datetime|None=None, loader=None) -> dict:
    """Two bounded index queries; each can independently fail closed.

    Called AFTER a market scan. Timestamp reflects the time these reference
    candles were captured, not an earlier scheduled job start.
    """
    when=now or datetime.now(timezone.utc)
    if when.tzinfo is None:raise ValueError('timezone aware time required')
    if loader is None:
        from kis_reference import fetch_kis_reference
        loader=lambda ticker:fetch_kis_reference(ticker,now=when)
    result={}
    for ticker in BENCHMARKS:
        try:result[ticker]=classify_benchmark(loader(ticker),when,ticker)
        except Exception:result[ticker]=unknown_context(ticker,'provider_unavailable')
    return result


def context_for_symbol(symbol: str, contexts: dict|None) -> dict:
    benchmark='^KS11' if symbol.endswith('.KS') else '^KQ11' if symbol.endswith('.KQ') else None
    if benchmark is None:return {'status':'UNAVAILABLE','reason':'symbol_market_missing'}
    if not isinstance(contexts,dict) or not isinstance(contexts.get(benchmark),dict):
        return unknown_context(benchmark,'not_recorded')
    obj=contexts[benchmark]
    if obj.get('benchmark')!=benchmark or obj.get('status') not in ('READY','UNAVAILABLE'):
        return unknown_context(benchmark,'invalid_context')
    return {k:v for k,v in obj.items() if k in (
        'status','benchmark','name','reason','asof','observed_at','lag_calendar_days',
        'trend','volatility','ma20_over_ma60_pct','momentum20_pct',
        'daily_volatility20_pct','history_bars','rule_version','source_note')}


def _pct(v):return round(v,3)


def _observed_kst_day(rec: dict) -> str:
    t=datetime.fromisoformat(rec['observed_at'])
    if t.tzinfo is None:raise ValueError('observation timestamp must be timezone-aware')
    return t.astimezone(KST).date().isoformat()


def _summary(samples, *, lane: str):
    n=len(samples)
    if not n:return {'completed':0,'unique_days':0,'unique_runs':0,'hit_rate_pct':None,
                     'mean_net_long_pct':None,'mean_price_change_pct':None,
                     'statistical_confidence':'INSUFFICIENT'}
    raw=[float(o['asset_change_pct']) for _,o in samples]
    net=[float(o['net_long_pct']) for _,o in samples]
    days={_observed_kst_day(r) for r,_ in samples}
    runs={r['run_key'] for r,_ in samples}
    qualified=n>=MIN_OBSERVATIONS and len(runs)>=MIN_CLUSTERS and len(days)>=MIN_CLUSTERS
    return {'completed':n,'unique_days':len(days),'unique_runs':len(runs),
            'hit_rate_pct':_pct(100*sum(p>0 for p in raw)/n) if lane=='STRICT_BUY' else None,
            'mean_net_long_pct':_pct(sum(net)/n) if lane=='STRICT_BUY' else None,
            'mean_price_change_pct':_pct(sum(raw)/n),
            'statistical_confidence':'DESCRIPTIVE_ONLY' if qualified else 'INSUFFICIENT'}


def _split_shadow_rule(samples):
    """Predeclared hypothesis, chronological holdout. Does NOT select a model or train.

    Hypothesis: skip NEW longs in a DOWN or HIGH-volatility regime. Cash=0%
    change per skipped observation. Observations of the same day remain together.
    Neither the hypothesis nor the holdout can change NOVA BUY/SELL decisions.
    """
    days=sorted({_observed_kst_day(r) for r,_ in samples})
    if len(samples)<MIN_OBSERVATIONS or len(days)<MIN_CLUSTERS:
        return {'status':'NOT_READY','reason':'need_30_completed_and_12_unique_observation_days',
                'completed':len(samples),'unique_days':len(days),'live_adoption_allowed':False}
    boundary=max(1,int(len(days)*.7))
    train_days=set(days[:boundary]);test_days=set(days[boundary:])
    groups={'earlier':[(r,o) for r,o in samples if _observed_kst_day(r) in train_days],
            'later':[(r,o) for r,o in samples if _observed_kst_day(r) in test_days]}
    if min(len(groups['earlier']),len(groups['later']))<10 or len(test_days)<4:
        return {'status':'NOT_READY','reason':'chronological_holdout_too_small',
                'completed':len(samples),'unique_days':len(days),'live_adoption_allowed':False}
    out={}
    for part,rows in groups.items():
        baseline=sum(float(o['net_long_pct']) for _,o in rows)/len(rows)
        kept=[float(o['net_long_pct']) for r,o in rows if r['market_regime']['trend']!='DOWN'
              and r['market_regime']['volatility']!='HIGH']
        out[part]={'completed':len(rows),'unique_days':len({_observed_kst_day(r) for r,_ in rows}),
                   'kept':len(kept),'skipped':len(rows)-len(kept),
                   'baseline_mean_net_pct':_pct(baseline),
                   'shadow_mean_net_pct':_pct(sum(kept)/len(rows)),
                   'difference_pp':_pct(sum(kept)/len(rows)-baseline)}
    return {'status':'RESEARCH_ONLY','rule':'Skip prospective long when DOWN or HIGH volatility; skip=0% for comparison',
            'chronological_split':'earliest 70% of observation days vs latest 30%',
            'earlier':out['earlier'],'later':out['later'],
            'live_adoption_allowed':False,
            'disclaimer':'신호 수익이 아닌 각 관찰의 가상 순변화 평균. 순위·일자 상관, 거래비용·미체결·자금 제한·데이터 누락과 시장 환경 변화를 충분히 반영하지 않음. 실거래 우위가 입증된 것이 아님.'}


def regime_report(candidates: list[dict], completed: dict, *, limit: int=500) -> dict:
    """An observational report, based on immutable snapshots and future outcome records.

    Never back-label legacy candidate observations. Only observations that
    originally stored a READY benchmark context enter regime results.
    """
    if not 1<=limit<=1000:raise ValueError('limit out of range')
    records=candidates[:limit]
    unrecorded=0;unavailable=0;ready=0;segments=defaultdict(list);markets=defaultdict(list)
    held_out=[]
    for r in records:
        context=r.get('market_regime')
        if context is None:
            unrecorded+=1;continue
        if context.get('status')!='READY':
            unavailable+=1;continue
        if context.get('benchmark') not in BENCHMARKS or context.get('trend') not in ('UP','DOWN','SIDEWAYS') or context.get('volatility') not in ('HIGH','NORMAL'):
            unavailable+=1;continue
        # Treat only frozen original observation-time context as eligible.
        try:
            context_asof=context.get('asof','')
            if not isinstance(context_asof,str) or context_asof>=_observed_kst_day(r):
                unavailable+=1;continue
        except (ValueError,TypeError,KeyError):
            unavailable+=1;continue
        ready+=1
        for h in HORIZONS:
            outcome=completed.get((r['id'],h))
            if not isinstance(outcome,dict) or outcome.get('status')!='complete':continue
            try:
                if not all(isfinite(float(outcome[k])) for k in ('asset_change_pct','net_long_pct')):continue
            except (TypeError,ValueError,KeyError):continue
            key=(r['lane'],context['benchmark'],context['trend'],context['volatility'],h)
            segments[key].append((r,outcome))
            markets[(r['lane'],context['benchmark'],h)].append((r,outcome))
            if h==5 and r['lane']=='STRICT_BUY':held_out.append((r,outcome))
    results=[]
    for k,rows in sorted(segments.items()):
        lane,benchmark,trend,volatility,h=k
        x=_summary(rows,lane=lane)
        baseline=_summary(markets[(lane,benchmark,h)],lane=lane)
        # delta is purely within observed subgroup vs the same-index cohort.
        x.update(lane=lane,benchmark=benchmark,name=BENCHMARKS[benchmark],
                 trend=trend,volatility=volatility,horizon=h,
                 same_market_completed=baseline['completed'],
                 same_market_mean_net_pct=baseline['mean_net_long_pct'],
                 delta_from_market_pp=(_pct(x['mean_net_long_pct']-baseline['mean_net_long_pct'])
                     if x['mean_net_long_pct'] is not None and baseline['mean_net_long_pct'] is not None else None))
        results.append(x)
    return {'research_version':VERSION,'status':'observed' if ready else 'insufficient_context',
            'total_candidates':len(records),'context_recorded':ready,
            'legacy_unrecorded':unrecorded,'provider_unavailable':unavailable,
            'horizons':list(HORIZONS),'regime_groups':results,
            'shadow_policy_5d':_split_shadow_rule(held_out),
            'live_signals_changed':False,'automated_orders':False,
            'limitations':'규칙 기반 지수 국면 분류(학습형 AI 아님). KIS 지수 기준 관찰 전일까지 완료된 봉만 사용; 과거 v4.6 기록은 소급 분류하지 않음. 지수 참고 자료 지연·배당/조정·상관 표본·탐색 편향 때문에 분석 성과·실전 정확도 보장 불가. 표본 30건 및 관찰일 12일 미만은 결론 보류.'}
