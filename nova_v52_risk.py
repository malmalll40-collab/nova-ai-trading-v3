"""NOVA v5.2 -- read-only, point-in-time risk-filter and vote-overlap audit.

Fixed candidate thresholds; no best-fit selection, live signal modification,
portfolio blending, or database writes. Use only completed signal-day bars.
Market regime uses matched market sessions ending BEFORE the signal day.
"""
from __future__ import annotations

from itertools import combinations
import math
import pandas as pd
from core_comparison import _day, _reference

RISK_FILTERS = (
    ('atr', '높은 변동성 차단', '신호일 ATR/종가가 5% 이하여야 함'),
    ('extension', '추격 매수 제한', '신호일 종가가 EMA20 + 2 ATR 이하여야 함'),
    ('rsi', '과열 구간 제한', '신호일 RSI가 72 이하여야 함'),
    ('combined', '위험 3요소 동시 적용', 'ATR·EMA20 이격·RSI 세 조건 모두 만족'),
    ('market', '전일 시장 추세 확인', '신호 전일까지 20거래일 지수 수익률 양수 및 20일 평균 상회'),
)


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def risk_flags(f: pd.DataFrame, i: int, market: dict | None) -> dict:
    """Observe only row i and earlier. Unknown is not the same as pass."""
    row = f.iloc[i]
    close = _number(row.get('Close'))
    atr = _number(row.get('atr'))
    ema20 = _number(row.get('ema20'))
    rsi = _number(row.get('rsi'))
    ratio = atr / close if close and close > 0 and atr and atr > 0 else None
    extension = (close - ema20) / atr if close is not None and ema20 is not None and atr and atr > 0 else None
    market_return = None
    market_ok = None
    # All twenty-one index observations correspond to PREVIOUS equity sessions.
    if market is not None and i >= 21:
        days = [_day(d) for d in f.index[i-21:i]]
        if len(set(days)) == 21 and all(d in market for d in days):
            prices = [market[d] for d in days]
            if all(_number(p) is not None and p > 0 for p in prices):
                market_return = prices[-1] / prices[0] - 1
                market_ok = bool(market_return >= 0 and prices[-1] >= sum(prices[-20:]) / 20)
    atr_ok = ratio <= .05 if ratio is not None else None
    extension_ok = extension <= 2 if extension is not None else None
    rsi_ok = rsi <= 72 if rsi is not None and 0 <= rsi <= 100 else None
    combined = None if any(x is None for x in (atr_ok, extension_ok, rsi_ok)) else all((atr_ok, extension_ok, rsi_ok))
    return {
        'atr': atr_ok, 'extension': extension_ok, 'rsi': rsi_ok,
        'combined': combined, 'market': market_ok,
        'atr_close_pct': round(ratio * 100, 2) if ratio is not None else None,
        'ema20_distance_atr': round(extension, 2) if extension is not None else None,
        'rsi_value': round(rsi, 2) if rsi is not None else None,
        'market_prior_20d_pct': round(market_return * 100, 2) if market_return is not None else None,
    }


def role_overlap(vote_matrix: dict[int, list[int]], cut: int) -> dict:
    """Co-active directional agreement, not inflated by the shared HOLD state."""
    validation = [v for i, v in vote_matrix.items() if i >= cut and len(v) >= 10]
    results = []
    for a, b in combinations(range(10), 2):
        active_a = sum(v[a] != 0 for v in validation)
        active_b = sum(v[b] != 0 for v in validation)
        jointly = [v for v in validation if v[a] != 0 and v[b] != 0]
        union = sum(v[a] != 0 or v[b] != 0 for v in validation)
        aligned = sum(v[a] == v[b] for v in jointly)
        count = len(jointly)
        overlap = count / union if union else None
        agreement = aligned / count if count else None
        # Require both nontrivial co-activity and broad overlap for an alert.
        suspected = count >= 12 and union >= 18 and agreement is not None and agreement >= .9 and overlap is not None and overlap >= .7
        results.append({'roles':[a+1,b+1], 'active_counts':[active_a,active_b],
                        'both_active':count, 'either_active':union,
                        'direction_agreement_pct':round(100*agreement,1) if agreement is not None else None,
                        'activity_overlap_pct':round(100*overlap,1) if overlap is not None else None,
                        'redundancy_candidate':suspected})
    prioritized = sorted(results,key=lambda r:(r['redundancy_candidate'],r['both_active'],r['direction_agreement_pct'] or 0),reverse=True)
    return {'validation_sessions':len(validation),'evaluated_pairs':len(results),
            'candidates':[r for r in prioritized if r['redundancy_candidate']][:10],
            'most_observed_pairs':prioritized[:6],
            'interpretation':'동시 활성 시 같은 방향 여부만 비교합니다. 비활성(HOLD) 일치는 제외하며, 높은 일치도는 중복 가능성일 뿐 인과관계나 제거 지시가 아닙니다.'}


