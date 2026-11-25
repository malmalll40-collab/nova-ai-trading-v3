from datetime import date
import pandas as pd
import pytest
from kis_minute_coverage import collect, MAX_PAGES, MAX_GAP_PAGES
from collection_audit import integrity_detail

DAY=date(2026,11,25)
START=pd.Timestamp('2026-11-25 09:00',tz='Asia/Seoul')
CLOSE=pd.Timestamp('2026-11-25 15:30',tz='Asia/Seoul')
TIMES=list(pd.date_range(START,CLOSE-pd.Timedelta(minutes=11),freq='min'))+[CLOSE]
def row(t,vol='10'):
    return dict(stck_bsop_date='20261125',stck_cntg_hour=t.strftime('%H%M%S'),stck_oprc='100',stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol=vol)
class API:
    def __init__(self,rows,limit=40):self.rows=rows;self.limit=limit;self.calls=[]
    def get(self,path,tr,params):
        assert tr=='FHKST03010230' and params['FID_FAKE_TICK_INCU_YN']==''
        cursor=params['FID_INPUT_HOUR_1'];self.calls.append(cursor)
        return {'output2':[r for r in reversed(self.rows) if r['stck_cntg_hour']<=cursor][:self.limit]}
def run(api):return collect(api,'086520',DAY,START,CLOSE)
def test_more_pages_preserve_ohlcv():
    api=API([row(t) for t in TIMES]+[row(CLOSE+pd.Timedelta(hours=1))]);v,d=run(api)
    assert v==dict(Open=100,High=102,Low=99,Close=101,Volume=3810)
    assert d['pages']>6 and d['reason']=='VERIFIED' and d['missing_count']==0

def test_gap_target_recovers_actual_provider_row():
    gap=TIMES[123];target=gap.strftime('%H%M%S')
    class Gap(API):
        def get(self,path,tr,params):
            r=super().get(path,tr,params)
            if params['FID_INPUT_HOUR_1']!=target:r['output2']=[x for x in r['output2'] if x['stck_cntg_hour']!=target]
            return r
    v,d=run(Gap([row(t) for t in TIMES],100))
    assert v and v['Volume']==3810 and d['gap_pages']>=1 and d['missing_count']==0

@pytest.mark.parametrize('index',[0,123,-1])
def test_real_missing_not_synthesized(index):
    gap=TIMES[index];v,d=run(API([row(t) for t in TIMES if t!=gap],100))
    assert v is None and d['reason']=='COVERAGE_MISSING' and gap.strftime('%H:%M') in d['missing_minutes']

def test_zero_volume_present_is_distinct_from_missing():
    v,d=run(API([row(t,'0' if i==123 else '10') for i,t in enumerate(TIMES)],100))
    assert v and v['Volume']==3800 and d['zero_volume_minutes']==1 and d['missing_count']==0
    v,d=run(API([row(t,'0' if i==0 else '10') for i,t in enumerate(TIMES)],100))
    assert v is None and d['reason']=='OPEN_CLOSE_VOLUME_INVALID'

@pytest.mark.parametrize('change,reason',[({'cntg_vol':'-1'},'INVALID_VOLUME'),({'stck_prpr':'999'},'INVALID_PRICE'),({'stck_cntg_hour':'153001'},'COVERAGE_MISSING')])
def test_bad_data_rejected(change,reason):
    rows=[row(t) for t in TIMES];rows[-1].update(change)
    v,d=run(API(rows,100));assert v is None and d['reason']==reason

def test_conflicting_duplicate_rejected():
    rows=[row(t) for t in TIMES];rows.append({**rows[-1],'cntg_vol':'20'})
    v,d=run(API(rows,100));assert v is None and d['reason']=='CONFLICTING_DUPLICATE'

def test_page_budget_and_diagnostic_sanitization():
    api=API([row(t) for t in TIMES],1);v,d=run(api)
    assert v is None and len(api.calls)<=MAX_PAGES+MAX_GAP_PAGES and d['stop']=='PAGE_LIMIT'
    detail=integrity_detail({'daily_integrity':{'minute_coverage':{**d,'missing_minutes':['09:01','secret'],'secret':'x'}}})
    assert detail['minute_coverage']['missing_minutes']==['09:01'] and 'secret' not in detail['minute_coverage']
