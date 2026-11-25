from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
import pandas as pd
from fastapi.testclient import TestClient
import market_scanner as ms
import server
from data_sources import demo_data


def marketrow(symbol='005930.KS',name='삼성전자'):
    return {'symbol':symbol,'name':name,'market':'kospi','close':10000,'change_pct':2,
        'turnover_krw':20_000_000_000,'market_cap_krw':800_000_000_000,'volume':100_000,'screen_score':80}


def near_miss(date='2026-11-09',state='UNKNOWN'):
    return {'mode':'스윙','is_demo':False,'is_stale':False,'financial_gate':state,
      'last_completed_bar':date+'T00:00:00','reference':10000,'long_risk_reward':1.6,
      'quality_gate':{'passed_count':4,'checks':[
          {'key':'trend','passed':True},{'key':'viable','passed':True},
          {'key':'extension','passed':True},{'key':'participation','passed':True},
          {'key':'momentum','passed':False}]},'final_actionable_signal':'HOLD'}


def test_interest_is_a_research_result_never_a_buy():
    x=ms.interest_candidate(marketrow(),near_miss(),'20261109','독립 검증 부족')
    assert x['classification']=='INTEREST_ONLY' and x['not_actionable'] is True
    assert x['financial_gate']=='UNKNOWN' and x['reference']==10000
    assert 'signal' not in x


def test_interest_never_uses_future_or_missing_or_financial_fail():
    base=near_miss()
    assert ms.interest_candidate(marketrow(),base,'20261108','bad') is None
    for k,v in [('financial_gate','FAIL'),('is_demo',True),('is_stale',True)]:
        assert ms.interest_candidate(marketrow(),dict(base,**{k:v}),'20261109','bad') is None
    broken=near_miss(); broken['quality_gate']={'passed_count':4,'checks':[{'key':'trend','passed':False},{'key':'viable','passed':True}]}
    assert ms.interest_candidate(marketrow(),broken,'20261109','bad') is None


def test_48_candidates_and_interest_when_no_buy():
    class F:
        def fetch_day(self,date,market):
            assert date=='20261109'
            return [dict(marketrow(f'{i:06}'+('.KS' if market=='kospi' else '.KQ'),f'종목{i}'),
                         market=market) for i in range(18)]
    seen=[]
    def analysis(sym,asof):
        seen.append((sym,asof));return near_miss()
    data=ms.run_market_scan(market='all',api_key='irrelevant',client=F(),
        analyze=lambda _:None,analyze_asof=analysis,
        now=datetime(2026,11,10,10,0,tzinfo=ms.KST))
    assert data['recommended_count']==0 and data['deep_target']==36
    assert data['interest_count']==20 and all(x['not_actionable'] for x in data['interest_candidates'])
    assert data['rejection_codes']['FINANCIAL_UNVERIFIED']==36
    assert '최종 매수 조건 미충족 또는 시세 지연' not in data['rejection_summary']
    assert all(x[1]=='20261109' for x in seen)


def test_retired_nasdaq_routes_reject_without_market_fetch(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    c=TestClient(server.app);server.RATE.clear()
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','X'*24)
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    monkeypatch.delenv('FSC_STOCK_API_KEY',raising=False)
    assert c.get('/api/search?q=Apple&market=nasdaq').status_code==401
    h={'Authorization':'Bearer '+'X'*24}
    assert c.get('/api/search?q=Apple&market=nasdaq',headers=h).status_code==422
    assert c.get('/api/search?q=Apple&market=us',headers=h).status_code==422
    assert c.post('/api/recommendations/run',json={'market':'nasdaq','source':'auto'},headers=h).status_code==422
    assert c.post('/api/recommendations/run',json={'market':'all','source':'nasdaq_public_watchlist'},headers=h).status_code==422
    health=c.get('/healthz').json()
    assert health['nasdaq_search'] is False and health['nasdaq_watchlist_scan'] is False
    assert health['korean_market_only'] is True


def test_asof_recompute_removes_future_bars(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    market=demo_data('스윙')
    bars=market.bars.copy()
    # The only Yahoo call is mocked; this tests no future completed candles in analysis output.
    market.symbol='005930.KS';market.synthetic=False
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a,**k:None)
    monkeypatch.setattr(server,'kis_data',lambda symbol,mode,*args:market)
    monkeypatch.setattr(server,'_get_financial_state',lambda s,m:('UNKNOWN','자료 없음',None))
    idx=bars.index
    cutoff=idx[-4].strftime('%Y%m%d')
    # Historical data may be old; still calculate and retain as-of bar, never future bars.
    result=server._payload('005930.KS','스윙','kis',15,asof_date=cutoff)
    assert result['last_completed_bar'][:10].replace('-','')==cutoff
    assert len(result['chart']['dates'])<=125
    assert all(pd.Timestamp(x).strftime('%Y%m%d')<=cutoff for x in result['chart']['dates'])
    assert result['quote'] is None and result['quote_time']==''


def test_asof_rejects_absent_snapshot_date(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    market=demo_data('스윙');market.symbol='005930.KS';market.synthetic=False
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a,**k:None)
    monkeypatch.setattr(server,'kis_data',lambda symbol,mode,*args:market)
    with pytest.raises(ValueError):server._payload('005930.KS','스윙','kis',15,asof_date='20000101')


def test_ui_displays_domestic_scope_and_interest():
    html=(server.ASSETS/'index.html').read_text()
    js=(server.ASSETS/'recommendations.js').read_text()
    assert 'value="nasdaq"' not in html and 'id="searchMarket"' not in html
    assert '미국 나스닥' not in html and 'AAPL' not in html
    assert 'interest_candidates' in js and '관심 후보' in js
    assert '시장 전체 조회' in js
