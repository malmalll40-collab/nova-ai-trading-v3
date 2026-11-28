"""Offline research: fixed three-head one-day model; never issues orders."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
FEATURES=['ret5','ret20','relative20','vol20','turnover_log','turnover_ratio','compression','range_position','distance_high','close_location']
PARAMS=dict(n_estimators=100,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1)
RULES=('liquidity','core5','up_only','mean_only','joint','joint_abstain')
def fit(q):
    if q.duplicated(['day','code']).any(): raise ValueError('DUPLICATE')
    p=q[q.safe.eq(True)&np.isfinite(q[FEATURES]).all(axis=1)].copy()
    net=100*((1+p.gross1/100)*.997/1.003-1)
    tr=p.phase.eq('development')&net.notna()
    x=p.loc[tr,FEATURES]; y=net[tr]
    if len(y)<100: raise ValueError('INSUFFICIENT_TRAINING')
    for name,event in [('up',y.ge(3)),('down',y.le(-5))]:
        if event.nunique()!=2 or event.value_counts().min()<20: raise ValueError('INSUFFICIENT_CLASSES')
        m=LGBMClassifier(**PARAMS).fit(x,event.astype(int));p[name]=m.predict_proba(p[FEATURES])[:,1]
    p['mean']=LGBMRegressor(**PARAMS).fit(x,y).predict(p[FEATURES])
    p['joint']=p['mean']+3*p.up-5*p.down
    return p

def select(p,rule):
    if rule not in RULES: raise ValueError('RULE')
    z=p.copy()
    if rule=='core5': z=z[z.core5.eq(True)]
    if rule=='joint_abstain': z=z[(z['mean']>.61)&(z.up>z.down)]
    key={'liquidity':'turnover_log','core5':'turnover_log','up_only':'up','mean_only':'mean','joint':'joint','joint_abstain':'joint'}[rule]
    return z.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)

def compare(q):
    p=fit(q); rows=[]
    # Previously reviewed holdout is deliberately not used for selection or evaluation.
    for phase in ('development','validation'):
        g=p[p.phase.eq(phase)];days=sorted(q.loc[q.phase.eq(phase),'day'].unique())
        for rule in RULES:
            t=select(g,rule)
            for fee in (.003,.006):
                n=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1); k=n.dropna()
                daily=n.fillna(-100).groupby(t.day).sum().reindex(days,fill_value=0)/2
                rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean()))
    return pd.DataFrame(rows)

def gate(m):
    a=m[m.rule.isin(['mean_only','joint','joint_abstain'])].merge(m[m.rule=='up_only'],on=['phase','fee_bps'],suffixes=('','_base'),validate='many_to_one')
    a['pass_row']=(a.known>=30)&(a.conservative_daily>0)&(a.mean_net_pct>0)&(a.conservative_daily>a.conservative_daily_base)&(a.loss5<=a.loss5_base)
    return {k:bool(len(g)==4 and g.pass_row.all()) for k,g in a.groupby('rule')}

if __name__=='__main__':
    from research_v30.dataset import load
    out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
    m=compare(load(Path(sys.argv[1])));m.to_csv(out/'multiobjective.csv',index=False)
    print(gate(m));print('HOLD: independent future data required')
