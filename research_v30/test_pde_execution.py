import unittest
import numpy as np,pandas as pd
from research_v30.pde_execution import exit_path,simulate

class ExecutionTests(unittest.TestCase):
    def test_both_stop_first(self):
        h,r,why,both=exit_path([(100,107,96,105,True)]*5,'stop3_target6',.02)
        self.assertEqual((h,why,both),(1,'stop',True));self.assertAlmostEqual(r,-3)
    def test_gap_stop(self):
        b=[(100,102,99,101,True),(90,95,89,94,True)]*3
        h,r,why,_=exit_path(b,'stop3_target6',.02);self.assertEqual((h,why),(2,'gap_stop'));self.assertAlmostEqual(r,-10)
    def test_trailing_prior_high(self):
        b=[(100,110,99,109,True),(108,109,104,106,True)]*3
        h,r,why,_=exit_path(b,'trail4',.02);self.assertEqual((h,why),(2,'stop'));self.assertAlmostEqual(r,5.6)
    def test_unknown_and_early_exit(self):
        b=[(100,107,99,105,True)]+[(np.nan,0,0,0,False)]*4
        self.assertEqual(exit_path(b,'stop3_target6',.02)[2],'target');self.assertTrue(np.isnan(exit_path(b,'fixed5',.02)[1]))
    def test_gap_skip_and_duplicate(self):
        b=[(105,106,104,105,True)]*5
        q=pd.DataFrame([dict(seq=i,code='A',bars=b,close0=100,vol20=.02) for i in (0,1)])
        a=simulate(q,range(7),'fixed5',True,.006,0);self.assertEqual((a['gap_skips'],a['trades']),(2,0))
        a=simulate(q,range(7),'fixed5',False,.006,0);self.assertEqual((a['duplicate_skips'],a['trades']),(1,1))
if __name__=='__main__':unittest.main()
