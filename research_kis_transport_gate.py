"""Optional shared transport admission. No production installation or quota proof."""
from contextlib import contextmanager
from dataclasses import dataclass
import time

@dataclass(frozen=True)
class LocalPolicyPermit:
    gate:object
    issued:float
from nova_v579_admission import ResearchBlocked

class SharedRequestGate:
    def __init__(self,ledger,limit,*,operational_policy=False,clock=time.monotonic,pause=time.sleep):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('INVALID_QUOTA_POLICY')
        self._ledger=ledger;self._limit=limit
        self._policy=operational_policy is True
        self._clock=clock;self._pause=pause

    def reserve(self):
        # This is an internal permit, NOT a provider quota entitlement or DB slot.
        return LocalPolicyPermit(self,self._clock()) if self._policy else None

    def accepts(self,permit):
        return (self._policy and isinstance(permit,LocalPolicyPermit) and permit.gate is self
                and 0<=self._clock()-permit.issued<=5.0)

    def _finish(self,lease):
        try:
            if self._ledger.finish(lease) is not True:
                raise RuntimeError('LEASE_RELEASE_UNVERIFIED')
        except Exception:
            raise ResearchBlocked('SHARED_QUOTA_RELEASE_UNVERIFIED') from None

    @contextmanager
    def slot(self,kind):
        """Reserve before physical I/O; a failed/ambiguous dispatch remains held.

        Call start exactly once immediately before dispatch. A local refusal
        before dispatch releases the reservation. No unsafe retry or lease expiry.
        """
        # In rollout mode foreground requests may wait briefly for capacity;
        # research never waits for the ledger. No provider call before a claim.
        deadline=self._clock()+4.0
        while True:
            try:lease=self._ledger.claim(kind,self._limit)
            except Exception:raise ResearchBlocked('SHARED_QUOTA_UNAVAILABLE') from None
            if lease is not None:break
            if not self._policy or kind!='foreground' or self._clock()>=deadline:
                raise ResearchBlocked('SHARED_QUOTA_BUSY')
            self._pause(min(.25,max(0.,deadline-self._clock())))
        started=False
        def start():
            nonlocal started
            if started:raise ResearchBlocked('SHARED_QUOTA_DUPLICATE_DISPATCH')
            started=True
        try:
            yield start
        except BaseException:
            if not started:
                self._finish(lease)
            # On ambiguous physical failure retain the lease until independently
            # reconciled. An HTTP/business error after dispatch can also block.
            raise
        else:
            self._finish(lease)
