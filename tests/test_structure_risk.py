from __future__ import annotations
from copy import deepcopy
import math
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
import server
from data_sources import demo_data
from engine import Config, feature_frame
from structural_risk import evidence_from_features, reprice_from_evidence

c = TestClient(server.app)


def ev():
    return {'available':True,'reference_close':100.,'atr':2., 'stop_atr':2.,'target_atr':3.,
            'support_candidates':[95.,97.,85.], 'resistance_candidates':[107.,109.,130.],
            'mode':'스윙', 'as_of_bar':'test','bars_considered':60}


def test_level_math_and_no_action():
    x=reprice_from_evidence(ev(),cost_bps=15)
    assert x['entry']==100
    assert x['stop']==min(96,97-0.4)
    assert x['target']==min(106,107-0.3)
    assert x['actionable'] is False and x['backtested'] is False
    assert x['user_entry_provided'] is False
    assert x['reason_codes']==[]
    assert x['state']=='RESEARCH_REFERENCE_ONLY'


def test_repricing_uses_custom_entry_not_reference_close():
    x=reprice_from_evidence(ev(),entry_price=102,cost_bps=15)
    assert x['entry']==102 and x['reference_close']==100 and x['user_entry_provided']
    assert x['stop']==96.6 and x['target']==106.7
    assert x['risk_reward_net']<1.5
    assert x['state']=='UNFAVORABLE'


def test_near_resistance_blocks_false_positive():
    s=ev();s['resistance_candidates']=[100.1]
    x=reprice_from_evidence(s,cost_bps=15)
    assert x['target'] is None and x['state']=='NO_VALID_PLAN'
    assert 'RESISTANCE_TOO_CLOSE' in x['reason_codes']


def test_far_support_not_approved():
    s=ev();s['support_candidates']=[91.,90.]
    x=reprice_from_evidence(s)
    assert 'SUPPORT_TOO_FAR' in x['reason_codes'] and not x['actionable']


def test_missing_support_or_resistance_never_fabricated():
    for field in ['support_candidates','resistance_candidates']:
        s=ev();s[field]=[]
        x=reprice_from_evidence(s)
        assert x['state']!='RESEARCH_REFERENCE_ONLY'
        assert x['support'] is None or x['resistance'] is None
        assert x['actionable'] is False


def test_nan_infinite_or_extreme_entry_rejected():
    for entry in [float('nan'),float('inf'),float('-inf'),-3,0,200,None]:
        x=reprice_from_evidence(ev(),entry_price=entry)
        if entry is None:
            assert x['state']!='INVALID_ENTRY'
        else:
            assert x['state']=='INVALID_ENTRY'


def test_demo_and_stale_cannot_be_marked_eligible():
    for kw in [{'demo':True},{'stale':True},{'demo':True,'stale':True}]:
        x=reprice_from_evidence(ev(),**kw)
        assert x['state']!='RESEARCH_REFERENCE_ONLY' and not x['actionable']


def test_missing_ohlcv_or_atr_disables_plan():
    s=ev();s['available']=False
    x=reprice_from_evidence(s)
    assert x['state']=='INSUFFICIENT_DATA' and x['entry'] is None


def test_confirmed_pivots_no_future_lookahead():
    f=feature_frame(demo_data('스윙').bars,'스윙')
    cfg=Config.for_mode('스윙')
    at=110
    a=evidence_from_features(f.iloc[:at+1],cfg)
    b=f.copy()
    b.loc[b.index[at+1:],'High']*=100
    b.loc[b.index[at+1:],'Low']*=.01
    updated=evidence_from_features(b,cfg,at)
    assert a==updated


def test_end_to_end_api_only_structure_changes_with_custom_entry():
    server.CACHE.clear()
    r=c.get('/api/analysis',params={'source':'demo','mode':'스윙'})
    assert r.status_code==200,r.text
    base=r.json();baseline=base['risk_structure']
    assert baseline['evidence']['available']
    ent=base['reference']*1.01
    x=c.get('/api/analysis',params={'source':'demo','mode':'스윙','entry_price':ent})
    assert x.status_code==200,x.text
    customized=x.json()
    assert abs(customized['risk_structure']['entry']-ent)<1e-4
    assert customized['risk_structure']['user_entry_provided']
    assert customized['risk_structure']['actionable'] is False
    for field in ('signal','strict_signal','raw_signal','long_stop_reference','long_target_reference','backtest','votes','chart','high_precision_research'):
        assert customized[field]==base[field],field
    for invalid in ('inf','nan','999999999999','-2','0'):
        y=c.get('/api/analysis',params={'source':'demo','mode':'스윙','entry_price':invalid})
        assert y.status_code==422


def test_ui_and_cache_controls():
    html=c.get('/').text
    js=c.get('/static/app.js').text
    chart=c.get('/static/candles.js').text
    assert 'riskStructurePanel' in html and 'riskEntryApply' in html
    assert 'risk_structure' in js and 'renderRiskStructure(d)' in js
    assert c.get('/healthz').json()['orders_enabled'] is False
    assert 'long_stop_reference' in chart
    assert 'structure_levels_research_only' in c.get('/healthz').json()
