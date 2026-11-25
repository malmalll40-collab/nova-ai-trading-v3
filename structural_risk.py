"""Read-only, point-in-time structural stop/target reference levels.

Do not replace the existing ATR levels or alter trade signals.  All pivots
must be confirmed by bars that already closed at the analysis snapshot.
"""
from __future__ import annotations
import math
from typing import Any
import pandas as pd

MIN_RR_NET = 1.3
MAX_STOP_ATR = 2.8
SUPPORT_BUFFER_ATR = .20
RESISTANCE_BUFFER_ATR = .15


def _num(x: Any) -> float | None:
    try:
        y = float(x)
        return y if math.isfinite(y) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _round(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 6)


def _levels(values: list[float], entry: float, below: bool) -> list[float]:
    # Keep all distinct historical prices; entry-dependent filtering on reprice.
    return sorted({round(v, 6) for v in values if _num(v) is not None and v > 0})


def evidence_from_features(features: pd.DataFrame, cfg: Any, at: int = -1) -> dict:
    """Collect confirmed pivot candidates from bars before and including `at`.

    `at` may be used to assert that appending future bars never changes an old
    snapshot.  Current bar is excluded from support/resistance discoveries.
    """
    if features is None or len(features) < 65:
        return {'available': False, 'reason': '완료봉 65개 이상 필요', 'support_candidates':[], 'resistance_candidates':[]}
    i = len(features) + at if at < 0 else at
    if i < 64 or i >= len(features):
        return {'available': False, 'reason': '봉 인덱스 범위 오류', 'support_candidates':[], 'resistance_candidates':[]}
    history = features.iloc[max(0, i-60):i]  # strictly prior bars, no current/future use
    close = _num(features.iloc[i]['Close'])
    atr = _num(features.iloc[i]['atr'])
    if not close or close <= 0 or not atr or atr <= 0 or len(history) < 20:
        return {'available': False, 'reason': '유효한 완료봉·ATR 자료 부족', 'support_candidates':[], 'resistance_candidates':[]}
    highs = [_num(v) for v in history['High']]
    lows = [_num(v) for v in history['Low']]
    if any(x is None or x <= 0 for x in highs+lows):
        return {'available': False, 'reason': '고가·저가 데이터 오류', 'support_candidates':[], 'resistance_candidates':[]}
    # Pivot (2-left,2-right) is confirmed strictly within historical closed bars.
    piv_lows = [lows[j] for j in range(2,len(lows)-2)
                if lows[j] < min(lows[j-2:j]) and lows[j] < min(lows[j+1:j+3])]
    piv_highs = [highs[j] for j in range(2,len(highs)-2)
                 if highs[j] > max(highs[j-2:j]) and highs[j] > max(highs[j+1:j+3])]
    # 20- and 50-prior-bar extrema are support/resistance candidate evidence.
    for width in (20, 50):
        if len(history) >= width:
            piv_lows.append(min(lows[-width:]))
            piv_highs.append(max(highs[-width:]))
    return {'available': True, 'reference_close': _round(close), 'atr':_round(atr),
            'mode':cfg.mode,'stop_atr':float(cfg.stop_atr),'target_atr':float(cfg.target_atr),
            'support_candidates':_levels(piv_lows,close,True),
            'resistance_candidates':_levels(piv_highs,close,False),
            'as_of_bar':str(features.index[i]),'bars_considered':len(history),
            'method':'confirmed_2x2_pivots_and_prior_20_50_extrema'}


