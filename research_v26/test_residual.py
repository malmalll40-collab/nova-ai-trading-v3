import unittest
import numpy as np
import pandas as pd
from research_v26.residual import features,select,evaluate
class Tests(unittest.TestCase):
    def test_prefix_invariance(self):
        rng=np.random.default_rng(22);m=pd.Series(100*np.exp(np.cumsum(rng.normal(0,.01,120))));c=pd.Series(100*np.exp(np.cumsum(rng.normal(0,.02,120))))
        a=features(c,m);c.iloc[90:]*=2;m.iloc[90:]*=3;b=features(c,m);pd.testing.assert_frame_equal(a.iloc[:90],b.iloc[:90])
    def test_beta_and_residual(self):
        rng=np.random.default_rng(1);m=pd.Series(100*np.exp(np.cumsum(rng.normal(0,.01,100))));c=m**2
        a=features(c,m);self.assertAlmostEqual(a.beta60.iloc[-1],2);self.assertTrue(pd.isna(a.residual20.iloc[-1]))
    def fixture(self):
        return pd.DataFrame(dict(day=['20240102']*4+['20240103'],code=list('ABCDE'),phase=['development']*5,safe=[True,True,False,True,True],turnover_log=[1,2,9,3,4],relative20_current=[1,2,9,3,1],residual20=[1,2,9,3,1],residual5=[-2,-1,-9,1,1],residual15_skip5=[1,2,9,3,1],net_pct=[np.nan,2,99,3,4]))
    def test_hybrid_unknown(self):
        q=self.fixture();self.assertEqual(set(select(q,'hybrid_pullback').code),{'A','B'});r=evaluate(q);a=r[(r.strategy=='hybrid_pullback')&(r.roundtrip_bps==60)].iloc[0];self.assertEqual((a.signals,a.known,a.unknown),(2,1,1));self.assertAlmostEqual(a.daily_slot_mean_unknown_minus100,-24.5)
    def test_rank_independent_of_outcome(self):
        q=self.fixture();a=select(q,'residual20').code.tolist();q.net_pct=0;self.assertEqual(select(q,'residual20').code.tolist(),a)
if __name__=='__main__':unittest.main()
