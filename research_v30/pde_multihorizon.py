"""Multi-horizon chronological replay. Research only."""
import hashlib,pickle,sys,zipfile
from pathlib import Path
import numpy as np,pandas as pd
try:
    from research_v30.pde_sequence import prepare,SEQUENCE,FOLDS,net,select
except ModuleNotFoundError:
    from pde_sequence import prepare,SEQUENCE,FOLDS,net,select
H=(1,3,5,10,20)

def outcomes(d,days):
    if d.duplicated(['BAS_DD','ISU_CD']).any():raise ValueError('duplicate date/code')
    d=d.sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True).copy();d['seq']=d.BAS_DD.map(dict(zip(days,range(len(days)))))
    if d.seq.isna().any():raise ValueError('unknown date')
    v=(d.Close>0)&(d.Open>0)&(d.Low>0)&(d.Low<=d[['Open','Close']].min(axis=1))&(d.High>=d[['Open','Close']].max(axis=1))&(d.Volume>0)&(d.Shares>0)&(d.Cap==d.Close*d.Shares)
    b=(d.ISU_CD!=d.ISU_CD.shift())|(d.seq.diff()!=1)|(d.Close-d.Delta!=d.Close.shift())|(d.Shares!=d.Shares.shift())|(d.market!=d.market.shift())|~v|~v.shift(fill_value=False)
    d['seg']=b.cumsum();d=d[v].copy();g=d.groupby('seg',sort=False);entry=g.Open.shift(-1);low=None
    for k in range(1,21):
        x=g.Low.shift(-k).to_numpy(dtype=float);low=x if low is None else np.minimum(low,x)
        if k in H:d[f'r{k}']=100*(g.Close.shift(-k)/entry-1);d[f'mae{k}']=100*(low/entry-1)
    return d[['BAS_DD','ISU_CD',*[f'{p}{h}' for h in H for p in ('r','mae')]]].rename(columns={'BAS_DD':'day','ISU_CD':'code'})

def labels(p,h):
    n=net(p[f'r{h}']);a=p[f'mae{h}'];known=np.isfinite(n)&np.isfinite(a)
    return pd.Series(np.select([~known,(n>=3)&(a>-5),(n<=-5)|(a<=-5)],[-1,1,2],default=0),index=p.index)

def attach(q,days,src):
    fields=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','MKTCAP','LIST_SHRS']
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=fields,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any() and sorted(d.BAS_DD.unique())==days
    d=d[d.ISU_CD.isin(q.code.unique())].rename(columns=dict(zip(fields[3:],['Close','Delta','Open','High','Low','Volume','Cap','Shares'])))
    z=q.merge(outcomes(d,days),on=['day','code'],how='left',validate='one_to_one',suffixes=('_old',''))
    for h in (1,3,5):
        for p in ('r','mae'):
            k=f'{p}{h}';assert np.allclose(z[k],z[k+'_old'],equal_nan=True),k;z.drop(columns=k+'_old',inplace=True)
    for a,b in zip(H,H[1:]):assert (z[f'r{b}'].notna()<=z[f'r{a}'].notna()).all()
    return z

def split(z,start,end):
    tr=z[z.seq+20<start];te=z[(z.seq>=start)&(z.seq+20<=end)].copy()
    assert len(tr)>0 and len(te)>0 and tr.seq.max()+20<te.seq.min()
    return tr,te

def compare(z,days):
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score,brier_score_loss
    try:from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError:from regime_portfolio import portfolio
    for h in H:z[f'label{h}']=labels(z,h)
    rows=[];metrics=[]
    for start,end in FOLDS:
        tr,te=split(z,start,end)
        for h in H:
            a=tr[tr[f'label{h}'].ge(0)];y=a[f'label{h}'];assert set(y)=={0,1,2} and y.value_counts().min()>=100
            m=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(a[list(SEQUENCE)],y)
            p=m.predict_proba(te[list(SEQUENCE)]);assert list(m.classes_)==[0,1,2]
            te[f'clean{h}']=p[:,1];te[f'risk{h}']=p[:,2];te[f'score{h}']=p[:,1]-p[:,2]
        te['multi']=te[[f'score{h}' for h in H]].mean(axis=1)
        for scope in (100,300):
            t=te[te['rank']<=scope]
            for h in H:
                k=t[t[f'label{h}'].ge(0)];y=k[f'label{h}']
                metrics.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,horizon=h,train_known=int(tr[f'label{h}'].ge(0).sum()),eval_rows=len(t),eval_known=len(k),clean_base_rate=y.eq(1).mean(),risk_base_rate=y.eq(2).mean(),clean_auc=roc_auc_score(y.eq(1),k[f'clean{h}']),risk_auc=roc_auc_score(y.eq(2),k[f'risk{h}']),clean_brier=brier_score_loss(y.eq(1),k[f'clean{h}']),risk_brier=brier_score_loss(y.eq(2),k[f'risk{h}'])))
                for rule,score in [('liquidity','turnover_log'),('single',f'score{h}'),('multi','multi')]:
                    c=select(t,score);known=c[c[f'label{h}'].ge(0)]
                    for fee in (.003,.006):
                        n=net(known[f'r{h}'],fee);clean=n.ge(3)&known[f'mae{h}'].gt(-5);adverse=n.le(-5)|known[f'mae{h}'].le(-5)
                        for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,horizon=h,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(c),clean_precision=clean.sum()/len(c),adverse_rate=adverse.mean(),**portfolio(c,days[start:end+1],h,fee,stress)))
        print('fold complete',days[start],len(tr),len(te),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics)

def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(cache,src,out):
    assert digest(cache)=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c' and digest(src)=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    z,_=prepare(q,days);z=attach(z,days,src);print('matched',len(z),'known',z[[f'r{h}' for h in H]].count().to_dict(),flush=True)
    a,b=compare(z,days);assert len(a)==480 and len(b)==40 and not a.duplicated(['fold_start','scope','horizon','rule','fee_bps','unknown_multiplier']).any()
    out.mkdir(exist_ok=True,parents=True);a.to_csv(out/'nova_pde_multihorizon_replay.csv',index=False);b.to_csv(out/'nova_pde_multihorizon_metrics.csv',index=False)
    print(a.query('fee_bps==120 and scope==300').groupby(['horizon','rule','unknown_multiplier'])[['terminal_return_pct','clean_precision','adverse_rate','trades','unknown']].mean().to_string(),flush=True)
    print(b.query('scope==300').groupby('horizon')[['clean_auc','risk_auc','clean_base_rate','risk_base_rate']].mean().to_string(),flush=True)
if __name__=='__main__':run(*map(Path,sys.argv[1:4]))
