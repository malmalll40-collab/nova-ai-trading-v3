import unittest
import numpy as np
import pandas as pd
from engine import feature_frame
from core_comparison import _core_at
from research_v30.pde_core_compare import core_mask

class CoreParityTests(unittest.TestCase):
    def test_original_rule_parity(self):
        for seed in range(5):
            rng=np.random.default_rng(seed)
            dates=pd.bdate_range('2024-01-01',periods=240)
            c=100*np.exp(np.cumsum(rng.normal(.002,.018,len(dates))))
            market=100*np.exp(np.cumsum(rng.normal(.001,.009,len(dates))))
            bars=pd.DataFrame({'Open':c,'High':c*1.012,'Low':c*.988,'Close':c,'Volume':rng.integers(1000,5000,len(c))},index=dates)
            f=feature_frame(bars,'스윙')
            f['ema50_lag5']=f.ema50.shift(5);f['prev_close']=f.Close.shift()
            p=pd.Series(market,index=dates).shift()
            f['idx_close']=p;f['idx_ma20']=p.rolling(20).mean();f['idx_ma50']=p.rolling(50).mean()
            f['idx_ret20']=p/p.shift(20)-1;f['idx_vol20']=p.pct_change(fill_method=None).rolling(20).std(ddof=0)
            lookup=dict(zip(dates.strftime('%Y-%m-%d'),market))
            actual=[_core_at(f,i,lookup)['signal']=='BUY' for i in range(65,len(f))]
            np.testing.assert_array_equal(core_mask(f).iloc[65:].to_numpy(),actual)
    def test_risk_veto(self):
        f=pd.DataFrame([dict(Close=105,atr=3,idx_close=110,idx_ma20=105,idx_ma50=100,idx_ret20=.02,idx_vol20=.01,ema20=100,ema50=95,ema50_lag5=94,ret20=.08,rsi=65,p_high20=103,vol_ratio=1.2,Low=102,prev_close=104,Volume=1000)])
        self.assertTrue(core_mask(f).iloc[0])
        f['Volume']=0
        self.assertFalse(core_mask(f).iloc[0])
