"""Offline cross-sectional leadership rotation, not investor flow."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
try:
    from research_v30.pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE
except ModuleNotFoundError:
    from pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE
EXTRA=['leader_turnover_share','new_leader_share','breadth','breadth_delta5','leader_turnover_share_delta5','new_leader']
def rotation(q,days):
    p=q[['day','code','ret5','rank_relative20','turnover_log']].copy()
    if p.duplicated(['day','code']).any():raise ValueError('duplicate')
    p['seq']=p.day.map(dict(zip(days,range(len(days)))))
    if p.seq.isna().any():raise ValueError('unknown date')
    past=p[['code','seq','rank_relative20']].copy();past.seq+=5
    p=p.merge(past,on=['code','seq'],how='left',suffixes=('','_past'),validate='one_to_one')
    p['leader']=p.rank_relative20.ge(.8);p['new_leader']=np.where(p.rank_relative20_past.notna(),p.leader & p.rank_relative20_past.lt(.8),np.nan)
    p['positive']=p.ret5.gt(0);p['weight']=np.expm1(p.turnover_log.astype(float));p['leader_weight']=p.weight*p.leader
    agg=p.groupby('seq').agg(breadth=('positive','mean'),leader_weight=('leader_weight','sum'),weight=('weight','sum'),new_leader_share=('new_leader','mean'));agg['leader_turnover_share']=agg.leader_weight/agg.weight
    for f in ('breadth','leader_turnover_share'):agg[f+'_delta5']=agg[f]-agg[f].reindex(agg.index-5).set_axis(agg.index)
    return p.merge(agg,on='seq',validate='many_to_one')[['day','code',*EXTRA]]
def choose(p,rule):
    def top(x,k):return x.sort_values(['day',k,'code'],ascending=[True,False,True]).groupby('day').head(2)
    b=top(p[p.core],'turnover_log')
    if rule=='core':return b
    if rule=='liquidity':return top(p,'turnover_log')
    if rule in ('static','rotation'):return top(p,rule)
    score={'fill_liquidity':'turnover_log','fill_static':'static','fill_rotation':'rotation'}[rule]
    x=top(p[~p.core],score).copy();x['slot']=x.groupby('day').cumcount();x=x[x.slot<2-x.day.map(b.groupby('day').size()).fillna(0)]
    return pd.concat([b,x.drop(columns='slot')]).sort_values(['day','core','turnover_log','code'],ascending=[True,False,False,True])
def compare(q,days,root):
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    try:
        from research_v30.pde_core_compare import reconstruct
        from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError:
        from pde_core_compare import reconstruct
        from regime_portfolio import portfolio
    z,_=prepare(q,days);z=z.merge(rotation(q,days),on=['day','code'],validate='one_to_one');z=z[np.isfinite(z[EXTRA]).all(axis=1)].copy();flags=reconstruct(root/'NOVA_KRX_20231126_20261125.zip',root,days,z.code.unique());z=z.merge(flags,on=['day','code'],how='left',validate='one_to_one');assert z.core.notna().all();z['label']=labels(z)
    print('matched',len(z),'symbols',z.code.nunique(),flush=True);rows=[];metrics=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy();assert tr.seq.max()+H<te.seq.min()
        for name,cols in [('static',list(SEQUENCE)),('rotation',list(SEQUENCE)+EXTRA)]:
            m=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(tr[cols],tr.label);assert list(m.classes_)==[0,1,2];p=m.predict_proba(te[cols]);te[name]=p[:,1]-p[:,2];te[name+'_rise']=p[:,1];te[name+'_risk']=p[:,2]
        for scope in (100,300):
            p=te[te['rank']<=scope];k=p[p.label.ge(0)&~p.core]
            for name in ('static','rotation'):metrics.append(dict(fold_start=days[start],scope=scope,model=name,known_noncore=len(k),rise_auc=roc_auc_score(k.label.eq(1),k[name+'_rise']),risk_auc=roc_auc_score(k.label.eq(2),k[name+'_risk'])))
            b=choose(p,'core')
            for rule in ('core','liquidity','static','rotation','fill_liquidity','fill_static','fill_rotation'):
                t=choose(p,rule);assert t.groupby('day').size().le(2).all() and not t.duplicated(['day','code']).any()
                if rule.startswith('fill'):assert set(b.index)<=set(t.index)
                k=t[t.label.ge(0)]
                for fee in (.003,.006):
                    n=net(k.r5,fee);win=n.ge(3)&k.mae5.gt(-5);risk=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),clean_winners=int(win.sum()),adverse_rate=risk.mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics)
if __name__=='__main__':
    cache,root,out=map(Path,sys.argv[1:]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c';assert hashlib.file_digest((root/'NOVA_KRX_20231126_20261125.zip').open('rb'),'sha256').hexdigest()=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    a,b=compare(q,days,root);assert len(a)==224 and len(b)==16 and not a.duplicated(['fold_start','scope','rule','fee_bps','unknown_multiplier']).any()
    a.to_csv(out/'nova_pde_rotation_replay.csv',index=False);b.to_csv(out/'nova_pde_rotation_metrics.csv',index=False)
    print(a.query('scope==300 and fee_bps==120').groupby(['rule','unknown_multiplier'])[['terminal_return_pct','clean_winners','candidates','trades','adverse_rate']].agg({'terminal_return_pct':'mean','clean_winners':'sum','candidates':'sum','trades':'sum','adverse_rate':'mean'}).to_string());print(b.groupby(['scope','model'])[['rise_auc','risk_auc']].mean().to_string())
