"""PDE phase 1: discover shallow, interpretable tree paths, then freeze using
an earlier chronological calibration window. Offline research only.
"""
import hashlib, json, pickle, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor
H=5
FEATURES=['ret5','ret20','relative20','vol20','turnover_log','turnover_ratio','compression','range_position','distance_high','close_location','turnover_ratio5','turnover_ratio10','turnover_ratio20','rank_ret5','rank_ret5_change5','rank_relative20','rank_relative20_change5','rank_turnover_ratio','rank_turnover_ratio_change5','rank_turnover_log','rank_turnover_log_change5']
OBJECTIVES=('rise','return','risk_adjusted')
def net(gross,fee=.006):
    return 100*((1+gross/100)*(1-fee)/(1+fee)-1)
def paths(tree):
    t=tree.tree_; result={}
    def visit(i,path):
        if t.children_left[i]==t.children_right[i]:result[i]=path;return
        f=FEATURES[t.feature[i]];v=float(t.threshold[i]);visit(t.children_left[i],path+[[f,'<=',v]]);visit(t.children_right[i],path+[[f,'>',v]])
    visit(0,[]);return result

def discover(train,cal,test):
    assert train.seq.max()+H<cal.seq.min() and cal.seq.max()+H<test.seq.min()
    x=train[FEATURES];y=net(train.r5);risk=train.mae5.le(-5)
    targets={'rise':y.ge(3).astype(float),'return':y.clip(-20,20),'risk_adjusted':y.clip(-20,20)-5*risk}
    base=net(cal.r5);base_precision=base.ge(3).sum()/len(cal);base_risk=cal.mae5.le(-5).sum()/cal.r5.notna().sum()
    records=[];score=np.full(len(test),-np.inf);votes=np.zeros(len(test),dtype=int)
    for obj in OBJECTIVES:
        model=DecisionTreeRegressor(max_depth=3,min_samples_leaf=200,random_state=0).fit(x,targets[obj])
        c=model.apply(cal[FEATURES]);t=model.apply(test[FEATURES]);path=paths(model)
        for leaf,rule in path.items():
            if not rule:continue
            z=cal.iloc[np.flatnonzero(c==leaf)];k=net(z.r5).dropna();n=len(z);known=len(k)
            precision=k.ge(3).sum()/n if n else 0.;adverse=z.loc[k.index,'mae5'].le(-5).mean() if known else np.nan
            daily=net(z.r5).groupby(z.day).mean().dropna();lower=daily.mean()-daily.std()/np.sqrt(len(daily)) if len(daily)>1 else -np.inf
            accepted=bool(n>=100 and len(daily)>=20 and known/n>=.98 and lower>0 and precision>base_precision and adverse<=base_risk)
            records.append(dict(objective=obj,leaf=int(leaf),conditions=rule,cal_samples=n,cal_days=len(daily),cal_known=known,cal_mean=float(k.mean()) if known else None,cal_precision=float(precision),cal_adverse=float(adverse) if known else None,base_precision=float(base_precision),base_adverse=float(base_risk),cal_lower=float(lower) if np.isfinite(lower) else None,accepted=accepted,test_matches=int((t==leaf).sum())))
            if accepted:
                mask=t==leaf;score[mask]=np.maximum(score[mask],lower);votes[mask]+=1
    return records,score,votes

def compare(q,days):
    from research_v30.regime_portfolio import portfolio
    from research_v30.walkforward_rank import FOLDS
    if q.duplicated(['day','code']).any():raise ValueError('duplicate symbol/day')
    q=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(100).copy();q['seq']=q.day.map(dict(zip(days,range(len(days)))))
    if not np.isfinite(q[FEATURES]).all().all():
        q=q[np.isfinite(q[FEATURES]).all(axis=1)].copy()
    rows=[];rules=[]
    for start,end in FOLDS:
        cal_start=start-35
        train=q[(q.seq+H<cal_start)&q.r5.notna()].copy();cal=q[(q.seq>=cal_start)&(q.seq+H<start)].copy();test=q[(q.seq>=start)&(q.seq+H<=end)].copy()
        rec,test['pde_score'],test['votes']=discover(train,cal,test)
        for r in rec:r.update(fold_start=days[start],fold_end=days[end],train_last=days[int(train.seq.max())],cal_start=days[cal_start],cal_last=days[int(cal.seq.max())]);rules.append(r)
        for policy in ('liquidity','pde','pde_consensus','cash'):
            z=test if policy=='liquidity' else test[test.votes.ge(2 if policy=='pde_consensus' else 1)] if policy!='cash' else test.iloc[:0]
            key='turnover_log' if policy=='liquidity' else 'pde_score';t=z.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                k=net(t.r5,fee).dropna()
                for stress in (0.,1.):
                    rows.append(dict(fold_start=days[start],fold_end=days[end],policy=policy,fee_bps=round(fee*20000),unknown_multiplier=stress,eligible_days=test.day.nunique(),signal_days=t.day.nunique(),candidates=len(t),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,adverse5=t.loc[k.index,'mae5'].le(-5).mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
    return pd.DataFrame(rows),rules
if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:3]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    m,r=compare(q,days);assert len(m)==64
    out.mkdir(exist_ok=True,parents=True);m.to_csv(out/'nova_pde_replay.csv',index=False);(out/'nova_pde_patterns.json').write_text(json.dumps(r,indent=2,allow_nan=False))
    print('rules',len(r),'accepted',sum(x['accepted'] for x in r));print(m.query('fee_bps==120 and unknown_multiplier==0')[['fold_start','policy','signal_days','trades','unknown','terminal_return_pct']].to_string(index=False))
