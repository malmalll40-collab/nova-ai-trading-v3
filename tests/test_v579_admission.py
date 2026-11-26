from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from nova_v578_kis_telemetry import LocalTelemetry
from nova_v579_admission import (QuotaEvidence, ResearchBlocked, ResearchHistoryClient,
    STOCK_PATH, INDEX_PATH, valid_request)

NOW=datetime(2026,11,26,0,0,tzinfo=timezone.utc)
PARAMS={'FID_COND_MRKT_DIV_CODE':'J','FID_ORG_ADJ_PRC':'0',
    'FID_INPUT_ISCD':'005930','FID_INPUT_DATE_1':'20231126',
    'FID_INPUT_DATE_2':'20261125','FID_PERIOD_DIV_CODE':'D'}

def proof(**kw):
    return QuotaEvidence(**{'observed_at':NOW,'physical_headroom':4,
        'covers_all_consumers':True,'reserved_for_research':True,**kw})

class Transport:
    def __init__(self,obs,status=200):
        self.obs=obs;self.status=status;self.calls=0
    def get(self,*args,**kw):
        self.calls+=1
        assert kw['timeout']==3.0
        assert self.obs.snapshot()['research_active']==1
        return SimpleNamespace(status_code=self.status,json=lambda:{'rt_cd':'0'})

def client(obs,status=200,source=None):
    transport=Transport(obs,status)
    k=SimpleNamespace(session=transport,BASE='https://unused.invalid',token='private',
        key='private',secret='private')
    return ResearchHistoryClient(k,obs,source),transport

def test_default_no_proof_never_calls_network():
    o=LocalTelemetry(wall=lambda:NOW)
    c,t=client(o)
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_UNVERIFIED'):c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert t.calls==0 and o.snapshot()['physical_attempts_total']==0

@pytest.mark.parametrize('evidence,reason',[
    (proof(covers_all_consumers=False),'ACCOUNT_QUOTA_UNVERIFIED'),
    (proof(reserved_for_research=False),'ACCOUNT_QUOTA_UNVERIFIED'),
    (proof(observed_at=NOW-timedelta(seconds=6)),'ACCOUNT_QUOTA_STALE'),
    (proof(observed_at=NOW+timedelta(seconds=1)),'ACCOUNT_QUOTA_STALE'),
    (proof(physical_headroom=2),'QUOTA_HEADROOM_LOW')])
def test_invalid_evidence_blocks(evidence,reason):
    o=LocalTelemetry(wall=lambda:NOW)
    c,t=client(o,source=lambda:evidence)
    with pytest.raises(ResearchBlocked,match=reason):c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert t.calls==0

def test_foreground_priority_and_no_foreground_wait():
    o=LocalTelemetry(wall=lambda:NOW)
    with o.foreground():
        with pytest.raises(ResearchBlocked,match='FOREGROUND_BUSY'):
            with o.research_attempt(proof()):pytest.fail('should not enter')
    with o.research_attempt(proof()):
        with o.foreground():assert o.snapshot()['foreground_active']==1
        with pytest.raises(ResearchBlocked,match='PHYSICAL_REQUEST_BUSY'):
            with o.research_attempt(proof()):pytest.fail('should not enter')
    assert o.snapshot()['research_active']==0

def test_pacing_and_rolling_limit():
    t=[100.]
    o=LocalTelemetry(clock=lambda:t[0],wall=lambda:NOW)
    with o.research_attempt(proof()):pass
    with pytest.raises(ResearchBlocked,match='RESEARCH_PACING'):
        with o.research_attempt(proof()):pass
    for _ in range(11):
        t[0]+=2
        with o.research_attempt(proof()):pass
    t[0]+=2
    with pytest.raises(ResearchBlocked,match='RESEARCH_MINUTE_LIMIT'):
        with o.research_attempt(proof()):pass
    t[0]+=61
    with o.research_attempt(proof()):pass
    assert o.snapshot()['research_attempts_total']==13

def test_single_success_and_no_retry_on_error():
    o=LocalTelemetry(wall=lambda:NOW)
    c,t=client(o,source=proof)
    assert c.get(STOCK_PATH,'FHKST03010100',PARAMS)=={'rt_cd':'0'}
    assert t.calls==1
    s=o.snapshot()
    assert s['physical_attempts_total']==s['research_attempts_total']==1
    assert s['research_active']==s['physical_active']==0
    assert not s['verified'] and not s['research_enabled']
    o=LocalTelemetry(wall=lambda:NOW)
    c,t=client(o,status=429,source=proof)
    with pytest.raises(ResearchBlocked,match='RESEARCH_HTTP_FAILURE'):c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert t.calls==1 and o.snapshot()['recent_provider_errors']==1
    with pytest.raises(ResearchBlocked,match='PROVIDER_UNHEALTHY'):c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert t.calls==1

def test_endpoint_allowlist():
    assert valid_request(STOCK_PATH,'FHKST03010100',PARAMS)
    assert not valid_request('/uapi/order','FHKST03010100',PARAMS)
    assert not valid_request(STOCK_PATH,'wrong',PARAMS)
    assert not valid_request(STOCK_PATH,'FHKST03010100',{**PARAMS,'FID_PERIOD_DIV_CODE':'M'})
    p={k:v for k,v in PARAMS.items() if k!='FID_ORG_ADJ_PRC'}
    p.update(FID_INPUT_ISCD='0001',FID_COND_MRKT_DIV_CODE='U')
    assert valid_request(INDEX_PATH,'FHKUP03500100',p)
    assert not valid_request(INDEX_PATH,'FHKUP03500100',{**p,'extra':'1'})

def test_proof_exception_sanitized():
    o=LocalTelemetry(wall=lambda:NOW)
    def bad():raise RuntimeError('private')
    c,t=client(o,source=bad)
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_UNVERIFIED') as exc:
        c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert 'private' not in str(exc.value) and t.calls==0

def test_transport_timeout_is_distinct_sanitized_and_not_retried():
    import requests
    o=LocalTelemetry(wall=lambda:NOW)
    c,t=client(o,source=proof)
    def fail(*args,**kw):
        t.calls+=1
        raise requests.exceptions.ReadTimeout('private URL')
    t.get=fail
    with pytest.raises(ResearchBlocked,match='^RESEARCH_TRANSPORT_TIMEOUT$') as exc:
        c.get(STOCK_PATH,'FHKST03010100',PARAMS)
    assert t.calls==1 and 'private' not in str(exc.value)
    assert o.snapshot()['recent_provider_errors']==1
