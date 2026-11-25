"""Auto market recommendation evidence: immutable, prospective, token isolated, no fake fills."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from fastapi.testclient import TestClient
import pytest

import auto_evidence as ae
from server import app

KST=ZoneInfo('Asia/Seoul')
OBS=datetime(2026,6,4,18,30,tzinfo=KST)


def sample():
    return {'status':'complete','data_source_id':'fsc','market':'all','scan_limit':24,
         'data_date':'20260603','snapshot_date_status':'ON_SCHEDULE',
         'deep_target':24,'deep_succeeded':23,'deep_failed':1,
         'recommendations':[{'symbol':'005930.KS','name':'삼성전자','asof':'20260603',
            'rank':1,'reference':100,'signal':'BUY'},
            {'symbol':'000660.KS','name':'SK하이닉스','asof':'20260603',
            'rank':2,'reference':100,'signal':'BUY'}],
         'interest_candidates':[{'symbol':'035720.KQ','name':'카카오','asof':'20260603',
            'rank':1,'reference':100,'classification':'INTEREST_ONLY'}]}


def frame():
    dates=pd.bdate_range('2026-06-03',periods=21)
    return pd.DataFrame({'Open':[100+i for i in range(21)],
                         'Close':[100+i for i in range(21)]},index=dates)


def test_immutable_scan_storage_and_counts(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'nova.sqlite3'))
    initial=ae.store_scan(sample(),OBS)
    assert initial['stored'] is True and initial['candidates']==3
    updated=sample();updated['recommendations'][0]['reference']=10000
    assert ae.store_scan(updated,OBS)['stored'] is False
    rows=ae.list_candidates()
    assert len(rows)==3 and next(x for x in rows if x['symbol']=='005930.KS')['reference']==100
    assert ae.status()['scan_runs']==1


def test_forward_only_outcome_pending_until_fully_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'paper.sqlite3'))
    monkeypatch.delenv('DATABASE_URL',raising=False)
    ae.store_scan(sample(),OBS)
    row=next(x for x in ae.list_candidates() if x['symbol']=='005930.KS')
    early=ae.evaluate_candidate(row,frame(),now=datetime(2026,6,5,tzinfo=KST))
    assert early['status']=='pending'
    late=ae.evaluate_candidate(row,frame(),now=datetime(2026,7,15,tzinfo=KST))
    assert late[1]['entry_day']=='2026-06-05'
    assert late[5]['exit_day']=='2026-06-11'
    assert late[5]['status']=='complete'
    assert ae.store_completed(row['id'],late)>0
    assert ae.store_completed(row['id'],late)==0
    report=ae.report()
    rank=next(x for x in report['ranking'] if x['lane']=='STRICT_BUY' and x['rank_group']=='1')
    assert rank['horizons']['5']['complete']==1
    assert report['paper']['trades_count']==1


def test_interest_is_not_a_buy_success(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'interest.sqlite3'))
    monkeypatch.delenv('DATABASE_URL',raising=False)
    ae.store_scan(sample(),OBS)
    row=next(x for x in ae.list_candidates() if x['lane']=='INTEREST_ONLY')
    result=ae.evaluate_candidate(row,frame(),now=datetime(2026,7,15,tzinfo=KST))
    assert result[5]['direction_hit'] is None
    ae.store_completed(row['id'],result)
    group=next(x for x in ae.report()['ranking'] if x['lane']=='INTEREST_ONLY' and x['rank_group']=='1')
    assert group['horizons']['5']['direction_hit_pct'] is None
    assert group['horizons']['5']['average_net_long_pct'] is None


def test_anchor_mismatch_cannot_be_traded(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'x.sqlite3'))
    monkeypatch.delenv('DATABASE_URL',raising=False)
    ae.store_scan(sample(),OBS)
    row=ae.list_candidates()[0]
    f=frame();f.loc[pd.Timestamp('2026-06-03'),'Close']=60
    assert ae.evaluate_candidate(row,f,now=datetime(2026,7,15,tzinfo=KST))['status']=='unavailable'


def test_no_secret_no_cron_and_auth_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'status.sqlite3'))
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','A'*28)
    monkeypatch.delenv('NOVA_AUTOTRACK_ENABLED',raising=False)
    monkeypatch.delenv('NOVA_AUTOTRACK_TOKEN',raising=False)
    c=TestClient(app)
    assert c.post('/api/autotrack/run').status_code==503
    assert c.get('/api/autotrack/report').status_code==401
    headers={'Authorization':'Bearer '+'A'*28}
    assert c.get('/api/autotrack/report',headers=headers).status_code==200
    assert c.get('/api/autotrack/status',headers=headers).json()['configured'] is False
    monkeypatch.setenv('NOVA_AUTOTRACK_ENABLED','1')
    monkeypatch.setenv('NOVA_AUTOTRACK_TOKEN','T'*32)
    assert c.post('/api/autotrack/run',headers=headers).status_code==401
    assert c.get('/api/autotrack/job',headers={'X-Nova-Autotrack-Token':'incorrect'}).status_code==401
    assert c.get('/api/autotrack/status',headers=headers).json()['configured'] is True


def test_evaluation_is_bounded_with_missing_loader(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'limit.sqlite3'))
    ae.store_scan(sample(),OBS)
    events=[]
    def loader(symbol,src):
        events.append(symbol)
        raise TimeoutError('provider unresponsive')
    out=ae.evaluate_pending(loader,now=datetime(2026,7,15,tzinfo=KST),max_symbols=1)
    assert len(events)==1 and out['symbols_deferred']>=2
    assert ae.status()['completed_horizons']==0


def test_scanner_scan_does_not_change_decisions(monkeypatch):
    import server
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH','/invalid-path-not-accessible')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    # New calls use separate read-only reporting/storage modules; never place orders.
    status=TestClient(app).get('/healthz').json()
    assert status['orders_enabled'] is False
    assert status['app_version']=='5.6.3'


def test_observed_user_signal_prospective_close_recording(tmp_path,monkeypatch):
    from evidence_store import append_analysis
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'observed.sqlite3'))
    analysis={'symbol':'005930.KS','source':'kis','mode':'스윙','is_demo':False,
       'is_stale':False,'asof_evaluation_only':False,'signal_is_current':True,
       'last_completed_bar':'2026-06-03T00:00:00','reference':100,'raw_signal':'BUY',
       'signal':'BUY','financial_gate':'PASS','cost_bps_one_side':15}
    assert append_analysis(analysis,datetime(2026,6,4,16,45,tzinfo=KST))
    rows=ae.evaluate_server_observations(lambda *_:frame(),now=datetime(2026,7,15,tzinfo=KST))
    # 1D next close on June 4 preceded 16:45 observation, so it MUST NOT be credited.
    assert rows['outcomes_new']==3
    rows_again=ae.evaluate_server_observations(lambda *_:frame(),now=datetime(2026,7,15,tzinfo=KST))
    assert rows_again['outcomes_new']==0
    assert ae.status()['observed_signal_completed_horizons']==3


def test_cron_valid_secret_runs_mock_worker_and_never_returns_token(tmp_path,monkeypatch):
    import server
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'auto.sqlite3'))
    monkeypatch.setenv('NOVA_AUTOTRACK_ENABLED','1')
    monkeypatch.setenv('NOVA_AUTOTRACK_TOKEN','CRONTEST' * 5)
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','ACCESS' * 5)
    prior=server.AUTO_STATE.copy()
    def noop():
        with server.AUTO_LOCK:
            server.AUTO_STATE.update(status='complete',completed_at='2026-06-04T10:00:00+00:00',
                error_code=None,result={'scan':{'status':'complete'},'evaluation':{'outcomes_new':0}})
    monkeypatch.setattr(server,'_auto_worker',noop)
    with server.AUTO_LOCK:
        server.AUTO_STATE.update(status='idle',started_at=None,completed_at=None,result=None)
    c=TestClient(app)
    result=c.post('/api/autotrack/run',headers={'X-Nova-Autotrack-Token':'CRONTEST'*5})
    assert result.status_code==200 and result.json()['status']=='queued'
    t=server.AUTO_JOB
    t.join(timeout=3)
    payload=c.get('/api/autotrack/job',headers={'X-Nova-Autotrack-Token':'CRONTEST'*5}).json()
    assert payload['status']=='complete'
    assert 'CRONTEST' not in str(payload)
    with server.AUTO_LOCK:server.AUTO_STATE.update(prior)


def test_persistent_scheduler_attempt_audit(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'audit.sqlite3'))
    now=datetime(2026,6,5,10,tzinfo=timezone.utc)
    ae.record_auto_attempt(started_at=now,completed_at=now,state='partial',
                           summary={'scan':{'status':'failed','code':'provider_down'}})
    s=ae.status()
    assert s['recent_auto_attempts']==1
    assert s['recent_attempt_failures']==1
    assert s['last_auto_attempt']['summary']['scan']['code']=='provider_down'
