"""CI-only isolated PostgreSQL and HTTP tests. Never connect to production."""
import gzip
import hashlib
import os
import unittest
from unittest.mock import patch
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
import research_forward_seed as seed
import research_forward_seed_api as api

RAW = b'CI fixture only; not market data'
BLOB = gzip.compress(RAW)

class Contract(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'NOVA_ACCESS_TOKEN':'fixture-token',
                                          'NOVA_FORWARD_SEED_ENABLED':'0'}, clear=False)
        self.env.start()
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.headers = {'Authorization':'Bearer fixture-token','Content-Type':'application/gzip'}
    def tearDown(self):
        self.env.stop()
    def test_bad_hash(self):
        with self.assertRaisesRegex(seed.SeedError,'SEED_HASH_MISMATCH'):
            seed.verify_upload(BLOB)
    def test_bad_gzip(self):
        with self.assertRaisesRegex(seed.SeedError,'GZIP_INVALID'):
            seed.verify_upload(b'invalid')
    def test_authentication(self):
        self.assertEqual(self.client.get('/api/research/forward-seed/status').status_code,401)
        self.assertEqual(self.client.post('/api/research/forward-seed',content=BLOB).status_code,401)
        with patch.dict(os.environ,{'NOVA_ACCESS_TOKEN':''}):
            self.assertEqual(self.client.get('/api/research/forward-seed/status').status_code,503)
    def test_disabled(self):
        with patch.object(seed,'initialize') as fn:
            r=self.client.post('/api/research/forward-seed',headers=self.headers,content=BLOB)
            self.assertEqual(r.status_code,503); fn.assert_not_called()
    def test_upload_guard_and_success(self):
        with patch.dict(os.environ,{'NOVA_FORWARD_SEED_ENABLED':'1'}), patch.object(seed,'initialize',return_value={'inserted':True}) as fn:
            r=self.client.post('/api/research/forward-seed',headers=self.headers,content=BLOB)
            self.assertEqual(r.status_code,200);self.assertTrue(r.json()['inserted'])
            self.assertEqual(r.headers['cache-control'],'no-store')
            fn.assert_called_once()
            with patch.object(seed,'MAX_UPLOAD',5):
                r=self.client.post('/api/research/forward-seed',headers=self.headers,content=BLOB)
                self.assertEqual(r.status_code,413)
    def test_no_error_secret(self):
        with patch.object(seed,'status',side_effect=RuntimeError('secret dsn')):
            r=self.client.get('/api/research/forward-seed/status',headers=self.headers)
            self.assertEqual(r.status_code,503);self.assertNotIn('secret',r.text)
    def test_real_database(self):
        # Guard destructive fixture cleanup by requiring exact disposable CI DSN.
        dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
        self.assertEqual(dsn,'postgresql://nova_ci:local_ci_only@127.0.0.1:5432/nova_ci')
        with psycopg.connect(dsn) as db:
            db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')
        try:
            with patch.multiple(seed,SEED_SIZE=len(RAW),SEED_SHA=hashlib.sha256(RAW).hexdigest()):
                self.assertFalse(seed.status(dsn)['seed_initialized'])
                with psycopg.connect(dsn) as db:
                    db.execute('SELECT pg_advisory_xact_lock(%s)',(601576110,))
                    with self.assertRaisesRegex(seed.SeedError,'SEED_BUSY'):
                        seed.initialize(dsn,BLOB)
                with patch.object(seed,'MAX_DB_BEFORE_SEED',1):
                    with self.assertRaisesRegex(seed.SeedError,'LOCAL_STORAGE_GUARD'):
                        seed.initialize(dsn,BLOB)
                self.assertFalse(seed.status(dsn)['seed_initialized'])
                first=seed.initialize(dsn,BLOB)
                self.assertTrue(first['inserted'])
                self.assertEqual(first['revision'],0)
                self.assertTrue(seed.status(dsn)['original_seed_hash_match'])
                self.assertFalse(seed.initialize(dsn,BLOB)['inserted'])
                with psycopg.connect(dsn) as db:
                    state=db.execute(f'SELECT state FROM {seed.NS}.checkpoint WHERE id=%s',(seed.KEY,)).fetchone()[0]
                    self.assertEqual(gzip.decompress(bytes(state)),RAW)
                    db.execute(f'UPDATE {seed.NS}.checkpoint SET revision=1,last_day=%s WHERE id=%s',('20261126',seed.KEY))
                self.assertFalse(seed.initialize(dsn,BLOB)['inserted'])
                self.assertEqual(seed.status(dsn)['revision'],1)
        finally:
            with psycopg.connect(dsn) as db:
                db.execute(f'DROP SCHEMA IF EXISTS {seed.NS} CASCADE')

if __name__=='__main__':
    unittest.main(verbosity=2)
