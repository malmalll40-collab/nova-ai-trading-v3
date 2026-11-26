"""Bounded, read-only KIS historical coverage pilot; not a trading or training path."""
from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import isfinite
import json
import re
import time
from zoneinfo import ZoneInfo

VERSION='5.7.4'
KST=ZoneInfo('Asia/Seoul')
PILOT_SYMBOLS=('005930.KS','000660.KS','247540.KQ','263750.KQ')
INDEX_CODES={'^KS11':'0001','^KQ11':'1001'}
MAX_PAGES=12
MAX_INDEX_PAGES=20
MAX_CALLS=72
MAX_SECONDS=240
PILOT_DAYS=1096
STOCK_PATH='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
INDEX_PATH='/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice'


class ProbeError(ValueError):
    pass

# Only explicit control-plane stops can preserve earlier validated pages.
SAFE_ADMISSION_STOPS=frozenset(('ACCOUNT_QUOTA_UNVERIFIED','ACCOUNT_QUOTA_STALE',
    'QUOTA_HEADROOM_LOW','FOREGROUND_BUSY','PHYSICAL_REQUEST_BUSY','PROVIDER_UNHEALTHY',
    'RESEARCH_MINUTE_LIMIT','RESEARCH_PACING','TOKEN_UNAVAILABLE','PASS_DEADLINE',
    'QUIET_WINDOW_ENDED','RUN_ADMISSION_REVOKED','SHARED_QUOTA_BUSY',
    'SHARED_QUOTA_UNAVAILABLE','SHARED_QUOTA_RELEASE_UNVERIFIED'))


def session_dates(start,end):
    """Calendar labels are expected sessions, not proof of historical trading."""
    from krx_calendar_verified import get_krx_session_dates
    return get_krx_session_dates(start,end)


class Budget:
    def __init__(self,max_calls=MAX_CALLS,max_seconds=MAX_SECONDS,clock=time.monotonic):
        self.calls=0;self.max_calls=max_calls;self.clock=clock
        self.deadline=clock()+max_seconds
    def take(self):
        if self.calls>=self.max_calls or self.clock()>=self.deadline:
            raise ProbeError('BUDGET_EXHAUSTED')
        self.calls+=1


def number(v):
    try:n=float(v)
    except (TypeError,ValueError,OverflowError):raise ProbeError('INVALID_NUMERIC') from None
    if not isfinite(n):raise ProbeError('INVALID_NUMERIC')
    return n


def parse_row(r,index=False):
    if not isinstance(r,dict):raise ProbeError('INVALID_ROW')
    stamp=r.get('stck_bsop_date')
    if not isinstance(stamp,str) or not re.fullmatch(r'\d{8}',stamp):
        raise ProbeError('INVALID_DATE')
    try:d=datetime.strptime(stamp,'%Y%m%d').date()
    except ValueError:raise ProbeError('INVALID_DATE') from None
    if index:
        close=number(r.get('bstp_nmix_prpr'))
        if close<=0:raise ProbeError('INVALID_PRICE')
        return d,{'close':close}
    o,h,l,c,v=[number(r.get(k)) for k in ('stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol')]
    if min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c) or v<0:
        raise ProbeError('INVALID_OHLCV')
    # Optional native turnover is not fabricated from adjusted price * volume.
    t=r.get('acml_tr_pbmn')
    try:turnover=number(t) if t not in (None,'') else None
    except ProbeError:turnover=None
    if turnover is not None and turnover<0:turnover=None
    return d,{'open':o,'high':h,'low':l,'close':c,'volume':v,'turnover':turnover}


