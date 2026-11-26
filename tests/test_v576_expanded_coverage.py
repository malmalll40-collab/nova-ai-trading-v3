from datetime import date, datetime, timedelta
import pytest
import nova_v576_expanded_coverage as m
import nova_v574_data_audit as old

D=[date(2026,7,15)+timedelta(days=i) for i in range(6)]
C=date(2026,7,17)
T=datetime(2026,7,21,7,tzinfo=m.KST)

class Fake:
    def __init__(self,conflict=False,missing=None):
        self.conflict=conflict;self.missing=missing or {};self.calls=[]
    def get(self,path,tr,p):
        code=p['FID_INPUT_ISCD'];self.calls.append(code)
        end=datetime.strptime(p['FID_INPUT_DATE_2'],'%Y%m%d').date()
        ds=[d for d in reversed(D) if d<=end and (self.conflict or d!=C)
            and d not in self.missing.get(code,set())]
        idx=path==old.INDEX_PATH
        return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),**(
            {'bstp_nmix_prpr':'2000'} if idx else {'stck_oprc':'100','stck_hgpr':'110',
            'stck_lwpr':'90','stck_clpr':'105','acml_vol':'12','acml_tr_pbmn':'1200'})}
            for d in ds[:2]]}

def run(f,**kw):
    return m.pilot(f,T,calendar=lambda a,b:D,pause=lambda _:None,**kw)

def test_expanded_fixed_universe():
    assert len(m.SYMBOLS)==22 and len(set(m.SYMBOLS))==22
    assert len(m.PRESET)==20

def test_exact_closure_dates_and_idempotence():
    raw=[date(2026,6,3),date(2026,7,17),date(2026,7,20),date(2026,7,21)]
    ds,a=m.corrected_sessions(raw,raw[0],raw[-1])
    assert a['corrections_applied']==2 and len(ds)==2
    assert m.corrected_sessions(ds,raw[0],raw[-1])[1]['corrections_applied']==0

def test_no_generic_holiday_extrapolation():
    d=date(2025,7,17)
    assert m.corrected_sessions([d],d,d)[0]==(d,)

def test_reject_bad_calendar():
    with pytest.raises(ValueError):m.corrected_sessions([D[0],D[0]],D[0],D[-1])

def test_all_streams_and_turnover_counts():
    r=run(Fake())
    assert r['status']=='READY' and r['ready_streams']==22
    assert r['calendar']['corrections_applied']==1 and r['expected_sessions']==5
    assert r['observed_rows']==110 and r['stock_rows']==100
    assert r['turnover_available']==100 and r['liquidity_fields_complete_stocks']==20
    assert r['logical_calls']==66 and not r['persistent_storage'] and not r['training_performed']

def test_official_closure_conflict_never_silently_dropped():
    r=run(Fake(conflict=True))
    assert r['status']=='PARTIAL' and r['ready_streams']==0
    assert all(s['official_closure_conflicts']==[C.isoformat()] for s in r['streams'])

def test_symbol_gap_stays_visible():
    r=run(Fake(missing={'005930':{D[3]}}))
    assert r['ready_streams']==21 and r['any_missing_count']==1
    assert r['common_missing_count']==0

def test_budget_stops_before_unchecked_streams():
    r=run(Fake(),budget=old.Budget(max_calls=4))
    assert r['logical_calls']==4 and r['reason']=='BUDGET_EXHAUSTED'
    assert r['finished_streams']<22 and r['common_missing_count'] is None

def test_guard_blocks_requests():
    f=Fake();b=m.GuardedBudget(lambda:False,max_calls=208)
    r=run(f,budget=b)
    assert not f.calls and r['reason']=='QUIET_WINDOW_ENDED' and r['logical_calls']==0

def test_quiet_window():
    s={'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'}
    assert m.quiet_window(T,s)
    assert not m.quiet_window(T.replace(hour=8,minute=46),s)
    assert not m.quiet_window(T.replace(hour=18),s)
    assert not m.quiet_window(T.replace(hour=19),s)
    assert not m.quiet_window(T.replace(hour=20,minute=9),s)
    assert m.quiet_window(T.replace(hour=20,minute=10),s)
    assert not m.quiet_window(T.replace(hour=7,minute=46),s)
    assert not m.quiet_window(T,{**s,'calendar_verified':False})
    assert not m.quiet_window(T,{**s,'status':'OPEN'})
    assert not m.quiet_window(T.replace(hour=12),{**s,'open_kst':None,'close_kst':None})

def test_startup_mode_explicit(monkeypatch):
    import ops_diagnostics as ops
    monkeypatch.setenv('NOVA_OPS_DIAGNOSTICS_ON_START','576')
    s=ops.configured()
    assert s['startup_data_probe_enabled'] and not s['startup_full_enabled'] and not s['orders_enabled']

def test_server_skips_open_market(monkeypatch):
    import server, ops_diagnostics as ops
    logs=[]
    monkeypatch.setattr(server,'krx_session',lambda now:{'calendar_verified':True,'status':'OPEN'})
    monkeypatch.setattr(ops,'emit',lambda event,payload:logs.append((event,payload)))
    monkeypatch.setattr(server,'_kis_creds',lambda:pytest.fail('no provider call'))
    assert server._start_v574_data_probe('576')=='skipped'
    assert logs[-1][0]=='v576_data_end'


def test_grouped_audit_reports_five_groups_and_pauses():
    pauses=[];f=Fake()
    r=m.pilot(f,T,calendar=lambda a,b:D,pause=pauses.append,grouped=True)
    assert r['status']=='READY' and r['observed_rows']==110
    assert [g['requested_streams'] for g in r['groups']]==[2,5,5,5,5]
    assert [g['logical_calls'] for g in r['groups']]==[6,15,15,15,15]
    assert all(g['status']=='READY' for g in r['groups'])
    assert pauses.count(5)==4 and sum(g['logical_calls'] for g in r['groups'])==r['logical_calls']

def test_grouped_audit_stops_before_remaining_groups():
    f=Fake();r=run(f,grouped=True,budget=old.Budget(max_calls=6))
    assert len(f.calls)==6 and r['reason']=='BUDGET_EXHAUSTED'
    assert r['groups'][0]['status']=='READY'
    assert r['groups'][1]['status']=='PARTIAL'
    assert all(g['status']=='NOT_STARTED' for g in r['groups'][2:])

def test_group_cap_never_extends_global_budget():
    b=old.Budget(max_calls=100);g=m.GroupBudget(b,('^KS11',))
    for _ in range(old.MAX_INDEX_PAGES):g.take()
    with pytest.raises(old.ProbeError,match='GROUP_BUDGET_EXHAUSTED'):g.take()
    assert b.calls==old.MAX_INDEX_PAGES
