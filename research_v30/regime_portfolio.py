"""Offline fixed-ticket portfolio scenarios, not verified execution."""
import hashlib,pickle,sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
from research_v30.full_market import F,P
RULES=('liquidity','liquidity_guard','joint','joint_guard','liquid_joint_guard')
def portfolio(t,days,h,fee,unknown_multiplier):
    byday={k:v[['code',f'r{h}']].to_records(index=False).tolist() for k,v in t.groupby('day')};cash=100.;positions=[];pending=[];trades=unknown=dup=capacity=peak=0;mincash=100.;known=[]
    for day in days:
        for code,gross in pending:
            if any(p[0]==code for p in positions):dup+=1;continue
            if len(positions)>=10 or cash<10-1e-9:capacity+=1;continue
            mult=(1+gross/100)*(1-fee)/(1+fee) if np.isfinite(gross) else unknown_multiplier
            if np.isfinite(gross):known.append(100*(mult-1))
            else:unknown+=1
            cash-=10;trades+=1;positions.append((code,h,10*mult));mincash=min(mincash,cash);peak=max(peak,len(positions))
        keep=[]
        for code,left,proceeds in positions:
            if left==1:cash+=proceeds
            else:keep.append((code,left-1,proceeds))
        positions=keep;pending=byday.get(day,[])
    assert not positions and not pending and trades+dup+capacity==len(t)
    return dict(trades=trades,unknown=unknown,duplicate_skips=dup,capacity_skips=capacity,peak_slots=peak,min_cash=mincash,terminal_return_pct=cash-100,known_trade_mean_pct=np.mean(known) if known else np.nan)
def run(cache,root,out,h):
    with cache.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    series=[]
    for names,hashes in [(['data_0200_20261127.xlsx'],['7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40']),(['data_0622_20261127.xlsx','data_2426_20261127.xlsx'],['e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'])]:
        for n,digest in zip(names,hashes):assert hashlib.sha256((root/n).read_bytes()).hexdigest()==digest
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();p=a['종가'].shift(1);series.append(p/p.shift(20)-1)
    q['index20']=q.day.map((series[0]+series[1])/2);assert q.index20.notna().all();q['regime']=np.select([q.index20>.02,q.index20<-.02],['up','down'],default='flat')
    net=100*((1+q[f'r{h}']/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();cols=F+['index20'];x=q.loc[tr,cols];risk=q.loc[tr,f'mae{h}'].le(-5)
    q['mean']=LGBMRegressor(**P).fit(x,net[tr]).predict(q[cols]);q['risk']=LGBMClassifier(**P).fit(x,risk.astype(int)).predict_proba(q[cols])[:,1];q['joint']=q['mean']-5*q.risk
    rows=[];reg=[]
    for phase,cal in [('development',days[65:364]),('validation',days[364:546])]:
        p=q[q.phase.eq(phase)];print(h,phase,p.groupby('regime').day.nunique().to_dict(),flush=True)
        for rule in RULES:
            z=p[p.regime.ne('down')] if rule.endswith('guard') else p
            if rule=='liquid_joint_guard':z=z.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(100)
            score='turnover_log' if rule.startswith('liquidity') else 'joint';t=z.sort_values(['day',score,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                for stress in (0.,1.):rows.append(dict(horizon=h,phase=phase,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),**portfolio(t,cal,h,fee,stress)))
            for regime,g in t.groupby('regime'):
                n=100*((1+g[f'r{h}']/100)*.997/1.003-1);k=n.dropna();reg.append(dict(horizon=h,phase=phase,rule=rule,regime=regime,signals=len(g),known=len(k),mean_net_pct=k.mean(),precision=k.ge(3).sum()/len(g),adverse5=g.loc[k.index,f'mae{h}'].le(-5).mean()))
    pd.DataFrame(rows).to_csv(out/f'p{h}.csv',index=False);pd.DataFrame(reg).to_csv(out/f'r{h}.csv',index=False);print(pd.DataFrame(rows).query("phase=='validation' and fee_bps==60 and unknown_multiplier==0").to_string(index=False),flush=True)
if __name__=='__main__':run(*map(Path,sys.argv[1:4]),int(sys.argv[4]))
