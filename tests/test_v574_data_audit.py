from datetime import date, datetime, timedelta
import pytest
import nova_v574_data_audit as m

D=[date(2024,1,2)+timedelta(days=i) for i in range(6)]

def row(d,idx=False,**kw):
    v={'stck_bsop_date':d.strftime('%Y%m%d')}
    v.update({'bstp_nmix_prpr':'2000'} if idx else {
        'stck_oprc':'100','stck_hgpr':'110','stck_lwpr':'90','stck_clpr':'105',
        'acml_vol':'12','acml_tr_pbmn':'1200'})
    return {**v,**kw}

class Fake:
    def __init__(self,days=D,limit=2):
        self.days=days;self.limit=limit;self.calls=[]
    def get(self,path,tr,params):
        self.calls.append((path,tr,params.copy()))
        a=datetime.strptime(params['FID_INPUT_DATE_1'],'%Y%m%d').date()
        b=datetime.strptime(params['FID_INPUT_DATE_2'],'%Y%m%d').date()
        return {'output2':[row(d,path==m.INDEX_PATH) for d in sorted(self.days,reverse=True) if a<=d<=b][:self.limit]}

def collect(api,s='005930.KS',**kw):
    return m.collect(api,s,D[0],D[-1],D,m.Budget(),pause=lambda _:None,**kw)

def test_short_pages_continue_and_adjusted_basis():
    f=Fake();s,records=collect(f)
    assert s['status']=='READY' and s['pages']==3 and len(records)==6
    assert s['turnover_available']==6 and s['missing_sessions']==0
    assert all(x[2]['FID_ORG_ADJ_PRC']=='0' for x in f.calls)
    assert s['sha256']==collect(Fake())[0]['sha256']

def test_index_endpoint_and_no_stock_fields():
    f=Fake();s,_=collect(f,'^KQ11')
    assert s['status']=='READY' and s['turnover_available'] is None
    assert all(p==m.INDEX_PATH and tr=='FHKUP03500100' and a['FID_INPUT_ISCD']=='1001' and 'FID_ORG_ADJ_PRC' not in a for p,tr,a in f.calls)

def test_internal_and_trailing_gaps():
    s,_=collect(Fake([D[0],D[1],D[3],D[4]]))
    assert s['status']=='PARTIAL' and s['internal_missing']==1 and s['trailing_missing']==1

def test_leading_gap_not_listing_inferred():
    s,_=collect(Fake(D[2:]))
    assert s['status']=='PARTIAL' and s['leading_missing']==2

def test_invalid_ohlcv_rejected():
    f=Fake()
    f.get=lambda *a:{'output2':[row(D[-1],stck_lwpr='150')]}
    s,_=collect(f)
    assert s['status']=='UNAVAILABLE' and s['reason']=='INVALID_OHLCV'

def test_conflicting_duplicates_rejected():
    f=Fake()
    f.get=lambda *a:{'output2':[row(D[-1]),row(D[-1],stck_clpr='106')]}
    s,_=collect(f)
    assert s['reason']=='CONFLICTING_DUPLICATE' and s['status']=='UNAVAILABLE'

def test_nonprogress_outside_window_fails():
    f=Fake()
    f.get=lambda *a:{'output2':[row(D[-1])]}
    s,_=collect(f)
    assert s['reason']=='OUTSIDE_REQUEST_WINDOW' and s['status']=='UNAVAILABLE'

def test_zero_volume_and_missing_turnover_not_imputed():
    d,r=m.parse_row(row(D[0],acml_vol='0',acml_tr_pbmn=''))
    assert r['volume']==0 and r['turnover'] is None

def test_page_and_global_budget():
    s,_=collect(Fake(),max_pages=1)
    assert s['reason']=='PAGE_LIMIT' and s['status']=='PARTIAL'
    f=Fake();b=m.Budget(max_calls=1)
    s,_=m.collect(f,'005930.KS',D[0],D[-1],D,b,pause=lambda _:None)
    assert s['reason']=='BUDGET_EXHAUSTED' and b.calls==1

def test_pilot_today_excluded_and_stops_on_error():
    now=datetime(2024,1,8,10,tzinfo=m.KST)
    f=Fake();res=m.pilot(f,now,calendar=lambda a,b:D,pause=lambda _:None)
    assert res['status']=='READY' and res['ready_streams']==6 and res['logical_calls']==18
    assert res['end']=='2024-01-07' and res['point_in_time_universe_verified'] is False
    assert all(p['FID_INPUT_DATE_2']<='20240107' for _,_,p in f.calls)
    f.get=lambda *a:(_ for _ in ()).throw(RuntimeError('secret'))
    res=m.pilot(f,now,calendar=lambda a,b:D,pause=lambda _:None)
    assert res['finished_streams']==1 and 'secret' not in str(res)

def test_calendar_failure_no_provider_call():
    f=Fake()
    def fail(*a):raise RuntimeError('calendar unavailable')
    res=m.pilot(f,datetime(2024,1,8,tzinfo=m.KST),calendar=fail)
    assert res['reason']=='CALENDAR_UNAVAILABLE' and not f.calls
