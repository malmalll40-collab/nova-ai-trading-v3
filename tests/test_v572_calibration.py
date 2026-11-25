from datetime import date, timedelta
import pytest
from nova_v572_calibration import fit, decide, historical

def row(day='2026-01-01',exit_day='2026-01-02',symbol='A',net=2,**kw):
    return dict(day=day,exit_day=exit_day,symbol=symbol,net_pct=net,cost_bps=15,subregime='strong_up',market_regime='up',risk_veto=False,signals={'core':'BUY','rs_required':'BUY'})|kw

def sample(net):
    start=date(2026,1,1)
    return [row(str(start+timedelta(days=2*i)),str(start+timedelta(days=2*i+1)),str(i%5),net) for i in range(20)]

def test_no_history_keeps_core():
    d=decide(row(),fit([],'2026-03-01',15))
    assert d['signal']=='BUY' and d['exposure']==1 and d['probability']==.5

def test_pooling_unseen_regime():
    m=fit(sample(2),'2026-03-01',15)
    d=decide(row(subregime='sideways'),m)
    assert d['signal']=='BUY' and d['probability']==m['prior'] and d['exposure']==1

def test_future_outcomes_excluded():
    hist=sample(2);day='2026-03-01'
    assert fit(hist,day,15)==fit(hist+[row('2026-02-28',day,net=-99)],day,15)

def test_size_not_signal():
    d=decide(row(),fit(sample(-3),'2026-03-01',15))
    assert d['signal']=='BUY' and d['exposure']==.5 and d['state']=='RISK_HALF'

def test_no_new_signal_or_forecast_for_ineligible():
    m=fit(sample(2),'2026-03-01',15)
    d=decide(row(signals={'core':'HOLD'}),m)
    assert d['signal']=='HOLD' and d['exposure']==0 and d['probability'] is None
    d=decide(row(risk_veto=True),m)
    assert d['probability'] is None and d['state']=='CORE_UNCHANGED'

def test_panel_and_exposure_reference():
    dates=[str(date(2026,1,1)+timedelta(days=i)) for i in range(100)]
    pts=[{'subregime':'strong_up','market_regime':'up','signals':{'core':'BUY'},'candidates':{'rs_required':'BUY'},'net':-.03} for _ in range(95)]
    r=historical([(dates,None,pts)]*5,list('ABCDE'),5,15)
    o=r['overall']
    assert o['core_candidates']==o['scored']==475
    assert o['core']['trades']==o['v572_sized']['trades']==o['unchanged']['trades']
    assert o['v572_sized']['mean_exposure']==o['equal_exposure_reference']['mean_exposure']
    assert o['reduced_trades']>0 and r['strategy_adoption']=='HOLD'
    assert sum(b['n'] for b in o['calibration_bins'])==o['scored']

def test_invalid_panel():
    with pytest.raises(ValueError):historical([],[],5,15)
    with pytest.raises(ValueError):historical([(['2026-01-01'],None,[])],['A'],5,15)
