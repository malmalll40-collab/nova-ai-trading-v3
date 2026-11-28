"""Offline volatility bias and excess-return research. Benchmark: equal-weight resolved eligible cohort, not KRX index. Trusted local cache only; no orders."""
import sys,pickle,hashlib
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
try:
    from research_v30.full_market import F,P
except ModuleNotFoundError:
    from full_market import F,P
RULES=('liquidity','up','excess','risk_scaled')
def fit(q):
    if q.duplicated(['day','code']).any() or not q.phase.isin(['development','validation']).all():raise ValueError('COHORT')
    q=q.copy();gross=q.gross1.where(np.isfinite(q.gross1));net=100*((1+gross/100)*.997/1.003-1)
    tr=q.phase.eq('development')&net.notna();x=q.loc[tr,F];y=net[tr].ge(3)
    if y.nunique()!=2 or y.value_counts().min()<20:raise ValueError('CLASSES')
    g=gross[tr];ex=g-g.groupby(q.loc[tr,'day']).transform('mean')
    scale=(100*q.vol20).clip(lower=0.5)
    q['up']=LGBMClassifier(**P).fit(x,y.astype(int)).predict_proba(q[F])[:,1]
    q['excess']=LGBMRegressor(**P).fit(x,ex).predict(q[F])
    q['risk_scaled']=LGBMRegressor(**P).fit(x,ex/scale[tr]).predict(q[F])
    q['liquidity']=q.turnover_log
    return q

def evaluate(q,days):
    q=q.copy();q['vol_rank']=q.vol20.groupby(q.day).rank(pct=True)
    q['benchmark']=q.gross1.groupby(q.day).transform('mean');q['excess_actual']=q.gross1-q.benchmark
    rows=[]
    for phase,calendar in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)];known=p[p.gross1.notna()];label=100*((1+known.gross1/100)*.997/1.003-1)>=3
        for rule in RULES:
            t=p.sort_values(['day',rule,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                n=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1);k=n.dropna();daily=n.fillna(-100).groupby(t.day).sum().reindex(calendar,fill_value=0)/2
                rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t),mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean(),vol20_mean_pct=t.vol20.mean()*100,top_vol_quintile=t.vol_rank.gt(.8).mean(),mean_gross_excess_pct=t.excess_actual.mean(),auc=roc_auc_score(label,known[rule]),ap=average_precision_score(label,known[rule])))
    return pd.DataFrame(rows)
if __name__=='__main__':
    cache=Path(sys.argv[1]);out=Path(sys.argv[2]);
    with cache.open('rb') as f:print('cache_sha256',hashlib.file_digest(f,'sha256').hexdigest(),flush=True)
    with cache.open('rb') as f:q,meta,days=pickle.load(f)
    print('cohort',len(q),flush=True);m=evaluate(fit(q),days);assert len(m)==16 and (m.known+m.unknown).eq(m.signals).all();m.to_csv(out,index=False);print(m.to_string(index=False),flush=True)
