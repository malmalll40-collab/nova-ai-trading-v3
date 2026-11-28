"""PDE 6, retrospective fixed-regime specialists only."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
from research_v30.pde_sequence import prepare,labels,SEQUENCE,FOLDS,H,net,select
REGIMES=('up','flat','down');COLS=list(SEQUENCE)+['index20']
def classify(x):
    if not np.isfinite(x).all():raise ValueError('missing market return')
    return np.select([x>.02,x<-.02],['up','down'],default='flat')
def index_feature(root,days):
    series=[]
    for names in [('data_0200_20261127.xlsx',),('data_0622_20261127.xlsx','data_2426_20261127.xlsx')]:
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();p=a['종가'].shift(1);series.append(p/p.shift(20)-1)
    return (series[0]+series[1])/2
def eligible(t):return t.day.nunique()>=20 and set(t.label)=={0,1,2} and t.label.value_counts().min()>=100
def fit(tr,te):
    from lightgbm import LGBMClassifier
    m=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(tr[COLS],tr.label);assert list(m.classes_)==[0,1,2];return m.predict_proba(te[COLS])
def compare(q,days,idx):
    from research_v30.regime_portfolio import portfolio
    z,_=prepare(q,days);z['label']=labels(z);z['index20']=z.day.map(idx);z['regime']=classify(z.index20);rows=[];coverage=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy();assert tr.seq.max()+H<te.seq.min() and eligible(tr)
        p=fit(tr,te);s=p.copy()
        for r in REGIMES:
            a=tr[tr.regime.eq(r)];mask=te.regime.eq(r).to_numpy();ok=eligible(a);coverage.append(dict(fold_start=days[start],regime=r,train_days=a.day.nunique(),train_rows=len(a),test_days=te.loc[mask,'day'].nunique(),test_rows=int(mask.sum()),specialist=bool(ok)))
            if ok and mask.any():s[mask]=fit(a,te.loc[mask])
        te['global']=p[:,1]-p[:,2];te['specialist']=s[:,1]-s[:,2];te['blend']=(te['global']+te.specialist)/2
        for scope in (100,300):
            t=te[te['rank']<=scope]
            for rule in ('liquidity','global','specialist','blend'):
                c=select(t,'turnover_log' if rule=='liquidity' else rule);k=c[c.label.ge(0)]
                for fee in (.003,.006):
                    n=net(k.r5,fee);clean=n.ge(3)&k.mae5.gt(-5);adverse=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(c),known=len(k),clean_precision=clean.sum()/len(c),adverse_rate=adverse.mean(),**portfolio(c,days[start:end+1],H,fee,stress)))
        print('fold',days[start],len(tr),len(te),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(coverage)
if __name__=='__main__':
    cache,root,out=map(Path,sys.argv[1:4]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    m,c=compare(q,days,index_feature(root,days));assert len(m)==128 and len(c)==12
    m.to_csv(out/'nova_pde_regime_replay.csv',index=False);c.to_csv(out/'nova_pde_regime_coverage.csv',index=False)
    print(m.query('fee_bps==120').groupby(['scope','rule','unknown_multiplier'])[['terminal_return_pct','clean_precision','adverse_rate','trades','unknown']].mean().to_string());print(c.to_string(index=False))
