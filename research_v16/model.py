"""One prespecified comparison, offline research only; no adoption or model export."""
import json,hashlib,zipfile,sys
from pathlib import Path
import pandas as pd,numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor,HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,brier_score_loss
from quality import quality,FIELDS,SOURCE
FEATURES=['r1','r5','r20','vol20','dist20','dist50','range','liq','cap','volume_rel','breadth','relative20','market20','kosdaq']
def prepare(d,days):
    d=d.sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True)
    q,_=quality(d,days,(363,545,728));g=d.groupby('ISU_CD',sort=False)
    c=d.TDD_CLSPRC.astype(float);t=d.ACC_TRDVAL.astype(float)
    def roll(s,n,method):return getattr(s.groupby(d.ISU_CD,sort=False).rolling(n),method)().reset_index(level=0,drop=True).sort_index()
    for n in (1,5,20):q['r'+str(n)]=c/g.TDD_CLSPRC.shift(n)-1
    q['vol20']=roll(q.r1,20,'std')
    for n in (20,50):q['dist'+str(n)]=c/roll(c,n,'mean')-1
    q['range']=(d.TDD_HGPRC-d.TDD_LWPRC)/c
    q['liq']=np.log1p(q.median_turnover20);q['cap']=np.log1p(d.MKTCAP)
    q['volume_rel']=t/roll(t,20,'mean');q['kosdaq']=q.market.eq('KOSDAQ').astype(int)
    return q[q.eligible&~q.purged].copy()
def aggregate(q):
    q=q.copy();g=q.groupby(['day','market'],sort=False)
    q['breadth']=g.r1.transform(lambda s:s.gt(0).mean());q['market20']=g.r20.transform('median');q['relative20']=q.r20-q.market20
    return q

def metrics(q):
    y=q.net_pct.dropna();unknown=q.net_pct.isna();daily=q.assign(pess=q.net_pct.fillna(-100)).groupby('day').pess.mean();a=daily.to_numpy();rng=np.random.default_rng(1943)
    lower=None
    if len(a)>=40:
        b=10;starts=rng.integers(0,len(a)-b+1,(2000,(len(a)+b-1)//b));lower=float(np.quantile(a[(starts[:,:,None]+np.arange(b)).reshape(2000,-1)[:,:len(a)]].mean(axis=1),.05))
    return {'signals':len(q),'days':q.day.nunique(),'known':len(y),'unknown':int(unknown.sum()),'mean_net_pct':float(y.mean()),'win_pct':float(y.gt(0).mean()*100),'loss5_pct':float(y.le(-5).mean()*100),'pessimistic_daily_mean_pct':float(daily.mean()),'pessimistic_daily_lower95_pct':lower}
def main(root,out):
    source=root/'NOVA_KRX_20231126_20261125.zip'
    with source.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(source) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=['market','BAS_DD','ISU_CD',*FIELDS],dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    days=sorted(d.BAS_DD.unique());assert len(days)==729 and len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any();codes=d.ISU_CD.unique();parts=[]
    for i in range(0,len(codes),200):parts.append(prepare(d[d.ISU_CD.isin(codes[i:i+200])],days))
    del d;q=aggregate(pd.concat(parts,ignore_index=True));del parts
    assert len(q)==415309 and np.isfinite(q[FEATURES].to_numpy()).all()
    train=q[(q.phase=='development')&q.known];X=train[FEATURES].astype('float32');y=train.net_pct
    args=dict(max_iter=60,max_leaf_nodes=15,learning_rate=.06,min_samples_leaf=150,l2_regularization=5,early_stopping=False,random_state=1943)
    ret=HistGradientBoostingRegressor(**args).fit(X,y.clip(-30,30));risk=HistGradientBoostingClassifier(**args).fit(X,y.le(-5))
    q['pred']=ret.predict(q[FEATURES].astype('float32'));q['loss_score']=risk.predict_proba(q[FEATURES].astype('float32'))[:,1];q['risk_rank']=q.pred-5*q.loss_score
    rows=[];cal=[]
    for phase,g in q.groupby('phase',sort=False):
        for strategy,score in [('liquidity','liq'),('model_return','pred'),('model_risk','risk_rank')]:
            chosen=g.sort_values(['day',score,'code'],ascending=[True,False,True]).groupby('day',sort=False).head(2)
            rows.append({'phase':phase,'strategy':strategy,**metrics(chosen)})
        z=g[g.known];target=z.net_pct.le(-5);p=z.loss_score;prevalence=float(y.le(-5).mean());cal.append({'phase':phase,'n':len(z),'loss_auc':roc_auc_score(target,p),'loss_brier':brier_score_loss(target,p),'constant_training_brier':float(np.mean((target.astype(float)-prevalence)**2))})
    m=pd.DataFrame(rows);m.to_csv(out/'model_comparison.csv',index=False);pd.DataFrame(cal).to_csv(out/'risk_diagnostics.csv',index=False)
    val=m[m.phase=='validation'].set_index('strategy');r=val.loc['model_risk'];base=val.loc['liquidity'];gate=bool(r.pessimistic_daily_lower95_pct>0 and r.mean_net_pct>base.mean_net_pct and r.loss5_pct<base.loss5_pct and r.unknown/r.signals<=.02)
    summary={'source_sha256':SOURCE,'training_rows':len(train),'features':FEATURES,'params':args,'gate_pass':gate,'live_adoption':'HOLD','orders_enabled':False,'adjusted_prices_verified':False,'notes':'Model fitted only on development with observed outcomes. Scores uncalibrated. Market context is same-day eligible-universe breadth and median return, not verified indices. Top-two overlapping opportunities, not portfolio returns. -100% unresolved sensitivity is hypothetical. Reviewed holdout is not independent. No model artifact exported.'};(out/'model_summary.json').write_text(json.dumps(summary,indent=2));print(m.to_string(index=False));print(pd.DataFrame(cal).to_string(index=False));print(json.dumps(summary))
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
