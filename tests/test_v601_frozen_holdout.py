from datetime import date,timedelta
from nova_v571_policy import historical

def panel(n=125,tail_net=.02):
    dates=[str(date(2025,1,1)+timedelta(days=i)) for i in range(n+5)]
    pts=[{'subregime':'strong_up','market_regime':'up','signals':{'core':'BUY'},
          'candidates':{'rs_hybrid':'BUY'},'net':.02 if i<n*4//5 else tail_net}
         for i in range(n)]
    return [(dates,None,pts) for _ in range(5)],list('ABCDE')

def test_frozen_holdout_strictly_mature_training():
    frames,symbols=panel()
    r=historical(frames,symbols,5,15)['frozen_holdout']
    assert r['status']=='READY' and r['signal_dates']==25
    assert r['training_rows']==95*5 and r['last_training_exit']<r['start']
    assert r['model_updates_in_holdout']==0
    assert r['overall']['opportunities']==125
    assert r['by_market']['up']['opportunities']==125
    assert not r['independent_future_verified'] and r['strategy_adoption']=='HOLD'

def test_future_labels_change_results_not_frozen_decisions():
    frames,symbols=panel(tail_net=.03)
    a=historical(frames,symbols,5,15)['frozen_holdout']['overall']
    frames,symbols=panel(tail_net=-.03)
    b=historical(frames,symbols,5,15)['frozen_holdout']['overall']
    assert a['states']==b['states']
    assert a['v571']['trades']==b['v571']['trades']>0
    assert a['v571']['mean_net_pct']>0>b['v571']['mean_net_pct']

def test_short_panel_not_presented_as_validated_holdout():
    frames,symbols=panel(99)
    r=historical(frames,symbols,5,15)['frozen_holdout']
    assert r['status']=='INSUFFICIENT' and 'overall' not in r
