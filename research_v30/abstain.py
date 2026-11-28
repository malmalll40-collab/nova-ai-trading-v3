"""Fixed abstention policies on already reviewed chronological folds; no live use."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
try:
    from research_v30.full_market import F,P
    from research_v30.regime_portfolio import portfolio
    from research_v30.walkforward_rank import FOLDS
except ModuleNotFoundError:
    from full_market import F,P
    from regime_portfolio import portfolio
    from walkforward_rank import FOLDS
H=5
RULES=('liquidity','liquidity_veto','mean','mean_veto','early_veto','cash')
def select(p,rule):
    key='turnover_log' if rule.startswith('liquidity') else 'mean'
    eligible=p['mean'].gt(.5)&p.risk.le(.25)
    early=p.ret5.le(.08)&p.ret20.le(.25)&p.turnover_ratio.ge(1.2)&p.compression.lt(1)
    if rule=='cash':return p.iloc[:0]
    # Liquidity veto does not substitute a rejected top-two candidate.
    if rule=='liquidity_veto':
        t=p.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
        return t[eligible.loc[t.index]]
    if rule=='mean_veto':p=p[eligible]
    if rule=='early_veto':p=p[eligible&early]
    return p.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
def compare(q,days):
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    q=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(100).copy()
    q['seq']=q.day.map(dict(zip(days,range(len(days)))))
    rows=[]
    for start,end in FOLDS:
        tr=q[(q.seq+H<start)&q.r5.notna()].copy();te=q[(q.seq>=start)&(q.seq+H<=end)].copy()
        assert tr.seq.max()+H<te.seq.min()
        net=100*((1+tr.r5/100)*.994/1.006-1);risk=tr.mae5.le(-5)
        assert risk.value_counts().min()>=20
        te['mean']=LGBMRegressor(**P).fit(tr[F],net).predict(te[F]);te['risk']=LGBMClassifier(**P).fit(tr[F],risk.astype(int)).predict_proba(te[F])[:,1]
        for rule in RULES:
            t=select(te,rule);assert not t.groupby('day').size().gt(2).any()
            for fee in (.003,.006):
                n=100*((1+t.r5/100)*(1-fee)/(1+fee)-1);k=n.dropna()
                for stress in (0.,1.):
                    rows.append(dict(fold_start=days[start],fold_end=days[end],rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),signal_days=t.day.nunique(),eligible_days=te.day.nunique(),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,adverse5=t.loc[k.index,'mae5'].le(-5).mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold complete',days[start],flush=True)
    return pd.DataFrame(rows)
if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:3]);assert hashlib.sha256(cache.read_bytes()).hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    m=compare(q,days);assert len(m)==96 and not m.duplicated(['fold_start','rule','fee_bps','unknown_multiplier']).any();m.to_csv(out,index=False)
    print(m.query('fee_bps==120 and unknown_multiplier==0')[['fold_start','rule','candidates','trades','unknown','terminal_return_pct']].to_string(index=False))
