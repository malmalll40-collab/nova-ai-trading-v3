"""NOVA v3.4 — deterministic, cautious financial RISK overlay.

Current annual statements only. NEVER apply these figures to previous bars.
Weights and thresholds are unvalidated heuristics, NOT calibrated probabilities.
"""
from __future__ import annotations
import math
from typing import Any

RULES = (
    # id, displayed label, weight, threshold for positive, threshold for negative
    ('revenue_yoy_pct', '매출 성장률', 15, 8., -10.),
    ('operating_margin_pct', '영업이익률', 18, 10., 0.),
    ('net_income', '순이익 흑자 여부', 12, 0., 0.),
    ('operating_cashflow', '영업현금흐름', 18, 0., 0.),
    ('debt_ratio_pct', '부채비율', 15, 100., 200.),
    ('revenue', '매출 규모 확인', 10, 0., 0.),
    ('operating_profit_yoy_pct', '영업이익 증가율', 12, 10., -15.),
)
TOTAL_WEIGHT = sum(x[2] for x in RULES)

def _safe_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try: result = float(value)
    except (ValueError, TypeError, OverflowError): return None
    return result if math.isfinite(result) else None

def _fraction(key: str, value: float, good: float, bad: float) -> float:
    if key == 'debt_ratio_pct':
        if value < 0: return 0
        if value <= good: return 1
        if value >= bad: return 0
        return (bad - value)/(bad-good)
    if key in ('net_income','operating_cashflow','revenue'):
        return 1. if value > 0 else 0.
    if value >= good: return 1.
    if value <= bad: return 0.
    return (value - bad)/(good - bad)

def assess_financial(overview: dict) -> dict:
    """Return coverage, transparent factors, and a conservative BUY-only gate."""
    metrics = overview.get('metrics') or {}
    factors=[]
    weight_known=0
    earned=0.
    for key, label, weight, good, bad in RULES:
        v=_safe_number(metrics.get(key))
        fraction=None if v is None else _fraction(key,v,good,bad)
        if fraction is not None:
            weight_known+=weight
            earned+=fraction*weight
        factors.append({'key':key,'label':label,'weight':weight,'value':v,
                        'earned':None if fraction is None else round(fraction*weight,1),
                        'grade':'미확인' if fraction is None else ('양호' if fraction>=0.70 else '주의' if fraction<0.35 else '보통')})
    coverage=round(weight_known/TOTAL_WEIGHT*100,1)
    usable=weight_known>=70 and overview.get('status')=='ok' and bool(overview.get('annual_latest'))
    score=round(earned/weight_known*100,1) if usable else None
    risks=[]
    if (_safe_number(metrics.get('operating_profit')) is not None
        and metrics['operating_profit'] < 0): risks.append('영업이익 적자')
    if (_safe_number(metrics.get('net_income')) is not None
        and metrics['net_income'] < 0):risks.append('순이익 적자')
    if (_safe_number(metrics.get('operating_cashflow')) is not None
        and metrics['operating_cashflow'] < 0): risks.append('영업현금흐름 음수')
    ratio=_safe_number(metrics.get('debt_ratio_pct'))
    if ratio is not None and ratio>250: risks.append('부채비율 250% 초과 (업종별 차이 주의)')
    if len(risks)>=2 or (score is not None and score<=40):
        gate='BLOCK';detail='재무 위험이 중첩되어 기술적 신규 매수 후보를 보류합니다.'
    elif not usable:
        gate='UNKNOWN';detail='확인된 재무 항목이 부족해 신규 매수 차단 여부를 판단할 수 없습니다.'
    elif len(risks)==1 or (score is not None and score<55):
        gate='CAUTION';detail='재무 위험이 있어 기술적 신규 매수 후보를 관망으로 낮춥니다.'
    else:
        gate='PASS';detail='설정된 재무 위험 필터에 걸리지 않았습니다. 상승을 보장하지 않습니다.'
    return {'score':score,'coverage_pct':coverage,'as_of_annual_year':overview.get('annual_latest'),
            'statement_type':(overview.get('annual') or [{}])[0].get('fs_div'),
            'gate':gate,'gate_label':{'BLOCK':'신규 매수 보류','CAUTION':'주의·신규 매수 보류',
                                     'PASS':'위험 필터 통과','UNKNOWN':'자료 불충분'}[gate],
            'reason':detail,'risks':risks,'factors':factors,
            'method':'검증 전 휴리스틱; 예측 확률이 아님',
            'point_in_time':'현재 조회된 연간 재무제표. 과거 봉 시점에 소급 적용하지 않음'}

def combine_signal(technical: str, financial: dict, mode: str='스윙') -> dict:
    if technical not in ('BUY','SELL','HOLD'):
        raise ValueError('올바르지 않은 기술 신호')
    if mode!='스윙':
        return {'technical':technical,'combined':technical,'modified':False,'reason':'단타에는 재무 필터를 적용하지 않습니다.'}
    gate=financial.get('gate','UNKNOWN')
    if technical=='BUY' and gate in ('BLOCK','CAUTION'):
        return {'technical':'BUY','combined':'HOLD','modified':True,
                'reason':financial.get('reason','재무 경고로 신규 매수를 보류합니다.')}
    if technical=='BUY' and gate=='UNKNOWN':
        return {'technical':'BUY','combined':'BUY','modified':False,
                'reason':'재무 미확인: 기술적 매수 신호만 표시. 재무 검증을 통과한 신호가 아닙니다.'}
    if technical=='SELL':
        return {'technical':'SELL','combined':'SELL','modified':False,
                'reason':'보유분 축소 의견은 재무 필터가 취소하지 않습니다. 공매도 신호가 아닙니다.'}
    if technical=='HOLD':
        return {'technical':'HOLD','combined':'HOLD','modified':False,
                'reason':'기술적 관망을 재무 지표만으로 신규 매수로 상향하지 않습니다.'}
    return {'technical':technical,'combined':technical,'modified':False,
            'reason':'현재 재무 필터에서 신규 매수 보류 사유가 확인되지 않았습니다.'}
