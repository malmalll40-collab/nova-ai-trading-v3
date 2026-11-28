import unittest
import numpy as np
import pandas as pd
from engine import feature_frame,decide,Config
from core_comparison import _core_at,_day
from nova_v5_research import remove_role_vote
from nova_v541_core import _rule
from role_audit import NAMES,FACTORS,votes,signals,buy,evaluate
class Tests(unittest.TestCase):
    def fixture(self):
        rng=np.random.default_rng(1943);dates=pd.bdate_range('20240102',periods=200)
        c=pd.Series(10000*np.cumprod(1+rng.normal(.001,.025,len(dates))),index=dates)
        m=pd.Series(100*np.cumprod(1+rng.normal(.001,.015,len(dates))),index=dates);p=m.shift(1);r=p/p.shift(20)-1;v=p.pct_change().rolling(20).std(ddof=0)
        g=pd.DataFrame({'Open':c*.998,'High':c*1.025,'Low':c*.975,'Close':c,'Volume':rng.integers(1000,4000,len(c)),'index_ret20':r,'market_ok':(p>p.rolling(20).mean())&(p.rolling(20).mean()>p.rolling(50).mean())&(r>=0)&(v<=.04)})
        return g,dict(zip(map(_day,dates),m))
    def test_roles_parity(self):
        g,_=self.fixture();f=feature_frame(g,'스윙');cfg=Config.for_mode('스윙',30);a,v,z=votes(g);s,_=signals(g)
        for i in range(65,len(g)):
            d=decide(f,cfg,i)
            self.assertEqual(a.iloc[i].tolist(),[x.direction for x in d.votes[:10]])
            self.assertEqual(bool(v.iloc[i]),d.risk_veto)
            self.assertEqual(bool(s.roles13.iloc[i]),d.signal=='BUY')
            for j,k in enumerate(NAMES):self.assertEqual(bool(s['omit_role_'+k].iloc[i]),remove_role_vote(d.votes,j,cfg,d.risk_veto)=='BUY')
    def test_core_parity(self):
        g,m=self.fixture();f=feature_frame(g,'스윙');s,_=signals(g)
        for i in range(65,len(g)):
            d=_core_at(f,i,m);self.assertEqual(d['status'],'READY')
            self.assertEqual(bool(s.core5.iloc[i]),d['signal']=='BUY')
            z={x['key']:x['pass'] for x in d['checks']}
            for k in FACTORS:self.assertEqual(bool(s['omit_core_'+k].iloc[i]),_rule(z,k))
    def test_reviewers_and_veto(self):
        a=pd.DataFrame([[1]*5+[0]*5,[1]*6+[0]*4],columns=NAMES);v=pd.Series([False,True])
        self.assertEqual(buy(a,v).tolist(),[True,False])
        self.assertEqual(buy(a,v,False).tolist(),[False,False])
    def test_future_and_unknown(self):
        g,_=self.fixture();s,_=signals(g);g.iloc[150:,g.columns.get_loc('Close')]*=2;t,_=signals(g);pd.testing.assert_frame_equal(s.iloc[:150],t.iloc[:150])
        q=s.iloc[65:67].reset_index(drop=True);q.loc[:,'roles13']=True;q['phase']='fixture';q['day']=['20240102','20240103'];q['net_pct']=[2.,np.nan]
        row=evaluate(q).query("strategy=='roles13'").iloc[0]
        self.assertEqual(row['unknown'],1);self.assertEqual(row['known'],1);self.assertAlmostEqual(row['daily_opportunity_pess_mean'],-49.)
if __name__=='__main__':unittest.main(verbosity=2)
