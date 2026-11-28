"""Exploratory fixed 5-day reversal on the v22/v23 cohort; no production signals."""
import sys,json
from pathlib import Path
import numpy as np,pandas as pd
RULES=('liquidity','roles13','core5','reversal5','reversal5_volscaled')
def select(q,rule):
    if rule not in RULES:raise ValueError('RULE')
    z=q[q.safe].copy()
    if rule in ('roles13','core5'):z=z[z[rule]]
    if rule.startswith('reversal'):
        z=z[z.ret5.lt(0)&np.isfinite(z.ret5)]
        if rule=='reversal5_volscaled':
            z=z[z.vol20.gt(0)&np.isfinite(z.vol20)]
            z['rank_score']=-z.ret5/z.vol20
        else:z['rank_score']=-z.ret5
    else:z['rank_score']=z.turnover_log
    return z.sort_values(['day','rank_score','code'],ascending=[True,False,True]).groupby('day',sort=False).head(2)
def evaluate(q):
    if q.duplicated(['day','code']).any():raise ValueError('DUPLICATE')
    rows=[]
    for phase,g in q.groupby('phase',sort=False):
        for rule in RULES:
            t=select(g,rule)
            for fee in (.003,.006):
                net=((1+t.net_pct/100)*(1+.003)/(1-.003)*(1-fee)/(1+fee)-1)*100
                a=net.dropna();daily=t.assign(neutral=net.fillna(0),pess=net.fillna(-100)).groupby('day')[['neutral','pess']].sum().reindex(sorted(g.day.unique()),fill_value=0)/2
                rows.append(dict(phase=phase,strategy=rule,roundtrip_bps=round(fee*20000),signal_days=t.day.nunique(),signals=len(t),known=len(a),unknown=len(t)-len(a),mean_net_pct=a.mean(),win_pct=100*a.gt(0).mean(),loss5_pct=100*a.le(-5).mean(),daily_slot_mean_unknown_zero=daily.neutral.mean(),daily_slot_mean_unknown_minus100=daily.pess.mean()))
    return pd.DataFrame(rows)
def main(root,out):
    from research_v23.dataset import load
    q=load(root);assert len(q)==47603 and q.net_pct.notna().sum()==46946
    m=evaluate(q);out.mkdir(exist_ok=True,parents=True);m.to_csv(out/'reversal_comparison.csv',index=False)
    baseline=pd.read_csv(Path(__file__).with_name('v23_reference.csv'));a=m[(m.roundtrip_bps==60)&m.strategy.isin(['liquidity','roles13','core5'])].merge(baseline,on=['phase','strategy'],suffixes=('_new','_old'),validate='one_to_one');assert len(a)==9 and np.allclose(a.mean_net_pct_new,a.mean_net_pct_old,atol=1e-9,rtol=0) and (a.signals_new==a.signals_old).all() and (a.unknown_new==a.unknown_old).all()
    gates={}
    for rule in RULES[3:]:
        a=m[m.strategy==rule].merge(m[m.strategy=='liquidity'],on=['phase','roundtrip_bps'],suffixes=('_new','_base'));a=a[a.phase.isin(['development','validation'])]
        checks={'positive_conservative_daily':bool((a.daily_slot_mean_unknown_minus100_new>0).all()),'better_than_liquidity_conservative_daily':bool((a.daily_slot_mean_unknown_minus100_new>a.daily_slot_mean_unknown_minus100_base).all()),'min_known_30':bool((a.known_new>=30).all()),'loss5_not_worse':bool((a.loss5_pct_new<=a.loss5_pct_base).all())};gates[rule]={**checks,'pass':all(checks.values())}
    (out/'summary.json').write_text(json.dumps(dict(eligible=len(q),known=int(q.net_pct.notna().sum()),baseline_parity=9,gate=gates,adoption='HOLD',orders_enabled=False,independent_future_verified=False,limitations='Fixed first-day 100-symbol v23 universe, 65-bar contiguous segments, prior market session, 5-day next-open-to-close opportunities. Unknown labels retained, -100% sensitivity is not forecast. Already reviewed history; not a portfolio. No verified corporate adjustment or actual fills.'),indent=2));print(m.to_string(index=False));print(gates)
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
