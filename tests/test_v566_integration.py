from datetime import datetime, timezone
import sqlite3
from fastapi.testclient import TestClient
import server
import core561_evidence as ev
import prospective_collection as pc
from tests.test_v550_research import observation
from nova_v561_hierarchical import PRESET


def test_auth_database_and_restart(monkeypatch,tmp_path):
    path=tmp_path/'test.db'; token='test-scheduler-token-123456789'
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(path))
    monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-app-token')
    monkeypatch.setenv('NOVA_AUTOTRACK_TOKEN',token)
    monkeypatch.delenv('NOVA_CORE566_AUTO_ENABLED',raising=False)
    client=TestClient(server.app); url='/api/research/core566/'
    headers={'X-Nova-Autotrack-Token':token}
    assert client.post(url+'collect',headers=headers).status_code==503
    monkeypatch.setenv('NOVA_CORE566_AUTO_ENABLED','1')
    assert client.post(url+'collect').status_code==401
    assert client.get(url+'status').status_code==401
    now=datetime(2026,11,25,9,15,tzinfo=timezone.utc).astimezone(pc.KST); day=now.date().isoformat()
    syms=PRESET[:2]; calls=[]
    def fetch(s):
        calls.append(s); a=observation(now);a['symbol']=s;return a
    def cal(t):return {'day':day,'calendar_verified':True,'close_kst':'15:30','is_open':False}
    kwargs=dict(fetch=fetch,record=ev.record,existing=pc.existing_for_day,now=lambda:now,calendar=cal,progress=lambda r:None)
    status,rows=pc.run(day,syms,**kwargs)
    assert status=='complete' and set(rows.values())=={'SAVED'}
    with sqlite3.connect(path) as db: assert db.execute('SELECT COUNT(*) FROM nova_core561_observations').fetchone()[0]==2
    calls.clear();status,rows=pc.run(day,syms,**kwargs)
    assert status=='complete' and set(rows.values())=={'EXISTS'} and not calls
    assert client.get(url+'status',headers={'Authorization':'Bearer test-app-token'}).status_code==200
