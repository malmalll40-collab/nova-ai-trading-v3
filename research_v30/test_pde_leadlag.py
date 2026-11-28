import unittest
import numpy as np,pandas as pd
try:
    from research_v30.pde_leadlag import corr_pairs,graph,score
except ModuleNotFoundError:
    from pde_leadlag import corr_pairs,graph,score
class LeadLagTests(unittest.TestCase):
    def test_pairwise_missing(self):
        x=np.arange(70.,dtype=float);y=x.copy();y[:10]=np.nan
        r,n=corr_pairs(x[:,None],y[:,None]);self.assertEqual(n[0,0],60);self.assertAlmostEqual(r[0,0],.5)
        y[:30]=np.nan;self.assertTrue(np.isnan(corr_pairs(x[:,None],y[:,None])[0][0,0]))
    def test_graph_maturity_and_self_exclusion(self):
        p=pd.DataFrame([dict(seq=i,code=c,x=float(i),y=float(i),turnover_log=1.) for i in range(80) for c in ['A','B','C']]);a=graph(p,70)
        self.assertGreater(len(a),0);self.assertTrue((a.follower!=a.leader).all());self.assertTrue(a.support.eq(65).all())
        q=p.copy();q.loc[q.seq>=65,'y']=-999.;pd.testing.assert_frame_equal(a,graph(q,70))
    def test_score_requires_two_observed_leaders(self):
        e=pd.DataFrame(dict(follower=['C','C'],leader=['A','B'],weight=[.2,.4],support=[60,60]));p=pd.DataFrame(dict(seq=[1,1,2],code=['A','B','A'],x=[.1,.4,.3]));z=score(p,e)
        self.assertAlmostEqual(z.loc[z.seq==1,'lead'].iloc[0],.3);self.assertTrue(np.isnan(z.loc[z.seq==2,'lead'].iloc[0]))
