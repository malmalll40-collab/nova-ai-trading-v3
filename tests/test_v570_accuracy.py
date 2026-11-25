from datetime import date,timedelta
import pytest
from nova_v570_accuracy import *

def row(i,s='A',net=2.,reg='sideways',market='range',guard=True):
    day=date(2025,1,1)+timedelta(days=i*7)
    return dict(day=str(day),exit_day=str(day+timedelta(days=5)),symbol=s,net_pct=net,cost_bps=15,subregime=reg,market_regime=market,signals={'core':'BUY','range_guard':'BUY' if guard else 'HOLD','rs_required':'BUY','transition_soft':'BUY'})

def pool(net=2.):return [row(i,s,net) for i in range(20) for s in 'ABCDE']
def test_gate():
    assert guarded(row(1),'core')
    assert not guarded(row(1,guard=False),'core')
    assert not guarded(row(1,market='down'),'core')
    assert not guarded(row(1,market=None),'core')
    assert not guarded({**row(1),'risk_veto':True},'core')
def test_fit_mature_and_date_cluster():
    current=row(21); hist=pool();d=choose(current,hist)
    assert d['signal']=='BUY' and d['pool_dates']==20 and d['pool_symbols']==5
    assert choose(current,hist+[row(22,net=-80)])==d
    assert choose(row(0),hist)['state']=='INSUFFICIENT_POOL'
    assert choose(current,[row(0,s) for s in map(str,range(100))])['state']=='INSUFFICIENT_POOL'
def test_strict_exit():
    h=pool();c=row(21); bad={**row(20,net=-90),'exit_day':c['day']}
    assert choose(c,h+[bad])==choose(c,h)
def test_loss_and_cost_veto():
    assert choose(row(21),pool(-2))['signal']=='HOLD'
    assert choose(row(21),pool(.1))['signal']=='HOLD'
    h=pool(5);h[0]['net_pct']=-95;h[5]['net_pct']=-95;h[10]['net_pct']=-95;h[15]['net_pct']=-95;h[20]['net_pct']=-95;h[25]['net_pct']=-95;h[30]['net_pct']=-95;h[35]['net_pct']=-95;h[40]['net_pct']=-95;h[45]['net_pct']=-95
    assert choose(row(21),h)['signal']=='HOLD'
def test_empty_and_cohort():
    e=evaluate([]);assert e['strategies']['core']['hit_pct'] is None and e['validation_state']=='COLLECTING'
    a=row(0); b=row(1);b['v570']={'version':VERSION,'signal':'HOLD','probability':.7,'naive_probability':.5}
    e=evaluate([a,b]);assert e['strategies']['core']['trades']==2 and e['shadow_comparison']['core']['trades']==1 and e['shadow_comparison']['v570']['trades']==0
    assert e['calibration']['scored']==1 and e['calibration']['brier']==.09
    assert e['changes']['v570']['blocked_wins']==1
    assert e['strategy_adoption']=='HOLD'
def test_nonoverlap():
    a=row(0);b={**a,'day':'2025-01-02','exit_day':'2025-01-07'};c={**b,'symbol':'B'}
    assert metrics([a,b,c],15)['trades']==2
    assert stress(1,15)<1

def test_replay_future_unchanged():
    dates=[str(date(2025,1,1)+timedelta(days=i)) for i in range(70)]
    points=[{'signals':{'core':'BUY'},'candidates':{'range_guard':'BUY','rs_required':'BUY'},'subregime':'sideways','market_regime':'range','net':.02} for _ in range(65)]
    frames=[(dates,None,points) for _ in range(5)]
    e=historical(frames,list('ABCDE'),5,15);assert e['shadow_completed']==325 and e['basis']=='HISTORICAL_CAUSAL_REPLAY' and e['strategy_adoption']=='HOLD'
