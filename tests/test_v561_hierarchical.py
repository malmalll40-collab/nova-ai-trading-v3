from nova_release import APP_VERSION
from datetime import date,timedelta
from nova_v561_hierarchical import choose,replay_panel,nonoverlap_changes,bucket,disjoint,PRESET

def day(n):return (date(2025,1,1)+timedelta(days=n)).isoformat()
def row(n,symbol,net=2.,regime='sideways'):
    return {'day':day(n),'exit_day':day(n+5),'symbol':symbol,'net_pct':net,'subregime':regime,'signals':{'core':'BUY'},'risk_veto':False}

def test_pool_unlocks_sparse_local_and_counts_once():
    symbols=PRESET[:4];history=[row(i,s) for i in range(0,35,5) for s in symbols]
    cur=row(50,symbols[0]);d=choose(cur,history)
    assert d['signal']=='BUY' and d['pool_samples']==28 and d['pool_symbols']==4
    assert d['local_samples']==7 and d['research_position_pct']==10
    assert sum(d[k] for k in ('local_samples','sector_samples','regime_samples','global_remainder_samples'))==28
    assert choose(cur,history+[row(48,symbols[1],-500)])==d
    assert choose(cur,history[:5])['state']=='INSUFFICIENT_POOL'
    assert choose({**cur,'risk_veto':True},history)['state']=='RISK_OR_DATA_VETO'
    assert choose({**cur,'signals':{'core':'HOLD'}},history)['state']=='NO_BUY_CANDIDATE'
    assert choose(cur,[{**r,'net_pct':-1} for r in history])['state']=='NONPOSITIVE_EV'

def test_cross_symbol_causal_replay():
    symbols=PRESET[:4];dates=[day(i) for i in range(85)];frames=[]
    for _ in symbols:frames.append((dates,None,[{'signals':{'core':'BUY'},'candidates':{},'subregime':'sideways','net':.02} for i in range(80)]))
    a=replay_panel(frames,symbols);assert any(d['signal']=='BUY' for d in a[0]);assert all(d['probability'] is None for d in a[0][:25])
    for p in frames[1][2][55:]:p['net']=-.9
    b=replay_panel(frames,symbols);assert all(x[:61]==y[:61] for x,y in zip(a,b))

def test_nonoverlap_opportunity_and_fixed_buckets():
    dates=[day(i) for i in range(15)];p=[{'signals':{'core':'HOLD'},'candidates':{'risk_chase':'BUY'},'net':.01} for i in range(10)]
    d=nonoverlap_changes([(dates,None,p)],[PRESET[0]],['risk_chase']);assert d['risk_chase']['added_count']==2
    assert all(bucket(s) for s in PRESET) and bucket('999999.KS') is None

def test_separate_storage(tmp_path,monkeypatch):
    import core561_evidence as e
    from tests.test_v550_research import observation,NOW
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    now=NOW+timedelta(days=1);a=observation(now)
    assert e.record(a,now)=='SAVED';assert e.record(a,now)=='EXISTS'
    with e.connection() as (db,kind):r=e.read(db.cursor(),kind,'005930.KS')[0]
    assert r['version']=='5.6.1' and r['unified']['probability'] is None
    assert e.snapshot(a,now.replace(hour=17)) is None

def test_scheduler_auth(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.delenv('NOVA_AUTOTRACK_TOKEN',raising=False);monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    c=TestClient(server.app);assert c.post('/api/research/core561/collect').status_code==503
    assert c.get('/healthz').json()['app_version']==APP_VERSION and not c.get('/healthz').json()['v561_live_adoption']

def test_heldout_bucket_and_variable_research_cap():
    import pandas as pd
    from nova_v5_research import simulate_long_only
    symbols=PRESET[:4];dates=[day(i) for i in range(90)];frames=[]
    for _ in symbols:frames.append((dates,None,[{'signals':{'core':'BUY'},'candidates':{},'subregime':'sideways','net':.02} for i in range(85)]))
    a=replay_panel(frames,symbols,holdout_bucket=True)
    # Four names across two research buckets: holding out one bucket leaves only two names.
    assert all(d['probability'] is None for ds in a for d in ds)
    f=pd.DataFrame({'Open':[100]*6,'Close':[100]*5+[110]},index=pd.date_range('2025-01-01',periods=6))
    base=simulate_long_only(f,{0:'BUY'},0,6,15)
    small=simulate_long_only(f,{0:'BUY'},0,6,15,research_caps={0:.10})
    assert base['completed_trades']==small['completed_trades']==1 and 0<small['portfolio_return_pct']<base['portfolio_return_pct']
