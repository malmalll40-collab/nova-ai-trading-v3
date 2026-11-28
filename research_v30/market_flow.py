"""Offline market turnover/breadth proxies, not investor flows or industry rotation."""
import sys,pickle,hashlib,zipfile
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
try:
    from research_v30.full_market import F,P
except ModuleNotFoundError:
    from full_market import F,P
EXTRA=['market_turnover_share','share_change5','market_breadth','breadth_change5','market_turnover_ratio']
def attach(q,src):
    with src.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
    cols=['BAS_DD','ISU_CD','market','ACC_TRDVAL','ACC_TRDVOL','CMPPREVDD_PRC']
    with zipfile.ZipFile(src) as z:
        d=pd.concat([pd.read_csv(z.open(n),usecols=cols,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any()
    t=d[d.ACC_TRDVOL.gt(0)&d.ACC_TRDVAL.gt(0)].assign(up=lambda x:x.CMPPREVDD_PRC.gt(0))
    a=t.groupby(['market','BAS_DD']).agg(turnover=('ACC_TRDVAL','sum'),market_breadth=('up','mean')).reset_index().sort_values(['market','BAS_DD'])
    a['market_turnover_share']=a.turnover/a.groupby('BAS_DD').turnover.transform('sum')
    g=a.groupby('market');a['share_change5']=a.market_turnover_share-g.market_turnover_share.shift(5);a['breadth_change5']=a.market_breadth-g.market_breadth.shift(5)
    a['market_turnover_ratio']=a.turnover/g.turnover.transform(lambda x:x.shift().rolling(20).median()).replace(0,np.nan)
    z=q.merge(d[['BAS_DD','ISU_CD','market']].rename(columns={'BAS_DD':'day','ISU_CD':'code'}),on=['day','code'],validate='one_to_one',how='left').merge(a[['market','BAS_DD',*EXTRA]].rename(columns={'BAS_DD':'day'}),on=['market','day'],validate='many_to_one',how='left')
    assert len(z)==len(q) and np.isfinite(z[EXTRA]).all().all()
    return z

def compare(q,days):
    net=100*((1+q.gross1/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();y=net[tr].ge(3).astype(int)
    for name,cols in [('base',F),('market_flow',F+EXTRA)]:
        q[name]=LGBMClassifier(**P).fit(q.loc[tr,cols],y).predict_proba(q[cols])[:,1]
    rows=[]
    for phase,cal in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)];known=p[p.gross1.notna()];label=100*((1+known.gross1/100)*.997/1.003-1)>=3
        for rule in ('liquidity','base','market_flow'):
            key='turnover_log' if rule=='liquidity' else rule;t=p.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                n=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1);k=n.dropna();daily=n.fillna(-100).groupby(t.day).sum().reindex(cal,fill_value=0)/2
                rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),eligible=len(p),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t),mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean(),auc=roc_auc_score(label,known[rule]) if rule!='liquidity' else np.nan,ap=average_precision_score(label,known[rule]) if rule!='liquidity' else np.nan))
    return pd.DataFrame(rows)
if __name__=='__main__':
    cache,src,out=map(Path,sys.argv[1:4]);assert hashlib.sha256(cache.read_bytes()).hexdigest()=='db4e78cbf124701be438500dd78810250a39e52ef2bb5a2dd545e8366141032c'
    # Only the trusted locally created cache may be loaded.
    with cache.open('rb') as f:q,meta,days=pickle.load(f)
    q=attach(q,src);print('cohort',len(q),flush=True);m=compare(q,days);assert len(m)==12 and (m.known+m.unknown).eq(m.signals).all();m.to_csv(out,index=False);print(m.to_string(index=False),flush=True)
