"""Private offline replay; run each model separately, no production writes."""
import argparse,hashlib,json,os,pickle,time
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
try:
    from research_v30.full_market import load,F,P
    from research_v30.rank_turnover import EXTRA
except ModuleNotFoundError:
    from full_market import load,F,P
    from rank_turnover import EXTRA
RULES={'liquidity':[], 'base':F, 'turnover':F+EXTRA[:3], 'rank':F+EXTRA[3:], 'both':F+EXTRA}
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def prepare(root,cache):
    q,meta,days=load(root,extended=True)
    q=q[q.phase.isin(['development','validation']) & np.isfinite(q[F+EXTRA]).all(axis=1)].copy()
    if q.duplicated(['day','code']).any():raise ValueError('DUPLICATE')
    for k in F+EXTRA:q[k]=q[k].astype('float32')
    cache.parent.mkdir(parents=True,exist_ok=True);tmp=cache.with_suffix('.tmp')
    with tmp.open('wb') as f:pickle.dump((q,meta,days),f,protocol=5)
    os.replace(tmp,cache)
    print('cached',q.groupby('phase').size().to_dict(),sha(cache),flush=True)
def fit(cache,out,rule):
    start=time.monotonic();digest=sha(cache)
    # Load only a locally generated, trusted private cache; never accept an external pickle.
    with cache.open('rb') as f:q,meta,days=pickle.load(f)
    net=100*((1+q.gross1/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();y=net[tr].ge(3).astype(int)
    if y.nunique()!=2 or y.value_counts().min()<20:raise ValueError('INSUFFICIENT_CLASSES')
    cols=RULES[rule]
    if cols:
        model=LGBMClassifier(**P).fit(q.loc[tr,cols],y);q['score']=model.predict_proba(q[cols])[:,1]
    else:q['score']=q.turnover_log
    rows=[]
    for phase,phase_days in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)];known=p[p.gross1.notna()];label=100*((1+known.gross1/100)*.997/1.003-1)>=3
        t=p.sort_values(['day','score','code'],ascending=[True,False,True]).groupby('day').head(2)
        for fee in (.003,.006):
            n=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1);k=n.dropna();daily=n.fillna(-100).groupby(t.day).sum().reindex(phase_days,fill_value=0)/2
            rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),eligible=len(p),eligible_known=len(known),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean(),auc=roc_auc_score(label,known.score) if cols else np.nan,ap=average_precision_score(label,known.score) if cols else np.nan,base_rate=label.mean(),cache_sha256=digest))
    out.mkdir(parents=True,exist_ok=True);target=out/f'{rule}.csv';tmp=target.with_suffix('.tmp');pd.DataFrame(rows).to_csv(tmp,index=False);os.replace(tmp,target);print(rule,'seconds',round(time.monotonic()-start,2),flush=True)
def combine(out):
    m=pd.concat([pd.read_csv(out/f'{r}.csv') for r in RULES],ignore_index=True)
    if len(m)!=20 or m.cache_sha256.nunique()!=1 or m.duplicated(['phase','rule','fee_bps']).any():raise ValueError('INCOMPLETE_OR_MIXED_CACHE')
    if not m.groupby(['phase','fee_bps']).eligible.nunique().eq(1).all():raise ValueError('COHORT_MISMATCH')
    if not (m.known+m.unknown).eq(m.signals).all():raise ValueError('COUNTS')
    m.to_csv(out/'comparison.csv',index=False);print(m.to_string(index=False))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','fit','combine']);p.add_argument('--root',type=Path);p.add_argument('--cache',type=Path);p.add_argument('--out',type=Path);p.add_argument('--rule',choices=RULES);a=p.parse_args()
    if a.stage=='prepare':prepare(a.root,a.cache)
    elif a.stage=='fit':fit(a.cache,a.out,a.rule)
    else:combine(a.out)
