"""Disposable PostgreSQL only. Synthetic archive bytes; no KRX calls."""
import gzip,json,os,unittest
from unittest.mock import patch
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
import research_krx_archive as a
import research_krx_archive_api as api
import research_forward_seed as seed

def fixture():
    header=('\ufeff'+a.HEADER+'\r\n').encode()
    rows=[f'KOSPI,{day},000001,시험,KOSPI,,100,0,0,100,100,100,1,100,10000,100\r\n'.encode() for day in ('20240102','20240201')]
    chunks=[]; blobs={}
    for month,line in zip(('202401','202402'),rows):
        raw=header+line; blob=gzip.compress(raw,mtime=0); name='KOSPI_'+month+'.csv.gz';blobs[name]=blob
        day=month+('02' if month=='202401' else '01')
        chunks.append(dict(file=name,market='KOSPI',month=month,source_member='KOSPI_2024.csv',rows=1,sessions=1,
          first_day=day,last_day=day,raw_bytes=len(raw),compressed_bytes=len(blob),raw_sha256=a.sha(raw),compressed_sha256=a.sha(blob)))
    coverage=b'synthetic coverage'
    m={'source_sha256':a.SOURCE,'format':'exact-csv-month-gzip-v1','training_ready':False,
       'members':{'KOSPI_2024.csv':{'rows':2,'sha256':a.sha(header+b''.join(rows))}},
       'chunks':chunks,'coverage_sha256':a.sha(coverage)}
    raw=json.dumps(m).encode()
    return raw,blobs,coverage

class Tests(unittest.TestCase):
    def setUp(self):
        self.dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
        self.assertEqual(self.dsn,'postgresql://nova_ci:local_ci_only@127.0.0.1:5432/nova_ci')
        self.raw,self.blobs,self.cov=fixture()
        self.p=patch.multiple(a,MANIFEST_SHA=a.sha(self.raw),TOTAL=2,NCHUNKS=2,NBYTES=sum(map(len,self.blobs.values())))
        self.p.start()
        with psycopg.connect(self.dsn) as db:
            db.execute(f'DROP SCHEMA IF EXISTS {a.NS} CASCADE')
            db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')
            for sql in seed.DDL:db.execute(sql)
            db.execute(f'INSERT INTO {seed.NS}.checkpoint(id,revision,digest,state,last_day) VALUES (%s,2,%s,%s,%s)',(seed.KEY,'fixture',b'fixture','20261127'))
    def tearDown(self):
        with psycopg.connect(self.dsn) as db:
            db.execute(f'DROP SCHEMA IF EXISTS {a.NS} CASCADE')
            db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')
        self.p.stop()
    def test_resume_and_exact_reconstruction(self):
        self.assertFalse(a.read_status(self.dsn)['manifest_registered'])
        a.register(self.dsn,self.raw)
        a.put_coverage(self.dsn,self.cov)
        name,blob=next(iter(self.blobs.items()))
        self.assertTrue(a.put(self.dsn,name,blob)['inserted'])
        self.assertFalse(a.put(self.dsn,name,blob)['inserted'])
        self.assertEqual(a.read_status(self.dsn)['stored_rows'],1)
        with self.assertRaisesRegex(a.ArchiveError,'MEMBER_INCOMPLETE'):a.verify_member(self.dsn,'KOSPI_2024.csv')
        a.register(self.dsn,self.raw)
        for name,blob in self.blobs.items():a.put(self.dsn,name,blob)
        self.assertFalse(a.read_status(self.dsn)['archive_verified'])
        self.assertTrue(a.verify_member(self.dsn,'KOSPI_2024.csv')['actual_reconstructed_hash_match'])
        s=a.read_status(self.dsn)
        self.assertTrue(s['archive_verified']);self.assertEqual(s['stored_rows'],2)
        self.assertFalse(s['training_ready'])
    def test_bad_manifest_coverage_and_chunk(self):
        with self.assertRaisesRegex(a.ArchiveError,'MANIFEST_HASH'):a.register(self.dsn,self.raw+b' ')
        a.register(self.dsn,self.raw)
        with self.assertRaisesRegex(a.ArchiveError,'COVERAGE_HASH'):a.put_coverage(self.dsn,b'bad')
        with self.assertRaisesRegex(a.ArchiveError,'UNKNOWN_CHUNK'):a.put(self.dsn,'unknown',b'x')
        with self.assertRaisesRegex(a.ArchiveError,'COMPRESSED_HASH'):a.put(self.dsn,next(iter(self.blobs)),b'bad')
        self.assertEqual(a.read_status(self.dsn)['stored_chunks'],0)
    def test_actual_readback_detects_corruption(self):
        a.register(self.dsn,self.raw)
        for name,blob in self.blobs.items():a.put(self.dsn,name,blob)
        name,blob=next(iter(self.blobs.items()))
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {a.NS}.chunk SET body=%s WHERE name=%s',(b'x'*len(blob),name))
        with self.assertRaisesRegex(a.ArchiveError,'COMPRESSED_HASH'):a.verify_member(self.dsn,'KOSPI_2024.csv')
        self.assertFalse(a.read_status(self.dsn)['archive_verified'])
    def test_shadow_owner_and_shared_lock(self):
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {seed.NS}.checkpoint SET owner=%s WHERE id=%s',('busy',seed.KEY))
        with self.assertRaisesRegex(a.ArchiveError,'SHADOW_BUSY_OR_MISSING'):a.register(self.dsn,self.raw)
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {seed.NS}.checkpoint SET owner=NULL')
        with psycopg.connect(self.dsn) as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)',(a.LOCK,))
            with self.assertRaisesRegex(a.ArchiveError,'ARCHIVE_BUSY'):a.register(self.dsn,self.raw)
        self.assertFalse(a.read_status(self.dsn)['manifest_registered'])
    def test_storage_guard_rolls_back(self):
        with patch.object(a,'GUARD',1):
            with self.assertRaisesRegex(a.ArchiveError,'LOCAL_ARCHIVE_STORAGE_GUARD'):a.register(self.dsn,self.raw)
        self.assertFalse(a.read_status(self.dsn)['manifest_registered'])
    def test_api_auth_disabled_and_resume(self):
        app=FastAPI();app.include_router(api.router);client=TestClient(app)
        url='/api/research/history-archive';h={'Authorization':'Bearer fixture','Content-Type':'application/octet-stream'}
        with patch.dict(os.environ,{'DATABASE_URL':self.dsn,'NOVA_ACCESS_TOKEN':'fixture','NOVA_HISTORY_ARCHIVE_UPLOAD':'0'}):
            self.assertEqual(client.get(url+'/status').status_code,401)
            self.assertFalse(client.get(url+'/status',headers=h).json()['upload_enabled'])
            self.assertEqual(client.post(url+'/manifest',headers=h,content=self.raw).status_code,503)
            with patch.dict(os.environ,{'NOVA_HISTORY_ARCHIVE_UPLOAD':'1'}):
                self.assertEqual(client.post(url+'/manifest',headers=h,content=self.raw).status_code,200)
                self.assertEqual(client.post(url+'/coverage',headers=h,content=self.cov).status_code,200)
                name,blob=next(iter(self.blobs.items()))
                self.assertEqual(client.post(url+'/chunk/'+name,headers=h,content=blob).status_code,415)
                self.assertEqual(client.post(url+'/chunk/'+name,headers={**h,'Content-Type':'application/gzip'},content=blob).status_code,200)
                self.assertEqual(client.get(url+'/status',headers=h).json()['stored_chunks'],1)

if __name__=='__main__':unittest.main(verbosity=2)
