import unittest
import numpy as np,pandas as pd
from research_v23.learn import FEATURES,evaluate
class Tests(unittest.TestCase):
    def fixture(self):
        rng=np.random.default_rng(42);n=1800
        q=pd.DataFrame(rng.normal(size=(n,len(FEATURES))),columns=FEATURES)
        q['phase']=np.repeat(['development','validation','reviewed_holdout'],600)
        q['day']=np.repeat(pd.bdate_range('20230102',periods=180).strftime('%Y%m%d'),10)
        q['code']=np.tile([str(i) for i in range(10)],180)
        q['net_pct']=rng.normal(0,8,n);q['safe']=True;q['roles13']=q.ret5>0;q['core5']=q.ret20>0
        return q
    def test_non_development_labels_do_not_change_training_metrics(self):
        q=self.fixture();m,r,n=evaluate(q);q.loc[q.phase!='development','net_pct']*=-1;t,s,k=evaluate(q)
        self.assertEqual(n,600);self.assertEqual(k,n)
        pd.testing.assert_frame_equal(m[m.phase=='development'],t[t.phase=='development'])
        pd.testing.assert_frame_equal(r[r.phase=='development'],s[s.phase=='development'])
    def test_unknown_and_selection_limit(self):
        q=self.fixture();q.loc[(q.phase=='validation')&(q.code=='0'),'net_pct']=np.nan;q.loc[q.phase=='validation','turnover_log']=np.where(q.loc[q.phase=='validation','code']=='0',100,0)
        _,r,_=evaluate(q);v=r[(r.phase=='validation')&(r.strategy=='liquidity')].iloc[0]
        self.assertEqual(v.signals,120);self.assertEqual(v.unknown,60);self.assertEqual(v.known,60)
        self.assertAlmostEqual(v.daily_slot_mean_unknown_zero-v.daily_slot_mean_unknown_minus100,50)
if __name__=='__main__':unittest.main(verbosity=2)
