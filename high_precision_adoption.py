"""Read-only adoption of NOVA high precision research (not a trading signal).

The result is designed to be displayed alongside, not in place of, the unchanged
v4.4 signal. No order, account, or promotion implementation exists here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from engine import Config
from high_precision import assess_high_precision

SHADOW = 'SHADOW_ONLY'
DISCLAIMER = ('연구 관찰 전용: 실제 과거 수익률 개선 미입증, 주문/매수 권고 아님. '
              '시장·업종·수급의 시점별 원본과 전 종목 검증이 필요합니다.')


def observe_high_precision(*, features: Any, cfg: Config, symbol: str,
                           mode: str, is_demo: bool, is_stale: bool,
                           financial_gate: str, decision_at: datetime,
                           benchmark=None, sector=None, flows=None) -> dict:
    """Fail closed and leave the baseline signal untouched.

    The historical data sources, their publication times and point-in-time
    financial disclosures must be validated separately. A research APPROVED
    pattern is not a promotion to real trading or to v4.4 main recommendations.
    """
    result = {
        'adoption_mode': SHADOW,
        'baseline_signal_modified': False,
        'live_orders_approved': False,
        'performance_advantage_verified': False,
        'state': 'NOT_EVALUATED',
        'display': '고정밀 연구 관찰: 평가 전',
        'bar_date': None,
        'pattern': None,
        'checks': [],
        'reasons': [],
        'disclaimer': DISCLAIMER,
    }
    if mode != '스윙' or not symbol.endswith(('.KS','.KQ')):
        result.update(state='OUT_OF_SCOPE', display='고정밀 연구: 국내 일봉 스윙 전용')
        return result
    if is_demo:
        result.update(state='DEMO_EXCLUDED', display='고정밀 연구: 가상 시세 제외')
        return result
    if is_stale:
        result.update(state='STALE_BLOCKED', display='고정밀 연구: 지연 시세 판단 보류')
        return result
    try:
        r = assess_high_precision(features, cfg, symbol=symbol, decision_at=decision_at,
                                  benchmark=benchmark, sector=sector, flows=flows,
                                  financial_gate=financial_gate)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        result.update(state='INPUT_NOT_READY', display='고정밀 연구: 완료봉 또는 입력 자료 확인 필요')
        return result
    # Untrusted, unvalidated public feeds cannot be promoted to main BUY.
    state = r['status']
    names = {
        'RESEARCH_APPROVED': '연구 조건 충족 · 실거래 채택 보류',
        'DATA_INCOMPLETE': '추가 데이터 필요 · 매수 판정 보류',
        'FILTERED': '고정밀 조건 미충족 · 관찰 제외',
        'ORIGINAL_QUALITY_REJECTED': '기존 안정형 미통과 · 관찰 제외',
        'ORIGINAL_SELL': '기존 매도·축소 의견 유지',
        'ORIGINAL_HOLD': '기존 관망 의견 유지',
        'REJECTED': '관찰 보류',
    }
    result.update(state=state, display=names.get(state,'고정밀 연구: 자료 확인 필요'),
                  pattern=r.get('pattern'),bar_date=r.get('bar_date'),
                  checks=r.get('checks', []),reasons=r.get('reasons', []),
                  vote_diagnostics=r.get('vote_diagnostics',{}))
    return result
