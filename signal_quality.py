"""NOVA 3.7: fixed, explainable buy-entry quality gates.

This is NOT a trained predictor, probability, or proof of higher accuracy.
Gates only use the current and prior *completed* bars. SELL remains an exit-only
signal, not a new short entry. No fundamental look-ahead is used here.
"""
from __future__ import annotations
from math import isfinite
from typing import Any
import pandas as pd
from engine import Config, WARMUP, decide


def _num(v: Any) -> float:
    try:
        result = float(v)
        return result if isfinite(result) else float('nan')
    except (ValueError, TypeError):
        return float('nan')


def quality_gate(features: pd.DataFrame, cfg: Config, i: int = -1,
                 raw_signal: str | None = None) -> dict:
    """Use fixed predeclared rules; no optimization from the evaluated history.

    Returns raw signal, conservative signal, 5 evidence categories and notes.
    The filter can only change BUY->HOLD, never invent BUY or turn SELL into short.
    """
    if i < 0:
        i += len(features)
    if i < WARMUP - 1 or i >= len(features):
        raise ValueError('기술 지표 준비 구간 밖입니다.')
    raw = raw_signal if raw_signal is not None else decide(features, cfg, i).signal
    if raw not in ('BUY','SELL','HOLD'):
        raise ValueError('올바른 기본 신호가 아닙니다.')
    r = features.iloc[i]
    p, atr = _num(r['Close']), _num(r['atr'])
    ema20, ema50 = _num(r['ema20']), _num(r['ema50'])
    ema50_prev = _num(features['ema50'].iloc[i-5])
    rsi = _num(r['rsi'])
    hist = _num(r['macd']) - _num(r['macd_s'])
    volume = _num(r['Volume'])
    vol_ratio = _num(r['vol_ratio'])
    atr_ratio = atr/p if p > 0 else float('nan')
    cost = 2*cfg.cost_bps/10000
    upside = cfg.target_atr*atr/p if p > 0 else float('nan')
    intraday = cfg.mode != '스윙'
    vwap = _num(r['vwap'])

    # 5 groups are conditions, not 5 independent probabilistic forecasts.
    trend = p >= ema20 and ema20 > ema50 and ema50 > ema50_prev
    momentum = 51 <= rsi <= 73 and hist > 0
    participation = volume > 0 and vol_ratio >= (0.75 if intraday else 0.70)
    extension = p <= ema20 + 2.2 * atr and (not intraday or (isfinite(vwap) and p >= vwap))
    volatility_floor = 0.0005 if intraday else 0.003
    viable = (isfinite(atr_ratio) and volatility_floor <= atr_ratio <= cfg.max_atr_ratio
              and isfinite(upside) and upside > 1.8 * cost and p > 0)
    checks = [
        {'key':'trend','name':'추세 방향','passed':bool(trend),
         'detail':'종가≥EMA20, EMA20>EMA50, EMA50 5봉 상승'},
        {'key':'momentum','name':'모멘텀','passed':bool(momentum),
         'detail':'RSI 51~73 및 MACD 상승 모멘텀'},
        {'key':'participation','name':'거래 참여','passed':bool(participation),
         'detail':'거래량 유효 및 20봉 기준 참여율 충족'},
        {'key':'extension','name':'과열·장중 위치','passed':bool(extension),
         'detail':'EMA20 대비 2.2ATR 이내'+(' 및 당일 VWAP 상회' if intraday else '')},
        {'key':'viable','name':'변동성·거래비용','passed':bool(viable),
         'detail':'ATR 범위 및 목표 이동폭이 왕복 비용의 1.8배 초과'},
    ]
    passed_count = sum(item['passed'] for item in checks)
    approved = bool(trend and viable and extension and passed_count >= 4)
    strict = 'HOLD' if raw == 'BUY' and not approved else raw
    failed = [x['name'] for x in checks if not x['passed']]
    if raw == 'BUY':
        note = ('사전 고정된 5개 조건 중 핵심 조건과 4개 이상 통과. 실제 적중률을 보장하지 않습니다.'
                if approved else '신규 매수 보류: ' + ', '.join(failed))
    elif raw == 'SELL':
        note = '보유분 축소 판단 유지. 엄격 필터는 신규 매수에만 적용하며 공매도 신호가 아닙니다.'
    else:
        note = '기본 신호가 관망이므로 신규 매수 후보를 생성하지 않습니다.'
    return {
        'raw_signal':raw, 'strict_signal':strict,
        'approved_buy': raw == 'BUY' and approved,
        'passed_count':passed_count, 'total_checks':len(checks),
        'checks':checks, 'failed_checks':failed,
        'reason':note, 'method':'사전 고정된 규칙형 선별기, 예측확률 또는 검증된 우월성 아님',
        'cost_basis':'편도 거래비용 '+str(cfg.cost_bps)+'bp',
    }
