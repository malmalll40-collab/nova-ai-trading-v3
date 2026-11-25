"""Independent verification fixes for NOVA search/rank PWA release."""
from fastapi.testclient import TestClient
from pathlib import Path
import server


def test_service_worker_refreshes_mutable_assets_and_preserves_api_safety():
    js=(server.ASSETS/'sw.js').read_text()
    assert 'nova3-shell-v45-korea-only-chartfirst-popular-breadth-status-rank-20261120' in js
    assert "nova3-shell-v45-korea-only-chartfirst-popular-breadth-20261120';" not in js
    assert "url.pathname.startsWith('/api/')" in js
    assert "url.pathname.endsWith('.js')||url.pathname.endsWith('.css')" in js
    assert "fetch(e.request,{cache:'no-store'})" in js
    assert "caches.match(e.request)" in js
    assert "skipWaiting()" in js and "clients.claim()" in js


def test_network_error_clears_previous_stock_cards():
    js=(server.ASSETS/'recommendations.js').read_text()
    state=js.split('async function fetchState(){',1)[1].split('async function scan(){',1)[0]
    failure=state.split("}catch(e){",1)[1]
    assert "$('recommendNumbers').hidden=true" in failure
    assert "$('recommendResults').replaceChildren()" in failure
    assert "shownResult=''" in failure
    assert "setSearchState('unavailable'" in failure


def test_health_reports_cachefix_version_and_sw_has_no_store():
    client=TestClient(server.app)
    health=client.get('/healthz')
    assert health.status_code==200
    assert health.json().get('recommendation_ui_release')=='2026-11-20-status-rank-cachefix'
    sw=client.get('/sw.js')
    assert sw.status_code==200
    assert sw.headers['cache-control']=='no-store'
    assert 'status-rank-20261120' in sw.text
