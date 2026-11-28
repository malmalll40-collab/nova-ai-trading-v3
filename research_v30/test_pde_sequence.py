import unittest
import numpy as np
import pandas as pd
try:
    from research_v30 import pde_sequence as m
except ModuleNotFoundError as e:
    if e.name not in ('research_v30','research_v30.pde_sequence'):raise
    import pde_sequence as m

class TestSequence(unittest.TestCase):
    def frame(self):
        days=[f'{i:03}' for i in range(31)];rows=[]
        for i,day in enumerate(days):
            for code in ('A','B'):
                v={f:float(i) for f in m.STATIC};v.update(day=day,code=code,r5=6.,mae5=-1.,turnover_log=100. if code=='A' else 50.,vol20=-float(i),relative20=float(i))
                rows.append(v)
        return pd.DataFrame(rows),days
    def test_sequence_exact_and_missing(self):
        q,days=self.frame();z,raw=m.prepare(q,days);self.assertEqual(raw,62);self.assertEqual(len(z),22)
        self.assertTrue(z.reversal_order.eq(1).all());self.assertTrue(z.ret20_d20_10.eq(10).all())
        z,_=m.prepare(q[~(q.day.eq('010')&q.code.eq('A'))],days);self.assertFalse(((z.day=='030')&(z.code=='A')).any())
    def test_future_outcomes_do_not_change_features(self):
        q,days=self.frame();a,_=m.prepare(q,days);q['r5']=-90;q['mae5']=-90;b,_=m.prepare(q,days)
        pd.testing.assert_frame_equal(a[list(m.SEQUENCE)],b[list(m.SEQUENCE)])
        self.assertTrue(m.labels(b).eq(2).all());self.assertTrue(m.labels(a).eq(1).all())
    def test_unknown_and_duplicates(self):
        q,days=self.frame();self.assertEqual(m.labels(pd.DataFrame({'r5':[6.,6.,np.nan,0.],'mae5':[-1.,-5.,-1.,-1.]})).tolist(),[1,2,-1,0])
        with self.assertRaises(ValueError):m.prepare(pd.concat([q,q.iloc[:1]]),days)
if __name__=='__main__':unittest.main()
