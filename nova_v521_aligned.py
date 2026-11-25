"""v5.2.1: paired, read-only exit-policy comparison on the same BUY decisions.

Both variants use identical signal dates, entry eligibility, 25% of cash,
integer shares, entry/exit fees and a five-session maximum holding interval.
The risk variant may exit early after a stop/target or completed SELL signal.
This does NOT reproduce the separately published full-history legacy backtest.
"""
from __future__ import annotations
import math
import numpy as np
from core_comparison import _day
from nova_v5_research import _finite, INITIAL_CASH


def simulate_paired(f, signals, start: int, stop: int, cost_bps: float,
                    *, mode: str, stop_atr: float, target_atr: float) -> dict:
    if mode not in ('fixed_five', 'risk_exit'):
        raise ValueError('unsupported comparison mode')
    end = min(len(f), stop)
    cash = float(INITIAL_CASH)
    shares = 0; curve = [cash]; returns = []; ledger = []
    fee = cost_bps / 10000.0
    entry_price = 0.0; entry_cost = 0.0
    entry_i = -1; signal_i = -1; stop_level = 0.; target_level = 0.
    opportunities = 0; skipped = 0
    for i in range(start, end):
        op, hi, lo, close = [_finite(f[k].iloc[i]) for k in ('Open','High','Low','Close')]
        if any(v is None or v<=0 for v in (op, hi, lo, close)) or lo>min(op,close) or hi<max(op,close):
            return {'status':'UNAVAILABLE','reason':'검증 구간 OHLC 미확인 또는 비정상'}
        prev_sig = signals.get(i-1) if i>start else None
        if prev_sig=='BUY':
            opportunities += 1
            if shares:skipped+=1
        if shares==0 and prev_sig=='BUY' and i+4 < end:
            prev_atr = _finite(f['atr'].iloc[i-1])
            if prev_atr is None or prev_atr <= 0:
                return {'status':'UNAVAILABLE','reason':'신호일 ATR 미확인'}
            candidate = int(math.floor(cash*0.25/(op*(1+fee))))
            if candidate > 0:
                shares=candidate; entry_price=op; entry_cost=candidate*op*(1+fee)
                cash-=entry_cost; entry_i=i; signal_i=i-1
                stop_level=max(0.01, op-prev_atr*stop_atr)
                target_level=op+prev_atr*target_atr
        exit_price = None; why = None
        if shares:
            # First use the previous completed candle's SELL recommendation.
            # Execution at this day's open does not use today's close.
            if mode=='risk_exit' and i>entry_i and prev_sig=='SELL':
                exit_price=op;why='이전 완료봉 매도 신호 → 시가 청산'
            # Conservative OHLC collision ordering; a gap is executed at open.
            if mode=='risk_exit' and exit_price is None:
                if op<=stop_level:exit_price=op;why='손절 갭'
                elif op>=target_level:exit_price=op;why='목표가 갭'
                elif lo<=stop_level:exit_price=stop_level;why='손절 우선'
                elif hi>=target_level:exit_price=target_level;why='목표가'
            if exit_price is None and i-entry_i==4:
                exit_price=close;why='진입일 포함 5번째 거래일 종가'
            if exit_price is not None:
                proceeds=shares*exit_price*(1-fee)
                cash+=proceeds
                ret=100*(proceeds/entry_cost-1)
                returns.append(ret)
                ledger.append({'signal_date':_day(f.index[signal_i]),'entry_date':_day(f.index[entry_i]),
                               'exit_date':_day(f.index[i]),'net_trade_return_pct':round(ret,3),
                               'exit_reason':why,'actual_fill':False})
                shares=0
        curve.append(cash+shares*close)
    if shares:
        return {'status':'UNAVAILABLE','reason':'검증 종료 시 미청산 포지션'}
    a=np.asarray(curve,dtype=float)
    drawdown=np.min(a/np.maximum.accumulate(a)-1)*100
    count=len(returns)
    return {'status':'READY','completed_trades':count,'win_rate_pct':round(100*sum(x>0 for x in returns)/count,2) if count else None,
            'portfolio_return_pct':round((cash/INITIAL_CASH-1)*100,3),'portfolio_mdd_pct':round(float(drawdown),3),
            'performance_assessable':bool(count),'buy_opportunities':opportunities,'overlap_skipped':skipped,
            'trade_ledger':ledger[:60], 'cost_bps_per_side':cost_bps,'position_cap_pct':25}


def compare_paired_exit_rules(f, signals, start, stop, cfg):
    base = {'status':'INSUFFICIENT','research_only':True,'modifies_live_signal':False,
            'same_signal_decisions':True,'same_price_source':True,'same_validation_dates':True,
            'same_entry_conditions':True,'same_cost_and_position_cap':True,
            'analysis_is_prospective':False,'results_are_live_order_performance':False,
            'comparison_name':'동일 신호·기간·비용: 5일 고정보유 vs 위험 조기청산',
            'important':'원래의 장기 전체기간 백테스트와 동일한 계산이 아닙니다. 검증 기간만의 통제된 연구 비교입니다.'}
    if stop-start<8:
        return {**base,'reason':'검증 구간 짧음'}
    common = dict(stop_atr=cfg.stop_atr,target_atr=cfg.target_atr)
    fixed=simulate_paired(f,signals,start,stop,cfg.cost_bps,mode='fixed_five',**common)
    risk=simulate_paired(f,signals,start,stop,cfg.cost_bps,mode='risk_exit',**common)
    if fixed.get('status')!='READY' or risk.get('status')!='READY':
        return {**base,'status':'UNAVAILABLE','reason':fixed.get('reason') or risk.get('reason') or '가격 자료 확인 실패'}
    assessable=bool(fixed['completed_trades'] and risk['completed_trades'])
    return {**base,'status':'READY','validation_start':_day(f.index[start]),'validation_end':_day(f.index[stop-1]),
            'cost_bps_per_side':cfg.cost_bps, 'holding_max_sessions':5,
            'fixed_five':fixed,'risk_exit':risk,
            'portfolio_return_difference_pp':round(risk['portfolio_return_pct']-fixed['portfolio_return_pct'],3) if assessable else None,
            'assessment':'추가 검증 필요 · 거래 수 부족' if min(fixed['completed_trades'],risk['completed_trades'])<20 else '기존 연구 구간 내 탐색적 비교',
            'outcome_known_at_signal':False,
            'limitations':'동일 원신호·자료·검증일·25% 투자비중·편도 거래비용으로 청산 규칙만 비교. 바 내부 손절/목표 순서는 보수적으로 추정. 실제 체결·세금·시장충격·재무 필터는 재현 불가.'}
