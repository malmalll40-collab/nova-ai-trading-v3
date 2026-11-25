"""Research-only expanded KIS coverage audit; no persistence, model fitting, or live signals."""
from collections import Counter
from datetime import date, datetime, time as walltime, timedelta
import time
from nova_v574_data_audit import (KST, PILOT_DAYS, INDEX_CODES, MAX_INDEX_PAGES,
    MAX_PAGES, Budget, ProbeError, collect, session_dates)
from nova_v560_unified import PRESET

VERSION='5.7.6'
# Exact dated closures announced by KRX on 2026-05-20, reported by Yonhap.
# Do not extrapolate recurring holidays or silently infer missing sessions.
CLOSURES={date(2026,6,3):'LOCAL_ELECTION',date(2026,7,17):'CONSTITUTION_DAY'}
CLOSURE_SOURCE='https://www.yna.co.kr/amp/view/AKR20260520064251008'
SYMBOLS=(*INDEX_CODES,*PRESET)
MAX_CALLS=208
MAX_SECONDS=300
MAX_GAP_DATES=20


def corrected_sessions(raw,start,end):
    original=tuple(raw)
    if (not original or any(type(d) is not date or not start<=d<=end for d in original)
            or len(set(original))!=len(original)):
        raise ValueError('CALENDAR_INVALID')
    removed=sorted(set(original)&set(CLOSURES))
    expected=tuple(sorted(set(original)-set(removed)))
    if not expected:raise ValueError('CALENDAR_EMPTY')
    return expected,{'original_sessions':len(original),'expected_sessions':len(expected),
        'corrections_applied':len(removed),
        'removed_dates':[{'date':d.isoformat(),'reason':CLOSURES[d]} for d in removed],
        'closure_source':CLOSURE_SOURCE,
        'scope':'RESEARCH_ONLY','calendar_fully_verified':False}


def quiet_window(now,schedule):
    """Leave a buffer around market hours and prospective collection."""
    if now.tzinfo is None:return False
    if not schedule.get('calendar_verified') or schedule.get('status')!='CLOSED':return False
    here=now.astimezone(KST).time()
    if not schedule.get('open_kst') and not schedule.get('close_kst'):
        return True # verified non-session
    if not schedule.get('open_kst') or not schedule.get('close_kst'):return False
    return here<=walltime(8,45) or here>=walltime(19,0)


class GuardedBudget(Budget):
    def __init__(self,guard,**kw):
        super().__init__(**kw)
        self.guard=guard
    def take(self):
        if not self.guard():raise ProbeError('QUIET_WINDOW_ENDED')
        super().take()


def pilot(api,now=None,*,calendar=session_dates,budget=None,pause=time.sleep,progress=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:raise ValueError('timezone required')
    end=now.astimezone(KST).date()-timedelta(days=1)
    start=end-timedelta(days=PILOT_DAYS-1)
    b=budget or Budget(max_calls=MAX_CALLS,max_seconds=MAX_SECONDS)
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD',
        'historical_session_verified':False,'point_in_time_universe_verified':False,
        'persistent_storage':False,'training_performed':False,
        'start':start.isoformat(),'end':end.isoformat(),'requested_streams':len(SYMBOLS),
        'requested_stocks':len(PRESET),'logical_call_limit':b.max_calls,
        'price_basis':'KIS_NATIVE_ADJUSTED_HISTORY',
        'limitations':'Fixed current preset, survivorship bias; coverage is not point-in-time listing or raw session verification. No storage, training, orders. Logical GETs exclude provider retries.'}
    try:expected,audit=corrected_sessions(calendar(start,end),start,end)
    except Exception:return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_UNAVAILABLE','streams':[],'logical_calls':0}
    stats=[]; common=None; union=set(); rows=0; stock_rows=0; turnover=0;quality_ready=0
    stop=None
    for s in SYMBOLS:
        item,records=collect(api,s,start,end,expected,b,
            max_pages=MAX_INDEX_PAGES if s in INDEX_CODES else MAX_PAGES,pause=pause)
        gaps=set(expected)-set(records)
        item['missing_dates']=[d.isoformat() for d in sorted(gaps)[:MAX_GAP_DATES]]
        item['missing_dates_truncated']=len(gaps)>MAX_GAP_DATES
        conflicts=set(records)&set(CLOSURES)
        item['official_closure_conflicts']=[d.isoformat() for d in sorted(conflicts)]
        if conflicts:item['status']='PARTIAL'
        n=item['observed_sessions'];rows+=n
        if s not in INDEX_CODES:
            stock_rows+=n;turnover+=item['turnover_available'] or 0
            # No imputation. Coverage alone does not certify training readiness.
            item['liquidity_fields_complete']=(item['status']=='READY' and
                item['turnover_available']==n and item['zero_volume_sessions']==0 and
                item['duplicate_rows']==0)
            quality_ready+=int(item['liquidity_fields_complete'])
        stats.append(item)
        common=gaps if common is None else common&gaps
        union|=gaps
        if progress:progress(dict(item))
        # Records are discarded after their audit, never written to disk/DB.
        if item['reason'] not in ('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT'):
            stop=item['reason'];break
    counts=Counter(x['status'] for x in stats)
    full=len(stats)==len(SYMBOLS) and all(x['reason']=='WINDOW_REACHED' for x in stats)
    return {**base,'status':'READY' if counts['READY']==len(SYMBOLS) else 'PARTIAL' if rows else 'UNAVAILABLE',
        'reason':stop,'calendar':audit,'expected_sessions':len(expected),
        'finished_streams':len(stats),'ready_streams':counts['READY'],
        'expected_rows':len(expected)*len(SYMBOLS),'observed_rows':rows,
        'stock_rows':stock_rows,'turnover_available':turnover,
        'liquidity_fields_complete_stocks':quality_ready,'logical_calls':b.calls,
        'all_windows_reached':full,
        'common_missing_dates':[d.isoformat() for d in sorted(common or set())[:MAX_GAP_DATES]] if full else [],
        'common_missing_count':len(common or set()) if full else None,
        'any_missing_count':len(union),'status_counts':dict(counts),'streams':stats}
