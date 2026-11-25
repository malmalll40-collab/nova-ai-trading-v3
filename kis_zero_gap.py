"""Same-day zero-execution interval proof; never manufacture or merge OHLCV rows."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import pandas as pd

MAX_ZERO_GAPS = 4
FIELDS = ('Open','High','Low','Close')
WIRE = ('stck_oprc','stck_hgpr','stck_lwpr','stck_prpr')


def nonnegative_int(value):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d!=d.to_integral_value() or d>10**15:return None
        return int(d)
    except (InvalidOperation,TypeError,ValueError,OverflowError):return None


def neighbor_matches(raw, expected):
    try:
        values={k:float(raw[v]) for k,v in zip(FIELDS,WIRE)}
        return (all(math.isfinite(v) and v>0 for v in values.values()) and
                all(values[k]==expected[k] for k in FIELDS) and
                nonnegative_int(raw.get('cntg_vol'))==expected['Volume'])
    except (KeyError,TypeError,ValueError,OverflowError):return False


def certify_zero_gaps(api, code, day, missing, close, rows, cumulative, comparison, *, now=None):
    """All-or-nothing proof using actual KIS rows and cumulative-volume conservation.

    Require isolated interior gaps, positive matching neighbor trades, a zero target
    in two same-day queries, and exact cumulative delta on the original endpoint.
    The first array row of the same-day endpoint is never used. No row is inserted.
    """
    if not 0<len(missing)<=MAX_ZERO_GAPS:return {}
    local=now or datetime.now(close.tz)
    if local.astimezone(close.tz).date()!=day or pd.Timestamp(local)<close:return {}
    proofs={}; minute=pd.Timedelta(minutes=1); day_key=day.strftime('%Y%m%d')
    for target in missing:
        key=target.strftime('%H:%M'); prev=target-minute; nxt=target+minute
        if comparison.get(key)!='TARGET_ZERO_VOLUME' or prev not in rows or nxt not in rows:return {}
        a,b=cumulative.get(prev),cumulative.get(nxt)
        v=nonnegative_int(rows[nxt].get('Volume'))
        if a is None or b is None or not v or rows[prev]['Volume']<=0 or a<rows[prev]['Volume'] or b-a!=v:return {}
        # Cursor later than BOTH neighbors; reject a response that places any
        # proof row first (KIS documents first-row execution-volume ambiguity).
        cursor=min(target+2*minute,close)
        if cursor<=nxt:return {}
        try:
            response=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice','FHKST03010200',{
                'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor.strftime('%H%M%S'),
                'FID_PW_DATA_INCU_YN':'N','FID_ETC_CLS_CODE':''})
        except Exception:return {}
        if not isinstance(response,dict) or not isinstance(response.get('output2'),list):return {}
        chosen={}
        for stamp in (prev,target,nxt):
            matches=[(i,r) for i,r in enumerate(response['output2']) if isinstance(r,dict)
                     and r.get('stck_bsop_date')==day_key and r.get('stck_cntg_hour')==stamp.strftime('%H%M%S')]
            if len(matches)!=1 or matches[0][0]==0:return {}
            chosen[stamp]=matches[0][1]
        if nonnegative_int(chosen[target].get('cntg_vol'))!=0:return {}
        if not neighbor_matches(chosen[prev],rows[prev]) or not neighbor_matches(chosen[nxt],rows[nxt]):return {}
        # If the alternate endpoint supplies cumulative volume, it must agree.
        for stamp,expected in ((prev,a),(target,a),(nxt,b)):
            raw=chosen[stamp]
            if 'acml_vol' in raw and nonnegative_int(raw['acml_vol'])!=expected:return {}
        proofs[key]={'prev':prev.strftime('%H:%M'),'next':nxt.strftime('%H:%M'),
                     'prev_cumulative':a,'next_cumulative':b,'next_volume':v,'target_volume':0}
    return proofs


def sanitized_proofs(value):
    """Strict, bounded serialization of the actual proof, not raw API responses."""
    import re
    if not isinstance(value,dict) or not 0<len(value)<=MAX_ZERO_GAPS:return {}
    clean={}
    def minutes(t):
        if not isinstance(t,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',t):raise ValueError
        return int(t[:2])*60+int(t[3:])
    try:
        for t,p in value.items():
            if not isinstance(p,dict) or minutes(p['prev'])!=minutes(t)-1 or minutes(p['next'])!=minutes(t)+1:return {}
            a,b,v,z=[p[k] for k in ('prev_cumulative','next_cumulative','next_volume','target_volume')]
            if any(type(x) is not int or not 0<=x<=10**15 for x in (a,b,v,z)) or not v or z!=0 or b-a!=v:return {}
            clean[t]={'prev':p['prev'],'next':p['next'],'prev_cumulative':a,'next_cumulative':b,'next_volume':v,'target_volume':z}
    except (KeyError,TypeError,ValueError):return {}
    return clean
