"""Offline price-cap-at-open proxy; not an executable fill simulation."""
import sys,pickle,hashlib,zipfile
from pathlib import Path
import numpy as np,pandas as pd
from research_v30.excess_risk import fit

def attach_gap(q,days,src):
    with src.open('rb') as f: assert hashlib.file_digest(f,'sha256').hexdigest()=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
    cols=['BAS_DD','ISU_CD','TDD_CLSPRC','TDD_OPNPRC','LIST_SHRS','CMPPREVDD_PRC','market']
    with zipfile.ZipFile(src) as z:
        d=pd.concat([pd.read_csv(z.open(n),usecols=cols,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert not d.duplicated(['BAS_DD','ISU_CD']).any()
    d=d.sort_values(['ISU_CD','BAS_DD']);g=d.groupby('ISU_CD',sort=False);seq=d.BAS_DD.map(dict(zip(days,range(len(days)))))
    nxt=g.TDD_OPNPRC.shift(-1);c=d.TDD_CLSPRC
    ok=(seq.groupby(d.ISU_CD).shift(-1)-seq==1)&(g.LIST_SHRS.shift(-1)==d.LIST_SHRS)&(g.market.shift(-1)==d.market)&(g.TDD_CLSPRC.shift(-1)-g.CMPPREVDD_PRC.shift(-1)==c)&(nxt>0)&(c>0)
    d['gap']=100*(nxt/c-1).where(ok)
    return q.merge(d[['BAS_DD','ISU_CD','gap']].rename(columns={'BAS_DD':'day','ISU_CD':'code'}),on=['day','code'],validate='one_to_one',how='left')

def eval_caps(t,calendar,cap,fee):
    skip=t.gap.gt(cap) if cap is not None else pd.Series(False,index=t.index)
    a=t[~skip];n=100*((1+a.gross1/100)*(1-fee)/(1+fee)-1)
    if cap is not None:n=n.where(a.gap.notna())
    k=n.dropna();daily=n.fillna(-100).groupby(a.day).sum().reindex(calendar,fill_value=0)/2
    return dict(candidates=len(t),skipped=int(skip.sum()),attempted=len(a),known=len(k),unknown=len(a)-len(k),precision=k.ge(3).sum()/len(a) if len(a) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean())

def compare(q,days):
    rows=[];bins=[]
    for phase,calendar in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)]
        for rule in ('liquidity','up','excess'):
            t=p.sort_values(['day',rule,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for cap in (None,0.,1.,2.):
                for fee in (.003,.006):rows.append(dict(phase=phase,rule=rule,cap='none' if cap is None else str(cap),fee_bps=round(fee*20000),**eval_caps(t,calendar,cap,fee)))
            bucket=pd.cut(t.gap,[-np.inf,-2,0,1,2,np.inf],include_lowest=True).astype(str)
            for b,g in t.groupby(bucket):
                k=(100*((1+g.gross1/100)*.997/1.003-1)).dropna();bins.append(dict(phase=phase,rule=rule,gap_bin=b,candidates=len(g),known=len(k),mean_net_pct=k.mean()))
    return pd.DataFrame(rows),pd.DataFrame(bins)

if __name__=='__main__':
    cache,src,out=map(Path,sys.argv[1:4])
    # Trusted locally generated private cache only. Never load an external pickle.
    with cache.open('rb') as f:q,meta,days=pickle.load(f)
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    q=attach_gap(q,days,src);print('gap known',q.gap.notna().sum(),flush=True);q=fit(q);m,b=compare(q,days);assert len(m)==48 and (m.known+m.unknown+m.skipped).eq(m.candidates).all();m.to_csv(out,index=False);b.to_csv(out.with_name('nova_v30_gap_buckets.csv'),index=False);print(m[(m.phase=='validation')&(m.fee_bps==60)].to_string(index=False),flush=True)
