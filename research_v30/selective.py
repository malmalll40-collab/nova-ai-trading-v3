"""Offline fixed 1-day model, validation-only selection, fail closed."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
FEATURES=['ret5','ret20','relative20','vol20','turnover_log','turnover_ratio','compression','range_position','distance_high','close_location']
QUANTILES=(0.,.8,.9,.95,.98)
REGIMES=('all','up','flat','down')
def scores(q):
    if q.duplicated(['day','code']).any(): raise ValueError('DUPLICATE')
    p=q[q.safe.eq(True)&np.isfinite(q[FEATURES]).all(axis=1)].copy()
    net=100*((1+p.gross1/100)*.997/1.003-1)
    tr=p.phase.eq('development')&net.notna()
    y=net[tr].ge(3).astype(int)
    if y.nunique()!=2 or y.value_counts().min()<20: raise ValueError('INSUFFICIENT_TRAINING')
    model=LGBMClassifier(n_estimators=100,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1)
    model.fit(p.loc[tr,FEATURES],y)
    p['score']=model.predict_proba(p[FEATURES])[:,1]
    ix=p.ret20-p.relative20
    p['regime']=np.select([ix>.02,ix<-.02],['up','down'],default='flat')
    return p

def select(p,threshold,regime):
    z=p[p.score.ge(threshold)]
    if regime!='all': z=z[z.regime.eq(regime)]
    return z.sort_values(['day','score','code'],ascending=[True,False,True]).groupby('day').head(2)

def metrics(t,days,fee):
    a=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1)
    k=a.dropna()
    daily=a.fillna(-100).groupby(t.day).sum().reindex(days,fill_value=0)/2
    return dict(signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean())

def experiment(q):
    p=scores(q)
    dev=p[p.phase.eq('development')]
    val=p[p.phase.eq('validation')]
    days={s:sorted(g.day.unique()) for s,g in q.groupby('phase')}
    candidates=[]
    for regime in REGIMES:
        for quantile in QUANTILES:
            threshold=float(dev.score.quantile(quantile)) if quantile else 0.
            rows=[]
            for phase,group in [('development',dev),('validation',val)]:
                t=select(group,threshold,regime)
                for fee in (.003,.006):
                    rows.append(dict(regime=regime,quantile=quantile,threshold=threshold,phase=phase,fee_bps=round(fee*20000),**metrics(t,days[phase],fee)))
            passed=all(r['known']>=30 and r['conservative_daily']>0 and r['mean_net_pct']>0 for r in rows)
            for r in rows:r['passes_gate']=passed
            candidates.extend(rows)
    table=pd.DataFrame(candidates)
    valid=table[(table.phase=='validation')&(table.fee_bps==120)&table.passes_gate]
    if valid.empty:return table,None
    best=valid.sort_values(['conservative_daily','regime','quantile'],ascending=[False,True,True]).iloc[0]
    hold=p[p.phase.eq('reviewed_holdout')]
    t=select(hold,best.threshold,best.regime)
    extra=pd.DataFrame([dict(regime=best.regime,quantile=best.quantile,threshold=best.threshold,phase='reviewed_holdout',fee_bps=round(fee*20000),passes_gate=True,**metrics(t,days['reviewed_holdout'],fee)) for fee in (.003,.006)])
    return pd.concat([table,extra],ignore_index=True),best.to_dict()

if __name__=='__main__':
    from research_v30.dataset import load
    table,best=experiment(load(Path(sys.argv[1])))
    out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
    table.to_csv(out/'selective.csv',index=False)
    print('HOLD',best)
