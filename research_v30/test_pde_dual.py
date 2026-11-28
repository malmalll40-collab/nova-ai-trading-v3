import unittest
import numpy as np,pandas as pd
from research_v30.pde_dual import select_candidates
class DualTests(unittest.TestCase):
    def setUp(self):
        self.p=pd.DataFrame(dict(day=['a','a','a','b','b'],code=['A','B','C','A','D'],core=[True,True,True,True,False],turnover_log=[9,8,7,9,10],risk_score=[.7,.2,.1,np.nan,.01]))
    def test_veto_never_promotes_replacement(self):
        t=select_candidates(self.p,'veto_p50',{'veto_p50':.5})
        self.assertEqual(t.code.tolist(),['B'])
    def test_baseline_and_fallback(self):
        b=select_candidates(self.p,'core',{});self.assertEqual(b.code.tolist(),['A','B','A'])
        p=self.p.copy();p['risk_score']=0.
        self.assertEqual(select_candidates(p,'veto_q80',{'veto_q80':1.}).index.tolist(),b.index.tolist())
    def test_outcomes_do_not_change_selection(self):
        a=select_candidates(self.p,'veto_p50',{'veto_p50':.5});p=self.p.assign(r5=-99,mae5=-99)
        self.assertEqual(a.index.tolist(),select_candidates(p,'veto_p50',{'veto_p50':.5}).index.tolist())
