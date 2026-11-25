"""Prevent the confirmed DataFrame->dict cache bug in /api/chart/history?source=kis."""
from datetime import datetime
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
import pytest
import server


def ohlcv(idx):
    v=np.linspace(40000,55000,len(idx))
    return pd.DataFrame({'Open':v-10,'High':v+20,'Low':v-30,'Close':v,
                         'Volume':np.full(len(idx),200000)},index=pd.DatetimeIndex(idx))


def test_kis_chart_df_survives_cache_and_all_four_periods(monkeypatch):
    """With old code, every call returned 502 because _cached converts DataFrame to dict."""
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','not-a-real-key')
    monkeypatch.setenv('KIS_APP_SECRET','not-a-real-secret')
    server.CACHE.clear();server.RATE.clear()
    frames={
        '1d':ohlcv(pd.bdate_range('2025-12-01',periods=180)),
        '1w':ohlcv(pd.date_range('2025-01-03',periods=52,freq='W-FRI')),
        '1mo':ohlcv(pd.date_range('2021-01-01',periods=55,freq='MS')),
        '1y':ohlcv(pd.date_range('1999-12-31',periods=26,freq='YE')),
    }
    calls=[]
    def fetch(symbol,timeframe):
        assert symbol=='005930.KS'
        calls.append(timeframe)
        return frames[timeframe]
    monkeypatch.setattr(server,'fetch_kis_native_period',fetch)
    c=TestClient(server.app)
    for tf in frames:
        for hit in range(2):
            res=c.get('/api/chart/history',params={'symbol':'005930.KS','source':'kis','timeframe':tf})
            assert res.status_code==200, f'{tf} hit={hit}: {res.text}'
            body=res.json()
            assert body['source']=='kis' and body['timeframe']==tf
            assert body['chart']['dates']
            assert body['chart']['close']
            assert len(body['chart']['dates'])==len(body['chart']['close'])
            assert body['streaming'] is False
            assert body['polling_supported'] is True
    assert sorted(calls)==sorted(frames.keys())   # 15 min cache works
    server.CACHE.clear();server.RATE.clear()


def test_failed_kis_provider_does_not_return_fake_candles(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','not-a-real-key')
    monkeypatch.setenv('KIS_APP_SECRET','not-a-real-secret')
    server.CACHE.clear();server.RATE.clear()
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda *args:(_ for _ in ()).throw(RuntimeError('SECRET_PROVIDER_DETAILS')))
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d')
    assert r.status_code==502
    assert 'SECRET_PROVIDER_DETAILS' not in r.text
    server.CACHE.clear();server.RATE.clear()


def test_kis_chart_503_when_keys_missing(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    server.CACHE.clear();server.RATE.clear()
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1w')
    assert r.status_code==503
    server.CACHE.clear();server.RATE.clear()


def test_kis_vendor_code_is_visible_without_vendor_details(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','not-a-real-key')
    monkeypatch.setenv('KIS_APP_SECRET','not-a-real-secret')
    server.CACHE.clear();server.RATE.clear()
    def bad(*args):
        raise RuntimeError('KIS 시세 오류 EGW00201: SECRET_PROVIDER_DETAILS')
    monkeypatch.setattr(server,'fetch_kis_native_period',bad)
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d')
    assert r.status_code==502
    assert 'EGW00201' in r.text
    assert 'SECRET_PROVIDER_DETAILS' not in r.text
    server.CACHE.clear();server.RATE.clear()
