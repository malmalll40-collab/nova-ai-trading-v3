"""NOVA v3.9 defect regressions: final signal, no-lookahead, fees, UI safety."""
from __future__ import annotations
from zoneinfo import ZoneInfo
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from engine import Config, WARMUP
from signal_policy import resolve_final_signal
import server
import backtest
from data_sources import demo_data


def resolve(sig='BUY',gate='PASS',mode='스윙',stale=False,symbol='005930.KS',demo=False):
    return resolve_final_signal(sig,mode=mode,symbol=symbol,financial_gate=gate,
                                stale=stale,is_demo=demo)


def test_domestic_buy_requires_verified_finance():
    for gate in ['UNKNOWN','CAUTION','BLOCK']:
        data=resolve(gate=gate)
        assert data['final_signal']=='HOLD'
        assert data['final_actionable_signal']=='HOLD'
    assert resolve(gate='PASS')['final_signal']=='BUY'


def test_swing_sell_stays_sell_and_finance_never_upgrades_hold():
    assert resolve('SELL','BLOCK')['final_signal']=='SELL'
    assert resolve('HOLD','PASS')['final_signal']=='HOLD'


def test_intraday_and_non_dart_stock_are_technical_only():
    assert resolve(mode='1분',gate='UNKNOWN')['final_signal']=='BUY'
    assert resolve(symbol='AAPL',gate='NOT_SUPPORTED')['final_signal']=='BUY'
    assert resolve(symbol='AAPL',gate='NOT_SUPPORTED')['final_reason'].startswith('OpenDART 미지원')


def test_stale_blocks_actionable_signal_and_keeps_historical():
    p=resolve(gate='PASS',stale=True)
    assert p['final_signal']=='HOLD'
    assert p['historical_signal']=='BUY'
    assert p['final_actionable_signal'] is None
    assert p['signal_is_current'] is False
    assert resolve(gate='PASS',demo=True)['final_actionable_signal'] is None


