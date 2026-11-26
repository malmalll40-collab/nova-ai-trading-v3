"""Offline verified-cache admission, expiry, isolation and mutation safety."""
from datetime import datetime
from copy import deepcopy
import pandas as pd
import pytest
from data_sources import demo_data
from nova_v593_scan_cache import VerifiedScanCache,KST

def sample(symbol='005930.KS',asof='20261125'):
    m=demo_data('스윙');m.symbol=symbol;m.synthetic=False;m.quote=None
    day=datetime.strptime(asof,'%Y%m%d').date()
    m.bars.index=pd.bdate_range(end=day,periods=len(m.bars))
    m.daily_integrity={'scan_asof':asof,'verified':True,'signal_hold':False,
        'regular_volume_verified':True,'verification_basis':'KIS_REGULAR_MINUTE',
        'latest_date':day.isoformat(),'raw_scan_freshness':{'stale':False}}
    return m

NOW=datetime(2026,11,26,16,0,tzinfo=KST)
ASOF='20261125'

def test_verified_cache_hit_and_defensive_copy():
    cache=VerifiedScanCache();calls=[]
    def load():calls.append(1);return sample()
    first=cache.fetch('005930.KS',ASOF,'a',load,now=NOW,clock=lambda:100.)
    assert first.daily_integrity['scan_cache']['state']=='MISS'
    price=first.bars.iloc[-1].Close
    first.bars.iloc[-1,first.bars.columns.get_loc('Close')]=-10
    second=cache.fetch('005930.KS',ASOF,'a',load,now=NOW,clock=lambda:120.)
    assert second.daily_integrity['scan_cache']=={'state':'HIT','age_seconds':20.}
    assert second.bars.iloc[-1].Close==price and calls==[1]

def test_expiry_and_account_isolation():
    cache=VerifiedScanCache(ttl=300);calls=[]
    def load():calls.append(1);return sample()
    for scope,t in [('a',0),('b',1),('a',300)]:
        assert cache.fetch('005930.KS',ASOF,scope,load,now=NOW,clock=lambda:t).daily_integrity['scan_cache']['state']=='MISS'
    assert len(calls)==3

@pytest.mark.parametrize('field,value',[('verified',False),('signal_hold',True),('regular_volume_verified',False),('verification_basis','OTHER'),('raw_scan_freshness',{'stale':True})])
def test_unverified_never_cached(field,value):
    cache=VerifiedScanCache()
    def load():
        m=sample();m.daily_integrity[field]=value;return m
    for _ in range(2):
        assert cache.fetch('005930.KS',ASOF,'a',load,now=NOW).daily_integrity['scan_cache']['state']=='BYPASS'
    assert not cache._entries

def test_current_day_never_cached():
    cache=VerifiedScanCache()
    for _ in range(2):
        m=cache.fetch('005930.KS','20261126','a',lambda:sample(asof='20261126'),now=NOW)
        assert m.daily_integrity['scan_cache']['state']=='BYPASS'
    assert not cache._entries

def test_session_boundary_and_day_invalidate_reuse():
    cache=VerifiedScanCache()
    for now in (datetime(2026,11,26,15,29,tzinfo=KST),NOW,datetime(2026,11,27,16,0,tzinfo=KST)):
        assert cache.fetch('005930.KS',ASOF,'a',sample,now=now).daily_integrity['scan_cache']['state']=='MISS'

def test_capacity_bound_and_lru():
    cache=VerifiedScanCache(capacity=1)
    for symbol in ('005930.KS','000660.KS','005930.KS'):
        assert cache.fetch(symbol,ASOF,'a',lambda:sample(symbol),now=NOW).daily_integrity['scan_cache']['state']=='MISS'
        assert len(cache._entries)==1

def test_loader_failure_never_cached():
    cache=VerifiedScanCache()
    def fail():raise RuntimeError('fixture')
    with pytest.raises(RuntimeError):cache.fetch('005930.KS',ASOF,'a',fail,now=NOW)
    assert not cache._entries

def test_slow_loader_not_reused_past_ttl():
    cache=VerifiedScanCache(ttl=5)
    ticks=iter([0.,0.,6.])
    cache.fetch('005930.KS',ASOF,'a',sample,now=NOW,clock=lambda:next(ticks))
    assert not cache._entries
