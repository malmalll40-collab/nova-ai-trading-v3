"""Frozen 3-year research cohort status. Read-only; never authorizes collection."""
from datetime import date,timedelta
from nova_v574_data_audit import PILOT_DAYS,session_dates
from nova_v576_expanded_coverage import corrected_sessions,SYMBOLS
from research_history_run_control import read_status
from krx_calendar_verified import CalendarUnavailable

END=date(2026,11,25)
START=END-timedelta(days=PILOT_DAYS-1)

def probe(dsn,*,calendar=session_dates,reader=read_status):
    base={'start':START.isoformat(),'end':END.isoformat(),
          'requested_streams':len(SYMBOLS),'research_only':True,
          'execution_authorized':False,'strategy_adoption':'HOLD','orders_enabled':False,
          'training_ready':False,'historical_calendar_fully_verified':False,
          'point_in_time_universe_verified':False}
    if not isinstance(dsn,str) or not dsn.startswith(('postgresql://','postgres://')):
        return {**base,'status':'UNAVAILABLE','reason':'POSTGRES_DSN_REQUIRED'}
    try:
        sessions,audit=corrected_sessions(calendar(START,END),START,END)
    except CalendarUnavailable as exc:
        code=exc.code if exc.code in ('XKRX_RANGE_INVALID','XKRX_IMPORT_UNAVAILABLE','XKRX_HISTORY_LOAD_FAILED') else 'XKRX_UNKNOWN'
        return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_UNAVAILABLE','calendar_error_code':code}
    except Exception:
        return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_UNAVAILABLE','calendar_error_code':'XKRX_UNKNOWN'}
    try:
        r=reader(dsn,sessions)
        if r.get('status') not in ('NOT_INITIALIZED','PARTIAL','COVERAGE_COMPLETE','UNAVAILABLE'):
            raise ValueError('INVALID_STATUS')
        # Explicit output allowlist: never log DSNs, raw rows or exceptions.
        summary={k:r[k] for k in ('dataset','status','reason','stored_rows','complete_streams') if k in r}
        return {**summary,**base,'expected_sessions':len(sessions),'expected_rows':len(sessions)*len(SYMBOLS),
                'calendar_corrections':audit['corrections_applied']}
    except Exception:
        return {**base,'status':'UNAVAILABLE','reason':'STATUS_READ_FAILED'}
