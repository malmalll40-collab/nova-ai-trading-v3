"""Frozen research hypotheses and causal, shrunk conditional frequency estimates.
No order or production signal integration. Parameters are not optimized on returns.
"""
from __future__ import annotations
import math

VERSION='5.5.0'
NAMES={'range_guard':'횡보 돌파 방어','early_turn':'상승 초기 전환','regime_switch':'국면별 통합'}
MIN_TRAIN=40
MIN_GROUP=10
PRIOR_WEIGHT=30


def candidate_flags(*,base_buy,regime,market_regime,market_vol,checks,close,ema20,ema50,atr,volume_ratio,rsi,adx,previous_adx,directional,slope,previous_slope,breakout,rel_strength):
    """All inputs are as of the signal close (benchmark ends one day earlier)."""
    vals=(market_vol,close,ema20,ema50,atr,volume_ratio,rsi,adx,previous_adx,slope,previous_slope,rel_strength)
    if not all(math.isfinite(float(v)) for v in vals) or not checks['risk'] or market_regime=='down' or regime=='down' or market_vol>.04:
        return dict.fromkeys(NAMES,False)
    range_entry=bool(regime=='range' and directional and slope>0 and breakout and adx>=20 and adx>previous_adx and rel_strength>0 and 45<=rsi<=72)
    # Early turn may precede EMA50 alignment, but requires recent slope reversal,
    # positive relative strength, confirmed volume breakout and existing risk veto.
    turn=bool(regime=='range' and directional and previous_slope<=0<slope and close>=ema20 and close>=ema50-.5*atr and volume_ratio>=1.5 and breakout and adx>=18 and adx>previous_adx and rel_strength>0 and 45<=rsi<=72)
    trend=bool(base_buy and regime in ('strong_up','mild_up'))
    return {'range_guard':trend or range_entry,'early_turn':bool(base_buy or turn),'regime_switch':trend or range_entry or turn}


def change_metrics(labels,baseline,candidate):
    added=[i for i in labels if baseline.get(i)!='BUY' and candidate.get(i)=='BUY']
    removed=[i for i in labels if baseline.get(i)=='BUY' and candidate.get(i)!='BUY']
    return {'added_positive':sum(bool(labels[i]) for i in added),'added_negative':sum(not labels[i] for i in added),
            'blocked_positive':sum(bool(labels[i]) for i in removed),'blocked_negative':sum(not labels[i] for i in removed)}


def probability(train,group):
    """train: iterable of (group, binary outcome), already settled strictly earlier."""
    train=list(train)
    if len(train)<MIN_TRAIN:return None
    members=[y for g,y in train if g==group]
    if len(members)<MIN_GROUP:return None
    prior=(sum(y for _,y in train)+1)/(len(train)+2)
    conditional=(sum(members)+PRIOR_WEIGHT*prior)/(len(members)+PRIOR_WEIGHT)
    return {'candidate':round(conditional,8),'naive':round(prior,8),'train_count':len(train),'group_count':len(members)}


def group(point):
    return f"{point['regime']}|{point['signals']['core']}"


def score(items):
    items=list(items)
    if not items:return {'status':'INSUFFICIENT','samples':0,'candidate_brier':None,'naive_brier':None}
    return {'status':'READY','samples':len(items),**{k+'_brier':round(sum((p[k]-y)**2 for p,y in items)/len(items),6) for k in ('candidate','naive')}}


def historical_probability(points,rows,horizon):
    pairs=[]
    for i,p in sorted(points.items()):
        pred=probability(((group(points[j]),rows[j][1]) for j in points if j+horizon<i),group(p))
        if pred:pairs.append((pred,rows[i][1]))
    return {**score(pairs),'research_only':True,'independent_future':False,'method':'고정 국면·Core 조건부 빈도 수축 / 이전 확정 결과만 학습'}
