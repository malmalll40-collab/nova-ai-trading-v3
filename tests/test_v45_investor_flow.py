"""v4.5 strict read-only flow and REST quote regressions. Provider calls mocked."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
import pytest
import server
import investor_flow as flow

KST=ZoneInfo('Asia/Seoul')


def test_stock_code_only_korean_equities():
    assert flow.stock_code('005930')=='005930'
    assert flow.stock_code('005930.KS')=='005930'
    assert flow.stock_code('035720.kq')=='035720'
    for value in ['AAPL','005930.KS.X','00593','../005930', '', '005930 KQ']:
        with pytest.raises(ValueError): flow.stock_code(value)


def test_investor_aggregate_complete_and_partial():
    now=datetime(2026,11,10,12,30,tzinfo=KST)
    rows=[]
    for i in range(12):
        d=(now-timedelta(days=13-i)).strftime('%Y%m%d')
        rows.append({'stck_bsop_date':d, 'frgn_ntby_qty':'1', 'orgn_ntby_qty':'-2', 'prsn_ntby_qty':'3'})
    rows.append({'stck_bsop_date':'20261110','frgn_ntby_qty':'999999','orgn_ntby_qty':'999999','prsn_ntby_qty':'999999'})
    res=flow.summarize(rows,'005930',now)
    assert res['partial_today_excluded']
    assert res['aggregate'][0]['foreign']==5
    assert res['aggregate'][1]['institution']==-20
    assert res['aggregate'][2]['complete'] is False
    assert res['aggregate'][2]['foreign'] is None
    assert all(item['date']<'20261110' for item in res['daily'])


def test_missing_is_not_zero_and_sample_missing():
    now=datetime(2026,11,10,tzinfo=KST)
    rows=[{'stck_bsop_date':(now-timedelta(days=d)).strftime('%Y%m%d'),
           'frgn_ntby_qty':'4', 'orgn_ntby_qty': 'oops' if d==3 else '-4',
           'prsn_ntby_qty':'0'} for d in range(1,7)]
    res=flow.summarize(rows,'123456',now)
    assert res['aggregate'][0]['complete'] is False
    assert res['aggregate'][0]['institution'] is None


def test_signed_quote_no_guessed_sign():
    now=datetime(2026,11,10,tzinfo=KST)
    neg=flow.parse_quote({'stck_prpr':'78000','prdy_vrss_sign':'5','prdy_vrss':'1200','prdy_ctrt':'1.52'},'005930',now)
    assert neg['price']==78000 and neg['change']==-1200 and neg['change_pct']==-1.52
    unknown=flow.parse_quote({'stck_prpr':'78000','prdy_vrss':'1200','prdy_ctrt':'1.52'},'005930',now)
    assert unknown['change'] is None and unknown['change_pct'] is None
    assert unknown['realtime_stream'] is False
    with pytest.raises(RuntimeError):flow.parse_quote({'stck_prpr':'0'},'005930',now)


def test_provider_contract_investor(monkeypatch):
    args=[]
    monkeypatch.setattr(flow.KIS,'__init__',lambda self,a,b: None)
    def fake_get(self,path,tr_id,params):
        args.append((path,tr_id,params));return {'output': []}
    monkeypatch.setattr(flow.KIS,'get',fake_get)
    result=flow.get_flows('fakekey','fakesecret','005930')
    assert result['has_data'] is False
    assert args[0][0].endswith('/inquire-investor')
    assert args[0][1]=='FHKST01010900'
    assert args[0][2]['FID_INPUT_ISCD']=='005930'


def test_intraday_empty_and_chart_comes_from_real_bars(monkeypatch):
    import pandas as pd
    now=datetime(2026,11,10,14,25,tzinfo=KST)
    idx=pd.DatetimeIndex([pd.Timestamp('2026-11-10T10:00:00+09:00'),pd.Timestamp('2026-11-09T10:00:00+09:00')])
    df=pd.DataFrame({'Close':[5010,4990], 'Volume':[12,9]},index=idx)
    monkeypatch.setattr(flow.KIS,'__init__',lambda self,a,b: None)
    monkeypatch.setattr(flow.KIS,'minutes',lambda self,code,now,sessions=1: df)
    res=flow.get_intraday('x','x','005930',now)
    assert res['points']==[{'time':'10:00','price':5010.0,'volume':12}]


def test_http_auth_and_no_key(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','S'*22)
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    assert c.get('/api/market/quote?symbol=005930').status_code==401
    headers={'Authorization':'Bearer '+'S'*22}
    assert c.get('/api/market/flows?symbol=AAPL',headers=headers).status_code==422
    assert c.get('/api/market/quote?symbol=005930',headers=headers).status_code==503
    assert c.get('/api/market/intraday?symbol=005930',headers=headers).status_code==503


def test_authenticated_mocked_http_data_and_no_secrets(monkeypatch):
    c=TestClient(server.app)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','Q'*22)
    monkeypatch.setenv('KIS_APP_KEY','privateappkey')
    monkeypatch.setenv('KIS_APP_SECRET','privateappsecret')
    monkeypatch.setattr(server,'get_quote',lambda *args:{'price':123.0,'realtime_stream':False})
    monkeypatch.setattr(server,'get_flows',lambda *args:{'daily':[{'foreign':1,'institution':0,'individual':-1}],'aggregate':[],'has_data':True})
    monkeypatch.setattr(server,'get_intraday',lambda *args:{'points':[]})
    headers={'Authorization':'Bearer '+'Q'*22}
    for ep in ['/api/market/quote','/api/market/flows','/api/market/intraday']:
        res=c.get(ep+'?symbol=035720.KQ',headers=headers)
        assert res.status_code==200
        assert res.headers['cache-control']=='no-store'
        assert 'privateappsecret' not in res.text
    health=c.get('/healthz').json()
    assert health['app_version']=='5.6.7' and not health['realtime_quote_websocket']
