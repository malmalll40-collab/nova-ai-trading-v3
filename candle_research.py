"""v4.7.4 — retrospective candle-by-candle rule diagnostics (READ ONLY).

This is NOT a point-in-time archive of actual published recommendations, and
it cannot alter live trading signals or Neon observations. Every observation
uses data available at that historical completed bar; the outcome is later.
"""
from __future__ import annotations

import math
from collections import defaultdict
import pandas as pd
from engine import Config, WARMUP, decide
from core_comparison import _day, _core_at, _reference

HORIZON = 5
MAX_CANDLES = 125


def _num(v, ndigits=3):
    try:
        x = float(v)
        return round(x, ndigits) if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def build_candle_research(features: pd.DataFrame, cfg: Config, *, benchmark: pd.DataFrame | None,
                          source: str, is_demo: bool = False, asof_date: str | None = None) -> dict:
    base = {
        'version': '4.7.4', 'research_only': True, 'modifies_live_signal': False,
        'symbol_data_source': source, 'horizon_sessions': HORIZON,
        'candle_timeframe': 'analysis', 'observations': [], 'role_statistics': [],
        'disclaimer': '과거 완료봉으로 재계산한 가상 연구 결과이며 당시 실제 발송된 추천 기록이 아닙니다. SELL은 보유축소 의견으로 공매도 수익을 뜻하지 않습니다. 종목이 바뀌면 데이터도 초기화됩니다.'
    }
    if cfg.mode != '스윙' or is_demo or asof_date is not None:
        return {**base, 'status': 'NOT_APPLICABLE', 'reason': '현재 국내 주식 스윙 완료 일봉 전용 연구입니다.'}
    if features is None or len(features) < WARMUP + 6:
        return {**base, 'status': 'INSUFFICIENT', 'reason': '과거 완료 일봉 데이터가 부족합니다.'}
    market, market_reason = _reference(benchmark)
    role_bins = defaultdict(lambda: {'label':'', 'kind':'independent', 'buy':0, 'sell':0, 'hold':0,
                                     'evaluated':0, 'correct':0, 'aligned_move_sum':0.0})
    rows = []
    start=max(WARMUP, len(features)-MAX_CANDLES)
    fee=cfg.cost_bps/10000
    for i in range(start, len(features)):
        d=decide(features,cfg,i)
        day=_day(features.index[i])
        status = 'PENDING' if i+HORIZON >= len(features) else 'EVALUATED'
        next_open=exit_close=move=net_buy=None
        if status == 'EVALUATED':
            next_open=_num(features['Open'].iloc[i+1]);exit_close=_num(features['Close'].iloc[i+HORIZON])
            if next_open is None or exit_close is None or next_open<=0 or exit_close<=0:
                status='UNAVAILABLE'
            else:
                move=(exit_close/next_open-1)*100
                net_buy=(exit_close*(1-fee)/(next_open*(1+fee))-1)*100
        r={
            'date': day, 'completed': True, 'price_close': _num(features['Close'].iloc[i]),
            'baseline': d.signal, 'buy_votes': d.buy_votes, 'sell_votes': d.sell_votes,
            'hold_votes': d.hold_votes, 'risk_veto': bool(d.risk_veto),
            'risk_message': d.risk_message,
            'outcome_status': status,
            'next_open': next_open, 'exit_close_t5': exit_close,
            'price_change_pct_t5': _num(move),
            'hypothetical_buy_net_pct_t5': _num(net_buy),
            'vote_details': []
        }
        if market is not None:
            core = _core_at(features,i,market)
            r['five_core'] = core['signal'] if core['status']=='READY' else None
            r['five_core_status'] = core['status']
            r['five_core_checks'] = core.get('checks',[])
        else:
            r['five_core']=None;r['five_core_status']='UNAVAILABLE';r['five_core_checks']=[]
        for idx,v in enumerate(d.votes):
            role_id=idx+1
            kind='risk_veto' if role_id==13 else 'derived' if role_id in (11,12) else 'independent'
            b=role_bins[role_id]
            b['label']=v.role;b['kind']=kind
            direction='BUY' if v.direction==1 else 'SELL' if v.direction==-1 else 'HOLD'
            if direction=='BUY':b['buy']+=1
            elif direction=='SELL':b['sell']+=1
            else:b['hold']+=1
            r['vote_details'].append({'role':v.role,'direction':direction,'kind':kind,'reason':v.reason})
            # 13 is a veto, 11/12 are derived from other votes; not independent models.
            # Historical directional hit does NOT denote an actual trade execution.
            if role_id<=12 and direction!='HOLD' and status=='EVALUATED':
                b['evaluated']+=1
                aligned=move if direction=='BUY' else -move
                b['correct']+=int(aligned>0)
                b['aligned_move_sum']+=aligned
        rows.append(r)
    stats=[]
    for idx in sorted(role_bins):
        b=role_bins[idx];n=b['evaluated']
        stats.append({'role': b['label'],'kind':b['kind'],'buy_count':b['buy'],
                      'sell_count':b['sell'],'hold_count':b['hold'],
                      'directional_samples':n,
                      'directional_hit_pct':round(b['correct']/n*100,1) if n>=10 else None,
                      'mean_aligned_price_change_pct':round(b['aligned_move_sum']/n,3) if n>=10 else None,
                      'sufficient_for_display':n>=10})
    return {**base,'status':'READY','observations':rows,'role_statistics':stats,
            'observation_count':len(rows), 'market_reference_status':'READY' if market else 'UNAVAILABLE',
            'market_reference_note': market_reason or '5-Core는 과거 일자별 전일 지수 완료봉 일치를 요구합니다.',
            'evaluation_note':'각 날짜의 13개 원신호와 5-Core 연구 신호를 분리 표시합니다. 방향 적중률은 다음 거래일 시가 → 5번째 거래일 종가의 상승/하락 부호 평가이며, SELL에 공매도 이익을 가정하지 않습니다. 날짜 간 5거래일 결과가 겹치므로 독립 표본이 아닙니다.'}
