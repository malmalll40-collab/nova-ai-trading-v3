from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
import pytest
from nova_v578_kis_telemetry import LocalTelemetry
from nova_v579_admission import QuotaEvidence,ResearchBlocked
from research_kis_transport_gate import SharedRequestGate

NOW=datetime(2026,11,26,tzinfo=timezone.utc)

class Ledger:
    def __init__(self):
        self.claims=[];self.finished=[];self.busy=False;self.fail=False
    def claim(self,kind,limit):
        self.claims.append((kind,limit))
        return None if self.busy else len(self.claims)
    def finish(self,lease):
        self.finished.append(lease)
        if self.fail:raise RuntimeError('secret db detail')
        return True

def proof():
    return QuotaEvidence(NOW,4,True,True)

def test_foreground_dispatch_claims_and_finishes_one_slot():
    db=Ledger();t=LocalTelemetry(shared_gate=SharedRequestGate(db,4))
    with t.physical(token=True):
        assert db.claims==[('foreground',4)] and db.finished==[]
        assert t.snapshot()['physical_active']==1
    assert db.finished==[1] and t.snapshot()['physical_attempts_total']==1

def test_refused_slot_never_counts_or_dispatches():
    db=Ledger();db.busy=True;t=LocalTelemetry(shared_gate=SharedRequestGate(db,4))
    with pytest.raises(ResearchBlocked,match='SHARED_QUOTA_BUSY'):
        with t.physical():pytest.fail('dispatch must be blocked')
    assert t.snapshot()['physical_attempts_total']==0 and not db.finished

def test_uncertain_dispatch_retains_slot():
    db=Ledger();t=LocalTelemetry(shared_gate=SharedRequestGate(db,4))
    with pytest.raises(TimeoutError):
        with t.physical():raise TimeoutError('unknown provider outcome')
    assert not db.finished and t.snapshot()['physical_active']==0
    assert t.snapshot()['recent_provider_errors']==1

def test_research_requires_independent_evidence_before_ledger():
    db=Ledger();t=LocalTelemetry(wall=lambda:NOW,shared_gate=SharedRequestGate(db,4))
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_UNVERIFIED'):
        with t.research_attempt(None):pytest.fail('dispatch')
    assert not db.claims

def test_research_rechecks_after_shared_claim_and_releases_if_stale():
    db=Ledger();times=iter([NOW,NOW+timedelta(seconds=6)])
    t=LocalTelemetry(wall=lambda:next(times),shared_gate=SharedRequestGate(db,4))
    with pytest.raises(ResearchBlocked,match='ACCOUNT_QUOTA_STALE'):
        with t.research_attempt(proof()):pytest.fail('dispatch')
    assert db.claims==[('research',4)] and db.finished==[1]
    assert t._total==0

def test_research_success_is_counted_once():
    db=Ledger();t=LocalTelemetry(wall=lambda:NOW,shared_gate=SharedRequestGate(db,4))
    with t.research_attempt(proof()):
        assert t.snapshot()['research_active']==1
    assert db.claims==[('research',4)] and db.finished==[1]
    assert t.snapshot()['research_attempts_total']==1
    assert not t.snapshot()['verified']

def test_release_failure_is_sanitized_without_reissuing():
    db=Ledger();db.fail=True;t=LocalTelemetry(shared_gate=SharedRequestGate(db,4))
    with pytest.raises(ResearchBlocked,match='SHARED_QUOTA_RELEASE_UNVERIFIED') as exc:
        with t.physical():pass
    assert 'secret' not in str(exc.value) and db.claims==[('foreground',4)]
