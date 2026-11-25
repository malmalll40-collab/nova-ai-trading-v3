import json
import sys
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
import ops_diagnostics as d
import server
from fastapi.testclient import TestClient


@pytest.mark.parametrize('failure,expected,code', [(None,'READ_OK',None),('connect','CONNECT_FAILED','08006'),('table_lookup','READ_FAILED','42501'),('count','READ_FAILED','57014')])
def test_postgres_probe_uses_standard_connection_and_safe_stages(monkeypatch,caplog,failure,expected,code):
    url='postgresql://secret:secret@private.example/db'
    monkeypatch.setenv('DATABASE_URL',url)
    commands=[]
    class OperationalError(Exception):
        sqlstate=code
    def fail():raise OperationalError(url)
    class Cursor:
        def execute(self,sql):
            commands.append(sql)
            if failure=='table_lookup' and 'to_regclass' in sql:fail()
            if failure=='count' and 'COUNT' in sql:fail()
        def fetchone(self):return (7,) if 'COUNT' in commands[-1] else (True,)
    @contextmanager
    def cursor():yield Cursor()
    @contextmanager
    def connect(*args,**kwargs):
        assert args==(url,) and kwargs=={'connect_timeout':5}
        if failure=='connect':fail()
        yield SimpleNamespace(cursor=cursor)
    monkeypatch.setitem(sys.modules,'psycopg',SimpleNamespace(connect=connect))
    result=d.database_probe()
    assert result['status']==expected
    if failure:
        assert result['failure_stage']==failure and result['sqlstate']==code
        assert result['error_type']=='OperationalError'
    else:assert result['observations']==7 and result['observation_table_exists'] is True
    if failure=='count':assert result['observation_table_exists'] is True
    if failure!='connect':assert commands[:3]==['SET TRANSACTION READ ONLY','SET LOCAL statement_timeout = 5000','SELECT 1']
    assert all(x.startswith(('SET ','SELECT ')) for x in commands)
    with caplog.at_level('INFO',logger='uvicorn.error'):d.emit('connections',result)
    assert 'private.example' not in caplog.text and 'secret' not in caplog.text


def test_unrecognized_exception_not_logged():
    class PrivateSecret(Exception):sqlstate='secret value'
    assert d.db_error(PrivateSecret('password'),'count')=={'status':'READ_FAILED','failure_stage':'count','error_type':'OTHER','sqlstate':None}


def test_research_progress_and_aggregate(monkeypatch,caplog):
    import nova_v542_aligned
    symbols=['005930.KS','000660.KS']
    monkeypatch.setattr(server,'BATCH_RESEARCH_STATE',{'job_id':'private-job','status':'running','rows':[],'symbols':symbols})
    def one(s,*args):
        if s==symbols[1]:raise RuntimeError('private-password')
        return {'status':'READY','source':'kis'}
    monkeypatch.setattr(server,'_v5_one',one)
    monkeypatch.setattr(nova_v542_aligned,'aligned_summary',lambda rows:{'status':'READY'})
    with caplog.at_level('INFO',logger='uvicorn.error'):server._v5_batch_worker('private-job',symbols,'kis',15)
    events=[json.loads(r.message.split('NOVA_OPS ',1)[1]) for r in caplog.records if 'NOVA_OPS ' in r.message]
    progress=[e for e in events if e['event']=='research_progress']
    assert [e['phase'] for e in progress]==['batch_start','symbol_start','symbol_done','symbol_start','symbol_done','aggregate_start','aggregate_done']
    assert [e['finished'] for e in progress]==[0,0,1,1,2,2,2]
    assert progress[4]['symbol_status']=='UNAVAILABLE' and progress[-1]['ready']==1
    assert events[-1]['event']=='research_batch' and server.BATCH_RESEARCH_STATE['status']=='complete'
    assert 'private-password' not in caplog.text and 'private-job' not in caplog.text


def test_authenticated_status_progress_only_matching_job(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','local-test-token-not-secret')
    monkeypatch.setattr(server,'OPS_STATE',{'status':'running','job_id':'a'})
    monkeypatch.setattr(server,'BATCH_RESEARCH_STATE',{'job_id':'a','progress':{'finished':3,'requested':20}})
    c=TestClient(server.app);h={'Authorization':'Bearer local-test-token-not-secret'}
    assert c.get('/api/ops/diagnostics/status').status_code==401
    data=c.get('/api/ops/diagnostics/status',headers=h).json();assert data['progress']['finished']==3 and 'job_id' not in data
    server.BATCH_RESEARCH_STATE['job_id']='b'
    assert 'progress' not in c.get('/api/ops/diagnostics/status',headers=h).json()
