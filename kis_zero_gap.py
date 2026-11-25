"""Same-day zero-execution interval proof; never manufacture or merge OHLCV rows."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import pandas as pd

MAX_ZERO_GAPS = 4
PROOF_CODES = frozenset(('VERIFIED','GAP_LIMIT','NOT_SAME_DAY','TARGET_NOT_ZERO',
    'NEIGHBOR_MISSING','CUMULATIVE_UNAVAILABLE','NEIGHBOR_VOLUME_INVALID',
    'CUMULATIVE_DELTA_MISMATCH','CURSOR_UNSAFE','QUERY_FAILED','INVALID_RESPONSE',
    'PROOF_ROW_MISSING','PROOF_ROW_DUPLICATE','PROOF_ROW_FIRST','TARGET_VOLUME_MISMATCH',
    'NEIGHBOR_MISMATCH','ALTERNATE_CUMULATIVE_MISMATCH'))
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


def certify_zero_gaps(api, code, day, missing, close, rows, cumulative, comparison, diagnostics=None, *, now=None):
    """All-or-nothing proof using actual KIS rows and cumulative-volume conservation.

    Require isolated interior gaps, positive matching neighbor trades, a zero target
    in two same-day queries, and exact cumulative delta on the original endpoint.
    The first array row of the same-day endpoint is never used. No row is inserted.
    """
    diagnostics = diagnostics if diagnostics is not None else {}
    if not 0<len(missing)<=MAX_ZERO_GAPS:
        diagnostics['overall']='GAP_LIMIT';return {}
    local=now or datetime.now(close.tz)
    if local.astimezone(close.tz).date()!=day or pd.Timestamp(local)<close:
        diagnostics['overall']='NOT_SAME_DAY';return {}
    proofs={}; minute=pd.Timedelta(minutes=1); day_key=day.strftime('%Y%m%d')
    for target in missing:
        key=target.strftime('%H:%M'); prev=target-minute; nxt=target+minute
        if comparison.get(key)!='TARGET_ZERO_VOLUME':
            diagnostics[key]='TARGET_NOT_ZERO';continue
        if prev not in rows or nxt not in rows:
            diagnostics[key]='NEIGHBOR_MISSING';continue
        a,b=cumulative.get(prev),cumulative.get(nxt)
        v=nonnegative_int(rows[nxt].get('Volume'))
        if a is None or b is None:
            diagnostics[key]='CUMULATIVE_UNAVAILABLE';continue
        if not v or rows[prev]['Volume']<=0 or a<rows[prev]['Volume']:
            diagnostics[key]='NEIGHBOR_VOLUME_INVALID';continue
        if b-a!=v:
            diagnostics[key]='CUMULATIVE_DELTA_MISMATCH';continue
        cursor=min(target+2*minute,close)
        if cursor<=nxt:
            diagnostics[key]='CURSOR_UNSAFE';continue
        try:
            response=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice','FHKST03010200',{
                'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor.strftime('%H%M%S'),
                'FID_PW_DATA_INCU_YN':'N','FID_ETC_CLS_CODE':''})
        except Exception:
            diagnostics[key]='QUERY_FAILED';continue
        if not isinstance(response,dict) or not isinstance(response.get('output2'),list):
            diagnostics[key]='INVALID_RESPONSE';continue
        chosen={}; failure=None
        for stamp in (prev,target,nxt):
            matches=[(i,r) for i,r in enumerate(response['output2']) if isinstance(r,dict)
                     and r.get('stck_bsop_date')==day_key and r.get('stck_cntg_hour')==stamp.strftime('%H%M%S')]
            if not matches:failure='PROOF_ROW_MISSING';break
            if len(matches)!=1:failure='PROOF_ROW_DUPLICATE';break
            if matches[0][0]==0:failure='PROOF_ROW_FIRST';break
            chosen[stamp]=matches[0][1]
        if failure:
            diagnostics[key]=failure;continue
        if nonnegative_int(chosen[target].get('cntg_vol'))!=0:
            diagnostics[key]='TARGET_VOLUME_MISMATCH';continue
        if not neighbor_matches(chosen[prev],rows[prev]) or not neighbor_matches(chosen[nxt],rows[nxt]):
            diagnostics[key]='NEIGHBOR_MISMATCH';continue
        if any('acml_vol' in chosen[stamp] and nonnegative_int(chosen[stamp]['acml_vol'])!=expected
               for stamp,expected in ((prev,a),(target,a),(nxt,b))):
            diagnostics[key]='ALTERNATE_CUMULATIVE_MISMATCH';continue
        proofs[key]={'prev':prev.strftime('%H:%M'),'next':nxt.strftime('%H:%M'),
                     'prev_cumulative':a,'next_cumulative':b,'next_volume':v,'target_volume':0}
        diagnostics[key]='VERIFIED'
    return proofs if len(proofs)==len(missing) else {}


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
            if 'method' in p:
                from kis_turnover_proof import sanitize
                normalized=sanitize(p)
                if normalized is None:return {}
                clean[t]=normalized
                continue
            a,b,v,z=[p[k] for k in ('prev_cumulative','next_cumulative','next_volume','target_volume')]
            if any(type(x) is not int or not 0<=x<=10**15 for x in (a,b,v,z)) or not v or z!=0 or b-a!=v:return {}
            clean[t]={'prev':p['prev'],'next':p['next'],'prev_cumulative':a,'next_cumulative':b,'next_volume':v,'target_volume':z}
    except (KeyError,TypeError,ValueError):return {}
    return clean
