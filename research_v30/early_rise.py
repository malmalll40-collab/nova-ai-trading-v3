"""Research only. Fixed predeclared proxies on v23 cohort, no orders."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

RULES=("liquidity","core5","early_rise")
def select(q, rule):
    if rule not in RULES:
        raise ValueError("RULE")
    z=q.loc[q.safe.eq(True)].copy()
    if rule=="core5":
        z=z.loc[z.core5.eq(True)]
    if rule=="early_rise":
        cols=["ret5","ret20","relative20","vol20","turnover_log"]
        z=z.loc[np.isfinite(z[cols]).all(axis=1)&z.vol20.gt(0)&z.ret5.between(-.03,.03)&z.ret20.between(-.05,.15)&z.relative20.gt(0)]
        # Quiet recent price action with positive relative strength; not a probability.
        z["score"]=z.relative20/z.vol20
    else:
        z=z.loc[np.isfinite(z.turnover_log)]
        z["score"]=z.turnover_log
    return z.sort_values(["day","score","code"],ascending=[True,False,True]).groupby("day").head(2)

def evaluate(q):
    if q.duplicated(["day","code"]).any():
        raise ValueError("DUPLICATE")
    rows=[]
    for phase,g in q.groupby("phase"):
        for rule in RULES:
            t=select(g,rule)
            for fee in (.003,.006):
                net=((1+t.net_pct/100)*(1+.003)/(1-.003)*(1-fee)/(1+fee)-1)*100
                known=net.dropna()
                daily=net.fillna(-100).groupby(t.day).sum().reindex(sorted(g.day.unique()),fill_value=0)/2
                rows.append(dict(phase=phase,rule=rule,roundtrip_bps=round(fee*20000),signals=len(t),known=len(known),unknown=len(t)-len(known),positive_rate=known.gt(0).mean(),rise3_rate=known.ge(3).mean(),mean_net_pct=known.mean(),loss5_rate=known.le(-5).mean(),conservative_slot_mean=daily.mean()))
    return pd.DataFrame(rows)

def main(root,out):
    from research_v23.dataset import load
    q=load(root)
    m=evaluate(q)
    out.mkdir(parents=True,exist_ok=True)
    m.to_csv(out/"early_rise_comparison.csv",index=False)
    print(m.to_string(index=False))
    print("HOLD: reviewed history only; independent prospective validation required.")

if __name__=="__main__":
    main(Path(sys.argv[1]),Path(sys.argv[2]))
