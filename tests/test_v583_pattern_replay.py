from datetime import date,timedelta
from copy import deepcopy
import pytest
from nova_v583_pattern_replay import replay,_outcome

def fixture(n=240,symbols=8,step=.25):
    dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(n)]
    h={}
    for j in range(symbols):
        rows=[]
        for i,d in enumerate(dates):
            p=100+j+i*step
            rows.append({'date':d,'completed':True,'price_open':p,'price_high':p+1,
                'price_low':p-1,'price_close':p+.2,'risk_veto':False,
                'baseline':'BUY','five_core':'HOLD'})
        h[str(j)]={'source':'kis','session_verified':True,'rows':rows}
    return dates,h

def test_multi_horizon_time_split_and_cost():
    d,h=fixture()
    r=replay(h,expected_dates=d)
    assert r['status']=='DESCRIPTIVE_ONLY' and r['eligible_symbols']==8
    assert r['split_date']==d[168]
    for k in ('5','10','20'):
        early=r['results'][k]['earlier'];late=r['results'][k]['later']
        assert early['end']<late['start']
        assert early['all_buy']['samples']>=late['all_buy']['samples']
        assert early['all_buy']['mean_net_pct']>early['all_buy']['stress_mean_pct']
        assert sum(v['samples'] for v in early['by_count'].values())==early['all_buy']['samples']
        assert early['all_buy']['samples']==early['pattern_candidate']['samples']+early['other_buy']['samples']

def test_exact_nonoverlap_and_embargo():
    d,h=fixture()
    r=replay(h,expected_dates=d)
    for k in (5,10,20):
        for phase,start,end in [('earlier',19,168),('later',168,240)]:
            expected=len(range(start,end-k,k+1))*8
            assert r['results'][str(k)][phase]['all_buy']['samples']==expected

def test_independent_signal_families():
    d,h=fixture()
    r=replay(h,expected_dates=d,family='five_core')
    assert r['results']['5']['earlier']['all_buy']['samples']==0

def test_bad_source_calendar_and_ohlc_excluded():
    d,h=fixture(symbols=4)
    h['0']['source']='yahoo'
    h['1']['rows'].pop(3)
    h['2']['rows'][3]['price_low']=999
    h['3']['session_verified']=False
    r=replay(h,expected_dates=d)
    assert r['status']=='UNAVAILABLE' and r['eligible_symbols']==0 and len(r['excluded'])==4

def test_small_cohort_suppressed():
    d,h=fixture(symbols=1)
    r=replay(h,expected_dates=d)
    assert r['results']['5']['earlier']['all_buy']['mean_net_pct'] is None
    assert not r['results']['5']['earlier']['all_buy']['sufficient']

def test_adverse_is_entry_to_low_not_terminal_loss():
    d,h=fixture()
    rows=h['0']['rows']
    entry=rows[20]['price_open']
    rows[21]['price_low']=entry*.9
    r=_outcome(rows,19,5,15)
    assert r['adverse']==pytest.approx(-10)
    assert r['net']>0 and r['stress']<r['net']

def test_later_prices_cannot_change_earlier_report():
    d,h=fixture()
    r=replay(h,expected_dates=d)
    other=deepcopy(h)
    for x in other.values():
        for row in x['rows'][168:]:
            for k in ('price_open','price_high','price_low','price_close'):row[k]*=2
    s=replay(other,expected_dates=d)
    assert all(r['results'][k]['earlier']==s['results'][k]['earlier'] for k in ('5','10','20'))
    assert not r['independent_future_verified']

def test_invalid_panel_rejected():
    d,h=fixture()
    assert replay(h,expected_dates=list(reversed(d)))['status']=='UNAVAILABLE'
    assert replay(h,expected_dates=d,cost_bps=-1)['status']=='UNAVAILABLE'
