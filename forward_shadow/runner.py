"""One bounded daily shadow session. Transactional durable claims; no broker."""
import gzip, io, json, os, sqlite3, tempfile, uuid, time
from contextlib import closing
from datetime import datetime, time as walltime
from pathlib import Path
import requests
from . import forward as f, collector as c
from research_forward_seed import NS, KEY, SEED_SHA, MAX_DB_BEFORE_SEED

MAX_RAW=128*1024*1024
MAX_COMPRESSED=48*1024*1024
MAX_BODY=16*1024*1024
LOCK=601576110

def decode(digest,z,limit=MAX_RAW,packed_limit=MAX_COMPRESSED):
    if not 0<len(z)<=packed_limit:raise ValueError('PACKED_SIZE')
    with gzip.GzipFile(fileobj=io.BytesIO(z)) as g:raw=g.read(limit+1)
    if not 0<len(raw)<=limit or f.sha(raw)!=digest:raise ValueError('STATE_HASH')
    return raw

def encode(raw,limit=MAX_RAW,packed_limit=MAX_COMPRESSED):
    if not 0<len(raw)<=limit:raise ValueError('RAW_SIZE')
    z=gzip.compress(raw,compresslevel=4,mtime=0)
    if len(z)>packed_limit:raise ValueError('PACKED_SIZE')
    return f.sha(raw),z

def check_db(path,last,revision):
    c.verify_frozen()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('SQLITE_INTEGRITY')
        if dict(db.execute('SELECT key,value FROM metadata'))!={'source_sha256':f.SOURCE,'freeze':f.FREEZE,'strategy':KEY,'orders_enabled':'false'}:raise ValueError('STATE_METADATA')
        n,day=db.execute('SELECT count(*),max(day) FROM sessions').fetchone()
        if day!=last or n!=65+revision:raise ValueError('STATE_REVISION')
        if revision==0 and f.sha(path.read_bytes())!=SEED_SHA:raise ValueError('SEED_HASH')

class Store:
    def __init__(self,dsn):
        import psycopg
        if not dsn:raise ValueError('DATABASE_NOT_CONFIGURED')
        self.db=psycopg.connect(dsn,connect_timeout=10,autocommit=True)
    def close(self):self.db.close()
    def owned(self,cur,owner):
        cur.execute(f'SELECT revision,last_day FROM {NS}.checkpoint WHERE id=%s AND owner=%s FOR UPDATE',(KEY,owner))
        row=cur.fetchone()
        if row is None:raise ValueError('OWNER_LOST')
        return row
    def budget(self,cur,extra):
        cur.execute('SELECT pg_database_size(current_database())')
        if cur.fetchone()[0]+extra>MAX_DB_BEFORE_SEED:raise ValueError('LOCAL_STORAGE_GUARD')
    def acquire(self,owner):
        with self.db.transaction(),self.db.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20s'")
            cur.execute('SELECT pg_try_advisory_xact_lock(%s)',(LOCK,))
            if cur.fetchone()!=(True,):raise ValueError('BUSY')
            self.budget(cur,MAX_COMPRESSED+4*MAX_BODY)
            cur.execute(f'UPDATE {NS}.checkpoint SET owner=%s,acquired_at=now() WHERE id=%s AND owner IS NULL AND octet_length(state)<=%s RETURNING revision,digest,state,last_day',(owner,KEY,MAX_COMPRESSED))
            row=cur.fetchone()
            if row is None:raise ValueError('BUSY_OR_UNINITIALIZED')
            return row
    def release(self,owner):
        with self.db.transaction(),self.db.cursor() as cur:
            cur.execute(f'UPDATE {NS}.checkpoint SET owner=NULL,acquired_at=NULL WHERE id=%s AND owner=%s',(KEY,owner))
    def begin(self,owner,day,service):
        with self.db.transaction(),self.db.cursor() as cur:
            _,last=self.owned(cur,owner)
            if f.next_day(last)!=day or service not in c.SERVICES:raise ValueError('DAY_OR_SERVICE')
            # Any unresolved dispatch blocks the entire run, not just this service.
            cur.execute(f'SELECT 1 FROM {NS}.response WHERE id=%s AND body IS NULL LIMIT 1',(KEY,))
            if cur.fetchone():raise ValueError('UNCERTAIN_CALL')
            cur.execute(f'SELECT body,digest FROM {NS}.response WHERE id=%s AND day=%s AND service=%s',(KEY,day,service))
            row=cur.fetchone()
            if row is not None:return decode(row[1],bytes(row[0]),MAX_BODY,MAX_BODY)
            self.budget(cur,MAX_COMPRESSED+MAX_BODY)
            cur.execute(f'INSERT INTO {NS}.response(id,day,service) VALUES (%s,%s,%s)',(KEY,day,service))
            return None
    def finish(self,owner,day,service,body):
        c.parse_payload(body,day)
        digest,z=encode(body,MAX_BODY,MAX_BODY)
        with self.db.transaction(),self.db.cursor() as cur:
            self.owned(cur,owner);self.budget(cur,len(z))
            cur.execute(f'UPDATE {NS}.response SET body=%s,digest=%s WHERE id=%s AND day=%s AND service=%s AND body IS NULL',(z,digest,KEY,day,service))
            if cur.rowcount!=1:raise ValueError('CLAIM_LOST')
    def commit(self,owner,revision,day,raw):
        digest,z=encode(raw)
        with self.db.transaction(),self.db.cursor() as cur:
            r=self.owned(cur,owner)
            if r[0]!=revision or f.next_day(r[1])!=day:raise ValueError('REVISION_OR_DAY')
            cur.execute(f'SELECT service FROM {NS}.response WHERE id=%s AND day=%s AND body IS NOT NULL',(KEY,day))
            if {x[0] for x in cur.fetchall()}!=set(c.SERVICES):raise ValueError('INCOMPLETE_RESPONSES')
            self.budget(cur,len(z))
            cur.execute(f'UPDATE {NS}.checkpoint SET revision=revision+1,digest=%s,state=%s,last_day=%s,updated_at=now() WHERE id=%s AND owner=%s AND revision=%s',(digest,z,day,KEY,owner,revision))
            if cur.rowcount!=1:raise ValueError('REVISION_LOST')
            return revision+1

