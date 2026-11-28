import unittest
import numpy as np
import pandas as pd
from research_v30.early_rise import select,evaluate

class Tests(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame(dict(day=["20240102"]*5,code=list("ABCDE"),phase=["development"]*5,safe=[True,True,False,True,True],core5=[True]*5,ret5=[.01,.02,.01,.2,.01],ret20=[.05,.06,.05,.3,.04],relative20=[.04,.03,.5,.2,.02],vol20=[.01,.02,.01,.01,.02],turnover_log=[10,11,12,13,14],net_pct=[np.nan,4,100,20,1]))
    def test_selection(self):
        self.assertEqual(select(self.fixture(),"early_rise").code.tolist(),["A","B"])
    def test_label_independence(self):
        q=self.fixture()
        q.net_pct=[-99,99,0,0,100]
        self.assertEqual(select(q,"early_rise").code.tolist(),["A","B"])
    def test_cost_unknown(self):
        m=evaluate(self.fixture())
        a=m[m.rule=="early_rise"].sort_values("roundtrip_bps")
        self.assertEqual(a.unknown.tolist(),[1,1])
        self.assertLess(a.mean_net_pct.iloc[1],a.mean_net_pct.iloc[0])
        self.assertAlmostEqual(a.conservative_slot_mean.iloc[0],-48)
    def test_duplicates(self):
        q=self.fixture()
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            evaluate(pd.concat([q,q]))
if __name__=="__main__":
    unittest.main()
