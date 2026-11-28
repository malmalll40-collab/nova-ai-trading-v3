import unittest
import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor
from research_v30.pde import FEATURES, net, paths, discover

class TestPDE(unittest.TestCase):
    def test_cost(self):
        self.assertLess(net(0),0)
        self.assertLess(net(3,.006),net(3,.003))
    def test_paths_partition(self):
        x=pd.DataFrame(np.random.default_rng(1).normal(size=(800,len(FEATURES))),columns=FEATURES)
        m=DecisionTreeRegressor(max_depth=3,min_samples_leaf=30,random_state=0).fit(x,x.ret5)
        for leaf,rule in paths(m).items():
            mask=np.ones(len(x),dtype=bool)
            for f,op,t in rule:
                values=x[f].to_numpy(dtype=np.float32)
                mask &= values<=t if op=='<=' else values>t
            np.testing.assert_array_equal(mask,m.apply(x)==leaf)
    def test_no_future_labels(self):
        rng=np.random.default_rng(42)
        def frame(start,n_days):
            n=n_days*100
            x=pd.DataFrame(rng.normal(size=(n,len(FEATURES))),columns=FEATURES)
            x['seq']=np.repeat(np.arange(start,start+n_days),100);x['day']=x.seq.astype(str);x['r5']=np.where(x.ret5>0,7,-2);x['mae5']=np.where(x.ret5>0,-1,-6);return x
        a,b,c=frame(0,30),frame(35,30),frame(70,10)
        r,s,v=discover(a,b,c)
        self.assertTrue(any(p['accepted'] for p in r))
        c['r5']=-99;c['mae5']=-99
        r2,s2,v2=discover(a,b,c)
        self.assertEqual(r,r2);np.testing.assert_array_equal(s,s2);np.testing.assert_array_equal(v,v2)
        with self.assertRaises(AssertionError):discover(a,b,b)
if __name__=='__main__':unittest.main()
