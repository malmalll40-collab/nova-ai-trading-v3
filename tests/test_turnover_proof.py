from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from kis_turnover_proof import verify,sanitize
T=pd.Timestamp('2026-11-25 13:21',tz='Asia/Seoul'); C=pd.Timestamp('2026-11-25 15:30',tz='Asia/Seoul'); D=T.date(); M=pd.Timedelta(minutes=1)
NOW=datetime(2026,11,25,21,tzinfo=ZoneInfo('Asia/Seoul'))
R={'Open':100,'High':102,'Low':99,'Close':101,'Volume':10}; ROWS={T-M:R,T+M:R}; AMOUNTS={T-M:10000,T+M:11010}
class API:
    def __init__(self,mode):self.mode=mode;self.calls=[]
    def get(self,path,tr,p):
        self.calls.append(p['FID_INPUT_HOUR_1']); assert tr=='FHKST03010200'
        def row(stamp,vol,amt):return dict(stck_bsop_date='20261125',stck_cntg_hour=stamp,stck_oprc='100',stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol=str(vol),acml_tr_pbmn=str(amt))
        r=[row(p['FID_INPUT_HOUR_1'],10,12000),row('132200',10,11010),row('132100',0,10000),row('132000',10,10000)]
        if self.mode=='changed':r[2]['acml_tr_pbmn']='10001'
        if self.mode=='missing':r[2].pop('acml_tr_pbmn')
        if self.mode=='positive':r[2]['cntg_vol']='1'
        if self.mode=='neighbor':r[1]['stck_prpr']='102'
        if self.mode=='amount':r[1]['acml_tr_pbmn']='11011'
        if self.mode=='first':r=r[1:]
        if self.mode=='duplicate':r.append(r[2].copy())
        if self.mode=='late_conflict' and len(self.calls)==2:r[2]['acml_tr_pbmn']='10001'
        if self.mode=='error':raise RuntimeError('private')
        return {'output2':r}
def run(mode='ok',amounts=AMOUNTS,now=NOW):return verify(API(mode),'086520',D,T,C,ROWS,amounts,{'13:21':'TARGET_ZERO_VOLUME'},now=now)
def test_good():
    proof,code=run();assert code=='TURNOVER_VERIFIED' and sanitize(proof)==proof and proof['target_volume']==0
    assert proof['next_turnover']-proof['prev_turnover']==1010
@pytest.mark.parametrize('mode,code',[('changed','TURNOVER_TARGET_CHANGED'),('missing','TURNOVER_UNAVAILABLE'),('positive','TURNOVER_TARGET_NOT_ZERO'),('neighbor','TURNOVER_NEIGHBOR_MISMATCH'),('amount','TURNOVER_NEIGHBOR_MISMATCH'),('first','TURNOVER_ROW_UNSAFE'),('duplicate','TURNOVER_ROW_UNSAFE'),('late_conflict','TURNOVER_TARGET_CHANGED'),('error','TURNOVER_PROBE_FAILED')])
def test_reject(mode,code):assert run(mode)==(None,code)
def test_no_amount():assert run(amounts={})==(None,'TURNOVER_UNAVAILABLE')
def test_invalid_delta():assert run(amounts={T-M:10000,T+M:11021})==(None,'TURNOVER_DELTA_INVALID')
def test_wrong_day():assert run(now=NOW+pd.Timedelta(days=1))==(None,'TURNOVER_WINDOW_BLOCKED')
def test_tamper():
    p,_=run();p['target_turnover']+=1;assert sanitize(p) is None
