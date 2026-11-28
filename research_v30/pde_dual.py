"""PDE risk-only veto on reconstructed 5-Core candidates, offline research."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
POLICIES=('core','veto_q80','veto_q90','veto_p50','cash')
def select_candidates(p,policy,thresholds):
    b=p[p.core].sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(2)
    if policy=='core':return b
    if policy=='cash':return b.iloc[:0]
    return b[np.isfinite(b.risk_score)&b.risk_score.le(thresholds[policy])]
def compare(z,days,flags,idx):
    from research_v30.pde_sequence import labels,net,FOLDS,H
    from research_v30.pde_regime import classify,COLS
    from research_v30.regime_portfolio import portfolio
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score,brier_score_loss
    z=z.merge(flags,on=['day','code'],validate='one_to_one',how='left');assert z.core.notna().all();z['label']=labels(z);z['index20']=z.day.map(idx);z['regime']=classify(z.index20)
    rows=[];calrows=[]
    for start,end in FOLDS:
        cs=start-35
        tr=z[(z.seq+H<cs)&z.core&z.label.ge(0)];cal=z[(z.seq>=cs)&(z.seq+H<start)&z.core].copy();te=z[(z.seq>=start)&(z.seq+H<=end)].copy()
        assert tr.seq.max()+H<cal.seq.min() and cal.seq.max()+H<te.seq.min()
        y=tr.label.eq(2);assert y.value_counts().min()>=100
        model=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=100,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(tr[COLS],y.astype(int))
        cal['risk_score']=model.predict_proba(cal[COLS])[:,1];te['risk_score']=model.predict_proba(te[COLS])[:,1]
        for scope in (100,300):
            p=te[te['rank']<=scope];c=select_candidates(cal[cal['rank']<=scope],'core',{});assert len(c)>=20
            thresholds={'veto_q80':float(c.risk_score.quantile(.8)),'veto_q90':float(c.risk_score.quantile(.9)),'veto_p50':.5}
            base=select_candidates(p,'core',{});k=base[base.label.ge(0)]
            calrows.append(dict(fold_start=days[start],scope=scope,training_rows=len(tr),calibration_candidates=len(c),**thresholds,eval_known=len(k),risk_auc=roc_auc_score(k.label.eq(2),k.risk_score) if k.label.eq(2).nunique()==2 else np.nan,brier=brier_score_loss(k.label.eq(2),k.risk_score)))
            for policy in POLICIES:
                t=select_candidates(p,policy,thresholds);assert set(t.index)<=set(base.index)
                for fee in (.003,.006):
                    k=t[t.label.ge(0)];n=net(k.r5,fee);clean=n.ge(3)&k.mae5.gt(-5);adverse=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,policy=policy,fee_bps=round(fee*20000),unknown_multiplier=stress,base_candidates=len(base),clean_precision=clean.sum()/len(t) if len(t) else np.nan,adverse_rate=adverse.mean(),clean_winners=int(clean.sum()),candidates=len(t),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],'train',len(tr),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(calrows)
def run(cache,root,out):
    from research_v30.pde_core_compare import reconstruct
    from research_v30.pde_sequence import prepare
    from research_v30.pde_regime import index_feature
    src=root/'NOVA_KRX_20231126_20261125.zip'
    for p,h in [(cache,'af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'),(src,'b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753')]:
        with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==h
    with cache.open('rb') as f:q,days=pickle.load(f)
    z,_=prepare(q,days);flags=reconstruct(src,root,days,z.code.unique());print('matched',len(z),'core flags',flags.core.sum(),flush=True)
    m,c=compare(z,days,flags,index_feature(root,days));assert len(m)==160 and len(c)==8
    m.to_csv(out/'nova_pde_dual_replay.csv',index=False);c.to_csv(out/'nova_pde_dual_calibration.csv',index=False)
    print(m.query('fee_bps==120').groupby(['scope','policy','unknown_multiplier'])[['terminal_return_pct','clean_precision','adverse_rate','trades','unknown','candidates','clean_winners']].mean().to_string());print(c.to_string(index=False))
if __name__=='__main__':run(*map(Path,sys.argv[1:4]))
