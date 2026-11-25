from datetime import date, datetime, timedelta
import pytest
import nova_v575_calendar_coverage as m
import nova_v574_data_audit as old

D=[date(2024,1,2)+timedelta(days=i) for i in range(16)]

class Fake:
    def __init__(self,gaps=None):self.gaps=gaps or {};self.calls=[]
    def get(self,path,tr,p):
        code=p['FID_INPUT_ISCD'];self.calls.append(code)
        end=datetime.strptime(p['FID_INPUT_DATE_2'],'%Y%m%d').date()
        ds=[d for d in reversed(D) if d<=end and d not in self.gaps.get(code,set())]
        idx=path==old.INDEX_PATH
        return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),**(
            {'bstp_nmix_prpr':'2000'} if idx else {'stck_oprc':'100','stck_hgpr':'110',
            'stck_lwpr':'90','stck_clpr':'105','acml_vol':'12','acml_tr_pbmn':'1200'})}
            for d in ds[:1 if idx else 2]]}

def run(f,**kw):
    return m.pilot(f,datetime(2024,1,18,8,tzinfo=m.KST),calendar=lambda a,b:D,pause=lambda _:None,**kw)

def test_index_uses_extended_limit():
    r=run(Fake())
    assert r['status']=='READY' and r['ready_streams']==6
    assert r['streams'][0]['pages']==16 and r['streams'][1]['pages']==16
    assert r['logical_calls']==64 and r['reconciliation']['any_missing_count']==0

def test_stock_limit_not_relaxed():
    with pytest.raises(ValueError):
        old.collect(Fake(),'005930.KS',D[0],D[-1],D,old.Budget(),max_pages=20)

def test_shared_gap_is_not_holiday():
    f=Fake({s:{D[7]} for s in ('0001','1001',*(s[:6] for s in old.PILOT_SYMBOLS))})
    r=run(f);a=r['reconciliation']
    assert r['status']=='PARTIAL' and r['ready_streams']==0
    assert a['common_missing_dates']==[D[7].isoformat()] and a['all_windows_reached']
    assert a['calendar_corrections_applied']==0 and not a['official_closure_verified']
    assert a['gap_details'][0]['classification']=='COMMON_GAP_UNVERIFIED'

def test_symbol_specific_gap_distinguished():
    r=run(Fake({'005930':{D[7]}}));a=r['reconciliation']
    assert r['ready_streams']==5 and a['common_missing_count']==0
    d=a['gap_details'][0]
    assert d['missing']==['005930.KS'] and len(d['present'])==5
    assert d['classification']=='PROVIDER_OR_SYMBOL_GAP'

def test_incomplete_windows_cannot_infer_common_gap():
    r=run(Fake(),budget=old.Budget(max_calls=17))
    a=r['reconciliation']
    assert not a['all_windows_reached'] and a['common_missing_count']==0

def test_missing_dates_truncated_without_losing_count():
    days=[D[0]+timedelta(days=i) for i in range(40)]
    r=m.reconciliation(days,{m.SYMBOLS[0]:{}},{m.SYMBOLS[0]:{'reason':'PAGE_LIMIT'}})
    assert r['any_missing_count']==40 and len(r['gap_details'])==20
    assert r['gap_details_truncated'] and r['missing_dates_by_stream'][m.SYMBOLS[0]]['count']==40

def test_calendar_failure_does_not_call():
    f=Fake()
    def fail(*a):raise RuntimeError('secret')
    r=m.pilot(f,datetime(2024,1,18,8,tzinfo=m.KST),calendar=fail)
    assert r['reason']=='CALENDAR_UNAVAILABLE' and not f.calls and 'secret' not in str(r)

def test_new_mode_explicit(monkeypatch):
    import ops_diagnostics as ops
    monkeypatch.setenv('NOVA_OPS_DIAGNOSTICS_ON_START','575')
    c=ops.configured()
    assert c['startup_data_probe_enabled'] and not c['startup_full_enabled'] and not c['orders_enabled']

def test_open_market_v575_guard(monkeypatch):
    import server,ops_diagnostics as ops
    logs=[]
    monkeypatch.setattr(server,'krx_session',lambda now:{'calendar_verified':True,'status':'OPEN'})
    monkeypatch.setattr(ops,'emit',lambda event,payload:logs.append((event,payload)))
    monkeypatch.setattr(server,'_kis_creds',lambda:pytest.fail('must not request credentials'))
    assert server._start_v574_data_probe('575')=='skipped'
    assert logs[-1][0]=='v575_data_end'
