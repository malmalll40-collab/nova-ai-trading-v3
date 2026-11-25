from __future__ import annotations
from datetime import datetime,timedelta,timezone
import importlib
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
import pytest
import server
import backtest
from engine import Config,WARMUP

client=TestClient(server.app)

def test_demo_analysis_all_modes_and_13_roles():
    for mode in ['1분','5분','스윙']:
        r=client.get('/api/analysis',params={'source':'demo','mode':mode})
        assert r.status_code==200,r.text
        d=r.json()
        assert d['is_demo'] is True
        assert d['signal'] in ['BUY','SELL','HOLD']
        assert d['signal_is_current'] is False
        assert len(d['votes'])==13
        assert d['buy_votes']+d['sell_votes']+d['hold_votes']==12
        assert len(d['chart']['close'])==125
        assert d['backtest']['directional_samples']>=0
        assert d['long_stop_reference']<d['reference']<d['long_target_reference']
        assert len(d['backtest']['curve']['strategy'])==len(d['backtest']['curve']['dates'])

def test_bad_input_no_market_call(monkeypatch):
    monkeypatch.delenv('KIS_APP_KEY',raising=False);monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    assert client.get('/api/analysis',params={'mode':'30분','source':'demo'}).status_code==422
    assert client.get('/api/analysis',params={'cost_bps':500,'source':'demo'}).status_code==422
    assert client.get('/api/analysis',params={'source':'yahoo','symbol':'hello;rm -rf'}).status_code==422
    assert client.get('/api/analysis',params={'source':'kis','symbol':'AAPL'}).status_code==422
    assert client.get('/api/analysis',params={'source':'kis','symbol':'005930'}).status_code==503  # missing server keys

def test_auth_protection(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','secret_test_123456789')
    assert client.get('/api/analysis',params={'source':'demo'}).status_code==401
    assert client.get('/api/status').status_code==401
    r=client.get('/api/analysis',params={'source':'demo'},headers={'Authorization':'Bearer secret_test_123456789'})
    assert r.status_code==200
    assert r.json()['is_demo']
    monkeypatch.delenv('NOVA_ACCESS_TOKEN')

def test_pwa_assets():
    assert client.get('/').status_code==200
    assert 'viewport-fit=cover' in client.get('/').text
    assert client.get('/manifest.webmanifest').status_code==200
    assert client.get('/sw.js').status_code==200
    assert 'startsWith(\'/api/\')' in client.get('/sw.js').text
    assert client.get('/static/icons/icon-192.png').status_code==200
    assert client.get('/static/icons/icon-512.png').status_code==200

def test_no_future_candle_lookahead():
    from data_sources import demo_data
    from engine import feature_frame,decide
    d=demo_data('1분').bars
    f=feature_frame(d.iloc[:155],'1분')
    a=decide(f,Config.for_mode('1분'))
    shifted=d.copy()
    shifted.loc[shifted.index>=d.index[155],'Close']*=1.2
    z=decide(feature_frame(shifted.iloc[:155],'1분'),Config.for_mode('1분'))
    assert a.signal==z.signal

def test_sell_direction_accuracy_considers_costs(monkeypatch):
    length=WARMUP+20
    ix=pd.date_range('2026-10-30',periods=length,freq='min',tz='Asia/Seoul')
    f=pd.DataFrame({'Open':100.,'Close':100.},index=ix)
    def fake_decide(feat,cfg,i):return SimpleNamespace(signal='SELL' if i==WARMUP-1 else 'HOLD')
    monkeypatch.setattr(backtest,'decide',fake_decide)
    cfg=Config.for_mode('1분',cost_bps=15)
    rate,n,_,_,records,_,_=backtest._accuracy(f,cfg)
    assert n==1
    assert rate==0
    assert records.iloc[0]['방향 적중']==False or not records.iloc[0]['방향 적중']
    assert records.iloc[0]['신호방향 수익률(왕복 비용 차감)']<0

def test_caching_and_no_real_orders():
    r1=client.get('/api/analysis',params={'source':'demo','mode':'스윙','cost_bps':20})
    r2=client.get('/api/analysis',params={'source':'demo','mode':'스윙','cost_bps':20})
    assert r1.status_code==200 and r2.status_code==200
    assert r2.json()['cached'] is True
    assert client.get('/healthz').json()['orders_enabled'] is False
    assert 'order-cash' not in Path(server.__file__).read_text()


def test_preview_is_synthetic():
    preview=(Path(__file__).resolve().parents[1]/'NOVA_v3_모바일_체험판.html').read_text()
    assert '오프라인 미리보기' in preview
    assert 'window.NOVA_PREVIEW_DATA' in preview
    assert 'is_demo' in preview
