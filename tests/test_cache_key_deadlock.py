"""Deterministic regression tests for nested cache lookups and lock lifetime."""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
import server

class CollisionKey:
    def __init__(self, name):
        self.name = name
    def __hash__(self):
        return 42
    def __eq__(self, other):
        return isinstance(other, CollisionKey) and self.name == other.name

@pytest.fixture(autouse=True)
def clean_cache():
    with server.CACHE_LOCK:
        server.CACHE.clear()
    yield
    with server.CACHE_LOCK:
        server.CACHE.clear()

def test_nested_distinct_colliding_keys():
    outer, inner = CollisionKey('outer'), CollisionKey('inner')
    def calculate():
        return {'value': server._cached(inner, lambda: {'value': 7})['value']}
    assert server._cached(outer, calculate)['value'] == 7
    assert server.CACHE_KEY_LOCKS == {}

def test_concurrent_same_key_coalesces():
    calls = []
    entered = threading.Event()
    release = threading.Event()
    def calculate():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return {'value': 9}
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(server._cached, ('regression', 1), calculate)
        assert entered.wait(3)
        b = pool.submit(server._cached, ('regression', 1), calculate)
        release.set()
        assert a.result(timeout=3)['value'] == b.result(timeout=3)['value'] == 9
    assert len(calls) == 1
    assert server.CACHE_KEY_LOCKS == {}

def test_failed_fetch_releases_lock_and_does_not_cache():
    key = ('regression', 'fail')
    def fail():
        raise ValueError('test failure')
    with pytest.raises(ValueError):
        server._cached(key, fail)
    assert key not in server.CACHE
    assert server.CACHE_KEY_LOCKS == {}
    assert server._cached(key, lambda: {'value': 1})['value'] == 1
    assert server.CACHE_KEY_LOCKS == {}
