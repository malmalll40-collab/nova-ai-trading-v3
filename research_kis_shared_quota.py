"""Explicit, offline-testable shared KIS quota reservation ledger.

Not installed in production: all physical consumers must use this ledger,
the provider's actual entitlement must be independently confirmed, and
schema initialization must be explicitly authorized. Never issues quota proof.
Reservations count from admission through completion plus a one-second
buffer. Unfinished reservations remain blocking; no unsafe lease expiry.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

TABLE='nova_kis_shared_quota_v1'
LOCK_KEY=601_576_003
BUFFER_SECONDS=1.1
RESEARCH_RESERVE=2

@dataclass(frozen=True)
class Claim:
    lease_id:str
    kind:str

class SharedQuotaLedger:
    def __init__(self,dsn,*,connect=None):
        if connect is None:
            import psycopg
            connect=psycopg.connect
        self._dsn=dsn;self._connect=connect

    @contextmanager
    def _tx(self):
        with self._connect(self._dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3s'")
                cur.execute("SET LOCAL lock_timeout = '1s'")
                cur.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_KEY,))
                yield cur

    def initialize(self):
        """Explicit schema operation; never called by claim, finish or startup."""
        with self._tx() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                lease_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('foreground','research')),
                finished_at TIMESTAMPTZ NULL)""")

    def claim(self,kind,limit):
        """Atomic, non-waiting policy decision after short bounded DB lock.

        limit is an externally confirmed conservative cap, not a value this
        module discovers. No caller can convert this claim into account proof.
        A successful claim must precede exactly one physical request; finish
        only after it returns. All processes must use the same DB and cap.
        """
        if kind not in ('foreground','research') or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('INVALID_QUOTA_POLICY')
        with self._tx() as cur:
            cur.execute(f"DELETE FROM {TABLE} WHERE finished_at IS NOT NULL AND finished_at <= clock_timestamp() - (%s * interval '1 second')",(BUFFER_SECONDS,))
            cur.execute(f"SELECT count(*), count(*) FILTER (WHERE kind='foreground' AND finished_at IS NULL), count(*) FILTER (WHERE kind='research' AND finished_at IS NULL) FROM {TABLE}")
            used,foreground,research=cur.fetchone()
            if used>=limit:
                return None
            if kind=='research' and (foreground or research or limit-used<=RESEARCH_RESERVE):
                return None
            lease=Claim(uuid4().hex,kind)
            cur.execute(f'INSERT INTO {TABLE}(lease_id,kind) VALUES (%s,%s)',(lease.lease_id,kind))
            return lease

    def finish(self,lease):
        """Idempotence: repeated finish cannot shorten the safety buffer."""
        if not isinstance(lease,Claim):raise ValueError('INVALID_LEASE')
        with self._tx() as cur:
            cur.execute(f'UPDATE {TABLE} SET finished_at=clock_timestamp() WHERE lease_id=%s AND kind=%s AND finished_at IS NULL',(lease.lease_id,lease.kind))
            return cur.rowcount==1
