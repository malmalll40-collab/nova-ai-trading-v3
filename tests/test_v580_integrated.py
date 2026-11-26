from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from nova_v577_split_coverage import new_plan
from nova_v578_kis_telemetry import LocalTelemetry
from nova_v579_admission import QuotaEvidence
from nova_v580_integrated import run_slice

BASE=datetime(2026,11,26,0,0,tzinfo=timezone.utc)
DATES=[date(2026,11,1)+timedelta(days=i) for i in range(15)]
class Clock:
    def __init__(self):self.t=100.
    def monotonic(self):return self.t
    def now(self):return BASE+timedelta(seconds=self.t-100)
    def sleep(self,n):self.t+=n
class Transport:
    def __init__(self):self.calls=[]
    def get(self,url,headers,params,timeout):
        self.calls.append(params['FID_INPUT_ISCD'])
        cursor=datetime.strptime(params['FID_INPUT_DATE_2'],'%Y%m%d').date()
        rows=[d for d in DATES if d<=cursor]
        return SimpleNamespace(status_code=200,json=lambda:{'rt_cd':'0','output2':[{
            'stck_bsop_date':max(rows).strftime('%Y%m%d'),'bstp_nmix_prpr':'1000'}] if rows else []})
def fixture():
    c=Clock();o=LocalTelemetry(clock=c.monotonic,wall=c.now);t=Transport()
    k=SimpleNamespace(BASE='https://unused.invalid',token='private',key='private',secret='private',session=t)
    cp=new_plan(c.now(),calendar=lambda a,b:DATES)
    return c,o,t,k,cp
def execute(c,o,k,cp,source=None):
    return run_slice(k,cp,observer=o,proof_source=source,now=c.now,clock=c.monotonic,pause=c.sleep)
def test_missing_coordinator_blocks_without_network():
    c,o,t,k,cp=fixture()
    r=execute(c,o,k,cp)
    assert r['status']=='PAUSED' and not t.calls and not r['live_activation']
def test_fifteen_pages_progress_under_rolling_cap():
    c,o,t,k,cp=fixture()
    r=execute(c,o,k,cp,lambda:QuotaEvidence(c.now(),4,True,True))
    assert r['status']=='SLICE_READY' and r['completed_streams']==1
    assert len(t.calls)==15 and len(set(t.calls))==1
    assert o.snapshot()['research_attempts_total']==15
    assert c.t>=177 and not r['persistent_storage']
def test_resume_cooldown_and_second_index():
    c,o,t,k,cp=fixture()
    proof=lambda:QuotaEvidence(c.now(),4,True,True)
    r=execute(c,o,k,cp,proof)
    s=execute(c,o,k,r['checkpoint'],proof)
    assert s['reason']=='COOLDOWN' and len(t.calls)==15
    c.sleep(300)
    s=execute(c,o,k,r['checkpoint'],proof)
    assert s['status']=='GROUP_READY' and s['completed_streams']==2
    assert len(t.calls)==30 and len(set(t.calls))==2
def test_midstream_foreground_stops_without_retry():
    c,o,t,k,cp=fixture()
    def proof():
        return QuotaEvidence(c.now(),4,True,len(t.calls)<2)
    r=execute(c,o,k,cp,proof)
    assert r['status']=='PAUSED' and r['reason']=='ACCOUNT_QUOTA_UNVERIFIED'
    assert len(t.calls)==2 and r['completed_streams']==0
