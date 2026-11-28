"""Private immutable monthly KRX archive; not training-ready, no API calls."""
import csv,gzip,hashlib,io,json,re
from contextlib import contextmanager
from datetime import datetime
from research_forward_seed import NS as SHADOW, KEY as SHADOW_KEY

NS='nova_krx_archive_v1'
MANIFEST_SHA='14319ba99836953d119091a1b62cf3bca56489cc750fcd30b049c3437238795b'
SOURCE='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
TOTAL=1995617
NCHUNKS=74
NBYTES=93635140
MAX_RAW=6*1024*1024
MAX_CHUNK=2*1024*1024
MAX_MANIFEST=64*1024
MAX_COVERAGE=128*1024
# Preserve at least 128 MiB below existing 384 MiB shadow guard.
# This local database-size estimate is NOT the Neon project quota.
GUARD=256*1024*1024
HEADROOM=16*1024*1024
LOCK=601576110
HEADER='market,BAS_DD,ISU_CD,ISU_NM,MKT_NM,SECT_TP_NM,TDD_CLSPRC,CMPPREVDD_PRC,FLUC_RT,TDD_OPNPRC,TDD_HGPRC,TDD_LWPRC,ACC_TRDVOL,ACC_TRDVAL,MKTCAP,LIST_SHRS'
DDL=(
 f'CREATE SCHEMA IF NOT EXISTS {NS}',
 f'CREATE TABLE IF NOT EXISTS {NS}.manifest (id text PRIMARY KEY, body bytea NOT NULL, coverage bytea, created_at timestamptz NOT NULL DEFAULT now())',
 f'CREATE TABLE IF NOT EXISTS {NS}.chunk (id text NOT NULL REFERENCES {NS}.manifest(id), name text NOT NULL, body bytea NOT NULL, rows int NOT NULL, raw_sha text NOT NULL, PRIMARY KEY(id,name))',
 f'CREATE TABLE IF NOT EXISTS {NS}.verified_member (id text NOT NULL REFERENCES {NS}.manifest(id), name text NOT NULL, rows int NOT NULL, sha text NOT NULL, verified_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(id,name))',
)
class ArchiveError(ValueError):pass
def require(ok,code):
    if not ok:raise ArchiveError(code)
def sha(b):return hashlib.sha256(b).hexdigest()
def manifest(raw):
    require(0<len(raw)<=MAX_MANIFEST and sha(raw)==MANIFEST_SHA,'MANIFEST_HASH')
    m=json.loads(raw)
    require(m['source_sha256']==SOURCE and m['format']=='exact-csv-month-gzip-v1' and m['training_ready'] is False,'MANIFEST_ID')
    require(len(m['chunks'])==NCHUNKS and sum(x['rows'] for x in m['chunks'])==TOTAL and sum(x['compressed_bytes'] for x in m['chunks'])==NBYTES,'MANIFEST_COUNTS')
    require(len({x['file'] for x in m['chunks']})==NCHUNKS,'MANIFEST_DUPLICATE')
    for x in m['chunks']:
        require(re.fullmatch(r'(KOSPI|KOSDAQ)_\d{6}\.csv\.gz',x['file']) and x['source_member'] in m['members'] and 0<x['raw_bytes']<=MAX_RAW and 0<x['compressed_bytes']<=MAX_CHUNK,'MANIFEST_CHUNK')
    return m

def chunk(raw,x):
    require(len(raw)==x['compressed_bytes'] and len(raw)<=MAX_CHUNK and sha(raw)==x['compressed_sha256'],'COMPRESSED_HASH')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as g:body=g.read(x['raw_bytes']+1)
    except (OSError,EOFError,ValueError):raise ArchiveError('GZIP_INVALID') from None
    require(len(body)==x['raw_bytes'] and sha(body)==x['raw_sha256'],'RAW_HASH')
    rows=csv.reader(io.StringIO(body.decode('utf-8-sig'),newline=''))
    require(next(rows,None)==HEADER.split(','),'CSV_HEADER')
    n=0;days=set()
    for row in rows:
        require(len(row)==16 and row[0]==x['market'] and re.fullmatch(r'\d{8}',row[1]) and row[1].startswith(x['month']),'CSV_ROW')
        n+=1;days.add(row[1])
    require(n==x['rows'] and len(days)==x['sessions'] and min(days)==x['first_day'] and max(days)==x['last_day'],'CSV_COUNTS')
    return body

@contextmanager
def tx(dsn,write=False):
    import psycopg
    require(bool(dsn),'DATABASE_NOT_CONFIGURED')
    with psycopg.connect(dsn,connect_timeout=8) as db,db.cursor() as cur:
        if not write:cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute("SET LOCAL statement_timeout = '25s'")
        cur.execute("SET LOCAL lock_timeout = '3s'")
        if write:
            cur.execute('SELECT pg_try_advisory_xact_lock(%s)',(LOCK,))
            require(cur.fetchone()==(True,),'ARCHIVE_BUSY')
            # Shared lock and owner guard serialize against shadow acquisition.
            cur.execute(f'SELECT owner FROM {SHADOW}.checkpoint WHERE id=%s',(SHADOW_KEY,))
            require(cur.fetchone()==(None,),'SHADOW_BUSY_OR_MISSING')
        yield cur

def size(cur):
    cur.execute('SELECT pg_database_size(current_database())')
    return int(cur.fetchone()[0])

def load(cur):
    cur.execute(f'SELECT body,coverage FROM {NS}.manifest WHERE id=%s',(MANIFEST_SHA,))
    row=cur.fetchone()
    require(row is not None,'MANIFEST_REQUIRED')
    return manifest(bytes(row[0])),row[1]

