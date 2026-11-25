"""No real investment statements are used in tests; samples are synthetic."""
import pytest
from finance_gate import RULES, assess_financial, combine_signal

@pytest.fixture
def healthy():
    return {'status':'ok','annual_latest':2025,'annual':[{'fs_div':'CFS'}],
            'metrics':{'revenue_yoy_pct':18.,'operating_margin_pct':16.,'net_income':12e9,
                       'operating_cashflow':13e9,'debt_ratio_pct':55.,'revenue':140e9,
                       'operating_profit_yoy_pct':20.,'operating_profit':20e9}}

def test_weights_total_100():
    assert sum(x[2] for x in RULES)==100

def test_fully_healthy(healthy):
    d=assess_financial(healthy)
    assert d['gate']=='PASS' and d['coverage_pct']==100 and d['score']==100
    assert d['statement_type']=='CFS'

def test_two_red_flags_restrict_buy(healthy):
    healthy['metrics']['operating_profit']=-1e8
    healthy['metrics']['net_income']=-8e8
    d=assess_financial(healthy)
    assert d['gate']=='BLOCK'
    assert combine_signal('BUY',d)['combined']=='HOLD'
    assert combine_signal('SELL',d)['combined']=='SELL'

def test_one_red_flag_caution(healthy):
    healthy['metrics']['operating_cashflow']=-2e9
    d=assess_financial(healthy)
    assert d['gate']=='CAUTION'
    assert combine_signal('BUY',d)['modified']

def test_missing_metrics_keep_unknown(healthy):
    for k in ('revenue_yoy_pct','operating_margin_pct','net_income','operating_cashflow','revenue'):
        healthy['metrics'][k]=None
    d=assess_financial(healthy)
    assert d['coverage_pct']<70 and d['score'] is None and d['gate']=='UNKNOWN'
    assert combine_signal('BUY',d)['combined']=='BUY'
    assert '미확인' in combine_signal('BUY',d)['reason']

def test_bad_data_not_counted(healthy):
    healthy['metrics']['debt_ratio_pct']=float('nan')
    healthy['metrics']['operating_margin_pct']='not a number'
    healthy['metrics']['operating_cashflow']=True
    d=assess_financial(healthy)
    assert d['coverage_pct']<100
    assert any(x['grade']=='미확인' for x in d['factors'])

def test_low_score_blocks_even_if_no_red_flags(healthy):
    healthy['metrics'].update(revenue_yoy_pct=-80, operating_margin_pct=0, debt_ratio_pct=240,
                               operating_profit_yoy_pct=-75)
    d=assess_financial(healthy)
    assert d['score']<=40 and d['gate']=='BLOCK'

def test_nothing_promotes_to_buy(healthy):
    d=assess_financial(healthy)
    assert combine_signal('HOLD',d)['combined']=='HOLD'
    assert combine_signal('SELL',d)['combined']=='SELL'
    assert combine_signal('BUY',d,'1분')['combined']=='BUY'

@pytest.mark.parametrize('signal',['LONG','SHORT',None])
def test_invalid_signal_throws(signal):
    with pytest.raises(ValueError):combine_signal(signal,{'gate':'PASS'})

def test_unavailable_filing_is_unknown(healthy):
    healthy['status']='partial'
    assert assess_financial(healthy)['gate']=='UNKNOWN'

def test_financial_endpoint_returns_gate_with_mock_client(monkeypatch, healthy):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','fake-12345678901234567890')
    class Client:
        key='a'*40
        def overview(self, symbol, years):
            assert symbol=='005930.KS' and years==3
            return dict(healthy, ticker=symbol)
    monkeypatch.setattr(server,'get_client',lambda:Client())
    server.CACHE.clear()
    client=TestClient(server.app)
    rsp=client.get('/api/fundamentals',params={'symbol':'005930.KS','years':3},headers={'Authorization':'Bearer fake-12345678901234567890'})
    assert rsp.status_code==200
    data=rsp.json()
    assert data['financial_gate']['gate']=='PASS'
    assert data['financial_gate']['score']==100
    assert data['annual_latest']==2025
    assert data['metrics']['revenue']==140e9
    assert client.get('/healthz').json()['historical_fundamental_backtest'] is False
    server.CACHE.clear()

def test_no_auth_finance_gate(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','fake-12345678901234567890')
    client=TestClient(server.app)
    rsp=client.get('/api/fundamentals?symbol=005930.KS')
    assert rsp.status_code==401
