"""NOVA v3.6 read-only foreground batch scanner, no broker orders or scheduled jobs."""
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
import pytest
import server
from data_sources import demo_data
from finance_gate import assess_financial

client = TestClient(server.app)

def test_health_readonly_and_assets():
    info=client.get('/healthz').json()
    assert info['app_version']=='5.6.4'
    assert info['orders_enabled'] is False
    assert info['watchlist_visible_tab_only'] is True
    assert info['push_notifications'] is False
    html=client.get('/').text
    assert 'monitorButton' in html
    assert 'monitorResults' in html
    assert '/static/monitor.js' in html
    assert 'nova3-shell-v45' in client.get('/sw.js').text
    assert 'startsWith(\'/api/\')' in client.get('/sw.js').text
    assert 'document.hidden' in client.get('/static/monitor.js').text
    assert 'showNotification' in client.get('/static/monitor.js').text
    assert client.get('/static/monitor.js').status_code==200


def test_batch_rejects_invalid(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.setattr(server, '_limit', lambda _:None)
    assert client.post('/api/watch/scan',json={'symbols':[]}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL']*6}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL','aapl']}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'modes':['1분','5분','스윙']}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'modes':['스윙','스윙']}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'modes':['2분']}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'modes':[{}]}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'source':'demo'}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'source':'kis'}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['<bad>']}).status_code==422
    assert client.post('/api/watch/scan',json={'symbols':['AAPL'],'cost_bps':True}).status_code==422


def test_batch_auth_and_order_no_mutation(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','T'*24)
    monkeypatch.setattr(server,'_limit',lambda _:None)
    assert client.post('/api/watch/scan',json={'symbols':['AAPL']}).status_code==401
    def fake_one(s,m,source,cost):
        return {'symbol':s,'mode':m,'source':source,'status':'ok','combined':'HOLD','is_stale':False}
    monkeypatch.setattr(server,'_watch_item',fake_one)
    r=client.post('/api/watch/scan',headers={'Authorization':'Bearer '+'T'*24},json={'symbols':['005930.KS','086520.KQ'],'modes':['5분','스윙']})
    assert r.status_code==200,r.text
    d=r.json()
    assert d['requested']==4 and d['successful']==4 and d['real_orders'] is False
    assert [(x['symbol'],x['mode']) for x in d['results']]==[('005930.KS','5분'),('005930.KS','스윙'),('086520.KQ','5분'),('086520.KQ','스윙')]


def test_partial_results_cannot_be_fabricated(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(server,'_limit',lambda _:None)
    def fake_one(s,m,source,cost):
        if s=='005930.KS':raise ConnectionError('private url/token not for client')
        return {'symbol':s,'mode':m,'source':source,'status':'ok','combined':'BUY','is_stale':True}
    monkeypatch.setattr(server,'_watch_item',fake_one)
    d=client.post('/api/watch/scan',json={'symbols':['005930.KS','086520.KQ'],'modes':['스윙']}).json()
    assert d['successful']==1 and d['requested']==2
    assert d['results'][0]['status']=='error' and d['results'][0]['combined'] is None
    assert 'private url' not in str(d)
    assert d['results'][1]['combined']=='BUY' and d['results'][1]['is_stale']


def test_current_financial_gate_only_for_swing(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    source=demo_data('스윙')
    def fake_market(symbol, mode,*args):
        sample=demo_data(mode)
        return SimpleNamespace(bars=sample.bars,symbol=symbol,provider='test-feed',quote=100,
             quote_time='fake',retrieved_at='2026-11-09T07:00:00Z',freshness='TEST',note='테스트',currency='KRW',synthetic=False)
    monkeypatch.setattr(server,'kis_data',fake_market)
    monkeypatch.setattr(server,'_last_age_minutes',lambda *args:0.0)
    monkeypatch.setattr(server,'completed_swing_bars',lambda x:(x,{'stale':False}))
    class FakeClient:
        key='x'*40
        def overview(self, symbol, years=3):
            return {'status':'ok','annual_latest':2025,'annual':[{'fs_div':'CFS'}],
                    'metrics':{'revenue':1000,'revenue_yoy_pct':-20,'operating_profit':-2,
                     'operating_profit_yoy_pct':-60,'net_income':-50,'operating_cashflow':-20,
                     'operating_margin_pct':-3,'debt_ratio_pct':350}}
    monkeypatch.setattr(server,'get_client',lambda:FakeClient())
    d=server._watch_item('005930.KS','스윙','kis',15)
    assert d['status']=='ok' and d['financial_gate']=='BLOCK' and d['combined'] in ('HOLD','SELL')
    assert d['is_stale'] is False
    tech=server._watch_item('005930.KS','5분','kis',15)
    assert tech['status']=='ok' and tech['financial_gate']=='NOT_APPLIED'
    assert tech['combined']==tech['technical']


def test_unknown_financial_does_not_claim_pass(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.setattr(server,'kis_data',lambda symbol, mode,*args: SimpleNamespace(
        bars=demo_data(mode).bars,symbol=symbol,provider='test-feed',retrieved_at='now',
        freshness='TEST',note='테스트',currency='KRW',synthetic=False))
    monkeypatch.setattr(server,'_last_age_minutes',lambda *_:0.0)
    monkeypatch.setattr(server,'completed_swing_bars',lambda x:(x,{'stale':False}))
    monkeypatch.setattr(server,'get_client',lambda: (_ for _ in ()).throw(ValueError('no key')))
    data=server._watch_item('086520.KQ','스윙','kis',25)
    assert data['financial_gate']=='UNKNOWN'
    assert data['combined']==('HOLD' if data['quality_gate']['strict_signal']=='BUY' else data['quality_gate']['strict_signal'])
    assert '미확인' in data['finance_note']


def test_no_real_orders_and_no_clock_in_journal():
    source=Path(server.__file__).read_text()
    assert 'post(\'/api/watch/scan\')' in source
    assert 'create_order(' not in source
    assert 'BackgroundTasks(' not in source
    assert '/api/watch/scan' not in Path(server.ASSETS/'sw.js').read_text()
