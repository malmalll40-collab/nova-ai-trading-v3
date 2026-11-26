"""Passive process-local KIS telemetry. Never claims account-wide quota verification."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
import time

VERSION='5.7.8'
WINDOW=60.0
MAX_EVENTS=8192


class LocalTelemetry:
    def __init__(self,clock=time.monotonic,wall=lambda:datetime.now(timezone.utc)):
        self._clock=clock;self._wall=wall
        self._lock=threading.Lock()
        self._active=0;self._physical_active=0
        self._attempts=deque();self._errors=deque()
        self._overflow_until=0.0
        self._total=0;self._retry_total=0;self._token_total=0
        self._research_active=0;self._research_total=0
        self._research_attempts=deque()

    def _prune(self,q,now):
        while q and q[0]<=now-WINDOW:q.popleft()

    def _append(self,q,now):
        self._prune(q,now)
        if len(q)>=MAX_EVENTS:
            q.popleft()
            self._overflow_until=now+WINDOW
        q.append(now)

    @contextmanager
    def foreground(self):
        # Observe whole logical operation, including backoff and token lock wait.
        with self._lock:self._active+=1
        try:yield
        finally:
            with self._lock:self._active-=1

    @contextmanager
    def physical(self,*,retry=False,token=False):
        with self._lock:
            self._physical_active+=1;self._total+=1
            self._retry_total+=int(retry);self._token_total+=int(token)
            self._append(self._attempts,self._clock())
        try:yield
        except Exception:
            self.failure()
            raise
        finally:
            with self._lock:self._physical_active-=1

    @contextmanager
    def research_attempt(self,proof):
        """Atomic local admission; foreground never waits for research.
        Proof must come from a trusted account-wide coordinator (not installed).
        This is a per-attempt gate, not a reservation of global provider quota.
        """
        from nova_v579_admission import admission_reason, ResearchBlocked
        with self._lock:
            now=self._clock()
            self._prune(self._attempts,now);self._prune(self._errors,now)
            self._prune(self._research_attempts,now)
            reason=admission_reason(proof,self._wall(),foreground=self._active,
                physical=self._physical_active,research=self._research_active,
                errors=len(self._errors),overflow=now<self._overflow_until,
                starts=tuple(self._research_attempts),monotonic_now=now)
            if reason:raise ResearchBlocked(reason)
            self._research_active+=1;self._physical_active+=1
            self._total+=1;self._research_total+=1
            self._append(self._attempts,now);self._append(self._research_attempts,now)
        try:yield
        except Exception:
            self.failure()
            raise
        finally:
            with self._lock:
                self._physical_active-=1;self._research_active-=1

    def failure(self):
        with self._lock:self._append(self._errors,self._clock())

    def snapshot(self):
        with self._lock:
            now=self._clock()
            self._prune(self._attempts,now);self._prune(self._errors,now)
            return {'version':VERSION,'scope':'PROCESS_LOCAL','at':self._wall().isoformat(),
                'foreground_active':self._active,'physical_active':self._physical_active,
                'physical_attempts_60s':len(self._attempts),
                'recent_provider_errors':len(self._errors),
                'physical_attempts_total':self._total,'retries_total':self._retry_total,
                'token_attempts_total':self._token_total,'window_overflow':now<self._overflow_until,
                'research_active':self._research_active,'research_attempts_total':self._research_total,
                # No account entitlement, multi-process or other client visibility.
                # Never substitute local counts for global available quota.
                'verified':False,'physical_headroom':0,'foreground_waiting':0,
                'foreground_waiting_verified':False,'research_enabled':False,
                'research_block_reason':'ACCOUNT_QUOTA_AND_SHARED_ADMISSION_UNVERIFIED'}

KIS_LOAD=LocalTelemetry()
