"""Offline mock tests; Naver production endpoint availability is not asserted."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
import naver_investor as nv
import server

KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026, 11, 12, 13, 20, tzinfo=KST)


def entry(day, foreign=100, institution=-30, individual=-70):
    return {'bizdate':day, 'foreignerPureBuyQuant':str(foreign),
            'organPureBuyQuant':str(institution), 'individualPureBuyQuant':str(individual)}


def test_valid_20_day_mobile_schema():
    dates=[(NOW-timedelta(days=n)).strftime('%Y%m%d') for n in range(1,28)]
    rows=[entry(d,'+1,000','-2,000','+1,000') for d in dates]
    parsed=nv.parse_naver({'dealTrendInfos':rows}, '005930.KS',now=NOW)
    assert parsed['provider']=='naver' and parsed['unit']=='shares'
    assert parsed['aggregate'][0]['complete'] and parsed['aggregate'][0]['foreign']==5000
    assert parsed['aggregate'][1]['institution']==-20000
    assert parsed['aggregate'][2]['individual']==20000
    assert parsed['source_detail']
    assert parsed['independent_source_verified'] is False
    assert parsed['daily'][0]['date'] == '20261111'


def test_current_day_and_bad_numbers_are_missing_not_zero():
    data={'dealTrendInfos':[entry('20261111','500','-200',None),entry('20261112','999','999','999'),entry('20261110','1.25','100','-100')]}
    res=nv.parse_naver(data,'005930',now=NOW)
    assert res['partial_today_excluded']
    assert res['daily'][0]['individual'] is None
    assert res['daily'][1]['foreign'] is None
    assert not res['aggregate'][0]['complete']
    assert res['aggregate'][0]['individual'] is None


def test_missing_date_and_bad_stock_code_rejected():
    with pytest.raises(nv.NaverFlowError):nv.parse_naver({'dealTrendInfos':[{'individualPureBuyQuant':'100'}]},'005930',now=NOW)
    with pytest.raises(ValueError):nv.get_naver_flows('../../x')


def test_future_only_rejected():
    with pytest.raises(nv.NaverFlowError,match='완료 거래일'):
        nv.parse_naver({'dealTrendInfos':[entry('20261113'),entry('20261112')]},'005930',now=NOW)


def test_conflicting_same_day_rejected():
    with pytest.raises(nv.NaverFlowError,match='동일 날짜'):
        nv.parse_naver({'dealTrendInfos':[entry('20261111',100),entry('20261111',300)]},'005930',now=NOW)


class Response:
    def __init__(self,status,content,headers=None):
        self.status_code=status
        self._data=content
        self.headers=headers or {'Content-Type':'application/json'}
        self.content=b'{}'
    def json(self):return self._data

class Session:
    def __init__(self,values):self.values=list(values);self.urls=[]
    def get(self,url,**kwargs):
        self.urls.append(url)
        assert kwargs.get('allow_redirects') is False
        assert kwargs.get('timeout') == (3,8)
        return self.values.pop(0)


def test_legacy_mobile_fallback_only_on_schema_error():
    ses=Session([Response(200,{'unexpected':'format'}), Response(200,{'content':[entry('20261111')]})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['daily'][0]['foreign']==100
    assert res['source_detail'].startswith('stock.naver.com')
    assert len(ses.urls)==2
    assert ses.urls[0]=='https://m.stock.naver.com/api/stock/005930/integration'
    assert ses.urls[1].startswith('https://stock.naver.com/api/domestic/detail/005930/trend?')


def test_mobile_short_response_queries_modern_once():
    rows=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,26)]
    ses=Session([Response(200,{'dealTrendInfos':[entry('20261111')]}),
                 Response(200,{'content':rows})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['provider']=='naver' and len(ses.urls)==2
    assert res['aggregate'][2]['complete'] is True
    assert 'pageSize=25' in ses.urls[1]
    assert 'stock.naver.com 종목 투자자 동향' in res['source_detail']


def test_mobile_complete_20_does_not_query_modern():
    rows=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,24)]
    ses=Session([Response(200,{'dealTrendInfos':rows})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['aggregate'][2]['complete'] and len(ses.urls)==1


def test_expansion_http_failure_keeps_five_days_without_inventing_data():
    short=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    ses=Session([Response(200,{'dealTrendInfos':short}),Response(429,{})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['aggregate'][0]['complete']
    assert res['aggregate'][1]['complete'] is False
    assert res['aggregate'][1]['foreign'] is None
    assert len(ses.urls)==2
    assert '조회 실패' in res['history_expansion_status']


def test_expansion_poorer_history_keeps_more_complete_snapshot():
    short=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    bad=[{'bizdate':(NOW-timedelta(days=i)).strftime('%Y%m%d'),
          'foreignerPureBuyQuant':'100','organPureBuyQuant':'-100'} for i in range(1,24)]
    ses=Session([Response(200,{'dealTrendInfos':short}),Response(200,{'content':bad})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['source_detail'].startswith('m.stock.naver.com')
    assert res['aggregate'][0]['complete'] and not res['aggregate'][1]['complete']


def test_expansion_older_history_does_not_displace_latest_data():
    short=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    older=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(3,28)]
    ses=Session([Response(200,{'dealTrendInfos':short}),Response(200,{'content':older})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['source_detail'].startswith('m.stock.naver.com')
    assert res['as_of']=='20261111'


def test_expansion_overlap_conflict_is_not_trusted():
    short=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    expanded=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,24)]
    expanded[0]['foreignerPureBuyQuant']='999'
    ses=Session([Response(200,{'dealTrendInfos':short}),Response(200,{'content':expanded})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['source_detail'].startswith('m.stock.naver.com')
    assert res['aggregate'][0]['complete'] and res['aggregate'][1]['foreign'] is None
    assert '불일치' in res['history_expansion_status']


def test_expansion_includes_today_but_does_not_count_it():
    history=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(0,25)]
    short=[entry((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    ses=Session([Response(200,{'dealTrendInfos':short}),Response(200,{'content':history})])
    res=nv.get_naver_flows('005930',now=NOW,session=ses)
    assert res['partial_today_excluded']
    assert res['aggregate'][2]['complete']
    assert res['daily'][0]['date']=='20261111'


@pytest.mark.parametrize('status',[401,403,429,301,503])
def test_http_denied_no_retry(status):
    ses=Session([Response(status,{})])
    with pytest.raises(nv.NaverFlowError):nv.get_naver_flows('005930',now=NOW,session=ses)
    assert len(ses.urls)==1


def test_missing_key_uses_naver_backend(monkeypatch):
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(server,'get_naver_flows',lambda code:{'symbol':code,'provider':'naver','has_data':True,'daily':[], 'aggregate':[]})
    server.CACHE.clear()
    res=TestClient(server.app).get('/api/market/flows?symbol=123456')
    assert res.status_code==200
    assert res.json()['provider']=='naver'
    assert res.headers['cache-control']=='no-store'


def test_kis_failure_uses_naver(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','testkey')
    monkeypatch.setenv('KIS_APP_SECRET','testsecret')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    def kis_fail(*args):raise RuntimeError('secret-must-not-be-in-response')
    monkeypatch.setattr(server,'get_flows',kis_fail)
    monkeypatch.setattr(server,'get_naver_flows',lambda code:{'symbol':code,'provider':'naver','has_data':True,'daily':[], 'aggregate':[]})
    server.CACHE.clear()
    res=TestClient(server.app).get('/api/market/flows?symbol=123456')
    assert res.status_code==200 and res.json()['fallback_reason']=='KIS 인증·권한 또는 응답 오류'
    assert 'testsecret' not in res.text


def test_valid_kis_remains_primary(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','testkey')
    monkeypatch.setenv('KIS_APP_SECRET','testsecret')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(server,'get_flows',lambda *args:{'source':'KIS','daily':[{'foreign':2,'institution':-1,'individual':-1}],'aggregate':[],'has_data':True})
    monkeypatch.setattr(server,'get_naver_flows',lambda code:pytest.fail('unexpected naver call'))
    server.CACHE.clear()
    r=TestClient(server.app).get('/api/market/flows?symbol=123456')
    assert r.status_code==200 and r.json()['provider']=='kis'


def test_both_failed_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','testkey')
    monkeypatch.setenv('KIS_APP_SECRET','testsecret')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(server,'get_flows',lambda *args: (_ for _ in ()).throw(RuntimeError('testsecret')))
    monkeypatch.setattr(server,'get_naver_flows',lambda code: (_ for _ in ()).throw(nv.NaverFlowError('HTTP 403 차단')))
    server.CACHE.clear()
    r=TestClient(server.app).get('/api/market/flows?symbol=123456')
    assert r.status_code==502 and 'testsecret' not in r.text
    assert 'HTTP 403' in r.text


def test_mobile_exposes_source_and_clears_stale_table():
    js=(__import__('pathlib').Path(__file__).resolve().parents[1]/'static'/'investor_flow.js').read_text()
    assert '네이버(비공식)' in js and 'res.provider' in js
    assert 'sums.replaceChildren();if(daily)daily.replaceChildren();' in js
    assert '수급 자료 미확인' in js


def test_first_endpoint_404_can_use_modern():
    ses=Session([Response(404,{}),Response(200,{'items':[entry('20261111')]})])
    assert nv.get_naver_flows('005930',now=NOW,session=ses)['daily'][0]['foreign']==100
    assert len(ses.urls)==2


def test_missing_kis_price_error_does_not_hide_successful_flow():
    js=(__import__('pathlib').Path(__file__).resolve().parents[1]/'static'/'investor_flow.js').read_text()
    assert 'if(target===\'recommendations\'&&code===selected){const el=' in js
    assert '현재가 미확인(KIS)' in js
    assert 'setStatus(`현재가 미확인' not in js
