from datetime import date
import sqlite3
import pytest
from research_krx_snapshot import (SERVICES,BASIS,DATASETS,TABLE,manifest,initialize,
    parse_snapshot,import_snapshot,coverage)
from research_history_checkpoint import manifest as kis_manifest,TABLE as KIS_TABLE

DAY=date(2026,11,25)
DAYS=(DAY,)
def payload(service):
    if service=='kospi_dd_trd':
        return {'OutBlock_1':[{'BAS_DD':'20261125','IDX_NM':'코스피','CLSPRC_IDX':'3,000.25'}]}
    if service=='kosdaq_dd_trd':
        return {'OutBlock_1':[{'BAS_DD':'20261125','IDX_NM':'코스닥','CLSPRC_IDX':'900.25'}]}
    return {'OutBlock_1':[{'BAS_DD':'20261125','ISU_CD':s[:6],
        'MKT_NM':'KOSPI' if s.endswith('.KS') else 'KOSDAQ','TDD_OPNPRC':'100',
        'TDD_CLSPRC':'101','TDD_HGPRC':'102','TDD_LWPRC':'99','ACC_TRDVOL':'1,000',
        'ACC_TRDVAL':'100,000'} for s in SERVICES[service]]}

@pytest.fixture
def db():
    with sqlite3.connect(':memory:') as conn:
        cur=conn.cursor()
        yield cur,initialize(cur,'sqlite',DAYS)

def test_basis_and_storage_isolated_from_kis(db):
    cur,ds=db
    assert ds!=kis_manifest(DAYS)[0] and BASIS!='KIS_NATIVE_ADJUSTED_HISTORY'
    assert TABLE!=KIS_TABLE
    assert cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(KIS_TABLE,)).fetchone() is None
    assert coverage(cur,'sqlite',ds)['stored_rows']==0

def test_exact_primary_index_not_related_index():
    p=payload('kospi_dd_trd')
    p['OutBlock_1'].insert(0,{'BAS_DD':'20261125','IDX_NM':'코스피 200','CLSPRC_IDX':'300'})
    assert parse_snapshot('kospi_dd_trd',DAY,p)=={'^KS11':{'close':3000.25}}

def test_wrong_date_and_auth_response_fail_closed():
    p=payload('kospi_dd_trd')
    p['OutBlock_1'][0]['BAS_DD']='20261124'
    with pytest.raises(ValueError,match='KRX_DATE_MISMATCH'):parse_snapshot('kospi_dd_trd',DAY,p)
    with pytest.raises(ValueError,match='KRX_PROVIDER_RESPONSE_INVALID'):parse_snapshot('kospi_dd_trd',DAY,{'respCode':'401','OutBlock_1':[]})

def test_missing_duplicate_and_wrong_market_refused():
    service='stk_bydd_trd'
    p=payload(service);p['OutBlock_1'].pop()
    with pytest.raises(ValueError,match='KRX_COHORT_INCOMPLETE'):parse_snapshot(service,DAY,p)
    p=payload(service);p['OutBlock_1'].append(dict(p['OutBlock_1'][0]))
    with pytest.raises(ValueError,match='KRX_DUPLICATE_SYMBOL'):parse_snapshot(service,DAY,p)
    p=payload(service);p['OutBlock_1'][0]['MKT_NM']='KOSDAQ'
    with pytest.raises(ValueError,match='KRX_MARKET_MISMATCH'):parse_snapshot(service,DAY,p)

def test_invalid_values_no_imputation():
    service='ksq_bydd_trd'
    for field,value in [('TDD_OPNPRC','-'),('TDD_HGPRC','98'),('ACC_TRDVOL','1.5'),('TDD_CLSPRC','NaN'),('ACC_TRDVAL','10,00')]:
        p=payload(service);p['OutBlock_1'][0][field]=value
        with pytest.raises(ValueError):parse_snapshot(service,DAY,p)

def test_explicit_service_authorization_required(db):
    cur,ds=db
    with pytest.raises(ValueError,match='KRX_IMPORT_NOT_AUTHORIZED'):
        import_snapshot(cur,'sqlite',ds,'kospi_dd_trd',DAY,payload('kospi_dd_trd'),authorized=lambda s,d:False)
    assert coverage(cur,'sqlite',ds)['stored_rows']==0

def test_immutable_resume_and_complete_counts(db):
    cur,ds=db
    for service,syms in SERVICES.items():
        p=payload(service)
        assert import_snapshot(cur,'sqlite',ds,service,DAY,p,authorized=lambda s,d:True)==len(syms)
        assert import_snapshot(cur,'sqlite',ds,service,DAY,p,authorized=lambda s,d:True)==0
    info=coverage(cur,'sqlite',ds)
    assert info['stored_rows']==info['expected_rows']==info['complete_streams']==22
    assert info['status']=='COVERAGE_COMPLETE' and not info['training_ready']
    p=payload('kospi_dd_trd');p['OutBlock_1'][0]['CLSPRC_IDX']='3100'
    with pytest.raises(ValueError,match='KRX_SNAPSHOT_CONFLICT'):
        import_snapshot(cur,'sqlite',ds,'kospi_dd_trd',DAY,p,authorized=lambda s,d:True)
    assert coverage(cur,'sqlite',ds)==info

def test_corrupt_snapshot_not_counted(db):
    cur,ds=db
    import_snapshot(cur,'sqlite',ds,'kospi_dd_trd',DAY,payload('kospi_dd_trd'),authorized=lambda s,d:True)
    cur.execute(f'UPDATE {TABLE} SET digest=? WHERE dataset=?',('bad',ds))
    with pytest.raises(ValueError,match='KRX_SNAPSHOT_CORRUPT'):coverage(cur,'sqlite',ds)
