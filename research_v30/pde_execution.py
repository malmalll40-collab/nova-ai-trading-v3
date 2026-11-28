"""PDE7: daily-bar exit ablation on fixed liquidity candidates; no live orders."""
import hashlib,pickle,sys,zipfile
from pathlib import Path
import numpy as np,pandas as pd
POLICIES=('fixed1','fixed3','fixed5','stop3_target6','stop5_target10','vol_stop_target','trail4')
FOLDS=((180,269),(270,363),(364,454),(455,545))
def exit_path(bars,policy,vol):
    h={'fixed1':1,'fixed3':3}.get(policy,5);entry=bars[0][0]
    if not np.isfinite(entry) or entry<=0:return h,np.nan,'unknown',False
    risk={'stop3_target6':.03,'stop5_target10':.05,'vol_stop_target':float(np.clip(2*vol,.02,.08))}.get(policy)
    stop=entry*(1-risk) if risk is not None else -np.inf;target=entry*(1+2*risk) if risk is not None else np.inf
    if policy=='trail4':stop=entry*.96
    peak=entry
    for i,(o,hi,lo,c,valid) in enumerate(bars[:h],1):
        if not valid or not np.isfinite([o,hi,lo,c]).all():return h,np.nan,'unknown',False
        if o<=stop:return i,100*(o/entry-1),'gap_stop',False
        if o>=target:return i,100*(target/entry-1),'gap_target',False
        if lo<=stop:return i,100*(stop/entry-1),'stop',bool(hi>=target)
        if hi>=target:return i,100*(target/entry-1),'target',False
        if policy=='trail4':peak=max(peak,hi);stop=peak*.96
        if i==h:return i,100*(c/entry-1),'scheduled',False
    raise ValueError('short path')
def simulate(signals,calendar,policy,cap,fee,stress):
    grouped={int(k):list(v.itertuples()) for k,v in signals.groupby('seq')};cash=100.;pos=[];trades=unknown=dup=capacity=gap=ambiguous=0;known=[];duration=[];reasons={}
    for day in calendar:
        for t in grouped.get(day-1,[]):
            if any(p[0]==t.code for p in pos):dup+=1;continue
            entry=t.bars[0][0]
            if cap and np.isfinite(entry) and entry>0 and np.isfinite(t.close0) and entry/t.close0-1>.02:gap+=1;continue
            if len(pos)>=10 or cash<10-1e-9:capacity+=1;continue
            h,gross,why,both=exit_path(t.bars,policy,t.vol20);trades+=1;cash-=10;duration.append(h);ambiguous+=int(both);reasons[why]=reasons.get(why,0)+1
            if np.isfinite(gross):mult=(1+gross/100)*(1-fee)/(1+fee);known.append(100*(mult-1))
            else:unknown+=1;mult=stress
            pos.append((t.code,day+h-1,10*mult))
        cash+=sum(p[2] for p in pos if p[1]==day);pos=[p for p in pos if p[1]!=day]
    assert not pos and trades+dup+capacity+gap==len(signals)
    return dict(candidates=len(signals),trades=trades,unknown=unknown,duplicate_skips=dup,capacity_skips=capacity,gap_skips=gap,ambiguous_stops=ambiguous,terminal_return_pct=cash-100,known_trade_mean_pct=np.mean(known) if known else np.nan,mean_hold=np.mean(duration) if duration else np.nan,stop_exits=reasons.get('stop',0)+reasons.get('gap_stop',0),target_exits=reasons.get('target',0)+reasons.get('gap_target',0))
def attach_bars(s,src,days):
    fields=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','MKTCAP','LIST_SHRS']
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=fields,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any() and sorted(d.BAS_DD.unique())==days
    d=d.rename(columns=dict(zip(fields[3:],['Close','Delta','Open','High','Low','Volume','Cap','Shares'])))
    d=d[d.ISU_CD.isin(s.code.unique())].sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True);d['seq']=d.BAS_DD.map(dict(zip(days,range(len(days)))))
    v=(d.Close>0)&(d.Open>0)&(d.Low>0)&(d.Low<=d[['Open','Close']].min(axis=1))&(d.High>=d[['Open','Close']].max(axis=1))&(d.Volume>0)&(d.Shares>0)&(d.Cap==d.Close*d.Shares)
    d['valid']=v&(d.ISU_CD==d.ISU_CD.shift())&(d.seq.diff()==1)&(d.Close-d.Delta==d.Close.shift())&(d.Shares==d.Shares.shift())&(d.market==d.market.shift())&v.shift(fill_value=False)
    d=d.set_index(['ISU_CD','seq']);s=s.copy();s['close0']=d.Close.reindex(pd.MultiIndex.from_arrays([s.code,s.seq])).to_numpy();a=[]
    for i in range(1,6):
        b=d[['Open','High','Low','Close','valid']].reindex(pd.MultiIndex.from_arrays([s.code,s.seq+i]));b['valid']=b.valid.fillna(False).astype(bool);a.append(b.to_numpy(dtype=float))
    s['bars']=[tuple(tuple(b[i]) for b in a) for i in range(len(s))]
    for h in (1,3,5):
        actual=np.array([exit_path(b,'fixed'+str(h),v)[1] for b,v in zip(s.bars,s.vol20)])
        assert np.allclose(actual,s[f'r{h}'],equal_nan=True),f'fixed{h} mismatch'
    return s
def run(cache,src,out):
    for p,digest in [(cache,'af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'),(src,'b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753')]:
        with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==digest
    with cache.open('rb') as f:q,days=pickle.load(f)
    assert len(q)==966400 and not q.duplicated(['day','code']).any()
    q['seq']=q.day.map(dict(zip(days,range(len(days)))));choices=[]
    for start,end in FOLDS:
        te=q[(q.seq>=start)&(q.seq+5<=end)];c=te.sort_values(['day','turnover_log','code'],ascending=[True,False,True]).groupby('day').head(2).copy();c['start']=start;c['end']=end;choices.append(c[['day','code','seq','vol20','r1','r3','r5','start','end']])
    signals=attach_bars(pd.concat(choices,ignore_index=True),src,days);assert len(signals)==692;rows=[]
    for (start,end),g in signals.groupby(['start','end']):
        for policy in POLICIES:
            for cap in (False,True):
                for fee in (.003,.006):
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],policy=policy,gap_cap=cap,fee_bps=round(fee*20000),unknown_multiplier=stress,**simulate(g,range(start,end+1),policy,cap,fee,stress)))
    m=pd.DataFrame(rows);assert len(m)==224;m.to_csv(out,index=False);print(m.query('fee_bps==120').groupby(['policy','gap_cap','unknown_multiplier'])[['terminal_return_pct','trades','unknown','ambiguous_stops']].mean().to_string(),flush=True)
if __name__=='__main__':run(*map(Path,sys.argv[1:4]))
