"""v5.2: fixed pre-specified risk research never promoted to a live signal."""
import math
import pandas as pd
from fastapi.testclient import TestClient
from engine import Config, decide
from nova_v52_risk import risk_flags, study_risks, role_overlap
from nova_v5_research import analyze, simulate_long_only
from tests.test_core_comparison_473 import sample_frames


def test_diagnostic_uses_only_signal_day_and_prior_market_history():
    f,idx=sample_frames(240)
    mark={str(d.date()):float(v) for d,v in zip(idx.index,pd.to_numeric(idx['Close']))}
    i=190
    a=risk_flags(f,i,mark)
    # An impossible enormous next-day rally may alter outcomes but not the historical risk flag.
    new=f.copy(deep=True)
    for column in ('Open','Close','High','Low'):
        new.loc[new.index[i+1:],column]=new.loc[new.index[i+1:],column]*2
    b=risk_flags(new,i,mark)
    assert a==b
    altered=mark.copy()
    altered[str(f.index[i].date())]=1e9
    assert risk_flags(f,i,altered)==a  # market index on the signal day is excluded


def test_missing_benchmark_does_not_silently_clear_market_filter():
    f,_=sample_frames(240)
    r=risk_flags(f,200,None)
    assert r['market'] is None
    values={120:'BUY',130:'BUY', 160:'BUY', 195:'BUY'}
    votes={i:[0]*10 for i in values}
    audit=study_risks(f,values,votes,formation_start=110,cut=170,stop=230,
                      cost_bps=15,benchmark=None,simulator=simulate_long_only)
    market=[item for item in audit['filter_trials'] if item['key']=='market'][0]
    assert market['status']=='UNAVAILABLE' and not market['adopted']
    assert audit['modifies_live_signal'] is False


def test_role_overlap_excludes_shared_hold_days():
    votes={i:([0]*10) for i in range(100,170)}
    # Same direction for 2 sparse sessions, not enough evidence despite apparent 100% agreement.
    for i in (140,150):
        votes[i][0]=votes[i][1]=1
    report=role_overlap(votes,120)
    assert report['evaluated_pairs']==45
    assert report['candidates']==[]
    match=next(x for x in report['most_observed_pairs'] if x['roles']==[1,2])
    assert match['both_active']==2 and match['direction_agreement_pct']==100
    assert not match['redundancy_candidate']


def test_v52_audit_contains_trade_risks_and_fixed_filters_no_source_mutation():
    f,idx=sample_frames(265)
    original=f.copy(deep=True)
    cfg=Config.for_mode('스윙')
    orig_decision=decide(f,cfg).signal
    result=analyze(f,cfg,benchmark=idx,source='kis')
    assert result['version']=='5.3.0' and result['status']=='READY'
    a=result['risk_audit']
    assert a['status']=='READY' and a['version']=='5.2.0'
    assert a['research_only'] and not a['modifies_live_signal'] and not a['adopted']
    assert len(a['filter_trials'])==5
    assert a['role_overlap']['evaluated_pairs']==45
    assert a['baseline_validation_trades']==result['validation']['baseline']['completed_trades']
    assert len(a['risk_ledger'])==len(result['trade_diagnostics']['baseline'])
    assert all(t['status'] in ('READY','UNAVAILABLE') and t['adopted'] is False for t in a['filter_trials'])
    assert decide(f,cfg).signal==orig_decision
    pd.testing.assert_frame_equal(f,original)


def test_risk_audit_does_not_invent_profit_without_trades():
    f,_=sample_frames(255)
    baseline={i:'HOLD' for i in range(120,250)}
    a=study_risks(f,baseline,{i:[0]*10 for i in baseline},formation_start=120,cut=200,
                  stop=255,cost_bps=15,benchmark=None,simulator=simulate_long_only)
    assert a['baseline_validation_trades']==0
    assert a['risk_ledger']==[]
    for trial in a['filter_trials']:
        if trial['status']=='READY':
            assert trial['validation']['completed_trades']==0
            assert trial['validation']['performance_assessable'] is False


def test_health_status_and_api_research_only(monkeypatch):
    import server
    h=server.healthz()
    assert h['app_version']=='5.6.7'
    assert h['v52_risk_audit'] and not h['v52_filters_live_adopted']
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','v52-test-secret')
    client=TestClient(server.app)
    assert client.get('/api/research/v5?symbol=005930.KS').status_code==401
    assert client.get('/api/research/v5/batch/status').status_code==401
    assets=server.ASSETS
    html=(assets/'index.html').read_text()
    assert 'id="v52RiskPanel"' in html and 'id="v52RoleOverlap"' in html
    assert 'v52-risk' in (assets/'styles.css').read_text()
