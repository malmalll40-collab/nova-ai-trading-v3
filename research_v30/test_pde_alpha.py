import unittest
import numpy as np,pandas as pd
try:
    from research_v30.pde_alpha import excess,market_returns,alpha_labels,choose
except ModuleNotFoundError:
    from pde_alpha import excess,market_returns,alpha_labels,choose
class AlphaTests(unittest.TestCase):
    def test_relative_math_and_entry(self):
        self.assertAlmostEqual(excess(10,5),100*(1.1/1.05-1))
        self.assertAlmostEqual(excess(-2,-2),0.)
        o=pd.Series(range(100,110));c=o+2
        self.assertAlmostEqual(market_returns(o,c).iloc[0],100*(107/101-1))
    def test_label_unknown_and_risk(self):
        p=pd.DataFrame(dict(r5=[10,10,10,np.nan,0],market_r5=[1,9,1,0,np.nan],mae5=[-1,-1,-6,-1,-1]))
        self.assertEqual(alpha_labels(p).tolist(),[1,0,2,-1,-1])
    def test_selection_not_outcome_dependent(self):
        p=pd.DataFrame(dict(day=['a']*3+['b']*2,code=list('ABCDE'),core=[True,True,False,True,False],turnover_log=[5,4,9,5,6],alpha=[0,0,9,0,1],r5=[1]*5))
        b=choose(p,'core');t=choose(p,'fill_alpha');self.assertTrue(set(b.index)<=set(t.index));self.assertEqual(set(t.code),{'A','B','D','E'});self.assertEqual(t.index.tolist(),choose(p.assign(r5=-99),'fill_alpha').index.tolist())
