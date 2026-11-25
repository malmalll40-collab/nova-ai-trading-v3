"""Sequential no-short backtest: decision on closed t, execution at open t+1.

For intrabar collision both stop & target -> choose stop (conservative).
One-sided friction is applied to entry and exit. No optimization/fit.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from engine import Config, WARMUP, feature_frame, decide, size_for_trade

@dataclass
class Backtest:
    curve: pd.DataFrame
    trades: pd.DataFrame
    total_return:float
    benchmark_return:float
    max_drawdown:float
    win_rate:float|None
    closed_trades:int
    directional_hit_rate:float|None
    directional_samples:int
    latest_20pct_hit_rate:float|None
    latest_20pct_samples:int
    buy_signals:int
    sell_signals:int
    signal_log:pd.DataFrame


def _accuracy(features:pd.DataFrame, cfg:Config)->tuple[float|None,int,float|None,int,pd.DataFrame,int,int]:
    h={'1분':10,'5분':6,'15분':4,'스윙':5}[cfg.mode]
    records=[]
    signals=[decide(features,cfg,i).signal for i in range(WARMUP-1,len(features))]
    for offset,sig in enumerate(signals):
        i=offset+WARMUP-1
        if sig not in ('BUY','SELL') or i+h>=len(features): continue
        entry=float(features['Open'].iloc[i+1]); close=float(features['Close'].iloc[i+h])
        if entry<=0: continue
        ret=(close/entry-1)
        net=(ret if sig=='BUY' else -ret)-2*cfg.cost_bps/10000
        hit = net>0
        records.append({'기준봉':str(features.index[i]),'방향':sig,'진입(다음봉 시가)':entry,
                        '검증종료봉':str(features.index[i+h]),'신호방향 수익률(왕복 비용 차감)':net,
                        '방향 적중':bool(hit)})
    logs=pd.DataFrame(records)
    count=len(logs)
    rate=float(logs['방향 적중'].mean()) if count else None
    cut=int(len(features)*0.8)
    last=logs.loc[logs['기준봉'].isin([str(x) for x in features.index[cut:]])] if count else logs
    last_rate=float(last['방향 적중'].mean()) if len(last)>0 else None
    return rate,count,last_rate,len(last),logs,signals.count('BUY'),signals.count('SELL')


def run_backtest(data:pd.DataFrame,cfg:Config,initial_cash:float=10_000_000)->Backtest:
    f=feature_frame(data,cfg.mode)
    if len(f)<WARMUP+12: raise ValueError('백테스트에 최소 77개의 완성봉이 필요합니다')
    cash=float(initial_cash); shares=0; purchase=0.; purchase_fee=0.
    opened_i=0; stop=target=0.0; closed=[]; equity=[]; idx=[]
    expense=cfg.cost_bps/10000
    # Fair buy&hold comparator has SAME single-stock position exposure cap.
    anchor=float(f['Open'].iloc[WARMUP])
    bench_qty=int((initial_cash*cfg.max_position_pct)//(anchor*(1+expense)))
    bench_cash=initial_cash-bench_qty*anchor*(1+expense)
    baseline=[]
    prev_sigs={i:decide(f,cfg,i).signal for i in range(WARMUP-1,len(f))}
    for i in range(WARMUP,len(f)):
        bar=f.iloc[i]
        op,hi,lo,cl=(float(bar[k]) for k in ['Open','High','Low','Close'])
        action=prev_sigs[i-1]
        # Prior close signal is the ONLY signal available before today's open.
        if shares and (action=='SELL' or i-opened_i>=cfg.max_hold_bars):
            exit_price=op
            fee=shares*exit_price*expense
            cash+=shares*exit_price-fee
            pnl=(shares*exit_price-fee)-(shares*purchase+purchase_fee)
            closed.append({'진입시점':str(f.index[opened_i]),'청산시점':str(f.index[i]),'진입가':purchase,'청산가':exit_price,
                           '수량':shares,'순손익':pnl,'수익률':pnl/(shares*purchase+purchase_fee),'청산사유':'매도 신호' if action=='SELL' else '최대 보유봉'})
            shares=0
        elif shares==0 and action=='BUY':
            last_atr=float(f.iloc[i-1]['atr'])
            distance=last_atr*cfg.stop_atr
            qty=size_for_trade(cash,cash,op,distance,cfg)
            if qty:
                fee=qty*op*expense
                cash-=qty*op+fee
                shares=qty; purchase=op; purchase_fee=fee; opened_i=i
                stop=max(0.01,op-distance)
                target=op+last_atr*cfg.target_atr
        # Stop checks are based on this bar's realized high/low, not on future bars.
        if shares:
            if op<=stop: exit_price=op;why='손절 갭'
            elif op>=target: exit_price=op;why='익절 갭'
            elif lo<=stop: exit_price=stop;why='손절(동일봉 충돌 시 우선)'
            elif hi>=target: exit_price=target;why='목표가'
            else: exit_price=None;why=''
            if exit_price is not None:
                fee=shares*exit_price*expense
                cash+=shares*exit_price-fee
                pnl=(shares*exit_price-fee)-(shares*purchase+purchase_fee)
                closed.append({'진입시점':str(f.index[opened_i]),'청산시점':str(f.index[i]),'진입가':purchase,'청산가':exit_price,
                               '수량':shares,'순손익':pnl,'수익률':pnl/(shares*purchase+purchase_fee),'청산사유':why})
                shares=0
        equity.append(cash+shares*cl)
        baseline.append(bench_cash+bench_qty*cl)
        idx.append(f.index[i])
    # Symmetric end-of-test liquidation: both the strategy and the benchmark
    # pay the one-sided exit fee at the last completed close. Never leave the
    # strategy position valued gross while charging the comparator on exit.
    if shares:
        last_close=float(f['Close'].iloc[-1])
        fee=shares*last_close*expense
        net_proceeds=shares*last_close-fee
        cash+=net_proceeds
        pnl=net_proceeds-(shares*purchase+purchase_fee)
        closed.append({'진입시점':str(f.index[opened_i]),'청산시점':str(f.index[-1]),
                       '진입가':purchase,'청산가':last_close,'수량':shares,
                       '순손익':pnl,'수익률':pnl/(shares*purchase+purchase_fee),
                       '청산사유':'검증 종료 가상청산(수수료 포함)'})
        shares=0
        equity[-1]=cash
    # The benchmark series must end at the SAME net-liquidation convention.
    baseline[-1]=bench_cash+bench_qty*float(f['Close'].iloc[-1])*(1-expense)
    curve=pd.DataFrame({'전략 자산':equity,'비교 매수보유':baseline},index=pd.Index(idx))
    tr=pd.DataFrame(closed)
    ret=equity[-1]/initial_cash-1
    # for benchmark open entry was at first analyzed-tradable bar; final liquidation at fee
    bench_terminal=bench_cash+bench_qty*float(f['Close'].iloc[-1])*(1-expense)
    bret=bench_terminal/initial_cash-1
    draw=curve['전략 자산']/curve['전략 자산'].cummax()-1
    win=float((tr['순손익']>0).mean()) if not tr.empty else None
    hit,n,holdout,n_hold,logs,buys,sells=_accuracy(f,cfg)
    return Backtest(curve,tr,float(ret),float(bret),float(draw.min()),win,len(tr),hit,n,holdout,n_hold,buys,sells,logs)
