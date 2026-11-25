"""Regression tests for NOVA v4.5 stability maintenance: no external network."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import time
import pytest
from fastapi.testclient import TestClient
import server
from data_sources import ticker_format


def test_bare_six_digit_requires_verified_market():
    assert ticker_format('086520') == '086520.KQ'  # known KOSDAQ
    assert ticker_format('005930') == '005930.KS'  # known KOSPI
    assert ticker_format('086520.KS') == '086520.KS'  # explicit market not rewritten
    with pytest.raises(ValueError, match='구분'):
        ticker_format('999998')


def test_unknown_six_digit_is_422_not_wrong_market(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN', 'z'*20)
    c=TestClient(server.app)
    r=c.get('/api/analysis?source=kis&symbol=999998',headers={'Authorization':'Bearer '+'z'*20})
    assert r.status_code == 422
    assert '코스피' in r.json()['detail']


def test_cache_single_flight_for_same_symbol():
    key=('v45_stability_test', '005930.KS', time.monotonic_ns())
    called=[]
    lock=Lock()
    barrier=Barrier(8)
    def make():
        with lock:called.append(1)
        time.sleep(.04)
        return {'v':321}
    def worker(_):
        barrier.wait(timeout=5)
        return server._cached(key,make,ttl=30)['v']
    with ThreadPoolExecutor(max_workers=8) as pool:
        got=list(pool.map(worker,range(8)))
    assert got == [321]*8
    assert len(called) == 1


def test_failed_fetch_never_cached():
    key=('v45_stability_fail',time.monotonic_ns())
    calls=[]
    def fail():
        calls.append(1)
        raise RuntimeError('blocked')
    for _ in range(2):
        with pytest.raises(RuntimeError):server._cached(key,fail)
    assert len(calls)==2


def test_kis_status_never_discloses_credentials(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','r'*22)
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    c=TestClient(server.app)
    headers={'Authorization':'Bearer '+'r'*22}
    r=c.get('/api/status',headers=headers)
    assert r.status_code==200
    assert r.json()['kis_status']=='NOT_CONFIGURED'
    assert r.json()['quote_provider']=='KIS_ONLY'
    monkeypatch.setenv('KIS_APP_KEY','DoNotLeakKey')
    monkeypatch.setenv('KIS_APP_SECRET','DoNotLeakSecret')
    r=c.get('/api/status',headers=headers)
    assert r.json()['kis_status']=='CONFIGURED_NOT_VERIFIED'
    assert 'DoNotLeakKey' not in r.text and 'DoNotLeakSecret' not in r.text
    assert c.get('/healthz').json()['release_tag']=='4.5-stability-20261112'


def test_429_has_retry_after(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','t'*23)
    h={'Authorization':'Bearer '+'t'*23}
    c=TestClient(server.app)
    for _ in range(24):
        r=c.get('/api/analysis?source=invalid',headers=h)
        assert r.status_code==422
    r=c.get('/api/analysis?source=invalid',headers=h)
    assert r.status_code==429 and r.headers['Retry-After']=='60'
