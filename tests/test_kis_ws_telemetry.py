import asyncio
from types import SimpleNamespace
import pytest
import kis_ws_readonly as ws
from nova_v578_kis_telemetry import LocalTelemetry

@pytest.fixture
def telemetry(monkeypatch):
    obs=LocalTelemetry()
    monkeypatch.setattr(ws,'KIS_LOAD',obs)
    monkeypatch.setattr(ws,'_APPROVAL',None)
    monkeypatch.setattr(ws,'_APPROVAL_AT',0.)
    monkeypatch.setattr(ws,'enabled',lambda:True)
    monkeypatch.setenv('KIS_APP_KEY','test-only')
    monkeypatch.setenv('KIS_APP_SECRET','test-only')
    return obs

def test_approval_physical_count_and_cached_key(telemetry,monkeypatch):
    calls=[]
    def post(*a,**kw):
        calls.append(1)
        snap=telemetry.snapshot()
        assert snap['foreground_active']==1 and snap['physical_active']==1
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'approval_key':'mock-approval-value'})
    monkeypatch.setattr(ws.requests,'post',post)
    assert ws.approval_key()==ws.approval_key()
    snap=telemetry.snapshot()
    assert calls==[1] and snap['token_attempts_total']==1 and snap['physical_attempts_total']==1
    assert snap['foreground_active']==snap['physical_active']==0

def test_approval_error_counts_and_releases(telemetry,monkeypatch):
    def post(*a,**kw):
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{})
    monkeypatch.setattr(ws.requests,'post',post)
    with pytest.raises(RuntimeError,match='KIS_APPROVAL_UNAVAILABLE'):
        ws.approval_key()
    snap=telemetry.snapshot()
    assert snap['recent_provider_errors']==1 and snap['physical_attempts_total']==1
    assert snap['foreground_active']==snap['physical_active']==0

def test_subscription_counts_as_foreground_until_end(telemetry,monkeypatch):
    async def fake(client,code):
        assert telemetry.snapshot()['foreground_active']==1
        await asyncio.sleep(0)
        assert telemetry.snapshot()['foreground_active']==1
        raise RuntimeError('mock interruption')
    monkeypatch.setattr(ws,'_relay',fake)
    with pytest.raises(RuntimeError,match='mock interruption'):
        asyncio.run(ws.relay(None,'005930'))
    assert telemetry.snapshot()['foreground_active']==0

def test_disabled_subscription_does_not_count(telemetry,monkeypatch):
    monkeypatch.setattr(ws,'enabled',lambda:False)
    async def fake(client,code):
        assert telemetry.snapshot()['foreground_active']==0
    monkeypatch.setattr(ws,'_relay',fake)
    asyncio.run(ws.relay(None,'005930'))
    assert telemetry.snapshot()['physical_attempts_total']==0
