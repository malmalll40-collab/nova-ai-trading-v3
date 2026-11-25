"""User-requested 24/48/60 breadth + fail-closed optional interest-only flow tests."""
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
import market_scanner as m
import server


def rows(count=100):
    return [dict(symbol=f'{i:06}.KS', name=f'종목{i}', market='kospi',close=12000.,
        volume=300000,turnover_krw=4_000_000_000+i*150_000_000,
        market_cap_krw=(100_000_000_000 if i%2 else 5_000_000_000_000),change_pct=2.,
        screen_score=float(i)) for i in range(count)]


def detail():
    return {'mode':'스윙','is_demo':False,'is_stale':False,'financial_gate':'UNKNOWN',
        'last_completed_bar':'2026-11-09T00:00:00', 'reference':10000,'long_risk_reward':1.6,
        'quality_gate':{'passed_count':4,'checks':[{'key':'trend','passed':True},
            {'key':'viable','passed':True},{'key':'extension','passed':True},
            {'key':'participation','passed':True},{'key':'momentum','passed':False}]},
        'final_actionable_signal':'HOLD'}


def test_two_lanes_have_disjoint_stocks_and_respect_depth():
    source=rows()
    for depth in (24,48,60):
        top=m.select_deep_candidates(source,depth)
        assert len(top)==depth
        assert len({x['symbol'] for x in top})==depth
        assert sum(x['selection_lane']=='유동성' for x in top)==depth//2
        assert sum(x['selection_lane']=='거래활동' for x in top)==depth//2
        assert all(x['snapshot_activity_score']>=0 for x in top)
    assert [x['symbol'] for x in m.select_deep_candidates(source,48)] != [x['symbol'] for x in source[:48]]


def test_breadth_input_is_strictly_bounded():
    for bad in (0,1,23,25,40,61,True,'48',48.0,None):
        with pytest.raises(ValueError):m.select_deep_candidates(rows(),bad)


def test_auxiliary_flow_fails_closed_when_missing_or_wrong_unit():
    assert m.summarize_auxiliary_flow(None,'20261109')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow({'unit':'krw','daily':[]},'20261109')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow({'unit':'shares','daily':[]},'20261109')['status']=='NO_VERIFIED_FLOW'


def test_auxiliary_flow_stale_future_duplicate_not_promoted():
    ok=[{'date':f'2026110{i}','foreign':i*100,'institution':i*300}
        for i in range(5,10)]
    payload={'unit':'shares','source':'test source','daily':ok}
    assert m.summarize_auxiliary_flow(payload,'20261109')['label']=='동반 순매수'
    assert m.summarize_auxiliary_flow(payload,'20261109')['used_for_buy'] is False
    assert m.summarize_auxiliary_flow(payload,'20261108')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow(dict(payload,daily=ok+ok[:1]),'20261109')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow(dict(payload,daily=ok[:3]),'20261109')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow(dict(payload,daily=[dict(ok[0],institution=None)]+ok[1:]),'20261109')['status']=='NO_VERIFIED_FLOW'
    assert m.summarize_auxiliary_flow(dict(payload,daily=[dict(ok[0],foreign='100')]+ok[1:]),'20261109')['status']=='NO_VERIFIED_FLOW'


def test_interest_holding_flow_does_not_change_strict_buy(monkeypatch):
    class Fake:
        def fetch_day(self,date,market):
            assert date=='20261109'
            return [dict(x,market=market,symbol=x['symbol'].replace('.KS','.KQ') if market=='kosdaq' else x['symbol'])
                    for x in rows(35)]
    all_details=[]
    def analyze(sym,asof):
        all_details.append((sym,asof));return detail()
    flow={'unit':'shares','source':'mock KIS','daily':[
        {'date':f'2026110{i}','foreign':-20,'institution':10} for i in range(5,10)]}
    data=m.run_market_scan(market='all',api_key='notused',client=Fake(),analyze=lambda _:None,
        analyze_asof=analyze,now=datetime(2026,11,10,10,0,tzinfo=m.KST),
        depth=48,auxiliary_flow=lambda sym:flow)
    assert data['deep_target']==48
    assert data['recommended_count']==0
    assert data['interest_count']==20
    assert data['flow_auxiliary_coverage']==20
    assert all(x['flow_auxiliary']['label']=='수급 혼조' for x in data['interest_candidates'])
    assert all(x['flow_auxiliary']['used_for_buy'] is False for x in data['interest_candidates'])
    assert len(all_details)==48 and all(x[1]=='20261109' for x in all_details)
    assert data['selection_lanes']=={'유동성':24,'거래활동':24}


def test_cached_flows_are_not_refetched(monkeypatch):
    server.CACHE.clear()
    assert server._cached_research_flow('005930.KS') is None
    # Do not use this cache for historical validation, only user-observed overlay.
    import time
    payload={'symbol':'005930','unit':'shares','daily':[]}
    server.CACHE[('v46_flow_per_investor_20261112','005930')]=(time.monotonic(),payload)
    assert server._cached_research_flow('005930.KS')==payload
    assert server._cached_research_flow('005931.KS') is None
    server.CACHE.clear()


def test_api_depth_validation_and_ui_guard(monkeypatch):
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setenv('KRX_API_KEY','Z'*22)
    for bad in (12,40,61,'48',True):
        response=c.post('/api/recommendations/run',json={'market':'all','source':'krx','depth':bad})
        assert response.status_code==422
    h=(server.ASSETS/'index.html').read_text()
    j=(server.ASSETS/'recommendations.js').read_text()
    assert 'id="recommendDepth"' in h and 'value="48" selected' in h
    assert "depth:Number($('recommendDepth')" in j
    assert 'used_for_buy' in m.summarize_auxiliary_flow(None,'20261109')


def test_strict_buy_guard_source_unchanged():
    # Stable byte hashes from the verified pre-change release. Offline/Windows safe.
    from pathlib import Path
    import hashlib
    current=Path(m.__file__).read_text().replace('KIS 완료봉과 공식 스냅샷 기준일이 일치하지 않습니다.','Yahoo 완료봉과 공식 스냅샷 기준일이 일치하지 않습니다.')
    expected={'def classify_rejection': '547f1818022f942ab4c9e061026c99510caadf3fe13bc72abc9c1b4d2156e0c4', 'def evaluate_candidate': 'dbc4196d3ee08e35ea06c200162f2de6c2af69f10e926a7eeac83ac0682cdeac'}
    for a,b in [('def classify_rejection','def evaluate_candidate'),('def evaluate_candidate','def interest_candidate')]:
        assert hashlib.sha256(current.split(a)[1].split(b)[0].encode()).hexdigest()==expected[a]
