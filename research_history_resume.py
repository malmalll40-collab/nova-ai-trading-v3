"""Bounded, explicitly invoked research history resume. No production wiring.

The caller supplies a dedicated commit/rollback transaction factory and an
admission guard. Neither a local-clear snapshot nor this module grants access.
No automatic scheduling, order path, or strategy adoption.
"""
from datetime import datetime
import time
from nova_v574_data_audit import KST, collect, ProbeError, MAX_PAGES, MAX_INDEX_PAGES, INDEX_CODES, SAFE_ADMISSION_STOPS
from nova_v576_expanded_coverage import GROUPS, GroupBudget, GuardedBudget, MAX_CALLS, MAX_SECONDS, GROUP_PAUSE_SECONDS, quiet_window
from research_history_checkpoint import initialize, missing_windows, append, manifest, saved

SAFE_PARTIAL = frozenset(('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT','BUDGET_EXHAUSTED',
                          'GROUP_BUDGET_EXHAUSTED','FOREGROUND_BUSY','PROVIDER_UNHEALTHY','QUIET_WINDOW_ENDED')) | SAFE_ADMISSION_STOPS
MAX_WINDOWS = 22

def resume(api, sessions, *, transaction, now, calendar, admission, pause=time.sleep,
           max_calls=MAX_CALLS, max_seconds=MAX_SECONDS, max_windows=MAX_WINDOWS):
    """One bounded pass; missing windows plus adjacent saved-session anchors.

    transaction() yields (cursor, backend), commits on normal exit and rolls
    back on exceptions. No transaction is held across a provider request;
    a caller may keep a dedicated session lock open.
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
    rows=0; windows=0; completed=0; anchors_checked=0
    def result(status,reason):
        return {'status':status,'reason':reason,'dataset':dataset,'new_rows':rows,
                'logical_calls':b.calls,'windows_attempted':windows,'complete_streams':completed,
                'research_only':True,'training_ready':False,'adjustment_basis_verified_across_runs':False,
                'overlap_anchors_checked':anchors_checked,'overlap_scope':'ADJACENT_SESSIONS_ONLY',
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
                    with transaction() as (cur,kind):
                        prior=saved(cur,kind,dataset,symbol)
                    if any(d in prior for d in gap):
                        return result('PARTIAL','CHECKPOINT_CHANGED')
                    lo=sessions.index(gap[0]);hi=sessions.index(gap[-1])
                    anchors=tuple(d for d in (
                        sessions[lo-1] if lo else None,
                        sessions[hi+1] if hi+1<len(sessions) else None
                    ) if d in prior)
                    if prior and not anchors:
                        return result('PARTIAL','OVERLAP_ANCHOR_UNAVAILABLE')
                    requested=tuple(sorted((*gap,*anchors)))
                    stats,records=collect(api,symbol,requested[0],requested[-1],requested,gb,
                        max_pages=MAX_INDEX_PAGES if symbol in INDEX_CODES else MAX_PAGES,pause=pause)
                    reason=stats['reason']
                    if reason not in SAFE_PARTIAL or not set(records)<=set(requested):
                        return result('PARTIAL','WINDOW_INTEGRITY_REJECTED')
                    if any(d in records and records[d]!=prior[d] for d in anchors):
                        return result('PARTIAL','ADJUSTED_HISTORY_OVERLAP_CONFLICT')
                    if any(d not in records for d in anchors):
                        return result('PARTIAL','OVERLAP_ANCHOR_MISSING')
                    anchors_checked+=len(anchors)
                    if records:
                        with transaction() as (cur,kind):
                            if saved(cur,kind,dataset,symbol)!=prior:
                                raise ValueError('CHECKPOINT_CHANGED')
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
