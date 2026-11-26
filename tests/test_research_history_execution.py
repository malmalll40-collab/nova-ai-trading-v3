from datetime import datetime,date
from types import SimpleNamespace
import pytest
from nova_v574_data_audit import KST,collect,Budget
from nova_v579_admission import ResearchBlocked
from research_history_execution import execute,PacedClient,INTERVAL

NOW=datetime(2026,11,26,22,tzinfo=KST)
SCHEDULE={'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'}

def test_missing_coordinator_blocks_before_runner():
    def forbidden(*a,**kw):pytest.fail('unexpected call')
    r=execute('fake',kis=None,telemetry=None,coordinator=None,session_mode_confirmed=True,
              now=forbidden,calendar=forbidden,runner=forbidden)
    assert r['reason']=='ACCOUNT_QUOTA_UNVERIFIED' and r['logical_calls']==0

def test_injected_coordinator_but_unverified_db_blocks():
    coordinator=SimpleNamespace(available=lambda:True,reserve=lambda:None)
    r=execute('fake',kis=None,telemetry=None,coordinator=coordinator,session_mode_confirmed=False,
              now=lambda:NOW,calendar=lambda t:SCHEDULE)
    assert r['reason']=='SESSION_MODE_UNVERIFIED'

def test_pacing_and_deadline_no_extra_request():
    t=[0.];calls=[]
    client=SimpleNamespace(get=lambda *a:calls.append(t[0]))
    paced=PacedClient(client,lambda:True,clock=lambda:t[0],pause=lambda n:t.__setitem__(0,t[0]+n),deadline=10)
    paced.get('a','b',{})
    paced.get('a','b',{})
    with pytest.raises(ResearchBlocked,match='PASS_DEADLINE'):
        paced.get('a','b',{})
    assert calls==[0.,INTERVAL]

def test_admission_revoked_during_wait():
    t=[0.];calls=[]
    client=SimpleNamespace(get=lambda *a:calls.append(1))
    paced=PacedClient(client,lambda:t[0]<2,clock=lambda:t[0],pause=lambda n:t.__setitem__(0,t[0]+n),deadline=20)
    paced.get('a','b',{})
    with pytest.raises(ResearchBlocked,match='RUN_ADMISSION_REVOKED'):
        paced.get('a','b',{})
    assert calls==[1]

def test_allowlisted_stop_preserves_validated_pages():
    a,b=date(2026,11,24),date(2026,11,25)
    class Fake:
        def __init__(self):self.calls=0
        def get(self,*args):
            self.calls+=1
            if self.calls==2:raise ResearchBlocked('FOREGROUND_BUSY')
            return {'output2':[{'stck_bsop_date':'20261125','bstp_nmix_prpr':'100'}]}
    stats,rows=collect(Fake(),'^KS11',a,b,(a,b),Budget(),pause=lambda n:None)
    assert stats['reason']=='FOREGROUND_BUSY' and set(rows)=={b}

def test_untrusted_error_sanitized():
    class Fake:
        def get(self,*args):raise ResearchBlocked('private string')
    d=date(2026,11,25)
    stats,rows=collect(Fake(),'^KS11',d,d,(d,),Budget(),pause=lambda n:None)
    assert stats['reason']=='PROVIDER_REQUEST_FAILED' and not rows
