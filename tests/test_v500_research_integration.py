from __future__ import annotations
import re
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from engine import Config, decide
from nova_v5_research import analyze, remove_role_vote, simulate_long_only, _fit_logit, _predict
from kis_ws_readonly import trade_frame, status as ws_status
from tests.test_core_comparison_473 import sample_frames


def test_v5_fail_closed_no_live_adoption():
    from pathlib import Path
    import server
    h=server.healthz()
    assert h['app_version']=='5.6.6'
    assert h['orders_enabled'] is False and not h['v5_ai_auto_adoption']
    assert h['v5_lab_enabled'] and h['v5_ui_tabs']==6
    html=(server.ASSETS/'index.html').read_text()
    assert 'id="v5ResearchRun"' in html and 'id="v5MoreNav"' in html
    assert 'v5_dashboard.js' in html
    assert (server.ASSETS/'v5_dashboard.js').exists()


def test_role_ablation_recomputes_reviewers_and_veto():
    f,_=sample_frames(225)
    cfg=Config.for_mode('스윙')
    dec=decide(f,cfg)
    clone=list(dec.votes)
    out=remove_role_vote(clone,0,cfg,dec.risk_veto)
    assert out in ('BUY','SELL','HOLD')
    with pytest.raises(ValueError):remove_role_vote(clone,12,cfg,False)
    assert remove_role_vote(clone,0,cfg,True)!='BUY'


def test_v5_same_future_bar_changes_outcome_not_past_signal():
    f,idx=sample_frames(245)
    c=Config.for_mode('스윙')
    first=decide(f,c,120).signal
    baseline=simulate_long_only(f,{120:'BUY'},120,127,15)
    assert baseline['completed_trades']==1
    f2=f.copy()
    # Change only the NEXT session open. Past signal at day 120 stays fixed.
    f2.iloc[121,f2.columns.get_loc('Open')]*=1.4
    changed=simulate_long_only(f2,{120:'BUY'},120,127,15)
    assert changed['mean_trade_net_pct']<baseline['mean_trade_net_pct']
    assert decide(f2,c,120).signal==first


def test_v5_cannot_masquerade_as_live_predictions():
    f,idx=sample_frames(255)
    original=f.copy(deep=True)
    cfg=Config.for_mode('스윙')
    before=decide(f,cfg).signal
    r=analyze(f,cfg,benchmark=idx,source='kis')
    assert r['status']=='READY'
    assert r['research_only'] and not r['modifies_live_signal']
    assert not r['model_promoted_to_live'] and not r['places_orders']
    assert r['validation']['baseline']['status']=='READY'
    assert len(r['role_ablations'])==10
    assert len(r['role_ablations'][0]['role'])>2
    assert r['validation']['ai_model']['status'] in ('READY','INSUFFICIENT')
    assert decide(f,cfg).signal==before
    pd.testing.assert_frame_equal(original,f)


def test_v5_missing_benchmark_not_fabricated():
    f,_=sample_frames(220)
    r=analyze(f,Config.for_mode('스윙'),benchmark=None,source='kis')
    assert r['status']=='READY'
    assert r['validation']['five_core']['status']=='UNAVAILABLE'
    assert all(x['removed_model']['status']=='READY' for x in r['role_ablations'])


def test_v5_no_signal_has_no_artificial_profit():
    f,_=sample_frames(230)
    r=simulate_long_only(f,{},110,180,15)
    assert r['completed_trades']==0
    assert r['portfolio_return_pct']==0
    assert r['portfolio_mdd_pct']==0


def test_v5_ai_fit_does_not_modify_training_data():
    X=np.tile(np.array([.05,-.01,.02,.2,1.1,.03]),(100,1))
    X[:,0]=np.linspace(-.2,.3,100)
    y=(X[:,0]>0).astype(float)
    clone=X.copy()
    m=_fit_logit(X[:70],y[:70])
    assert np.all((_predict(m,X[70:])>=0)&(_predict(m,X[70:])<=1))
    np.testing.assert_array_equal(clone,X)


def test_v5_ai_no_future_train_labels():
    f,idx=sample_frames(240)
    r=analyze(f,Config.for_mode('스윙'),benchmark=idx,source='kis')
    a=r['validation']['ai_model']
    if a['status']=='READY':
        assert a['train_last_signal_date']<a['validation_start_date']
        assert a['score_is_calibrated_probability'] is False
        assert a['validated_edge'] is False


