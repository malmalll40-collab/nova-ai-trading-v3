"""NOVA v4.7.3 – read-only, fixed-rule 13-role vs 5-core research.

Research comparisons are retrospective, single-stock long-only illustrations.
They cannot alter the actionable signal, scan ranking, broker orders, or stored
historical observations. External KIS index history is NOT a point-in-time
publication archive and is marked as such. All index lookups use PREVIOUS
completed stock session or earlier; no as-of forward filling.
"""
from __future__ import annotations

from math import floor, isfinite
import numpy as np
import pandas as pd
from engine import Config, WARMUP, decide

HOLD = 5
COST_UNIT = 1 / 10000


def _day(x) -> str:
    stamp = pd.Timestamp(x)
    if pd.isna(stamp):
        raise ValueError('날짜 미확인')
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert('Asia/Seoul')
    return stamp.date().isoformat()


def _reference(benchmark: pd.DataFrame | None):
    if benchmark is None or benchmark.empty or 'Close' not in benchmark:
        return None, '시장지수 완료 일봉 미연결 (KIS 수집 불가)'
    try:
        values = pd.to_numeric(benchmark['Close'], errors='coerce')
        dates = [_day(x) for x in benchmark.index]
        if len(set(dates)) != len(dates) or values.isna().any() or not np.isfinite(values.to_numpy()).all() or (values<=0).any():
            raise ValueError('중복 또는 비정상 가격')
        return dict(zip(dates, values.astype(float))), None
    except (ValueError, TypeError, OverflowError):
        return None, '시장지수 과거 데이터 형식 오류'


def _core_at(f: pd.DataFrame, i: int, market: dict[str,float]) -> dict:
    """Fixed rule, no future bars: market input ends at prior completed session."""
    if i < max(WARMUP, 65):
        return {'signal':'HOLD','status':'INSUFFICIENT','checks':[]}
    dates = [_day(x) for x in f.index[i-51:i]]
    if len(set(dates)) != 51 or any(day not in market for day in dates):
        return {'signal':'HOLD','status':'UNAVAILABLE','checks':[]}
    values = np.array([market[day] for day in dates],dtype=float)
    # Conservative: the market regime and RS only see the PRIOR stock session.
    idx20 = values[-20:].mean(); idx50 = values[-50:].mean()
    idx_return20 = values[-1]/values[-21]-1
    daily_returns = np.diff(values[-21:])/values[-21:-1]-1
    idx_vol20 = float(np.std(daily_returns,ddof=0))
    r=f.iloc[i]; prev=f.iloc[i-1]
    px=float(r['Close']); atr=float(r['atr'])
    if not (px>0 and atr>0 and isfinite(atr)):
        return {'signal':'HOLD','status':'UNAVAILABLE','checks':[]}
    market_ok = values[-1]>idx20>idx50 and idx_return20>=0 and idx_vol20<=.04
    trend_ok = px>float(r['ema20'])>float(r['ema50']) and float(r['ema50'])>float(f['ema50'].iloc[i-5])
    rel_strength20 = float(r['ret20'])-idx_return20
    momentum_ok = float(r['ret20'])>0 and rel_strength20>0 and 45<=float(r['rsi'])<=78
    volume_ratio=float(r['vol_ratio'])
    breakout = px>float(r['p_high20']) and volume_ratio>=1.1
    pullback = (float(r['Low'])<=float(r['ema20'])+.45*atr and
                px>=float(r['ema20']) and px>float(prev['Close']) and volume_ratio>=.85)
    entry_ok=breakout or pullback
    volatility=atr/px
    risk_ok=(.003<=volatility<=.08 and
             float(r['Volume'])>0 and
             px<=float(r['ema20'])+2.5*atr)
    checks=[
        {'key':'market','label':'시장 국면','pass':bool(market_ok),'detail':f'전일 지수 추세·20일 일변동성 {idx_vol20:.2%}'},
        {'key':'trend','label':'종목 추세','pass':bool(trend_ok),'detail':'종가·EMA20/50·EMA50 기울기'},
        {'key':'strength','label':'상대강도','pass':bool(momentum_ok),'detail':f'종목-지수 20일 비교 {rel_strength20:+.2%} (지수는 전일)'},
        {'key':'entry','label':'진입·거래량','pass':bool(entry_ok),'detail':f'돌파={breakout} / 눌림={pullback} · 거래량 {volume_ratio:.2f}배'},
        {'key':'risk','label':'변동성 위험','pass':bool(risk_ok),'detail':f'ATR/가격 {volatility:.2%} · 추격 제한'},
    ]
    # Independently justified factor groups; risk is a mandatory veto rather than a buy vote.
    passed = sum(c['pass'] for c in checks[:4])
    buy=bool(risk_ok and market_ok and trend_ok and (momentum_ok or entry_ok) and passed>=3)
    return {'signal':'BUY' if buy else 'HOLD','status':'READY','checks':checks,
            'passing_factors':passed,'market_lag_sessions':1}


