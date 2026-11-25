"""v4.7 research-only, no lookahead, immutable observational proof."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import pytest

from regime_research import (classify_benchmark, capture_market_context, context_for_symbol,
                             regime_report, VERSION)
from auto_evidence import store_scan, list_candidates, list_completed, store_completed

KST=ZoneInfo('Asia/Seoul')
OBS=datetime(2026,10,30,17,37,tzinfo=KST)


def benchmark(*, today_close=None):
    dates=pd.bdate_range(end='2026-10-30',periods=91)
    values=[100.0*(1.001**i) for i in range(len(dates))]
    if today_close is not None:values[-1]=today_close
    return pd.DataFrame({'Close':values},index=dates)


def base_scan(day='20261029', symbol='005930.KS'):
    return {'status':'complete','data_source_id':'fsc','market':'all','scan_limit':24,
            'data_date':day,'deep_target':24,'deep_succeeded':23,'deep_failed':1,
            'recommendations':[{'symbol':symbol,'name':'종목','asof':day,
                                'rank':1,'reference':100.,'signal':'BUY'}],
            'interest_candidates':[]}


def test_today_partial_is_never_used():
    # Oct 30 daily candle is ignored even when this is after 16:00 KST.
    a=classify_benchmark(benchmark(today_close=1.0),OBS,'^KS11')
    b=classify_benchmark(benchmark(today_close=99999.0),OBS,'^KS11')
    assert a==b
    assert a['status']=='READY'
    assert a['asof']=='2026-10-29'
    assert a['lag_calendar_days']==1
    assert a['trend']=='UP'
    assert a['rule_version']==VERSION


def test_invalid_stale_or_insufficient_context_fails_closed():
    assert classify_benchmark(benchmark().iloc[:30],OBS,'^KS11')['status']=='UNAVAILABLE'
    assert classify_benchmark(benchmark().iloc[:-70],OBS,'^KS11')['status']=='UNAVAILABLE'
    future=benchmark().copy()
    assert classify_benchmark(future,datetime(2027,2,20,tzinfo=KST),'^KS11')['reason']=='stale_index'
    future.iloc[5,0]=0
    assert classify_benchmark(future,OBS,'^KS11')['status']=='UNAVAILABLE'
    with pytest.raises(ValueError):classify_benchmark(benchmark(),OBS,'^SP500')
    with pytest.raises(ValueError):classify_benchmark(benchmark(),datetime(2026,10,30),'^KS11')


def test_independent_benchmark_failure_and_market_selection():
    def loader(ticker):
        if ticker=='^KQ11':raise TimeoutError('provider')
        return benchmark()
    d=capture_market_context(now=OBS,loader=loader)
    assert d['^KS11']['status']=='READY'
    assert d['^KQ11']['reason']=='provider_unavailable'
    assert context_for_symbol('005930.KS',d)['status']=='READY'
    assert context_for_symbol('086520.KQ',d)['status']=='UNAVAILABLE'
    assert context_for_symbol('005930.KS',None)['reason']=='not_recorded'


def test_immutable_scan_context_not_updated_after_retry(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'r.db'))
    first=capture_market_context(now=OBS,loader=lambda t:benchmark())
    result=store_scan(base_scan(),observed_at=OBS,market_context=first)
    assert result['stored'] is True
    a=list_candidates()[0]
    assert a['market_regime']['status']=='READY'
    assert a['market_regime']['asof']<'2026-10-30'
    altered={x:dict(v,trend='DOWN') for x,v in first.items()}
    assert store_scan(base_scan(),observed_at=OBS+timedelta(hours=1),market_context=altered)['stored'] is False
    assert list_candidates()[0]['market_regime']==a['market_regime']
    # Future outcomes don't overwrite the original observation context.
    store_completed(a['id'],{5:{'status':'complete','asset_change_pct':3.,'net_long_pct':2.4,'direction_hit':True}})
    score=regime_report(list_candidates(),list_completed())
    assert score['context_recorded']==1
    assert score['regime_groups'][0]['horizon']==5
    assert score['shadow_policy_5d']['status']=='NOT_READY'


def test_no_backfilling_legacy_and_interest_not_trading():
    ctx={'status':'READY','benchmark':'^KS11','asof':'2026-10-29',
         'trend':'DOWN','volatility':'HIGH','rule_version':VERSION}
    records=[{'id':'old','observed_at':OBS.isoformat(),'lane':'STRICT_BUY','run_key':'r1'},
             {'id':'unknown','observed_at':OBS.isoformat(),'lane':'STRICT_BUY','run_key':'r2',
              'market_regime':{'status':'UNAVAILABLE'}},
             {'id':'ready','observed_at':OBS.isoformat(),'lane':'INTEREST_ONLY','run_key':'r3',
              'market_regime':ctx}]
    outcomes={('old',5):{'status':'complete','asset_change_pct':4,'net_long_pct':3},
              ('ready',5):{'status':'complete','asset_change_pct':6,'net_long_pct':5}}
    r=regime_report(records,outcomes)
    assert r['legacy_unrecorded']==1
    assert r['provider_unavailable']==1
    assert r['context_recorded']==1
    assert r['regime_groups'][0]['lane']=='INTEREST_ONLY'
    assert r['regime_groups'][0]['hit_rate_pct'] is None
    assert r['regime_groups'][0]['mean_net_long_pct'] is None
    assert not r['live_signals_changed'] and not r['automated_orders']


def test_chronological_shadow_research_is_not_labeled_predictive():
    ctx={'status':'READY','benchmark':'^KS11','asof':'2026-07-01',
         'trend':'DOWN','volatility':'HIGH','rule_version':VERSION}
    records=[];outcomes={}
    for i in range(45):
        day=(datetime(2026,8,1,tzinfo=KST)+timedelta(days=i)).date().isoformat()
        rec={'id':f'c{i}','observed_at':day+'T17:00:00+09:00', 'run_key':f'run{i}',
             'lane':'STRICT_BUY','market_regime':dict(ctx),'symbol':'005930.KS'}
        records.append(rec)
        outcomes[(rec['id'],5)]={'status':'complete','asset_change_pct':2.0,'net_long_pct':1.5}
    result=regime_report(records,outcomes)
    shadow=result['shadow_policy_5d']
    assert shadow['status']=='RESEARCH_ONLY'
    assert shadow['live_adoption_allowed'] is False
    assert shadow['later']['skipped']==shadow['later']['completed']
    assert shadow['later']['shadow_mean_net_pct']==0
    # Insufficient historical context does not create an outcome score for the new strategy.
    assert result['regime_groups'][0]['statistical_confidence']=='DESCRIPTIVE_ONLY'


def test_auth_protected_endpoint_and_no_orders(tmp_path,monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'endpoint.db'))
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','A'*30)
    c=TestClient(server.app)
    assert c.get('/api/research/market-regimes').status_code==401
    response=c.get('/api/research/market-regimes',headers={'Authorization':'Bearer '+'A'*30})
    assert response.status_code==200
    assert response.json()['status']=='insufficient_context'
    h=c.get('/healthz').json()
    assert h['app_version']=='5.6.4'
    assert h['orders_enabled'] is False
    assert h['market_regime_live_adoption'] is False


def test_worker_persists_preobserved_context(monkeypatch):
    import server
    seen=[]
    monkeypatch.setattr(server,'run_market_scan',lambda **kw:base_scan())
    monkeypatch.setattr(server,'store_auto_scan',lambda result,**kw:seen.append(kw) or {'stored':True})
    monkeypatch.setattr(server,'capture_market_context',lambda:{'^KS11':{'status':'READY'}})
    monkeypatch.setattr(server,'evidence_backend',lambda:'sqlite')
    with server.SCAN_LOCK:
        old=dict(server.SCAN_STATE)
        server.SCAN_STATE.update(job_id='test-47',status='running')
    try:
        server._scan_worker('all','test-47','krx','testkey',24)
        assert seen and 'market_context' in seen[0]
        assert server.SCAN_STATE['status']=='complete'
    finally:
        with server.SCAN_LOCK: server.SCAN_STATE.clear();server.SCAN_STATE.update(old)


def test_latest_scan_regimes_and_legacy_visibility(tmp_path,monkeypatch):
    from auto_evidence import latest_scan_regimes
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'regime_history.db'))
    store_scan(base_scan(),observed_at=OBS)
    legacy=latest_scan_regimes()
    assert len(legacy)==1 and legacy[0]['regimes_recorded'] is False
    assert legacy[0]['market_regimes']['^KS11'] is None
    assert latest_scan_regimes(5)[0]['source_day']=='20261029'


def test_v47_ui_visible_and_backwards_compatible():
    from pathlib import Path
    import server
    root=Path(server.ASSETS)
    html=(root/'index.html').read_text(encoding='utf-8')
    js=(root/'journal.js').read_text(encoding='utf-8')
    sw=(root/'sw.js').read_text(encoding='utf-8')
    assert 'NOVA | AI Trading Mobile v5.6.4' in html
    assert 'id="autoEvidenceButton"' in html
    assert 'id="regimeResearchButton"' in html
    assert 'id="intradayContext"' in html
    assert 'initRegimeResearch()' in js
    assert '/api/research/market-regimes' in js
    assert 'regime470' in sw


def test_utc_midnight_does_not_mislabel_prior_day_context():
    from regime_research import _observed_kst_day
    # UTC date is Nov 23, but first observation was Nov 24 at 00:30 KST.
    rec={'id':'r','lane':'STRICT_BUY','run_key':'s',
         'observed_at':'2026-11-23T15:30:00+00:00',
         'market_regime':{'status':'READY','benchmark':'^KS11',
                          'trend':'UP','volatility':'NORMAL','asof':'2026-11-23'}}
    assert _observed_kst_day(rec)=='2026-11-24'
    out=regime_report([rec],{('r',5):{'status':'complete','asset_change_pct':1,
                                      'net_long_pct':0.5}})
    assert out['context_recorded']==1
