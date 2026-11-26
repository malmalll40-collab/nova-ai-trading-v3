"""Offline parity and isolation for lean manual scan profiles."""
from copy import deepcopy
from datetime import datetime
import time
import pandas as pd
import pytest
import server
import market_scanner as scanner
import nova_v584_pattern_scan as patterns
import nova_v588_scan_projection as projection
from data_sources import demo_data

def fixture(monkeypatch, gate='PASS', stale=False):
    market=demo_data('스윙')
    market.symbol='005930.KS'; market.synthetic=False
    market.daily_integrity={'signal_hold':stale}
    market.bars.index=pd.bdate_range(end='2026-11-25',periods=len(market.bars))
    monkeypatch.setattr(server,'_kis_creds',lambda:('fake','fake'))
    monkeypatch.setattr(server,'kis_data',lambda *a:deepcopy(market))
    monkeypatch.setattr(server,'assess_scan_sessions',lambda *a:{'stale':False,'reason':'fixture'})
    monkeypatch.setattr(server,'_get_financial_state',lambda *a:(gate,'fixture',2025))
    monkeypatch.setattr(server,'build_shadow_inputs',lambda *a,**k:{'benchmark':None,'sector':None,'flows':None,'diagnostics':{}})
    original=patterns.from_features
    def fixed(f,cfg,**kw):
        return original(f,cfg,**kw,now=datetime(2026,11,26,12,tzinfo=patterns.KST),
            calendar=lambda a,b:[d.date() for d in pd.bdate_range(a,b)])
    monkeypatch.setattr(patterns,'from_features',fixed)
    monkeypatch.setattr(projection,'from_features',fixed)
    return market

def test_full_projection_matches_original_and_reports_cpu_time(monkeypatch):
    fixture(monkeypatch)
    args=('005930.KS','스윙','kis',15)
    t=time.perf_counter()
    old=server._payload(*args,asof_date='20261125')
    a=time.perf_counter()-t
    t=time.perf_counter()
    new=server._payload(*args,asof_date='20261125',scan_profile='full')
    b=time.perf_counter()-t
    assert all(old[k]==v for k,v in new.items()),[k for k,v in new.items() if old[k]!=v]
    snapshot={'symbol':'005930.KS','name':'fixture','market':'kospi','close':100,'change_pct':1,'turnover_krw':1000000,'screen_score':1}
    assert scanner.evaluate_candidate(snapshot,old,'20261125')==scanner.evaluate_candidate(snapshot,new,'20261125')
    assert scanner.interest_candidate(snapshot,old,'20261125','fixture')==scanner.interest_candidate(snapshot,new,'20261125','fixture')
    assert patterns.candidate(snapshot,old,'20261125','20261125')==patterns.candidate(snapshot,new,'20261125','20261125')
    print(f'V588_OFFLINE_TIMING legacy={a:.4f}s full={b:.4f}s (one synthetic symbol, no provider calls)')

def test_pattern_profile_uses_same_pattern_without_other_calculations(monkeypatch):
    fixture(monkeypatch)
    args=('005930.KS','스윙','kis',15)
    full=server._payload(*args,asof_date='20261125',scan_profile='full')
    def forbidden(*a,**k):raise AssertionError('unrelated computation')
    for name in ('run_backtest','quality_comparison','signal_performance','quality_gate','decide','resolve_final_signal','summarize_higher_trends','observe_high_precision','compare_research_engines','build_candle_research','reprice_from_evidence'):
        monkeypatch.setattr(server,name,forbidden)
    t=time.perf_counter()
    lean=server._payload(*args,asof_date='20261125',scan_profile='pattern')
    print(f'V588_OFFLINE_TIMING pattern={time.perf_counter()-t:.4f}s (synthetic symbol)')
    assert all(full[k]==v for k,v in lean.items())
    assert 'quality_gate' not in lean and 'signal_performance' not in lean

def test_full_projection_skips_only_unused_computations(monkeypatch):
    fixture(monkeypatch)
    def forbidden(*a,**k):raise AssertionError('unused computation')
    for name in ('run_backtest','quality_comparison','summarize_higher_trends','observe_high_precision','compare_research_engines','build_candle_research','reprice_from_evidence','build_shadow_inputs'):
        monkeypatch.setattr(server,name,forbidden)
    r=server._payload('005930.KS','스윙','kis',15,asof_date='20261125',scan_profile='full')
    assert 'quality_gate' in r and 'signal_performance' in r and 'final_actionable_signal' in r

def test_fail_closed_gates_unchanged(monkeypatch):
    fixture(monkeypatch,gate='UNKNOWN',stale=True)
    for profile in ('full','pattern'):
        r=server._payload('005930.KS','스윙','kis',15,asof_date='20261125',scan_profile=profile)
        assert r['is_stale'] is True and r['financial_gate']=='UNKNOWN'
        assert r['signal_pattern_research']['status']=='UNAVAILABLE'
        if profile=='full':assert r['final_actionable_signal'] is None
    with pytest.raises(ValueError):server._payload('005930.KS','스윙','kis',15,scan_profile='pattern')

def test_pattern_scan_never_runs_other_rankers(monkeypatch):
    rows=[{'symbol':'005930.KS','name':'fixture','market':'kospi','selection_lane':'유동성'}]
    class Client:
        def candidate_dates(self,now):return ['20261125']
        def fetch_day(self,date,market):return rows
    monkeypatch.setattr(scanner,'screen',lambda x:(rows,{}))
    monkeypatch.setattr(scanner,'select_deep_candidates',lambda x,d:rows)
    def forbidden(*a,**k):raise AssertionError('other ranker called')
    monkeypatch.setattr(scanner,'evaluate_candidate',forbidden)
    monkeypatch.setattr(scanner,'interest_candidate',forbidden)
    result=scanner.run_market_scan(market='kospi',api_key='',client=Client(),depth=24,
        analyze=forbidden,analyze_asof=lambda s,d:{},pattern_only=True,
        now=datetime(2026,11,26,12,tzinfo=patterns.KST))
    assert result['scan_profile']=='pattern' and result['deep_succeeded']==1 and result['deep_failed']==0
    assert result['recommended_count'] is None and result['interest_count'] is None
    assert result['signal_pattern_research']['evaluated']==1

def test_pattern_worker_does_not_persist_evidence(monkeypatch):
    monkeypatch.setattr(server,'SCAN_STATE',{'job_id':'fixture','status':'running'})
    seen=[]
    def fake_scan(**kwargs):
        seen.append(kwargs['pattern_only'])
        return {'deep_target':24,'deep_failed':0}
    def forbidden(*a,**k):raise AssertionError('evidence persistence')
    monkeypatch.setattr(server,'run_market_scan',fake_scan)
    monkeypatch.setattr(server,'evidence_backend',forbidden)
    monkeypatch.setattr(server,'capture_market_context',forbidden)
    monkeypatch.setattr(server,'store_auto_scan',forbidden)
    server._scan_worker('kospi','fixture','krx','fake',24,'pattern')
    assert seen==[True] and server.SCAN_STATE['status']=='complete'
    assert server.SCAN_STATE['result']['elapsed_seconds']>=0
