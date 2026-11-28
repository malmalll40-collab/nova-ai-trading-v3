import unittest
import numpy as np,pandas as pd
try:
    from research_v30.pde_multihorizon import outcomes,labels,split,H
except ModuleNotFoundError:
    from pde_multihorizon import outcomes,labels,split,H
class MultiHorizonTests(unittest.TestCase):
    def bars(self):
        days=[f'{i:03}' for i in range(30)]
        d=pd.DataFrame({'BAS_DD':days,'ISU_CD':'A','market':'KOSPI','Close':np.arange(100,130),'Delta':1,'Open':np.arange(100,130),'High':np.arange(101,131),'Low':np.arange(99,129),'Volume':100,'Shares':1000})
        d['Cap']=d.Close*d.Shares
        return d,days
    def test_horizons_and_exact_entry(self):
        d,days=self.bars();z=outcomes(d,days).iloc[0]
        for h in H:
            self.assertAlmostEqual(z[f'r{h}'],100*((100+h)/101-1))
            self.assertAlmostEqual(z[f'mae{h}'],100*(100/101-1))
    def test_discontinuity_not_bridged(self):
        d,days=self.bars();z=outcomes(d[d.BAS_DD!='006'],days).iloc[0]
        self.assertTrue(np.isfinite(z.r5));self.assertTrue(np.isnan(z.r10));self.assertTrue(np.isnan(z.r20))
        d.loc[6:,'Shares']=2000;d['Cap']=d.Close*d.Shares
        z=outcomes(d,days).iloc[0];self.assertTrue(np.isnan(z.r10))
    def test_label_and_embargo(self):
        q=pd.DataFrame({'r5':[6,6,np.nan,0],'mae5':[-1,-5,-1,-1]})
        self.assertEqual(labels(q,5).tolist(),[1,2,-1,0])
        tr,te=split(pd.DataFrame({'seq':range(100)}),40,90)
        self.assertEqual((tr.seq.max(),te.seq.min(),te.seq.max()),(19,40,70))
if __name__=='__main__':unittest.main()
