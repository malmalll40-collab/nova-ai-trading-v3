import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
import ops_diagnostics as d
import server


def test_allowlisted_summary_no_secrets(caplog,monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','PRIVATE_KEY_123');monkeypatch.setenv('KIS_APP_SECRET','PRIVATE_SECRET_123')
    monkeypatch.setenv('DATABASE_URL','postgres://private_password')
    p=d.batch_summary({'symbols':['a'],'rows':[{'status':'READY','source':'kis','private':'PRIVATE_SECRET_123'}],'core542_aligned':{'status':'READY','v561_unified':{'scored':1,'brier':float('nan'),'diagnostics':{'INSUFFICIENT_POOL':3,'PRIVATE_SECRET_123':9},'portfolio':{'unified':{'return_pct':2,'raw':'PRIVATE_SECRET_123'}}}}})
    with caplog.at_level('INFO',logger='uvicorn.error'):d.emit('test',{'config':d.configured(),'research':p})
    assert p['ready']==1 and p['brier'] is None and p['diagnostics']=={'INSUFFICIENT_POOL':3}
    assert 'PRIVATE_' not in caplog.text and 'private_password' not in caplog.text


def test_chart_probe_strict_today_and_valid_ohlcv():
    b={'open':10,'high':12,'low':9,'close':11,'volume':100};today='2026-11-25'
    assert d.chart_summary({'available':True,'date':today,'bar':b},today,True)['status']=='PASS'
    assert d.chart_summary({'available':True,'date':'2026-11-24','bar':b},today,True)['status']=='NOT_AVAILABLE'
    assert d.chart_summary({'available':True,'date':today,'bar':{**b,'volume':0}},today,True)['status']=='NOT_AVAILABLE'
    assert d.chart_summary({},today,False)['status']=='OUTSIDE_WINDOW'
    assert d.chart_summary({'available':True,'rows':[{'date':today+'T15:00:00+09:00','bar':b}]},today,True,True)['status']=='PASS'


def test_database_probe_read_only(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);p=tmp_path/'test.db';monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(p))
    assert d.database_probe()['status']=='FILE_NOT_CREATED' and not p.exists()
    with sqlite3.connect(p) as c:c.execute('CREATE TABLE nova_core561_observations (id TEXT)');c.execute("INSERT INTO nova_core561_observations VALUES ('x')")
    assert d.database_probe()=={'backend':'sqlite','status':'READ_OK','observation_table_exists':True,'observations':1}


def test_auth_endpoints(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','local-test-token-no-real-secret');monkeypatch.setattr(server,'_start_ops',lambda full:'running')
    c=TestClient(server.app)
    assert c.post('/api/ops/diagnostics/run').status_code==401
    assert c.get('/api/ops/diagnostics/status').status_code==401
    h={'Authorization':'Bearer local-test-token-no-real-secret'}
    assert c.post('/api/ops/diagnostics/run',headers=h).json()=={'status':'running'}
    assert c.get('/api/ops/diagnostics/status',headers=h).status_code==200
    monkeypatch.delenv('NOVA_ACCESS_TOKEN');assert c.post('/api/ops/diagnostics/run').status_code==503


def test_worker_no_network_and_safe_logging(monkeypatch,caplog):
    monkeypatch.setattr(server,'OPS_STATE',{'status':'running'});monkeypatch.setattr(server,'BATCH_RESEARCH_STATE',{'job_id':'a','status':'running'})
    monkeypatch.setattr(d,'configured',lambda:{'kis_configured':True});monkeypatch.setattr(d,'database_probe',lambda:{'status':'READ_OK'})
    monkeypatch.setattr(server,'krx_session',lambda n:{'day':n.date().isoformat(),'calendar_verified':True,'is_open':True,'open_kst':'09:00','close_kst':'15:30'})
    monkeypatch.setattr(server,'_kis_creds',lambda:('privatekey','privatesecret'));monkeypatch.setattr(server,'get_quote',lambda *args:{'price':100})
    monkeypatch.setattr(server,'latest_kis_period',lambda *args,**kwargs:{'available':False})
    import analysis_chart_live
    monkeypatch.setattr(analysis_chart_live,'fetch',lambda *args,**kwargs:{'available':False})
    def batch(*args):server.BATCH_RESEARCH_STATE.update(status='complete',symbols=['a']*20,rows=[{'status':'READY','source':'kis'}]*20,core542_aligned={'status':'READY','v561_unified':{'scored':9}})
    monkeypatch.setattr(server,'_v5_batch_worker',batch)
    with caplog.at_level('INFO',logger='uvicorn.error'):server._ops_worker('a',True)
    assert server.OPS_STATE['status']=='complete' and server.OPS_STATE['result']['research']['scored']==9
    assert 'NOVA_OPS' in caplog.text and 'privatekey' not in caplog.text


def test_no_duplicate_batch_or_cooldown(monkeypatch):
    monkeypatch.setattr(server,'OPS_STATE',{'status':'idle'});monkeypatch.setattr(server,'OPS_LAST_START',None);monkeypatch.setattr(server,'BATCH_RESEARCH_STATE',{'status':'running'})
    assert server._start_ops()=='batch_busy'
    monkeypatch.setattr(server,'OPS_LAST_START',server.time.monotonic());assert server._start_ops()=='cooldown'
    server.OPS_STATE['status']='running';assert server._start_ops()=='running'


def test_startup_defaults_no_heavy_job(monkeypatch,caplog):
    monkeypatch.delenv('NOVA_OPS_DIAGNOSTICS_ON_START',raising=False)
    monkeypatch.setattr(server,'_start_ops',lambda *args: (_ for _ in ()).throw(AssertionError('must not run')))
    with caplog.at_level('INFO',logger='uvicorn.error'):server._ops_startup()
    assert 'startup' in caplog.text


def test_asgi_lifespan_starts_without_legacy_handler(monkeypatch, caplog):
    monkeypatch.delenv('NOVA_OPS_DIAGNOSTICS_ON_START', raising=False)
    monkeypatch.setattr(server, '_start_ops', lambda *args: (_ for _ in ()).throw(AssertionError('must not run')))
    with caplog.at_level('INFO', logger='uvicorn.error'):
        with TestClient(server.app) as client:
            assert client.get('/').status_code == 200
    assert 'NOVA_OPS' in caplog.text and '"event":"startup"' in caplog.text


def test_lifespan_optional_diagnostic_failure_does_not_block_app(monkeypatch):
    def fail():
        raise RuntimeError('private error not for logging')
    monkeypatch.setattr(server, '_ops_startup', fail)
    with TestClient(server.app) as client:
        assert client.get('/').status_code == 200


def test_lifespan_opt_in_calls_full_diagnostics_once(monkeypatch):
    calls = []
    monkeypatch.setenv('NOVA_OPS_DIAGNOSTICS_ON_START', '1')
    monkeypatch.setattr(server, '_start_ops', lambda full: calls.append(full))
    with TestClient(server.app) as client:
        assert client.get('/').status_code == 200
    assert calls == [True]
