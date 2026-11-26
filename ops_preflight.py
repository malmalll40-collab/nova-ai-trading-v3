"""Read-only operations visibility. Local snapshots are not an admission grant."""
from nova_v576_expanded_coverage import quiet_window

TERMINAL = frozenset(('idle','complete','completed','partial','failed','skipped','cancelled','canceled'))
JOBS = ('scan_full','scan_pattern','backtest','popular','autotrack','prospective','batch','diagnostics')
COUNTERS = ('foreground_active','physical_active','research_active','physical_attempts_60s','recent_provider_errors')

def snapshot(now, schedule, load, jobs):
    window = quiet_window(now, schedule)
    states = {k: jobs.get(k) if jobs.get(k) in TERMINAL or jobs.get(k) in ('running','cancelling','canceling','queued','pending') else 'UNKNOWN' for k in JOBS}
    counts = {k: load.get(k) if type(load.get(k)) is int and load[k] >= 0 else None for k in COUNTERS}
    reasons = []
    if not window:
        reasons.append('QUIET_WINDOW_OR_CALENDAR_UNVERIFIED')
    if any(v not in TERMINAL for v in states.values()):
        reasons.append('JOB_BUSY_OR_UNKNOWN')
    if load.get('scope') != 'PROCESS_LOCAL' or any(v != 0 for v in counts.values()) or load.get('window_overflow') is not False:
        reasons.append('LOCAL_KIS_BUSY_OR_UNVERIFIED')
    return {
        'as_of': now.isoformat(timespec='seconds'),
        'krx_regular_status': schedule.get('status') if schedule.get('status') in ('OPEN','CLOSED') else 'UNKNOWN',
        'calendar_verified': schedule.get('calendar_verified') is True,
        'quiet_window': window,
        'after_hours_basis': 'SCHEDULE_BUFFER_ONLY_NOT_LIVE_EXCHANGE_STATUS',
        'jobs': states,
        'kis_load': counts,
        'window_overflow': load.get('window_overflow') if type(load.get('window_overflow')) is bool else None,
        'scope': 'PROCESS_LOCAL_NON_ATOMIC',
        'account_quota_verified': False,
        'shared_admission_verified': False,
        'local_preflight': 'BLOCKED' if reasons else 'LOCAL_CLEAR',
        'reasons': reasons,
        'execution_authorized': False,
        'orders_enabled': False,
    }
