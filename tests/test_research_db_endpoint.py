import pytest
from research_db_endpoint import endpoint_kind
from research_history_run_control import exclusive_run
from research_history_status import probe,END

DIRECT='postgresql://fixture:fake@ep-example.us-east-2.aws.neon.tech/db?sslmode=require'
POOLED=DIRECT.replace('ep-example.','ep-example-pooler.')

def test_neon_endpoint_classification():
    assert endpoint_kind(DIRECT)=='NEON_DIRECT_ENDPOINT_HINT'
    assert endpoint_kind(POOLED)=='NEON_TRANSACTION_POOLER'

def test_ambiguous_or_invalid_endpoint_fails_closed():
    for dsn in ('',None,'postgresql://fixture:fake@localhost/db',
                DIRECT.replace('neon.tech','neon.tech.example.com'),
                DIRECT+'&hostaddr=127.0.0.1',DIRECT+'&service=fixture',
                DIRECT+'&host=localhost'):
        assert endpoint_kind(dsn)=='UNKNOWN'

def test_known_pooler_rejected_even_with_confirmed_flag():
    def forbidden(*a,**kw):pytest.fail('unexpected connection')
    with pytest.raises(ValueError,match='TRANSACTION_POOLER_UNSUPPORTED'):
        with exclusive_run(POOLED,session_mode_confirmed=True,connect=forbidden):pass

def test_direct_hint_does_not_authorize_execution():
    r=probe(DIRECT,calendar=lambda a,b:(END,),reader=lambda *a:{'status':'NOT_INITIALIZED','stored_rows':0})
    assert r['db_endpoint_kind']=='NEON_DIRECT_ENDPOINT_HINT'
    assert r['session_mode_confirmed'] is False and r['execution_authorized'] is False
    assert 'ep-example' not in str(r) and 'fixture:fake' not in str(r)
