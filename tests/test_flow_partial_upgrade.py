"""High-signal scope checks: real market source completeness remains unverified."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import naver_investor as n
import investor_flow as f

NOW=datetime(2026,11,12,13,20,tzinfo=ZoneInfo('Asia/Seoul'))


def row(day,*,ind=-70,org=-30,foreign=100):
    result={'bizdate':day, 'foreignerPureBuyQuant':str(foreign),
            'organPureBuyQuant':str(org)}
    if ind is not None:result['individualPureBuyQuant']=str(ind)
    return result


class Response:
    status_code=200
    headers={'Content-Type':'application/json'}
    content=b'{}'
    def __init__(self,content):self._content=content
    def json(self):return self._content


class FakeSession:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,url,**kw):
        assert kw['allow_redirects'] is False
        self.calls.append(url)
        return self.responses.pop(0)


def test_partial_source_independent_investor_aggregates():
    d=[row((NOW-timedelta(days=i)).strftime('%Y%m%d'),ind=None) for i in range(1,26)]
    result=n.parse_naver({'items':d},'005930',now=NOW)
    for p,agg in zip((5,10,20),result['aggregate']):
        assert agg['period']==p
        assert agg['complete'] is False
        assert agg['foreign']==100*p and agg['institution']==-30*p
        assert agg['individual'] is None
        assert agg['complete_by_investor']=={'foreign':True,'institution':True,'individual':False}
        assert agg['count_by_investor']['individual']==0


def test_verified_merge_preserves_legacy_personal_5d_but_unknown_at_10d():
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    modern=[row((NOW-timedelta(days=i)).strftime('%Y%m%d'),ind=None) for i in range(1,26)]
    ses=FakeSession([Response({'dealTrendInfos':mobile}),Response({'content':modern})])
    result=n.get_naver_flows('005930',now=NOW,session=ses)
    a=result['aggregate']
    assert len(ses.calls)==2 and len(result['daily'])==20
    assert a[0]['complete'] is True and a[0]['individual']==-70*5
    assert a[1]['complete'] is False and a[1]['foreign']==1000 and a[1]['institution']==-300
    assert a[1]['individual'] is None
    assert a[2]['foreign']==2000 and a[2]['institution']==-600
    assert a[2]['individual'] is None
    assert '개인 5/20' in result['history_coverage_note']
    assert result['independent_source_verified'] is False
    assert 'm.stock.naver.com' in result['source_detail']


def test_mismatched_overlap_prevents_merge():
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    modern=[row((NOW-timedelta(days=i)).strftime('%Y%m%d'),ind=None) for i in range(1,26)]
    modern[0]['foreignerPureBuyQuant']='9999'
    ses=FakeSession([Response({'dealTrendInfos':mobile}),Response({'content':modern})])
    result=n.get_naver_flows('005930',now=NOW,session=ses)
    assert len(result['daily'])==5
    assert result['aggregate'][1]['foreign'] is None
    assert '불일치' in result['history_expansion_status']


def test_no_personal_sum_imputation_from_foreign_and_institution():
    x=[{'stck_bsop_date':(NOW-timedelta(days=i)).strftime('%Y%m%d'),
        'frgn_ntby_qty':100, 'orgn_ntby_qty':-50,'prsn_ntby_qty':None} for i in range(1,22)]
    a=f.summarize(x,'005930',NOW)['aggregate'][2]
    assert a['foreign']==2000 and a['institution']==-1000 and a['individual'] is None


def test_ui_marks_unknown_as_unlinked_not_failure():
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/app.js').read_text()
    assert "check.state==='UNAVAILABLE'?'—'" in js
    assert '외부 데이터 미연동/미검증' in js
    assert '자료 미연동' in js
    assert 'live_orders_approved' not in js[js.index('function renderHighPrecision'):js.index('function renderQualityBacktest')]


def test_ui_partial_values_not_blanketed_by_complete_flag():
    js=(Path(__file__).resolve().parents[1]/'static'/'investor_flow.js').read_text()
    assert 'complete_by_investor' in js
    assert 'hasField(k)?qty(d[k])' in js
    assert 'history_coverage_note' in js


def test_documented_front_api_makes_one_extra_attempt_when_two_short_results():
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    modern=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    long=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,26)]
    ses=FakeSession([Response({'dealTrendInfos':mobile}), Response({'content':modern}),
                     Response({'dealTrendInfos':long})])
    r=n.get_naver_flows('005930',now=NOW,session=ses)
    assert len(ses.calls)==3 and 'front-api/stock/domestic/trend' in ses.calls[-1]
    assert r['aggregate'][2]['complete'] and r['aggregate'][2]['foreign']==2000
    assert r['independent_source_verified'] is False


def test_documented_front_api_discrepancy_does_not_overwrite_first_five():
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    modern=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    long=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,26)]
    long[0]['foreignerPureBuyQuant']='99999'
    ses=FakeSession([Response({'dealTrendInfos':mobile}), Response({'content':modern}),
                     Response({'dealTrendInfos':long})])
    r=n.get_naver_flows('005930',now=NOW,session=ses)
    assert len(ses.calls)==3 and r['aggregate'][0]['complete']
    assert r['aggregate'][2]['foreign'] is None
    assert '동일 날짜 값이 달라' in r['history_expansion_status']


def test_modern_404_uses_documented_front_route_not_blanket_failed():
    class NotFound(Response):
        status_code=404
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    long=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,26)]
    ses=FakeSession([Response({'dealTrendInfos':mobile}),NotFound({}),Response({'dealTrendInfos':long})])
    x=n.get_naver_flows('005930',now=NOW,session=ses)
    assert len(ses.calls)==3 and x['aggregate'][2]['complete']


def test_modern_429_never_triggers_extra_endpoint():
    class TooMany(Response):
        status_code=429
    mobile=[row((NOW-timedelta(days=i)).strftime('%Y%m%d')) for i in range(1,6)]
    ses=FakeSession([Response({'dealTrendInfos':mobile}),TooMany({})])
    x=n.get_naver_flows('005930',now=NOW,session=ses)
    assert len(ses.calls)==2 and x['aggregate'][1]['foreign'] is None
    assert '429' in x['history_expansion_status']


def test_kis_only_5_days_discloses_naver_not_attempted(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv('KIS_APP_KEY','dummy')
    monkeypatch.setenv('KIS_APP_SECRET','dummy')
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    now=NOW
    rows=[{'stck_bsop_date':(now-timedelta(days=i)).strftime('%Y%m%d'),
           'frgn_ntby_qty':1,'orgn_ntby_qty':2,'prsn_ntby_qty':3} for i in range(1,6)]
    monkeypatch.setattr(server,'get_flows',lambda *a:f.summarize(rows,'005930',now))
    monkeypatch.setattr(server,'get_naver_flows',lambda c: (_ for _ in ()).throw(AssertionError('not used')))
    server.CACHE.clear()
    result=TestClient(server.app).get('/api/market/flows?symbol=005930')
    assert result.status_code==200
    data=result.json()
    assert data['provider']=='kis' and data['aggregate'][1]['foreign'] is None
    assert '네이버 대체 조회를 실행하지 않음' in data['history_expansion_status']


def test_shadow_explanations_do_not_substitute_share_quantities_for_net_values():
    html=(Path(__file__).resolve().parents[1]/'static'/'index.html').read_text()
    assert '순매수 수량(주)을 순매수금액(원)으로 임의 변경하지 않습니다' in html
    assert '시장지수·업종지수의 기준일별 가격 61개' in html
    hp=(Path(__file__).resolve().parents[1]/'high_precision.py').read_text()
    assert '허용 0.5%' in hp and '최대 +1.60ATR' in hp
