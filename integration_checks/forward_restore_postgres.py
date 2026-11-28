"""Run against disposable PostgreSQL only; no actual market data in GitHub."""
import gzip
import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import psycopg
import research_forward_seed as seed
import research_forward_restore as restore

class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=Path(self.tmp.name)/'fixture.sqlite'
        with sqlite3.connect(p) as c:
            c.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
             'CREATE TABLE bars(day TEXT,market TEXT,code TEXT,PRIMARY KEY(day,code));'
             'CREATE TABLE sessions(day TEXT PRIMARY KEY,mode TEXT,observed_at TEXT,digest TEXT,market_counts TEXT,index_close TEXT,candidates TEXT,equity REAL,cash REAL,positions TEXT,events TEXT);')
            c.executemany('INSERT INTO metadata VALUES (?,?)',{'source_sha256':restore.SOURCE,'freeze':restore.FREEZE,'strategy':seed.KEY,'orders_enabled':'false'}.items())
            c.executemany('INSERT INTO bars VALUES (?,?,?)',[(seed.SEED_DAY,'KOSPI','A'),(seed.SEED_DAY,'KOSDAQ','B')])
            c.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)',(seed.SEED_DAY,'SEED',restore.FREEZE,restore.SOURCE,'{"KOSPI":1,"KOSDAQ":1}','{}','[]',restore.CASH,restore.CASH,'{}','[]'))
        self.raw=p.read_bytes()
        self.blob=gzip.compress(self.raw)
        self.patches=[patch.multiple(seed,SEED_SIZE=len(self.raw),SEED_SHA=hashlib.sha256(self.raw).hexdigest(),SEED_BARS=2),
                      patch.multiple(restore,FIRST=seed.SEED_DAY,SESSIONS=1,MARKET_ROWS={'KOSPI':1,'KOSDAQ':1})]
        for p in self.patches:p.start()
        self.dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
        self.assertEqual(self.dsn,'postgresql://nova_ci:local_ci_only@127.0.0.1:5432/nova_ci')
        with psycopg.connect(self.dsn) as db:db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')
    def tearDown(self):
        with psycopg.connect(self.dsn) as db:db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_verified_actual_database(self):
        seed.initialize(self.dsn,self.blob)
        report=restore.verify(self.dsn)
        self.assertEqual((report['bars'],report['sessions'],report['response_claims']),(2,1,0))
        self.assertTrue(report['actual_bytes_hash_match'])
        self.assertFalse(report['orders_enabled'])
    def test_metadata_hash_not_enough(self):
        seed.initialize(self.dsn,self.blob)
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {seed.NS}.checkpoint SET state=%s WHERE id=%s',(gzip.compress(b'x'*len(self.raw)),seed.KEY))
        self.assertTrue(seed.status(self.dsn)['original_seed_hash_match'])
        with self.assertRaisesRegex(seed.SeedError,'ACTUAL_BYTES_HASH_MISMATCH'):
            restore.verify(self.dsn)
    def test_wrong_count(self):
        with patch.object(seed,'SEED_BARS',3):
            with self.assertRaisesRegex(seed.SeedError,'BAR_COUNT_OR_MARKET'):
                restore.verify_blob(self.blob)
    def test_uninitialized(self):
        with self.assertRaisesRegex(seed.SeedError,'UNINITIALIZED'):restore.verify(self.dsn)
    def test_busy_or_nonseed(self):
        seed.initialize(self.dsn,self.blob)
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {seed.NS}.checkpoint SET owner=%s WHERE id=%s',('test',seed.KEY))
        with self.assertRaisesRegex(seed.SeedError,'NOT_IDLE_OR_SEED_REVISION'):restore.verify(self.dsn)
        with psycopg.connect(self.dsn) as db:
            db.execute(f'UPDATE {seed.NS}.checkpoint SET owner=NULL,revision=1 WHERE id=%s',(seed.KEY,))
        with self.assertRaisesRegex(seed.SeedError,'NOT_IDLE_OR_SEED_REVISION'):restore.verify(self.dsn)
    def test_unexpected_claim(self):
        seed.initialize(self.dsn,self.blob)
        with psycopg.connect(self.dsn) as db:
            db.execute(f'INSERT INTO {seed.NS}.response(id,day,service) VALUES (%s,%s,%s)',(seed.KEY,'20261126','stk_bydd_trd'))
        with self.assertRaisesRegex(seed.SeedError,'UNEXPECTED_RESPONSE_CLAIMS'):restore.verify(self.dsn)

if __name__=='__main__':unittest.main(verbosity=2)
