"""Test real settlement guard ordering on synthetic KIS responses, no network."""
from datetime import datetime
from types import SimpleNamespace
import pandas as pd
import pytest
import data_sources as ds
import daily_settlement_guard as guard
import kis_daily_archive as archive
import server
from tests.test_v588_scan_efficiency import fixture

def broker_fixture(monkeypatch, *, moment=None, missing=False, conflict=False):
    moment=moment or datetime(2026,11,26,16,48,tzinfo=ds.KST)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    idx=pd.bdate_range(end='2026-11-26',periods=160)
    bars=pd.DataFrame({'Open':100.,'High':105.,'Low':99.,'Close':102.,'Volume':1000.},index=idx)
    if missing:bars=bars.drop(pd.Timestamp('2026-11-25'))
    calls=[]
    class KIS:
        def __init__(self,*a):pass
        def daily(self,*a):return bars.copy()
        def quote(self,*a):calls.append('quote');return 103.,'fixture'
        def period_bars(self,*a,**kw):return bars.copy()
    monkeypatch.setattr(ds,'datetime',Clock)
    monkeypatch.setattr(ds,'KIS',KIS)
    monkeypatch.setattr(archive,'load_regular',lambda *a:None)
    monkeypatch.setattr(guard,'independent_quotes',lambda *a,**k:(None,[]))
    def regular(api,code,day,**kw):
        calls.append(day.isoformat())
        return None if conflict else {'Open':100.,'High':105.,'Low':99.,'Close':102.,'Volume':1000.}
    monkeypatch.setattr(guard,'cached_regular_reference',regular)
    from market_sessions import KST
    import market_sessions
    monkeypatch.setattr(market_sessions,'krx_session',lambda now:{'calendar_verified':True,'close_kst':'15:30','open_kst':'09:00'})
    return calls

def test_previous_day_scan_validates_exact_cutoff_without_quote(monkeypatch):
    calls=broker_fixture(monkeypatch)
    d=ds.kis_data('005930.KS','스윙','fixture','fixture',scan_asof='20261125')
    assert d.quote is None and calls==['2026-11-25']
    assert d.bars.index[-1].date().isoformat()=='2026-11-25'
    assert d.daily_integrity['verified'] and not d.daily_integrity['signal_hold']
    assert d.daily_integrity['raw_scan_freshness']['session_gap']==1

def test_live_guard_unchanged_after_close(monkeypatch):
    calls=broker_fixture(monkeypatch)
    d=ds.kis_data('005930.KS','스윙','fixture','fixture')
    assert d.daily_integrity['state']=='SETTLEMENT_PENDING'
    assert d.daily_integrity['signal_hold'] and calls==['quote']

def test_current_day_scan_is_not_promoted_before_settlement(monkeypatch):
    calls=broker_fixture(monkeypatch)
    d=ds.kis_data('005930.KS','스윙','fixture','fixture',scan_asof='20261126')
    assert d.daily_integrity['signal_hold'] and d.daily_integrity['state']=='SETTLEMENT_PENDING'
    assert calls==[]

def test_missing_cutoff_rejected_before_reference(monkeypatch):
    calls=broker_fixture(monkeypatch,missing=True)
    with pytest.raises(RuntimeError,match='SCAN_ASOF_BAR_MISSING'):
        ds.kis_data('005930.KS','스윙','fixture','fixture',scan_asof='20261125')
    assert not calls

def test_unverified_cutoff_remains_held(monkeypatch):
    calls=broker_fixture(monkeypatch,conflict=True)
    d=ds.kis_data('005930.KS','스윙','fixture','fixture',scan_asof='20261125')
    assert d.daily_integrity['signal_hold']
    assert d.bars.index[-1].date().isoformat()!='2026-11-25'
    assert calls==['2026-11-25']

def test_raw_coverage_not_lost_after_cutoff(monkeypatch):
    m=fixture(monkeypatch)
    m.daily_integrity={'signal_hold':False,'verified':True,'scan_asof':'20261125','raw_scan_freshness':{'stale':True,'state':'PROVIDER_BEHIND_SESSIONS','reason':'fixture'}}
    r=server._payload('005930.KS','스윙','kis',15,asof_date='20261125',scan_profile='full')
    assert r['is_stale'] and r['scan_freshness']['state']=='PROVIDER_BEHIND_SESSIONS'
    assert r['final_actionable_signal'] is None
