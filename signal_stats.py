"""NOVA v4.0: symbol/mode-specific exploratory BUY-condition statistics.

Each of the first 10 technical BUY roles is evaluated separately. Events are
chosen on the last COMPLETED candle, assuming entry at next candle OPEN and
exit at candle close after a predeclared fixed horizon. Events from the same
role may NOT overlap within each segment; positive states held for many bars
can be sampled again after a full horizon (NOT a fresh crossing signal).

A TRAIN-only ranking selects one candidate; a separate recent chronological
validation block is a diagnostic, not a calibrated probability. This does not
recreate historical financial overlays, stops, execution or live fills.
"""
from __future__ import annotations
from math import sqrt
import math
import pandas as pd
from engine import Config, WARMUP, vote_row
from quality_validation import HORIZON

TRAIN_FRACTION = 0.70
TECHNICAL_ROLES = 10
MIN_TRAIN = 20
MIN_VALIDATION = 15


def _wilson_lower(wins: int, n: int, z: float = 1.96) -> float | None:
    if n == 0:
        return None
    p = wins / n
    zz = z * z
    result = (p + zz / (2*n) - z*sqrt((p*(1-p) + zz/(4*n))/n))/(1+zz/n)
    return round(max(0.0, result)*100, 1)


def _summary(rows: list[float]) -> dict:
    n = len(rows)
    wins = sum(r > 0 for r in rows)
    return {
        'samples': n,
        'wins': wins,
        'hit_pct': round(wins*100/n, 1) if n else None,
        'wilson_lower_pct': _wilson_lower(wins, n),
        'mean_net_pct': round(sum(rows)/n, 3) if n else None,
        'worst_net_pct': round(min(rows), 3) if n else None,
    }


def _rank_metric(role: dict):
    s = role['train']
    return (s['wilson_lower_pct'] or 0, s['mean_net_pct'] or -1e9, s['samples'], role['id'])


def signal_performance(features: pd.DataFrame, cfg: Config) -> dict:
    """No future data relative to each historical candle is used for its BUY role.

    This is per-symbol research only; latest votes are NOT claimed as proven
    profitable and the display never overrides the NOVA final action.
    """
    if cfg.mode not in HORIZON:
        raise ValueError('지원하지 않는 분석 모드입니다.')
    h = HORIZON[cfg.mode]
    n = len(features)
    if n < WARMUP + h + 3:
        raise ValueError('매수 조건 검증에 사용할 완료봉이 부족합니다.')
    split = max(WARMUP + h + 1, min(n-h-1, int(n*TRAIN_FRACTION)))
    names = []
    events: list[dict[str, list[float]]] = [dict(train=[], validation=[]) for _ in range(TECHNICAL_ROLES)]
    next_allowed = {segment: [WARMUP-1]*TECHNICAL_ROLES for segment in ('train','validation')}
    latest_active = []

    # Rows in [split-h,split) are deliberately embargoed: their outcome could
    # cross into the validation period and contaminate training results.
    for i in range(WARMUP-1, n):
        signal, votes, _, _ = vote_row(features.iloc[i], cfg.mode, cfg)
        roles = votes[:TECHNICAL_ROLES]
        if not names:
            names = [v.role for v in roles]
        if i == n-1:
            latest_active = [j for j, v in enumerate(roles) if v.direction == 1]
        if i+h >= n:  # do not score incomplete future candles
            continue
        if i+h < split:
            segment = 'train'
        elif i >= split:
            segment = 'validation'
        else:
            continue  # training/validation embargo gap
        entry = float(features['Open'].iloc[i+1])
        settle = float(features['Close'].iloc[i+h])
        if not (math.isfinite(entry) and math.isfinite(settle) and entry > 0 and settle > 0):
            continue
        # Same convention as quality_comparison: simplified round-trip fee.
        net_pct = (settle/entry - 1)*100 - 2*cfg.cost_bps/100
        for j, vote in enumerate(roles):
            if vote.direction == 1 and i >= next_allowed[segment][j]:
                events[j][segment].append(net_pct)
                next_allowed[segment][j] = i + h + 1

    ranked=[]
    for j, name in enumerate(names):
        train = _summary(events[j]['train'])
        val = _summary(events[j]['validation'])
        ready_train = train['samples'] >= MIN_TRAIN
        # Outcome evaluated on chronological tail, not a future forecast.
        ready_val = val['samples'] >= MIN_VALIDATION
        row = {
            'id': f'{j+1:02d}', 'name': name,
            'active_now': j in latest_active,
            'train': train, 'validation': val,
            'train_sufficient': ready_train,
            'validation_sufficient': ready_val,
            'validation_positive': ready_val and val['mean_net_pct'] is not None and val['mean_net_pct'] > 0 and val['hit_pct'] > 50 and (val['wilson_lower_pct'] or 0) >= 40,
        }
        ranked.append(row)

    ranked.sort(key=lambda x: (x['train_sufficient'],) + _rank_metric(x), reverse=True)
    selected = next((r for r in ranked if r['train_sufficient'] and r['train']['mean_net_pct'] > 0), None)
    selected_validated = bool(selected and selected['validation_positive'])
    # Do not choose the validation leader after seeing the holdout outcomes.
    result = {
        'method': '첫 70% 선택 / 최근 30% 검증 / 구간 간 결과 embargo / 다음 봉 시가 진입 / 미래 완료봉 종가 청산 / 왕복 비용 반영',
        'mode': cfg.mode,
        'horizon_bars': h,
        'train_fraction': TRAIN_FRACTION,
        'min_train': MIN_TRAIN,
        'min_validation': MIN_VALIDATION,
        'latest_completed_bar': str(features.index[-1]),
        'selected_id': selected['id'] if selected else None,
        'selected_name': selected['name'] if selected else None,
        'selected_validation_positive': selected_validated,
        'selected_active_now': bool(selected and selected['active_now']),
        'roles': ranked,
        'active_buy_roles': sum(r['active_now'] for r in ranked),
        'limitations': ('개별 조건 충족 상태에서 겹치지 않도록 간격을 둔 가상 진입 관찰값이며 실제 신호 체결 이력·상승 확률이 아닙니다. '
                        '같은 종목·장세, 인접 역할, 거래비용, 데이터 길이 및 다중 비교에 영향을 받습니다. '
                        '여러 조건 중 훈련 구간 1개를 고르고 최근 구간에서 확인한 탐색적 결과입니다. '
                        '손절·목표가·과거 재무정보·호가·슬리피지·실거래 수수료/세금은 별도 검증이 필요합니다.'),
    }
    return result
