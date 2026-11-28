"""Directed predictive correlation, not causal or actual fund flow."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
try:
    from research_v30.pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE
except ModuleNotFoundError:
    from pde_sequence import prepare,labels,net,FOLDS,H,SEQUENCE

def corr_pairs(x,y):
    a=np.isfinite(x).astype(float);b=np.isfinite(y).astype(float)
    x=np.where(a,x,0.);y=np.where(b,y,0.)
    n=a.T@b;sx=x.T@b;sy=a.T@y
    with np.errstate(divide='ignore',invalid='ignore'):
        cov=x.T@y-sx*sy/n;vx=(x*x).T@b-sx*sx/n;vy=a.T@(y*y)-sy*sy/n
        r=cov/np.sqrt(np.maximum(vx,0)*np.maximum(vy,0))
    return np.where((n>=45)&np.isfinite(r),r*n/(n+60),np.nan),n

def graph(hist,cut):
    tr=hist[hist.seq+H<cut];assert len(tr)>0
    codes=tr.groupby('code').turnover_log.median().nlargest(600).index.tolist();leaders=codes[:300]
    x=tr.pivot(index='seq',columns='code',values='x').reindex(columns=leaders)
    y=tr.pivot(index='seq',columns='code',values='y').reindex(index=x.index,columns=codes)
    r,n=corr_pairs(x.to_numpy(),y.to_numpy());edges=[]
    for j,f in enumerate(codes):
        v=r[:,j].copy();v[np.array(leaders)==f]=np.nan
        for i in np.argsort(-np.nan_to_num(v,nan=-np.inf),kind='stable')[:3]:
            if np.isfinite(v[i]) and v[i]>=.15:edges.append(dict(follower=f,leader=leaders[i],weight=v[i],support=int(n[i,j])))
    return pd.DataFrame(edges,columns=['follower','leader','weight','support'])

def score(hist,edges):
    a=hist[['seq','code','x']].merge(edges,left_on='code',right_on='leader');a=a[np.isfinite(a.x)].copy();a['wx']=a.x*a.weight
    z=a.groupby(['seq','follower']).agg(wx=('wx','sum'),w=('weight','sum'),n=('leader','size'));z['lead']=(z.wx/z.w).where(z.n>=2)
    return z.reset_index().rename(columns={'follower':'code'})[['seq','code','lead']]

def compare(q,days):
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    try:from research_v30.regime_portfolio import portfolio
    except ModuleNotFoundError:from regime_portfolio import portfolio
    z,_=prepare(q,days);z['label']=labels(z)
    hist=q[['day','code','ret5','r5','turnover_log']].copy();hist['seq']=hist.day.map(dict(zip(days,range(len(days)))));hist['x']=(hist.ret5-hist.groupby('day').ret5.transform('median')).clip(-.3,.3);hist['y']=(hist.r5-hist.groupby('day').r5.transform('median')).clip(-30,30)
    rows=[];metrics=[];graphs=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy();assert tr.seq.max()+H<te.seq.min()
        edges=graph(hist,start);graphs.append(edges.assign(fold_start=days[start]));te=te.merge(score(hist[hist.seq.between(start,end)],edges),on=['seq','code'],how='left',validate='one_to_one')
        m=LGBMClassifier(n_estimators=60,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1).fit(tr[list(SEQUENCE)],tr.label);pr=m.predict_proba(te[list(SEQUENCE)]);assert list(m.classes_)==[0,1,2];te['static']=pr[:,1]-pr[:,2]
        for scope in (100,300):
            p=te[te['rank']<=scope].copy();p['sr']=p.groupby('day').static.rank(pct=True);p['lr']=p.groupby('day').lead.rank(pct=True);p['blend']=((p.sr+p.lr)/2).fillna(p.sr)
            k=p[p.label.ge(0)&p.lead.notna()];metrics.append(dict(fold_start=days[start],scope=scope,edges=len(edges),followers=edges.follower.nunique(),eligible=len(p),covered=int(p.lead.notna().sum()),known_covered=len(k),**{f'{f}_auc':roc_auc_score(k.label.eq(1),k[f]) if k.label.eq(1).nunique()==2 else np.nan for f in ('static','lead','blend')}))
            for rule in ('liquidity','static','lead','blend','matched_liquidity','matched_static'):
                matched=rule.startswith('matched') or rule=='lead';t=p[p.lead.notna()] if matched else p
                key={'liquidity':'turnover_log','matched_liquidity':'turnover_log','matched_static':'static'}.get(rule,rule)
                t=t.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2);k=t[t.label.ge(0)]
                for fee in (.003,.006):
                    n=net(k.r5,fee);win=n.ge(3)&k.mae5.gt(-5);risk=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),clean_winners=int(win.sum()),adverse_rate=risk.mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],'edges',len(edges),'covered',te.lead.notna().sum(),flush=True)
    return pd.DataFrame(rows),pd.DataFrame(metrics),pd.concat(graphs,ignore_index=True)

if __name__=='__main__':
    cache,out=map(Path,sys.argv[1:]);assert hashlib.file_digest(cache.open('rb'),'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    a,b,c=compare(q,days);assert len(a)==192 and len(b)==8 and not a.duplicated(['fold_start','scope','rule','fee_bps','unknown_multiplier']).any()
    a.to_csv(out/'nova_pde_leadlag_replay.csv',index=False);b.to_csv(out/'nova_pde_leadlag_metrics.csv',index=False);c.to_csv(out/'nova_pde_leadlag_edges.csv',index=False)
    print(a.query('scope==300 and fee_bps==120').groupby(['rule','unknown_multiplier']).agg({'terminal_return_pct':'mean','clean_winners':'sum','candidates':'sum','trades':'sum','adverse_rate':'mean'}).to_string());print(b.to_string(index=False))
