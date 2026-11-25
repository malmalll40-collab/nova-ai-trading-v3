"""Production-compatible shadow release must never block original analysis."""
from fastapi.testclient import TestClient
import server


def test_shadow_exception_does_not_break_original_analysis(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError('shadow evaluator was unavailable')
    monkeypatch.setattr(server, 'observe_high_precision', raise_error)
    data = server._payload(symbol='005930.KS',mode='스윙',source='demo',cost_bps=15)
    assert data['high_precision_research']['state'] == 'RESEARCH_ERROR'
    assert data['high_precision_research']['baseline_signal_modified'] is False
    assert data['high_precision_research']['live_orders_approved'] is False
    assert data['signal'] == data['historical_signal']
    assert len(data['votes']) == 13