def test_kis_ws_never_orders_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv('NOVA_KIS_WS_ENABLED',raising=False)
    monkeypatch.setenv('KIS_APP_KEY','dummy');monkeypatch.setenv('KIS_APP_SECRET','dummy')
    s=ws_status()
    assert s['configured'] and not s['enabled'] and not s['orders_enabled']
    assert not s['modifies_live_signal']
    from kis_ws_readonly import approval_key
    with pytest.raises(RuntimeError):approval_key()


def test_kis_ws_strict_krx_tick_parser():
    values=['005930','142518','87300']+['0']*9+['31','4001']
    good='0|H0STCNT0|001|'+'^'.join(values)
    res=trade_frame(good,'005930')
    assert res['price']==87300 and res['trade_volume']==31 and res['cumulative_volume']==4001
    assert res['complete_candle'] is False and res['affects_model_or_orders'] is False
    assert trade_frame(good,'000660') is None
    assert trade_frame(good.replace('H0STCNT0','H0STCNI0'),'005930') is None
    assert trade_frame('bad','005930') is None
    assert trade_frame(good.replace('87300','-1'),'005930') is None


def test_v5_research_api_auth_and_market_result(monkeypatch):
    import server
    from data_sources import MarketData
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    f,idx=sample_frames(250)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','super-secret-auth-token-for-tests')
    client=TestClient(server.app)
    assert client.get('/api/research/v5').status_code==401
    headers={'Authorization':'Bearer super-secret-auth-token-for-tests'}
    monkeypatch.setenv('KIS_APP_KEY','dummy');monkeypatch.setenv('KIS_APP_SECRET','dummy')
    def fake_kis(ticker,mode,key,secret):
        return MarketData(f[['Open','High','Low','Close','Volume']], 'TEST HIST','005930.KS',mode,
                          None,'','', 'TEST ONLY','historical','KRW')
    monkeypatch.setattr(server,'kis_data',fake_kis)
    monkeypatch.setattr(server,'completed_swing_bars',lambda x:(x,{'state':'TEST','stale':False}))
    monkeypatch.setattr(server,'fetch_kis_reference',lambda x,**kw:idx)
    monkeypatch.setattr(server,'append_analysis',lambda *args,**kw: (_ for _ in ()).throw(AssertionError('MUST NOT WRITE DB')))
    response=client.get('/api/research/v5?symbol=005930.KS&source=kis',headers=headers)
    assert response.status_code==200,response.text[:250]
    data=response.json()
    assert data['version']=='5.4.3' and data['research_only']
    assert data['status']=='READY' and not data['modifies_live_signal']
    assert len(data['role_ablations'])==10
    health=client.get('/api/realtime/kis/status',headers=headers)
    assert health.status_code==200 and not health.json()['orders_enabled']


def test_v5_regression_source_and_menu():
    import server
    from pathlib import Path
    s=(server.ASSETS/'index.html').read_text()
    assert 'id="v5MoreMenu"' in s
    assert 'data-view="finance"' in s and 'data-view="guide"' in s and 'data-view="news"' in s
    assert 'id="view-finance"' in s
    assert 'id="chartPanel"' in s
    assert 'id="coreComparisonPanel"' in s
    assert 'id="candleResearchPanel"' in s
    assert "'/static/v5_dashboard.js'" in (server.ASSETS/'sw.js').read_text()


def test_kis_ws_origin_rejection_even_without_broker(monkeypatch):
    import server
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('NOVA_KIS_WS_ENABLED',raising=False)
    client=TestClient(server.app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/realtime/kis/stream?symbol=005930.KS',
                                     headers={'origin':'https://evil-site.example'}):
            pass


def test_kis_ws_disabled_same_origin_fails_without_approval_request(monkeypatch):
    import server
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.delenv('NOVA_KIS_WS_ENABLED',raising=False)
    client=TestClient(server.app)
    with client.websocket_connect('/api/realtime/kis/stream?symbol=005930.KS',
                                  headers={'origin':'http://testserver'}) as sock:
        data=sock.receive_json()
        assert data['type']=='error' and '비활성화' in data['message']
