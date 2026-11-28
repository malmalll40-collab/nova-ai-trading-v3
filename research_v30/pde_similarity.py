"""Historical nearest-neighbor indicator snapshots; research only."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import roc_auc_score
FOLDS=((180,269),(270,363),(364,454),(455,545));H=5
FEATURES=('ret5','ret20','relative20','vol20','turnover_ratio','compression','range_position')
LAGS=(0,5,10,15,20,30,40,50,60)
def cols(lags):return [f'{f}_lag{lag}' for lag in lags for f in FEATURES]
def net(x,fee=.006):return 100*((1+x/100)*(1-fee)/(1+fee)-1)
def prepare(q,days):
    if q.duplicated(['day','code']).any():raise ValueError('duplicate date/code')
    p=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).copy();p['seq']=p.day.map(dict(zip(days,range(len(days)))))
    if p.seq.isna().any():raise ValueError('unmapped date')
    p['rank']=p.groupby('day').cumcount()+1
    hist=p.set_index(['code','seq'])[list(FEATURES)];z=p[p['rank']<=300].copy().reset_index(drop=True)
    for lag in LAGS:
        keys=pd.MultiIndex.from_arrays([z.code,z.seq-lag],names=['code','seq'])
        z[cols((lag,))]=hist.reindex(keys).to_numpy()
    return z[np.isfinite(z[cols(LAGS)]).all(axis=1)].copy(),len(z)
def labels(z):
    n=net(z.r5);a=z.mae5;known=np.isfinite(n)&np.isfinite(a)
    return np.select([~known,(n>=3)&(a>-5),(n<=-5)|(a<=-5)],[-1,1,2],default=0)
def analogs(tr,te,features,k=50):
    if len(tr)<k or tr.seq.max()+H>=te.seq.min() or (tr.label<0).any():raise ValueError('insufficient or immature analog history')
    scaler=RobustScaler().fit(tr[features]);x=np.clip(scaler.transform(tr[features]),-8,8).astype('float32');y=np.clip(scaler.transform(te[features]),-8,8).astype('float32')
    model=NearestNeighbors(n_neighbors=k,algorithm='brute',metric='euclidean',n_jobs=2).fit(x)
    dist,ix=model.kneighbors(y);lab=tr.label.to_numpy()[ix]
    return (lab==1).mean(axis=1),(lab==2).mean(axis=1),dist.mean(axis=1)/np.sqrt(len(features))
def compare(z,days):
    try:from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError as e:
        if e.name not in ('research_v30','research_v30.regime_portfolio'):raise
        from regime_portfolio import portfolio
    z=z.copy();z['label']=labels(z);rows=[];metrics=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy()
        tr=tr.sample(n=min(10000,len(tr)),random_state=0).sort_values(['seq','code'])
        for name,lags in [('static',(0,)),('path20',LAGS[:5]),('path60',LAGS)]:
            te[name+'_rise'],te[name+'_risk'],te[name+'_dist']=analogs(tr,te,cols(lags));te[name+'_joint']=te[name+'_rise']-te[name+'_risk']
        for scope in (100,300):
            t=te[te['rank']<=scope];k=t[t.label.ge(0)]
            for name in ('static','path20','path60'):
                metrics.append(dict(fold_start=days[start],scope=scope,model=name,train_rows=len(tr),eval_rows=len(t),known=len(k),clean_auc=roc_auc_score(k.label.eq(1),k[name+'_rise']),risk_auc=roc_auc_score(k.label.eq(2),k[name+'_risk']),mean_distance=t[name+'_dist'].mean()))
            for rule in ('liquidity','static_rise','static_joint','path20_rise','path20_joint','path60_rise','path60_joint'):
                score='turnover_log' if rule=='liquidity' else rule
                c=t.sort_values(['day',score,'code'],ascending=[True,False,True]).groupby('day').head(2);known=c[c.label.ge(0)]
                for fee in (.003,.006):
                    n=net(known.r5,fee);clean=n.ge(3)&known.mae5.gt(-5);adverse=n.le(-5)|known.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(c),clean_precision=clean.sum()/len(c),adverse_rate=adverse.mean(),**portfolio(c,days[start:end+1],H,fee,stress)))
        print('fold',days[start],len(tr),len(te),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics)
def run(cache,out):
    with cache.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    z,raw=prepare(q,days);print('matched',len(z),'before',raw,'symbols',z.code.nunique(),flush=True)
    a,b=compare(z,days);assert len(a)==224 and len(b)==24 and not a.duplicated(['fold_start','scope','rule','fee_bps','unknown_multiplier']).any()
    a.to_csv(out/'nova_pde_similarity_replay.csv',index=False);b.to_csv(out/'nova_pde_similarity_metrics.csv',index=False)
    print(a.query('fee_bps==120 and scope==300').groupby(['rule','unknown_multiplier'])[['terminal_return_pct','clean_precision','adverse_rate','trades','unknown']].mean().to_string());print(b.query('scope==300').groupby('model')[['clean_auc','risk_auc']].mean().to_string(),flush=True)
if __name__=='__main__':run(*map(Path,sys.argv[1:3]))
