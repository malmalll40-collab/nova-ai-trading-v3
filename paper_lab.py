"""Prospective observation-only, conservative single-position paper RESEARCH.

Not broker fills, not a strategy backtest of all recommended candidates.
Only database-stamped live BUY observations are used (no imports or historical
backfills). Entries are at the next observed trading day's OPEN strictly after
the actual observation date, exit at the 5th available session CLOSE. No stops,
partial fills or liquidity constraints; no orders.
"""
from __future__ import annotations
from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo('Asia/Seoul')


def simulate_paper(snapshots: list[dict], loader, *, capital: float = 10_000_000,
                   hold_days: int = 5, cost_bps: int = 15,
                   slippage_bps: int = 5, max_pairs: int = 4, now: datetime | None = None) -> dict:
    if not 1 <= hold_days <= 20 or not 0 <= cost_bps <= 100 or not 0 <= slippage_bps <= 100:
        raise ValueError('가상투자 파라미터 범위 오류')
    if not 100_000 <= capital <= 10**12:
        raise ValueError('가상투자 원금 범위 오류')
    rows = [dict(x) for x in snapshots if x.get('mode') == '스윙' and x.get('technical') == 'BUY' and x.get('filtered') == 'BUY' and x.get('provenance') == 'server_analysis_request']
    rows.sort(key=lambda x: (x.get('observed_at') or '', x.get('id') or ''))
    selected_pairs = []
    for row in rows:
        pair = (row['symbol'],row['source'])
        if pair not in selected_pairs and len(selected_pairs) < max_pairs:
            selected_pairs.append(pair)
    included = [x for x in rows if (x['symbol'],x['source']) in selected_pairs]
    insufficient_scope = len(rows)-len(included)
    frames, errors = {}, {}
    for pair in selected_pairs:
        try:
            f=loader(*pair)
            if not isinstance(f,pd.DataFrame) or not {'Open','Close'} <= set(f.columns):
                raise ValueError('OHLC 누락')
            bars=[]
            for t, val in f.iterrows():
                day=pd.Timestamp(t).date()
                opening=float(val['Open']); closing=float(val['Close'])
                if isfinite(opening) and isfinite(closing) and opening>0 and closing>0:
                    bars.append((day, opening, closing))
            # Last market day is not assumed finished until following KST day.
            today=(now or datetime.now(KST)).astimezone(KST).date()
            frames[pair]=sorted([b for b in bars if b[0]<today],key=lambda b:b[0])
        except Exception:
            errors[pair] = '검증 가능한 완료 일봉이 부족합니다.'
    balance=float(capital)
    equity=[{'date':'START','equity':round(balance)}]
    skipped=0
    pending=0
    trades=[]
    last_exit=None
    for rec in included:
        pair=(rec['symbol'],rec['source'])
        if pair in errors:
            skipped+=1;continue
        try:
            observed=datetime.fromisoformat(rec['observed_at'])
            if observed.tzinfo is None: raise ValueError('timezone required')
            observed_day=observed.astimezone(KST).date()
            signal_day=pd.Timestamp(rec['bar_day']).date()
        except Exception:
            skipped+=1;continue
        bars=frames[pair]
        anchor=[b for b in bars if b[0]==signal_day]
        if not anchor or abs(anchor[-1][2]/float(rec['reference'])-1) > .05:
            skipped+=1;continue
        candidate=[b for b in bars if b[0]>max(observed_day,signal_day)]
        if not candidate:
            pending+=1;continue
        if last_exit is not None and candidate[0][0] <= last_exit:
            skipped+=1;continue
        if len(candidate)<hold_days:
            pending+=1;continue
        entry=candidate[0];exitbar=candidate[hold_days-1]
        entry_px=entry[1]*(1+slippage_bps/10000)
        exit_px=exitbar[2]*(1-slippage_bps/10000)
        net_factor=exit_px*(1-cost_bps/10000)/(entry_px*(1+cost_bps/10000))
        net_pct=(net_factor-1)*100
        balance*=net_factor
        trades.append({'symbol':rec['symbol'],'source':rec['source'],
                       'observed_at':rec['observed_at'],
                       'entry_day':entry[0].isoformat(),'exit_day':exitbar[0].isoformat(),
                       'entry_open':round(entry[1],6),'exit_close':round(exitbar[2],6),
                       'net_pct':round(net_pct,4),'equity_after':round(balance)})
        equity.append({'date':exitbar[0].isoformat(),'equity':round(balance)})
        last_exit=exitbar[0]
    peak=capital
    realized_mdd=0.0
    for row in equity:
        v=row['equity'];peak=max(peak,v);realized_mdd=min(realized_mdd,(v/peak-1)*100)
    return {
        'study':'OBSERVED_BUY_SINGLE_POSITION_PAPER', 'initial_capital':capital,
        'final_equity':round(balance), 'cumulative_net_pct':round((balance/capital-1)*100,3),
        'trades_count':len(trades), 'wins_count':sum(t['net_pct']>0 for t in trades),
        'win_rate_pct':round(100*sum(t['net_pct']>0 for t in trades)/len(trades),1) if trades else None,
        'realized_exit_only_mdd_pct':round(realized_mdd,3),
        'candidate_buy_observations':len(rows), 'scope_excluded':insufficient_scope,
        'skipped':skipped,'pending':pending,'trades':trades,'equity':equity,
        'hold_days':hold_days,'cost_bps_per_side':cost_bps,'slippage_bps_per_side':slippage_bps,
        'limitations':'서버에서 실제 관찰한 매수 신호에 한정된 연구용 가상 계산입니다. 미래 정보는 진입 결정에 미사용; 실제 체결·세금·배당·시장 충격·장중 평가·손절·매도 신호·거래정지는 반영하지 않음. 기록된 관찰 종목의 선택 편향이 존재하며 기간 수익률·실전 성능을 입증하지 않습니다. 낙폭은 청산일만 평가하여 실제 MDD보다 작을 수 있습니다.'
    }
