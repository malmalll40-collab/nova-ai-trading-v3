"""Offline status-poll quota and coherent independent scan snapshot."""
import json
from types import SimpleNamespace
import pytest
import server

def setup(monkeypatch):
    monkeypatch.setattr(server,'RATE',{})
    monkeypatch.setattr(server,'SCAN_STATUS_RATE',{})
    monkeypatch.setattr(server,'SCAN_PROFILES',{
        'full':{'status':'idle','result':None},
        'pattern':{'status':'running','job_id':'p1','progress':{'done':3,'total':24}}})
    monkeypatch.setattr(server,'_authorize',lambda r:None)
    monkeypatch.setattr(server,'evidence_backend',lambda:'disabled')
    return SimpleNamespace(client=SimpleNamespace(host='fixture'))

def test_combined_status_preserves_independent_state(monkeypatch):
    req=setup(monkeypatch)
    r=server.recommendations_status(req,'all')
    p=json.loads(r.body)['profiles']
    assert p['full']['status']=='idle'
    assert p['pattern']['status']=='running'
    assert p['pattern']['job_id']=='p1'
    assert p['full']['active_profile']==p['pattern']['active_profile']=='pattern'
    assert r.headers['cache-control']=='no-store'
    assert len(server.SCAN_STATUS_RATE['fixture'])==1
    assert server.RATE=={}

def test_status_budget_does_not_consume_data_api_budget(monkeypatch):
    req=setup(monkeypatch)
    now=[100.]
    monkeypatch.setattr(server.time,'monotonic',lambda:now[0])
    for i in range(12):
        server.recommendations_status(req,'all')
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_status(req,'all')
    assert e.value.status_code==429 and e.value.headers['Retry-After']=='60'
    for i in range(24):
        server._limit(req)
    with pytest.raises(server.HTTPException) as e:
        server._limit(req)
    assert e.value.status_code==429
    now[0]=161.
    assert server.recommendations_status(req,'all').status_code==200
    assert len(server.SCAN_STATUS_RATE['fixture'])==1

def test_combined_status_authentication_and_validation(monkeypatch):
    req=setup(monkeypatch)
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_status(req,'invalid')
    assert e.value.status_code==422
    def denied(r):raise server.HTTPException(401,'fixture')
    monkeypatch.setattr(server,'_authorize',denied)
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_status(req,'all')
    assert e.value.status_code==401
    assert not server.SCAN_STATUS_RATE

def test_legacy_status_and_combined_share_bounded_poll_budget(monkeypatch):
    req=setup(monkeypatch)
    for i in range(6):
        assert server.recommendations_status(req,'full').status_code==200
        assert server.recommendations_status(req,'pattern').status_code==200
    with pytest.raises(server.HTTPException) as e:
        server.recommendations_status(req,'all')
    assert e.value.status_code==429
    assert server.RATE=={}
