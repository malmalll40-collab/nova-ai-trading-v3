"""Offline regression tests for KIS-only migration and strict regular-session guard."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from fastapi.testclient import TestClient
import daily_settlement_guard as g
from tests.test_v532_daily_settlement import bars, FakeBroker
KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026,11,24,21,0,tzinfo=KST)
@pytest.fixture(autouse=True)
def cache_clear():
    g._PROVIDER_CACHE.clear();g._REGULAR_CACHE.clear()
    yield
    g._PROVIDER_CACHE.clear();g._REGULAR_CACHE.clear()

def check(primary,api,monkeypatch,reference):
    monkeypatch.setattr(g,'regular_reference',lambda *a:reference)
    def forbidden(*args):raise AssertionError('Yahoo called')
    return g.verify_latest_kis_daily(primary,api=api,code='005930',symbol='005930.KS',now=NOW,yahoo_loader=forbidden,force=True)

def test_same_day_valid_without_yahoo(monkeypatch):
    primary=bars();api=FakeBroker()
    safe,report=check(primary,api,monkeypatch,{**g._value_at(primary,NOW.date()),'Volume':10000.})
    assert report['verified'] and report['verification_basis']=='KIS_REGULAR_MINUTE' and not report['yahoo_used']
    pd.testing.assert_frame_equal(safe,primary)

def test_same_day_two_kis_daily_prices_not_enough(monkeypatch):
    primary=bars();api=FakeBroker()
    safe,report=check(primary,api,monkeypatch,None)
    assert report['state']=='UNVERIFIED' and report['signal_hold'] and len(safe)==5

def test_regular_conflict_quarantines_and_requery_recovers(monkeypatch):
    primary=bars(last=184100,low=183000);reference=g._value_at(bars(),NOW.date())
    safe,report=check(primary,FakeBroker(secondary=primary,requery=primary),monkeypatch,reference)
    assert report['state']=='PRICE_CONFLICT' and len(safe)==5
    safe,report=check(primary,FakeBroker(secondary=primary,requery=bars()),monkeypatch,{**reference,'Volume':10000.})
    assert report['state']=='REGULAR_RECONSTRUCTED' and safe.Close.iloc[-1]==189400
    assert primary.Close.iloc[-1]==184100

def test_regular_minute_coverage_rejects_missing_and_excludes_after_hours(monkeypatch):
    import market_sessions
    monkeypatch.setattr(market_sessions,'krx_session',lambda *a:{'calendar_verified':True,'open_kst':'09:00','close_kst':'15:30'})
    minutes=pd.date_range('2026-11-24 09:00','2026-11-24 15:19',freq='min').tolist()+[pd.Timestamp('2026-11-24 15:30'),pd.Timestamp('2026-11-24 19:00')]
    def row(t):return {'stck_bsop_date':'20261124','stck_cntg_hour':t.strftime('%H%M%S'),'cntg_vol':'10','stck_oprc':'100','stck_hgpr':'102','stck_lwpr':'99','stck_prpr':'101' if t.hour!=19 else '99'}
    class API:
        def __init__(self,ts):self.ts=ts
        def get(self,path,tr,params):
            assert tr=='FHKST03010230' and params['FID_COND_MRKT_DIV_CODE']=='J'
            return {'output2':[row(t) for t in reversed(self.ts) if t.strftime('%H%M%S')<=params['FID_INPUT_HOUR_1']][:100]}
    assert g.regular_reference(API(minutes),'005930',NOW.date())=={'Open':100,'High':102,'Low':99,'Close':101,'Volume':3810}
    assert g.regular_reference(API(minutes[1:]),'005930',NOW.date()) is None
    assert g.regular_reference(API([t for t in minutes if t.strftime('%H%M')!='1530']),'005930',NOW.date()) is None

def test_index_reference_uses_kis_and_prior_dates_only():
    from kis_reference import fetch_kis_reference
    class API:
        def get(self,path,tr,params):
            assert tr=='FHKUP03500100' and params['FID_INPUT_ISCD']=='0001'
            return {'output2':[{'stck_bsop_date':'20261124','bstp_nmix_prpr':'3000'},{'stck_bsop_date':'20261123','bstp_nmix_prpr':'2900'}]}
    r=fetch_kis_reference('^KS11',NOW,api=API(),period='2y')
    assert list(r.index)==['2026-11-23'] and r.Close.iloc[0]==2900

def test_public_routes_reject_legacy_yahoo(monkeypatch):
    import server
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','v533-test-token-long-enough')
    c=TestClient(server.app);h={'Authorization':'Bearer v533-test-token-long-enough'}
    for path in ['/api/analysis','/api/chart/history','/api/research/v5']:
        r=c.get(path+'?symbol=005930.KS&source=yahoo',headers=h)
        assert r.status_code==422,(path,r.status_code)
    assert server.healthz()['kis_only_analysis'] and server.healthz()['app_version']=='5.6.4'

def test_auto_source_never_falls_back(monkeypatch):
    import server
    monkeypatch.setattr(server,'_kis_creds',lambda:('key','secret'))
    def unavailable(*a):raise RuntimeError('KIS failure')
    monkeypatch.setattr(server,'kis_data',unavailable)
    with pytest.raises(RuntimeError,match='KIS failure'):server._payload('005930.KS','스윙','auto',15)
    with pytest.raises(RuntimeError,match='KIS failure'):server._v5_compute('005930.KS','auto',15)

def test_archive_opt_in_and_dedup(tmp_path,monkeypatch):
    from kis_daily_archive import archive_verified
    import sqlite3
    db=tmp_path/'observations.sqlite'
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(db))
    monkeypatch.delenv('NOVA_KIS_ARCHIVE_ENABLED',raising=False)
    report={'verified':True,'regular_volume_verified':True,'verification_basis':'KIS_REGULAR_MINUTE','latest_date':'2026-11-24'}
    assert not archive_verified('005930.KS',bars(),report)
    assert not db.exists()
    monkeypatch.setenv('NOVA_KIS_ARCHIVE_ENABLED','1')
    assert archive_verified('005930.KS',bars(),report)
    assert not archive_verified('005930.KS',bars(),report)
    assert not archive_verified('005930.KS',bars(),{**report,'verified':False})
    with sqlite3.connect(db) as c:assert c.execute('select count(*) from nova_kis_daily_observations').fetchone()[0]==1
