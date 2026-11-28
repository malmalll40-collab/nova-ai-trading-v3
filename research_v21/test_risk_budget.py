import unittest
import pandas as pd
from risk_budget import policy,replay,f
class Tests(unittest.TestCase):
    def test_market(self):
        self.assertEqual([policy('market_half',s,0) for s in ['up','sideways','down','shock','unknown']],[1,.5,.5,0,0])
    def test_drawdown(self):
        self.assertEqual(policy('drawdown_half','up',-.049),1)
        self.assertEqual(policy('drawdown_half','up',-.05),.5)
        self.assertEqual(policy('combined','sideways',-.06),.25)
    def test_invalid(self):
        for x in [('bad','up',0),('combined','up',.01),('combined','up',float('nan'))]:
            with self.assertRaises(ValueError):policy(*x)
    def test_prior_close_and_existing_exit(self):
        days=pd.bdate_range('20240102',periods=7).strftime('%Y%m%d').tolist()
        b=pd.DataFrame([dict(day=days[0],code='A',market='KOSPI',close=10000,delta=0,open=10000,high=10000,low=10000,volume=100,turnover=1000000,cap=10000000,shares=1000)])
        bars={d:b.assign(day=d) for d in days};ranked={d:['A'] for d in days[:2]}
        states=dict.fromkeys(days,'shock');states[days[0]]='sideways'
        r,h=replay(days,bars,ranked,states,'market_half',.003)
        self.assertEqual(r['entries'],1);self.assertEqual(r['closed'],1);self.assertEqual(h.entry_scale.iloc[1],.5);self.assertEqual(h.entry_scale.iloc[2],0)
        self.assertEqual(h.open.iloc[2],1);self.assertEqual(h.open.iloc[-1],0);self.assertAlmostEqual(f.FEE,.003)
if __name__=='__main__':unittest.main(verbosity=2)
