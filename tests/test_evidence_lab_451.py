"""v4.5.1: no backfilling, immutable observations, session calendar, and paper research."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from fastapi.testclient import TestClient
import pytest

import evidence_store
from evidence_store import append_analysis, list_snapshots, count_snapshots, make_snapshot
from market_sessions import krx_session
from paper_lab import simulate_paper
from server import app

KST=ZoneInfo('Asia/Seoul')
OBS=datetime(2026,6,3,10,30,tzinfo=KST)


def analysis(**kw):
    row={'symbol':'005930.KS','source':'kis','mode':'스윙','is_demo':False,
         'is_stale':False,'asof_evaluation_only':False,'signal_is_current':True,
         'last_completed_bar':'2026-06-02T00:00:00','reference':100.0,
         'raw_signal':'BUY','signal':'BUY','financial_gate':'PASS','cost_bps_one_side':15,
         'fetched_at':'2026-06-03T00:00:00','provider':'KIS','quote_time':'2026-06-03T10:20:00',
         'price_age_minutes':10}
    row.update(kw)
    return row


def test_market_calendar_weekend_holiday_special_exam():
    assert krx_session(datetime(2026,11,22,12,tzinfo=KST))['status']=='CLOSED'
    assert krx_session(datetime(2026,10,9,12,tzinfo=KST))['status']=='CLOSED'
    assert krx_session(datetime(2026,11,19,9,30,tzinfo=KST))['is_open'] is False
    a=krx_session(datetime(2026,11,19,16,20,tzinfo=KST))
    assert a['is_open'] is True and a['close_kst']=='16:30'
    assert krx_session(datetime(2026,11,20,13,tzinfo=KST))['is_open'] is True
    with pytest.raises(ValueError):krx_session(datetime(2026,11,22,12))


def test_immutable_durable_sqlite_and_no_backfill(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'evidence'/'signals.sqlite3'))
    assert evidence_store.backend()=='sqlite'
    assert append_analysis(analysis(),OBS)
    assert not append_analysis(analysis(reference=110),OBS)  # first observation wins
    assert count_snapshots()==1
    entry=list_snapshots()[0]
    assert entry['reference']==100 and entry['observed_at']==OBS.astimezone(timezone.utc).isoformat(timespec='seconds')
    assert len(entry['id'])<160
    for kw in ({'is_stale':True},{'signal_is_current':False},{'is_demo':True},
               {'asof_evaluation_only':True},{'raw_signal':'HOLD'},
               {'mode':'5분'}):
        assert make_snapshot(analysis(**kw),OBS) is None
    assert count_snapshots()==1


def test_disabled_backend_does_not_create_files(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('NOVA_EVIDENCE_SQLITE_PATH',raising=False)
    assert evidence_store.backend()=='disabled'
    assert not append_analysis(analysis(),OBS)
    assert not list(tmp_path.iterdir())


def test_paper_next_open_nonoverlap_fees_and_no_fake_fills():
    days=pd.bdate_range('2026-06-02',periods=15)
    frame=pd.DataFrame({'Open':[100+i for i in range(15)], 'Close':[100+i for i in range(15)]},index=days)
    rec=make_snapshot(analysis(),OBS)
    r=simulate_paper([rec],lambda *_:frame,now=datetime(2026,7,5,tzinfo=KST))
    assert r['trades_count']==1
    assert r['trades'][0]['entry_day']=='2026-06-04'  # observed June 3, never June 3 open
    assert r['trades'][0]['exit_day']=='2026-06-10'
    assert 0<r['cumulative_net_pct']<5
    assert r['realized_exit_only_mdd_pct']<=0
    two=dict(rec,id='2',observed_at='2026-06-04T10:30:00+09:00',bar_day='2026-06-03')
    r2=simulate_paper([rec,two],lambda *_:frame,now=datetime(2026,7,5,tzinfo=KST))
    assert r2['trades_count']==1 and r2['skipped']==1


def test_no_entry_if_outcome_not_yet_completed():
    frame=pd.DataFrame({'Open':[100,101,102],'Close':[100,101,102]},index=pd.bdate_range('2026-06-02',periods=3))
    rec=make_snapshot(analysis(),OBS)
    r=simulate_paper([rec],lambda *_:frame,now=datetime(2026,6,5,tzinfo=KST))
    assert r['trades_count']==0 and r['pending']==1


def test_authenticated_endpoints_and_empty_db(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','Z'*20)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'data.sqlite3'))
    monkeypatch.delenv('DATABASE_URL',raising=False)
    client=TestClient(app)
    assert client.get('/api/evidence/status').status_code==401
    assert client.post('/api/evidence/report',json={}).status_code==401
    auth={'Authorization':'Bearer '+'Z'*20}
    assert client.get('/api/evidence/status',headers=auth).json()['enabled'] is True
    assert client.post('/api/evidence/report',json={},headers=auth).json()['status']=='no_observations'
    assert client.post('/api/evidence/report',json={'limit':1000},headers=auth).status_code==422
    assert client.get('/api/market/session',headers=auth).status_code==200
    assert client.get('/healthz').json()['persistent_server_journal'] is True


def test_default_no_db_and_ui(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('NOVA_EVIDENCE_SQLITE_PATH',raising=False)
    client=TestClient(app)
    assert client.get('/api/evidence/status').json()['storage']=='browser_only'
    assert client.post('/api/evidence/report',json={}).status_code==503
    assert 'evidenceReportButton' in client.get('/').text
    assert 'marketSession' in client.get('/static/candles.js').text
    assert 'evidence451' in client.get('/sw.js').text


def test_server_report_from_actual_stamped_observations(tmp_path,monkeypatch):
    import server
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'ledger.sqlite3'))
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    assert append_analysis(analysis(),OBS)
    dates=pd.bdate_range('2026-06-02',periods=18)
    frame=pd.DataFrame({'Open':[100+i for i in range(len(dates))],
                        'Close':[100+i for i in range(len(dates))]},index=dates)
    monkeypatch.setattr(server,'_journal_daily_bars',lambda symbol,source:frame)
    data=TestClient(app).post('/api/evidence/report',json={'limit':60})
    assert data.status_code==200
    r=data.json()
    assert r['count']==1 and r['total']==1
    assert r['horizons']['1']['complete']==1
    assert r['paper']['trades_count']==1
    assert r['paper']['trades'][0]['entry_day']=='2026-06-04'


def test_analysis_is_unaffected_by_failed_persistent_write(monkeypatch):
    import server
    monkeypatch.setattr(server,'_cached',lambda key,make,ttl=None:{'symbol':'005930.KS',
        'source':'kis','mode':'스윙','raw_signal':'BUY','signal':'BUY','is_demo':False,
        'risk_structure':{},'is_stale':False})
    monkeypatch.setattr(server,'evidence_backend',lambda:'postgresql')
    def fail(*args,**kwargs):raise RuntimeError('a private credential string must never surface')
    monkeypatch.setattr(server,'append_analysis',fail)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    response=TestClient(app).get('/api/analysis?source=kis')
    assert response.status_code==200
    assert 'private credential' not in response.text


def test_snapshot_integrity_protects_against_mutation(tmp_path,monkeypatch):
    import sqlite3
    monkeypatch.delenv('DATABASE_URL',raising=False)
    path=str(tmp_path/'ledger.sqlite3')
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',path)
    assert append_analysis(analysis(),OBS)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE nova_evidence SET snapshot_json=?', ('{"reference": 999999}',))
    with pytest.raises(ValueError,match='무결성'):
        list_snapshots()
