"""Paired historical ablation; no runtime or order changes."""
import sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
from research_v30.full_market import load,F,P
EXTRA=['turnover_ratio5','turnover_ratio10','turnover_ratio20']+[f'rank_{k}{suffix}' for k in ('ret5','relative20','turnover_ratio','turnover_log') for suffix in ('','_change5')]
def compare(q,days):
    q=q[q.phase.isin(['development','validation']) & np.isfinite(q[F+EXTRA]).all(axis=1)].copy()
    assert not q.duplicated(['day','code']).any()
    net=100*((1+q.gross1/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();y=net[tr].ge(3).astype(int)
    for name,cols in [('base',F),('turnover',F+EXTRA[:3]),('rank',F+EXTRA[3:]),('both',F+EXTRA)]:
        q[name]=LGBMClassifier(**P).fit(q.loc[tr,cols],y).predict_proba(q[cols])[:,1]
    rows=[]
    for phase,phase_days in [('development',days[65:359]),('validation',days[364:541])]:
        p=q[q.phase.eq(phase)];known=p[p.gross1.notna()];label=100*((1+known.gross1/100)*.997/1.003-1)>=3
        for rule in ['liquidity','base','turnover','rank','both']:
            key='turnover_log' if rule=='liquidity' else rule
            t=p.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
            for fee in (.003,.006):
                n=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1);k=n.dropna();daily=n.fillna(-100).groupby(t.day).sum().reindex(phase_days,fill_value=0)/2
                rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),eligible=len(p),eligible_known=len(known),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean(),auc=roc_auc_score(label,known[rule]) if rule!='liquidity' else np.nan,ap=average_precision_score(label,known[rule]) if rule!='liquidity' else np.nan,base_rate=label.mean()))
    return pd.DataFrame(rows)
if __name__=='__main__':
    q,meta,days=load(Path(sys.argv[1]),extended=True);print(meta,flush=True);m=compare(q,days);out=Path(sys.argv[2]);out.mkdir(exist_ok=True,parents=True);m.to_csv(out/'nova_v30_rank_turnover.csv',index=False);print(m.to_string(index=False),flush=True)
