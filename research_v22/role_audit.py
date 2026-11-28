"""Offline vectorized raw swing role audit. Not a live recommendation or probability."""
import numpy as np
import pandas as pd

NAMES=['ema12_26','ema20_50','macd','rsi','breakout20','momentum5','momentum20','volume_direction','bollinger','channel50']
FACTORS=['market','trend','strength','entry','risk']
def comp(x,t):return (x>t).astype(int)-(x<-t).astype(int)
def votes(g):
    c,h,l,v=g.Close,g.High,g.Low,g.Volume
    ema=lambda n:c.ewm(span=n,adjust=False).mean()
    e12,e26,e20,e50=[ema(n) for n in (12,26,20,50)]
    macd=e12-e26;ms=macd.ewm(span=9,adjust=False).mean()
    d=c.diff();gain=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();loss=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean();rs=gain/loss.replace(0,np.nan)
    rsi=(100-100/(1+rs)).where(rs.notna(),pd.Series(np.where(gain>0,100,50),index=g.index))
    atr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1).rolling(14,min_periods=14).mean()
    ph=h.shift().rolling(20).max();pl=l.shift().rolling(20).min();r5=c.pct_change(5);r20=c.pct_change(20)
    vr=v/v.shift().rolling(20).mean().replace(0,np.nan);r1=c.pct_change();sma=c.rolling(20).mean();std=c.rolling(20).std(ddof=0)
    a=pd.DataFrame(dict(zip(NAMES,[comp(e12/e26-1,.003),comp(e20/e50-1,.0048),comp((macd-ms)/c,.0009),np.select([rsi.between(53,73),(rsi<45)|(rsi>82)],[1,-1],default=0),(c>ph).astype(int)-(c<pl).astype(int),comp(r5,.012),comp(r20,.035),comp(r1,.002).where(vr>1.4,0),(c<sma-2*std).astype(int)-(c>sma+2*std).astype(int),(c>h.shift().rolling(50).max()).astype(int)-(c<l.shift().rolling(50).min()).astype(int)])),index=g.index)
    veto=(~np.isfinite(atr/c))|(atr/c<=0)|(atr/c>.10)
    z=pd.DataFrame({'trend':(c>e20)&(e20>e50)&(e50>e50.shift(5)),'strength':(r20>0)&(r20>g.index_ret20)&rsi.between(45,78),'entry':((c>ph)&(vr>=1.1))|((l<=e20+.45*atr)&(c>=e20)&(c>c.shift())&(vr>=.85)),'risk':(atr/c>=.003)&(atr/c<=.08)&(v>0)&(c<=e20+2.5*atr),'market':g.market_ok},index=g.index)
    return a,veto,z
def buy(a,veto,review=True):
    b=a.eq(1).sum(axis=1);s=a.eq(-1).sum(axis=1)
    if review:b,s=b+((b>=4)&(b>s)),s+((s>=4)&(s>b))
    return (~veto)&(b>=6)&(b-s>=3)&~((s>=6)&(s-b>=3))
def core(z,omit=None):
    z=z.copy()
    if omit in ('market','trend','risk'):z[omit]=True
    if omit in ('strength','entry'):z[omit]=False
    return z.market&z.trend&z.risk&(z.strength|z.entry)
def signals(g):
    a,v,z=votes(g);b=buy(a,v);out={'roles13':b,'roles10_no_reviewers':buy(a,v,False),'core5':core(z)}
    out.update({'omit_role_'+k:buy(a.drop(columns=k),v) for k in NAMES})
    out.update({'omit_core_'+k:core(z,k) for k in FACTORS})
    return pd.DataFrame(out,index=g.index),a
def evaluate(q):
    rows=[]
    for phase,p in q.groupby('phase',sort=False):
        for k in ['roles13','roles10_no_reviewers','core5']+['omit_role_'+x for x in NAMES]+['omit_core_'+x for x in FACTORS]:
            base='core5' if k.startswith('omit_core_') else 'roles13';v=p[k];b=p[base];net=p.net_pct;chosen=net[v];known=chosen.dropna();added=net[v&~b];blocked=net[b&~v]
            daily=(v.astype(int)*net.fillna(-100)).groupby(p.day).mean()
            rows.append(dict(phase=phase,strategy=k,eligible=len(p),signals=int(v.sum()),known=len(known),unknown=int(chosen.isna().sum()),win_pct=float(known.gt(0).mean()*100) if len(known) else None,mean_net_pct=float(known.mean()) if len(known) else None,loss5_pct=float(known.le(-5).mean()*100) if len(known) else None,added_wins=int(added.gt(0).sum()),added_losses=int(added.le(0).sum()),added_unknown=int(added.isna().sum()),blocked_wins=int(blocked.gt(0).sum()),blocked_losses=int(blocked.le(0).sum()),blocked_unknown=int(blocked.isna().sum()),daily_opportunity_pess_mean=float(daily.mean())))
    return pd.DataFrame(rows)
