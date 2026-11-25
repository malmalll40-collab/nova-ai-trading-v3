"""v5.1 audit: trade ledger, role-removal evidence and bounded read-only multi-stock API."""
import copy
import time
from fastapi.testclient import TestClient
import pytest
from engine import Config
from nova_v5_research import analyze, simulate_long_only
from tests.test_core_comparison_473 import sample_frames


def test_trade_ledger_reconciles_cost_and_is_not_real_fill():
    f,_=sample_frames(250)
    signals={118:'BUY', 128:'BUY',150:'BUY'}
    res=simulate_long_only(f,signals,110,170,15,include_trades=True)
    assert res['status']=='READY'
    assert res['completed_trades']==len(res['trade_ledger'])
    assert res['trade_ledger_truncated'] is False
    for trade in res['trade_ledger']:
        assert trade['signal_date']<trade['entry_date']<=trade['exit_date']
        assert trade['actual_fill'] is False
        assert trade['entry_price']>0 and trade['quantity_shares']>0
        assert trade['entry_rule']=='신호 다음 거래일 시가'
        fee=.0015
        ret=100*(trade['exit_price']*(1-fee)/(trade['entry_price']*(1+fee))-1)
        assert abs(ret-trade['net_trade_return_pct'])<.01


def test_no_trades_not_claimed_profitable():
    f,_=sample_frames(240)
    zero=simulate_long_only(f,{},110,180,15,include_trades=True)
    assert zero['completed_trades']==0
    assert not zero['performance_assessable']
    assert zero['trade_ledger']==[]


def test_v51_role_removal_produces_trace_without_modifying_source():
    f,idx=sample_frames(260)
    original=f.copy(deep=True)
    out=analyze(f,Config.for_mode('스윙'),benchmark=idx,source='kis')
    assert out['version']=='5.3.0' and out['status']=='READY'
    assert set(out['trade_diagnostics'])=={'baseline','five_core','ai_model'}
    assert out['research_only'] and not out['modifies_live_signal']
    for role in out['role_ablations']:
        assert 0<=role['removed_role_index']<=10
        assert role['base_completed_trades']==out['validation']['baseline']['completed_trades']
        assert role['removed_trade_count']==len(role['removed_trade_examples']) or role['removed_trade_count']>8
        assert role['without_completed_trades']==role['removed_model']['completed_trades']
    import pandas as pd
    pd.testing.assert_frame_equal(f,original)


def test_batch_auth_validate_and_finish(monkeypatch):
    import server
    token='a-long-auth-token-for-v51-test'
    monkeypatch.setenv('NOVA_ACCESS_TOKEN',token)
    client=TestClient(server.app)
    endpoint='/api/research/v5/batch/run'
    assert client.post(endpoint,json={'symbols':['005930.KS','000660.KS']}).status_code==401
    auth={'Authorization':'Bearer '+token}
    for bad in [[],['005930.KS'],['005930.KS']*2,['005930.KS']*6,['123456'],['005930.KS','999999.INVALID']]:
        assert client.post(endpoint,json={'symbols':bad},headers=auth).status_code==422
    assert client.post(endpoint,json={'symbols':['005930.KS','000660.KS'],'cost_bps':True},headers=auth).status_code==422
    assert client.get('/api/research/v5/batch/status').status_code==401
    called=[]
    def mock_one(symbol,source,cost):
        called.append((symbol,source,cost))
        return {'status':'READY','source':'test','validation':{
            'start':'2026-01-01','end':'2026-04-20',
            'baseline':{'status':'READY','completed_trades':1,'portfolio_return_pct':-0.2,'trade_ledger':[]},
            'five_core':{'status':'READY','completed_trades':0,'portfolio_return_pct':0,'trade_ledger':[]},
            'ai_model':{'status':'INSUFFICIENT'}} ,'role_ablations':[], 'trade_diagnostics':{}}
    monkeypatch.setattr(server,'_v5_one',mock_one)
    monkeypatch.setattr(server,'append_analysis',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('NO DB WRITES')))
    res=client.post(endpoint,json={'symbols':['005930.KS','000660.KS']},headers=auth)
    assert res.status_code==200 and res.json()['research_only']
    for _ in range(100):
        state=client.get('/api/research/v5/batch/status',headers=auth).json()
        if state['status']=='complete':break
        time.sleep(.01)
    assert state['status']=='complete'
    assert state['finished']==2
    assert len(state['rows'])==2
    assert len(called)==2
    assert all(row['status']=='READY' for row in state['rows'])
    assert all(x['cost_bps_per_side']==15 for x in [state])
    assert not state['places_orders'] and not state['modifies_live_signal']


def test_batch_failure_is_marked_not_fabricated(monkeypatch):
    import server
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    def fail(*_):raise RuntimeError('test token/secret should not leak')
    monkeypatch.setattr(server,'_v5_one',fail)
    client=TestClient(server.app)
    r=client.post('/api/research/v5/batch/run',json={'symbols':['005930.KS','247540.KQ']})
    assert r.status_code==200
    for _ in range(100):
        d=client.get('/api/research/v5/batch/status').json()
        if d['status']=='complete':break
        time.sleep(.01)
    assert d['status']=='complete' and d['finished']==2
    assert all(x['status']=='UNAVAILABLE' for x in d['rows'])
    assert 'secret' not in repr(d)


def test_v51_readonly_html_and_js_separate():
    import server
    h=(server.ASSETS/'index.html').read_text()
    js=(server.ASSETS/'v5_dashboard.js').read_text()
    for key in ('v51BatchRun','v51BatchSymbols','v51TradeRows','v51TradeMethod'):
        assert 'id="'+key+'"' in h
    assert '/api/research/v5/batch/run' in js
    assert '/api/research/v5/batch/status' in js
    assert 'trade_diagnostics' in js
    assert server.healthz()['v51_batch_research']
