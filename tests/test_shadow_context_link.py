"""No actual provider calls: strict context-linkage contract and original signal guard."""
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest

import shadow_context as sc
from engine import Config, feature_frame
from data_sources import demo_data
import server

KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026,11,12,17,30,tzinfo=KST)


def ctx_frame():
    idx=pd.bdate_range(end='2026-11-12', periods=85)
    return pd.DataFrame({'Close':np.linspace(900,1200,len(idx)),
                         'available_at':[NOW.isoformat()]*len(idx)},
                        index=idx.strftime('%Y-%m-%d'))


def flow_data(days=20, have_institution=True):
    idx=pd.bdate_range(end='2026-11-11', periods=days)
    return {'source':'NAVER 공개 일별 매매동향','unit':'shares',
            'daily':[{'date':d.strftime('%Y%m%d'),'foreign':100 if j%2 else -20,
                      'institution':20 if have_institution else None,'individual':None}
                     for j,d in enumerate(idx)]}


def test_market_and_verified_etf_link_with_share_flow():
    refs=[]
    result=sc.build_shadow_inputs('005930.KS',now=NOW,
        ref_loader=lambda ticker:(refs.append(ticker) or ctx_frame()),
        flow_loader=lambda _: flow_data())
    assert refs==['^KS11','091160.KS']
    assert result['diagnostics']['benchmark']['status']=='READY'
    assert result['diagnostics']['sector']['kind']=='ETF_PROXY'
    assert result['diagnostics']['flows']['status']=='READY'
    assert result['flows'].iloc[-1]['foreign_net_shares']==100
    assert 'foreign_net_value' not in result['flows'].columns


def test_kosdaq_index_and_unmapped_sector_remain_unavailable():
    refs=[]
    c=sc.build_shadow_inputs('181710.KQ',now=NOW,
        ref_loader=lambda x:(refs.append(x) or ctx_frame()),flow_loader=lambda _:flow_data(5))
    assert refs==['^KQ11']
    assert c['diagnostics']['sector']['status']=='UNAVAILABLE'
    assert c['diagnostics']['flows']['status']=='PARTIAL'
    assert c['diagnostics']['flows']['complete_rows']==5


def test_past_scan_never_uses_todays_context():
    def forbidden(*args):
        raise AssertionError('historical look-ahead')
    result=sc.build_shadow_inputs('005930.KS',now=NOW,ref_loader=forbidden,
                                   flow_loader=forbidden,historical_asof='20261110')
    assert all(s['status']=='UNAVAILABLE' for s in result['diagnostics'].values())


def test_sources_fail_independently():
    def partly(ticker):
        if ticker=='091160.KS':raise RuntimeError('blocked')
        return ctx_frame()
    c=sc.build_shadow_inputs('005930.KS',now=NOW,ref_loader=partly,
                              flow_loader=lambda _:None)
    assert c['diagnostics']['benchmark']['status']=='READY'
    assert c['diagnostics']['sector']['status']=='UNAVAILABLE'
    assert c['diagnostics']['flows']['status']=='UNAVAILABLE'


def test_only_known_sector_proxies_selected():
    assert sc.ETF_PROXIES['005930'][0]=='091160.KS'
    assert sc.ETF_PROXIES['105560'][0]=='091170.KS'
    assert sc.ETF_PROXIES['005380'][0]=='091180.KS'
    assert '068270' not in sc.ETF_PROXIES


def test_flow_rejects_non_share_unit_and_partial_daily_without_invention():
    result,status=sc.flow_share_frame({'unit':'KRW','daily':flow_data()['daily']},NOW)
    assert result is None and status['status']=='UNAVAILABLE'
    p,meta=sc.flow_share_frame(flow_data(5,False),NOW)
    assert meta['status']=='PARTIAL'
    assert meta['complete_rows']==0
    assert p['institution_net_shares'].isna().all()


def test_flow_includes_only_completed_and_rejects_bad_values():
    data=flow_data(5)
    data['daily'].append({'date':'20261112','foreign':99999,'institution':99999})
    data['daily'][0]['foreign']=False
    frame,meta=sc.flow_share_frame(data,NOW)
    assert '2026-11-12' not in frame.index
    assert pd.isna(frame.loc[min(frame.index),'foreign_net_shares'])
    assert meta['status']=='PARTIAL'


def test_reference_ticker_allowlist():
    with pytest.raises(ValueError):
        sc.fetch_yahoo_reference('001999.KS')


