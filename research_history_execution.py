"""Explicit fixed-cohort orchestration. Not registered with production routes.

No fabricated quota proof, token issuance, scheduler or provider retry.
A trusted installed coordinator must supply a fresh reservation for each GET.
"""
import time
from nova_v579_admission import ResearchHistoryClient,ResearchBlocked,QuotaEvidence,MIN_HEADROOM,MAX_PROOF_AGE
from nova_v576_expanded_coverage import corrected_sessions,quiet_window,MAX_CALLS,MAX_SECONDS
from nova_v574_data_audit import session_dates
from research_history_status import START,END
from research_history_run_control import run_exclusive

INTERVAL=5.1

class PacedClient:
    def __init__(self,client,guard,*,clock=time.monotonic,pause=time.sleep,deadline):
        self.client=client;self.guard=guard;self.clock=clock;self.pause=pause
        self.deadline=deadline;self.last=None

    def get(self,path,tr_id,params):
        if not self.guard():
            raise ResearchBlocked('RUN_ADMISSION_REVOKED')
        now=self.clock()
        wait=0 if self.last is None else max(0,self.last+INTERVAL-now)
        if now+wait>=self.deadline:
            raise ResearchBlocked('PASS_DEADLINE')
        if wait:
            self.pause(wait)
        if self.clock()>=self.deadline:
            raise ResearchBlocked('PASS_DEADLINE')
        if not self.guard():
            raise ResearchBlocked('RUN_ADMISSION_REVOKED')
        self.last=self.clock()
        return self.client.get(path,tr_id,params)

def execute(dsn,*,kis,telemetry,coordinator,session_mode_confirmed,now,calendar,
            historical_calendar=session_dates,clock=time.monotonic,pause=time.sleep,
            max_calls=MAX_CALLS,max_seconds=MAX_SECONDS,runner=run_exclusive):
    """Injected coordinator exposes available() and reserve() from trusted quota service.
    No production coordinator exists yet. An affirmative local snapshot is insufficient.
    """
    base={'status':'BLOCKED','new_rows':0,'logical_calls':0,'training_ready':False,
          'strategy_adoption':'HOLD','orders_enabled':False}
    if coordinator is None or not callable(getattr(coordinator,'available',None)) or not callable(getattr(coordinator,'reserve',None)):
        return {**base,'reason':'ACCOUNT_QUOTA_UNVERIFIED'}
    if session_mode_confirmed is not True:
        return {**base,'reason':'SESSION_MODE_UNVERIFIED'}
    if not getattr(kis,'token',None):
        return {**base,'reason':'TOKEN_UNAVAILABLE'}
    if type(max_calls) is not int or not 1<=max_calls<=MAX_CALLS or type(max_seconds) is not int or not 1<=max_seconds<=MAX_SECONDS:
        return {**base,'reason':'INVALID_BUDGET'}
    deadline=clock()+max_seconds
    def guard():
        try:
            t=now()
            return (clock()<deadline and quiet_window(t,calendar(t))
                    and coordinator.available() is True)
        except Exception:
            return False
    if not guard():
        return {**base,'reason':'RUN_ADMISSION_UNVERIFIED'}
    try:
        sessions,_=corrected_sessions(historical_calendar(START,END),START,END)
    except Exception:
        return {**base,'reason':'CALENDAR_UNAVAILABLE'}
    client=ResearchHistoryClient(kis,telemetry,coordinator.reserve)
    paced=PacedClient(client,guard,clock=clock,pause=pause,deadline=deadline)
    return runner(dsn,paced,sessions,now=now,calendar=calendar,admission=guard,
                  session_mode_confirmed=session_mode_confirmed,pause=pause,
                  max_calls=max_calls,max_seconds=max_seconds)
