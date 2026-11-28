import unittest
import numpy as np,pandas as pd
from research_v25.reversal import select,evaluate
class Tests(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame(dict(phase=['development']*5,day=['20240102']*4+['20240103'],code=list('ABCDE'),safe=[True,True,True,False,True],roles13=[True]*5,core5=[True]*5,ret5=[-.1,-.06,.1,-.4,.1],vol20=[.1,.02,.02,.01,.02],turnover_log=[10,11,12,13,14],net_pct=[np.nan,2.,3.,50.,4.]))
    def test_rank_and_veto(self):
        q=self.fixture();self.assertEqual(select(q,'reversal5').code.tolist(),['A','B']);self.assertEqual(select(q,'reversal5_volscaled').code.tolist(),['B','A'])
    def test_no_future_label_selection(self):
        q=self.fixture();a=select(q,'reversal5').code.tolist();q.net_pct=[99,-99,0,1,2];self.assertEqual(select(q,'reversal5').code.tolist(),a)
    def test_cash_unknown_and_cost(self):
        m=evaluate(self.fixture());a=m[(m.strategy=='reversal5')&(m.roundtrip_bps==60)].iloc[0];b=m[(m.strategy=='reversal5')&(m.roundtrip_bps==120)].iloc[0];self.assertEqual((a.signals,a.known,a.unknown),(2,1,1));self.assertAlmostEqual(a.daily_slot_mean_unknown_zero,.5);self.assertAlmostEqual(a.daily_slot_mean_unknown_minus100,-24.5);self.assertLess(b.mean_net_pct,a.mean_net_pct)
    def test_duplicate_reject(self):
        q=self.fixture();with_duplicate=pd.concat([q,q.iloc[:1]],ignore_index=True)
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):evaluate(with_duplicate)
if __name__=='__main__':unittest.main(verbosity=2)