def collect(api,symbol,start,end,expected,budget,*,max_pages=MAX_PAGES,pause=time.sleep):
    if not (type(start) is date and type(end) is date and start<=end):
        raise ValueError('invalid date range')
    index=symbol in INDEX_CODES
    if type(max_pages) is not int or not 1<=max_pages<=(MAX_INDEX_PAGES if index else MAX_PAGES):raise ValueError('invalid page limit')
    if not index and not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise ValueError('invalid symbol')
    sessions=set(expected)
    if not sessions or any(d<start or d>end for d in sessions):raise ValueError('invalid sessions')
    records={};cursor=end;reason='PAGE_LIMIT';pages=0;duplicates=0
    for _ in range(max_pages):
        if cursor<start:reason='WINDOW_REACHED';break
        try:
            budget.take();pages+=1
            params={'FID_COND_MRKT_DIV_CODE':'U' if index else 'J',
                    'FID_INPUT_ISCD':INDEX_CODES[symbol] if index else symbol[:6],
                    'FID_INPUT_DATE_1':start.strftime('%Y%m%d'),
                    'FID_INPUT_DATE_2':cursor.strftime('%Y%m%d'),'FID_PERIOD_DIV_CODE':'D'}
            if not index:params['FID_ORG_ADJ_PRC']='0'
            try:
                payload=api.get(INDEX_PATH if index else STOCK_PATH,
                                'FHKUP03500100' if index else 'FHKST03010100',params)
            except Exception as exc:
                from nova_v579_admission import ResearchBlocked,PROVIDER_STOPS
                if isinstance(exc,ResearchBlocked) and str(exc) in (SAFE_ADMISSION_STOPS | PROVIDER_STOPS):
                    raise ProbeError(str(exc)) from None
                raise ProbeError('PROVIDER_REQUEST_FAILED') from None
            if not isinstance(payload,dict) or not isinstance(payload.get('output2'),list):
                raise ProbeError('INVALID_PAYLOAD')
            raw=payload['output2']
            if not raw:reason='PROVIDER_EMPTY';break
            page={}
            for r in raw:
                d,row=parse_row(r,index)
                if not start<=d<=cursor:raise ProbeError('OUTSIDE_REQUEST_WINDOW')
                if d in page:
                    if page[d]!=row:raise ProbeError('CONFLICTING_DUPLICATE')
                    duplicates+=1
                page[d]=row
            if not page:raise ProbeError('NO_PROGRESS')
            if any(d in records for d in page):raise ProbeError('PAGINATION_OVERLAP')
            records.update(page)
            earliest=min(page)
            if earliest<=min(sessions):reason='WINDOW_REACHED';break
            cursor=earliest-timedelta(days=1)
            # Never assume a short response is the last page.
            pause(.35)
        except ProbeError as exc:
            reason=str(exc);break
    observed=set(records);missing=sessions-observed;unexpected=observed-sessions
    first=min(observed,default=None);last=max(observed,default=None)
    leading=sum(first is None or d<first for d in missing)
    trailing=sum(last is not None and d>last for d in missing)
    internal=len(missing)-leading-trailing
    status='READY' if reason=='WINDOW_REACHED' and not missing and not unexpected else 'PARTIAL'
    if not records or reason not in ('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT','BUDGET_EXHAUSTED'):
        status='UNAVAILABLE'
    digest=sha256(json.dumps([(d.isoformat(),records[d]) for d in sorted(records)],
                             sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    stats={'symbol':symbol,'status':status,'reason':reason,'pages':pages,
           'expected_sessions':len(sessions),'observed_sessions':len(observed&sessions),
           'missing_sessions':len(missing),'leading_missing':leading,'internal_missing':internal,
           'trailing_missing':trailing,'unexpected_sessions':len(unexpected),
           'duplicate_rows':duplicates,'coverage_pct':round(100*len(observed&sessions)/len(sessions),3),
           'first':first.isoformat() if first else None,'last':last.isoformat() if last else None,
           'zero_volume_sessions':None if index else sum(r['volume']==0 for r in records.values()),
           'turnover_available':None if index else sum(r['turnover'] is not None for r in records.values()),
           'sha256':digest}
    return stats,records


def pilot(api,now=None,*,calendar=session_dates,budget=None,pause=time.sleep,progress=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:raise ValueError('timezone required')
    # Today's bar excluded even after close. Explicit historical coverage only.
    end=now.astimezone(KST).date()-timedelta(days=1)
    start=end-timedelta(days=PILOT_DAYS-1)
    b=budget or Budget()
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD',
          'historical_session_verified':False,'point_in_time_universe_verified':False,
          'start':start.isoformat(),'end':end.isoformat(),'requested_streams':6,
          'price_basis':'KIS_NATIVE_ADJUSTED_HISTORY','logical_call_limit':b.max_calls,
          'limitations':'Current pilot symbols, survivorship bias; no historical listing proof, no raw session verification, no training, no orders. Logical GET budget excludes bounded provider retries.'}
    try:expected=calendar(start,end)
    except Exception:return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_UNAVAILABLE','streams':[],'logical_calls':0}
    if not expected:return {**base,'status':'UNAVAILABLE','reason':'CALENDAR_EMPTY','streams':[],'logical_calls':0}
    results=[]
    for s in (*INDEX_CODES,*PILOT_SYMBOLS):
        stats,_=collect(api,s,start,end,expected,b,pause=pause)
        results.append(stats)
        if progress is not None:progress(dict(stats))
        # Stop on provider/format errors rather than repeatedly hammering KIS.
        if stats['reason'] not in ('WINDOW_REACHED','PROVIDER_EMPTY','PAGE_LIMIT'):
            break
    count=Counter(x['status'] for x in results)
    ready=count['READY']
    return {**base,'status':'READY' if ready==6 else 'PARTIAL' if results else 'UNAVAILABLE',
            'expected_sessions':len(expected),'finished_streams':len(results),'ready_streams':ready,
            'logical_calls':b.calls,'streams':results,'status_counts':dict(count)}
