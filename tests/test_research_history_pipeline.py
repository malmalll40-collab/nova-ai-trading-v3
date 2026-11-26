from datetime import date,timedelta
from math import sin
import pytest
from research_history_pipeline import run,build_frames,START,END
from nova_v576_expanded_coverage import SYMBOLS
from nova_v574_data_audit import INDEX_CODES

DAYS=tuple(date(2025,1,1)+timedelta(days=i) for i in range(190))
def panel():
    p={}
    for s in SYMBOLS:
        data={}
        for i,d in enumerate(DAYS):
            px=100+i*.2+2*sin(i*.2)
            data[d]={'close':px} if s in INDEX_CODES else {
                'open':px*.995,'high':px*1.02,'low':px*.98,'close':px,
                'volume':1000+i%10,'turnover':None}
        p[s]=data
    return p

def test_partial_does_not_collect_or_evaluate_by_default():
    def forbidden(*a):pytest.fail('unexpected execution')
    r=run('fixture',calendar=lambda a,b:(END,),reader=lambda *a:({'status':'PARTIAL','stored_rows':1},None),
          collection=forbidden,evaluator=forbidden)
    assert r['reason']=='COMPLETE_SNAPSHOT_REQUIRED' and r['coverage']['stored_rows']==1
    assert not r['execution_authorized']

def test_explicit_collection_rereads_before_evaluation():
    calls=[]
    def reader(*a):
        calls.append('read')
        return ({'status':'PARTIAL'},None) if len(calls)==1 else ({'status':'COVERAGE_COMPLETE'},'panel')
    def collect():
        calls.append('collect')
        return {'status':'READY','new_rows':22,'secret':'not output'}
    def evaluate(p,days):
        calls.append('evaluate')
        assert p=='panel' and days==(END,)
        return {'status':'INSUFFICIENT_EVIDENCE','strategy_adoption':'BUY','execution_authorized':True}
    r=run('fixture',calendar=lambda a,b:(END,),reader=reader,collection=collect,allow_collection=True,evaluator=evaluate)
    assert calls==['read','collect','read','evaluate']
    assert r['evaluation']['strategy_adoption']=='HOLD' and not r['evaluation']['execution_authorized']
    assert 'secret' not in r['collection']

def test_unavailable_snapshot_never_triggers_collection():
    def forbidden(*a):pytest.fail('unexpected execution')
    r=run('fixture',calendar=lambda a,b:(END,),reader=lambda *a:({'status':'UNAVAILABLE'},None),
          collection=forbidden,allow_collection=True,evaluator=forbidden)
    assert r['status']=='BLOCKED'

def test_evaluation_failure_preserves_coverage():
    def fail(*a):raise ValueError('ZERO_VOLUME_FILL_UNVERIFIED')
    r=run('fixture',calendar=lambda a,b:(END,),reader=lambda *a:({'status':'COVERAGE_COMPLETE','stored_rows':22},'panel'),evaluator=fail)
    assert r['reason']=='ZERO_VOLUME_FILL_UNVERIFIED' and r['coverage']['stored_rows']==22

def test_exact_panel_required_and_zero_volume_not_assumed_tradable():
    p=panel();s=next(s for s in SYMBOLS if s not in INDEX_CODES)
    row=p[s].pop(DAYS[-1])
    with pytest.raises(ValueError,match='INCOMPLETE_PANEL'):build_frames(p,DAYS)
    p[s][DAYS[-1]]={**row,'volume':0}
    with pytest.raises(ValueError,match='ZERO_VOLUME_FILL_UNVERIFIED'):build_frames(p,DAYS)

def test_causal_features_and_next_open_to_fifth_close_outcome():
    p=panel();frames,symbols=build_frames(p,DAYS)
    s=symbols[0];ds,_,pts=frames[0]
    assert len(symbols)==20 and len(pts)==105 and len(ds)==110
    i=80;fee=15/10000
    expected=p[s][DAYS[i+5]]['close']*(1-fee)/(p[s][DAYS[i+1]]['open']*(1+fee))-1
    assert pts[0]['net']==pytest.approx(expected)
    r=p[s][DAYS[-1]];r['close']*=1.1;r['high']=max(r['high'],r['close'])
    altered,_=build_frames(p,DAYS)
    assert [x for x in pts[:-1]]==altered[0][2][:-1]
    assert pts[-1]['net']!=altered[0][2][-1]['net']
    assert {k:v for k,v in pts[-1].items() if k!='net'}=={k:v for k,v in altered[0][2][-1].items() if k!='net'}
