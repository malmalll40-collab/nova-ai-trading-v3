"""Fail-closed proof using actual cumulative turnover, not invented share counts."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import pandas as pd

METHOD = 'KIS_CUMULATIVE_TURNOVER_V1'
CODES = frozenset(('TURNOVER_VERIFIED','TURNOVER_UNAVAILABLE','TURNOVER_DELTA_INVALID',
    'TURNOVER_TARGET_CHANGED','TURNOVER_NEIGHBOR_MISMATCH','TURNOVER_PROBE_FAILED',
    'TURNOVER_ROW_UNSAFE','TURNOVER_TARGET_NOT_ZERO','TURNOVER_WINDOW_BLOCKED'))

def amount(value):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d!=d.to_integral_value() or d>10**18:return None
        return int(d)
    except (InvalidOperation,ValueError,TypeError,OverflowError):return None


def verify(api, code, day, target, close, rows, turnover, comparison, *, now=None):
    """Same-day isolated gap: two non-first zero rows and exact turnover agreement.
    No alternate OHLCV is added to the aggregate. All amounts must be integer KRW.
    """
    from kis_zero_gap import neighbor_matches
    local=now or datetime.now(close.tz)
    minute=pd.Timedelta(minutes=1); prev=target-minute; nxt=target+minute
    if local.astimezone(close.tz).date()!=day or pd.Timestamp(local)<close or min(target+3*minute,close)<=nxt:
        return None,'TURNOVER_WINDOW_BLOCKED'
    if comparison.get(target.strftime('%H:%M'))!='TARGET_ZERO_VOLUME':return None,'TURNOVER_TARGET_NOT_ZERO'
    if prev not in rows or nxt not in rows:return None,'TURNOVER_NEIGHBOR_MISMATCH'
    a,b=turnover.get(prev),turnover.get(nxt)
    if a is None or b is None:return None,'TURNOVER_UNAVAILABLE'
    v=amount(rows[nxt].get('Volume')); pv=amount(rows[prev].get('Volume'))
    lo=amount(rows[nxt].get('Low')); hi=amount(rows[nxt].get('High'))
    if not v or not pv or not lo or not hi or lo>hi or not lo*v<=b-a<=hi*v:
        return None,'TURNOVER_DELTA_INVALID'
    for offset in (2,3):
        cursor=min(target+offset*minute,close)
        if cursor<=nxt:return None,'TURNOVER_WINDOW_BLOCKED'
        try:
            response=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice','FHKST03010200',{
                'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor.strftime('%H%M%S'),
                'FID_PW_DATA_INCU_YN':'N','FID_ETC_CLS_CODE':''})
        except Exception:return None,'TURNOVER_PROBE_FAILED'
        if not isinstance(response,dict) or not isinstance(response.get('output2'),list):return None,'TURNOVER_PROBE_FAILED'
        chosen={}
        for t in (prev,target,nxt):
            match=[(i,r) for i,r in enumerate(response['output2']) if isinstance(r,dict) and r.get('stck_bsop_date')==day.strftime('%Y%m%d') and r.get('stck_cntg_hour')==t.strftime('%H%M%S')]
            if len(match)!=1 or match[0][0]==0:return None,'TURNOVER_ROW_UNSAFE'
            chosen[t]=match[0][1]
        if amount(chosen[target].get('cntg_vol'))!=0:return None,'TURNOVER_TARGET_NOT_ZERO'
        if not neighbor_matches(chosen[prev],rows[prev]) or not neighbor_matches(chosen[nxt],rows[nxt]):return None,'TURNOVER_NEIGHBOR_MISMATCH'
        ca,ct,cb=[amount(chosen[t].get('acml_tr_pbmn')) for t in (prev,target,nxt)]
        if None in (ca,ct,cb):return None,'TURNOVER_UNAVAILABLE'
        if ca!=a or cb!=b:return None,'TURNOVER_NEIGHBOR_MISMATCH'
        if ct!=ca:return None,'TURNOVER_TARGET_CHANGED'
    return {'method':METHOD,'prev':prev.strftime('%H:%M'),'next':nxt.strftime('%H:%M'),
            'prev_turnover':a,'target_turnover':a,'next_turnover':b,'next_volume':v,
            'next_low':lo,'next_high':hi,'target_volume':0},'TURNOVER_VERIFIED'


def sanitize(p):
    if not isinstance(p,dict) or p.get('method')!=METHOD:return None
    keys=('prev_turnover','target_turnover','next_turnover','next_volume','next_low','next_high','target_volume')
    try:a,t,b,v,lo,hi,z=[p[k] for k in keys]
    except KeyError:return None
    if any(type(x) is not int or not 0<=x<=10**18 for x in (a,t,b,v,lo,hi,z)):return None
    if t!=a or z!=0 or not v or not lo or hi<lo or not lo*v<=b-a<=hi*v:return None
    return {'method':METHOD,'prev':p['prev'],'next':p['next'],**{k:p[k] for k in keys}}
