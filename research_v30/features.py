"""Completed daily bars, contiguous audited segments only. Offline research."""
import numpy as np
import pandas as pd

def features(x):
    c=x.Close.astype(float)
    t=x.Turnover.astype(float)
    r=c.pct_change(fill_method=None)
    prior_high=x.High.shift(1).rolling(20).max()
    prior_low=x.Low.shift(1).rolling(20).min()
    a=pd.DataFrame(index=x.index)
    a["turnover_ratio"]=t.rolling(3).mean()/t.shift(3).rolling(20).median().replace(0,np.nan)
    a["compression"]=r.rolling(5).std()/r.shift(5).rolling(20).std().replace(0,np.nan)
    a["range_position"]=(c-prior_low)/(prior_high-prior_low).replace(0,np.nan)
    a["distance_high"]=c/prior_high.replace(0,np.nan)-1
    a["close_location"]=(c-x.Low)/(x.High-x.Low).replace(0,np.nan)
    for h in (1,3,5):
        # Unknown future bars stay unknown. Enter next session open, exit h-th close.
        a[f"gross{h}"]=100*(c.shift(-h)/x.Open.shift(-1)-1)
    return a
