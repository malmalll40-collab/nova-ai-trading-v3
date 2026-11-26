"""Standalone integration check, run only against disposable CI PostgreSQL."""
import os
from datetime import date,timedelta
import psycopg
from research_history_checkpoint import initialize,append,saved,missing_windows,TABLE
from research_history_postgres import transaction_factory
from research_history_resume import resume
from research_history_run_control import exclusive_run, read_status, run_exclusive
from nova_v576_expanded_coverage import SYMBOLS
from nova_v574_data_audit import KST
from datetime import datetime

def main():
    dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
    from research_db_session_probe import verify_session,PROBE_LOCK
    verified=verify_session(dsn)
    assert verified['direct_connection_status']=='READ_OK' and verified['session_probe_verified'] is True
    assert verified['execution_authorized'] is False and verified['session_mode_confirmed'] is False
    with psycopg.connect(dsn,autocommit=True) as check:
        with check.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)',(PROBE_LOCK,))
            assert cur.fetchone()==(True,)
            cur.execute('SELECT pg_advisory_unlock(%s)',(PROBE_LOCK,))
            assert cur.fetchone()==(True,)
    print('Direct session probe PASS: lock survived read-only commit and was released')
    from research_kis_shared_quota import SharedQuotaLedger,BUFFER_SECONDS
    from concurrent.futures import ThreadPoolExecutor
    import time
    ledger=SharedQuotaLedger(dsn)
    peer=SharedQuotaLedger(dsn)
    ledger.initialize(4)
    peer.initialize(4)
    for operation in (lambda:peer.initialize(5),lambda:peer.claim('foreground',5)):
        try:operation()
        except ValueError as exc:assert str(exc)=='SHARED_QUOTA_POLICY_MISMATCH'
        else:raise AssertionError('conflicting cap accepted')
    first=ledger.claim('foreground',4)
    assert first and peer.claim('research',4) is None
    assert peer.finish(first) is True and ledger.finish(first) is False
    r=peer.claim('research',4)
    assert r and ledger.claim('research',4) is None
    # Concurrent independent connections cannot oversubscribe the common cap.
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims=list(pool.map(lambda _:SharedQuotaLedger(dsn).claim('foreground',4),range(4)))
    accepted=[x for x in claims if x is not None]
    assert len(accepted)==2 and peer.claim('foreground',4) is None
    assert ledger.finish(r) and all(peer.finish(x) for x in accepted)
    assert ledger.claim('foreground',4) is None
    time.sleep(BUFFER_SECONDS+0.2)
    abandoned=peer.claim('foreground',4)
    assert abandoned
    time.sleep(BUFFER_SECONDS+0.2)
    assert ledger.claim('research',4) is None
    status=ledger.status(4)
    assert status['unfinished_foreground']==1 and status['unfinished_research']==0
    assert ledger.finish(abandoned)
    assert ledger.status(4)['unfinished_foreground']==0
    print('Shared quota ledger PASS: cross-connection cap, foreground priority, completion buffer, abandoned lease blocking')
    # Use disposable CI DB only: emulate one historical, one dispatched and
    # one old pre-dispatch reservation. No provider traffic.
    from research_kis_shared_quota import TABLE as QUOTA_TABLE,UNDISPATCHED_GRACE_SECONDS
    from research_kis_shared_quota import Claim
    time.sleep(BUFFER_SECONDS+0.2)
    old=ledger.claim('research',4)
    assert old and ledger.mark_research_dispatched(old)
    assert not peer.mark_research_dispatched(old)
    with psycopg.connect(dsn) as fixture:
        with fixture.cursor() as cur:
            cur.execute(f"UPDATE {QUOTA_TABLE} SET research_claimed_at=clock_timestamp() - interval '1 hour' WHERE lease_id=%s",(old.lease_id,))
            cur.execute(f"INSERT INTO {QUOTA_TABLE}(lease_id,kind) VALUES ('ci-legacy','research')")
            cur.execute(f"INSERT INTO {QUOTA_TABLE}(lease_id,kind,research_claimed_at) VALUES ('ci-undispatched','research',clock_timestamp() - (%s * interval '1 second'))",(UNDISPATCHED_GRACE_SECONDS+1,))
    status=ledger.status(4)
    assert status['legacy_unknown_research']==1 and status['dispatched_unconfirmed_research']==1 and status['undispatched_research']==1
    assert peer.recover_undispatched_research(4)==1
    assert not ledger.mark_research_dispatched(Claim('ci-undispatched','research'))
    assert not ledger.mark_research_dispatched(Claim('ci-legacy','research'))
    assert peer.recover_undispatched_research(4)==0
    status=peer.status(4)
    assert status['unfinished_research']==2 and status['legacy_unknown_research']==1 and status['dispatched_unconfirmed_research']==1
    assert ledger.claim('research',4) is None
    print('Research lease lifecycle PASS: durable dispatch, CAS cancellation, legacy and dispatched reservations retained')
    tx=transaction_factory(dsn)
    days=tuple(date(2026,11,23)+timedelta(days=i) for i in range(3))
    symbol=SYMBOLS[0]
    assert read_status(dsn,days)['status']=='NOT_INITIALIZED'
    with exclusive_run(dsn,session_mode_confirmed=True) as alive:
        assert alive is not None and alive()
        with exclusive_run(dsn,session_mode_confirmed=True) as duplicate:
            assert duplicate is None
        blocked=run_exclusive(dsn,None,days,now=lambda:None,calendar=lambda t:{},admission=lambda:True,session_mode_confirmed=True)
        assert blocked['reason']=='DUPLICATE_RUN' and blocked['logical_calls']==0
    with exclusive_run(dsn,session_mode_confirmed=True) as alive:
        assert alive is not None and alive()
    assert run_exclusive(dsn,None,days,now=lambda:None,calendar=lambda t:{},admission=lambda:False)['status']=='BLOCKED'
    with tx() as (cur,kind):
        dataset=initialize(cur,kind,days)
        assert append(cur,kind,dataset,symbol,{days[1]:{'close':100}})==1
    with tx() as (cur,kind):
        assert saved(cur,kind,dataset,symbol)=={days[1]:{'close':100.0}}
        assert missing_windows(cur,kind,dataset,symbol)==[(days[0],),(days[2],)]
        assert append(cur,kind,dataset,symbol,{days[1]:{'close':100}})==0
    try:
        with tx() as (cur,kind):
            append(cur,kind,dataset,symbol,{days[0]:{'close':100}})
            raise RuntimeError('ROLLBACK_TEST')
    except RuntimeError as exc:
        assert str(exc)=='ROLLBACK_TEST'
    else:
        raise AssertionError('missing rollback')
    with tx() as (cur,kind):
        assert len(saved(cur,kind,dataset,symbol))==1
    class Fake:
        def __init__(self): self.calls=[]
        def get(self,path,tr,params):
            self.calls.append(params)
            return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),'bstp_nmix_prpr':'100'} for d in days if params['FID_INPUT_DATE_1']<=d.strftime('%Y%m%d')<=params['FID_INPUT_DATE_2']]}
    api=Fake()
    def run():
        return resume(api,days,transaction=tx,now=lambda:datetime(2026,11,26,21,tzinfo=KST),
            calendar=lambda t:{'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'},
            admission=lambda:True,pause=lambda n:None,max_windows=1)
    a=run()
    assert a['new_rows']==1 and a['logical_calls']==1
    b=run()
    assert b['new_rows']==1 and b['logical_calls']==1
    assert [(x['FID_INPUT_DATE_1'],x['FID_INPUT_DATE_2']) for x in api.calls]==[('20261123','20261124'),('20261124','20261125')]
    assert a['overlap_anchors_checked']==b['overlap_anchors_checked']==1
    with tx() as (cur,kind):
        assert missing_windows(cur,kind,dataset,symbol)==[]
        assert len(saved(cur,kind,dataset,symbol))==3
        cur.execute(f'SELECT COUNT(*) FROM {TABLE}')
        assert cur.fetchone()[0]==3
    status=read_status(dsn,days)
    assert status['stored_rows']==3 and status['complete_streams']==1
    assert status['expected_rows']==3*len(SYMBOLS) and status['training_ready'] is False
    assert status['status']=='PARTIAL' and status['execution_authorized'] is False
    # A provider response arriving after the owner session dies must never
    # be appended via a separate PostgreSQL connection. Use a second cohort.
    from research_history_run_control import LOCK_KEY
    other_days=tuple(d-timedelta(days=7) for d in days)
    class DisconnectingAPI:
        def __init__(self):self.calls=0
        def get(self,path,tr,params):
            self.calls+=1
            with psycopg.connect(dsn,autocommit=True) as killer:
                with killer.cursor() as cur:
                    cur.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=%s AND objsubid=1 AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",(LOCK_KEY,))
                    owner=cur.fetchall()
                    assert len(owner)==1
                    cur.execute('SELECT pg_terminate_backend(%s)',(owner[0][0],))
                    assert cur.fetchone()==(True,)
            return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),'bstp_nmix_prpr':'100'} for d in other_days]}
    interrupted=DisconnectingAPI()
    outcome=run_exclusive(dsn,interrupted,other_days,
        now=lambda:datetime(2026,11,26,21,tzinfo=KST),
        calendar=lambda t:{'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'},
        admission=lambda:True,session_mode_confirmed=True,pause=lambda n:None,max_windows=1)
    assert interrupted.calls==1 and outcome['status']=='PARTIAL' and outcome['new_rows']==0
    assert outcome['reason']=='CHECKPOINT_OR_ADMISSION_FAILED'
    assert read_status(dsn,other_days)['stored_rows']==0
    assert read_status(dsn,days)['stored_rows']==3
    with exclusive_run(dsn,session_mode_confirmed=True) as alive:
        assert alive and alive()
        with alive.transaction() as (cur,kind):
            assert kind=='postgresql'
            cur.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND objid=%s AND granted",(LOCK_KEY,))
            assert cur.fetchone()==(1,)
    print('Lock-bound checkpoint PASS: terminated owner cannot persist, lock released')
    with tx() as (cur,kind):
        cur.execute(f'UPDATE {TABLE} SET digest=%s WHERE dataset=%s AND symbol=%s',('bad',dataset,symbol))
    assert read_status(dsn,days)['status']=='UNAVAILABLE'
    from research_history_pipeline import read_panel
    from nova_v574_data_audit import INDEX_CODES
    # The integrated reader must not return corrupted or partial panels.
    broken,raw=read_panel(dsn,days)
    assert broken['status']=='UNAVAILABLE' and raw is None
    partial,raw=read_panel(dsn,other_days)
    assert partial['status']=='PARTIAL' and raw is None
    full_days=tuple(d-timedelta(days=14) for d in days)
    with tx() as (cur,kind):
        full_id=initialize(cur,kind,full_days)
        for s in SYMBOLS:
            value={'close':100} if s in INDEX_CODES else {'open':99,'high':102,'low':98,'close':100,'volume':1000,'turnover':None}
            assert append(cur,kind,full_id,s,{d:value for d in full_days})==len(full_days)
    complete,raw=read_panel(dsn,full_days)
    assert complete['status']=='COVERAGE_COMPLETE' and complete['stored_rows']==len(full_days)*len(SYMBOLS)
    assert set(raw)==set(SYMBOLS) and all(set(v)==set(full_days) for v in raw.values())
    print('Integrated snapshot PASS: full cohort, partial refusal and checksum failure')
    # Full-size checkpoint batch on the actual PostgreSQL adapter.
    bulk_days=tuple(date(2023,11,27)+timedelta(days=i) for i in range(729))
    with tx() as (cur,kind):
        bulk_id=initialize(cur,kind,bulk_days)
        rows={d:{'close':float(i+100)} for i,d in enumerate(bulk_days)}
        assert append(cur,kind,bulk_id,SYMBOLS[0],rows)==729
        assert append(cur,kind,bulk_id,SYMBOLS[0],rows)==0
        assert saved(cur,kind,bulk_id,SYMBOLS[0])==rows
    print('Bulk checkpoint PASS: 729 rows, idempotent verification')
    from research_krx_snapshot import initialize as krx_initialize, import_snapshot as krx_import, coverage as krx_coverage
    d=days[0]
    snapshot={'OutBlock_1':[{'BAS_DD':d.strftime('%Y%m%d'),'IDX_NM':'코스피','CLSPRC_IDX':'2500.25'}]}
    with tx() as (cur,kind):
        krx_id=krx_initialize(cur,kind,days)
        assert krx_id!=dataset
        assert krx_import(cur,kind,krx_id,'kospi_dd_trd',d,snapshot,authorized=lambda s,t:True)==1
        assert krx_import(cur,kind,krx_id,'kospi_dd_trd',d,snapshot,authorized=lambda s,t:True)==0
        assert krx_coverage(cur,kind,krx_id)['stored_rows']==1
    with tx() as (cur,kind):
        assert krx_coverage(cur,kind,krx_id)['stored_rows']==1
    assert read_status(dsn,full_days)['stored_rows']==len(full_days)*len(SYMBOLS)
    print('KRX isolated checkpoint PASS: separate basis, immutable day, persistent coverage')
    from research_krx_access_probe import Attempts
    a=Attempts(dsn);b=Attempts(dsn)
    a.initialize();b.initialize()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda x:x.claim('kospi'),(a,b)))
    assert sorted(results)==[False,True] and not b.claim('kospi')
    assert b.claim('kosdaq') and not a.claim('kosdaq')
    print('KRX one-shot probe ledger PASS: cross-process durable deduplication')
    from research_krx_collection import CollectorStore
    store=CollectorStore(dsn,days)
    store.setup()
    info,saved,held=store.status()
    assert info['stored_rows']==1 and not held
    assert not store.claim('kospi_dd_trd',d)
    assert store.claim('kosdaq_dd_trd',d)
    assert not store.claim('stk_bydd_trd',d)
    q={'OutBlock_1':[{'BAS_DD':d.strftime('%Y%m%d'),'IDX_NM':'코스닥','CLSPRC_IDX':'800.25'}]}
    try:store.save('kosdaq_dd_trd',d,q,authorized=lambda s,t:False)
    except ValueError:pass
    else:raise AssertionError('unauthorized save accepted')
    assert store.status()[2]=={('kosdaq_dd_trd',d.isoformat())}
    assert store.save('kosdaq_dd_trd',d,q,authorized=lambda s,t:(s,t)==('kosdaq_dd_trd',d))==1
    assert store.status()[0]['stored_rows']==2 and not store.status()[2]
    assert not store.claim('kosdaq_dd_trd',d)
    assert store.claim('stk_bydd_trd',d)
    store.fail('stk_bydd_trd',d)
    assert not store.claim('ksq_bydd_trd',d)
    print('KRX private collector PASS: committed claim, atomic snapshot, rollback, durable stop and resume')
    print('PostgreSQL integration PASS: durable resume, idempotence, rollback, isolated rows')
if __name__=='__main__':
    main()
