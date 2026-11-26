from contextlib import contextmanager
from datetime import date,datetime,timedelta
import sqlite3
import pytest
from nova_v574_data_audit import KST, INDEX_CODES
from nova_v576_expanded_coverage import SYMBOLS
from research_history_checkpoint import initialize, append, saved, missing_windows
from research_history_resume import resume

D=tuple(date(2026,11,23)+timedelta(days=i) for i in range(3))
NOW=datetime(2026,11,26,21,0,tzinfo=KST)
SCHEDULE={'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'}

class API:
    def __init__(self):
        self.calls=[]
    def get(self,path,tr,params):
        self.calls.append(dict(params))
        index=params['FID_COND_MRKT_DIV_CODE']=='U'
        rows=[]
        for d in D:
            stamp=d.strftime('%Y%m%d')
            if params['FID_INPUT_DATE_1']<=stamp<=params['FID_INPUT_DATE_2']:
                rows.append(dict(stck_bsop_date=stamp,bstp_nmix_prpr='100') if index else
                    dict(stck_bsop_date=stamp,stck_oprc='10',stck_hgpr='12',stck_lwpr='9',stck_clpr='11',acml_vol='5',acml_tr_pbmn='55'))
        return {'output2':rows}

@pytest.fixture
def db():
    conn=sqlite3.connect(':memory:')
    @contextmanager
    def tx():
        with conn:
            yield conn.cursor(),'sqlite'
    yield tx
    conn.close()

def run(db,api,**kwargs):
    return resume(api,D,transaction=db,now=lambda:NOW,calendar=lambda t:SCHEDULE,
        admission=kwargs.pop('admission',lambda:True),pause=lambda s:None,**kwargs)

def test_no_admission_no_writes_or_calls(db):
    api=API()
    r=run(db,api,admission=lambda:False)
    assert r['status']=='BLOCKED' and api.calls==[]
    with db() as (cur,kind):
        assert cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]==0

def test_resume_pass_only_missing(db):
    api=API()
    a=run(db,api,max_windows=1)
    assert a['new_rows']==3 and a['logical_calls']==1 and a['status']=='PARTIAL'
    first=api.calls[0]['FID_INPUT_ISCD']
    api.calls.clear()
    b=run(db,api)
    assert b['status']=='READY' and b['new_rows']==3*(len(SYMBOLS)-1)
    assert first not in [c['FID_INPUT_ISCD'] for c in api.calls]
    assert not b['training_ready'] and b['strategy_adoption']=='HOLD'
    api.calls.clear()
    c=run(db,api)
    assert c['status']=='READY' and c['new_rows']==0 and api.calls==[]

def test_holes_are_separate_windows(db):
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[1]:{'close':100}})
    api=API()
    r=run(db,api,max_windows=2)
    assert r['new_rows']==2
    assert [(c['FID_INPUT_DATE_1'],c['FID_INPUT_DATE_2']) for c in api.calls]==[(D[0].strftime('%Y%m%d'),D[1].strftime('%Y%m%d')),(D[1].strftime('%Y%m%d'),D[2].strftime('%Y%m%d'))]
    assert r['overlap_anchors_checked']==2

def test_global_budget_enforced(db):
    api=API()
    r=run(db,api,max_calls=1)
    assert r['logical_calls']==len(api.calls)==1 and r['status']=='PARTIAL'

def test_invalid_window_not_saved(db):
    class Bad(API):
        def get(self,*args):
            p=super().get(*args)
            p['output2'].append({'stck_bsop_date':'20261123','bstp_nmix_prpr':'999'})
            return p
    r=run(db,Bad())
    assert r['reason']=='WINDOW_INTEGRITY_REJECTED' and r['new_rows']==0
    with db() as (cur,kind):
        assert saved(cur,kind,r['dataset'],SYMBOLS[0])=={}