def eligible_env():
    return (os.getenv('NOVA_FORWARD_CLOUD_ENABLED')=='1'
            and os.getenv('NOVA_KRX_PRIVATE_COLLECTION')=='off'
            and os.getenv('NOVA_FORWARD_SEED_ENABLED')!='1'
            and os.getenv('NOVA_FORWARD_VERIFY_ON_START')!='1')

def run(store,key,*,clock=f.now,fetch=c.get_payload,pause=time.sleep):
    if not key.strip():raise ValueError('KRX_KEY_REQUIRED')
    c.verify_frozen();owner=uuid.uuid4().hex
    rev,digest,z,last=store.acquire(owner)
    try:
        with tempfile.TemporaryDirectory(prefix='nova_shadow_') as tmp:
            path=Path(tmp);db=path/'forward.sqlite';db.write_bytes(decode(digest,bytes(z)))
            check_db(db,last,rev)
            day=f.next_day(last);start=clock().astimezone(f.KST)
            if start<datetime.fromisoformat(f.FREEZE):raise ValueError('CLOCK_BEFORE_FREEZE')
            if f.dayfmt(day)>start.date() or (f.dayfmt(day)==start.date() and start.time()<walltime(18,30)):
                return {'state':'NO_SESSION_READY','next_day':day,'report':f.report(db),'orders_enabled':False}
            with closing(sqlite3.connect(db)) as conn:
                prior=json.loads(conn.execute('SELECT market_counts FROM sessions WHERE day=?',(last,)).fetchone()[0])
            payloads={}
            with requests.Session() as session:
                for service in c.SERVICES:
                    body=store.begin(owner,day,service)
                    if body is None:
                        body=fetch(session,service,day,key)
                        store.finish(owner,day,service,body)
                        pause(5)
                    payloads[service]=body
            b,m=c.convert(payloads,day,prior)
            csv=path/'daily.csv';manifest=path/'manifest.json'
            csv.write_bytes(b);manifest.write_bytes(c.canonical(m))
            x=f.ingest(db,csv,manifest,observed=clock())
            check_db(db,day,rev+1)
            newrev=store.commit(owner,rev,day,db.read_bytes())
            return {'state':'INGESTED','day':day,'mode':x['mode'],'revision':newrev,'report':f.report(db),'orders_enabled':False}
    finally:
        store.release(owner)

def execute():
    if not eligible_env():raise ValueError('EXPLICIT_ENABLE_AND_LEGACY_OFF_REQUIRED')
    s=Store(os.getenv('DATABASE_URL','').strip())
    try:return run(s,os.getenv('KRX_API_KEY','').strip())
    finally:s.close()
