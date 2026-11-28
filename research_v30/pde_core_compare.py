"""PDE9: 5-Core fixed-rule reconstruction; not live recommendation history."""
import hashlib,pickle,sys,zipfile
from pathlib import Path
import numpy as np,pandas as pd
from research_v30.pde_sequence import prepare,labels,net,FOLDS,H
from research_v30.pde_regime import index_feature,classify,eligible
from research_v30.pde_generator import scores

def core_mask(f):
    # core_comparison._core_at and engine.feature_frame at 012a5a5.
    px=f.Close;atr=f.atr
    market=(f.idx_close>f.idx_ma20)&(f.idx_ma20>f.idx_ma50)&(f.idx_ret20>=0)&(f.idx_vol20<=.04)
    trend=(px>f.ema20)&(f.ema20>f.ema50)&(f.ema50>f.ema50_lag5)
    strength=(f.ret20>0)&(f.ret20-f.idx_ret20>0)&f.rsi.between(45,78)
    entry=((px>f.p_high20)&(f.vol_ratio>=1.1))|((f.Low<=f.ema20+.45*atr)&(px>=f.ema20)&(px>f.prev_close)&(f.vol_ratio>=.85))
    risk=(atr/px).between(.003,.08)&(f.Volume>0)&(px<=f.ema20+2.5*atr)
    return market&trend&risk&(strength|entry)

def reconstruct(src,root,days,codes):
    fields=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','MKTCAP','LIST_SHRS']
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=fields,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any() and sorted(d.BAS_DD.unique())==days
    d=d[d.ISU_CD.isin(codes)].rename(columns=dict(zip(fields[3:],['Close','Delta','Open','High','Low','Volume','Cap','Shares']))).sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True);d['seq']=d.BAS_DD.map(dict(zip(days,range(len(days)))))
    valid=(d.Close>0)&(d.Open>0)&(d.Low>0)&(d.Low<=d[['Open','Close']].min(axis=1))&(d.High>=d[['Open','Close']].max(axis=1))&(d.Volume>0)&(d.Shares>0)&(d.Cap==d.Close*d.Shares)
    boundary=(d.ISU_CD!=d.ISU_CD.shift())|(d.seq.diff()!=1)|(d.Close-d.Delta!=d.Close.shift())|(d.Shares!=d.Shares.shift())|(d.market!=d.market.shift())|~valid|~valid.shift(fill_value=False)
    d['seg']=boundary.cumsum();d=d[valid].reset_index(drop=True);g=d.groupby('seg',sort=False)
    def roll(s,n,op):return getattr(s.groupby(d.seg,sort=False).rolling(n),op)().reset_index(level=0,drop=True).reindex(d.index)
    def ewm(s,**kw):return s.groupby(d.seg,sort=False).ewm(adjust=False,**kw).mean().reset_index(level=0,drop=True).reindex(d.index)
    c=d.Close.astype(float);d['prev_close']=g.Close.shift();d['ema20']=ewm(c,span=20);d['ema50']=ewm(c,span=50);d['ema50_lag5']=d.ema50.groupby(d.seg).shift(5)
    diff=c-d.prev_close;gain=ewm(diff.clip(lower=0),alpha=1/14);loss=ewm(-diff.clip(upper=0),alpha=1/14);rs=gain/loss.replace(0,np.nan);d['rsi']=(100-100/(1+rs)).where(rs.notna(),pd.Series(np.where(gain>0,100,50),index=d.index))
    d['atr']=roll(pd.concat([d.High-d.Low,(d.High-d.prev_close).abs(),(d.Low-d.prev_close).abs()],axis=1).max(axis=1),14,'mean');d['p_high20']=roll(g.High.shift(),20,'max');d['ret20']=c/g.Close.shift(20)-1;d['vol_ratio']=d.Volume/roll(g.Volume.shift(),20,'mean').replace(0,np.nan)
    d=d[g.cumcount()>=65].copy();idx=[]
    for market,names in [('KOSPI',('data_0200_20261127.xlsx',)),('KOSDAQ',('data_0622_20261127.xlsx','data_2426_20261127.xlsx'))]:
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();p=a['종가'].shift(1);idx.append(pd.DataFrame({'BAS_DD':p.index,'market':market,'idx_close':p.values,'idx_ma20':p.rolling(20).mean().values,'idx_ma50':p.rolling(50).mean().values,'idx_ret20':(p/p.shift(20)-1).values,'idx_vol20':p.pct_change(fill_method=None).rolling(20).std(ddof=0).values}))
    d=d.merge(pd.concat(idx),on=['BAS_DD','market'],validate='many_to_one');d['core']=core_mask(d);return d[['BAS_DD','ISU_CD','core']].rename(columns={'BAS_DD':'day','ISU_CD':'code'})

