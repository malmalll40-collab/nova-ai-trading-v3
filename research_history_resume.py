"""Bounded, explicitly invoked research history resume. No production wiring.

The caller supplies a dedicated commit/rollback transaction factory and an
admission guard. Neither a local-clear snapshot nor this module grants access.
No automatic scheduling, order path, or strategy adoption.
"""
from datetime import datetime
import time
from nova_v574_data_audit import KST, collect, ProbeError, MAX_PAGES, MAX_INDEX_PAGES, INDEX_CODES
from nova_v576_expanded_coverage import GROUPS, GroupBudget, GuardedBudget, MAX_CALLS, MAX_SECONDS, GROUP_PAUSE_SECONDS, quiet_window
from research_history_checkpoint import initialize, missing_windows, append, manifest

SAFE_PARTIAL = frozenset(('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT','BUDGET_EXHAUSTED',
                          'GROUP_BUDGET_EXHAUSTED','FOREGROUND_BUSY','PROVIDER_UNHEALTHY','QUIET_WINDOW_ENDED'))
MAX_WINDOWS = 22

def resume(api, sessions, *, transaction, now, calendar, admission, pause=time.sleep,
           max_calls=MAX_CALLS, max_seconds=MAX_SECONDS, max_windows=MAX_WINDOWS):
    """One bounded pass; only expected sessions in missing windows are requested.

    transaction() yields (cursor, backend), commits on normal exit and rolls
    back on exceptions. No connection is held across a provider request.
    """
    if (type(max_calls) is not int or not 1<=max_calls<=MAX_CALLS
            or type(max_seconds) is not int or not 1<=max_seconds<=MAX_SECONDS
            or type(max_windows) is not int or not 1<=max_windows<=MAX_WINDOWS):
        raise ValueError('INVALID_BUDGET')
    sessions=tuple(sessions)
    dataset,_=manifest(sessions)
    def guard():
        t=now()
        return (quiet_window(t,calendar(t)) and sessions[-1]<t.astimezone(KST).date()
                and admission() is True)
    b=GuardedBudget(guard,max_calls=max_calls,max_seconds=max_seconds)
    rows=0; windows=0; completed=0; stop=None
    def result(status,reason):
        return {'status':status,'reason':reason,'dataset':dataset,'new_rows':rows,
                'logical_calls':b.calls,'windows_attempted':windows,'complete_streams':completed,
                'research_only':True,'training_ready':False,'adjustment_basis_verified_across_runs':False,
                'strategy_adoption':'HOLD','orders_enabled':False}
    try:
        if not guard():
            return result('BLOCKED','ADMISSION_OR_WINDOW_UNVERIFIED')
        with transaction() as (cur,kind):
            initialize(cur,kind,sessions)
        for group_no,group in enumerate(GROUPS):
            if group_no:
                if not guard():
                    return result('PARTIAL','ADMISSION_OR_WINDOW_UNVERIFIED')
                pause(GROUP_PAUSE_SECONDS)
            gb=GroupBudget(b,group)
            for symbol in group:
                if not guard():
                    return result('PARTIAL','ADMISSION_OR_WINDOW_UNVERIFIED')
                with transaction() as (cur,kind):
                    gaps=missing_windows(cur,kind,dataset,symbol)
                for gap in gaps:
                    if windows>=max_windows or b.calls>=b.max_calls or b.clock()>=b.deadline:
                        return result('PARTIAL','PASS_LIMIT')
                    if not guard():
                        return result('PARTIAL','ADMISSION_OR_WINDOW_UNVERIFIED')
                    windows+=1
                    stats,records=collect(api,symbol,gap[0],gap[-1],gap,gb,
                        max_pages=MAX_INDEX_PAGES if symbol in INDEX_CODES else MAX_PAGES,pause=pause)
                    reason=stats['reason']
                    # Earlier validated pages can survive a clean interruption.
                    # Reject entire window on malformed/conflicting provider rows.
                    if reason not in SAFE_PARTIAL or not set(records)<=set(gap):
                        return result('PARTIAL','WINDOW_INTEGRITY_REJECTED')
                    if records:
                        with transaction() as (cur,kind):
                            n=append(cur,kind,dataset,symbol,records)
                        rows+=n
                    if reason!='WINDOW_REACHED':
                        return result('PARTIAL',reason)
                with transaction() as (cur,kind):
                    if not missing_windows(cur,kind,dataset,symbol):
                        completed+=1
        return result('READY' if completed==sum(map(len,GROUPS)) else 'PARTIAL',None)
    except ProbeError:
        return result('PARTIAL','GUARD_INTERRUPTED')
    except Exception:
        # Do not leak database/provider exceptions or claim a failed transaction committed.
        return result('PARTIAL','CHECKPOINT_OR_ADMISSION_FAILED')
