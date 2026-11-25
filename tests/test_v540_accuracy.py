import numpy as np
import pandas as pd
from engine import Config
from nova_v54_accuracy import analyze_accuracy, labeled_rows, walk_predictions, score_predictions, MIN_TRAIN, HORIZON
from tests.test_core_comparison_473 import sample_frames


def test_walk_forward_same_samples_and_strict_training_boundary():
    f,benchmark=sample_frames(400)
    r=analyze_accuracy(f,Config.for_mode('스윙'),source='kis',benchmark=benchmark)
    assert r['status']=='READY' and r['fold_count']>=2
    assert all(x['last_training_outcome']<x['start'] and x['train_count']>=MIN_TRAIN for x in r['folds'])
    assert sum(x['samples'] for x in r['folds'])==r['paired_samples']
    assert r['strategy_adoption']=='HOLD' and not r['modifies_live_signal'] and not r['writes_observation_database']
    assert set(r['brier'])=={'legacy_ai','shrink_ai','naive'}
    assert r['strategies']['five_core']['portfolio']['status']=='READY'
    for s in r['strategies'].values():
        o=s['opportunities'];assert o['positive_signals']+o['negative_signals']==o['buy_signals']
        assert o['positive_signals']+o['missed_positive_opportunities']==r['positive_opportunities']
        assert s['stress_portfolio']['cost_bps_per_side']==r['cost_bps']*2


def test_future_label_mutation_cannot_change_earlier_predictions():
    f,_=sample_frames(400);rows=labeled_rows(f,66,15)
    a,folds=walk_predictions(rows,171,395)
    assert a and folds
    changed={i:(x,1-y,ret) if i+HORIZON>=211 else (x,y,ret) for i,(x,y,ret) in rows.items()}
    b,_=walk_predictions(changed,171,395)
    assert all(a[i]==b[i] for i in a if i<211)
    assert all(fold['last_training_label_idx']<fold['start_idx'] for fold in folds)


def test_readonly_missing_data_and_non_kis_fail_closed():
    f,_=sample_frames(400);original=f.copy(deep=True);cfg=Config.for_mode('스윙')
    analyze_accuracy(f,cfg,source='kis');pd.testing.assert_frame_equal(f,original)
    assert analyze_accuracy(f,cfg,source='yahoo')['status']=='INSUFFICIENT'
    f.iloc[250,f.columns.get_loc('Open')]=np.nan
    assert analyze_accuracy(f,cfg,source='kis')['status']=='INSUFFICIENT'
    assert analyze_accuracy(f.iloc[:170],cfg,source='kis')['status']=='INSUFFICIENT'


def test_brier_uses_identical_outcomes():
    rows={1:(None,1,1),2:(None,0,-1)};p={1:(.9,.7,.5),2:(.1,.3,.5)}
    assert score_predictions(rows,p)=={'legacy_ai':.01,'shrink_ai':.09,'naive':.25}


def test_missing_market_not_counted_as_no_trade():
    f,_=sample_frames(400)
    r=analyze_accuracy(f,Config.for_mode('스윙'),source='kis')
    assert r['strategies']['five_core']['portfolio']['status']=='UNAVAILABLE'
    assert 'completed_trades' not in r['strategies']['five_core']['portfolio']
