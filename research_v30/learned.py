"""Offline fixed development-only logistic models; scores are not calibrated probabilities."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

FEATURES=["ret5","ret20","relative20","vol20","turnover_log","turnover_ratio","compression","range_position","distance_high","close_location"]
HORIZONS=(1,3,5)
TARGET=3.0

def fit_scores(q,h):
    if h not in HORIZONS or q.duplicated(["day","code"]).any():
        raise ValueError("INVALID_COHORT")
    finite=np.isfinite(q[FEATURES]).all(axis=1)
    eligible=q.safe.eq(True)&finite
    gross=q[f"gross{h}"]
    net=100*((1+gross/100)*.997/1.003-1)
    train=eligible&q.phase.eq("development")&np.isfinite(net)
    y=net[train].ge(TARGET)
    if len(y)<100 or y.value_counts().min()<20 or y.nunique()!=2:
        raise ValueError("INSUFFICIENT_TRAINING")
    model=make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=1000,random_state=0))
    model.fit(q.loc[train,FEATURES],y)
    score=pd.Series(np.nan,index=q.index,dtype=float)
    score.loc[eligible]=model.predict_proba(q.loc[eligible,FEATURES])[:,1]
    return score,net

def compare(q):
    rows=[]
    for h in HORIZONS:
        score,net=fit_scores(q,h)
        z=q.assign(learned=score,net=net)
        for phase,g in z.groupby("phase"):
            # All ranking strategies see identical feature-complete safe candidates.
            pool=g[g.learned.notna()]
            known=pool[pool.net.notna()]
            y=known.net.ge(TARGET)
            auc=roc_auc_score(y,known.learned) if y.nunique()==2 else np.nan
            ap=average_precision_score(y,known.learned) if y.nunique()==2 else np.nan
            for rule in ("learned","liquidity","core5"):
                p=pool[pool.core5.eq(True)] if rule=="core5" else pool
                key="learned" if rule=="learned" else "turnover_log"
                t=p.sort_values(["day",key,"code"],ascending=[True,False,True]).groupby("day").head(2)
                for fee in (.003,.006):
                    n=100*((1+t[f"gross{h}"]/100)*(1-fee)/(1+fee)-1)
                    a=n.dropna()
                    daily=n.fillna(-100).groupby(t.day).sum().reindex(sorted(g.day.unique()),fill_value=0)/2
                    rows.append(dict(horizon=h,phase=phase,rule=rule,roundtrip_bps=round(fee*20000),eligible=len(pool),eligible_known=len(known),base_rate=y.mean(),auc=auc if rule=="learned" else np.nan,average_precision=ap if rule=="learned" else np.nan,signals=len(t),known=len(a),unknown=len(t)-len(a),rise3_rate=a.ge(TARGET).mean(),conservative_precision=float(a.ge(TARGET).sum()/len(t)) if len(t) else np.nan,mean_net_pct=a.mean(),loss5_rate=a.le(-5).mean(),conservative_slot_mean=daily.mean()))
    return pd.DataFrame(rows)

def main(root,out):
    from research_v30.dataset import load
    q=load(root)
    out.mkdir(parents=True,exist_ok=True)
    compare(q).to_csv(out/"learned_comparison.csv",index=False)
    print("HOLD: no independent prospective evidence; no live signals or orders.")

if __name__=="__main__":
    main(Path(sys.argv[1]),Path(sys.argv[2]))