def metadata(cur,m):
    cur.execute(f'SELECT name,rows,raw_sha,octet_length(body) FROM {NS}.chunk WHERE id=%s',(MANIFEST_SHA,))
    got={x[0]:x[1:] for x in cur.fetchall()}
    expected={x['file']:(x['rows'],x['raw_sha256'],x['compressed_bytes']) for x in m['chunks']}
    require(all(k in expected and v==expected[k] for k,v in got.items()),'STORED_METADATA_MISMATCH')
    return got

def budget(cur,m,got):
    remaining=sum(x['compressed_bytes'] for x in m['chunks'] if x['file'] not in got)
    require(size(cur)+remaining+HEADROOM<=GUARD,'LOCAL_ARCHIVE_STORAGE_GUARD')

def status(cur):
    n=size(cur)
    cur.execute('SELECT to_regclass(%s)',(NS+'.manifest',))
    info={'manifest_registered':False,'stored_chunks':0,'stored_rows':0,'verified_members':[],
          'stored_files':[],'coverage_verified':False,'archive_verified':False,'database_bytes':n,
          'local_guard_bytes':GUARD,'expected_rows':TOTAL,'expected_chunks':NCHUNKS,
          'training_ready':False,'orders_enabled':False}
    if cur.fetchone()[0] is None:return info
    cur.execute(f'SELECT body,coverage FROM {NS}.manifest WHERE id=%s',(MANIFEST_SHA,))
    row=cur.fetchone()
    if row is None:return info
    m=manifest(bytes(row[0]))
    cov=row[1]
    coverage_ok=cov is not None and sha(bytes(cov))==m['coverage_sha256']
    if cov is not None:require(coverage_ok,'COVERAGE_HASH')
    got=metadata(cur,m)
    cur.execute(f'SELECT name,rows,sha FROM {NS}.verified_member WHERE id=%s',(MANIFEST_SHA,))
    verified=[]
    for name,rows,digest in cur.fetchall():
        require(name in m['members'] and rows==m['members'][name]['rows'] and digest==m['members'][name]['sha256'],'MEMBER_PROOF_MISMATCH')
        require(all(x['file'] in got for x in m['chunks'] if x['source_member']==name),'MEMBER_INCOMPLETE')
        verified.append(name)
    return {**info,'manifest_registered':True,'stored_chunks':len(got),'stored_rows':sum(v[0] for v in got.values()),
            'stored_files':sorted(got),'coverage_verified':coverage_ok,'verified_members':sorted(verified),
            'archive_verified':len(got)==NCHUNKS and len(verified)==len(m['members']) and coverage_ok,
            'metadata_only_counts':True,'manifest_sha256':MANIFEST_SHA}

def read_status(dsn):
    with tx(dsn) as cur:return status(cur)

def register(dsn,raw):
    m=manifest(raw)
    with tx(dsn,True) as cur:
        for sql in DDL:cur.execute(sql)
        cur.execute(f'INSERT INTO {NS}.manifest(id,body) VALUES (%s,%s) ON CONFLICT DO NOTHING',(MANIFEST_SHA,raw))
        stored,_=load(cur)
        budget(cur,stored,metadata(cur,stored))
        return status(cur)

def put_coverage(dsn,raw):
    require(0<len(raw)<=MAX_COVERAGE,'COVERAGE_SIZE')
    with tx(dsn,True) as cur:
        m,cov=load(cur)
        require(sha(raw)==m['coverage_sha256'],'COVERAGE_HASH')
        budget(cur,m,metadata(cur,m))
        if cov is None:cur.execute(f'UPDATE {NS}.manifest SET coverage=%s WHERE id=%s AND coverage IS NULL',(raw,MANIFEST_SHA))
        return status(cur)

def put(dsn,name,blob):
    require(0<len(blob)<=MAX_CHUNK,'CHUNK_SIZE')
    with tx(dsn,True) as cur:
        m,_=load(cur)
        x=next((x for x in m['chunks'] if x['file']==name),None)
        require(x is not None,'UNKNOWN_CHUNK')
        chunk(blob,x)
        got=metadata(cur,m)
        budget(cur,m,got)
        cur.execute(f'INSERT INTO {NS}.chunk(id,name,body,rows,raw_sha) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (MANIFEST_SHA,name,blob,x['rows'],x['raw_sha256']))
        inserted=cur.rowcount==1
        cur.execute(f'SELECT body FROM {NS}.chunk WHERE id=%s AND name=%s',(MANIFEST_SHA,name))
        stored=bytes(cur.fetchone()[0]);chunk(stored,x)
        return {'name':name,'inserted':inserted,'actual_bytes_verified':True,'rows':x['rows'],'orders_enabled':False}

def verify_member(dsn,name):
    with tx(dsn,True) as cur:
        m,_=load(cur)
        require(name in m['members'],'UNKNOWN_MEMBER')
        xs=sorted((x for x in m['chunks'] if x['source_member']==name),key=lambda x:x['month'])
        h=hashlib.sha256();rows=0
        for i,x in enumerate(xs):
            cur.execute(f'SELECT body FROM {NS}.chunk WHERE id=%s AND name=%s',(MANIFEST_SHA,x['file']))
            row=cur.fetchone()
            require(row is not None,'MEMBER_INCOMPLETE')
            raw=chunk(bytes(row[0]),x)
            h.update(raw if i==0 else raw.split(b'\n',1)[1])
            rows+=x['rows']
        ref=m['members'][name]
        require(h.hexdigest()==ref['sha256'] and rows==ref['rows'],'MEMBER_RECONSTRUCTION_HASH')
        cur.execute(f'INSERT INTO {NS}.verified_member(id,name,rows,sha) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (MANIFEST_SHA,name,rows,h.hexdigest()))
        return {'name':name,'rows':rows,'actual_reconstructed_hash_match':True,'orders_enabled':False}
