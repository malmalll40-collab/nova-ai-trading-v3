import unittest
from datetime import datetime,timezone
import pandas as pd
from research_v30.flow_ingest import join_asof_day,validate
from research_v30.flow_probe import parse,request_params

class FlowTests(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame([dict(day="20241101",code="005930",foreign_shares=500,institution_shares=-20,published_at="2024-11-01T18:00:00+09:00",source="test fixture",scope="KRX")])
    def test_cutoff(self):
        x=self.fixture()
        q=pd.DataFrame([dict(day="20241101",code="005930",decision_at="2024-11-01T16:00:00+09:00")])
        a=join_asof_day(q,x,"flow")
        self.assertFalse(a.flow_available.iloc[0]);self.assertTrue(pd.isna(a.foreign_shares.iloc[0]))
        q.decision_at="2024-11-01T19:00:00+09:00"
        self.assertTrue(join_asof_day(q,x,"flow").flow_available.iloc[0])
    def test_duplicates_scope(self):
        x=self.fixture()
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):validate(pd.concat([x,x]),"flow")
        x.scope="NXT"
        with self.assertRaisesRegex(ValueError,"SCOPE"):validate(x,"flow")
    def test_missing_not_zero(self):
        x=self.fixture();x.institution_shares=None
        self.assertTrue(pd.isna(validate(x,"flow").institution_shares.iloc[0]))
    def test_dated_response(self):
        p={"rt_cd":"0","output2":[{"stck_bsop_date":"20241101","frgn_ntby_qty":"1,200","orgn_ntby_qty":"-3"}]}
        now=datetime(2026,11,28,tzinfo=timezone.utc)
        a=parse(p,"005930","20241101",now)
        self.assertEqual(a[0]["foreign_shares"],1200)
        self.assertEqual(a[0]["published_at"],now.isoformat())
        self.assertEqual(request_params("005930","20241101")["FID_COND_MRKT_DIV_CODE"],"J")
    def test_later_response_rejected(self):
        p={"rt_cd":"0","output2":[{"stck_bsop_date":"20241104"}]}
        with self.assertRaisesRegex(ValueError,"UNEXPECTED_LATER_DATE"):
            parse(p,"005930","20241101",datetime(2026,11,28,tzinfo=timezone.utc))
if __name__=="__main__":unittest.main()
