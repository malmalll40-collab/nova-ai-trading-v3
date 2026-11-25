"""Navigation-only quality gates; trading rules and market data remain untouched."""
from pathlib import Path
from bs4 import BeautifulSoup
import server

ASSETS = Path(server.ASSETS)


def _doc():
    return BeautifulSoup((ASSETS/'index.html').read_text(encoding='utf-8'), 'html.parser')


def test_chart_is_first_in_analyze_view_after_search():
    s=_doc()
    res=s.select_one('#results')
    analyze=res.select_one('#view-analyze')
    assert analyze and res.select_one('#chartPanel')
    children=[x for x in analyze.children if getattr(x, 'name', None)]
    assert children[0].name=='nav' and 'analysis-jump' in children[0].get('class',[])
    assert children[1].get('id')=='chartPanel'
    assert analyze.select_one('#chartPanel') is not None
    assert analyze.select_one('#tradeDecisionPanel') is not None
    assert str(analyze).index('id="chartPanel"') < str(analyze).index('id="tradeDecisionPanel"')
    assert res.select_one('#chartPanel').find_parent(id='view-analyze') is not None


def test_key_risk_info_follows_signal_and_precedes_advanced_content():
    s=_doc();a=s.select_one('#view-analyze')
    ids=['chartPanel','tradeDecisionPanel','riskOverviewPanel','riskStructurePanel','qualityPanel','analysisDetails']
    for i in ids: assert a.select_one('#'+i)
    markup=str(a)
    assert all(markup.index('id="'+ids[i]+'"') < markup.index('id="'+ids[i+1]+'"') for i in range(len(ids)-1))


def test_advanced_details_are_collapsed_but_fully_available():
    s=_doc(); d=s.select_one('#analysisDetails')
    assert d and d.name=='details' and not d.has_attr('open')
    assert s.select_one('#view-research #highPrecisionPanel') and not d.select_one('#highPrecisionPanel')
    assert d.select_one('#fusionPanel')
    assert s.select_one('#view-research #signalPerformancePanel') and not d.select_one('#signalPerformancePanel')
    assert d.select_one('#roleList')
    assert d.select_one('#metaData') is None


def test_tab_boundaries_remain_intact():
    s=_doc()
    for name in ('analyze','backtest','research','finance','watchlist','recommendations','news','guide'):
        assert len(s.select('#view-'+name))==1
    for other in ('backtest','finance','watchlist'):
        assert s.select_one('#view-'+other).find_parent(id='view-analyze') is None


def test_quick_nav_buttons_have_valid_targets_and_are_real_controls():
    s=_doc();buttons=s.select('.analysis-jump [data-scroll-target]')
    assert len(buttons)==4
    for b in buttons:
        assert b.name=='button' and b.get('type')=='button'
        assert s.select_one('#'+b['data-scroll-target']) is not None


def test_chart_clearly_warns_it_is_not_a_live_stream():
    s=_doc(); c=s.select_one('#chartPanel')
    assert '완료봉 기준 차트' in c.get_text(' ')
    assert '실시간 체결가 아님' in c.get_text(' ')
    assert c.select_one('#candleCanvas')
    assert c.select_one('#v45ChartQuote')
    assert c.select_one('.chart-help')


def test_search_jump_does_not_affect_auto_refresh():
    js=(ASSETS/'app.js').read_text(encoding='utf-8')
    assert 'async function analyze({jumpToChart=false}={})' in js
    assert "$('searchForm').addEventListener('submit',e=>" in js
    assert "analyze({jumpToChart:true})" in js
    assert 'if(jumpToChart)' in js
    assert "if(target.tagName==='DETAILS')target.open=true" in js
    assert "if(!document.hidden&&!pending)analyze();" in js


def test_pwa_cache_is_bumped_and_finance_remains_read_only():
    sw=(ASSETS/'sw.js').read_text(encoding='utf-8')
    assert 'nova3-shell-v45-korea-only-chartfirst' in sw
    assert "url.pathname.startsWith('/api/')" in sw
    assert not (server.BASE/'nasdaq_universe.py').exists()
    assert server.app
