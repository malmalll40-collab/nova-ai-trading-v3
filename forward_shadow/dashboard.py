"""Authenticated read-only shadow performance from committed Neon checkpoint.
No collection, order calls, or model adoption. All returns are hypothetical.
"""
import json, os, sqlite3, tempfile, threading, time
from pathlib import Path
from contextlib import closing
from datetime import datetime
from . import runner as r, forward as f, collector as c, evidence_audit
from research_forward_seed import NS, KEY, MAX_DB_BEFORE_SEED

def summary(path,last,rev):
    r.check_db(path,last,rev)
    rep=f.report(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        total,first=db.execute('SELECT count(*),min(day) FROM bars').fetchone()
        market=dict(db.execute('SELECT market,count(*) FROM bars GROUP BY market'))
        sessions=db.execute('SELECT day,mode,equity,cash,positions,events,candidates,market_counts FROM sessions ORDER BY day').fetchall()
        audit=evidence_audit.ledger(db.execute('SELECT day,mode,observed_at,candidates,events FROM sessions ORDER BY day'))
    if sum(market.values())!=total or len(sessions)!=65+rev or set(market)!={'KOSPI','KOSDAQ'}:
        raise ValueError('COUNTS_MISMATCH')
    for row in sessions:
        if row[1] not in ('SEED','LATE_OR_BRIDGE','PROSPECTIVE'):raise ValueError('MODE_INVALID')
    current=sessions[-1]
    events=[e for row in sessions if row[1]!='SEED' for e in json.loads(row[5])]
    exits=[float(e['net_pct']) for e in events if e['event']=='EXIT_ASSUMED']
    if len(exits)!=rep['closed_assumed'] or audit['exit_assumed_count']!=len(exits):raise ValueError('EXIT_COUNT')
    if audit['prospective_ledger_sessions']!=rep['prospective_sessions'] or audit['prospective_candidates']!=rep['candidate_count']:raise ValueError('LEDGER_COUNT')
    wins=sum(v>0 for v in exits)
    positions=[{'code':code,'shares':p['n'],'cost_krw':p['cost'],'mark_krw':p['mark'],
                'locked':bool(p['locked']),'entry_day':sessions[p['entry_seq']][0]}
               for code,p in sorted(json.loads(current[4]).items())]
    curve=[{'day':row[0],'equity_krw':row[2],'mode':row[1]} for row in sessions if row[1]!='SEED']
    return {**rep,**audit,'total_bars':total,'market_bars':market,'first_day':first,'total_sessions':len(sessions),
            'last_market_counts':json.loads(current[7]),'cash_krw':current[3],
            'positions':positions,'equity_curve':curve,'win_rate_pct':100*wins/len(exits) if exits else None,
            'winning_exits':wins,'mean_closed_return_pct':sum(exits)/len(exits) if exits else None,
            'latest_candidates':json.loads(current[6]) if current[1]=='PROSPECTIVE' else [],
            'minimum_prospective_sessions':60,'minimum_closed_trades':30,
            'validation_status':'INSUFFICIENT_EVIDENCE' if rep['prospective_sessions']<60 or len(exits)<30 else 'MANUAL_REVIEW_REQUIRED',
            'initial_cash_krw':f.CASH,'roundtrip_cost_pct':2*f.FEE*100,
            'benchmark_verified':False,'corporate_events_verified':False,'research_only':True,
            'strategy_adoption':'HOLD','orders_enabled':False}

def read(dsn):
    import psycopg
    if not dsn:raise ValueError('DATABASE_NOT_CONFIGURED')
    with psycopg.connect(dsn,connect_timeout=8) as db, db.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute("SET LOCAL statement_timeout = '12s'")
        cur.execute(f'SELECT revision,digest,state,last_day,owner IS NOT NULL,updated_at '
                    f'FROM {NS}.checkpoint WHERE id=%s AND octet_length(state)<=%s',(KEY,r.MAX_COMPRESSED))
        row=cur.fetchone()
        if row is None:raise ValueError('UNINITIALIZED')
        rev,digest,blob,last,busy,updated=row
        cur.execute(f'SELECT day,service,body IS NOT NULL FROM {NS}.response WHERE id=%s',(KEY,))
        claims=cur.fetchall()
        cur.execute('SELECT pg_database_size(current_database())')
        size=int(cur.fetchone()[0])
    complete={(day,svc) for day,svc,ok in claims if ok}
    unresolved=sum(not ok for _,_,ok in claims)
    with tempfile.TemporaryDirectory(prefix='nova_forward_report_') as tmp:
        path=Path(tmp)/'shadow.sqlite'
        path.write_bytes(r.decode(digest,bytes(blob)))
        out=summary(path,last,rev)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            days=[x[0] for x in db.execute("SELECT day FROM sessions WHERE mode!='SEED'")]
        if any((day,svc) not in complete for day in days for svc in c.SERVICES):
            raise ValueError('RESPONSE_LEDGER_MISMATCH')
    next_day=f.next_day(last)
    now=f.now()
    expected=now.date().isoformat()
    overdue=f.dayfmt(next_day)<now.date() or (f.dayfmt(next_day)==now.date() and now.hour>=20)
    return {**out,'status':'VERIFIED','revision':rev,'committed_at':updated.isoformat(),
            'read_at':now.isoformat(),'next_session':next_day,'collection_busy':busy,
            'unresolved_calls':unresolved,'completed_response_claims':len(complete),
            'database_bytes':size,'storage_guard_bytes':MAX_DB_BEFORE_SEED,
            'schedule_configured':os.getenv('NOVA_FORWARD_SCHEDULE_ENABLED')=='1' and r.eligible_env(),
            'collection_status':'BUSY' if busy else 'BLOCKED_UNCERTAIN' if unresolved else 'OVERDUE' if overdue else 'CURRENT',
            'live_schedule_verified':False}

# Single-flight bounded work. Repeated taps reuse the same recent verified snapshot.
# No stale fallback on failure. Cached data includes its original verification time.
_lock=threading.Lock()
_cached=None
def get(dsn,*,read_fn=read,clock=time.monotonic):
    global _cached
    if not _lock.acquire(blocking=False):raise ValueError('REPORT_BUSY')
    try:
        now=clock()
        if _cached and _cached[0]==dsn and 0<=now-_cached[1]<20:
            return {**_cached[2],'cached':True}
        _cached=None
        out=read_fn(dsn)
        _cached=(dsn,clock(),out)
        return {**out,'cached':False}
    finally:_lock.release()
