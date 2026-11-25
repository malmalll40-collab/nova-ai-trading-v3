from datetime import datetime,timezone
import json,sqlite3
from core570_report import report,summarize,TABLE
NOW=datetime(2026,11,25,12,tzinfo=timezone.utc)
def observation(day='2026-11-25',shadow=False):
    r=dict(id='5.6.1|005930.KS|'+day,version='5.6.1',symbol='005930.KS',day=day,observed_at=day+'T10:00:00+00:00',open=100.,close=101.,signals={'core':'BUY'},regime='range',subregime='sideways',cost_bps=15,price_basis='KIS_REGULAR_MINUTE',research_only=True)
    if shadow:r.update(v570={'version':'5.7.0','signal':'HOLD'})
    return r
def test_readonly_pending(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);p=tmp_path/'obs.db';monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(p))
    assert report(NOW)['stored_records']==0 and not p.exists()
    with sqlite3.connect(p) as db:
        db.execute(f'CREATE TABLE {TABLE}(id TEXT PRIMARY KEY,symbol TEXT,trading_day TEXT,observed_at TEXT,payload TEXT)')
        r=observation();db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?)',(r['id'],r['symbol'],r['day'],r['observed_at'],json.dumps(r)))
    before=p.read_bytes();a=report(NOW);assert a['stored_records']==1 and a['pending']==1 and a['completed']==0 and a['shadow_frozen']==0 and a['strategies']['core']['hit_pct'] is None
    assert p.read_bytes()==before

def test_reject_invalid():
    a=observation();a['observed_at']='2026-11-24T10:00:00+00:00';assert summarize([a],NOW)['status']=='UNAVAILABLE'

def test_future_frozen_no_backfill():
    a=summarize([observation(shadow=True)],NOW);assert a['shadow_frozen']==1 and a['shadow_completed']==0
