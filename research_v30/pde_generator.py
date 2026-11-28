"""PDE8: finite strategy generation with frozen chronological selection; research only."""
import hashlib,pickle,sys
from pathlib import Path
from itertools import product
import pandas as pd
from research_v30.pde_regime import prepare,labels,index_feature,classify,eligible,fit,REGIMES,FOLDS,H
BASE=('liquidity',100,'all',5,2)
CASH=('cash',100,'all',5,2)
def generate():return list(product(('liquidity','global','blend'),(100,300),('all','guard'),(3,5),(1,2)))
def choose(p,spec):
    score,scope,gate,h,n=spec
    if score=='cash':return p.iloc[:0]
    z=p[p['rank']<=scope]
    if gate=='guard':z=z[z.regime.ne('down')&z.ret5.le(.08)]
    return z.sort_values(['day','turnover_log' if score=='liquidity' else score,'code'],ascending=[True,False,True]).groupby('day').head(n)
def evaluate(p,calendar,spec,fee=.006,stress=0.):
    from research_v30.regime_portfolio import portfolio
    t=choose(p,spec)
    return dict(candidates=len(t),signal_days=t.day.nunique(),**portfolio(t,calendar,spec[3],fee,stress))
def freeze(results,baseline):
    valid=[(s,r) for s,r in results if r['trades']>=12 and r['unknown']/r['trades']<=.05 and r['terminal_return_pct']>max(0.,baseline)+1]
    return (max(valid,key=lambda x:(x[1]['terminal_return_pct'],x[0]))[0] if valid else CASH),len(valid)
def scores(tr,ev):
    p=fit(tr,ev);s=p.copy()
    for r in REGIMES:
        a=tr[tr.regime.eq(r)];mask=ev.regime.eq(r).to_numpy()
        if eligible(a) and mask.any():s[mask]=fit(a,ev.loc[mask])
    ev=ev.copy();ev['global']=p[:,1]-p[:,2];ev['blend']=(ev['global']+s[:,1]-s[:,2])/2
    return ev
def compare(q,days,idx):
    z,_=prepare(q,days);z['label']=labels(z);z['index20']=z.day.map(idx);z['regime']=classify(z.index20)
    search=[];outer=[];choices=[]
    for start,end in FOLDS:
        cs=start-50
        tr=z[(z.seq+H<cs)&z.label.ge(0)];cal=z[(z.seq>=cs)&(z.seq+H<start)];te=z[(z.seq>=start)&(z.seq+H<=end)]
        assert tr.seq.max()+H<cal.seq.min() and cal.seq.max()+H<te.seq.min() and eligible(tr)
        cal=scores(tr,cal);calendar=days[cs:start];results=[]
        for spec in generate():
            r=evaluate(cal,calendar,spec);results.append((spec,r));search.append(dict(fold_start=days[start],spec=str(spec),**r))
        baseline=next(r['terminal_return_pct'] for s,r in results if s==BASE);chosen,passed=freeze(results,baseline)
        choices.append(dict(fold_start=days[start],train_last=days[int(tr.seq.max())],cal_start=days[cs],cal_last=days[int(cal.seq.max())],chosen=str(chosen),passed=passed,baseline_cal=baseline,chosen_cal=next((r['terminal_return_pct'] for s,r in results if s==chosen),0.)))
        te=scores(tr,te)
        for policy,spec in [('generated',chosen),('liquidity',BASE),('cash',CASH)]:
            for fee,stress in product((.003,.006),(0.,1.)):
                outer.append(dict(fold_start=days[start],fold_end=days[end],policy=policy,spec=str(spec),fee_bps=round(fee*20000),unknown_multiplier=stress,**evaluate(te,days[start:end+1],spec,fee,stress)))
        print('fold',days[start],'passed',passed,'chosen',chosen,flush=True)
    return pd.DataFrame(search),pd.DataFrame(outer),pd.DataFrame(choices)
if __name__=='__main__':
    cache,root,out=map(Path,sys.argv[1:4])
    with cache.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400
    a,b,c=compare(q,days,index_feature(root,days));assert len(a)==192 and len(b)==48 and len(c)==4
    a.to_csv(out/'nova_pde_generator_search.csv',index=False);b.to_csv(out/'nova_pde_generator_replay.csv',index=False);c.to_csv(out/'nova_pde_generator_choices.csv',index=False)
    print(c.to_string(index=False));print(b.query('fee_bps==120 and unknown_multiplier==0')[['fold_start','policy','trades','unknown','terminal_return_pct']].to_string(index=False))
