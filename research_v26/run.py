import sys,json
from pathlib import Path
import numpy as np,pandas as pd
from research_v26.dataset import load
from research_v26.residual import evaluate,gates
if __name__=='__main__':
    root,out=map(Path,sys.argv[1:]);q=load(root);out.mkdir(parents=True,exist_ok=True)
    cols=['relative20_current','residual20','residual5','residual15_skip5'];mask=np.isfinite(q[cols]).all(axis=1);full=evaluate(q);m=evaluate(q[mask]);m.to_csv(out/'comparison.csv',index=False);full.to_csv(out/'all_candidates_sensitivity.csv',index=False)
    ref=pd.read_csv(Path(__file__).with_name('baseline.csv'));a=full[full.strategy=='liquidity'].merge(ref,on=['phase','roundtrip_bps'],suffixes=('_new','_old'),validate='one_to_one');assert len(a)==6 and np.allclose(a.mean_net_pct_new,a.mean_net_pct_old,atol=1e-9,rtol=0) and (a.signals_new==a.signals_old).all() and (a.unknown_new==a.unknown_old).all()
    (out/'summary.json').write_text(json.dumps(dict(eligible=len(q),common=int(mask.sum()),known=int(q.loc[mask,'net_pct'].notna().sum()),parity=6,gate=gates(m),orders_enabled=False,adoption='HOLD',limitations='Market-only residual proxy, indices not independently identified, reviewed history, overlapping hypothetical opportunities, not account return.'),indent=2))
