from datetime import date, datetime, timedelta, timezone
import copy
import pytest
import nova_v577_split_coverage as m
import nova_v574_data_audit as old

D=[date(2026,7,15),date(2026,7,16),date(2026,7,17),date(2026,7,20)]
T=datetime(2026,7,21,0,tzinfo=timezone.utc)

class Fake:
    def __init__(self,fail_code=None,missing_turnover=False):
        self.calls=[];self.fail_code=fail_code;self.missing_turnover=missing_turnover
    def get(self,path,tr,p):
        code=p['FID_INPUT_ISCD'];self.calls.append(code)
        if code==self.fail_code:raise RuntimeError('private error')
        end=datetime.strptime(p['FID_INPUT_DATE_2'],'%Y%m%d').date()
        idx=path==old.INDEX_PATH
        return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),**(
            {'bstp_nmix_prpr':'2000'} if idx else {'stck_oprc':'100','stck_hgpr':'110',
            'stck_lwpr':'90','stck_clpr':'105','acml_vol':'12',
            'acml_tr_pbmn':'' if self.missing_turnover else '1200'})}
            for d in reversed(D) if d<=end and d!=date(2026,7,17)][:2]}

def plan():return m.new_plan(T,calendar=lambda a,b:D)
def sample(t=T,**kw):
    return {'verified':True,'at':t.isoformat(),'foreground_active':0,
        'foreground_waiting':0,'physical_headroom':10,'recent_provider_errors':0,**kw}
def run(f,cp=None,t=T,telemetry=None):
    return m.run_group(f,plan() if cp is None else cp,
        telemetry=telemetry or (lambda:sample(t)),now=lambda:t,pause=lambda _:None)

def test_groups_exact_partition_and_limits():
    assert list(map(len,m.GROUPS))==[2,5,5,5,5]
    assert len(set(m.SYMBOLS))==22
    assert m.PAGE_LIMITS==(40,60,60,60,60)

def test_plan_excludes_today_and_confirmed_closure():
    cp=plan();p=cp['plan']
    assert p['end']=='2026-07-20' and p['expected_dates']==['2026-07-15','2026-07-16','2026-07-20']
    assert p['calendar']['corrections_applied']==1
    assert m.validate_checkpoint(cp)[2]==tuple(date.fromisoformat(d) for d in p['expected_dates'])

def test_one_group_only_and_cooldown():
    f=Fake();p=plan();r=run(f,p)
    assert r['status']=='GROUP_READY' and r['completed_streams']==2 and r['logical_calls']==4
    assert set(f.calls)==set(old.INDEX_CODES.values())
    assert p['completed']==[]
    b=Fake();s=run(b,r['checkpoint'],T+timedelta(seconds=299))
    assert s['reason']=='COOLDOWN' and not b.calls

def test_resume_all_groups_without_repeat():
    f=Fake();cp=plan()
    for i in range(5):
        r=run(f,cp,T+timedelta(seconds=300*i));cp=r['checkpoint']
        assert r['group']==i+1
    assert r['status']=='READY' and r['completed_streams']==22 and r['observed_rows']==66
    assert len(f.calls)==44 and len(set(f.calls))==22
    assert not r['persistent_storage'] and not r['training_performed']
    count=len(f.calls)
    assert run(f,cp,T+timedelta(seconds=1800))['status']=='READY' and len(f.calls)==count

def test_partial_group_resumes_only_unfinished():
    f=Fake(fail_code='1001');r=run(f)
    assert r['status']=='PAUSED' and r['reason']=='PROVIDER_REQUEST_FAILED'
    assert r['completed_streams']==1 and 'private error' not in str(r)
    g=Fake();s=run(g,r['checkpoint'],T+timedelta(seconds=300))
    assert s['status']=='GROUP_READY' and set(g.calls)=={'1001'}

def test_missing_turnover_not_marked_complete():
    r=run(Fake());f=Fake(missing_turnover=True)
    s=run(f,r['checkpoint'],T+timedelta(seconds=300))
    assert s['status']=='PAUSED' and s['reason']=='QUALITY_GATE'
    assert s['completed_streams']==2

def test_checkpoint_rejects_tampering():
    cp=plan();cp['plan']['end']='2026-07-21'
    with pytest.raises(ValueError):run(Fake(),cp)
    r=run(Fake());cp=copy.deepcopy(r['checkpoint']);cp['completed'][0]['symbol']='000000.KS'
    with pytest.raises(ValueError):run(Fake(),cp,T+timedelta(seconds=300))

@pytest.mark.parametrize('kw,reason',[
    ({'verified':False},'TELEMETRY_UNVERIFIED'),
    ({'at':(T-timedelta(seconds=6)).isoformat()},'TELEMETRY_STALE'),
    ({'foreground_active':1},'FOREGROUND_BUSY'),
    ({'foreground_waiting':1},'FOREGROUND_BUSY'),
    ({'physical_headroom':2},'QUOTA_HEADROOM_LOW'),
    ({'recent_provider_errors':1},'PROVIDER_UNHEALTHY'),
    ({'physical_headroom':True},'TELEMETRY_INVALID')])
def test_fail_closed_before_any_provider_call(kw,reason):
    f=Fake();r=run(f,telemetry=lambda:sample(**kw))
    assert r['reason']==reason and r['logical_calls']==0 and not f.calls

def test_guard_rechecked_between_pages():
    f=Fake()
    def telemetry():return sample(foreground_waiting=int(bool(f.calls)))
    r=run(f,telemetry=telemetry)
    assert r['reason']=='FOREGROUND_BUSY' and r['logical_calls']==1
    assert len(f.calls)==1 and r['completed_streams']==0

def test_telemetry_exception_sanitized():
    f=Fake()
    def fail():raise RuntimeError('private token')
    r=run(f,telemetry=fail)
    assert r['reason']=='TELEMETRY_UNAVAILABLE' and not f.calls
    assert 'private token' not in str(r)
