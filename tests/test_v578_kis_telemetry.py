from datetime import datetime, timezone
import pytest
import requests
import data_sources as ds
from nova_v578_kis_telemetry import LocalTelemetry
from nova_v577_split_coverage import permit

class Reply:
    def __init__(self,status=200,data=None):
        self.status_code=status;self.data={'rt_cd':'0'} if data is None else data
    def json(self):
        if isinstance(self.data,Exception):raise self.data
        return self.data
    def raise_for_status(self):
        if self.status_code>=400:raise requests.HTTPError('private')

class Session:
    def __init__(self,replies,obs):self.replies=iter(replies);self.obs=obs
    def get(self,*a,**k):
        s=self.obs.snapshot()
        assert s['foreground_active']>=1 and s['physical_active']==1
        r=next(self.replies)
        if isinstance(r,Exception):raise r
        return r
    post=get

@pytest.fixture
def observer(monkeypatch):
    o=LocalTelemetry()
    monkeypatch.setattr(ds,'KIS_LOAD',o)
    monkeypatch.setattr(ds.time,'sleep',lambda _:None)
    return o

def client(observer,replies):
    k=ds.KIS.__new__(ds.KIS)
    k.key='private-key';k.secret='private-secret';k.token='private-token'
    k.session=Session(replies,observer)
    return k

def test_success_is_counted_without_credentials(observer):
    k=client(observer,[Reply()])
    assert k.get('/quote','id',{})=={'rt_cd':'0'}
    s=observer.snapshot()
    assert s['physical_attempts_total']==1 and s['retries_total']==0
    assert s['foreground_active']==s['physical_active']==0
    assert s['recent_provider_errors']==0 and 'private' not in str(s)
    assert not s['verified'] and not s['research_enabled']

@pytest.mark.parametrize('first',[Reply(429),Reply(503),requests.Timeout('private')])
def test_retry_counts_physical_attempts(observer,first):
    k=client(observer,[first,Reply()])
    k.get('/quote','id',{})
    s=observer.snapshot()
    assert s['physical_attempts_total']==2 and s['retries_total']==1
    assert s['recent_provider_errors']==1 and s['foreground_active']==0

@pytest.mark.parametrize('reply',[Reply(403),Reply(data={'rt_cd':'1'}),Reply(data=[]),Reply(data=ValueError())])
def test_nonretry_failure_count(observer,reply):
    k=client(observer,[reply])
    with pytest.raises(RuntimeError):k.get('/quote','id',{})
    s=observer.snapshot()
    assert s['physical_attempts_total']==1 and s['recent_provider_errors']==1
    assert s['foreground_active']==s['physical_active']==0

def test_token_post_count(observer):
    k=client(observer,[Reply(data={'access_token':'private','expires_in':3600})])
    with observer.foreground():assert k._request_token()==('private',3600)
    s=observer.snapshot()
    assert s['token_attempts_total']==1 and s['physical_attempts_total']==1
    assert 'private' not in str(s)

def test_rolling_window_and_fail_closed():
    t=[100.]
    o=LocalTelemetry(clock=lambda:t[0])
    with o.foreground():
        with o.physical():pass
        o.failure()
        assert o.snapshot()['foreground_active']==1
    t[0]+=61
    s=o.snapshot()
    assert s['physical_attempts_60s']==0 and s['recent_provider_errors']==0
    assert s['physical_attempts_total']==1
    assert permit(s,datetime.now(timezone.utc))=='TELEMETRY_UNVERIFIED'

def test_concurrent_foreground_observation():
    o=LocalTelemetry()
    with o.foreground():
        with o.foreground():
            assert o.snapshot()['foreground_active']==2
        assert o.snapshot()['foreground_active']==1
    assert o.snapshot()['foreground_active']==0
