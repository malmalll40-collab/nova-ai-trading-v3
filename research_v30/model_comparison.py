"""Offline experiment only; no production imports or order changes.
Run: python -m research_v30.model_comparison PRIVATE_DATA_DIR OUTPUT_DIR
Optional research dependencies: lightgbm, catboost, xgboost.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score,average_precision_score
from research_v30.learned import FEATURES
def compare(q):
    from lightgbm import LGBMClassifier
    from catboost import CatBoostClassifier
    from xgboost import XGBClassifier
    if q.duplicated(["day","code"]).any():
        raise ValueError("DUPLICATE")
    pool=q[q.safe.eq(True)&np.isfinite(q[FEATURES]).all(axis=1)].copy()
    rows=[]
    for h in (1,3,5):
        net=100*((1+pool[f"gross{h}"]/100)*.997/1.003-1)
        tr=pool.phase.eq("development")&net.notna()
        y=net[tr].ge(3).astype(int)
        if len(y)<100 or y.nunique()!=2 or y.value_counts().min()<20:
            raise ValueError("INSUFFICIENT_TRAINING")
        models={
            "logistic":make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=1000,random_state=0)),
            "lightgbm":LGBMClassifier(n_estimators=100,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1),
            "catboost":CatBoostClassifier(iterations=100,learning_rate=.03,depth=3,l2_leaf_reg=5,random_seed=0,thread_count=2,verbose=False,allow_writing_files=False),
            "xgboost":XGBClassifier(n_estimators=100,learning_rate=.03,max_depth=3,min_child_weight=20,reg_lambda=5,random_state=0,n_jobs=2,eval_metric="logloss")
        }
        z=pool.assign(net=net)
        for name,model in models.items():
            model.fit(pool.loc[tr,FEATURES],y)
            z[name]=model.predict_proba(pool[FEATURES])[:,1]
        for phase,g in z.groupby("phase"):
            known=g[g.net.notna()]
            target=known.net.ge(3)
            for name in [*models,"liquidity","core5","early_rise"]:
                p=g[g.core5] if name=="core5" else g
                key=name if name in models else "turnover_log"
                if name=="early_rise":
                    p=p[p.vol20.gt(0)&p.ret5.between(-.03,.03)&p.ret20.between(-.05,.15)&p.relative20.gt(0)].copy()
                    p["proxy"]=p.relative20/p.vol20
                    key="proxy"
                t=p.sort_values(["day",key,"code"],ascending=[True,False,True]).groupby("day").head(2)
                for fee in (.003,.006):
                    a=100*((1+t[f"gross{h}"]/100)*(1-fee)/(1+fee)-1)
                    k=a.dropna()
                    daily=a.fillna(-100).groupby(t.day).sum().reindex(sorted(q[q.phase==phase].day.unique()),fill_value=0)/2
                    rows.append(dict(h=h,phase=phase,model=name,fee_bps=round(fee*20000),eligible=len(g),eligible_known=len(known),base_rate=target.mean(),auc=roc_auc_score(target,known[name]) if name in models and target.nunique()==2 else np.nan,ap=average_precision_score(target,known[name]) if name in models and target.nunique()==2 else np.nan,signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean()))
    return pd.DataFrame(rows)

if __name__=="__main__":
    from research_v30.dataset import load
    out=Path(sys.argv[2])
    out.mkdir(parents=True,exist_ok=True)
    compare(load(Path(sys.argv[1]))).to_csv(out/"model_comparison.csv",index=False)
    print("HOLD: reviewed history is not independent prospective evidence.")
