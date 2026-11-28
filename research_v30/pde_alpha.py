"""Offline market-relative target ablation, no orders."""
import hashlib,pickle,sys,zipfile
from pathlib import Path
import numpy as np,pandas as pd
try:
    from research_v30.pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE
except ModuleNotFoundError:
    from pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE

def excess(stock,market):return 100*((1+stock/100)/(1+market/100)-1)
def market_returns(o,c):return 100*(c.shift(-H)/o.shift(-1)-1)
def alpha_labels(z):
    n=net(z.r5);a=excess(n,z.market_r5);known=np.isfinite(n)&np.isfinite(a)&np.isfinite(z.mae5)
    return pd.Series(np.select([~known,(a>=3)&(z.mae5>-5),(n<=-5)|(z.mae5<=-5)],[-1,1,2],default=0),index=z.index)
def attach_market(z,root,days):
    with zipfile.ZipFile(root/'NOVA_KRX_20231126_20261125.zip') as f:
        m=pd.concat([pd.read_csv(f.open(n),usecols=['BAS_DD','ISU_CD','market'],dtype=str) for n in f.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(m)==1995617 and not m.duplicated(['BAS_DD','ISU_CD']).any()
    z=z.merge(m.rename(columns={'BAS_DD':'day','ISU_CD':'code'}),on=['day','code'],how='left',validate='one_to_one');assert z.market.isin(['KOSPI','KOSDAQ']).all()
    frames=[]
    for market,names in [('KOSPI',['data_0200_20261127.xlsx']),('KOSDAQ',['data_0622_20261127.xlsx','data_2426_20261127.xlsx'])]:
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','시가','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and np.isfinite(a[['시가','종가']]).all().all() and (a[['시가','종가']]>0).all().all()
        frames.append(pd.DataFrame({'day':days,'market':market,'market_r5':market_returns(a['시가'],a['종가']).values}))
    return z.merge(pd.concat(frames),on=['day','market'],validate='many_to_one')
def choose(p,rule):
    def top(t,k):return t.sort_values(['day',k,'code'],ascending=[True,False,True]).groupby('day').head(2)
    b=top(p[p.core],'turnover_log')
    if rule=='core':return b
    if rule=='liquidity':return top(p,'turnover_log')
    if not rule.startswith('fill_'):return top(p,rule)
    x=top(p[~p.core],rule[5:]).copy();x=x[x.groupby('day').cumcount()<2-x.day.map(b.groupby('day').size()).fillna(0)]
    return pd.concat([b,x]).sort_values(['day','core','turnover_log','code'],ascending=[True,False,False,True])
RULES=('core','liquidity','absolute','alpha','blend','fill_absolute','fill_alpha','fill_blend')
def compare(z,days):
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    try:from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError:from regime_portfolio import portfolio
    z=z.copy();z['absolute_label']=labels(z);z['alpha_label']=alpha_labels(z);rows=[];metrics=[];coverage=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.absolute_label.ge(0)&z.alpha_label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy();assert tr.seq.max()+H<te.seq.min()
        for name in ('absolute','alpha'):
            y=tr[name+'_label'];assert set(y)=={0,1,2}
            model=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(tr[list(SEQUENCE)],y)
            pr=model.predict_proba(te[list(SEQUENCE)]);assert list(model.classes_)==[0,1,2];te[name]=pr[:,1]-pr[:,2];te[name+'_rise']=pr[:,1]
        te['blend']=(te.absolute+te.alpha)/2
        for scope in (100,300):
            p=te[te['rank']<=scope];k=p[p.absolute_label.ge(0)&p.alpha_label.ge(0)];w=k[k.absolute_label.eq(1)];coverage.append(dict(fold_start=days[start],scope=scope,eligible=len(p),known=len(k),absolute_winners=len(w),alpha_winners=int(k.alpha_label.eq(1).sum()),both=int(w.alpha_label.eq(1).sum()),core_absolute=int(w.core.sum()),core_both=int((w.core&w.alpha_label.eq(1)).sum())))
            for name in ('absolute','alpha'):
                metrics.append(dict(fold_start=days[start],scope=scope,model=name,train_rows=len(tr),known=len(k),absolute_auc=roc_auc_score(k.absolute_label.eq(1),k[name+'_rise']),alpha_auc=roc_auc_score(k.alpha_label.eq(1),k[name+'_rise'])))
            base=choose(p,'core')
            for rule in RULES:
                t=choose(p,rule);assert t.groupby('day').size().le(2).all() and not t.duplicated(['day','code']).any()
                if rule.startswith('fill'):assert set(base.index)<=set(t.index)
                k=t[t.absolute_label.ge(0)&t.alpha_label.ge(0)]
                for fee in (.003,.006):
                    n=net(k.r5,fee);win=n.ge(3)&k.mae5.gt(-5);a=excess(n,k.market_r5);aw=a.ge(3)&k.mae5.gt(-5);risk=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),clean_winners=int(win.sum()),alpha_winners=int(aw.sum()),adverse_rate=risk.mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],len(tr),len(te),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics),pd.DataFrame(coverage)
def run(cache,root,out):
    try:from research_v30.pde_core_compare import reconstruct
    except ModuleNotFoundError:from pde_core_compare import reconstruct
    for p,h in [(cache,'af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'),(root/'NOVA_KRX_20231126_20261125.zip','b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753')]:
        with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==h
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    z,_=prepare(q,days);z=attach_market(z,root,days);flags=reconstruct(root/'NOVA_KRX_20231126_20261125.zip',root,days,z.code.unique());z=z.merge(flags,on=['day','code'],how='left',validate='one_to_one');assert z.core.notna().all() and np.isfinite(z.market_r5).all();print('matched',len(z),flush=True)
    a,b,c=compare(z,days);assert len(a)==256 and len(b)==16 and len(c)==8 and not a.duplicated(['fold_start','scope','rule','fee_bps','unknown_multiplier']).any()
    a.to_csv(out/'nova_pde_alpha_replay.csv',index=False);b.to_csv(out/'nova_pde_alpha_metrics.csv',index=False);c.to_csv(out/'nova_pde_alpha_coverage.csv',index=False)
    print(a.query('scope==300 and fee_bps==120').groupby(['rule','unknown_multiplier']).agg({'terminal_return_pct':'mean','clean_winners':'sum','alpha_winners':'sum','candidates':'sum','trades':'sum','adverse_rate':'mean'}).to_string());print(b.groupby(['scope','model'])[['absolute_auc','alpha_auc']].mean().to_string());print(c.groupby('scope').sum(numeric_only=True).to_string())
if __name__=='__main__':run(*map(Path,sys.argv[1:]))
