"""NOVA v5.3 read-only market-history audit, grouped-vote experiment and evidence gate.

Design constraints:
- Index values must match actual previous completed stock sessions; never interpolate.
- Outcome-derived diagnostics are explicitly retrospective, never trading features.
- Fixed grouped-rule candidate is unoptimised and is never promoted to production.
- No database mutations, broker actions, state changes or order endpoints.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite

import pandas as pd
from core_comparison import _day, _reference
from nova_v5_research import simulate_long_only

# Defined before looking at realised performance; every independent vote is counted once.
GROUPS = (
    ('trend', '추세군 (01·02·03·07)', (0, 1, 2, 6)),
    ('momentum', '모멘텀군 (04·06)', (3, 5)),
    ('entry', '진입군 (05·10)', (4, 9)),
    ('confirmation', '참여·반전군 (08·09)', (7, 8)),
)


def benchmark_coverage(f: pd.DataFrame, benchmark: pd.DataFrame | None, indices: list[int], *, lag: int = 51) -> dict:
    """Audit exactly matched historical index dates, never nearest-date forward fill."""
    prices, reason = _reference(benchmark)
    if prices is None:
        return {'status': 'UNAVAILABLE', 'reason': reason, 'source': 'KIS 코스피·코스닥 지수 (연구)',
                'previous_sessions_only': True, 'missing_sessions': None,
                'completion_method': 'missing-is-unknown; no interpolation'}
    dates = sorted(prices)
    sample = [i for i in indices if i >= lag and i < len(f)]
    missing = set()
    matched = 0
    for i in sample:
        required = [_day(x) for x in f.index[i-lag:i]]
        if len(set(required)) != lag:
            missing.update(required)
            continue
        absent = [d for d in required if d not in prices]
        if absent:
            missing.update(absent)
        else:
            matched += 1
    first = dates[0] if dates else None
    short_history_count = sum(d < first for d in missing) if first else 0
    return {
        'status': 'READY' if sample and matched == len(sample) else 'INCOMPLETE',
        'reference_first': first, 'reference_last': dates[-1] if dates else None,
        'reference_bars': len(dates), 'stock_bars': len(f), 'reference_lag': lag,
        'evaluation_sessions': len(sample), 'matched_sessions': matched,
        'unmatched_sessions': len(sample)-matched,
        'matching_pct': round(100*matched/len(sample), 1) if sample else None,
        'missing_sessions': len(missing), 'missing_dates_examples': sorted(missing)[:12],
        'before_reference_start_count': short_history_count,
        'source': 'KIS 코스피·코스닥 지수 (연구)', 'previous_sessions_only': True,
        'completion_method': 'exact matching; never fill/interpolate missing index prices',
        'reliability_note': '현재 조회한 과거 지수는 당시 공개 시각이 확인된 기록이 아님',
    }


def _direction(votes: list[int], columns: tuple[int, ...]) -> int:
    """Independent group ballot; zero denotes missing consensus or HOLD."""
    vals = [votes[j] for j in columns]
    buy = sum(v == 1 for v in vals)
    sell = sum(v == -1 for v in vals)
    if buy > sell and buy >= 1:
        return 1
    if sell > buy and sell >= 1:
        return -1
    return 0


def grouped_signal(votes: list[int], *, risk_veto: bool = False) -> dict:
    """4 fixed non-overlapping blocks. Unlike raw 13-role voting, no double ballot."""
    if len(votes) < 10:
        raise ValueError('10개 독립 역할 값 필요')
    groups = {key: _direction(votes, members) for key, _, members in GROUPS}
    # Pre-specified candidate: primary trend plus momentum OR entry confirmation;
    # confirmation is a veto only when decidedly bearish.
    buy = (not risk_veto and groups['trend'] == 1 and
           (groups['momentum'] == 1 or groups['entry'] == 1) and
           groups['confirmation'] >= 0)
    return {'signal': 'BUY' if buy else 'HOLD', 'groups': groups,
            'risk_veto': bool(risk_veto)}


def grouped_vote_study(f: pd.DataFrame, baseline: dict[int, str],
                       votes: dict[int, list[int]], risk_vetos: dict[int, bool],
                       *, formation_start: int, cut: int, stop: int, cost_bps: float) -> dict:
    shadow = {i: grouped_signal(v, risk_veto=risk_vetos.get(i, False))['signal']
              for i, v in votes.items()}
    old = simulate_long_only(f, baseline, cut, stop, cost_bps, include_trades=True)
    alternative = simulate_long_only(f, shadow, cut, stop, cost_bps, include_trades=True)
    baseline_trades = {x['signal_date']: x for x in old.get('trade_ledger', [])}
    shadow_trades = {x['signal_date']: x for x in alternative.get('trade_ledger', [])}
    missed = [baseline_trades[d] for d in sorted(baseline_trades.keys()-shadow_trades.keys())]
    added = [shadow_trades[d] for d in sorted(shadow_trades.keys()-baseline_trades.keys())]
    idxs = [i for i in sorted(votes) if cut <= i < stop]
    split = cut + max(1, (stop-cut)//2)
    folds = []
    for lo, hi in ((cut, split), (split, stop)):
        a = simulate_long_only(f, baseline, lo, hi, cost_bps)
        b = simulate_long_only(f, shadow, lo, hi, cost_bps)
        folds.append({'start': _day(f.index[lo]), 'end': _day(f.index[hi-1]),
                      'baseline': a, 'grouped': b,
                      'independent_predeclared_test': False,
                      'reason': '동일 연구 기간을 나눈 설명용 하위 구간; 새로운 전향 표본이 아님'})
    return {
        'status': 'READY', 'strategy': '사전 고정 4군 역할 중복 투표 제거 실험',
        'groups': [{'id': k, 'label': label, 'indices': [x+1 for x in members]}
                   for k, label, members in GROUPS],
        'baseline': old, 'grouped': alternative,
        'formation': simulate_long_only(f, shadow, formation_start, cut, cost_bps),
        'validation_start': _day(f.index[cut]), 'validation_end': _day(f.index[stop-1]),
        'validation_folds': folds,
        'buy_signal_count': sum(shadow.get(i) == 'BUY' for i in idxs),
        'avoided_trade_count': len(missed),
        'avoided_winners': sum(t['net_trade_return_pct'] > 0 for t in missed),
        'added_trade_count': len(added),
        'avoided_trade_examples': missed[:8], 'added_trade_examples': added[:8],
        'research_only': True, 'live_adopted': False,
        'retrained_or_tuned_on_validation': False,
        'interpretation': '같은 기간의 비교일 뿐 새 엔진 우수성이 입증되지 않음. 거래 0건이면 평가 불가.',
    }


def retrospective_loss_paths(f: pd.DataFrame, ledger: list[dict], risk_audit: dict) -> list[dict]:
    """After-trade paths may explain *what happened*, never *why* it was foreseeable."""
    loc = {_day(d): i for i, d in enumerate(f.index)}
    by_signal = {x['signal_date']: x for x in risk_audit.get('risk_ledger', [])}
    result = []
    for tr in ledger[:60]:
        start = loc.get(tr.get('entry_date'))
        end = loc.get(tr.get('exit_date'))
        price = float(tr.get('entry_price') or 0)
        if start is None or end is None or start > end or price <= 0:
            continue
        window = f.iloc[start:end+1]
        lows = pd.to_numeric(window['Low'], errors='coerce')
        highs = pd.to_numeric(window['High'], errors='coerce')
        if lows.isna().any() or highs.isna().any():
            continue
        worst = (float(lows.min())/price-1)*100
        best = (float(highs.max())/price-1)*100
        diag = by_signal.get(tr.get('signal_date'), {})
        result.append({
            'signal_date': tr['signal_date'], 'entry_date': tr['entry_date'],
            'exit_date': tr['exit_date'], 'net_trade_return_pct': tr['net_trade_return_pct'],
            'max_adverse_excursion_pct': round(worst, 2),
            'max_favorable_excursion_pct': round(best, 2),
            'worst_bar_date': _day(lows.idxmin()),
            'best_bar_date': _day(highs.idxmax()),
            'known_at_signal': diag.get('signal_day_features', {}),
            'known_at_signal_warnings': diag.get('flagged_risks', []),
            'unknown_at_signal': diag.get('unknown_risks', []),
            'outcomes_are_not_features': True, 'causal_explanation': False,
            'description': '진입 이후의 최저·최고가는 사후 결과 설명 전용. 당시 매수 신호 재계산에 절대 사용하지 않음',
        })
    return result


def research_evidence_gate(*, baseline_trades: int, ai_model: dict,
                           market_audit: dict, number_of_stocks: int = 1,
                           truly_future_observations: int = 0) -> dict:
    """Fail-closed strategy adoption gate. NEVER authorizes real trades or signals."""
    reasons = []
    if baseline_trades < 20:
        reasons.append(f'기본 엔진 검증 거래 {baseline_trades}건 · 최소 20건 미충족')
    if number_of_stocks < 4:
        reasons.append(f'독립 검증 종목 {number_of_stocks}개 · 최소 4개 미충족')
    if truly_future_observations < 30:
        reasons.append(f'사후 전향 관찰 {truly_future_observations}건 · 최소 30건 미충족')
    if market_audit.get('status') != 'READY':
        reasons.append('시장지수 일별 자료 완전성 미확인')
    if ai_model.get('status') != 'READY':
        reasons.append('AI 학습/검증 표본 또는 모델 상태 부족')
    else:
        a = ai_model.get('brier_score')
        b = ai_model.get('naive_train_rate_brier')
        if a is None or b is None or not isfinite(a) or not isfinite(b) or a >= b:
            reasons.append('AI Brier 점수가 단순 기준보다 개선되지 않음')
    # Even if the structural minima were met, a separate prospective review is mandatory.
    return {'action': 'ABSTAIN', 'status': 'INSUFFICIENT' if reasons else 'REVIEW_REQUIRED',
            'reasons': reasons or ['최종 독립·전향 검증 및 운영 승인 필요'],
            'live_signal_changed': False, 'orders_permitted': False,
            'scope': '연구 전략 승격 여부만 보류. 현재 서비스 원신호의 매수·매도 권고를 바꾸지 않음'}


def summarize_batch(rows: list[dict]) -> dict:
    """Read-only cross-stock audit. Never sum standalone portfolio returns."""
    ready = [r for r in rows if r.get('status') == 'READY']
    source_counts = dict(Counter(str(r.get('source') or 'unknown') for r in ready))
    spans = {(r.get('validation_start'), r.get('validation_end')) for r in ready}
    baseline_trades = sum((r.get('baseline') or {}).get('completed_trades', 0) for r in ready)
    ai_scores = [(r.get('ai_model') or {}) for r in ready]
    brier_better = sum(x.get('status') == 'READY' and
                       isinstance(x.get('brier_score'), (float, int)) and
                       isinstance(x.get('naive_train_rate_brier'), (float, int)) and
                       x['brier_score'] < x['naive_train_rate_brier'] for x in ai_scores)
    return {
        'status': 'INCOMPLETE' if len(ready) != len(rows) else 'READY',
        'stocks_completed': len(ready), 'stocks_requested': len(rows),
        'validation_ranges_identical': len(spans) <= 1 if ready else False,
        'source_counts': source_counts, 'validation_trades_across_separate_stocks': baseline_trades,
        'ai_beats_naive_stocks': brier_better,
        'research_strategy_adoption': 'ABSTAIN',
        'research_only': True, 'live_signal_changed': False,
        'caveat': '종목별 개별 가상 투자 결과. 합산 수익률이나 포트폴리오 우수성으로 해석하지 않음. 기간·공급원·표본 격차 확인 필요.'
    }
