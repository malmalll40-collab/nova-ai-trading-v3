"""One bounded, non-persistent session probe. No collection or schema writes."""
from research_db_endpoint import direct_candidate

# Distinct from the research run lock; never blocks or acquires its key.
PROBE_LOCK=601_576_002

def verify_session(dsn,*,connect=None):
    """Check session-level lock survives commit on this connection only.

    All transactions read-only; lock is transient and connection is closed.
    The caller never receives a DSN, PID, database name, user, or exception.
    """
    base={'direct_connection_status':'UNAVAILABLE','session_probe_verified':False,
          'session_mode_confirmed':False,'execution_authorized':False}
    if not dsn:
        return {**base,'reason':'DIRECT_CANDIDATE_UNAVAILABLE'}
    try:
        if connect is None:
            import psycopg
            connect=psycopg.connect
        with connect(dsn,connect_timeout=5,autocommit=True) as db:
            with db.transaction():
                with db.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute("SET LOCAL statement_timeout = '3s'")
                    cur.execute('SELECT pg_backend_pid(), pg_is_in_recovery(), pg_try_advisory_lock(%s)',(PROBE_LOCK,))
                    pid,recovery,locked=cur.fetchone()
                    if recovery or not locked:
                        return {**base,'reason':'PROBE_LOCK_OR_PRIMARY_UNAVAILABLE'}
            with db.transaction():
                with db.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute("SET LOCAL statement_timeout = '3s'")
                    cur.execute("SELECT pg_backend_pid(), EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=0 AND objid=%s AND objsubid=1 AND granted AND mode='ExclusiveLock')",(PROBE_LOCK,))
                    if cur.fetchone()!=(pid,True):
                        return {**base,'reason':'SESSION_CONTINUITY_UNVERIFIED'}
                    cur.execute('SELECT pg_advisory_unlock(%s)',(PROBE_LOCK,))
                    if cur.fetchone()!=(True,):
                        return {**base,'reason':'PROBE_RELEASE_UNVERIFIED'}
        return {**base,'direct_connection_status':'READ_OK','session_probe_verified':True,
                'proof_scope':'THIS_CONNECTION_ONLY'}
    except Exception:
        return {**base,'reason':'DIRECT_SESSION_PROBE_FAILED'}

def probe(pooled_dsn,*,verifier=verify_session):
    candidate=direct_candidate(pooled_dsn)
    if candidate is None:
        return {'direct_connection_status':'UNAVAILABLE','reason':'DIRECT_CANDIDATE_UNAVAILABLE',
                'session_probe_verified':False,'session_mode_confirmed':False,'execution_authorized':False}
    try:
        r=verifier(candidate)
        ok=r.get('direct_connection_status')=='READ_OK' and r.get('session_probe_verified') is True
        return {'direct_connection_status':'READ_OK' if ok else 'UNAVAILABLE',
                'session_probe_verified':ok,'session_mode_confirmed':False,
                'proof_scope':'THIS_CONNECTION_ONLY' if ok else 'NONE',
                'execution_authorized':False}
    except Exception:
        return {'direct_connection_status':'UNAVAILABLE','session_probe_verified':False,
                'session_mode_confirmed':False,'execution_authorized':False}
