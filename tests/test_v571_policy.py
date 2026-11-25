from datetime import date, timedelta
from nova_v571_policy import decide, fit, historical

def row(day='2026-01-01', exit_day='2026-01-02', symbol='A', net=2, **kwargs):
    return dict(day=day, exit_day=exit_day, symbol=symbol, net_pct=net, cost_bps=15, subregime='strong_up', market_regime='up', risk_veto=False, signals={'core':'BUY','rs_hybrid':'BUY'}, **{}) | kwargs

def model():
    return {('strong_up','core'):{'probability':.6,'naive_probability':.5,'stress_mean_pct':2,'tail10_pct':-5},('strong_up','rs_hybrid'):{'probability':.7,'naive_probability':.5,'stress_mean_pct':3,'tail10_pct':-4}}

def test_risk_and_core_gate():
    for r in [row(risk_veto=True),row(market_regime='down'),row(market_regime=None),row(signals={'core':'HOLD','rs_hybrid':'BUY'})]:
        assert decide(r,model())['signal']=='HOLD'

def test_missing_sample():
    assert decide(row(),{})['state']=='INSUFFICIENT_POOL'

def test_causal_cutoff():
    start=date(2026,1,1)
    hist=[row(str(start+timedelta(days=i*2)),str(start+timedelta(days=i*2+1)),str(i%5)) for i in range(20)]
    cutoff='2026-03-01'; a=fit(hist,cutoff,15)
    assert a and fit(hist+[row('2026-02-28',cutoff,net=900)],cutoff,15)==a
    assert fit(hist+[row('2026-02-28','2026-03-02',net=-99)],cutoff,15)==a

def test_route_and_fallback():
    assert decide(row(),model())['route']=='rs_hybrid'
    assert decide(row(signals={'core':'BUY','rs_hybrid':'HOLD'}),model())['state']=='ROUTE_FILTER'
    m=model();m[('strong_up','rs_hybrid')]['tail10_pct']=-6
    assert decide(row(),m)['state']=='CORE_FALLBACK'

def test_negative_stress():
    m=model();m[('strong_up','core')]['stress_mean_pct']=-1
    assert decide(row(),m)['state']=='NONPOSITIVE_CORE'

def test_historical_coverage():
    start=date(2026,1,1); dates=[str(start+timedelta(days=i)) for i in range(80)]
    p={'subregime':'strong_up','market_regime':'up','signals':{'core':'BUY'},'candidates':{'rs_hybrid':'BUY'},'net':.02}
    result=historical([(dates,None,[p]*75)]*5,list('ABCDE'),5,15)
    assert result['overall']['opportunities']==375 and result['market_coverage']=={'up':375}
    assert result['strategy_adoption']=='HOLD' and not result['independent_future_verified']
    assert result['overall']['v571']['trades']>0
