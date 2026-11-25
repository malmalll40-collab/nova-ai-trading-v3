"""v4.0 role-level signal research regression tests."""
from datetime import datetime
import math
from copy import deepcopy

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data_sources import demo_data
from engine import Config, WARMUP, feature_frame, vote_row
from signal_stats import (
    MIN_TRAIN, MIN_VALIDATION, TECHNICAL_ROLES, _summary, _wilson_lower,
    signal_performance,
)
import server


@pytest.fixture(scope='module')
def swing_features():
    return feature_frame(demo_data('스윙').bars, '스윙')


def test_wilson_small_sample_not_100pct_certainty():
    assert _wilson_lower(2, 2) < 50
    assert _wilson_lower(0, 0) is None
    assert _summary([])['hit_pct'] is None
    s=_summary([2.0, -1.0, 0.0, 5.0])
    assert s['samples']==4 and s['wins']==2 and s['hit_pct']==50


@pytest.mark.parametrize('mode', ['1분','5분','스윙'])
def test_roles_match_current_closed_candle(mode):
    features=feature_frame(demo_data(mode).bars,mode)
    result=signal_performance(features,Config.for_mode(mode))
    assert len(result['roles'])==TECHNICAL_ROLES
    assert result['mode']==mode
    assert result['active_buy_roles'] == sum(x['active_now'] for x in result['roles'])
    _, votes, _, _=vote_row(features.iloc[-1],mode,Config.for_mode(mode))
    expected={f'{i+1:02d}' for i,v in enumerate(votes[:10]) if v.direction==1}
    actual={x['id'] for x in result['roles'] if x['active_now']}
    assert expected==actual
    assert all(x['name'] in [v.role for v in votes[:10]] for x in result['roles'])
    assert result['horizon_bars'] == {'1분':10,'5분':6,'스윙':5}[mode]


def test_future_test_data_does_not_change_training_or_selected(swing_features):
    # Change only the last 15% of prices. Past feature values must remain
    # unchanged because indicators are strictly causal.
    cfg=Config.for_mode('스윙')
    original=signal_performance(swing_features,cfg)
    bars=demo_data('스윙').bars.copy()
    part=bars.index[int(len(bars)*.85):]
    for field in ('Open','High','Low','Close'):
        bars.loc[part,field] = bars.loc[part,field]*1.25
    changed=signal_performance(feature_frame(bars,'스윙'),cfg)
    assert original['selected_id']==changed['selected_id']
    assert {r['id']:r['train'] for r in original['roles']} == {r['id']:r['train'] for r in changed['roles']}


def test_per_role_events_have_no_overlapping_windows():
    for mode in ('1분','5분','스윙'):
        d=feature_frame(demo_data(mode).bars,mode)
        p=signal_performance(d,Config.for_mode(mode))
        h=p['horizon_bars']
        split=max(WARMUP+h+1,min(len(d)-h-1,int(len(d)*.70)))
        max_train=math.ceil((split-WARMUP+1)/(h+1))
        max_val=math.ceil((len(d)-split)/(h+1))
        for role in p['roles']:
            assert role['train']['samples']<=max_train
            assert role['validation']['samples']<=max_val
            assert role['train']['wins']<=role['train']['samples']
            assert role['validation']['wins']<=role['validation']['samples']


def test_cost_increase_reduces_all_unchanged_role_mean_returns(swing_features):
    a=signal_performance(swing_features,Config.for_mode('스윙',10))
    b=signal_performance(swing_features,Config.for_mode('스윙',30))
    for ra,rb in zip(a['roles'],b['roles']):
        assert ra['id']==rb['id']
        assert ra['train']['samples']==rb['train']['samples']
        if ra['train']['samples']:
            assert rb['train']['mean_net_pct'] == pytest.approx(ra['train']['mean_net_pct']-0.4,abs=0.002)


def test_validation_does_not_select_best_looking_holdout(swing_features):
    stat=signal_performance(swing_features,Config.for_mode('스윙'))
    first=next((r for r in stat['roles'] if r['train_sufficient'] and r['train']['mean_net_pct']>0), None)
    assert stat['selected_id']==(first['id'] if first else None)
    assert stat['selected_validation_positive'] is False or (
        first is not None and first['validation_sufficient'] and first['validation']['mean_net_pct']>0
    )


def test_empty_and_short_data_fail_early(swing_features):
    with pytest.raises(ValueError):
        signal_performance(swing_features.iloc[:68],Config.for_mode('스윙'))


def test_api_no_orders_still_auth_and_research_does_not_change_signal(monkeypatch):
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    testclient=TestClient(server.app)
    v=testclient.get('/healthz').json()
    assert v['app_version']=='5.6.3' and v['signal_role_statistics'] is True
    assert v['calibrated_buy_probability'] is False and v['orders_enabled'] is False
    body=testclient.get('/api/analysis?source=demo&mode=스윙&cost_bps=15').json()
    assert len(body['signal_performance']['roles'])==10
    assert body['signal'] in ('BUY','SELL','HOLD') and body['final_actionable_signal'] is None
    assert body['signal_performance']['latest_completed_bar']==body['last_completed_bar']
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','x'*22)
    server.CACHE.clear()
    assert testclient.get('/api/analysis?source=demo').status_code==401


def test_frontend_safe_labeling():
    from pathlib import Path
    js=(server.ASSETS/'app.js').read_text()
    html=(server.ASSETS/'index.html').read_text()
    assert 'renderSignalPerformance(d)' in js
    assert 'label.textContent=r.name' in js  # no html injection
    assert 'id="signalPerformancePanel"' in html
    assert 'id="signalRankingPanel"' in html
    assert '확률 아님' in html
