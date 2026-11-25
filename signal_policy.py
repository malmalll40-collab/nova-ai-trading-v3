"""One final, read-only signal decision for analysis and watchlist.

Never turn a stale/unknown domestic financial BUY into an actionable recommendation.
This is a policy gate, not a forecast probability.
"""

from __future__ import annotations

SIGNALS = {'BUY', 'SELL', 'HOLD'}
FINANCE_GATES = {'PASS', 'CAUTION', 'BLOCK', 'UNKNOWN', 'NOT_APPLIED', 'NOT_SUPPORTED'}


def resolve_final_signal(technical: str, *, mode: str, symbol: str,
                         financial_gate: str = 'NOT_APPLIED',
                         finance_reason: str = '', stale: bool = False,
                         is_demo: bool = False) -> dict:
    if technical not in SIGNALS:
        raise ValueError('알 수 없는 기술적 신호입니다.')
    domestic_swing = mode == '스윙' and symbol.endswith(('.KS', '.KQ'))
    gate = financial_gate if financial_gate in FINANCE_GATES else 'UNKNOWN'
    final = technical
    reason = '안정형 기술 신호 기준. 예측 확률이나 매매 보장이 아닙니다.'
    if domestic_swing and technical == 'BUY':
        if gate in ('CAUTION', 'BLOCK'):
            final = 'HOLD'
            reason = finance_reason or '재무 위험으로 신규 매수를 보류합니다.'
        elif gate != 'PASS':
            final = 'HOLD'
            reason = '재무자료 미확인: 확인되지 않은 스윙 신규 매수는 보류합니다.'
        else:
            reason = '안정형 기술 조건과 재무 위험 필터 통과. 상승을 보장하지 않습니다.'
    elif domestic_swing and technical == 'SELL':
        reason = '보유분 축소 검토. 공매도 진입 의견은 아닙니다.'
    elif domestic_swing and technical == 'HOLD':
        reason = '기술적 관망은 재무자료만으로 매수로 상향하지 않습니다.'
    elif mode != '스윙':
        reason = '단타는 기술 조건을 사용하며 재무 필터는 적용하지 않습니다.'
    elif not domestic_swing:
        reason = 'OpenDART 미지원 종목: 기술적 신호만 사용하며 재무 검증을 받지 않았습니다.'

    historical_signal = final
    current = not stale and not is_demo
    if stale:
        final = 'HOLD'
        reason = '시세가 오래되었거나 완료봉 시각을 확인할 수 없어 신규 매매 판단을 보류합니다.'
    return {
        'technical_signal': technical,
        'historical_signal': historical_signal,
        'final_signal': final,
        'final_actionable_signal': final if current else None,
        'signal_is_current': current,
        'financial_gate': gate,
        'final_reason': reason,
    }