def test_backtest_terminal_liquidation_and_benchmark_use_same_exit_fee(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    n=WARMUP+20
    bars=pd.DataFrame({'Open':100.,'High':100.,'Low':100.,'Close':100.,
                       'atr':2.},index=pd.date_range('2026-01-01',periods=n,freq='B'))
    monkeypatch.setattr(backtest,'feature_frame',lambda data,mode:bars)
    monkeypatch.setattr(backtest,'decide',lambda data,cfg,i: SimpleNamespace(
        signal='BUY' if i==n-2 else 'HOLD'))
    initial=10_000_000
    bt=backtest.run_backtest(bars,Config.for_mode('스윙',15),initial_cash=initial)
    assert bt.closed_trades == 1
    assert bt.trades.iloc[-1]['청산사유'].startswith('검증 종료 가상청산')
    assert bt.trades.iloc[-1]['순손익'] < 0
    assert bt.total_return == pytest.approx(bt.curve['전략 자산'].iloc[-1]/initial-1)
    assert bt.benchmark_return == pytest.approx(bt.curve['비교 매수보유'].iloc[-1]/initial-1)
    assert bt.total_return < 0 and bt.benchmark_return < 0
    buy_fee=15/10000
    qty=int((initial*0.25)//(100*(1+buy_fee)))
    net_benchmark=initial-qty*100*(1+buy_fee)+qty*100*(1-buy_fee)
    assert bt.curve['비교 매수보유'].iloc[-1] == pytest.approx(net_benchmark)


def _fake_yahoo(symbol, mode,*args):
    data=demo_data(mode)
    return SimpleNamespace(bars=data.bars,symbol=symbol,provider='MOCK',retrieved_at='2026-11-10T00:00:00Z',
                           quote_time='2026-11-10T00:00:00Z',freshness='TEST',note='모의 데이터 테스트',
                           currency='KRW',synthetic=False,quote=100.0)


def _forced_buy(monkeypatch):
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a,**k:None)
    monkeypatch.setattr(server,'completed_swing_bars',lambda x:(x,{'stale':False,'state':'CURRENT_COMPLETED_SESSION'}))
    monkeypatch.setattr(server,'kis_data',_fake_yahoo)
    monkeypatch.setattr(server,'_last_age_minutes',lambda *args:0.0)
    original=server.decide
    def buy(f,cfg):
        d=original(f,cfg)
        d.signal='BUY'
        return d
    monkeypatch.setattr(server,'decide',buy)
    original_q=server.quality_gate
    def pass_quality(f,cfg,raw_signal):
        gate=original_q(f,cfg,raw_signal=raw_signal)
        gate['strict_signal']='BUY'
        return gate
    monkeypatch.setattr(server,'quality_gate',pass_quality)
    server.CACHE.clear()


def test_api_swing_financial_block_forces_final_hold_and_syncs_watch(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    _forced_buy(monkeypatch)
    monkeypatch.setattr(server,'_get_financial_state',lambda *args:('BLOCK','적자 확인',2025))
    client=TestClient(server.app)
    response=client.get('/api/analysis',params={'symbol':'005930.KS','mode':'스윙','source':'kis'})
    assert response.status_code==200,response.text
    d=response.json()
    assert d['strict_signal']=='BUY' and d['signal']=='HOLD'
    assert d['financial_gate']=='BLOCK' and d['final_actionable_signal']=='HOLD'
    scan=server._watch_item('005930.KS','스윙','kis',15)
    assert scan['combined']=='HOLD' and scan['final_actionable_signal']=='HOLD'
    server.CACHE.clear()


def test_api_missing_finance_swing_buy_is_not_actionable_buy(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    _forced_buy(monkeypatch)
    monkeypatch.setattr(server,'get_client',lambda: (_ for _ in ()).throw(RuntimeError('sensitive provider failure')))
    d=TestClient(server.app).get('/api/analysis',params={'symbol':'005930.KS','mode':'스윙','source':'kis'}).json()
    assert d['strict_signal']=='BUY' and d['financial_gate']=='UNKNOWN'
    assert d['signal']=='HOLD'
    assert 'sensitive provider failure' not in str(d)
    server.CACHE.clear()


def test_api_stale_signal_is_non_actionable(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    _forced_buy(monkeypatch)
    # Last Friday is stale after Monday's official close, regardless of age in minutes.
    from completed_session import completed_swing_bars
    source=_fake_yahoo('005930.KS','스윙')
    source.bars=source.bars[source.bars.index.date <= datetime(2026,11,20,tzinfo=ZoneInfo('Asia/Seoul')).date()].copy()
    monkeypatch.setattr(server,'kis_data',lambda *args:source)
    monkeypatch.setattr(server,'completed_swing_bars',lambda bars:completed_swing_bars(bars,now=datetime(2026,11,23,18,tzinfo=ZoneInfo('Asia/Seoul'))))
    monkeypatch.setattr(server,'_last_age_minutes',lambda *args:999999.0)
    monkeypatch.setattr(server,'_get_financial_state',lambda *args:('PASS','',2025))
    d=TestClient(server.app).get('/api/analysis',params={'symbol':'005930.KS','mode':'스윙','source':'kis'}).json()
    assert d['is_stale'] is True
    assert d['signal']=='HOLD' and d['final_actionable_signal'] is None
    assert '지난 완료봉' in d['signal_label']
    server.CACHE.clear()


def test_frontend_alert_finance_independent_and_journal_safety():
    root=Path(server.__file__).parent
    app=(root/'static/app.js').read_text()
    journal=(root/'static/journal.js').read_text()
    monitor=(root/'static/monitor.js').read_text()
    assert 'const final=d.final_actionable_signal;' in app
    assert 'was.bar < bar && was.signal !== final' in app
    assert 'renderFusion(null)' in app
    assert 'loadFinanceForFusion(d.symbol)' not in app
    assert "financeSymbol!==symbol || view!=='finance'" in app
    assert "d.mode!=='스윙'" in journal
    assert "x.mode==='스윙'" in journal
    assert 'row.final_actionable_signal' in monitor
    assert 'signal:row.final_actionable_signal' in monitor


def test_health_pwa_version_and_no_orders():
    client=TestClient(server.app)
    d=client.get('/healthz').json()
    assert d['app_version']=='5.6.3' and d['orders_enabled'] is False
    assert d['intraday_journal_verified'] is False
    assert 'nova3-shell-v45' in client.get('/sw.js').text
    assert 'id="view-guide"' in client.get('/').text