def compare(z,days,flags,idx):
    from research_v30.regime_portfolio import portfolio
    z=z.merge(flags,on=['day','code'],validate='one_to_one',how='left');assert z.core.notna().all();z['label']=labels(z);z['index20']=z.day.map(idx);z['regime']=classify(z.index20);rows=[];counts=[]
    for start,end in FOLDS:
        tr=z[(z.seq+H<start)&z.label.ge(0)];te=z[(z.seq>=start)&(z.seq+H<=end)].copy();assert tr.seq.max()+H<te.seq.min() and eligible(tr);te=scores(tr,te)
        for scope in (100,300):
            p=te[te['rank']<=scope];core=p[p.core];non=p[~p.core];counts.append(dict(fold_start=days[start],scope=scope,eligible=len(p),core_signals=len(core),clean_total=int(p.label.eq(1).sum()),clean_core=int(core.label.eq(1).sum()),clean_missed=int(non.label.eq(1).sum()),unknown=int(p.label.eq(-1).sum())))
            def top(x,key,n=2):return x.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(n)
            variants={'liquidity':top(p,'turnover_log'),'core':top(core,'turnover_log'),'core_pde_rank':top(core,'blend'),'pde':top(p,'blend'),'core_plus_pde':pd.concat([top(core,'turnover_log',1),top(non,'blend',1)])}
            for rule,t in variants.items():
                assert not t.duplicated(['day','code']).any() and t.groupby('day').size().le(2).all()
                for fee in (.003,.006):
                    k=t[t.label.ge(0)];n=net(k.r5,fee);clean=n.ge(3)&k.mae5.gt(-5);adverse=n.le(-5)|k.mae5.le(-5)
                    for stress in (0.,1.):rows.append(dict(fold_start=days[start],fold_end=days[end],scope=scope,rule=rule,fee_bps=round(fee*20000),unknown_multiplier=stress,candidates=len(t),clean_precision=clean.sum()/len(t) if len(t) else np.nan,adverse_rate=adverse.mean(),**portfolio(t,days[start:end+1],H,fee,stress)))
        print('fold',days[start],flush=True)
    return pd.DataFrame(rows),pd.DataFrame(counts)
if __name__=='__main__':
    cache,root,out=map(Path,sys.argv[1:4]);src=root/'NOVA_KRX_20231126_20261125.zip'
    for p,h in [(cache,'af57d58f8438865d6613d817e7e64d90d08675d160a15947573c5a16833cde5c'),(src,'b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753')]:
        with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==h
    with cache.open('rb') as f:q,days=pickle.load(f)
    z,_=prepare(q,days);print('matched',len(z),'symbols',z.code.nunique(),flush=True)
    flags=reconstruct(src,root,days,z.code.unique());print('reconstructed',len(flags),'buy',flags.core.sum(),flush=True)
    m,c=compare(z,days,flags,index_feature(root,days));assert len(m)==160 and len(c)==8
    m.to_csv(out/'nova_pde_core_compare.csv',index=False);c.to_csv(out/'nova_pde_core_coverage.csv',index=False);print(m.query('fee_bps==120').groupby(['scope','rule','unknown_multiplier'])[['terminal_return_pct','clean_precision','adverse_rate','trades']].mean().to_string());print(c.groupby('scope').sum(numeric_only=True).to_string())
