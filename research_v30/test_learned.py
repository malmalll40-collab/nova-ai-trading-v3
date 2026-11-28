import unittest
import numpy as np
import pandas as pd
from research_v30.features import features
from research_v30.learned import FEATURES,fit_scores,compare

class Tests(unittest.TestCase):
    def test_causal_features(self):
        c=100+np.arange(90)*.1+np.sin(np.arange(90))
        x=pd.DataFrame(dict(Close=c,Open=c,High=c+1,Low=c-1,Turnover=100+np.arange(90)))
        a=features(x)
        x.loc[71:,["Close","Open","High","Low","Turnover"]]*=2
        b=features(x)
        cols=[k for k in a if not k.startswith("gross")]
        pd.testing.assert_frame_equal(a.loc[:70,cols],b.loc[:70,cols])
        self.assertTrue(b.gross5.tail(5).isna().all())
        self.assertAlmostEqual(a.gross1.iloc[30],0)
    def fixture(self):
        n=240
        r=np.random.default_rng(42)
        q=pd.DataFrame(r.normal(size=(n,len(FEATURES))),columns=FEATURES)
        q=q.assign(day=[str(i//4) for i in range(n)],code=[str(i%4) for i in range(n)],phase=["development"]*160+["validation"]*80,safe=True,core5=True)
        for h in (1,3,5):
            q[f"gross{h}"]=np.where(q.ret5>0,6.,-3.)
        return q
    def test_holdout_labels_do_not_fit(self):
        q=self.fixture()
        a,_=fit_scores(q,5)
        q.loc[q.phase=="validation","gross5"]=99
        b,_=fit_scores(q,5)
        pd.testing.assert_series_equal(a,b)
    def test_unknown_and_horizons(self):
        q=self.fixture()
        q.loc[q.phase=="validation","gross5"]=np.nan
        m=compare(q)
        self.assertEqual(len(m),36)
        v=m[(m.phase=="validation")&(m.horizon==5)]
        self.assertTrue(v.known.eq(0).all())
        self.assertTrue(v.unknown.eq(v.signals).all())
        self.assertTrue(v.conservative_precision.eq(0).all())
if __name__=="__main__":
    unittest.main()
