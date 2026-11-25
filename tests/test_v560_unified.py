from datetime import date,timedelta
from pathlib import Path
import pytest
from nova_v560_unified import choose, replay, change_value, sector_groups, ACTIVE
from tests.test_v550_research import observation,NOW
import core560_evidence as ev

def day(n):return (date(2025,1,1)+timedelta(days=n)).isoformat()
def row(n,net=2.,regime='sideways'):
    return {'day':day(n),'exit_day':day(n+5),'net_pct':net,'subregime':regime,'signals':{'core':'BUY'},'risk_veto':False}

def test_gate_and_causal_history():
    cur=row(160);history=[row(i) for i in range(0,120,5)]
    out=choose(cur,history);assert out['signal']=='BUY' and out['route']=='core' and out['group_samples']==24
    assert choose(cur,history+[row(159,-100)])==out
    assert choose({**cur,'risk_veto':True},history)['signal']=='HOLD'
    assert choose(cur,history[:2])['state']=='INSUFFICIENT'
    assert choose(cur,[row(i,-2) for i in range(0,120,5)])['signal']=='HOLD'

def test_replay_future_mutation():
    pts=[{'signals':{'core':'BUY'},'candidates':{},'subregime':'sideways','net':.02} for _ in range(170)]
    dates=[day(i) for i in range(175)]
    a=replay(pts,dates);assert any(p['signal']=='BUY' for p in a)
    for p in pts[130:]:p['net']=-.5
    b=replay(pts,dates);assert a[:136]==b[:136]

def test_value_and_group():
    p=[{'signals':{'core':'BUY'},'candidates':{'a':'HOLD'},'net':.10},{'signals':{'core':'HOLD'},'candidates':{'a':'BUY'},'net':.02},{'signals':{'core':'BUY'},'candidates':{'a':'HOLD'},'net':-.05}]
    v=change_value(p,'candidates','a');assert v['blocked_gain_pct_sum']==10 and v['blocked_loss_pct_sum']==-5 and v['delta_pct_sum']==-3
    assert sector_groups(['005930.KS','999999.KQ'])['미분류']==['999999.KQ']

def test_versioned_storage(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    now=NOW+timedelta(days=1);a=observation(now)
    assert ev.record(a,now)=='SAVED';assert ev.record(a,now)=='EXISTS'
    with ev.connection() as (db,kind):r=ev.read(db.cursor(),kind,'005930.KS')[0]
    assert r['version']=='5.6.0' and r['unified']['signal']=='HOLD' and r['unified']['probability'] is None
    assert ev.snapshot(a,now.replace(hour=17)) is None

def test_collection_auth_and_health(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.delenv('NOVA_AUTOTRACK_TOKEN',raising=False)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    c=TestClient(server.app);assert c.post('/api/research/core560/collect').status_code==503
    assert c.get('/healthz').json()['v5_ui_tabs']==6
    assert not c.get('/healthz').json()['v560_live_adoption']

def test_ui_research_moved_no_duplicate_ids():
    from html.parser import HTMLParser
    class IDs(HTMLParser):
        def __init__(self):super().__init__();self.ids=[]
        def handle_starttag(self,tag,attrs):
            if 'id' in dict(attrs):self.ids.append(dict(attrs)['id'])
    h=Path('static/index.html').read_text();p=IDs();p.feed(h);assert len(p.ids)==len(set(p.ids))
    start=h.index('id="view-research"');end=h.index('id="view-watchlist"');research=h[start:end]
    for k in ('novaV5Lab','core541CurrentPanel','candleResearchPanel','evidencePanel','historyRun','core560EvidenceRun'):assert f'id="{k}"' in research
    assert 'id="candleCanvas"' not in research and 'data-view="research"' in h

def test_worker_20_symbols_and_calendar_guard(monkeypatch):
    import server
    import nova_v560_unified as u
    import core560_evidence as evidence
    from datetime import datetime
    class Clock:
        @staticmethod
        def now(tz):return datetime(2026,11,25,18,30,tzinfo=tz)
    monkeypatch.setattr(server,'datetime',Clock)
    monkeypatch.setattr(server,'krx_session',lambda _:{'calendar_verified':True,'close_kst':'15:30','is_open':False})
    monkeypatch.setattr(evidence,'enabled',lambda:True)
    calls=[]
    monkeypatch.setattr(server,'_payload',lambda symbol,mode,source,cost:{'symbol':symbol,'mode':mode,'source':source,'cost':cost})
    def record(a):
        calls.append(a);return 'SAVED'
    monkeypatch.setattr(evidence,'record',record)
    monkeypatch.setattr(server,'CORE560_JOB',{'status':'running','results':{}})
    server._core560_worker();assert server.CORE560_JOB['status']=='complete' and len(calls)==20
    assert all(a['mode']=='스윙' and a['source']=='kis' and a['cost']==15 for a in calls)
    monkeypatch.setattr(server,'krx_session',lambda _:{'calendar_verified':False})
    calls.clear();server._core560_worker();assert server.CORE560_JOB['status']=='failed' and not calls
