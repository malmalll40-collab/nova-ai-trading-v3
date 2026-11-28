"""Read-only proof of the actual stored frozen seed; no collector or orders."""
import gzip
import hashlib
import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import research_forward_seed as seed

FIRST = '20260821'
SESSIONS = 65
MARKET_ROWS = {'KOSPI':61125,'KOSDAQ':118647}
SOURCE = 'b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
FREEZE = '2026-11-28T06:25:00+09:00'
CASH = 10000000.0

def require(condition, code):
    if not condition:
        raise seed.SeedError(code)

def verify_blob(blob):
    require(0 < len(blob) <= seed.MAX_UPLOAD, 'COMPRESSED_SIZE')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            raw = stream.read(seed.SEED_SIZE+1)
    except (OSError, EOFError, ValueError):
        raise seed.SeedError('GZIP_INVALID') from None
    require(len(raw)==seed.SEED_SIZE and hashlib.sha256(raw).hexdigest()==seed.SEED_SHA,
            'ACTUAL_BYTES_HASH_MISMATCH')
    # Only known exact seed bytes may reach SQLite. No arbitrary uploaded database.
    with tempfile.TemporaryDirectory(prefix='nova_forward_verify_') as tmp:
        path = Path(tmp)/'seed.sqlite'
        path.write_bytes(raw)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            require(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'SQLITE_INTEGRITY')
            meta=dict(db.execute('SELECT key,value FROM metadata'))
            require(meta=={'source_sha256':SOURCE,'freeze':FREEZE,'strategy':seed.KEY,'orders_enabled':'false'},'METADATA_MISMATCH')
            count, first, last = db.execute('SELECT count(*),min(day),max(day) FROM sessions').fetchone()
            require((count,first,last)==(SESSIONS,FIRST,seed.SEED_DAY),'SESSION_COUNT_OR_RANGE')
            bars=db.execute('SELECT count(*) FROM bars').fetchone()[0]
            market=dict(db.execute('SELECT market,count(*) FROM bars GROUP BY market'))
            require(bars==seed.SEED_BARS and market==MARKET_ROWS,'BAR_COUNT_OR_MARKET')
            counts={}
            for day,m,n in db.execute('SELECT day,market,count(*) FROM bars GROUP BY day,market'):
                counts.setdefault(day,{})[m]=n
            require(len(counts)==SESSIONS,'BAR_DAY_COUNT')
            for day,mode,observed,digest,declared,indices,candidates,equity,cash,positions,events in db.execute(
                'SELECT day,mode,observed_at,digest,market_counts,index_close,candidates,equity,cash,positions,events FROM sessions'):
                require(mode=='SEED' and observed==FREEZE and digest==SOURCE,'NON_SEED_SESSION')
                require(counts.get(day)==json.loads(declared) and set(counts[day])==set(MARKET_ROWS),'SESSION_MARKET_COUNTS')
                require(json.loads(indices)=={} and json.loads(candidates)==[] and equity==cash==CASH
                        and json.loads(positions)=={} and json.loads(events)==[],'NONEMPTY_SHADOW_ACCOUNT')
    return {'status':'VERIFIED','actual_bytes_hash_match':True,'sqlite_integrity':True,
            'bars':bars,'sessions':count,'first_day':first,'last_day':last,
            'market_rows':market,'prospective_sessions':0,'closed_assumed':0,
            'initial_cash_krw':CASH,'orders_enabled':False,'strategy_adoption':'HOLD'}

def verify(dsn):
    import psycopg
    if not dsn:
        raise seed.SeedError('DATABASE_NOT_CONFIGURED')
    with psycopg.connect(dsn,connect_timeout=10) as db, db.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute("SET LOCAL statement_timeout = '15s'")
        status=seed.read_status(cur)
        require(status['seed_initialized'],'UNINITIALIZED')
        require(status['revision']==0 and not status['busy'],'NOT_IDLE_OR_SEED_REVISION')
        cur.execute(f'SELECT state FROM {seed.NS}.checkpoint WHERE id=%s AND revision=0 '
                    'AND owner IS NULL AND digest=%s AND last_day=%s AND octet_length(state)<=%s',
                    (seed.KEY,seed.SEED_SHA,seed.SEED_DAY,seed.MAX_UPLOAD))
        row=cur.fetchone()
        require(row is not None,'SEED_READBACK_BLOCKED')
        data=verify_blob(bytes(row[0]))
        cur.execute(f'SELECT count(*) FROM {seed.NS}.response WHERE id=%s',(seed.KEY,))
        require(cur.fetchone()==(0,),'UNEXPECTED_RESPONSE_CLAIMS')
        return {**data,'revision':0,'database_bytes':status['database_bytes'],
                'compressed_bytes':status['compressed_bytes'],'response_claims':0}
