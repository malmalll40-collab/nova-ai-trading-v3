"""PDE diagnostic: missed clean winners relative to a named liquidity proxy.
Not actual NOVA signal history, not a trained or deployable strategy.
"""
import hashlib
import pickle
import sys
from pathlib import Path
import numpy as np
import pandas as pd

FOLDS=((180,269),(270,363),(364,454),(455,545))
H=5
FEATURES=('ret5','ret20','relative20','vol20','turnover_ratio','compression','range_position','distance_high','close_location','turnover_ratio20')
LAGS=(0,5,10,20)

def label(q):
    net=100*((1+q.r5/100)*.994/1.006-1)
    known=np.isfinite(net)&np.isfinite(q.mae5)
    return np.select([~known,(net>=3)&(q.mae5>-5),net>=3,(net<=-5)|(q.mae5<=-5)],['unknown','clean_win','risky_win','adverse'],default='other')

def classify(q,days):
    if q.duplicated(['day','code']).any():raise ValueError('duplicate date/code')
    p=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).copy()
    p['seq']=p.day.map(dict(zip(days,range(len(days)))))
    if p.seq.isna().any():raise ValueError('unmapped dates')
    p['rank']=p.groupby('day').cumcount()+1
    p['bucket']=np.select([p['rank']<=2,p['rank']<=100],['picked_proxy','missed_inside100'],default='outside100')
    p['outcome']=label(p)
    return p

def audit(q,days):
    p=classify(q,days);history=p.set_index(['code','seq'])[list(FEATURES)].sort_index()
    counts=[]; contrasts=[]
    for start,end in FOLDS:
        a=p[(p.seq>=start)&(p.seq+H<=end)].copy()
        for bucket,b in a.groupby('bucket'):
            c=b.outcome.value_counts();counts.append(dict(fold_start=days[start],fold_end=days[end],bucket=bucket,rows=len(b),days=b.day.nunique(),**{k:int(c.get(k,0)) for k in ('unknown','clean_win','risky_win','adverse','other')}))
        for lag in LAGS:
            keys=pd.MultiIndex.from_arrays([a.code,a.seq-lag],names=['code','seq'])
            x=history.reindex(keys).reset_index(drop=True)
            x=x.where(np.isfinite(x));x['day']=a.day.to_numpy();x['bucket']=a.bucket.to_numpy();x['outcome']=a.outcome.to_numpy()
            for bucket in ('picked_proxy','missed_inside100','outside100'):
                b=x[x.bucket.eq(bucket)&x.outcome.isin(['clean_win','adverse'])]
                win=b[b.outcome.eq('clean_win')].groupby('day')[list(FEATURES)].median()
                fail=b[b.outcome.eq('adverse')].groupby('day')[list(FEATURES)].median()
                delta=win-fail
                for f in FEATURES:
                    d=delta[f].dropna();w=b[b.outcome.eq('clean_win')][f];v=b[b.outcome.eq('adverse')][f]
                    contrasts.append(dict(fold_start=days[start],fold_end=days[end],bucket=bucket,lag=lag,feature=f,paired_days=len(d),win_available=int(w.notna().sum()),adverse_available=int(v.notna().sum()),mean_daily_median_difference=float(d.mean()),positive_day_fraction=float(d.gt(0).mean())))
    c=pd.DataFrame(counts);assert (c[['unknown','clean_win','risky_win','adverse','other']].sum(axis=1)==c.rows).all()
    return c,pd.DataFrame(contrasts)

if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:3]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    c,d=audit(q,days);out.mkdir(parents=True,exist_ok=True);c.to_csv(out/'nova_pde_miss_counts.csv',index=False);d.to_csv(out/'nova_pde_miss_contrasts.csv',index=False)
    print(c.to_string(index=False));print('totals');print(c.groupby('bucket')[['rows','unknown','clean_win','risky_win','adverse','other']].sum().to_string())
    print('missed inside 100: signal day comparisons');print(d.query("bucket=='missed_inside100' and lag==0").pivot(index='feature',columns='fold_start',values='mean_daily_median_difference').to_string())
