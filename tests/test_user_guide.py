from nova_release import APP_VERSION
from pathlib import Path
from fastapi.testclient import TestClient
import server


def test_guide_tab_in_rendered_shell_and_version():
    client = TestClient(server.app)
    response = client.get('/')
    assert response.status_code == 200
    h = response.text
    assert 'data-view="guide"' in h
    assert h.count('class="nav-item') == 9
    assert 'id="v5MoreNav"' in h
    assert 'id="view-guide"' in h
    assert h.count('class="guide-step"') == 11
    assert h.index('id="view-guide"') > h.index('id="view-finance"')
    assert client.get('/healthz').json()['app_version'] == APP_VERSION


def test_guide_explains_existing_functions_without_fabricated_accuracy():
    h = (server.ASSETS / 'index.html').read_text(encoding='utf-8')
    for section in [
        '기본 신호 vs 안정형 신호', '기본 vs 엄격 매수 신호',
        '재무·공시', '관심종목', 'JSON 백업',
        '로그인 유지', '투자 수익을 보장하지 않습니다',
    ]:
        assert section in h
    assert 'data-guide-target="backtest"' in h
    assert 'data-guide-action="settings"' in h


def test_guide_works_without_analysis_and_sw_increments():
    js=(server.ASSETS/'app.js').read_text(encoding='utf-8')
    css=(server.ASSETS/'styles.css').read_text(encoding='utf-8')
    assert "next==='guide'||next==='news'||next==='recommendations'||!latest" in js
    assert "['analyze','recommendations','backtest','research','finance','watchlist','news','guide']" in js
    assert '.guide-open #results' in css
    assert 'repeat(7,minmax(0,1fr))' in css
    assert 'nova3-shell-v45' in (server.ASSETS/'sw.js').read_text(encoding='utf-8')


def test_prior_analysis_functions_unchanged():
    h=(server.ASSETS/'index.html').read_text(encoding='utf-8')
    for id in ['qualityPanel','qualityBacktestPanel','financeStatus','journalMetrics','monitorResults']:
        assert f'id="{id}"' in h
