from research_db_endpoint import direct_candidate
from research_db_session_probe import probe,verify_session
import pytest

POOL='postgresql://fixture:p%40ss@ep-example-pooler.us-east-2.aws.neon.tech/db?sslmode=require&channel_binding=require'
DIRECT=POOL.replace('ep-example-pooler.','ep-example.')

def test_direct_candidate_preserves_encoded_credentials_and_tls():
    assert direct_candidate(POOL)==DIRECT
    assert direct_candidate(DIRECT)==DIRECT

def test_direct_candidate_refuses_ambiguous_routing_or_tls():
    for dsn in (None,'',POOL.replace('sslmode=require','sslmode=disable'),
                POOL+'&host=localhost',POOL+'&hostaddr=127.0.0.1',
                POOL+'&sslmode=require',POOL.replace('.neon.tech/','.neon.tech.example.com/'),
                POOL.replace('.neon.tech/','.neon.tech:5433/'),POOL+'#fragment',
                POOL.replace('fixture:p%40ss@','')):
        assert direct_candidate(dsn) is None

def test_probe_never_connects_with_invalid_candidate():
    def forbidden(*args):pytest.fail('unexpected connection')
    r=probe('',verifier=forbidden)
    assert not r['session_probe_verified'] and not r['execution_authorized']

def test_probe_allowlist_and_no_execution_authorization():
    def fake(dsn):
        assert dsn==DIRECT
        return {'direct_connection_status':'READ_OK','session_probe_verified':True,
                'session_mode_confirmed':True,'execution_authorized':True,'secret':dsn}
    r=probe(POOL,verifier=fake)
    assert r['session_probe_verified'] and r['proof_scope']=='THIS_CONNECTION_ONLY'
    assert not r['session_mode_confirmed'] and not r['execution_authorized']
    assert 'secret' not in r and 'fixture' not in str(r)

def test_connection_failure_is_sanitized():
    def fail(*a,**kw):raise RuntimeError(DIRECT)
    r=verify_session(DIRECT,connect=fail)
    assert r['direct_connection_status']=='UNAVAILABLE' and not r['session_probe_verified']
    assert 'fixture' not in str(r) and 'ep-example' not in str(r)

def test_failed_verifier_remains_fail_closed():
    r=probe(POOL,verifier=lambda dsn:{'direct_connection_status':'READ_OK','session_probe_verified':False})
    assert r['direct_connection_status']=='UNAVAILABLE' and not r['execution_authorized']
