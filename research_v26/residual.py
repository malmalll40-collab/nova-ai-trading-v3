"""Fixed market-adjusted swing hypotheses. Not a full Fama-French replication."""
import numpy as np
import pandas as pd
RULES=('liquidity','relative20','residual20','residual_reversal5','hybrid_pullback')
def features(close,market_close):
    c=np.log(close.astype(float));m=np.log(market_close.astype(float));r=c.diff();s=m.diff()
    v=s.rolling(60).var().shift(1);beta=r.rolling(60).cov(s).shift(1)/v.where(v>1e-12)
    residual_var=r.rolling(60).var().shift(1)-beta**2*v
    sd=np.sqrt(residual_var.clip(lower=0)).where(lambda x:x>1e-6)
    e5=(c-c.shift(5)-beta*(m-m.shift(5)))/sd/np.sqrt(5)
    e20=(c-c.shift(20)-beta*(m-m.shift(20)))/sd/np.sqrt(20)
    prev15=(c.shift(5)-c.shift(20)-beta*(m.shift(5)-m.shift(20)))/sd/np.sqrt(15)
    return pd.DataFrame({'relative20_current':(c-c.shift(20))-(m-m.shift(20)),'residual20':e20,'residual5':e5,'residual15_skip5':prev15,'beta60':beta},index=close.index)
def select(q,rule):
    if rule not in RULES:raise ValueError('RULE')
    z=q[q.safe].copy()
    if rule=='liquidity':z['score']=z.turnover_log
    elif rule=='relative20':z['score']=z.relative20_current
    elif rule=='residual20':z['score']=z.residual20
    elif rule=='residual_reversal5':z=z[z.residual5.lt(0)];z['score']=-z.residual5
    else:
        z=z[z.residual15_skip5.gt(0)&z.residual5.lt(0)]
        z['score']=(z.groupby('day').residual15_skip5.rank(pct=True)+(-z.residual5).groupby(z.day).rank(pct=True))/2
    z=z[np.isfinite(z.score)]
    return z.sort_values(['day','score','code'],ascending=[True,False,True]).groupby('day',sort=False).head(2)
def evaluate(q):
    if q.duplicated(['day','code']).any():raise ValueError('DUPLICATE')
    rows=[]
    for phase,g in q.groupby('phase',sort=False):
        for rule in RULES:
            t=select(g,rule)
            for fee in (.003,.006):
                net=((1+t.net_pct/100)*(1+.003)/(1-.003)*(1-fee)/(1+fee)-1)*100
                a=net.dropna();daily=t.assign(neutral=net.fillna(0),pess=net.fillna(-100)).groupby('day')[['neutral','pess']].sum().reindex(sorted(g.day.unique()),fill_value=0)/2
                rows.append(dict(phase=phase,strategy=rule,roundtrip_bps=round(fee*20000),signals=len(t),known=len(a),unknown=len(t)-len(a),mean_net_pct=a.mean(),win_pct=100*a.gt(0).mean(),loss5_pct=100*a.le(-5).mean(),daily_slot_mean_unknown_zero=daily.neutral.mean(),daily_slot_mean_unknown_minus100=daily.pess.mean()))
    return pd.DataFrame(rows)
def gates(m):
    out={}
    for rule in RULES[1:]:
        a=m[m.strategy==rule].merge(m[m.strategy=='liquidity'],on=['phase','roundtrip_bps'],suffixes=('_new','_base'));a=a[a.phase.isin(['development','validation'])]
        checks={'positive_conservative_daily':bool((a.daily_slot_mean_unknown_minus100_new>0).all()),'better_than_liquidity_conservative_daily':bool((a.daily_slot_mean_unknown_minus100_new>a.daily_slot_mean_unknown_minus100_base).all()),'min_known_30':bool((a.known_new>=30).all()),'loss5_not_worse':bool((a.loss5_pct_new<=a.loss5_pct_base).all())};out[rule]={**checks,'pass':all(checks.values())}
    return out
