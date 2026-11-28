"""PDE early reversal: matched static and sequence features, offline only."""
import hashlib,pickle,sys,json
from pathlib import Path
import numpy as np
import pandas as pd
H=5
FOLDS=((180,269),(270,363),(364,454),(455,545))
STATIC=tuple('ret5 ret20 relative20 vol20 turnover_log turnover_ratio compression range_position distance_high close_location turnover_ratio5 turnover_ratio10 turnover_ratio20 rank_ret5 rank_ret5_change5 rank_relative20 rank_relative20_change5 rank_turnover_ratio rank_turnover_ratio_change5 rank_turnover_log rank_turnover_log_change5'.split())
DYNAMIC=('ret20','relative20','vol20','turnover_ratio','compression','range_position')
LAGS=(20,10,5,0)
DELTA=tuple(f'{f}_d{a}_{b}' for a,b in zip(LAGS,LAGS[1:]) for f in DYNAMIC)
SEQUENCE=STATIC+DELTA+('reversal_order','reverse_order')
def net(gross,fee=.006):return 100*((1+gross/100)*(1-fee)/(1+fee)-1)
def prepare(q,days,max_rank=300):
    if q.duplicated(['day','code']).any():raise ValueError('duplicate date/code')
    p=q.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).copy()
    p['seq']=p.day.map(dict(zip(days,range(len(days)))))
    if p.seq.isna().any():raise ValueError('unmapped trading date')
    p['rank']=p.groupby('day').cumcount()+1
    history=p.set_index(['code','seq'])[list(DYNAMIC)]
    z=p[p['rank']<=max_rank].copy().reset_index(drop=True);snapshots={0:z[list(DYNAMIC)]}
    for lag in LAGS[:-1]:
        keys=pd.MultiIndex.from_arrays([z.code,z.seq-lag],names=['code','seq'])
        snapshots[lag]=history.reindex(keys).reset_index(drop=True)
    for a,b in zip(LAGS,LAGS[1:]):
        for f in DYNAMIC:z[f'{f}_d{a}_{b}']=snapshots[b][f]-snapshots[a][f]
    z['reversal_order']=(z.vol20_d20_10.lt(0)&z.turnover_ratio_d10_5.gt(0)&z.relative20_d5_0.gt(0)).astype(int)
    z['reverse_order']=(z.relative20_d20_10.gt(0)&z.turnover_ratio_d10_5.gt(0)&z.vol20_d5_0.lt(0)).astype(int)
    return z[np.isfinite(z[list(SEQUENCE)]).all(axis=1)].copy(),len(z)
def labels(q):
    n=net(q.r5);known=np.isfinite(n)&np.isfinite(q.mae5)
    return pd.Series(np.select([~known,(n>=3)&(q.mae5>-5),(n<=-5)|(q.mae5<=-5)],[-1,1,2],default=0),index=q.index)
def select(t,score):return t.sort_values(['day',score,'code'],ascending=[True,False,True]).groupby('day').head(2)
def compare(q,days):
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score,brier_score_loss
    try:
        from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError as e:
        if e.name not in ('research_v30','research_v30.regime_portfolio'):raise
        from regime_portfolio import portfolio
    z,raw=prepare(q,days);z['label']=labels(z);rows=[];metrics=[]
    print('matched rows',len(z),'before matching',raw,flush=True)
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy()
        assert tr.seq.max()+H<te.seq.min() and set(tr.label)=={0,1,2}
        for name,features in [('static',STATIC),('sequence',SEQUENCE)]:
            model=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1)
            model.fit(tr[list(features)],tr.label);proba=model.predict_proba(te[list(features)]);assert list(model.classes_)==[0,1,2]
            te[name]=proba[:,1]-proba[:,2];te[name+'_clean']=proba[:,1];te[name+'_risk']=proba[:,2]
        for scope in (100,300):
            t=te[te['rank']<=scope];k=t[t.label.ge(0)]
            for name in ('static','sequence'):
                metrics.append(dict(fold_start=days[start],scope=scope,model=name,training_rows=len(tr),test_rows=len(t),known=len(k),clean_auc=roc_auc_score(k.label.eq(1),k[name+'_clean']),risk_auc=roc_auc_score(k.label.eq(2),k[name+'_risk']),clean_brier=brier_score_loss(k.label.eq(1),k[name+'_clean']),risk_brier=brier_score_loss(k.label.eq(2),k[name+'_risk'])))
            for rule,score in [('liquidity','turnover_log'),('static','static'),('sequence','sequence')]:
                chosen=select(t,score);known=chosen[chosen.label.ge(0)]
                for fee in (.003,.006):
                    n=net(known.r5,fee);clean=n.ge(3)&known.mae5.gt(-5);adverse=n.le(-5)|known.mae5.le(-5)
                    for stress in (0.,1.):
                        rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,eligible=len(t),candidates=len(chosen),clean_precision=clean.sum()/len(chosen),adverse_rate=adverse.mean(),**portfolio(chosen,days[start:end+1],H,fee,stress)))
        print('fold complete',days[start],len(tr),len(te),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics),{'before_matching':raw,'matched':len(z),'source_rows':len(q)}
if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:3]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    m,d,meta=compare(q,days);assert len(m)==96 and len(d)==16
    out.mkdir(exist_ok=True,parents=True);m.to_csv(out/'nova_pde_sequence_replay.csv',index=False);d.to_csv(out/'nova_pde_sequence_metrics.csv',index=False);(out/'nova_pde_sequence_meta.json').write_text(json.dumps(meta,indent=2))
    print(m.query('fee_bps==120 and unknown_multiplier==0')[['fold_start','scope','rule','trades','unknown','terminal_return_pct','clean_precision','adverse_rate']].to_string(index=False));print(d.to_string(index=False))
