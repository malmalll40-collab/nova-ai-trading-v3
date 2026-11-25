"""NOVA v4.4 high precision: research-only selection and production protection."""
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from data_sources import demo_data
from engine import Config, feature_frame
from high_precision_adoption import observe_high_precision
import server


def test_shadow_demo_never_approves_live():
    f = feature_frame(demo_data('스윙').bars,'스윙')
    result = observe_high_precision(features=f,cfg=Config.for_mode('스윙'),
               symbol='005930.KS',mode='스윙',is_demo=True,is_stale=False,
               financial_gate='PASS',decision_at=datetime.now(ZoneInfo('Asia/Seoul')))
    assert result['adoption_mode']=='SHADOW_ONLY'
    assert result['state']=='DEMO_EXCLUDED'
    assert result['baseline_signal_modified'] is False
    assert result['live_orders_approved'] is False
    assert result['performance_advantage_verified'] is False


def test_shadow_stale_blocks():
    f = feature_frame(demo_data('스윙').bars,'스윙')
    result = observe_high_precision(features=f,cfg=Config.for_mode('스윙'),
               symbol='005930.KS',mode='스윙',is_demo=False,is_stale=True,
               financial_gate='PASS',decision_at=datetime.now(ZoneInfo('Asia/Seoul')))
    assert result['state']=='STALE_BLOCKED'
    assert not result['live_orders_approved']


def test_shadow_foreign_or_intraday_out_of_scope():
    f = feature_frame(demo_data('스윙').bars,'스윙')
    result = observe_high_precision(features=f,cfg=Config.for_mode('스윙'),
               symbol='AAPL',mode='스윙',is_demo=False,is_stale=False,
               financial_gate='PASS',decision_at=datetime.now(ZoneInfo('Asia/Seoul')))
    assert result['state']=='OUT_OF_SCOPE'


def test_shadow_does_not_change_original_13_vote_result():
    client=TestClient(server.app)
    for mode in ('스윙','1분'):
        resp=client.get('/api/analysis',params={'source':'demo','mode':mode})
        assert resp.status_code==200,resp.text
        d=resp.json()
        assert d['high_precision_research']['adoption_mode']=='SHADOW_ONLY'
        assert d['high_precision_research']['live_orders_approved'] is False
        assert d['signal']==d['historical_signal'] or d['is_stale']
        assert len(d['votes'])==13
        assert d['high_precision_research']['state'] in {'DEMO_EXCLUDED', 'OUT_OF_SCOPE'}


def test_status_says_unvalidated_and_no_orders():
    client=TestClient(server.app)
    assert client.get('/healthz').json()['high_precision_adoption_mode']=='SHADOW_ONLY'
    assert client.get('/healthz').json()['orders_enabled'] is False
    r=client.get('/api/status')
    assert r.status_code==200
    assert r.json()['high_precision_live_validated'] is False
    assert r.json()['real_orders'] is False


def test_ui_is_clear_and_non_actionable():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=(root/'static/app.js').read_text(encoding='utf-8')
    assert 'highPrecisionPanel' in html
    assert '연구 관찰' in html
    assert 'function renderHighPrecision' in js
    assert 'renderHighPrecision(d)' in js
