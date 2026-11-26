from datetime import date
from research_history_status import probe,START,END
from nova_v576_expanded_coverage import SYMBOLS

def test_fixed_cohort_and_allowlist():
    def calendar(a,b):
        assert (a,b)==(START,END)
        return (date(2026,6,2),date(2026,6,3),date(2026,11,25))
    def reader(dsn,days):
        assert days==(date(2026,6,2),END)
        return {'status':'PARTIAL','stored_rows':1,'complete_streams':0,'secret':'never log',
                'execution_authorized':True,'training_ready':True}
    r=probe('postgresql://fake',calendar=calendar,reader=reader)
    assert r['expected_rows']==2*len(SYMBOLS) and r['calendar_corrections']==1
    assert r['stored_rows']==1 and 'secret' not in r
    assert not r['execution_authorized'] and not r['training_ready']

def test_missing_dsn_skips_everything():
    def forbidden(*a):raise AssertionError('unexpected call')
    assert probe('',calendar=forbidden,reader=forbidden)['reason']=='POSTGRES_DSN_REQUIRED'

def test_calendar_failure_closed():
    assert probe('postgresql://fake',calendar=lambda a,b:[])['reason']=='CALENDAR_UNAVAILABLE'

def test_reader_failure_is_sanitized():
    def fail(*a):raise RuntimeError('secret')
    r=probe('postgresql://fake',calendar=lambda a,b:(END,),reader=fail)
    assert r['reason']=='STATUS_READ_FAILED' and 'secret' not in str(r)
