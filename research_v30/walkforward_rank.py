"""Expanding historical walk-forward: fixed five-day horizon and top100 liquidity universe.
Not independent prospective validation. No order or production paths.
"""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMRanker,LGBMRegressor
try:
    from research_v30.full_market import F,P
    from research_v30.regime_portfolio import portfolio
except ModuleNotFoundError:
    from full_market import F,P
    from regime_portfolio import portfolio
FOLDS=((180,269),(270,363),(364,454),(455,545))
H=5
RULES=('liquidity','momentum','excess','ranker')
def compare(q,days):
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    assert set(q.phase)=={'development','validation'}
    q=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(100).copy()
    q['seq']=q.day.map(dict(zip(days,range(len(days)))))
    assert q.seq.notna().all()
    rows=[]
    for start,end in FOLDS:
        tr=q[(q.seq+H<start)&q.r5.notna()].sort_values(['day','code']).copy()
        te=q[(q.seq>=start)&(q.seq+H<=end)].copy()
        assert len(tr)>1000 and len(te)>1000 and tr.seq.max()+H<te.seq.min()
        net=100*((1+tr.r5/100)*.997/1.003-1)
        target=net-net.groupby(tr.day).transform('mean')
        relevance=np.minimum(4,np.floor(net.groupby(tr.day).rank(pct=True)*5)).astype(int)
        groups=tr.groupby('day',sort=False).size().to_numpy()
        model=LGBMRegressor(**P).fit(tr[F],target)
        te['excess']=model.predict(te[F])
        ranker=LGBMRanker(**P).fit(tr[F],relevance,group=groups)
        te['ranker']=ranker.predict(te[F]);te['liquidity']=te.turnover_log;te['momentum']=te.relative20
        for rule in RULES:
            t=te.sort_values(['day',rule,'code'],ascending=[True,False,True]).groupby('day').head(2)
            assert t.groupby('day').size().max()<=2
            for fee in (.003,.006):
                n=100*((1+t.r5/100)*(1-fee)/(1+fee)-1);k=n.dropna()
                for stress in (0.,1.):
                    rows.append(dict(fold_start=days[start],fold_end=days[end],training_last_signal=days[int(tr.seq.max())],training_rows=len(tr),test_rows=len(te),rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),precision=k.ge(3).sum()/len(t),mean_net_pct=k.mean(),adverse5=t.loc[k.index,'mae5'].le(-5).mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],days[end],'train',len(tr),'test',len(te),flush=True)
    return pd.DataFrame(rows)
if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:3])
    with cache.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    # Only load locally generated trusted cache.
    with cache.open('rb') as f:q,days=pickle.load(f)
    m=compare(q,days);assert len(m)==64 and not m.duplicated(['fold_start','rule','fee_bps','unknown_multiplier']).any();m.to_csv(out,index=False)
    print(m.query('fee_bps==60 and unknown_multiplier==0')[['fold_start','fold_end','rule','trades','unknown','terminal_return_pct','mean_net_pct']].to_string(index=False),flush=True)
