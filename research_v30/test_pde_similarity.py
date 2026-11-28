import unittest
import numpy as np,pandas as pd
try:
    from research_v30 import pde_similarity as m
except ModuleNotFoundError as e:
    if e.name not in ('research_v30','research_v30.pde_similarity'):raise
    import pde_similarity as m
class SimilarityTests(unittest.TestCase):
    def test_exact_lag_and_future_independence(self):
        days=[f'{i:03}' for i in range(66)]
        q=pd.DataFrame([dict(day=d,code='A',turnover_log=1.,r5=6.,mae5=-1.,**{f:float(i) for f in m.FEATURES}) for i,d in enumerate(days)])
        a,_=m.prepare(q,days);b,_=m.prepare(q.assign(r5=-90.,mae5=-90.),days)
        self.assertEqual(len(a),6);self.assertEqual(a.iloc[0]['ret5_lag60'],0.)
        pd.testing.assert_frame_equal(a[m.cols(m.LAGS)],b[m.cols(m.LAGS)])
        a,_=m.prepare(q[q.day!='000'],days);self.assertFalse(a.day.eq('060').any())
    def test_neighbors_and_embargo(self):
        tr=pd.DataFrame(dict(seq=[0,1,2,3],label=[1,1,2,0],x=[0.,.1,10.,11.]));te=pd.DataFrame(dict(seq=[9],x=[.05],label=[2]))
        p,r,d=m.analogs(tr,te,['x'],k=2);self.assertEqual((p[0],r[0]),(1.,0.));self.assertTrue(d[0]>=0)
        p2,r2,_=m.analogs(tr,te.assign(label=0),['x'],k=2);np.testing.assert_array_equal(p,p2);np.testing.assert_array_equal(r,r2)
        with self.assertRaises(ValueError):m.analogs(tr,te.assign(seq=8),['x'],k=2)
        with self.assertRaises(ValueError):m.analogs(tr.assign(label=-1),te,['x'],k=2)
    def test_unknown_and_duplicate(self):
        z=pd.DataFrame({'r5':[6.,6.,np.nan,0.],'mae5':[-1.,-5.,-1.,-1.]});self.assertEqual(m.labels(z).tolist(),[1,2,-1,0])
        with self.assertRaises(ValueError):m.prepare(pd.DataFrame({'day':['001','001'],'code':['A','A']}),['001'])
if __name__=='__main__':unittest.main()
