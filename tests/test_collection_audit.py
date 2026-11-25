from datetime import datetime, timezone
import sqlite3
import json
import pytest
from collection_audit import audit_day, unverified_reason, summarize_rows
from core561_evidence import SCHEMA, TABLE, VERSION
from tests.test_v550_research import observation

NOW = datetime(2026, 11, 25, 10, tzinfo=timezone.utc)
DAY = '2026-11-25'
SYMS = ['005930.KS', '086520.KQ']

@pytest.mark.parametrize('key,expected', [('verified','DAILY_BAR_UNVERIFIED'), ('regular_volume_verified','REGULAR_VOLUME_UNVERIFIED')])
def test_gate_reason(key, expected):
    a = observation(NOW)
    a['daily_integrity'][key] = False
    assert unverified_reason(a, NOW) == expected

def test_other_reasons():
    a = observation(NOW); a['daily_integrity']['signal_hold'] = True
    assert unverified_reason(a, NOW) == 'DAILY_INTEGRITY_HOLD'
    a = observation(NOW); a['completed_session']['calendar_verified'] = False
    assert unverified_reason(a, NOW) == 'SESSION_CALENDAR_UNVERIFIED'
    a = observation(NOW); a['last_completed_bar'] = '2026-11-24'
    assert unverified_reason(a, NOW) == 'COMPLETED_DAY_MISMATCH'

def payload(symbol, signals):
    return {'id':f'{VERSION}|{symbol}|{DAY}', 'version':VERSION,'symbol':symbol,'day':DAY,'cost_bps':15,
            'price_basis':'KIS_REGULAR_MINUTE','research_only':True,'signals':signals}

def test_readonly_actual_counts(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    path=tmp_path/'audit.db';monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(path))
    assert audit_day(DAY,SYMS)['status']=='FILE_NOT_CREATED' and not path.exists()
    with sqlite3.connect(path) as db: db.execute('SELECT 1')
    assert audit_day(DAY,SYMS)['table_exists'] is False
    with sqlite3.connect(path) as db:
        db.execute(SCHEMA)
        p=payload(SYMS[0],{'core':'HOLD'})
        db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?)',(p['id'],SYMS[0],DAY,NOW.isoformat(),json.dumps(p)))
    a=audit_day(DAY,SYMS)
    assert a['total_rows']==a['day_rows']==a['verified_signals']==1
    assert a['missing_symbols']==[SYMS[1]] and a['verified_symbols']==[SYMS[0]]
    assert audit_day(DAY,SYMS)==a

def test_price_only_invalid_and_duplicates():
    a=payload(SYMS[0],{});b=payload(SYMS[1],{'core':'BUY'})
    r=summarize_rows(DAY,SYMS,[(SYMS[0],json.dumps(a)),(SYMS[1],json.dumps(b)),(SYMS[1],'broken')],VERSION,3)
    assert r['price_only']==1 and r['verified_signals']==0 and r['invalid_symbols']==[SYMS[1]]
