"""Paired 1/3/5-session research; not a portfolio or live strategy."""
import hashlib,pickle,zipfile,sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
from research_v30.full_market import F,P
H=(1,3,5)
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def prepare(cache,src,out):
    assert digest(cache)=='db4e78cbf124701be438500dd78810250a39e52ef2bb5a2dd545e8366141032c'
    assert digest(src)=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
    with cache.open('rb') as f:q,meta,days=pickle.load(f)
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    cols=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','MKTCAP','LIST_SHRS']
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=cols,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any() and sorted(d.BAS_DD.unique())==days
    d=d.rename(columns=dict(zip(cols[3:],['Close','Delta','Open','High','Low','Volume','Cap','Shares']))).sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True);d['seq']=d.BAS_DD.map(dict(zip(days,range(len(days)))))
    valid=(d.Close>0)&(d.Open>0)&(d.Low>0)&(d.Low<=d[['Open','Close']].min(axis=1))&(d.High>=d[['Open','Close']].max(axis=1))&(d.Volume>0)&(d.Shares>0)&(d.Cap==d.Close*d.Shares)
    boundary=(d.ISU_CD!=d.ISU_CD.shift())|(d.seq.diff()!=1)|(d.Close-d.Delta!=d.Close.shift())|(d.Shares!=d.Shares.shift())|(d.market!=d.market.shift())|~valid|~valid.shift(fill_value=False)
    d['seg']=boundary.cumsum();d=d[valid].copy();g=d.groupby('seg',sort=False);entry=g.Open.shift(-1)
    for h in H:
        d[f'r{h}']=100*(g.Close.shift(-h)/entry-1)
        d[f'mae{h}']=100*(pd.concat([g.Low.shift(-k) for k in range(1,h+1)],axis=1).min(axis=1,skipna=False)/entry-1)
    q=q.merge(d[['BAS_DD','ISU_CD',*[f'{p}{h}' for h in H for p in ('r','mae')]]].rename(columns={'BAS_DD':'day','ISU_CD':'code'}),on=['day','code'],how='left',validate='one_to_one')
    assert np.allclose(q.r1,q.gross1,equal_nan=True) and (q.mae5.notna()==q.r5.notna()).all()
    assert (q.r5.notna()<=q.r3.notna()).all() and (q.r3.notna()<=q.r1.notna()).all()
    print('cohort',len(q),'known',q.groupby('phase')[[f'r{h}' for h in H]].count().to_dict(),flush=True)
    with out.open('wb') as f:pickle.dump((q,days),f,protocol=5)
def fit(cache,h,out):
    with cache.open('rb') as f:q,days=pickle.load(f)
    net=100*((1+q[f'r{h}']/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();x=q.loc[tr,F];y=net[tr];event=y.ge(3);risk=q.loc[tr,f'mae{h}'].le(-5)
    assert event.value_counts().min()>=20 and risk.value_counts().min()>=20
    q['up']=LGBMClassifier(**P).fit(x,event.astype(int)).predict_proba(q[F])[:,1]
    q['mean']=LGBMRegressor(**P).fit(x,y).predict(q[F]);q['risk']=LGBMClassifier(**P).fit(x,risk.astype(int)).predict_proba(q[F])[:,1]
    q['joint']=q['mean']-5*q.risk;q['liquidity']=q.turnover_log;rows=[]
    for phase,cal in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)];known=p[p[f'r{h}'].notna()];label=100*((1+known[f'r{h}']/100)*.997/1.003-1)>=3
        for rule in ('liquidity','up','mean','joint'):
            t=p.sort_values(['day',rule,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                n=100*((1+t[f'r{h}']/100)*(1-fee)/(1+fee)-1);k=n.dropna();daily=n.fillna(-100).groupby(t.day).sum().reindex(cal,fill_value=0)/2
                rows.append(dict(horizon=h,phase=phase,rule=rule,fee_bps=round(fee*20000),eligible=len(p),eligible_known=len(known),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t),mean_net_pct=k.mean(),loss5=k.le(-5).mean(),adverse5=t.loc[k.index,f'mae{h}'].le(-5).mean(),conservative_daily=daily.mean(),auc=roc_auc_score(label,known[rule]),ap=average_precision_score(label,known[rule]),base_rate=label.mean()))
    m=pd.DataFrame(rows);assert len(m)==16 and (m.known+m.unknown).eq(m.signals).all();m.to_csv(out,index=False);print(m[(m.phase=='validation')&(m.fee_bps==60)].to_string(index=False),flush=True)
if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare(*map(Path,sys.argv[2:5]))
    elif sys.argv[1]=='fit':fit(Path(sys.argv[2]),int(sys.argv[3]),Path(sys.argv[4]))
