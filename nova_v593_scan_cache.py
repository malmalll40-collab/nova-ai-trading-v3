"""Bounded process-local reuse of strictly verified historical scan inputs.

Never cache failures, current-day data, recommendations or finance decisions.
No persistence, no credentials in values, no provider call on a valid cache hit.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo
import time
import pandas as pd

KST=ZoneInfo('Asia/Seoul')

class VerifiedScanCache:
    def __init__(self, ttl=300, capacity=128):
        self.ttl=ttl
        self.capacity=capacity
        self._lock=Lock()
        self._entries=OrderedDict()

    @staticmethod
    def _eligible(market,symbol,asof):
        try:
            report=market.daily_integrity or {}
            raw=report.get('raw_scan_freshness') or {}
            cutoff=datetime.strptime(asof,'%Y%m%d').date()
            return (market.symbol==symbol and market.interval=='스윙' and not market.synthetic
                and market.quote is None and report.get('scan_asof')==asof
                and report.get('verified') is True and report.get('signal_hold') is False
                and report.get('regular_volume_verified') is True
                and report.get('verification_basis')=='KIS_REGULAR_MINUTE'
                and report.get('latest_date')==cutoff.isoformat()
                and raw.get('stale') is False and len(market.bars)>=110
                and market.bars.index.is_unique and market.bars.index.is_monotonic_increasing
                and pd.Timestamp(market.bars.index[-1]).date()==cutoff)
        except (TypeError,ValueError,AttributeError,IndexError):
            return False

    def fetch(self,symbol,asof,account_scope,loader,*,now=None,clock=time.monotonic):
        moment=(now or datetime.now(KST)).astimezone(KST)
        cutoff=datetime.strptime(asof,'%Y%m%d').date()
        # Session boundary partitions: never carry pre-close raw coverage over
        # into post-close/settlement, and never cross a Korean calendar day.
        from market_sessions import SPECIAL_HOURS
        opening,closing,_=SPECIAL_HOURS.get(moment.date().isoformat(),('09:00','15:30',''))
        hm=moment.strftime('%H:%M')
        phase=0 if hm<opening else 1 if hm<closing else 2 if hm<'18:00' else 3 if hm<'20:00' else 4
        key=(account_scope,symbol,asof,moment.date(),phase)
        use_cache=cutoff<moment.date()
        if use_cache:
            with self._lock:
                stamp=clock()
                for k,(t,_) in list(self._entries.items()):
                    if not 0<=stamp-t<self.ttl:self._entries.pop(k,None)
                rec=self._entries.get(key)
                if rec is not None:
                    self._entries.move_to_end(key)
                    market=deepcopy(rec[1])
                    market.daily_integrity['scan_cache']={'state':'HIT','age_seconds':round(stamp-rec[0],2)}
                    return market
        # Don't hold the cache lock across a broker request.
        started=clock()
        market=loader()
        if use_cache and self._eligible(market,symbol,asof):
            with self._lock:
                stamp=clock()
                if 0<=stamp-started<self.ttl:
                    self._entries[key]=(started,deepcopy(market))
                    self._entries.move_to_end(key)
                    while len(self._entries)>self.capacity:self._entries.popitem(last=False)
            market.daily_integrity['scan_cache']={'state':'MISS','age_seconds':0}
        elif isinstance(market.daily_integrity,dict):
            market.daily_integrity['scan_cache']={'state':'BYPASS','age_seconds':None}
        return market

CACHE=VerifiedScanCache()
