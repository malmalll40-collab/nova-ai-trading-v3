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
    print('PostgreSQL integration PASS: durable resume, idempotence, rollback, isolated rows')
if __name__=='__main__':
    main()
