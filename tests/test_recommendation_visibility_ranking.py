"""NOVA transparent ranking and visible search-state regressions."""
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
import market_scanner as m
import server


def sample(symbol, score=1.0):
    return {'symbol':symbol,'market':'kospi','name':symbol,'close':12000,
        'volume':300000,'turnover_krw':50000000000,
        'market_cap_krw':500000000000,'change_pct':1.5,'screen_score':score}


def interest_detail(passed_count,financial='UNKNOWN'):
    passed={'trend','viable','extension','momentum','participation'}
    keys=['trend','viable','extension','momentum','participation']
    checks=[{'key':k,'passed':k in passed and (i<passed_count)} for i,k in enumerate(keys)]
    return {'mode':'스윙','is_demo':False,'is_stale':False,'financial_gate':financial,
        'last_completed_bar':'2026-11-09','reference':12000,'long_risk_reward':1.6,
        'quality_gate':{'strict_signal':'HOLD','passed_count':passed_count,'checks':checks},
        'final_actionable_signal':'HOLD'}


def test_strict_rank_prioritizes_observed_validation_not_raw_popularity():
    stocks=[
        {'symbol':'000004.KS','validation':{'wilson_lower_pct':42,'mean_net_pct':2.,'samples':15},'risk_reward':2.},
        {'symbol':'000003.KS','validation':{'wilson_lower_pct':51,'mean_net_pct':0.2,'samples':20},'risk_reward':1.5},
        {'symbol':'000005.KS','validation':{'wilson_lower_pct':42,'mean_net_pct':2.,'samples':15},'risk_reward':2.},
        {'symbol':'000001.KS','validation':{'wilson_lower_pct':None,'mean_net_pct':4.,'samples':100},'risk_reward':5.},
        {'symbol':'000002.KS','validation':{'wilson_lower_pct':42,'mean_net_pct':3.,'samples':12},'risk_reward':1.5},
    ]
    assert [x['symbol'] for x in sorted(stocks,key=m.strict_candidate_rank_key)] == [
        '000003.KS','000002.KS','000004.KS','000005.KS','000001.KS']
    assert m.strict_candidate_rank_key(stocks[0])==m.strict_candidate_rank_key(dict(stocks[0]))


def test_interest_rank_prefers_financial_pass_then_technical_and_ties_are_deterministic():
    stocks=[
        {'symbol':'000008.KS','financial_gate':'UNKNOWN','passed_count':5,'technical_review':{'participation':True,'momentum':True},'risk_reward':2.},
        {'symbol':'000002.KS','financial_gate':'PASS','passed_count':3,'technical_review':{'participation':False,'momentum':True},'risk_reward':1.},
        {'symbol':'000004.KS','financial_gate':'PASS','passed_count':4,'technical_review':{'participation':True,'momentum':False},'risk_reward':1.},
        {'symbol':'000003.KS','financial_gate':'PASS','passed_count':4,'technical_review':{'participation':True,'momentum':False},'risk_reward':1.},
    ]
    assert [r['symbol'] for r in sorted(stocks,key=m.interest_candidate_rank_key)]==[
        '000003.KS','000004.KS','000002.KS','000008.KS']


def test_final_interest_top20_selected_after_all_48_checked():
    class KRX:
        def fetch_day(self,date,market):
            return [sample(f'{i:06}.KS',float(48-i)) for i in range(48)]
    called=[]
    def analyzer(symbol,asof):
        called.append(symbol)
        # Last five discoveries rank higher and must still appear in TOP 20.
        return interest_detail(5 if len(called)>43 else 3,financial='PASS' if len(called)>43 else 'UNKNOWN')
    res=m.run_market_scan(market='kospi',api_key='mock',client=KRX(),
        analyze=lambda _:None,analyze_asof=analyzer,
        now=datetime(2026,11,10,10,0,tzinfo=m.KST),depth=48)
    assert res['interest_eligible_total']==48
    assert res['interest_count']==20
    assert res['recommended_count']==0
    assert [r['rank'] for r in res['interest_candidates']]==list(range(1,21))
    assert set(r['symbol'] for r in res['interest_candidates'][:5])==set(called[-5:])
    assert all(r['classification']=='INTEREST_ONLY' and r['not_actionable'] for r in res['interest_candidates'])


def test_strict_candidates_ranked_after_scan_and_buy_guard_still_applies():
    class KRX:
        def fetch_day(self,date,market):
            return [sample(f'{i:06}.KS',float(3-i)) for i in range(3)]
    def analyzer(symbol,asof):
        i=int(symbol[:6]);lower=[42.5,61.0,50.0][i]
        return {'mode':'스윙','is_demo':False,'is_stale':False,'financial_gate':'PASS',
            'last_completed_bar':'2026-11-09','signal_is_current':True,
            'quality_gate':{'strict_signal':'BUY','passed_count':5},
            'signal_performance':{'selected_validation_positive':True,'selected_active_now':True,
                'selected_name':'검증된 과거 조건','selected_id':'xx','min_validation':15,
                'roles':[{'id':'xx','validation':{'samples':40,'hit_pct':62,'mean_net_pct':1.5,'wilson_lower_pct':lower}}]},
            'final_actionable_signal':'BUY','long_risk_reward':1.7,'reference':12000,
            'long_stop_reference':11000,'long_target_reference':13700,'provider':'test'}
    result=m.run_market_scan(market='kospi',api_key='mock',client=KRX(),
        analyze=lambda _:None,analyze_asof=analyzer,
        now=datetime(2026,11,10,10,0,tzinfo=m.KST),depth=24)
    assert result['recommended_count']==3
    assert [r['symbol'] for r in result['recommendations']]==['000001.KS','000002.KS','000000.KS']
    assert [r['rank'] for r in result['recommendations']]==[1,2,3]
    assert all(r['signal']=='BUY' and r['not_probability'] for r in result['recommendations'])


def test_api_exposes_real_progress_without_fabricating_percent(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    with server.SCAN_LOCK:
        old=dict(server.SCAN_STATE)
        server.SCAN_STATE.clear()
        server.SCAN_STATE.update({'status':'running','market':'all','source':'fsc','depth':48,
            'job_id':'test-status-rank','result':None,'error':None,
            'progress':{'phase':'deep','done':17,'total':48,'message':'심층 분석 17/48'},
            'started_monotonic':123.0})
    try:
        response=TestClient(server.app).get('/api/recommendations/status')
        assert response.status_code==200
        data=response.json()
        assert data['status']=='running'
        assert data['progress']['done']==17 and data['progress']['total']==48
        assert 'started_monotonic' not in data
        assert response.headers['cache-control']=='no-store'
    finally:
        with server.SCAN_LOCK:
            server.SCAN_STATE.clear();server.SCAN_STATE.update(old)


def test_ui_search_states_are_screenreader_announced_and_rank_visible():
    html=(server.ASSETS/'index.html').read_text()
    js=(server.ASSETS/'recommendations.js').read_text()
    css=(server.ASSETS/'styles.css').read_text()
    assert 'id="recommendScanState"' in html and 'aria-live="polite"' in html
    assert 'id="recommendProgressTrack"' in html and 'aria-valuemin="0"' in html
    assert "setSearchState('running'" in js and "setSearchState('failed'" in js
    assert "setSearchState('complete'" in js and 'progress?.done' in js
    assert 'recommend-rank-number' in js and 'r.rank||i+1' in js
    assert 'recommend-scan-state[data-state="running"]' in css
    assert '@media(prefers-reduced-motion:reduce)' in css
