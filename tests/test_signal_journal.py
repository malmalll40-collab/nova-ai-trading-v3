from datetime import date, timedelta
import math
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from signal_journal import evaluate_batch, evaluate_record, validate_record
from server import app

DAYS=pd.bdate_range('2026-06-01', periods=19)
FRAME=pd.DataFrame({'Close': [100,101,103,105,99,97,107,110,106,104,103,106,104,101,99,101,102,105,108]}, index=DAYS)

def record(**changes):
    obj={'id':'yahoo|005930.KS|스윙|2026-06-01','symbol':'005930.KS','source':'yahoo','mode':'스윙',
         'bar_day':'2026-06-01','reference':100,'technical':'BUY','filtered':'BUY','gate':'PASS','cost_bps':15}
    obj.update(changes)
    return obj


def test_requires_strict_validated_records():
    r=validate_record(record())
    assert r['cost_bps']==15
    with pytest.raises(ValueError):validate_record(record(reference='NaN'))
    with pytest.raises(ValueError):validate_record(record(reference=0))
    with pytest.raises(ValueError):validate_record(record(technical='HOLD'))
    with pytest.raises(ValueError):validate_record(record(technical='SELL',filtered='BUY'))
    with pytest.raises(ValueError):validate_record(record(symbol='AAPL<script>'))
    with pytest.raises(ValueError):validate_record(record(bar_day='2026-14-01'))


def test_forward_dates_and_next_day_only():
    result=evaluate_record(validate_record(record()),FRAME)['horizons']
    assert result['1']['settlement_day']=='2026-06-02'
    assert result['1']['asset_change_pct']==1
    assert result['3']['settlement_day']=='2026-06-04'
    assert result['3']['direction_hit'] is True
    assert result['5']['settlement_day']=='2026-06-08'
    assert result['10']['settlement_day']=='2026-06-15'


def test_buy_cost_and_close_path_drawdown():
    o=evaluate_record(validate_record(record()),FRAME)['horizons']['5']
    assert o['asset_change_pct']==-3
    assert o['buy_net_pct']==-3.3
    assert o['direction_hit'] is False
    assert o['close_path_drawdown_pct']<0


def test_sell_does_not_simulate_short_profit():
    rec=record(technical='SELL',filtered='SELL')
    o=evaluate_record(validate_record(rec),FRAME)['horizons']['5']
    assert o['direction_hit'] is True
    assert o['directional_score_pct']==3
    assert o['buy_net_pct'] is None


def test_pending_when_not_enough_future_candles():
    o=evaluate_record(validate_record(record()),FRAME.iloc[:3])['horizons']
    assert o['1']['status']=='complete'
    assert o['3']['status']=='pending'
    assert o['10']['status']=='pending'


def test_incomplete_same_day_returns_pending():
    short=FRAME.iloc[:1]
    assert all(x['status']=='pending' for x in evaluate_record(validate_record(record(mode='1분')),short)['horizons'].values())


def test_missing_anchor_date_does_not_fabricate_outcome():
    result=evaluate_record(validate_record(record(bar_day='2026-06-06')),FRAME)['horizons']
    assert all(x['status']=='unavailable' for x in result.values())


def test_same_day_not_closed_is_pending():
    result=evaluate_record(validate_record(record(bar_day='2026-06-26',mode='1분')),FRAME)['horizons']
    assert all(x['status']=='pending' for x in result.values())


def test_adjusted_price_mismatch_blocks_swing():
    o=evaluate_record(validate_record(record(reference=120)),FRAME)['horizons']
    assert all(x['status']=='unavailable' for x in o.values())


def test_batch_uses_one_loader_call_per_stock():
    calls=[]
    def loader(symbol,source):calls.append((symbol,source));return FRAME
    r=evaluate_batch([record(id='one'),record(id='two')],loader)
    assert len(calls)==1 and len(r['results'])==2


def test_batch_bounds_and_safe_errors():
    def fail(*args):raise RuntimeError('API_TOKEN_PRIVATE')
    r=evaluate_batch([record()],fail)
    assert 'API_TOKEN_PRIVATE' not in str(r)
    with pytest.raises(ValueError):evaluate_batch([],fail)
    with pytest.raises(ValueError):evaluate_batch([record(id=str(x)) for x in range(61)],fail)
    with pytest.raises(ValueError):evaluate_batch([record(id=str(x),symbol=f'{x:06}.KS') for x in range(5)],fail)


def test_endpoint_auth_precedes_data_access(monkeypatch):
    import server
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','J'*24)
    client=TestClient(server.app)
    assert client.post('/api/journal/evaluate',json={'records':[record()]}).status_code==401
    resp=client.post('/api/journal/evaluate',json={'records':[]},headers={'Authorization':'Bearer '+'J'*24})
    assert resp.status_code==422
    monkeypatch.setattr(server, '_journal_daily_bars', lambda symbol,source: FRAME)
    resp=client.post('/api/journal/evaluate',json={'records':[record()]},headers={'Authorization':'Bearer '+'J'*24})
    assert resp.status_code==200 and resp.json()['results'][0]['horizons']['1']['status']=='complete'
    server.RATE.clear()


def test_mobile_pwa_provides_client_journal():
    client=TestClient(app)
    h=client.get('/').text
    assert 'journalMetrics' in h and 'journalRefresh' in h and 'journalExportCsv' in h
    assert '/static/journal.js' in h
    assert 'v3.7' in h
    assert client.get('/static/journal.js').status_code==200
    assert client.get('/healthz').json()['forward_signal_journal'] is True
    assert 'nova3-shell-v45' in client.get('/sw.js').text
