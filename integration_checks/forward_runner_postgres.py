"""Disposable PostgreSQL tests; all KRX responses synthetic; no external calls."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forward_shadow import forward as f, collector as c, runner as r, api
from research_forward_seed import DDL

def fixture(day):
    rows={}
    for svc,(folder,market) in c.SERVICES.items():
        if folder=='idx':
            row={'BAS_DD':day,'IDX_NM':market,'CLSPRC_IDX':'2000'}
        else:
            code='000001' if market=='KOSPI' else '000002'
            row=dict(zip(f.COLS[1:],[day,code,10000,0,10000,10000,10000,100000,1000000000,10000000000,1000000]))
            row['MKT_NM']=market
        rows[svc]=c.canonical({'OutBlock_1':[row]})
    return rows

class Tests(unittest.TestCase):
    def setUp(self):
        self.dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
        self.assertEqual(self.dsn,'postgresql://nova_ci:local_ci_only@127.0.0.1:5432/nova_ci')
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)/'fixture.sqlite'
        days=f.CAL.sessions_window(f.pd.Timestamp(f.dayfmt(f.LAST)),-64).strftime('%Y%m%d').tolist()
        self.assertEqual(len(days),65)
        with sqlite3.connect(path) as db:
            db.executescript(f.SCHEMA)
            for d in days:
                for market,code in [('KOSPI','000001'),('KOSDAQ','000002')]:
                    db.execute('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(d,code,market,10000,0,10000,10000,10000,100000,1000000000,10000000000,1000000))
                db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(d,f.FREEZE,f.SOURCE,'SEED','fixture','{"KOSPI":1,"KOSDAQ":1}','{}','[]',f.CASH,f.CASH,'{}','[]'))
            db.executemany('INSERT INTO metadata VALUES (?,?)',{'source_sha256':f.SOURCE,'freeze':f.FREEZE,'strategy':r.KEY,'orders_enabled':'false'}.items())
        digest,z=r.encode(path.read_bytes())
        self.hashpatch=patch.object(r,'SEED_SHA',digest);self.hashpatch.start()
        with psycopg.connect(self.dsn) as db:
            db.execute(f'DROP SCHEMA IF EXISTS {r.NS} CASCADE')
            for sql in DDL:db.execute(sql)
            db.execute(f'INSERT INTO {r.NS}.checkpoint(id,revision,digest,state,last_day) VALUES (%s,0,%s,%s,%s)',(r.KEY,digest,z,f.LAST))
        self.calls=[]
        self.store=r.Store(self.dsn)
    def tearDown(self):
        self.store.close()
        with psycopg.connect(self.dsn) as db:db.execute(f'DROP SCHEMA IF EXISTS {r.NS} CASCADE')
        self.hashpatch.stop();self.tmp.cleanup()
    def fetch(self,session,svc,day,key):
        self.calls.append((day,svc));return fixture(day)[svc]
    def run_at(self,when,fetch=None):
        return r.run(self.store,'fixture',clock=lambda:datetime.fromisoformat(when),fetch=fetch or self.fetch,pause=lambda _:None)
    def test_frozen(self):
        c.verify_frozen()
        self.assertEqual(f.next_day('20261127'),'20261130')
    def test_bridge_restart_then_prospective(self):
        a=self.run_at('2026-11-28T14:00:00+09:00')
        self.assertEqual((a['day'],a['mode'],a['revision']),('20261126','LATE_OR_BRIDGE',1))
        self.assertEqual(a['report']['candidate_count'],0)
        self.store.close();self.store=r.Store(self.dsn)
        b=self.run_at('2026-11-28T14:00:00+09:00')
        self.assertEqual((b['day'],b['revision']),('20261127',2))
        self.assertEqual(len(self.calls),8)
        self.assertEqual(self.run_at('2026-11-30T18:29:00+09:00')['state'],'NO_SESSION_READY')
        self.assertEqual(len(self.calls),8)
        d=self.run_at('2026-11-30T18:31:00+09:00')
        self.assertEqual((d['mode'],d['revision']),('PROSPECTIVE',3))
        self.assertEqual(d['report']['candidate_count'],2)
        self.assertEqual(d['report']['closed_assumed'],0)
        self.assertEqual(self.run_at('2026-11-30T19:00:00+09:00')['state'],'NO_SESSION_READY')
        self.assertEqual(len(self.calls),12)
    def test_owner_exclusion(self):
        self.store.acquire('first')
        other=r.Store(self.dsn)
        try:
            with self.assertRaisesRegex(ValueError,'BUSY_OR_UNINITIALIZED'):other.acquire('second')
        finally:other.close();self.store.release('first')
    def test_uncertain_blocks_repeat(self):
        def fail(*args):
            self.calls.append(args[1:3]);raise ValueError('SYNTHETIC_NETWORK_FAILURE')
        with self.assertRaisesRegex(ValueError,'SYNTHETIC_NETWORK_FAILURE'):self.run_at('2026-11-28T14:00:00+09:00',fail)
        with self.assertRaisesRegex(ValueError,'UNCERTAIN_CALL'):self.run_at('2026-11-28T14:00:00+09:00')
        self.assertEqual(len(self.calls),1)
    def test_completed_response_reused(self):
        self.store.acquire('first')
        day='20261126';svc=next(iter(c.SERVICES))
        try:
            self.assertIsNone(self.store.begin('first',day,svc))
            self.store.finish('first',day,svc,fixture(day)[svc])
        finally:self.store.release('first')
        self.assertEqual(self.run_at('2026-11-28T14:00:00+09:00')['revision'],1)
        self.assertEqual(len(self.calls),3)
    def test_bad_payload_does_not_commit(self):
        def bad(session,svc,day,key):return fixture('20261125')[svc]
        with self.assertRaisesRegex(ValueError,'DATE_MISMATCH'):self.run_at('2026-11-28T14:00:00+09:00',bad)
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute(f'SELECT revision FROM {r.NS}.checkpoint WHERE id=%s',(r.KEY,)).fetchone(),(0,))
    def test_api_gate(self):
        app=FastAPI();app.include_router(api.router);client=TestClient(app)
        with patch.dict(os.environ,{'NOVA_FORWARD_RUN_TOKEN':'x'*32,'NOVA_FORWARD_CLOUD_ENABLED':'0'}):
            self.assertEqual(client.post('/api/research/forward/run').status_code,401)
            self.assertEqual(client.post('/api/research/forward/run',headers={'X-Nova-Forward-Token':'x'*32}).status_code,503)
        with patch.dict(os.environ,{'NOVA_FORWARD_CLOUD_ENABLED':'1','NOVA_KRX_PRIVATE_COLLECTION':'off','NOVA_FORWARD_SEED_ENABLED':'0','NOVA_FORWARD_VERIFY_ON_START':'0'}):
            self.assertTrue(r.eligible_env())
            with patch.dict(os.environ,{'NOVA_KRX_PRIVATE_COLLECTION':'single-history-v1'}):self.assertFalse(r.eligible_env())

if __name__=='__main__':unittest.main(verbosity=2)
