import unittest
import numpy as np
import pandas as pd
from regime import regime,compare
class Tests(unittest.TestCase):
    def test_future_does_not_change_regime(self):
        p=pd.Series(np.linspace(100,115,60));a=regime(p);p.iloc[45:]=20;b=regime(p);pd.testing.assert_series_equal(a.iloc[:45],b.iloc[:45]);self.assertEqual(a.iloc[0],'unknown')
    def test_shock_precedes_trend(self):
        p=pd.Series([100.]*15+[115.]*5+[110.]);self.assertEqual(regime(p).iloc[-1],'shock')
    def test_cash_and_no_replacement(self):
        days=pd.bdate_range('20230102',periods=729).strftime('%Y%m%d').tolist();d=days[364]
        q=pd.DataFrame([dict(day=d,market='KOSPI',code='A',phase='validation',median_turnover20=100,net_pct=np.nan),dict(day=d,market='KOSDAQ',code='B',phase='validation',median_turnover20=90,net_pct=4.),dict(day=d,market='KOSDAQ',code='C',phase='validation',median_turnover20=80,net_pct=100.)]);idx=pd.DataFrame([dict(day=d,market='KOSPI',regime='down'),dict(day=d,market='KOSDAQ',regime='up')]);m,_=compare(q,days,idx);z=m[m.phase=='validation'].set_index('strategy');self.assertEqual(z.loc['trend_only','signals'],1);self.assertEqual(z.loc['liquidity','unknown'],1);self.assertAlmostEqual(z.loc['trend_only','daily_slot_mean_unknown_minus100'],4/354);self.assertAlmostEqual(z.loc['liquidity','daily_slot_mean_unknown_minus100'],-96/354)
if __name__=='__main__':unittest.main(verbosity=2)