def test_yahoo_current_unfinished_bar_excluded(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    dates=pd.DatetimeIndex(['2026-11-10','2026-11-11','2026-11-12']).tz_localize('Asia/Seoul')
    fake=SimpleNamespace(Ticker=lambda t: SimpleNamespace(history=lambda **kw:
         pd.DataFrame({'Close':[100,102,105]},index=dates)))
    monkeypatch.setitem(sys.modules,'yfinance',fake)
    frame=sc.fetch_yahoo_reference('^KS11',now=NOW.replace(hour=14))
    assert list(frame.index)==['2026-11-10','2026-11-11']
    frame2=sc.fetch_yahoo_reference('^KS11',now=NOW)
    assert len(frame2)==3


def test_high_precision_context_checks_use_shares_not_krw():
    from high_precision import _context_assessment
    df=demo_data('스윙').bars
    # Only the last completed days matter for the first-party filter.
    f=feature_frame(df,'스윙')
    days=pd.to_datetime(f.index)
    # Realistic synthetic fixtures, not proof of financial performance.
    context=pd.DataFrame({'Close':np.linspace(100,150,len(days)),
                          'available_at':[datetime.now(KST).isoformat()]*len(days)},index=days)
    ff=pd.DataFrame({'foreign_net_shares':[100]*len(days),
                     'institution_net_shares':[25]*len(days),
                     'available_at':[datetime.now(KST).isoformat()]*len(days)},index=days)
    checks=_context_assessment(f,len(f)-1,context,context,ff,
                               pd.Timestamp.now(tz=KST)+pd.Timedelta(minutes=1))
    assert checks[2]['state']=='PASS'
    assert '주(' in checks[2]['detail']
    assert '금액 아님' in checks[2]['detail']


def test_shadow_integration_cannot_change_actionable_signal(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    demo=demo_data('스윙')
    demo.symbol='005930.KS'
    demo.synthetic=False
    monkeypatch.setattr(server,'kis_data',lambda *args:demo)
    monkeypatch.setattr(server,'_last_age_minutes',lambda *args:1)
    monkeypatch.setattr(server,'_get_financial_state',lambda *args:('PASS','test',2025))
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *args:ctx_frame())
    monkeypatch.setattr(server,'build_shadow_inputs',lambda *args,**kwargs:
        {'benchmark':ctx_frame(),'sector':ctx_frame(),'flows':None,
         'diagnostics':{'benchmark':{'status':'READY','name':'KOSPI'},
                        'sector':{'status':'READY','name':'ETF_PROXY'},
                        'flows':{'status':'PARTIAL','detail':'5/20'}}})
    p=server._payload('005930.KS','스윙','kis',15)
    assert p['high_precision_research']['adoption_mode']=='SHADOW_ONLY'
    assert p['high_precision_research']['baseline_signal_modified'] is False
    assert p['high_precision_research']['live_orders_approved'] is False
    assert p['high_precision_research']['context_data']['benchmark']['status']=='READY'
    assert p['signal']==p['historical_signal']


def test_ui_context_is_plain_text_not_html():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    src=(root/'static/app.js').read_text()
    html=(root/'static/index.html').read_text()
    assert "setText('highPrecisionContextSources'" in src
    assert 'id="highPrecisionContextSources"' in html
    assert '실거래' in html


def test_server_reuses_flow_screen_cache_and_links_reference_prices(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    import time
    market=demo_data('스윙')
    market.symbol='005930.KS'
    market.synthetic=False
    monkeypatch.setattr(server,'kis_data',lambda *a:market)
    monkeypatch.setattr(server,'_last_age_minutes',lambda *a:1)
    monkeypatch.setattr(server,'_get_financial_state',lambda *a:('PASS','fixture',2025))
    calls=[]
    monkeypatch.setattr(server,'fetch_kis_reference',lambda code: (calls.append(code) or ctx_frame()))
    with server.CACHE_LOCK:
        server.CACHE.clear()
        server.CACHE[('v46_flow_per_investor_20261112','005930')]=(time.monotonic(),flow_data(20))
    p=server._payload('005930.KS','스윙','kis',15)
    c=p['high_precision_research']['context_data']
    assert calls==['^KS11','091160.KS']
    assert c['flows']['status']=='READY'
    assert p['high_precision_research']['live_orders_approved'] is False
    assert p['signal']==p['historical_signal']
    # Second analysis reuses both market/industry cached sources.
    server._payload('005930.KS','스윙','kis',15)
    assert len(calls)==2
    server.CACHE.clear()


def test_server_historical_scan_never_downloads_shadow_references(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    market=demo_data('스윙')
    market.symbol='005930.KS'
    market.synthetic=False
    # Use a representative historical scanner snapshot, not live provider data.
    target=pd.Timestamp(market.bars.index[-5]).strftime('%Y%m%d')
    monkeypatch.setattr(server,'kis_data',lambda *a: market)
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a:
                         (_ for _ in ()).throw(AssertionError('lookahead')))
    monkeypatch.setattr(server,'_last_age_minutes',lambda *a:1)
    monkeypatch.setattr(server,'_get_financial_state',lambda *a:('PASS','fixture',2025))
    p=server._payload('005930.KS','스윙','kis',15,asof_date=target)
    assert p['high_precision_research']['context_data']['benchmark']['status']=='UNAVAILABLE'
    assert p['high_precision_research']['live_orders_approved'] is False
