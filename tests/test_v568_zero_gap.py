from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
import kis_minute_coverage as k
import kis_zero_gap as z
import kis_gap_probe as gp
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
from krx_calendar_verified import get_krx_calendar, CalendarUnavailable

START=pd.Timestamp('2026-11-25 09:00',tz='Asia/Seoul'); CLOSE=pd.Timestamp('2026-11-25 15:30',tz='Asia/Seoul'); DAY=START.date()
NOW=datetime(2026,11,25,20,tzinfo=ZoneInfo('Asia/Seoul'))
TIMES=list(pd.date_range(START,CLOSE-pd.Timedelta(minutes=11),freq='min'))+[CLOSE]
TARGET='132100'

def rows():
    cum=1000; out=[]
    for t in TIMES:
        ts=t.strftime('%H%M%S');v=0 if ts==TARGET else 10;cum+=v
        out.append(dict(stck_bsop_date='20261125',stck_cntg_hour=ts,stck_oprc='100',stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol=str(v),acml_vol=str(cum)))
    return out

class API:
    def __init__(self,mode):self.mode=mode;self.calls=[]
    def get(self,path,tr,p):
        self.calls.append((tr,p['FID_INPUT_HOUR_1']));raw=rows(); cursor=p['FID_INPUT_HOUR_1']
        if tr=='FHKST03010230':
            for r in raw:
                if r['stck_cntg_hour']=='132200':
                    if self.mode=='delta':r['acml_vol']=str(int(r['acml_vol'])+1)
                    if self.mode=='no_cumulative':r.pop('acml_vol')
            return {'output2':[r for r in reversed(raw) if r['stck_cntg_hour']<=cursor and r['stck_cntg_hour']!=TARGET][:100]}
        out=[r for r in reversed(raw) if r['stck_cntg_hour']<=cursor][:30]
        for r in out:
            if r['stck_cntg_hour']==TARGET and self.mode=='positive':r['cntg_vol']='1'
            if r['stck_cntg_hour']==TARGET and self.mode=='bad_target_cum' and cursor=='132300':r['acml_vol']='0'
            if r['stck_cntg_hour']=='132200' and self.mode=='neighbor_conflict' and cursor=='132300':r['stck_prpr']='102'
        if self.mode=='first_neighbor' and cursor=='132300':out=out[1:]
        if self.mode=='query_failed' and cursor=='132300':raise RuntimeError('private')
        return {'output2':out}

@pytest.fixture(autouse=True)
def same_day(monkeypatch):
    monkeypatch.setattr(k,'compare_same_day',lambda *a:gp.compare_same_day(*a,now=NOW))
    monkeypatch.setattr(k,'certify_zero_gaps',lambda *a:z.certify_zero_gaps(*a,now=NOW))

def test_certified_gap_no_synthetic_prices_or_volume():
    value,d=k.collect(API('ok'),'086520',DAY,START,CLOSE)
    assert value=={'Open':100,'High':102,'Low':99,'Close':101,'Volume':3800}
    assert d['missing_count']==1 and d['received_required']==380 and d['certified_zero_count']==1 and d['unresolved_count']==0
    proof=d['zero_proofs']['13:21'];assert proof['next_cumulative']-proof['prev_cumulative']==proof['next_volume']==10

@pytest.mark.parametrize('mode',['delta','no_cumulative','positive','bad_target_cum','neighbor_conflict','first_neighbor','query_failed'])
def test_unproven_gap_stays_blocked(mode):
    value,d=k.collect(API(mode),'086520',DAY,START,CLOSE)
    assert value is None and d['reason']=='COVERAGE_MISSING' and d['unresolved_count']==1


def test_calendar_initialization_serialized(monkeypatch):
    import exchange_calendars as ec
    original=ec.get_calendar; real=original('XKRX',start='2025-01-01',end='2027-12-31'); lock=Lock(); count=0; peak=0
    def fake(*a,**kw):
        nonlocal count,peak
        with lock:count+=1;peak=max(peak,count)
        try:time.sleep(.02);return real
        finally:
            with lock:count-=1
    monkeypatch.setattr(ec,'get_calendar',fake)
    with ThreadPoolExecutor(max_workers=6) as pool: result=list(pool.map(get_krx_calendar,[DAY]*12))
    assert peak==1 and all(c.is_session(DAY.isoformat()) for c in result)


def test_calendar_failure_does_not_infer_open(monkeypatch):
    import exchange_calendars as ec
    def fail(*a,**kw):raise RuntimeError('private')
    monkeypatch.setattr(ec,'get_calendar',fail)
    with pytest.raises(CalendarUnavailable,match='XKRX_LOAD_FAILED'):get_krx_calendar(DAY)


def test_zero_proof_immutable_storage(tmp_path,monkeypatch):
    from tests.test_v550_research import observation
    import core561_evidence as ev
    from collection_audit import integrity_detail
    from kis_daily_archive import save_regular, archive_verified
    import json,sqlite3
    monkeypatch.delenv('DATABASE_URL',raising=False)
    path=tmp_path/'obs.db';monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(path));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1');monkeypatch.setenv('NOVA_KIS_ARCHIVE_ENABLED','1')
    _,d=k.collect(API('ok'),'086520',DAY,START,CLOSE)
    a=observation(NOW);a['symbol']='086520.KQ';a['daily_integrity']['minute_coverage']=d
    assert ev.record(a,NOW)=='SAVED'
    with sqlite3.connect(path) as db:
        saved=json.loads(db.execute('SELECT payload FROM nova_core561_observations').fetchone()[0])
        assert saved['minute_coverage_proof']['zero_proofs']==d['zero_proofs']
    from collection_audit import audit_day
    assert audit_day(DAY.isoformat(),[a['symbol']])['certified_zero_symbols']=={'086520.KQ':['13:21']}
    assert ev.record(a,NOW)=='EXISTS'
    assert save_regular(a['symbol'],pd.DataFrame(),a['daily_integrity'])=='NOT_SAVED'
    assert not archive_verified(a['symbol'],pd.DataFrame(),a['daily_integrity'])
    d['zero_proofs']['13:21']['next_volume']=999
    assert not integrity_detail(a)['minute_coverage'].get('zero_proofs')
    assert ev.snapshot(a,NOW) is None
