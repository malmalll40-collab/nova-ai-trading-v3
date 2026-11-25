import numpy as np
import pandas as pd
import pytest
import nova_v573_ml as ml

def test_features_signal_day_only():
    idx=pd.date_range('2026-01-01',periods=50)
    f=pd.DataFrame(dict(Close=100.,ema20=99.,ema50=98.,atr=2.,rsi=55.,vol_ratio=1.2,ret5=.03,ret20=.08),index=idx)
    ind=pd.DataFrame(dict(adx=25.,plus_di=30.,minus_di=20.,slope=.01,width=.08),index=idx)
    market={t.date().isoformat():100+i for i,t in enumerate(idx)}
    a=ml.feature_vector(f,30,market,ind)
    assert len(a)==13 and np.isfinite(a).all()
    f.iloc[31:]=999.;ind.iloc[31:]=999.
    for t in idx[30:]:market[t.date().isoformat()]=99999
    assert a==ml.feature_vector(f,30,market,ind)
    market.pop(idx[29].date().isoformat())
    assert ml.feature_vector(f,30,market,ind) is None

def test_ml_learns_nonconstant_probabilities_and_return():
    train=[{'x':[float(i%2)]+[0.]*12,'net_pct':10. if i%2 else -8.} for i in range(80)]
    model=ml.fit(train)
    a,b=ml.predict(model,train[:2])
    for k in ('logit','boost','expected_net_pct'):assert a[k]<b[k]
    assert a['loss_probability']>b['loss_probability']
    assert a['naive']==b['naive']==.5

def test_single_class_no_fit():
    assert ml.fit([{'x':[0.]*13,'net_pct':1.} for _ in range(80)]) is None

def panel():
    dates=[t.date().isoformat() for t in pd.date_range('2026-01-01',periods=145)]
    frames=[]
    for s in range(6):
        pts=[]
        for i in range(140):
            sign=(i//5+s)%2
            # Stagger eligible core dates across symbols; separate range population.
            market='range' if i%2 else 'up'
            pts.append({'ml_features':[float(sign)]+[0.]*12,'net':.08 if sign else -.07,
                'signals':{'core':'BUY' if market=='up' and i%6==s else 'HOLD'},
                'market_regime':market,'subregime':'up_transition','route':'risk_veto' if market=='range' else 'core',
                'ml_stock_risk_ok':True})
        frames.append((dates,None,pts))
    return frames,list('ABCDEF')

def test_range_cohort_uses_separate_stock_risk():
    r={'core':False,'market':'range','regime':'sideways','stock_risk_ok':True}
    assert ml.cohort(r,'range_research') and not ml.cohort(r,'core')
    assert not ml.cohort({**r,'stock_risk_ok':False},'range_research')
    assert not ml.cohort({**r,'market':'down'},'range_research')

def test_walk_forward_matched_cohort(monkeypatch):
    frames,syms=panel()
    monkeypatch.setattr(ml,'MIN_DATES',8)
    monkeypatch.setattr(ml,'MIN_TRAIN',30)
    out=ml.historical(frames,syms,5,15)
    assert out['opportunities']==840 and out['strategy_adoption']=='HOLD'
    for k,v in out['cohorts'].items():
        o=v['overall']
        assert o['scored']>0 and o['scored']+o['unscored']==o['candidates']
        for f in v['folds']:
            assert f['last_training_exit'] is None or f['last_training_exit']<f['start']
        for bins in o['calibration_bins'].values():assert sum(b['n'] for b in bins)==o['scored']
        assert o['brier']['logit']<o['brier']['naive']
        assert o['nonoverlap_scored']<=o['scored']
    assert out['cohorts']['core']['by_market']['range']['candidates']==0
    assert out['cohorts']['range_research']['by_market']['up']['candidates']==0

def test_future_outcomes_do_not_change_earlier_predictions(monkeypatch):
    frames,syms=panel()
    monkeypatch.setattr(ml,'MIN_DATES',8);monkeypatch.setattr(ml,'MIN_TRAIN',30)
    captured=[]
    original=ml.predict
    def capture(model,rows):
        pred=original(model,rows)
        captured.extend((r['day'],r['symbol'],p) for r,p in zip(rows,pred) if r['day']<frames[0][0][100])
        return pred
    monkeypatch.setattr(ml,'predict',capture)
    ml.historical(frames,syms,5,15)
    before=list(captured);assert before
    captured.clear()
    for _,_,pts in frames:
        for p in pts[100:]:p['net']=-.9
    ml.historical(frames,syms,5,15)
    assert captured==before

def test_missing_features_fail_closed():
    frames,syms=panel();frames[0][2][0].pop('ml_features')
    assert ml.historical(frames,syms,5,15)['reason']=='FEATURES_MISSING'

def test_invalid_panel():
    with pytest.raises(ValueError):ml.historical([],[],5,15)
    frames,syms=panel()
    with pytest.raises(ValueError):ml.historical(frames,['A']*6,5,15)

def test_diagnostic_no_raw_features(monkeypatch):
    import ops_diagnostics as ops
    logs=[]
    monkeypatch.setattr(ops,'emit',lambda event,payload:logs.append((event,payload)))
    ops.emit_ml573({'core542_aligned':{'v573_ml':{'status':'READY','features':['SECRET'],
        'cohorts':{'core':{'overall':{'scored':5,'raw_predictions':['SECRET'],'brier':{'logit':.2,'boost':.21,'naive':.25}},
        'folds':[{'start':'2026-01-02','last_training_exit':'2026-01-01','train':50,'secret':'SECRET'}]}}}}})
    assert 'SECRET' not in str(logs)
    assert any(e=='v573_historical_cohort' and p['metrics']['brier']['logit']==.2 for e,p in logs)
