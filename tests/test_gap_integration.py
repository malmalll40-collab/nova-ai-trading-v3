from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
import kis_minute_coverage as k
import kis_gap_probe as gp
START=pd.Timestamp('2026-11-25 09:00',tz='Asia/Seoul'); CLOSE=pd.Timestamp('2026-11-25 15:30',tz='Asia/Seoul'); GAP='132100'; DAY=START.date()
TIMES=list(pd.date_range(START,CLOSE-pd.Timedelta(minutes=11),freq='min'))+[CLOSE]
def bar(t):return dict(stck_bsop_date='20261125',stck_cntg_hour=t.strftime('%H%M%S'),stck_oprc='100',stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol='10')
class API:
    def __init__(self,mode):self.mode=mode
    def get(self,path,tr,p):
        if tr=='FHKST03010200':
            return {'output2':[bar(pd.Timestamp('2026-11-25 13:22',tz='Asia/Seoul')),bar(pd.Timestamp('2026-11-25 13:21',tz='Asia/Seoul'))]}
        assert tr=='FHKST03010230' and p['FID_FAKE_TICK_INCU_YN']==''
        alt=p['FID_PW_DATA_INCU_YN']=='N'
        rows=[bar(t) for t in reversed(TIMES) if t.strftime('%H%M%S')<=p['FID_INPUT_HOUR_1'] and (self.mode=='full' or t.strftime('%H%M%S')!=GAP or (alt and self.mode in ('recover','conflict')))][:100]
        if alt and self.mode=='conflict':
            for r in rows:
                if r['stck_cntg_hour']=='132200':r['cntg_vol']='11'
        return {'output2':rows}
@pytest.fixture(autouse=True)
def same_day(monkeypatch):
    orig=gp.compare_same_day
    monkeypatch.setattr(k,'compare_same_day',lambda *a:orig(*a,now=datetime(2026,11,25,20,tzinfo=ZoneInfo('Asia/Seoul'))))
@pytest.mark.parametrize('mode',['full','recover'])
def test_recovery(mode):
    v,d=k.collect(API(mode),'086520',DAY,START,CLOSE)
    assert v['Volume']==3810 and d['reason']=='VERIFIED' and d['missing_count']==0
    assert d['alternate_pages']==(1 if mode=='recover' else 0)
def test_alternate_endpoint_not_used_for_storage():
    v,d=k.collect(API('missing'),'086520',DAY,START,CLOSE)
    assert v is None and d['missing_minutes']==['13:21'] and d['same_day_probe']=={'13:21':'TARGET_POSITIVE_VOLUME'}
def test_overlapping_conflict_rejected():
    v,d=k.collect(API('conflict'),'086520',DAY,START,CLOSE)
    assert v is None and d['reason']=='CONFLICTING_DUPLICATE'
