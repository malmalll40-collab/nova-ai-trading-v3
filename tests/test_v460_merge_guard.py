"""Release merge guard: v4.5.2 KIS/session logic and v4.6 evidence coexist."""
from pathlib import Path
from fastapi.testclient import TestClient

import server


def test_release_features_are_present_without_orders():
    d=TestClient(server.app).get('/healthz').json()
    assert d['app_version']=='5.6.4'
    assert d['auto_swing_kis_first'] is True
    assert d['completed_session_freshness'] is True
    assert d['evidence_lab_version']=='4.7.0-auto-research'
    assert d['orders_enabled'] is False


def test_v452_ui_and_v46_ui_are_both_present():
    root=Path(server.ASSETS)
    html=(root/'index.html').read_text()
    assert 'id="intradayContext"' in html
    assert 'value="auto"' in html
    assert 'id="autoEvidenceStatus"' in html
    assert 'id="autoEvidenceButton"' in html
    assert '/static/intraday_context.js' in (root/'sw.js').read_text()
    assert 'void initAutoEvidence()' in (root/'journal.js').read_text()


def test_market_scan_persists_only_after_success(monkeypatch):
    stored=[]
    seen=[]
    result={'status':'complete','data_date':'20261120','recommendations':[]}
    def run_scan(**kwargs):
        seen.append(kwargs)
        return result
    monkeypatch.setattr(server,'run_market_scan',run_scan)
    monkeypatch.setattr(server,'store_auto_scan',lambda d,**kw:stored.append(d))
    monkeypatch.setattr(server,'evidence_backend',lambda:'postgresql')
    # This is the existing manual scan worker, NOT the scheduled market worker.
    job='merge-guard-job'
    with server.SCAN_LOCK:
        prior=dict(server.SCAN_STATE)
        server.SCAN_STATE['job_id']=job
    try:
        server._scan_worker('all',job,'krx','test-key',24)
        assert len(stored)==1 and stored[0] is result
        assert seen[0]['analyze_asof'] is not None
        assert seen[0]['depth']==24
    finally:
        with server.SCAN_LOCK:
            server.SCAN_STATE.clear()
            server.SCAN_STATE.update(prior)


def test_autotrack_is_off_until_explicitly_configured(monkeypatch):
    monkeypatch.delenv('NOVA_AUTOTRACK_ENABLED',raising=False)
    monkeypatch.delenv('NOVA_AUTOTRACK_TOKEN',raising=False)
    resp=TestClient(server.app).post('/api/autotrack/run')
    assert resp.status_code==503
    assert 'AUTOTRACK_TOKEN' not in resp.text


def test_missing_market_provider_does_not_claim_daily_success(monkeypatch):
    monkeypatch.delenv('FSC_STOCK_API_KEY',raising=False)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    monkeypatch.setattr(server,'evaluate_auto_pending',lambda *a,**k:{'outcomes_new':0})
    monkeypatch.setattr(server,'evaluate_auto_observed',lambda *a,**k:{'outcomes_new':0})
    attempts=[]
    monkeypatch.setattr(server,'record_auto_attempt',lambda **kw:attempts.append(kw))
    with server.AUTO_LOCK: previous=dict(server.AUTO_STATE)
    try:
        server._auto_worker()
        with server.AUTO_LOCK:
            state=dict(server.AUTO_STATE)
        assert state['status']=='partial'
        assert state['result']['scan']['status']=='skipped'
        assert attempts[0]['state']=='partial'
    finally:
        with server.AUTO_LOCK:
            server.AUTO_STATE.clear()
            server.AUTO_STATE.update(previous)
