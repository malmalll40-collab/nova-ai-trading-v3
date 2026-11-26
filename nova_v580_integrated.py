"""Integration of split audit and local research admission; no live activation."""
from datetime import datetime, timezone
import time
from nova_v577_split_coverage import run_group
from nova_v579_admission import (QuotaEvidence, ResearchHistoryClient, ResearchBlocked,
                                  admission_reason)

VERSION='5.8.0'
PAGE_PAUSE=5.5
MAX_SLICE_SECONDS=180

def run_slice(kis,checkpoint,*,observer,proof_source=None,
              now=lambda:datetime.now(timezone.utc),clock=time.monotonic,
              pause=time.sleep,progress=None):
    """One stream per slice, retaining completed audit metadata only.

    A trusted coordinator must provide one fresh reservation per page.
    Without it the local adapter cannot send requests. Neither raw history nor
    checkpoints are persisted. This is not a live endpoint or scheduler.
    """
    pending=[None]
    blocked=[None]
    def sample():
        pending[0]=None
        local=observer.snapshot()
        result={**local,'verified':False,'physical_headroom':0}
        if proof_source is None:return result
        try:
            proof=proof_source()
            reason=admission_reason(proof,now(),foreground=local['foreground_active'],
                physical=local['physical_active'],research=local['research_active'],
                errors=local['recent_provider_errors'],overflow=local['window_overflow'],
                starts=(),monotonic_now=clock())
        except Exception:reason='ACCOUNT_QUOTA_UNVERIFIED'
        if reason:
            blocked[0]=reason
            return result
        pending[0]=proof
        return {**local,'verified':True,'physical_headroom':proof.physical_headroom,
                'at':proof.observed_at.isoformat()}
    def consume():
        p=pending[0];pending[0]=None
        return p
    adapter=ResearchHistoryClient(kis,observer,consume)
    class Client:
        def get(self,*args):
            try:return adapter.get(*args)
            except ResearchBlocked as exc:
                blocked[0]=str(exc)
                raise
    result=run_group(Client(),checkpoint,telemetry=sample,now=now,clock=clock,
                     pause=pause,progress=progress,max_streams=1,
                     page_pause=PAGE_PAUSE,max_seconds=MAX_SLICE_SECONDS)
    if result['status']=='PAUSED' and blocked[0] and result['reason'] in ('TELEMETRY_UNVERIFIED','PROVIDER_REQUEST_FAILED'):
        result['reason']=blocked[0]
    return {**result,'version':VERSION,'quota_coordinator_installed':False,
            'scope':'INJECTED_RESEARCH_ONLY','live_activation':False,
            'page_pause_seconds':PAGE_PAUSE}
