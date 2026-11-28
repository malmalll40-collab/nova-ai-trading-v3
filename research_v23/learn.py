"""Development-only fit. No live adoption, no model exports."""
import sys,json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingRegressor,HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,brier_score_loss
from research_v23.dataset import load,NAMES,FACTORS
FEATURES=NAMES+['factor_'+k for k in FACTORS]+['ret5','ret20','vol20','turnover_log','index_ret20','relative20']
def evaluate(q):
    assert np.isfinite(q[FEATURES]).all().all()
    train=q[(q.phase=='development')&q.net_pct.notna()].copy();X=train[FEATURES];y=train.net_pct
    args=dict(max_iter=60,max_leaf_nodes=15,learning_rate=.05,min_samples_leaf=100,l2_regularization=5,early_stopping=False,random_state=1943)
    models={'linear':make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=500,random_state=1943)).fit(X,y>0),'tree':HistGradientBoostingClassifier(**args).fit(X,y>0),'risk':HistGradientBoostingClassifier(**args).fit(X,y<=-5),'return':HistGradientBoostingRegressor(**args).fit(X,y.clip(-30,30))}
    q=q.copy();X=q[FEATURES];q['linear_p']=models['linear'].predict_proba(X)[:,1];q['tree_p']=models['tree'].predict_proba(X)[:,1];q['risk_p']=models['risk'].predict_proba(X)[:,1];q['pred_ret']=models['return'].predict(X);q['combined']=q.pred_ret-5*q.risk_p
    metrics=[];ranks=[]
    for phase,g in q.groupby('phase'):
        z=g[g.net_pct.notna()];ret=z.net_pct;target=ret>0
        for name,target,p,prior in [('linear',target,z.linear_p,(y>0).mean()),('tree',target,z.tree_p,(y>0).mean()),('risk',ret<=-5,z.risk_p,(y<=-5).mean())]:
            metrics.append(dict(phase=phase,model=name,known=len(z),auc=roc_auc_score(target,p),brier=brier_score_loss(target,p),baseline_brier=np.mean((target.astype(float)-prior)**2)))
        for name,score,mask in [('liquidity','turnover_log',g.safe),('roles13','turnover_log',g.safe&g.roles13),('core5','turnover_log',g.safe&g.core5),('linear','linear_p',g.safe),('tree','tree_p',g.safe),('expected_return','pred_ret',g.safe),('combined','combined',g.safe)]:
            chosen=g[mask].sort_values(['day',score,'code'],ascending=[True,False,True]).groupby('day').head(2);a=chosen.net_pct.dropna();daily=chosen.assign(pess=chosen.net_pct.fillna(-100),neutral=chosen.net_pct.fillna(0)).groupby('day')[['pess','neutral']].sum().reindex(sorted(g.day.unique()),fill_value=0)/2
            ranks.append(dict(phase=phase,strategy=name,signals=len(chosen),known=len(a),unknown=len(chosen)-len(a),mean_net_pct=a.mean(),win_pct=a.gt(0).mean()*100,loss5_pct=a.le(-5).mean()*100,daily_slot_mean_unknown_zero=daily.neutral.mean(),daily_slot_mean_unknown_minus100=daily.pess.mean()))
    return pd.DataFrame(metrics),pd.DataFrame(ranks),len(train)
if __name__=='__main__':
    root,out=map(Path,sys.argv[1:]);q=load(root);m,r,n=evaluate(q);out.mkdir(exist_ok=True);m.to_csv(out/'prediction_metrics.csv',index=False);r.to_csv(out/'ranking_comparison.csv',index=False);(out/'summary.json').write_text(json.dumps({'training_rows':n,'eligible':len(q),'known':int(q.net_pct.notna().sum()),'features':FEATURES,'adoption':'HOLD','orders_enabled':False,'limitations':'Previously reviewed history, no independent test, uncalibrated scores, conditional on resolved labels. Top-two daily overlapping hypothetical opportunities, not portfolio return. Unknown outcomes retained in sensitivity.'},indent=2));print(m.to_string(index=False));print(r.to_string(index=False))
