"""Research history run coordination, not account-wide KIS quota admission.

Explicitly invoked only. No startup hook, database migrations or provider calls.
The dedicated advisory-lock connection must be a direct/session-mode PostgreSQL
connection, never a transaction-pooling connection.
"""
from contextlib import contextmanager
from urllib.parse import urlsplit
from research_history_checkpoint import TABLE, DATASETS, manifest, saved
from nova_v576_expanded_coverage import SYMBOLS
from nova_v574_data_audit import INDEX_CODES

LOCK_KEY=601_576_001

@contextmanager
def exclusive_run(dsn, *, session_mode_confirmed=False, connect=None):
    if session_mode_confirmed is not True:
        raise ValueError('SESSION_MODE_UNVERIFIED')
    if not isinstance(dsn,str) or urlsplit(dsn).scheme not in ('postgres','postgresql'):
        raise ValueError('POSTGRES_DSN_REQUIRED')
    if connect is None:
        import psycopg
        connect=psycopg.connect
    # Session lock releases when dedicated connection closes even on exceptions.
    with connect(dsn,connect_timeout=5,autocommit=True) as db:
        with db.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
            cur.execute('SELECT pg_try_advisory_lock(%s)',(LOCK_KEY,))
            acquired=cur.fetchone()==(True,)
            if not acquired:
                yield None
                return
            def alive():
                # A lost/replaced session cannot continue claiming the lock.
                try:
                    cur.execute('SELECT pg_backend_pid()')
                    return cur.fetchone()==(pid,)
                except Exception:
                    return False
            try:
                cur.execute('SELECT pg_backend_pid()')
                pid=cur.fetchone()[0]
                @contextmanager
                def transaction():
                    # Use the very session that owns the advisory lock. If it dies,
                    # this transaction cannot commit via a different connection.
                    with db.transaction():
                        with db.cursor() as tx:
                            if not alive():
                                raise RuntimeError('RUN_LOCK_LOST')
                            tx.execute("SET LOCAL statement_timeout = '5s'")
                            tx.execute("SET LOCAL lock_timeout = '2s'")
                            yield tx,'postgresql'
                            if not alive():
                                raise RuntimeError('RUN_LOCK_LOST')
                alive.transaction=transaction
                yield alive
            finally:
                try:cur.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY,))
                except Exception:pass

def read_status(dsn,sessions,*,connect=None):
    """No table creation and no provider requests. Fail closed on corrupt rows."""
    days=tuple(sessions)
    dataset,_=manifest(days)
    base={'dataset':dataset,'expected_rows':len(days)*len(SYMBOLS),
          'research_only':True,'training_ready':False,'strategy_adoption':'HOLD',
          'execution_authorized':False,'orders_enabled':False}
    if not isinstance(dsn,str) or urlsplit(dsn).scheme not in ('postgres','postgresql'):
        return {**base,'status':'UNAVAILABLE','reason':'POSTGRES_DSN_REQUIRED'}
    if connect is None:
        import psycopg
        connect=psycopg.connect
    try:
        with connect(dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute('SELECT to_regclass(%s),to_regclass(%s)',(DATASETS,TABLE))
                tables=cur.fetchone()
                if tables==(None,None):
                    return {**base,'status':'NOT_INITIALIZED','stored_rows':0,'complete_streams':0}
                if None in tables:
                    return {**base,'status':'UNAVAILABLE','reason':'SCHEMA_INCOMPLETE'}
                cur.execute(f'SELECT 1 FROM {DATASETS} WHERE id=%s',(dataset,))
                if cur.fetchone() is None:
                    return {**base,'status':'NOT_INITIALIZED','stored_rows':0,'complete_streams':0}
                streams=[]
                for symbol in SYMBOLS:
                    data=saved(cur,'postgresql',dataset,symbol)
                    streams.append({'symbol':symbol,'stored_rows':len(data),
                        'missing_rows':len(days)-len(data),
                        'zero_volume':None if symbol in INDEX_CODES else sum(r['volume']==0 for r in data.values()),
                        'missing_turnover':None if symbol in INDEX_CODES else sum(r['turnover'] is None for r in data.values())})
                rows=sum(s['stored_rows'] for s in streams)
                cur.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE dataset=%s',(dataset,))
                if cur.fetchone()!=(rows,):
                    raise ValueError('UNEXPECTED_STREAM_ROWS')
                complete=sum(s['missing_rows']==0 for s in streams)
                return {**base,'status':'COVERAGE_COMPLETE' if complete==len(SYMBOLS) else 'PARTIAL',
                        'stored_rows':rows,'complete_streams':complete,'streams':streams}
    except Exception:
        return {**base,'status':'UNAVAILABLE','reason':'READ_OR_INTEGRITY_FAILED'}

def run_exclusive(dsn,api,sessions,*,now,calendar,admission,
                  session_mode_confirmed=False,connect=None,**limits):
    """Lock only coordinates research runs in the same database; not quota proof."""
    from research_history_resume import resume
    blocked={'status':'BLOCKED','reason':'RUN_LOCK_OR_ADMISSION_UNVERIFIED',
             'new_rows':0,'logical_calls':0,'training_ready':False,
             'strategy_adoption':'HOLD','orders_enabled':False}
    try:
        if admission() is not True:
            return blocked
        with exclusive_run(dsn,session_mode_confirmed=session_mode_confirmed,connect=connect) as alive:
            if alive is None:
                return {**blocked,'reason':'DUPLICATE_RUN'}
            if callable(getattr(api,'bind_lock',None)):
                api.bind_lock(alive)
            return resume(api,sessions,transaction=alive.transaction,now=now,calendar=calendar,
                          admission=lambda:alive() and admission() is True,**limits)
    except Exception:
        return {**blocked,'reason':'RUN_COORDINATION_FAILED'}
