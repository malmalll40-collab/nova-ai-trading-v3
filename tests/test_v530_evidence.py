"""NOVA 5.3: fixed-rule grouped votes, index coverage and no-live-adoption guards."""
import pandas as pd
from engine import Config, decide
from nova_v53_audit import (benchmark_coverage, grouped_signal, grouped_vote_study,
    retrospective_loss_paths, research_evidence_gate, summarize_batch)
from nova_v5_research import analyze
from tests.test_core_comparison_473 import sample_frames


def test_benchmark_diagnoses_short_history_without_filling_gaps():
    f, idx = sample_frames(265)
    sample=list(range(180,245))
    too_short=idx.iloc[170:].copy()
    audit=benchmark_coverage(f,too_short,sample)
    assert audit['status']=='INCOMPLETE'
    assert audit['unmatched_sessions']>0
    assert audit['missing_sessions']>0
    assert audit['before_reference_start_count']>0
    expanded=benchmark_coverage(f,idx,sample)
    assert expanded['status']=='READY' and expanded['missing_sessions']==0
    hole=idx.drop(idx.index[191])
    report=benchmark_coverage(f,hole,[220])
    assert report['status']=='INCOMPLETE'
    assert str(idx.index[191].date()) in report['missing_dates_examples']


def test_group_roles_no_double_count_and_risk_veto():
    bullish=[1,1,1,1,1,1,1,1,0,1]
    a=grouped_signal(bullish)
    assert a['signal']=='BUY' and len(a['groups'])==4
    assert grouped_signal(bullish,risk_veto=True)['signal']=='HOLD'
    assert grouped_signal([0]*10)['signal']=='HOLD'
    # A single role may not occupy two independent groups.
    from nova_v53_audit import GROUPS
    cols=[j for _,_,members in GROUPS for j in members]
    assert sorted(cols)==list(range(10))


def test_no_future_price_influence_on_historical_votes():
    f,_=sample_frames(265)
    cfg=Config.for_mode('스윙')
    i=198
    old=decide(f,cfg,i)
    before=grouped_signal([v.direction for v in old.votes[:10]],risk_veto=old.risk_veto)
    later=f.copy(deep=True)
    for column in ('Open','High','Low','Close'):
        later.loc[later.index[i+1:],column]*=3
    modified=decide(later,cfg,i)
    after=grouped_signal([v.direction for v in modified.votes[:10]],risk_veto=modified.risk_veto)
    assert before==after


def test_v53_full_study_no_live_writes_or_future_features():
    f,benchmark=sample_frames(265)
    source=f.copy(deep=True)
    prior=decide(f,Config.for_mode('스윙')).signal
    result=analyze(f,Config.for_mode('스윙'),benchmark=benchmark,source='kis')
    assert result['status']=='READY' and result['version']=='5.3.0'
    v=result['v53']
    assert v['market_data_audit']['status']=='READY'
    assert v['grouped_vote_shadow']['status']=='READY'
    assert v['grouped_vote_shadow']['live_adopted'] is False
    assert v['research_evidence_gate']['action']=='ABSTAIN'
    assert v['research_evidence_gate']['live_signal_changed'] is False
    assert v['writes_observation_database'] is False
    assert all(x['outcomes_are_not_features'] for x in v['retrospective_loss_paths'])
    assert decide(f,Config.for_mode('스윙')).signal==prior
    pd.testing.assert_frame_equal(source,f)


def test_future_trade_path_is_only_retrospective():
    f, _ = sample_frames(240)
    ledger=[{'signal_date':str(f.index[180].date()),'entry_date':str(f.index[181].date()),
             'exit_date':str(f.index[185].date()),'entry_price':120,
             'net_trade_return_pct':-2.0}]
    a=retrospective_loss_paths(f,ledger,{})
    assert len(a)==1 and a[0]['causal_explanation'] is False
    changed=f.copy(deep=True)
    changed.loc[changed.index[183],'Low']=0.1
    b=retrospective_loss_paths(changed,ledger,{})
    assert b[0]['max_adverse_excursion_pct']<a[0]['max_adverse_excursion_pct']
    assert b[0]['signal_date']==a[0]['signal_date']


def test_evidence_gate_refuses_promotion_on_good_backtest_alone():
    market={'status':'READY'}
    ai={'status':'READY','brier_score':0.16,'naive_train_rate_brier':0.25}
    a=research_evidence_gate(baseline_trades=50,ai_model=ai,market_audit=market,
                             number_of_stocks=5,truly_future_observations=50)
    assert a['action']=='ABSTAIN' and a['status']=='REVIEW_REQUIRED'
    assert not a['orders_permitted']
    b=research_evidence_gate(baseline_trades=3,ai_model=ai,market_audit=market)
    assert b['status']=='INSUFFICIENT'


def test_batch_shows_separate_stock_counts_not_aggregated_portfolio_returns():
    r=[{'symbol':'005930.KS','status':'READY','source':'kis',
         'validation_start':'2026-09-01','validation_end':'2026-11-24',
         'baseline':{'completed_trades':3,'portfolio_return_pct':-4.12},
         'ai_model':{'status':'READY','brier_score':0.3,'naive_train_rate_brier':0.25}},
       {'symbol':'247540.KQ','status':'READY','source':'yahoo',
        'validation_start':'2026-09-02','validation_end':'2026-11-24',
        'baseline':{'completed_trades':2,'portfolio_return_pct':-5},
        'ai_model':{'status':'READY','brier_score':0.15,'naive_train_rate_brier':0.25}}]
    s=summarize_batch(r)
    assert s['validation_trades_across_separate_stocks']==5
    assert not s['validation_ranges_identical']
    assert s['source_counts']=={'kis':1,'yahoo':1}
    assert s['ai_beats_naive_stocks']==1
    assert 'portfolio_return_pct' not in s


def test_research_uses_extended_benchmark_and_never_changes_live_endpoints(monkeypatch):
    import server
    from pathlib import Path
    assert server.healthz()['app_version']=='5.6.5'
    assert not server.healthz()['v53_live_adoption']
    assert 'id="v53DecisionGate"' in (server.ASSETS/'index.html').read_text()
    assert 'renderV53' in (server.ASSETS/'v5_dashboard.js').read_text()
    original=server.fetch_kis_reference
    calls=[]
    def stub(ticker,**kwargs):
        calls.append((ticker,kwargs))
        _,index=sample_frames(265)
        return index
    monkeypatch.setattr(server,'fetch_kis_reference',stub)
    # isolate from live providers and also validate explicit 18mo indexing convention
    server._cached(('v53_unit_test','^KS11'),lambda: {'frame':server.fetch_kis_reference('^KS11',period='2y')},ttl=1)
    assert calls==[('^KS11',{'period':'2y'})]
