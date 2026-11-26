from nova_release import APP_VERSION
import server
import ops_diagnostics

def test_release_health_and_ops_match():
    assert server.healthz()['app_version']==APP_VERSION
    assert server.healthz()['orders_enabled'] is False
    assert server.healthz()['pattern_research_ui'] is True
    assert ops_diagnostics.VERSION==APP_VERSION

def test_served_index_release_and_discovery():
    r=server.index()
    html=r.body.decode('utf-8')
    assert '__NOVA_APP_VERSION__' not in html
    assert '<title>NOVA | AI Trading Mobile v'+APP_VERSION+'</title>' in html
    assert 'TRADING LAB · MOBILE V'+APP_VERSION in html
    assert 'NOVA v'+APP_VERSION+' · 실거래 주문 없음' in html
    assert 'NOVA v5.7.0 · EVIDENCE FIRST' in html
    assert 'id="patternMenu"' in html
    assert 'id="patternMenuBody">' in html
    assert 'id="patternMenuToggle"' not in html
    for asset in ('pattern_research.js','recommendations.js','candles.js'):
        assert '/static/'+asset+'?v='+APP_VERSION+'"' in html
    assert r.headers['cache-control']=='no-cache'

def test_service_worker_release_cache_is_dynamic():
    r=server.service_worker()
    js=r.body.decode('utf-8')
    assert '__NOVA_APP_VERSION__' not in js
    assert 'v585-pattern-ui-app-'+APP_VERSION in js
    assert r.headers['cache-control']=='no-store'
    assert r.headers['service-worker-allowed']=='/'
    assert '/api/' in js and '/healthz' in js
    assert "const RELEASE='"+APP_VERSION+"'" in js
    assert 'INSTALL_FILES' in js and "cache:'reload'" in js
    assert 'cache.match(e.request)' in js
