"""KIS upstream error classification and bounded retry without secret exposure."""
import requests
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
import server
from data_sources import KIS


class Response:
    def __init__(self,status=200,payload=None):
        self.status_code=status
        self.payload=payload if payload is not None else {'rt_cd':'0','output2':[]}
    def json(self):
        return self.payload


def provider(*replies):
    api=KIS.__new__(KIS)
    api.key='SENSITIVE_APP_KEY'
    api.secret='SENSITIVE_APP_SECRET'
    api.token='SENSITIVE_BEARER_TOKEN'
    api.session=Mock()
    api.session.get.side_effect=list(replies)
    return api


def test_transient_429_then_success(monkeypatch):
    monkeypatch.setattr('data_sources.time.sleep',lambda x:None)
    api=provider(Response(429),Response(200))
    assert api.get('/uapi/test','FHKST03010100',{})['rt_cd']=='0'
    assert api.session.get.call_count==2


def test_persistent_429_classification_does_not_expose_keys(monkeypatch):
    monkeypatch.setattr('data_sources.time.sleep',lambda x:None)
    api=provider(Response(429),Response(429))
    with pytest.raises(RuntimeError,match='^KIS_HTTP_429$') as exc:
        api.get('/uapi/test','FHKST03010100',{})
    assert api.session.get.call_count==2
    assert 'SENSITIVE' not in str(exc.value)


def test_upstream_timeout_classification(monkeypatch):
    monkeypatch.setattr('data_sources.time.sleep',lambda x:None)
    api=provider(requests.Timeout('SENSITIVE_KEY'),requests.Timeout('SENSITIVE_KEY'))
    with pytest.raises(RuntimeError,match='^KIS_UPSTREAM_TIMEOUT$'):
        api.get('/uapi/test','FHKST03010100',{})


def test_rejected_response_classification():
    api=provider(Response(403))
    with pytest.raises(RuntimeError,match='^KIS_HTTP_403$'):
        api.get('/uapi/test','FHKST03010100',{})
    assert api.session.get.call_count==1


def test_chart_endpoint_safe_diagnostic(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','not-a-real-key')
    monkeypatch.setenv('KIS_APP_SECRET','not-a-real-secret')
    server.CACHE.clear();server.RATE.clear()
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda *_: (_ for _ in ()).throw(RuntimeError('KIS_HTTP_429')))
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d')
    assert r.status_code==502
    assert 'KIS_HTTP_429' in r.text
    assert 'not-a-real' not in r.text
    server.CACHE.clear();server.RATE.clear()
