"""Chronological recent-tail predeclared BUY-event test, not strategy PnL.

Compare raw BUYs and the strict-gate subset on identical base candidate events.
All decisions use t close; mark hypothetical entry at t+1 OPEN and outcome at
close of t+h. Cost is subtracted twice. Do not report predictive calibration.
"""
from __future__ import annotations
from math import sqrt
import pandas as pd
from engine import Config, WARMUP, decide
from signal_quality import quality_gate

HORIZON = {'1분':10,'5분':6,'15분':4,'스윙':5}


def _wilson_lower(wins: int, size: int, z: float = 1.96) -> float | None:
    if size <= 0:
        return None
    p = wins/size
    x = z*z/size
    return max(0.,(p+x/2-z*sqrt((p*(1-p)+x/4)/size))/(1+x))*100


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    hits = sum(x['net_pct']>0 for x in rows)
    return {'samples':n, 'wins':hits,
            'hit_pct':round(100*hits/n,1) if n else None,
            'mean_net_pct':round(sum(x['net_pct'] for x in rows)/n,3) if n else None,
            'wilson_95pct_lower':round(_wilson_lower(hits,n),1) if n else None}


def quality_comparison(f: pd.DataFrame, cfg: Config) -> dict:
    h = HORIZON[cfg.mode]
    # Most recent chronological 20%; events that do not yet have an outcome
    # are omitted. No fit, search, or parameter adjustment occurs here.
    start = max(WARMUP-1,int(len(f)*.80))
    event_rows=[]
    next_free = start
    for i in range(start,len(f)-h):
        if i < next_free:
            continue
        d=decide(f,cfg,i)
        if d.signal!='BUY':
            continue
        entry=float(f['Open'].iloc[i+1]);settle=float(f['Close'].iloc[i+h])
        if entry<=0 or settle<=0:
            continue
        # Candidate cooldown prevents multiple events with overlapping windows.
        next_free=i+h+1
        filt=quality_gate(f,cfg,i,raw_signal='BUY')
        net=(settle/entry-1)*100 - 2*cfg.cost_bps/100
        event_rows.append({'index':i,'net_pct':net,'accepted':filt['approved_buy']})
    raw=_summary(event_rows)
    kept=_summary([x for x in event_rows if x['accepted']])
    rejected=_summary([x for x in event_rows if not x['accepted']])
    good_sample=raw['samples']>=30 and kept['samples']>=20
    diff=(round(kept['hit_pct']-raw['hit_pct'],1)
          if kept['hit_pct'] is not None and raw['hit_pct'] is not None else None)
    return {'method':'최근 20% 시계열·사전 고정 규칙·다음 봉 시가·미래 완료봉·겹침 완화·왕복 거래비용',
            'horizon_bars':h,'region':'최근 20% 완료봉 · 튜닝용으로 사용하지 않은 비교 구간',
            'raw_buy':raw,'strict_buy':kept,'rejected':rejected,
            'coverage_pct':round(100*kept['samples']/raw['samples'],1) if raw['samples'] else None,
            'difference_hit_pct_point':diff, 'sufficient_sample':good_sample,
            'label':('비교용 표본이 확보됐지만 독립 장외검증이 필요합니다.' if good_sample
                     else '검증 표본 부족: 적중률 차이를 신뢰할 수 없습니다.'),
            'limitations':'동일 종목·기간의 과거 표본일 뿐 다른 시장에서의 우위 증거가 아닙니다. '
                          '왕복 비용은 고정 가정이며 슬리피지·세금·체결가능성은 완전 반영되지 않았습니다. '
                          '매도는 보유 축소 의견이므로 본 비교에서 제외합니다.'}
