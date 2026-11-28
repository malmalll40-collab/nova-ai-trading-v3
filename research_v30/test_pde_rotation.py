import unittest
import numpy as np,pandas as pd
try:
    from research_v30.pde_rotation import rotation,choose,EXTRA
except ModuleNotFoundError:
    from pde_rotation import rotation,choose,EXTRA
class RotationTests(unittest.TestCase):
    def test_exact_lag_and_no_future(self):
        days=[str(i) for i in range(12)]
        q=pd.DataFrame([dict(day=d,code=c,ret5=.01,rank_relative20=.9 if i>=5 and c=='A' else .3,turnover_log=np.log1p(100 if c=='A' else 200),r5=1.) for i,d in enumerate(days) for c in ['A','B']])
        z=rotation(q,days);r=z[(z.day=='5')&(z.code=='A')].iloc[0]
        self.assertEqual(r.new_leader,1);self.assertAlmostEqual(r.leader_turnover_share,1/3)
        a=rotation(q[q.day.isin(days[:6])],days);b=z[z.day.isin(days[:6])]
        pd.testing.assert_frame_equal(a.reset_index(drop=True),b.reset_index(drop=True))
        missing=rotation(q[~((q.day=='0')&(q.code=='A'))],days)
        self.assertTrue(np.isnan(missing[(missing.day=='5')&(missing.code=='A')].new_leader.iloc[0]))
    def test_fill_keeps_original(self):
        p=pd.DataFrame(dict(day=['a']*4+['b']*3,code=list('ABCDEFG'),core=[True,True,False,False,True,False,False],turnover_log=[5,4,9,8,5,10,2],rotation=[0,0,9,8,0,1,2],static=[0,0,9,8,0,3,1]))
        b=choose(p,'core');t=choose(p,'fill_rotation');self.assertTrue(set(b.index)<=set(t.index));self.assertEqual(set(t.code),{'A','B','E','G'});self.assertTrue(t.groupby('day').size().le(2).all())
    def test_duplicate_rejected(self):
        p=pd.DataFrame(dict(day=['0','0'],code=['A','A'],ret5=[0,0],rank_relative20=[.5,.5],turnover_log=[1.,1.]))
        with self.assertRaises(ValueError):rotation(p,['0'])
if __name__=='__main__':unittest.main()
