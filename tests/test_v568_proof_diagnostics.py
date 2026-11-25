import pytest
import kis_minute_coverage as k
import kis_gap_probe as gp
import kis_zero_gap as z
import kis_turnover_proof as tp
from tests.test_v568_zero_gap import API, DAY, START, CLOSE, NOW
from collection_audit import integrity_detail

@pytest.fixture(autouse=True)
def same_day(monkeypatch):
    monkeypatch.setattr(k,'compare_same_day',lambda *a:gp.compare_same_day(*a,now=NOW))
    monkeypatch.setattr(k,'certify_zero_gaps',lambda *a:z.certify_zero_gaps(*a,now=NOW))
    monkeypatch.setattr(k,'verify_turnover',lambda *a:tp.verify(*a,now=NOW))

@pytest.mark.parametrize('mode,reason',[('delta','CUMULATIVE_DELTA_MISMATCH'),('no_cumulative','CUMULATIVE_UNAVAILABLE'),('positive','TARGET_NOT_ZERO'),('bad_target_cum','ALTERNATE_CUMULATIVE_MISMATCH'),('neighbor_conflict','NEIGHBOR_MISMATCH'),('first_neighbor','PROOF_ROW_FIRST'),('query_failed','QUERY_FAILED'),('ok','VERIFIED')])
def test_exact_reason_preserves_gate(mode,reason):
    result,d=k.collect(API(mode),'086520',DAY,START,CLOSE)
    assert d['zero_proof_diagnostics']=={'13:21':reason}
    assert (result is not None)==(mode=='ok')
    if mode=='no_cumulative':assert d['cumulative_field_missing_rows']==1
    diag=integrity_detail({'daily_integrity':{'minute_coverage':d}})['minute_coverage']
    assert diag['zero_proof_diagnostics']==d['zero_proof_diagnostics']

def test_no_field_not_zero():
    class NoField(API):
        def get(self,path,tr,p):
            result=super().get(path,tr,p)
            if tr=='FHKST03010230':
                for r in result['output2']:r.pop('acml_vol',None)
            return result
    value,d=k.collect(NoField('ok'),'086520',DAY,START,CLOSE)
    # No cumulative share count AND no turnover: alternate proof fails closed.
    assert value is None and d['cumulative_field_missing_rows']==380
    assert d['reason']=='COVERAGE_MISSING' and d['unresolved_count']==1
    assert d['certified_zero_count']==0 and not d['zero_proofs']
    assert d['zero_proof_diagnostics']=={'13:21':'TURNOVER_UNAVAILABLE'}
    d['zero_proof_diagnostics']['secret']='raw'; d['zero_proof_diagnostics']['10:00']='secret'
    assert integrity_detail({'daily_integrity':{'minute_coverage':d}})['minute_coverage']['zero_proof_diagnostics']=={'13:21':'TURNOVER_UNAVAILABLE'}
