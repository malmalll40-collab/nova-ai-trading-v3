"""Mobile workspace contract; ensure the reorganized DOM retains critical controls."""
from bs4 import BeautifulSoup
from pathlib import Path
import server

def html():
    return BeautifulSoup(server.index().body.decode(),'html.parser')

def test_five_primary_tabs_and_existing_research_destinations():
    s=html()
    assert [b.get('data-view') or b.get('id') for b in s.select('.v6-five-nav > button')]==['home','analyze','recommendations','watchlist','v5MoreNav']
    assert {b['data-view'] for b in s.select('#v5MoreMenu button[data-view]')}=={'finance','news','guide','backtest','research'}
    for x in ('home','analyze','recommendations','watchlist','finance','news','guide','backtest','research'):
        assert len(s.select('#view-'+x))==1

def test_controls_and_results_are_independently_accessible():
    s=html()
    for x in ('pattern','recommend'):
        for field in ('Source','Market','Depth','Scan','Cancel','Status','Results'):
            assert len(s.select('#'+x+field))==1
    assert s.select_one('#v6-search-controls') is None
    assert s.select_one('.v6-search-controls #patternScan')
    assert s.select_one('.v6-search-controls #recommendScan')
    assert s.select_one('#v6FullResults #recommendResults')
    assert s.select_one('#v6PatternResults #patternResults')
    assert s.select_one('#popularPanel #popularLoad')
    assert s.select_one('#v45Load')

def test_chart_and_risk_summary_preserve_research_links():
    s=html()
    for x in ('v6SignalLabel','v6SignalReason','v6SignalDate','v6Reference','v6Stop','v6Target','chartPanel','candleChartOverlay','tradeDecisionPanel','riskOverviewPanel','candleResearchPanel'):
        assert len(s.select('#'+x))==1
    ids=[t['id'] for t in s.select('[id]')]
    assert len(ids)==len(set(ids))

def test_home_has_no_automatic_market_scan_and_versioned_assets():
    js=Path('static/app.js').read_text()
    assert "else selectView('home')" in js
    assert "view==='analyze')analyze()" in js
    assert "if(!b||b.disabled||PREVIEW)return;" in js
    assert "nova-v6-recent-symbols" in js
    s=html()
    assert s.select_one('#v6MarketRefresh')
    assert s.select_one('#v6RecentStocks')
    assert s.select_one('#v6HomeFull') and s.select_one('#v6HomePattern')