def test_block_during_run_preserves_completed_window(db):
    api=API()
    r=run(db,api,admission=lambda:len(api.calls)<1)
    assert r['status']=='PARTIAL' and r['new_rows']==3 and r['logical_calls']==1
    with db() as (cur,kind):
        assert not missing_windows(cur,kind,r['dataset'],SYMBOLS[0])

def test_changed_overlap_blocks_new_rows(db):
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[1]:{'close':99}})
    r=run(db,API())
    assert r['reason']=='ADJUSTED_HISTORY_OVERLAP_CONFLICT' and r['new_rows']==0
    with db() as (cur,kind):
        assert saved(cur,kind,dataset,SYMBOLS[0])=={D[1]:{'close':99.0}}

def test_missing_overlap_blocks_new_rows(db):
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[1]:{'close':100}})
    class Missing(API):
        def get(self,*args):
            p=super().get(*args)
            p['output2']=[row for row in p['output2'] if row['stck_bsop_date']!='20261124']
            return p
    r=run(db,Missing())
    assert r['reason']=='OVERLAP_ANCHOR_MISSING' and r['new_rows']==0
    with db() as (cur,kind):
        assert len(saved(cur,kind,dataset,SYMBOLS[0]))==1

def test_internal_gap_checks_both_edges(db):
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[0]:{'close':100},D[2]:{'close':100}})
    api=API()
    r=run(db,api,max_windows=1)
    assert r['new_rows']==1 and r['overlap_anchors_checked']==2
    assert api.calls[0]['FID_INPUT_DATE_1']=='20261123'
    assert api.calls[0]['FID_INPUT_DATE_2']=='20261125'
    assert not r['adjustment_basis_verified_across_runs'] and not r['training_ready']

def test_provider_stop_preserves_only_validated_anchor_and_previous_pages(db):
    from nova_v579_admission import ResearchBlocked
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[-1]:{'close':100}})
    class Interrupted(API):
        def get(self,*args):
            if self.calls:
                raise ResearchBlocked('RESEARCH_HTTP_FAILURE_429')
            p=super().get(*args)
            p['output2']=[row for row in p['output2'] if row['stck_bsop_date']!='20261123']
            return p
    r=run(db,Interrupted())
    assert r['reason']=='RESEARCH_HTTP_FAILURE_429' and r['new_rows']==1
    assert r['window_diagnostic']['anchors_observed']==1
    assert r['window_diagnostic']['observed_sessions']==2
    with db() as (cur,kind):
        assert set(saved(cur,kind,dataset,SYMBOLS[0]))==set(D[1:])

def test_integrity_rejection_exposes_safe_detail_without_writes(db):
    class Bad(API):
        def get(self,*args):
            p=super().get(*args)
            p['output2'].append({'stck_bsop_date':'20261123','bstp_nmix_prpr':'999'})
            return p
    r=run(db,Bad())
    assert r['reason']=='WINDOW_INTEGRITY_REJECTED' and r['new_rows']==0
    assert r['window_diagnostic']['reason']=='CONFLICTING_DUPLICATE'

def test_unverified_dispatch_keeps_existing_checkpoint_without_network_retry(db):
    from nova_v579_admission import ResearchBlocked
    with db() as (cur,kind):
        dataset=initialize(cur,kind,D)
        append(cur,kind,dataset,SYMBOLS[0],{D[-1]:{'close':100}})
    class Refused(API):
        def get(self,*args):
            self.calls.append('admission')
            raise ResearchBlocked('SHARED_QUOTA_DISPATCH_UNVERIFIED')
    api=Refused()
    r=run(db,api)
    assert r['window_diagnostic']['reason']=='SHARED_QUOTA_DISPATCH_UNVERIFIED'
    assert r['new_rows']==0 and len(api.calls)==1
    with db() as (cur,kind):
        assert saved(cur,kind,dataset,SYMBOLS[0])=={D[-1]:{'close':100.0}}
