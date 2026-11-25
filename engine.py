"""NOVA v2: 13 transparent rule-based roles, closed-candle indicators only.

No predictive model is trained; vote counts are correlated, NOT probabilities.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

WARMUP = 65

@dataclass(frozen=True)
class Config:
    mode: str = "스윙"  # '1분', '5분', '15분', '스윙'
    min_agree: int = 6
    min_margin: int = 3
    cost_bps: float = 15.0  # per side, includes estimated friction
    risk_pct: float = 0.01
    max_position_pct: float = 0.25
    stop_atr: float = 1.6
    target_atr: float = 2.6
    max_atr_ratio: float = 0.08
    max_hold_bars: int = 20

    @staticmethod
    def for_mode(mode: str, cost_bps: float = 15.0) -> "Config":
        if mode == '1분':
            return Config(mode=mode, cost_bps=cost_bps, stop_atr=1.7, target_atr=2.5, max_atr_ratio=0.045, max_hold_bars=60)
        if mode == '5분':
            return Config(mode=mode, cost_bps=cost_bps, stop_atr=1.8, target_atr=2.8, max_atr_ratio=0.06, max_hold_bars=30)
        if mode == '15분':
            # Initial conservative defaults, not optimized/validated against live fills.
            return Config(mode=mode, cost_bps=cost_bps, stop_atr=1.9, target_atr=2.9, max_atr_ratio=0.075, max_hold_bars=20)
        if mode == '스윙':
            return Config(mode=mode, cost_bps=cost_bps, stop_atr=2.0, target_atr=3.0, max_atr_ratio=0.10, max_hold_bars=20)
        raise ValueError("지원하지 않는 분석 모드입니다")

@dataclass(frozen=True)
class Vote:
    role: str
    direction: int
    reason: str
    category: str = "가격·거래량 분석"

    @property
    def label(self) -> str:
        return {1: '매수', -1: '매도', 0: '관망'}[self.direction]

@dataclass
class Decision:
    signal: str
    buy_votes: int
    sell_votes: int
    hold_votes: int
    risk_veto: bool
    risk_message: str
    votes: list[Vote]
    time: str
    reference: float
    atr: float
    stop: float | None
    target: float | None
    risk_reward: float | None
    rsi: float


def clean_bars(data: pd.DataFrame) -> pd.DataFrame:
    required = ['Open','High','Low','Close','Volume']
    if data is None or data.empty:
        raise ValueError('가격 데이터가 없습니다')
    d = data.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    if not all(x in d.columns for x in required):
        raise ValueError('OHLCV 필수 열이 누락되었습니다')
    d = d[required].copy()
    for k in required:
        d[k] = pd.to_numeric(d[k], errors='coerce')
    d = d.replace([np.inf,-np.inf], np.nan).dropna()
    d = d.loc[~d.index.duplicated(keep='last')].sort_index()
    valid = (d[required[:4]]>0).all(axis=1) & (d['Volume']>=0)
    valid &= d['High']>=d[['Open','Close','Low']].max(axis=1)
    valid &= d['Low']<=d[['Open','Close']].min(axis=1)
    return d.loc[valid]


def feature_frame(data: pd.DataFrame, mode: str) -> pd.DataFrame:
    d = clean_bars(data)
    if len(d)<WARMUP:
        raise ValueError(f'{mode} 분석은 완료된 유효 봉 {WARMUP}개 이상 필요합니다. 현재 {len(d)}개입니다.')
    c,h,l,v = (d[k].astype(float) for k in ['Close','High','Low','Volume'])
    f = d.copy()
    f['ema12'] = c.ewm(span=12,adjust=False).mean()
    f['ema26'] = c.ewm(span=26,adjust=False).mean()
    f['ema20'] = c.ewm(span=20,adjust=False).mean()
    f['ema50'] = c.ewm(span=50,adjust=False).mean()
    f['macd'] = f['ema12']-f['ema26']
    f['macd_s'] = f['macd'].ewm(span=9,adjust=False).mean()
    diff = c.diff()
    g = diff.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = g / loss.replace(0,np.nan)
    f['rsi'] = (100-100/(1+rs)).where(rs.notna(),pd.Series(np.where(g>0,100,50),index=d.index))
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    f['atr'] = tr.rolling(14,min_periods=14).mean()
    f['p_high20'] = h.shift(1).rolling(20).max()
    f['p_low20'] = l.shift(1).rolling(20).min()
    f['ret5'] = c.pct_change(5)
    f['ret20'] = c.pct_change(20)
    f['ret5prev'] = c.shift(5)/c.shift(10)-1
    f['vol_ratio'] = v / v.shift(1).rolling(20).mean().replace(0,np.nan)
    f['ret1'] = c.pct_change()
    sma = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    f['bb_hi'] = sma+2*std
    f['bb_lo'] = sma-2*std
    f['hi50'] = h.shift(1).rolling(50).max()
    f['lo50'] = l.shift(1).rolling(50).min()
    # Do not let cumulative VWAP mix trading sessions or span days.
    if mode != '스윙':
        daily_group = pd.Series(d.index.date,index=d.index)
        amount = ((h+l+c)/3*v)
        cumulative_amount = amount.groupby(daily_group).cumsum()
        cumulative_vol = v.groupby(daily_group).cumsum().replace(0,np.nan)
        f['vwap'] = cumulative_amount/cumulative_vol
    else:
        f['vwap'] = np.nan
    return f


def _comp(v: float, limit: float=0.0) -> int:
    if not math.isfinite(float(v)): return 0
    return int(v>limit)-int(v< -limit)


def vote_row(r: pd.Series, mode: str, cfg: Config) -> tuple[str,list[Vote],bool,str]:
    p = float(r['Close'])
    intraday = mode != '스윙'
    threshold = 0.0008 if intraday else 0.003
    mom5 = 0.002 if intraday else 0.012
    mom20 = 0.005 if intraday else 0.035
    votes: list[Vote] = []
    def add(role:str,direction:int, reason:str, category:str="가격·거래량 분석"):
        votes.append(Vote(role,direction,reason,category))
    add('01 단기 추세',_comp(r['ema12']/r['ema26']-1,threshold),f"EMA12 {r['ema12']:,.2f} / EMA26 {r['ema26']:,.2f}")
    add('02 중기 추세',_comp(r['ema20']/r['ema50']-1,threshold*1.6),f"EMA20 {r['ema20']:,.2f} / EMA50 {r['ema50']:,.2f}")
    add('03 MACD',_comp((r['macd']-r['macd_s'])/p,threshold*.30),f"MACD {r['macd']:.3f} / 시그널 {r['macd_s']:.3f}")
    rsi = float(r['rsi'])
    d = 1 if 53<=rsi<=73 else -1 if rsi<45 or rsi>82 else 0
    add('04 RSI',d,f"RSI(14) {rsi:.1f}, 과열·약세 주의")
    add('05 이전 20봉 돌파',int(p>r['p_high20'])-int(p<r['p_low20']),f"직전 20봉 고점 {r['p_high20']:,.2f} / 저점 {r['p_low20']:,.2f}")
    add('06 단기 모멘텀',_comp(r['ret5'],mom5),f"5봉 등락 {r['ret5']:+.2%}")
    add('07 중기 모멘텀',_comp(r['ret20'],mom20),f"20봉 등락 {r['ret20']:+.2%}")
    vol_dir = _comp(r['ret1'],0.0005 if intraday else 0.002) if r['vol_ratio']>1.4 else 0
    add('08 거래량·방향',vol_dir,f"직전 20봉 평균 대비 {r['vol_ratio']:.2f}배, 현재봉 변동 {r['ret1']:+.2%}")
    add('09 볼린저 역추세',int(p<r['bb_lo'])-int(p>r['bb_hi']),f"밴드 [{r['bb_lo']:,.2f}, {r['bb_hi']:,.2f}]")
    if intraday:
        d = _comp(p/float(r['vwap'])-1,threshold) if pd.notna(r['vwap']) else 0
        add('10 장중 VWAP',d,f"당일 거래량 가중 평균가 {r['vwap']:,.2f}")
    else:
        add('10 50봉 채널',int(p>r['hi50'])-int(p<r['lo50']),f"직전 50봉 채널 [{r['lo50']:,.2f}, {r['hi50']:,.2f}]")
    buys = sum(v.direction==1 for v in votes)
    sells = sum(v.direction==-1 for v in votes)
    add('11 강세 검토',1 if buys>=4 and buys>sells else 0,f"기존 10개 역할: 강세 {buys}, 약세 {sells}", '찬반 검토')
    add('12 약세 검토',-1 if sells>=4 and sells>buys else 0,f"기존 10개 역할: 약세 {sells}, 강세 {buys}", '찬반 검토')
    # 13th role: risk supervisor, veto not counted as an ordinary sell signal
    vol = float(r['atr'])/p
    veto_reasons=[]
    if not np.isfinite(vol) or vol<=0: veto_reasons.append('ATR 산출 불가')
    elif vol>cfg.max_atr_ratio: veto_reasons.append(f"ATR/가격 {vol:.1%} > 허용 {cfg.max_atr_ratio:.1%}")
    if intraday and float(r['Volume'])<=0: veto_reasons.append('마지막 봉 거래량 0')
    veto=bool(veto_reasons)
    note=' / '.join(veto_reasons) if veto else f"ATR/가격 {vol:.2%}, 신규 매수 차단 조건 없음"
    add('13 위험관리 책임자',-1 if veto else 0,note,'위험관리')
    # 12 voting roles include 10 technical and opposing bullish/bearish reviewers.
    count=votes[:-1]
    bulls=sum(v.direction==1 for v in count)
    bears=sum(v.direction==-1 for v in count)
    if bears>=cfg.min_agree and bears-bulls>=cfg.min_margin: signal='SELL'
    elif (not veto) and bulls>=cfg.min_agree and bulls-bears>=cfg.min_margin: signal='BUY'
    else: signal='HOLD'
    return signal,votes,veto,note


def decide(features: pd.DataFrame, cfg:Config, i:int=-1) -> Decision:
    if len(features)<WARMUP: raise ValueError('봉 데이터 부족')
    i = (len(features)+i) if i<0 else i
    if i<WARMUP-1 or i>=len(features): raise ValueError('지표 준비 기간 범위 밖입니다')
    r=features.iloc[i]
    signal,votes,veto,note=vote_row(r,cfg.mode,cfg)
    active=votes[:-1]
    buy=sum(v.direction==1 for v in active)
    sell=sum(v.direction==-1 for v in active)
    hold=sum(v.direction==0 for v in active)
    px=float(r['Close'])
    atr=float(r['atr'])
    if atr>0 and np.isfinite(atr):
        stop=max(0.01,px-cfg.stop_atr*atr)
        target=px+cfg.target_atr*atr
        rr=cfg.target_atr/cfg.stop_atr
    else:
        stop=target=rr=None
    return Decision(signal,buy,sell,hold,veto,note,votes,str(features.index[i]),px,atr,stop,target,rr,float(r['rsi']))


def size_for_trade(equity:float, cash:float, entry:float, stop_distance:float, cfg:Config)->int:
    if min(equity,cash,entry,stop_distance)<=0:return 0
    by_risk=int(equity*cfg.risk_pct // stop_distance)
    by_exposure=int(equity*cfg.max_position_pct // entry)
    by_cash=int(cash//(entry*(1+cfg.cost_bps/10000)))
    return max(0,min(by_risk,by_exposure,by_cash))
