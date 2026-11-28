import unittest
import pandas as pd
import numpy as np
from quality import quality

def fixture(n=90):
    days=pd.bdate_range('20240102',periods=n).strftime('%Y%m%d').tolist()
    d=pd.DataFrame({'ISU_CD':'000001','BAS_DD':days,'market':'KOSPI','TDD_CLSPRC':10000,'CMPPREVDD_PRC':0,'TDD_OPNPRC':10000,'TDD_HGPRC':10000,'TDD_LWPRC':10000,'ACC_TRDVOL':100000,'ACC_TRDVAL':1000000000,'MKTCAP':10000000000,'LIST_SHRS':1000000})
    return d,days
class Tests(unittest.TestCase):
    def test_clean_warmup_and_cost(self):
        d,days=fixture();q,_=quality(d,days,(89,89,89))
        self.assertFalse(q.eligible.iloc[:65].any());self.assertTrue(q.eligible.iloc[65]);self.assertTrue(q.known.iloc[65]);self.assertAlmostEqual(q.net_pct.iloc[65],100*(.997/1.003-1))
    def test_future_break_does_not_change_eligibility(self):
        d,days=fixture();q,_=quality(d,days,(89,89,89));d.loc[68,'LIST_SHRS']=2000000;d.loc[68,'MKTCAP']=20000000000
        r,flags=quality(d,days,(89,89,89));self.assertTrue(q.known.iloc[65]);self.assertTrue(r.eligible.iloc[65]);self.assertFalse(r.known.iloc[65]);self.assertTrue(np.isnan(r.net_pct.iloc[65]));self.assertFalse(r.eligible.iloc[68:].any());self.assertEqual(flags['shares_changed'],2)
    def test_missing_session_breaks_label(self):
        d,days=fixture();r,_=quality(d.drop(index=68),days,(89,89,89));self.assertTrue(r.eligible.iloc[65]);self.assertFalse(r.known.iloc[65]);self.assertTrue(np.isnan(r.net_pct.iloc[65]))
    def test_phase_purge(self):
        d,days=fixture();r,_=quality(d,days,(69,79,89));self.assertTrue(r.eligible.iloc[65]);self.assertTrue(r.purged.iloc[65]);self.assertFalse(r.known.iloc[65]);self.assertTrue(r.known.iloc[70]);self.assertTrue(r.purged.iloc[75])
    def test_zero_volume_restarts_warmup(self):
        d,days=fixture();d.loc[65,'ACC_TRDVOL']=0;r,f=quality(d,days,(89,89,89));self.assertEqual(f['zero_volume'],1);self.assertFalse(r.eligible.any())
    def test_no_cross_symbol_label(self):
        d,days=fixture();e=d.copy();e['ISU_CD']='000002';r,_=quality(pd.concat([d,e]),days,(89,89,89));self.assertEqual(r.known.sum(),40);self.assertFalse(r[r.code=='000001'].known.iloc[-5:].any())
if __name__=='__main__':unittest.main(verbosity=2)
