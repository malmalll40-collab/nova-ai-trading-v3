"""v3.7 fixed conservative gate + paired chronological evaluation tests."""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from engine import Config, WARMUP
from signal_quality import quality_gate
from quality_validation import quality_comparison


def fixture_features(n: int = 100) -> pd.DataFrame:
    idx = pd.date_range('2026-01-01', periods=n, freq='B')
    return pd.DataFrame({
        'Open': 105., 'Close': 106., 'Volume': 2200., 'atr': 2.,
        'ema20': 105., 'ema50': [99.+i/1000 for i in range(n)],
        'rsi': 60., 'macd': 2., 'macd_s': 1., 'vol_ratio': 1.1,
        'vwap': 102.,
    },index=idx)


def test_buy_gate_pass_and_independent_trend_required():
    f=fixture_features()
    cfg=Config.for_mode('스윙')
    ok=quality_gate(f,cfg,raw_signal='BUY')
    assert ok['raw_signal']=='BUY'
    assert ok['strict_signal']=='BUY'
    assert ok['approved_buy'] and ok['passed_count']==5
    # Reversal of regime blocks buys even if other indicators remain bullish.
    f.loc[f.index[-1], 'ema50']=110.
    veto=quality_gate(f,cfg,raw_signal='BUY')
    assert veto['strict_signal']=='HOLD'
    assert veto['approved_buy'] is False
    assert '추세 방향' in veto['failed_checks']


def test_gate_never_invents_buy_or_short():
    f=fixture_features();cfg=Config.for_mode('스윙')
    assert quality_gate(f,cfg,raw_signal='HOLD')['strict_signal']=='HOLD'
    assert quality_gate(f,cfg,raw_signal='SELL')['strict_signal']=='SELL'
    assert quality_gate(f,cfg,raw_signal='SELL')['approved_buy'] is False


def test_extreme_extension_and_activity_block_new_buy():
    f=fixture_features()
    f.loc[f.index[-1], 'Close']=140.
    f.loc[f.index[-1], 'Volume']=0.
    gate=quality_gate(f,Config.for_mode('스윙'),raw_signal='BUY')
    assert gate['strict_signal']=='HOLD'
    assert '거래 참여' in gate['failed_checks']
    assert '과열·장중 위치' in gate['failed_checks']


def test_gate_uses_past_only():
    f=fixture_features(105);cfg=Config.for_mode('스윙')
    a=quality_gate(f,cfg,i=90,raw_signal='BUY')
    modified=f.copy()
    modified.iloc[91:,modified.columns.get_loc('Close')]=1000000.
    modified.iloc[91:,modified.columns.get_loc('Volume')]=0.
    b=quality_gate(modified,cfg,i=90,raw_signal='BUY')
    assert a==b


def test_intraday_requires_vwap_and_trading_costs():
    f=fixture_features();cfg=Config.for_mode('1분',cost_bps=100)
    f.loc[f.index[-1], 'vwap']=110.
    f.loc[f.index[-1], 'atr']=1.4 # estimated target gross move falls below cost hurdle
    a=quality_gate(f,cfg,raw_signal='BUY')
    assert a['strict_signal']=='HOLD'
    assert '과열·장중 위치' in a['failed_checks']
    assert '변동성·거래비용' in a['failed_checks']


def test_validation_next_open_and_nonoverlap(monkeypatch):
    import quality_validation
    f=fixture_features(200)
    f['Close']=100.
    f['Open']=100.
    # 40-bar tail region; potential evaluation decisions at 160,166,172 etc.
    f.loc[f.index[161:],'Open']=101.
    f.loc[f.index[165:],'Close']=105.
    def fake_decide(f,cfg,i):return SimpleNamespace(signal='BUY')
    monkeypatch.setattr(quality_validation,'decide',fake_decide)
    monkeypatch.setattr(quality_validation,'quality_gate',
       lambda f,cfg,i,raw_signal:{'approved_buy':i%2==0})
    result=quality_comparison(f,Config.for_mode('스윙',cost_bps=20))
    assert result['raw_buy']['samples']>=2
    assert result['strict_buy']['samples']<=result['raw_buy']['samples']
    assert result['raw_buy']['mean_net_pct'] is not None
    assert result['raw_buy']['mean_net_pct'] < 5 # next opening price 101, not prior close 100
    assert result['sufficient_sample'] is False


def test_live_api_demo_includes_comparison_and_no_probability():
    import server
    c=TestClient(server.app)
    r=c.get('/api/analysis',params={'source':'demo','mode':'스윙'})
    assert r.status_code==200, r.text
    obj=r.json()
    assert obj['quality_gate']['raw_signal'] in ('BUY','SELL','HOLD')
    assert obj['signal']==obj['quality_gate']['strict_signal']
    assert obj['quality_comparison']['strict_buy']['samples']<=obj['quality_comparison']['raw_buy']['samples']
    assert obj['quality_comparison']['sufficient_sample'] in (True,False)
    assert c.get('/healthz').json()['quality_hit_rate_verified'] is False
    assert 'qualityPanel' in c.get('/').text
