"""Optional shared transport admission. No production installation or quota proof."""
from contextlib import contextmanager
from nova_v579_admission import ResearchBlocked

class SharedRequestGate:
    def __init__(self,ledger,limit):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('INVALID_QUOTA_POLICY')
        self._ledger=ledger;self._limit=limit

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
        try:lease=self._ledger.claim(kind,self._limit)
        except Exception:raise ResearchBlocked('SHARED_QUOTA_UNAVAILABLE') from None
        if lease is None:raise ResearchBlocked('SHARED_QUOTA_BUSY')
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