def study_risks(f:pd.DataFrame, baseline:dict[int,str], votes:dict[int,list[int]],
                *, formation_start:int, cut:int, stop:int, cost_bps:float,
                benchmark:pd.DataFrame|None, simulator) -> dict:
    """Fixed-signal counterfactuals on separate, same-sample research windows."""
    market, reason = _reference(benchmark)
    indices = [i for i in baseline if formation_start <= i < stop]
    flags = {i:risk_flags(f,i,market) for i in indices if baseline[i]=='BUY'}
    original = simulator(f,baseline,cut,stop,cost_bps,include_trades=True)
    original_formation = simulator(f,baseline,formation_start,cut,cost_bps,include_trades=True)
    by_date = {_day(f.index[i]):i for i in indices}
    trade_insights=[]
    for t in original.get('trade_ledger',[]):
        idx=by_date.get(t.get('signal_date'))
        if idx is None:continue
        feat=flags.get(idx,risk_flags(f,idx,market))
        entry_i=idx+1
        pre_close=_number(f['Close'].iloc[idx]);entry_open=_number(f['Open'].iloc[entry_i]) if entry_i < len(f) else None
        gap = (entry_open/pre_close-1)*100 if pre_close and entry_open and pre_close>0 and entry_open>0 else None
        blocked=[label for key,label,_ in RISK_FILTERS if feat.get(key) is False]
        uncertain=[label for key,label,_ in RISK_FILTERS if feat.get(key) is None]
        trade_insights.append({'signal_date':t['signal_date'], 'entry_date':t['entry_date'],
                               'exit_date':t['exit_date'],'trade_return_pct':t['net_trade_return_pct'],
                               'signal_day_features':{k:v for k,v in feat.items() if k not in ('combined',)},
                               'open_gap_pct':round(gap,2) if gap is not None else None,
                               'open_gap_executability':'다음날 시가 갭은 체결 시점 정보로만 기록하며, 동일 시가로 필터링할 수 있다고 가정하지 않음',
                               'flagged_risks':blocked,'unknown_risks':uncertain,
                               'risk_flags_are_causes':False})
    trials=[]
    for key,label,rule in RISK_FILTERS:
        if key=='market' and market is None:
            trials.append({'key':key,'label':label,'rule':rule,'status':'UNAVAILABLE',
                           'reason':reason or '시장지수 기준 데이터 없음','adopted':False})
            continue
        # Unknown on a BUY opportunity invalidates the entire experiment.
        missing=sum(flags[i][key] is None for i in flags)
        if missing:
            trials.append({'key':key,'label':label,'rule':rule,'status':'UNAVAILABLE',
                           'reason':f'매수 기회 중 판단에 필요한 데이터 미확인 {missing}건', 'adopted':False})
            continue
        revised={i:('HOLD' if v=='BUY' and not flags[i][key] else v) for i,v in baseline.items()}
        formative=simulator(f,revised,formation_start,cut,cost_bps,include_trades=True)
        validation=simulator(f,revised,cut,stop,cost_bps,include_trades=True)
        before={t['signal_date']:t for t in original.get('trade_ledger',[])}
        after={t['signal_date']:t for t in validation.get('trade_ledger',[])}
        avoided=[before[k] for k in sorted(before.keys()-after.keys())]
        created=[after[k] for k in sorted(after.keys()-before.keys())]
        winning_avoided=sum(t['net_trade_return_pct']>0 for t in avoided)
        trials.append({'key':key,'label':label,'rule':rule,'status':'READY' if formative.get('status')=='READY' and validation.get('status')=='READY' else 'UNAVAILABLE',
                       'formation':formative,'validation':validation,
                       'validation_return_difference_pp':round(validation['portfolio_return_pct']-original['portfolio_return_pct'],3) if validation.get('status')=='READY' and original.get('status')=='READY' else None,
                       'blocked_buy_signal_count':sum(baseline[i]=='BUY' and revised[i]=='HOLD' for i in baseline if i>=cut),
                       'avoided_trade_count':len(avoided),'new_trade_count':len(created),
                       'avoided_winners':winning_avoided,
                       'avoided_trade_examples':avoided[:12], 'created_trade_examples':created[:12],
                       'adopted':False,
                       'interpretation':'사전에 고정한 조건을 독립 구간에서 비교한 가상 결과. 검증 표본이 작고 필터 간 다중 비교가 있으므로 수익률 개선은 확증되지 않음.'})
    return {'status':'READY','version':'5.2.0','research_only':True,'modifies_live_signal':False,
            'writes_observation_database':False,'adopted':False,
            'baseline_validation_trades':original.get('completed_trades',0),
            'baseline_formation_trades':original_formation.get('completed_trades',0),
            'loss_trade_count':sum(t['trade_return_pct']<0 for t in trade_insights),
            'risk_ledger':trade_insights[:50], 'risk_ledger_truncated':len(trade_insights)>50,
            'filter_trials':trials,'role_overlap':role_overlap(votes,cut),
            'review_status':'검증 대기 — 데이터 부족' if original.get('completed_trades',0)<20 else '추가 독립 검증 필요',
            'scope':'과거 13역할 원신호만 연구. 5-Core·AI·실전 최종 매매/재무 필터에는 적용하지 않음.',
            'bias_warning':'같은 기간에서 여러 필터를 비교하면 선택 편향이 발생합니다. 현재 결과는 향후 표본으로 전향 검증해야 합니다.'}
