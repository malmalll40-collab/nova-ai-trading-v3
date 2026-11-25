"""Mocked v4.4 historical screen: no public portal key or fabricated live results."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server
import recommendation_backtest as bt
from market_scanner import MarketScanError


def bars(start='2026-09-01',end='2026-11-06',base=100.0):
    dates=pd.bdate_range(start,end)
    return pd.DataFrame({'Open':[base]*(len(dates)),
                         'Close':[base*1.01**i for i in range(len(dates))]},index=dates)


def row(sym,market='kospi',change=2.1):
    return dict(symbol=sym,name=sym,market=market,close=70000,volume=250000,
                turnover_krw=12_000_000_000,market_cap_krw=1_000_000_000_000,
                change_pct=change)


class FakeMarket:
    def __init__(self,data):self.data=data;self.calls=[]
    def fetch_day(self,date,market):
        self.calls.append((date,market))
        return [x.copy() for x in self.data.get(date,[]) if x['market']==market]


def test_holding_1_3_5_10_and_publication_delay():
    f=bars('2026-09-01','2026-10-31',100)
    out=bt.trade_outcome(f,'20261005',15)
    assert out['publication_date']=='2026-10-06'
    assert out['entry_date']=='2026-10-07'  # not Oct 5 close, not Oct 6 open
    ix=f.index.get_loc(pd.Timestamp('2026-10-07'))
    assert out['net_pct'][1]==pytest.approx(round((f.iloc[ix]['Close']/100-1)*100-.3,5))
    assert out['exit_dates'][3]=='2026-10-09'


def test_cost_and_partial_results_are_not_invented():
    f=bars('2026-10-05','2026-10-09')
    d=bt.trade_outcome(f,'20261005',20)
    assert d['net_pct'][1] is not None
    assert d['net_pct'][10] is None
    with pytest.raises(ValueError):bt.trade_outcome(f,'20261005',120)
    with pytest.raises(ValueError):bt.trade_outcome(pd.DataFrame(), '20261005')


def test_anchor_never_uses_recent_future_dates():
    ks=ZoneInfo('Asia/Seoul')
    a=bt.anchor_days(datetime(2026,11,10,14,tzinfo=ks),4)
    assert len(a)==4 and len(set(a))==4 and a==sorted(a)
    assert max(a)<='20261001'
    with pytest.raises(ValueError):bt.anchor_days(datetime(2026,11,10,14,tzinfo=ks),100)


def test_past_snapshot_only_selects_fsc_not_future_data():
    # The second symbol's future chart is enormously profitable, but its
    # historical snapshot fails the liquidity screen so it must NOT be chosen.
    snap='20261005'
    current=[row('000660.KS'),row('005930.KS',change=19)]
    called=[]
    def load(sym):
        called.append(sym)
        return bars(base=100 if sym=='000660.KS' else 1)
    result=bt.run_screen_history(market='kospi',api_key='fake',weeks=4,
        client=FakeMarket({snap:current}),test_dates=[snap],load_bars=load)
    assert called==['000660.KS']
    assert result['scope']=='first_stage_only' and result['full_nova_strategy_backtested'] is False
    assert result['trades_total']==1 and result['fully_verified']==1
    assert result['summary']['5']['samples']==1
    assert result['portfolio_return'] is None and result['portfolio_mdd'] is None


def test_incomplete_vendor_prices_are_never_counted_as_wins():
    snap='20261005'
    data=[row('000660.KS'),row('005930.KS')]
    def load(sym):
        if sym=='005930.KS':raise RuntimeError('private key in URL should never surface')
        return bars(end='2026-10-09')
    r=bt.run_screen_history(market='kospi',api_key='not-to-display',weeks=4,
        client=FakeMarket({snap:data}),test_dates=[snap],load_bars=load)
    assert r['status']=='incomplete' and not r['complete_coverage']
    assert r['trades_total']==2 and r['fully_verified']==0
    assert r['summary']['10']['samples']==0 and r['summary']['10']['hit_pct'] is None
    assert 'private key' not in str(r) and 'not-to-display' not in str(r)


def test_reject_missing_entire_market_snapshot():
    with pytest.raises(MarketScanError) as ex:
        bt.run_screen_history(market='all',api_key='x',client=FakeMarket({'20261005':[row('000660.KS')]}),
            test_dates=['20261005'],load_bars=lambda x:bars())
    assert ex.value.code=='historical_gap'


def test_api_auth_settings_and_parameter_validation(monkeypatch):
    c=TestClient(server.app)
    server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','secret-key-very-long-123')
    monkeypatch.setenv('FSC_STOCK_API_KEY','fsc-private-key')
    assert c.post('/api/recommendations/backtest/run',json={'market':'all','weeks':4}).status_code==401
    assert c.get('/api/recommendations/backtest/status').status_code==401
    h={'Authorization':'Bearer secret-key-very-long-123'}
    assert c.post('/api/recommendations/backtest/run',json={'market':'invalid'},headers=h).status_code==422
    assert c.post('/api/recommendations/backtest/run',json={'weeks':100},headers=h).status_code==422
    assert c.post('/api/recommendations/backtest/run',json={'weeks':True},headers=h).status_code==422
    assert c.post('/api/recommendations/backtest/run',json={'cost_bps':-2},headers=h).status_code==422
    assert 'fsc-private-key' not in c.get('/api/recommendations/backtest/status',headers=h).text
    monkeypatch.delenv('FSC_STOCK_API_KEY')
    assert c.post('/api/recommendations/backtest/run',json={'weeks':4},headers=h).status_code==503


def test_backtest_route_async_and_status(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('FSC_STOCK_API_KEY','secret-key-not-visible')
    def fake(**kwargs):return {'scope':'first_stage_only','status':'complete','trades_total':0,'fully_verified':0,'summary':{}}
    monkeypatch.setattr(server,'run_screen_history',fake)
    with server.BACKTEST_LOCK:server.BACKTEST_STATE.clear();server.BACKTEST_STATE['status']='idle'
    with server.SCAN_LOCK:server.SCAN_STATE['status']='idle'
    r=c.post('/api/recommendations/backtest/run',json={'market':'all','weeks':4})
    assert r.status_code==200
    server.BACKTEST_JOB.join(2)
    got=c.get('/api/recommendations/backtest/status').json()
    assert got['status']=='complete' and got['result']['scope']=='first_stage_only'
    assert 'secret-key-not-visible' not in str(got)


def test_ui_has_backend_connection_and_pwa_cache():
    html=(server.ASSETS/'index.html').read_text(encoding='utf8')
    js=(server.ASSETS/'recommendation_history.js').read_text(encoding='utf8')
    sw=(server.ASSETS/'sw.js').read_text(encoding='utf8')
    assert 'id="historyRun"' in html and 'id="historyResults"' in html
    assert '/api/recommendations/backtest/run' in js
    assert '/api/recommendations/backtest/status' in js
    assert 'nova3-shell-v45' in sw
    assert '/static/recommendation_history.js' in sw
    assert server.healthz()['app_version']=='5.6.3'
