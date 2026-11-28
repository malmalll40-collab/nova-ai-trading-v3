import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from research_v30 import pde_miss_audit as m

class TestMissAudit(unittest.TestCase):
    def test_outcome_partition(self):
        p=pd.DataFrame({'r5':[6,6,-8,0,np.nan,6],'mae5':[-1,-5,-9,-1,-1,np.nan]})
        self.assertEqual(m.label(p).tolist(),['clean_win','risky_win','adverse','other','unknown','unknown'])
    def test_selection_does_not_depend_on_outcome(self):
        p=pd.DataFrame({'day':['a']*103,'code':[str(i).zfill(6) for i in range(103)],'turnover_log':np.arange(103),'r5':6.,'mae5':-1.})
        a=m.classify(p,['a']);p['r5']=-30;b=m.classify(p,['a'])
        self.assertEqual(a.bucket.tolist(),b.bucket.tolist());self.assertEqual(a.bucket.value_counts().to_dict(),{'missed_inside100':98,'outside100':3,'picked_proxy':2})
        with self.assertRaises(ValueError):m.classify(pd.concat([p,p.iloc[:1]]),['a'])
    def test_exact_lag_missing_and_count(self):
        days=[str(i).zfill(3) for i in range(31)];records=[]
        for i,day in enumerate(days):
            for k in range(102):
                if i==15 and k==50:continue
                win=k%2==0
                rec={f:float(i) for f in m.FEATURES};rec.update(day=day,code=str(k).zfill(6),turnover_log=float(k),r5=6. if win else -8.,mae5=-1. if win else -8.)
                records.append(rec)
        with patch.object(m,'FOLDS',((20,30),)):
            c,d=m.audit(pd.DataFrame(records),days)
        self.assertEqual(int(c.rows.sum()),6*102)
        r=d.query("bucket=='missed_inside100' and lag==5 and feature=='ret5'").iloc[0]
        self.assertEqual(r.win_available,6*49-1);self.assertEqual(r.adverse_available,6*49);self.assertEqual(r.mean_daily_median_difference,0)
if __name__=='__main__':unittest.main()