def reprice_from_evidence(evidence: dict, *, entry_price: float|None=None, cost_bps: int=15,
                          demo:bool=False, stale:bool=False) -> dict:
    """Recompute with user-provided reference/entry price, never alter BUY/SELL.

    No invented structure if evidence is insufficient. Net RR includes both
    entry and exit friction; no stop fill/slippage guarantee.
    """
    base = {'method':'ATR + 확인된 지지·저항(연구 참고)','actionable':False,
            'backtested':False,'real_order':False,'stale':bool(stale),'demo':bool(demo),
            'user_entry_provided': entry_price is not None,
            'note':'실제 체결을 보장하지 않는 참고 시나리오. 기존 ATR선·매수신호·자동주문과 분리.'}
    if not isinstance(evidence, dict) or not evidence.get('available'):
        return {**base,'state':'INSUFFICIENT_DATA','reason':(evidence or {}).get('reason','자료 미확인'),
                'entry':None,'stop':None,'target':None,'risk_reward_net':None,
                'support':None,'resistance':None,'evidence': evidence}
    ref=_num(evidence.get('reference_close')); atr=_num(evidence.get('atr'))
    ent = ref if entry_price is None else _num(entry_price)
    if not ref or not atr or atr<=0 or not ent or ent<=0 or abs(ent/ref-1)>.30 or not isinstance(cost_bps,int) or not (0<=cost_bps<=100):
        return {**base,'state':'INVALID_ENTRY','reason':'진입가가 비정상적이거나 완료봉 종가 대비 ±30%를 벗어났습니다.',
                'entry':_round(ent),'stop':None,'target':None,'risk_reward_net':None,
                'support':None,'resistance':None,'evidence':evidence}
    sups=sorted([x for x in (_num(k) for k in evidence.get('support_candidates',[])[:150]) if x and x<ent],reverse=True)
    ress=sorted([x for x in (_num(k) for k in evidence.get('resistance_candidates',[])[:150]) if x and x>ent])
    support = sups[0] if sups else None
    resistance = ress[0] if ress else None
    stop_atr=ent-float(evidence['stop_atr'])*atr
    target_atr=ent+float(evidence['target_atr'])*atr
    stop=stop_atr
    reason_codes=[]
    if support is not None:
        under_support=support-SUPPORT_BUFFER_ATR*atr
        stop=min(stop_atr,under_support)
        if ent-stop>MAX_STOP_ATR*atr:
            reason_codes.append('SUPPORT_TOO_FAR')
    else: reason_codes.append('NO_SUPPORT')
    if stop <= 0:
        reason_codes.append('INVALID_STOP')
    target=target_atr
    if resistance is not None:
        target=min(target_atr,resistance-RESISTANCE_BUFFER_ATR*atr)
    else: reason_codes.append('NO_RESISTANCE')
    if target<=ent:reason_codes.append('RESISTANCE_TOO_CLOSE')
    friction=2*ent*cost_bps/10000
    net_profit=(target-ent)-friction
    net_risk=(ent-stop)+friction
    rr = net_profit/net_risk if net_risk>0 and target>ent and stop>0 else None
    if rr is None or rr<MIN_RR_NET:reason_codes.append('UNFAVORABLE_RR')
    if demo:reason_codes.append('DEMO_DATA')
    if stale:reason_codes.append('STALE_DATA')
    eligible = not reason_codes
    if eligible:
        state='RESEARCH_REFERENCE_ONLY';why='보수적 순손익비 조건을 충족한 연구 참고 시나리오입니다. 매수 승인이 아닙니다.'
    elif 'INVALID_STOP' in reason_codes or 'RESISTANCE_TOO_CLOSE' in reason_codes:
        state='NO_VALID_PLAN';why='유효한 손절·목표 구간을 구성할 수 없습니다.'
    elif 'SUPPORT_TOO_FAR' in reason_codes or 'UNFAVORABLE_RR' in reason_codes:
        state='UNFAVORABLE';why='지지선 위험 거리 또는 비용 반영 손익비가 불리하여 관찰만 가능합니다.'
    else:
        state='REFERENCE_ONLY';why='지지·저항/데이터 신뢰 정보가 부족해 ATR 참고 수준으로만 표시합니다.'
    return {**base,'state':state,'reason':why,'reason_codes':reason_codes,
            'entry':_round(ent),'reference_close':_round(ref),'atr':_round(atr),
            'stop':_round(stop) if stop>0 else None,'target':_round(target) if target>ent else None,
            'risk_reward_net':_round(rr),'rr_threshold':MIN_RR_NET,'fee_bps_each_side':cost_bps,
            'support':_round(support),'resistance':_round(resistance),
            'atr_stop':_round(stop_atr),'atr_target':_round(target_atr),
            'evidence':evidence}
