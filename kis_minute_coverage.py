"""Strict KIS regular-session coverage, bounded pagination and gap diagnostics."""
from datetime import datetime
import math
import pandas as pd
from kis_gap_probe import alternate_queries, compare_same_day
from kis_zero_gap import certify_zero_gaps, nonnegative_int

MAX_PAGES = 12
MAX_GAP_PAGES = 4
FIELDS = ('Open', 'High', 'Low', 'Close')
CODES = frozenset(('VERIFIED','SESSION_UNAVAILABLE','QUERY_FAILED','INVALID_RESPONSE',
    'INVALID_TIMESTAMP','INVALID_PRICE','INVALID_VOLUME','CONFLICTING_DUPLICATE',
    'COVERAGE_MISSING','OPEN_CLOSE_VOLUME_INVALID'))
STOPS = frozenset(('REACHED_OPEN','EMPTY_PAGE','CURSOR_STALLED','PAGE_LIMIT','NOT_STARTED'))


def collect(api, code, day, start, close):
    rows = {}; pages = 0; gaps = 0; stop = 'NOT_STARTED'
    alternate = 0; comparison = {}; cumulative = {}; zero_proofs = {}
    expected = list(pd.date_range(start, close-pd.Timedelta(minutes=11), freq='min')) + [close]
    day_key = day.strftime('%Y%m%d')
    def diagnostic(reason):
        missing = [t for t in expected if t not in rows]
        return {'reason':reason,'stop':stop,'pages':pages,'gap_pages':gaps,
                'alternate_pages':alternate,'same_day_probe':comparison,
                'zero_proofs':zero_proofs,'certified_zero_count':len(zero_proofs),
                'unresolved_count':len(missing)-len(zero_proofs),
                'required':len(expected),'received_required':len(expected)-len(missing),
                'missing_count':len(missing),'missing_minutes':[t.strftime('%H:%M') for t in missing],
                'open_present':start in rows,'close_present':close in rows,
                'zero_volume_minutes':sum(v['Volume']==0 for v in rows.values())}
    def page(cursor,past='Y'):
        try:
            response=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice','FHKST03010230',{
                'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor,
                'FID_INPUT_DATE_1':day_key,'FID_PW_DATA_INCU_YN':past,'FID_FAKE_TICK_INCU_YN':''})
        except Exception: return [], 'QUERY_FAILED'
        if not isinstance(response,dict) or not isinstance(response.get('output2'),list):return [], 'INVALID_RESPONSE'
        times=[]
        for x in response['output2']:
            if not isinstance(x,dict):return [], 'INVALID_RESPONSE'
            if x.get('stck_bsop_date')!=day_key:continue
            try:stamp=pd.Timestamp(datetime.strptime(day_key+x['stck_cntg_hour'],'%Y%m%d%H%M%S'),tz=start.tz)
            except (KeyError,TypeError,ValueError):return [], 'INVALID_TIMESTAMP'
            if not start<=stamp<=close:continue
            if stamp.second or stamp.microsecond:return [], 'INVALID_TIMESTAMP'
            try:val={f:float(x[k]) for f,k in zip(FIELDS,('stck_oprc','stck_hgpr','stck_lwpr','stck_prpr'))}
            except (KeyError,TypeError,ValueError,OverflowError):return [], 'INVALID_PRICE'
            if not all(math.isfinite(v) and v>0 for v in val.values()) or val['High']<max(val.values()) or val['Low']>min(val.values()):return [], 'INVALID_PRICE'
            try:volume=float(x['cntg_vol'])
            except (KeyError,TypeError,ValueError,OverflowError):return [], 'INVALID_VOLUME'
            if not math.isfinite(volume) or volume<0 or not volume.is_integer():return [], 'INVALID_VOLUME'
            val['Volume']=volume
            if stamp in rows and rows[stamp]!=val:return [], 'CONFLICTING_DUPLICATE'
            cum=nonnegative_int(x.get('acml_vol'))
            if stamp in cumulative and cumulative[stamp]!=cum:cumulative[stamp]=None
            elif stamp not in cumulative:cumulative[stamp]=cum
            rows[stamp]=val;times.append(stamp)
        return times, None
    cursor=close.strftime('%H%M%S')
    for _ in range(MAX_PAGES):
        pages+=1
        times,error=page(cursor)
        if error:return None,diagnostic(error)
        if not times:stop='EMPTY_PAGE';break
        if min(times)<=start:stop='REACHED_OPEN';break
        next_cursor=(min(times)-pd.Timedelta(seconds=1)).strftime('%H%M%S')
        if next_cursor>=cursor:stop='CURSOR_STALLED';break
        cursor=next_cursor
    else:stop='PAGE_LIMIT'
    # Target actual missing timestamps only, with original API parameters.
    # No synthetic rows; all overlapping returned rows undergo the same validation.
    attempted=set()
    for _ in range(MAX_GAP_PAGES):
        missing=[t for t in reversed(expected) if t not in rows and t not in attempted]
        if not missing:break
        target=missing[0];attempted.add(target);gaps+=1
        _,error=page(target.strftime('%H%M%S'))
        if error:return None,diagnostic(error)
    for target,cursor in alternate_queries([t for t in expected if t not in rows],close):
        if target in rows:continue
        alternate+=1
        _,error=page(cursor,'N')
        if error:return None,diagnostic(error)
    missing=[t for t in expected if t not in rows]
    if missing:
        comparison=compare_same_day(api,code,day,missing,close)
        zero_proofs=certify_zero_gaps(api,code,day,missing,close,rows,cumulative,comparison)
        if len(zero_proofs)!=len(missing):return None,diagnostic('COVERAGE_MISSING')
    if rows[start]['Volume']<=0 or rows[close]['Volume']<=0:return None,diagnostic('OPEN_CLOSE_VOLUME_INVALID')
    executed=[v for v in rows.values() if v['Volume']>0]
    result={'Open':rows[start]['Open'],'High':max(v['High'] for v in executed),
            'Low':min(v['Low'] for v in executed),'Close':rows[close]['Close'],
            'Volume':sum(v['Volume'] for v in rows.values())}
    return result,diagnostic('VERIFIED')
