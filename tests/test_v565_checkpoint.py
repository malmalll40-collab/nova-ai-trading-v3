from datetime import datetime, timezone, timedelta
import sqlite3
import pytest
import research_checkpoint as cp

NOW=datetime(2026,11,25,9,0,tzinfo=timezone.utc)
SYMS=['005930.KS','086520.KQ']

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('RENDER_GIT_COMMIT',raising=False)
    path=tmp_path/'neon_sim.db';monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(path))
    monkeypatch.setattr(cp,'krx_session',lambda now:{'calendar_verified':True,'close_kst':'15:30'})
    return path

def row(symbol=SYMS[0]):
    return {'symbol':symbol,'status':'READY','source':'kis','checkpoint_day':'2026-11-25','asof_last_bar':'2026-11-25 00:00:00',
            'v54_accuracy':{'core_focus':{'status':'READY','cost_bps':15,'_replay':{'points':{'2026-01-01':{'net':.1}}}}}}

def test_restart_exact_scope_and_expiry(store,monkeypatch):
    scope=cp.scope_for(SYMS,'kis',15,NOW)
    assert cp.save(scope,row(),NOW)
    assert cp.load(scope,NOW+timedelta(minutes=5))=={SYMS[0]:row()}
    for syms,source,cost in [(SYMS[::-1],'kis',15),(SYMS,'auto',15),(SYMS,'kis',20)]:
        assert cp.load(cp.scope_for(syms,source,cost,NOW),NOW)=={}
    monkeypatch.setenv('RENDER_GIT_COMMIT','new')
    assert cp.load(cp.scope_for(SYMS,'kis',15,NOW),NOW)=={}
    assert cp.load(scope,NOW+timedelta(hours=2,seconds=1))=={}
    assert cp.load(scope,NOW+timedelta(days=1))=={}
    assert cp.load(scope,NOW-timedelta(seconds=1))=={}

def test_fail_closed_and_corruption(store,monkeypatch):
    assert cp.scope_for(SYMS,'kis',15,NOW-timedelta(hours=4)) is None
    scope=cp.scope_for(SYMS,'kis',15,NOW)
    assert not cp.save(scope,{**row(),'status':'UNAVAILABLE'},NOW)
    assert not cp.save(scope,{**row(),'asof_last_bar':'2026-11-24'},NOW)
    assert cp.save(scope,row(),NOW)
    with sqlite3.connect(store) as db:db.execute(f'UPDATE {cp.TABLE} SET payload=?',('{}',))
    assert cp.load(scope,NOW)=={}
    monkeypatch.setattr(cp,'krx_session',lambda now:{'calendar_verified':False,'close_kst':'15:30'})
    assert cp.scope_for(SYMS,'kis',15,NOW) is None

def test_verified_data_only(store):
    scope=cp.scope_for(SYMS,'kis',15,NOW)
    data={**row(),'completed_session':{'calendar_verified':True,'stale':False,'expected_completed_session':scope['day'],'latest_completed_session':scope['day']},'daily_integrity':{'verified':True,'regular_volume_verified':True,'signal_hold':False,'verification_basis':'KIS_REGULAR_MINUTE','latest_date':scope['day']}}
    assert cp.eligible_data(data,scope)
    assert not cp.eligible_data({**data,'daily_integrity':{**data['daily_integrity'],'verified':False}},scope)
    assert not cp.eligible_data({**data,'source':'yahoo'},scope)

def test_worker_restore_and_db_failure(store,monkeypatch):
    import server
    import nova_v542_aligned
    scope=cp.scope_for(SYMS,'kis',15,NOW)
    monkeypatch.setattr(cp,'scope_for',lambda *a:scope)
    monkeypatch.setattr(cp,'load',lambda *a:{SYMS[0]:row()})
    monkeypatch.setattr(cp,'save',lambda *a:(_ for _ in ()).throw(RuntimeError('private secret')))
    calls=[]
    def fetch(s,*a):
        calls.append(s)
        return {**row(s),'completed_session':{'calendar_verified':True,'stale':False,'expected_completed_session':scope['day'],'latest_completed_session':scope['day']},'daily_integrity':{'verified':True,'regular_volume_verified':True,'verification_basis':'KIS_REGULAR_MINUTE','latest_date':scope['day']}}
    monkeypatch.setattr(server,'_v5_batch_fetch',fetch)
    monkeypatch.setattr(nova_v542_aligned,'aligned_summary',lambda rows:{'status':'READY'})
    monkeypatch.setattr(server,'BATCH_RESEARCH_STATE',{'job_id':'test','status':'running','symbols':SYMS,'rows':[],'checkpoint_enabled':True,'resume_checkpoint':True})
    server._v5_batch_worker('test',SYMS,'kis',15)
    assert calls==[SYMS[1]]
    assert server.BATCH_RESEARCH_STATE['status']=='complete'
    assert server.BATCH_RESEARCH_STATE['checkpoint']['restored']==1
    assert server.BATCH_RESEARCH_STATE['checkpoint']['errors']==1
    assert len(server.BATCH_RESEARCH_STATE['rows'])==2
