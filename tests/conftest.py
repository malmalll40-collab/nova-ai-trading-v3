"""Isolate the stateful request limiter between independent test cases.

Production rate limit remains unchanged (24 requests/IP/60s). A test may
still exercise throttling within its own case.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_request_rate_records():
    import server
    server.RATE.clear()
    yield
    server.RATE.clear()
