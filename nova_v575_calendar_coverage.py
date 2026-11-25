"""Research-only coverage reconciliation. Common missing dates are not presumed holidays."""
from collections import Counter
from datetime import datetime, timedelta
import time
from nova_v574_data_audit import (KST, PILOT_DAYS, PILOT_SYMBOLS, INDEX_CODES, Budget,
    collect, session_dates, MAX_PAGES, MAX_INDEX_PAGES)

VERSION='5.7.5'
MAX_CALLS=88
MAX_SECONDS=240
SYMBOLS=(*INDEX_CODES,*PILOT_SYMBOLS)
SAFE_REASONS=('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT')
MAX_GAP_DATES=20


def reconciliation(expected, records, stats):
    """Date-only cross-check. Never fill, drop, or certify exchange closures."""
    expected=set(expected)
    ready_scope={s for s in SYMBOLS if s in stats and stats[s]['reason']=='WINDOW_REACHED'}
    complete=len(ready_scope)==len(SYMBOLS)
    gaps={s:expected-set(records[s]) for s in records}
    common=set.intersection(*(gaps[s] for s in SYMBOLS)) if complete else set()
    all_gaps=set.union(*gaps.values()) if gaps else set()
    def iso(ds):return [d.isoformat() for d in sorted(ds)[:MAX_GAP_DATES]]
    details=[]
    for day in sorted(all_gaps)[:MAX_GAP_DATES]:
        missing=[s for s in SYMBOLS if s in records and day not in records[s]]
        present=[s for s in SYMBOLS if s in records and day in records[s]]
        unchecked=[s for s in SYMBOLS if s not in records]
        classification=('COMMON_GAP_UNVERIFIED' if day in common else
                        'PROVIDER_OR_SYMBOL_GAP' if present else 'COVERAGE_INCOMPLETE')
        details.append({'date':day.isoformat(),'missing':missing,'present':present,
                        'unchecked':unchecked,'classification':classification})
    return {'all_windows_reached':complete,'common_missing_count':len(common),
            'common_missing_dates':iso(common),'any_missing_count':len(all_gaps),
            'gap_details_truncated':len(all_gaps)>MAX_GAP_DATES,'gap_details':details,
            'calendar_corrections_applied':0,'official_closure_verified':False,
            'missing_dates_by_stream':{s:{'count':len(g),'dates':iso(g),'truncated':len(g)>MAX_GAP_DATES}
                                       for s,g in gaps.items()}}


def pilot(api,now=None,*,calendar=session_dates,budget=None,pause=time.sleep,progress=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:raise ValueError('timezone required')
    end=now.astimezone(KST).date()-timedelta(days=1)
    start=end-timedelta(days=PILOT_DAYS-1)
    b=budget or Budget(max_calls=MAX_CALLS,max_seconds=MAX_SECONDS)
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD',
          'historical_session_verified':False,'point_in_time_universe_verified':False,
          'start':start.isoformat(),'end':end.isoformat(),'requested_streams':len(SYMBOLS),
          'logical_call_limit':b.max_calls,'stock_page_limit':MAX_PAGES,'index_page_limit':MAX_INDEX_PAGES,
          'price_basis':'KIS_NATIVE_ADJUSTED_HISTORY',
          'limitations':'Coverage and date-only reconciliation; no holiday inference, no raw session verification, no persistent storage, no training or orders.'}
    try:expected=tuple(calendar(start,end))
    except Exception:return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_UNAVAILABLE','streams':[],'logical_calls':0}
    if not expected:return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_EMPTY','streams':[],'logical_calls':0}
    records={};stats={}
    for symbol in SYMBOLS:
        s,r=collect(api,symbol,start,end,expected,b,
                    max_pages=MAX_INDEX_PAGES if symbol in INDEX_CODES else MAX_PAGES,pause=pause)
        stats[symbol]=s
        if s['reason'] not in SAFE_REASONS:
            if progress is not None:progress(dict(s))
            break
        records[symbol]=r
        missing=set(expected)-set(r)
        s['missing_dates']=[d.isoformat() for d in sorted(missing)[:MAX_GAP_DATES]]
        s['missing_dates_truncated']=len(missing)>MAX_GAP_DATES
        if progress is not None:progress(dict(s))
    report=reconciliation(expected,records,stats)
    counts=Counter(s['status'] for s in stats.values())
    ready=counts['READY']
    return {**base,'status':'READY' if ready==len(SYMBOLS) else 'PARTIAL' if records else 'UNAVAILABLE',
            'expected_sessions':len(expected),'finished_streams':len(stats),'ready_streams':ready,
            'logical_calls':b.calls,'streams':list(stats.values()),'status_counts':dict(counts),
            'reconciliation':report}