def _evaluate(f: pd.DataFrame, signals: dict[int,str], valid_indices: list[int],
              cost_bps: float, start: int, stop: int) -> dict:
    """No overlapping positions, next-session open -> fifth-session close.

    25% exposure cap, integer shares, entry/exit friction, daily marked-to-close
    portfolio equity. Other 75% remains cash. Never short SELL signals.
    """
    fee=cost_bps*COST_UNIT
    equity=10_000_000.0
    history=[equity]
    trade_rets=[]
    next_available=start
    count_signals=0
    count_skipped=0
    allowed=set(valid_indices)
    # Count BUY signal opportunities regardless of whether blocked by active position.
    count_signals=sum(signals.get(i)=='BUY' for i in valid_indices if start<=i<stop)
    for i in range(start,stop):
        if i not in allowed or signals.get(i)!='BUY':
            continue
        if i<next_available:
            count_skipped+=1
            continue
        exit_i=i+HOLD
        if exit_i>=len(f) or exit_i>=stop:
            continue
        entry=float(f['Open'].iloc[i+1]); exit_price=float(f['Close'].iloc[exit_i])
        if entry<=0 or exit_price<=0:
            continue
        qty=floor((equity*.25)/(entry*(1+fee)))
        if qty<=0:continue
        cash=equity-qty*entry*(1+fee)
        entry_spend=qty*entry*(1+fee)
        for j in range(i+1,exit_i+1):
            marked=cash+qty*float(f['Close'].iloc[j])
            if j==exit_i:
                marked-=qty*exit_price*fee
            history.append(marked)
        equity=cash+qty*exit_price*(1-fee)
        trade_rets.append((exit_price*(1-fee)/(entry*(1+fee))-1)*100)
        next_available=exit_i+1
    peak=np.maximum.accumulate(history)
    draw=np.array(history)/peak-1
    return {'buy_opportunities':int(count_signals),'completed_trades':len(trade_rets),
            'overlap_skipped':int(count_skipped),
            'win_rate_pct':round(sum(x>0 for x in trade_rets)/len(trade_rets)*100,2) if trade_rets else None,
            'mean_trade_net_pct':round(float(np.mean(trade_rets)),3) if trade_rets else None,
            'portfolio_return_pct':round((equity/10_000_000-1)*100,3),
            'portfolio_mdd_pct':round(float(np.min(draw))*100,3),
            'gross_not_modeled':['호가·세금·배당·시장충격·주문 미체결'],
            'reference':'next open / t+5 close'}


def compare(f: pd.DataFrame, cfg: Config, *, benchmark: pd.DataFrame | None,
            source: str, asof_date: str | None = None, is_demo: bool = False) -> dict:
    base={'version':'4.7.3','research_only':True,'modifies_live_signal':False,
          'market_source':'KIS 시장지수 역사 데이터 (현재 조회·과거 게시시각 미검증)',
          'data_source':source,'window_days':HOLD,
          'comparison':'기존 13역할 원신호 BUY와 신규 5-Core 연구 BUY의 동일 조건 장기포지션 비교',
          'limitations':'기존 최종 안정형·재무 필터는 과거 시점으로 재현하지 않음. 실제 추천 적중률이나 AI 예측 확률이 아님. 5거래일 장기보유 가정·최대 25% 투자·정수 주식·왕복 비용. 동일 종목·시기 내 거래는 독립 표본이 아님.'}
    if cfg.mode!='스윙' or is_demo or asof_date is not None:
        return {**base,'status':'NOT_APPLICABLE','reason':'실제 국내 주식의 현재 스윙 완료봉만 비교합니다.'}
    if f is None or len(f)<120:
        return {**base,'status':'INSUFFICIENT','reason':'완료 일봉 120개 이상 필요'}
    market, reason=_reference(benchmark)
    if market is None:
        return {**base,'status':'UNAVAILABLE','reason':reason}
    # Each included date requires 51 earlier matched market sessions, with no forward fill.
    first=max(WARMUP+2,70,len(f)-190)
    last=len(f)-HOLD
    valid=[]; baseline={}; core={};latest=None
    for i in range(first,last):
        c=_core_at(f,i,market)
        if c['status']!='READY':continue
        valid.append(i)
        baseline[i]=decide(f,cfg,i).signal
        core[i]=c['signal']
        latest=c
    if len(valid)<35:
        return {**base,'status':'INSUFFICIENT','reason':f'일치하는 시장·종목 완료 거래일 {len(valid)}건 (35건 이상 필요)',
                'eligible_sessions':len(valid)}
    cut=valid[int(len(valid)*.7)]
    head=valid[0];tail=valid[-1]
    def phase(a,b):
        inds=[i for i in valid if a<=i<b]
        return {'period_start':_day(f.index[inds[0]]) if inds else None,
                'period_end':_day(f.index[inds[-1]]) if inds else None,
                'eligible_sessions':len(inds),
                'baseline':_evaluate(f,baseline,inds,cfg.cost_bps,a,b),
                'five_core':_evaluate(f,core,inds,cfg.cost_bps,a,b)}
    # Cut is a fixed chronological split, not a tuning/parameter-fitting procedure.
    train=phase(head,cut)
    validation=phase(cut,last+1)
    # A single-stock ~190-session study is insufficient to establish a live edge.
    robust=False
    return {**base,'status':'READY','eligible_sessions':len(valid),
            'observation_span':{'from':_day(f.index[head]),'to':_day(f.index[tail])},
            'formation':train,'validation':validation,
            'latest_core':latest,
            'conclusion':'단일 종목·짧은 검증: 우수성 판정 보류',
            'comparison_validated':False,
            'data_note':'시장지수는 전일 완료봉까지만 참조. KIS 과거 가격은 시점별 게시 기록이 아니므로 미래정보 비사용을 완전히 보증하지 않음. 매매 신호와 기존 실거래 추천 미변경.'}
