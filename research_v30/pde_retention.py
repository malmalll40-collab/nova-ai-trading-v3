"""Earlier-window risk veto with winner-retention constraint; research only."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
POLICIES=('core','lgbm','logistic','ensemble')
QUANTILES=(.75,.80,.85,.90,.95)
def base_candidates(p):
    return p[p.core].sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(2)
def gate(cal,score):
    known=cal[cal.label.ge(0)];wins=known.label.eq(1);risk=known.label.eq(2)
    default=dict(active=False,threshold=None,reason='insufficient_calibration',cal_candidates=len(cal),cal_known=len(known),cal_days=known.day.nunique(),cal_winners=int(wins.sum()),cal_adverse=int(risk.sum()),cal_retention=1.,cal_risk_reduction=0.)
    if len(known)<20 or known.day.nunique()<10 or wins.sum()<8 or len(known)/len(cal)<.95 or not np.isfinite(cal[score]).all():return default
    default['reason']='no_safe_threshold';passing=[]
    for q in QUANTILES:
        t=float(cal[score].quantile(q));keep=known[known[score]<=t]
        retention=keep.label.eq(1).sum()/wins.sum();reduction=float(risk.mean()-keep.label.eq(2).mean()) if len(keep) else -1.
        if retention>=.85 and cal[score].le(t).mean()>=.75 and reduction>=.03:
            passing.append(dict(default,active=True,threshold=t,reason='pass',cal_retention=float(retention),cal_risk_reduction=reduction))
    return max(passing,key=lambda r:(r['cal_risk_reduction'],r['cal_retention'],r['threshold'])) if passing else default
def apply_gate(base,score,g):
    if not g['active']:return base
    return base[np.isfinite(base[score])&base[score].le(g['threshold'])]
def compare(z,days,flags,idx):
    from research_v30.pde_sequence import labels,net,FOLDS,H
    from research_v30.pde_regime import classify,COLS
    from research_v30.regime_portfolio import portfolio
    from lightgbm import LGBMClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score,brier_score_loss
    z=z.merge(flags,on=['day','code'],validate='one_to_one',how='left');assert z.core.notna().all();z['label']=labels(z);z['index20']=z.day.map(idx);z['regime']=classify(z.index20)
    rows=[];calrows=[]
    for start,end in FOLDS:
        cs=start-60
        tr=z[(z.seq+H<cs)&z.core&z.label.ge(0)];cal=z[(z.seq>=cs)&(z.seq+H<start)&z.core].copy();te=z[(z.seq>=start)&(z.seq+H<=end)].copy()
        assert (tr.seq+H<cs).all() and (cal.seq+H<start).all() and (te.seq>=start).all()
        y=tr.label.eq(2);trained=y.nunique()==2 and y.value_counts().min()>=100
        for d in (cal,te):
            for s in POLICIES[1:]:d[s]=np.nan
        if trained:
            models={'lgbm':LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=100,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1),'logistic':make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=1000,random_state=0))}
            for name,model in models.items():
                model.fit(tr[COLS],y.astype(int))
                for d in (cal,te):
                    if len(d):d[name]=model.predict_proba(d[COLS])[:,1]
            for d in (cal,te):d['ensemble']=(d.lgbm+d.logistic)/2
        for scope in (100,300):
            b=base_candidates(te[te['rank']<=scope]);c=base_candidates(cal[cal['rank']<=scope]);k=b[b.label.ge(0)]
            for policy in POLICIES:
                g=gate(c,policy) if policy!='core' else dict(active=False,threshold=None,reason='baseline')
                t=apply_gate(b,policy,g);assert set(t.index)<=set(b.index)
                if policy!='core':
                    valid=trained and len(k)>0 and k.label.eq(2).nunique()==2
                    calrows.append(dict(fold_start=days[start],scope=scope,policy=policy,train_rows=len(tr),trained=trained,**g,risk_auc=roc_auc_score(k.label.eq(2),k[policy]) if valid else np.nan,brier=brier_score_loss(k.label.eq(2),k[policy]) if valid else np.nan))
                for fee in (.003,.006):
                    known=t[t.label.ge(0)];n=net(known.r5,fee);clean=n.ge(3)&known.mae5.gt(-5);adverse=n.le(-5)|known.mae5.le(-5)
                    bn=net(k.r5,fee);bw=int((bn.ge(3)&k.mae5.gt(-5)).sum())
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,policy=policy,fee_bps=round(fee*20000),unknown_multiplier=stress,active=g['active'],base_candidates=len(b),base_winners=bw,clean_winners=int(clean.sum()),winner_retention=clean.sum()/bw if bw else np.nan,candidates=len(t),clean_precision=clean.sum()/len(t) if len(t) else np.nan,adverse_rate=adverse.mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],len(tr),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(calrows)
def run(cache,root,out):
    from research_v30.pde_core_compare import reconstruct
    from research_v30.pde_sequence import prepare
    from research_v30.pde_regime import index_feature
    src=root/'NOVA_KRX_20231126_20261125.zip'
    for p,h in [(cache,'af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'),(src,'b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753')]:
        with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==h
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    z,_=prepare(q,days);flags=reconstruct(src,root,days,z.code.unique());print('matched',len(z),'core flags',flags.core.sum(),flush=True)
    m,c=compare(z,days,flags,index_feature(root,days));assert len(m)==128 and len(c)==24
    m.to_csv(out/'nova_pde_retention_replay.csv',index=False);c.to_csv(out/'nova_pde_retention_calibration.csv',index=False)
    print(m.query('fee_bps==120 and scope==100').groupby(['policy','unknown_multiplier'])[['terminal_return_pct','clean_winners','candidates','trades','adverse_rate']].mean().to_string());print(c.query('scope==100').to_string(index=False))
if __name__=='__main__':run(*map(Path,sys.argv[1:4]))
