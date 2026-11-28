"""No production credentials/network. Real signed JWT and disposable DB."""
import os
import time
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch,Mock
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forward_shadow import oidc,scheduler
from research_forward_seed import NS

class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.keys=SimpleNamespace(get_signing_keys=lambda:[SimpleNamespace(key_id='testkey',key=cls.key.public_key())])
    def token(self,**changes):
        now=int(time.time())
        data={**oidc.EXPECTED,'iss':oidc.ISSUER,'aud':oidc.AUDIENCE,'sub':'test','jti':'fixture',
              'event_name':'schedule','exp':now+300,'iat':now,'nbf':now-1,**changes}
        return jwt.encode(data,self.key,algorithm='RS256',headers={'kid':'testkey'})
    def test_valid_signed_identity(self):
        self.assertEqual(len(oidc.authenticate(self.token(),keys=self.keys)),64)
    def test_reject_wrong_repo_event_workflow_and_time(self):
        for changes in ({'repository_id':'0'},{'repository_owner_id':'0'},{'event_name':'pull_request'},
                        {'workflow_ref':'other'},{'aud':'other'},{'ref':'refs/heads/dev'},
                        {'iss':'https://other.invalid'},{'exp':int(time.time())-100},
                        {'runner_environment':'self-hosted'}):
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'IDENTITY_INVALID'):
                oidc.authenticate(self.token(**changes),keys=self.keys)
    def test_bad_signature(self):
        other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        wrong=SimpleNamespace(get_signing_keys=lambda:[SimpleNamespace(key_id='testkey',key=other.public_key())])
        with self.assertRaisesRegex(ValueError,'IDENTITY_INVALID'):
            oidc.authenticate(self.token(),keys=wrong)
    def test_replay_disposable_db(self):
        dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
        self.assertEqual(dsn,'postgresql://nova_ci:local_ci_only@127.0.0.1:5432/nova_ci')
        with psycopg.connect(dsn) as db:db.execute(f'CREATE SCHEMA IF NOT EXISTS {NS}')
        try:
            with patch.dict(os.environ,{'DATABASE_URL':dsn}):
                self.assertTrue(scheduler.claim_identity('fixture'))
                self.assertFalse(scheduler.claim_identity('fixture'))
        finally:
            with psycopg.connect(dsn) as db:db.execute(f'DROP SCHEMA IF EXISTS {NS} CASCADE')
    def test_bridge_fixed_cutoff(self):
        run=Mock(side_effect=[{'state':'INGESTED','day':'20261126'},
                              {'state':'INGESTED','day':'20261127'}])
        emit=Mock()
        with patch.dict(os.environ,{'NOVA_FORWARD_BRIDGE_ON_START':'1'}),patch.object(scheduler.runner,'eligible_env',return_value=True):
            scheduler.bridge(threading.Event(),run=run,admit=lambda:True,wait=lambda _:False,emit=emit)
        self.assertEqual(run.call_count,2)
        self.assertTrue(all(c.kwargs['latest_day']=='20261127' for c in run.call_args_list))
    def test_bridge_disabled(self):
        run=Mock()
        with patch.dict(os.environ,{'NOVA_FORWARD_BRIDGE_ON_START':'0'}):
            scheduler.bridge(threading.Event(),run=run,admit=lambda:True,wait=lambda _:False)
        run.assert_not_called()
    def test_scheduled_gate_and_outside_window(self):
        app=FastAPI();app.include_router(scheduler.router)
        client=TestClient(app);url='/api/research/forward/scheduled'
        with patch.dict(os.environ,{'NOVA_FORWARD_SCHEDULE_ENABLED':'0'}):
            self.assertEqual(client.post(url).status_code,503)
        with patch.dict(os.environ,{'NOVA_FORWARD_SCHEDULE_ENABLED':'1'}),patch.object(scheduler.runner,'eligible_env',return_value=True),patch.object(scheduler.oidc,'authenticate',return_value='fixture'),patch.object(scheduler.f,'now',return_value=datetime.fromisoformat('2026-11-28T19:00:00+09:00')),patch.object(scheduler,'claim_identity') as claim:
            self.assertEqual(client.post(url).status_code,401)
            self.assertEqual(client.post(url,headers={'Authorization':'Bearer fixture'}).json()['state'],'OUTSIDE_WINDOW')
            claim.assert_not_called()
    def test_scheduled_one_invocation_and_replay(self):
        app=FastAPI();app.include_router(scheduler.router)
        client=TestClient(app);url='/api/research/forward/scheduled'
        with patch.dict(os.environ,{'NOVA_FORWARD_SCHEDULE_ENABLED':'1'}),patch.object(scheduler.runner,'eligible_env',return_value=True),patch.object(scheduler.oidc,'authenticate',return_value='fixture'),patch.object(scheduler.f,'now',return_value=datetime.fromisoformat('2026-11-30T19:00:00+09:00')),patch.object(scheduler,'admitted',return_value=True),patch.object(scheduler,'claim_identity',side_effect=[True,False]),patch.object(scheduler.runner,'execute',return_value={'state':'INGESTED','day':'20261130','orders_enabled':False}) as run,patch.object(scheduler,'log'):
            headers={'Authorization':'Bearer fixture'}
            self.assertEqual(client.post(url,headers=headers).status_code,200)
            self.assertEqual(client.post(url,headers=headers).status_code,409)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['latest_day'],'20261130')

if __name__=='__main__':unittest.main(verbosity=2)
