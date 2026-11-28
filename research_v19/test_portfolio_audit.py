import unittest
import pandas as pd
from portfolio_audit import audit,proof,replay,benchmark,f
class TestPortfolioAudit(unittest.TestCase):
    def test_audit_and_disclosure(self):
        d=pd.DataFrame({'ISU_CD':['000001']*3,'BAS_DD':['20240102','20240103','20240104'],'market':'KOSPI','TDD_CLSPRC':[10000,5000,5100],'CMPPREVDD_PRC':[0,0,100],'LIST_SHRS':[100,200,200]})
        z,c=audit(d,list(d.BAS_DD))
        self.assertEqual(c['ratio_match_hypothesis'],1)
        self.assertFalse(z.independent_event_verified.any())
        p=proof(z,[('20240103','000001',100,200,'fixture')])
        self.assertTrue(p.verified_event.iloc[0])
        self.assertFalse(p.adjusted_price_verified.iloc[0])
        with self.assertRaisesRegex(ValueError,'DISCLOSURE_DATA_MISMATCH'):proof(z,[('20240103','000001',100,201,'fixture')])
    def test_cash_only(self):
        days=['20240102','20240103'];b=pd.DataFrame([dict(day=days[0],code='A',market='KOSPI',close=10000,delta=0,open=10000,high=10000,low=10000,volume=100,turnover=1000000,cap=10000000,shares=1000)])
        r,h=replay(days,{d:b.assign(day=d) for d in days},{},'fixture','liquidity',.006)
        self.assertEqual(r['entries'],0);self.assertEqual(r['return_frozen_pct'],0);self.assertEqual(f.FEE,.003)
        self.assertIsNone(r['top2_entry_pct'])
        m=benchmark(h,pd.DataFrame({'KOSPI':[100,120],'KOSDAQ':[100,110]},index=days))
        self.assertAlmostEqual(m['benchmark_return_pct'],15);self.assertEqual(m['exposure_matched_index_return_pct'],0)
    def test_filter_and_cost_reconciliation(self):
        days=pd.bdate_range('20240102',periods=7).strftime('%Y%m%d').tolist();b=pd.DataFrame([dict(day=days[0],code='A',market='KOSPI',close=10000,delta=0,open=10000,high=10000,low=10000,volume=100,turnover=1000000,cap=10000000,shares=1000)])
        bars={d:b.assign(day=d) for d in days};top={days[0]:[{'code':'A','regime':'down'}]}
        r,_=replay(days,bars,top,'fixture','liquidity',.003);s,_=replay(days,bars,top,'fixture','liquidity',.006);t,_=replay(days,bars,top,'fixture','trend_only',.003)
        self.assertEqual(r['closed'],1);self.assertLess(s['return_frozen_pct'],r['return_frozen_pct']);self.assertEqual(t['entries'],0);self.assertEqual(r['top2_entry_pct'],100);self.assertAlmostEqual(r['realized_pnl_krw'],f.CASH*r['return_frozen_pct']/100);self.assertEqual(r['realized_pnl_ex_top2_krw'],r['realized_pnl_krw'])
    def test_prior_exposure_and_bad_dates(self):
        days=['20240102','20240103','20240104'];h=pd.DataFrame({'day':days,'prior_exposure':[0,.1,.2],'equity':[f.CASH]*3})
        ip=pd.DataFrame({'KOSPI':[100,110,121],'KOSDAQ':[100,110,121]},index=days)
        m=benchmark(h,ip);self.assertAlmostEqual(m['exposure_matched_index_return_pct'],3.02)
        with self.assertRaisesRegex(ValueError,'INDEX_DATES_OR_VALUES'):benchmark(h,ip.iloc[::-1])
        h.loc[2,'prior_exposure']=2
        with self.assertRaisesRegex(ValueError,'EXPOSURE_RANGE'):benchmark(h,ip)
if __name__=='__main__':unittest.main(verbosity=2)
