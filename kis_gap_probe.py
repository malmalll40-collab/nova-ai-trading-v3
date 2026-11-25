"""Bounded same-endpoint recovery and diagnostic-only KIS same-day comparison."""
from datetime import datetime
import math
import pandas as pd

MAX_ALTERNATE = 4
PROBE_CODES = frozenset(('TARGET_ABSENT','TARGET_POSITIVE_VOLUME','TARGET_ZERO_VOLUME',
    'TARGET_VOLUME_UNRELIABLE','QUERY_FAILED','INVALID_RESPONSE','INVALID_VOLUME',
    'NOT_SAME_DAY','CONFLICTING_TARGET'))


def alternate_queries(missing, close):
    """Only small isolated gaps; use N and an overlapping later cursor."""
    if not 0 < len(missing) <= MAX_ALTERNATE:
        return []
    return [(target, min(target+pd.Timedelta(minutes=1),close).strftime('%H%M%S'))
            for target in missing]


def compare_same_day(api, code, day, targets, close, *, now=None):
    """Compare real same-day endpoint rows; never return price bars for storage."""
    if not 0 < len(targets) <= MAX_ALTERNATE:
        return {}
    local=now or datetime.now(close.tz)
    if local.astimezone(close.tz).date()!=day or pd.Timestamp(local)<close:
        return {t.strftime('%H:%M'):'NOT_SAME_DAY' for t in targets}
    result={}
    for target,cursor in alternate_queries(targets,close):
        key=target.strftime('%H:%M'); stamp=target.strftime('%H%M%S')
        try:
            resp=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice','FHKST03010200',{
                'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor,
                'FID_PW_DATA_INCU_YN':'N','FID_ETC_CLS_CODE':''})
        except Exception:
            result[key]='QUERY_FAILED';continue
        if not isinstance(resp,dict) or not isinstance(resp.get('output2'),list):
            result[key]='INVALID_RESPONSE';continue
        matches=[(i,r) for i,r in enumerate(resp['output2']) if isinstance(r,dict)
                 and r.get('stck_bsop_date')==day.strftime('%Y%m%d') and r.get('stck_cntg_hour')==stamp]
        if not matches:result[key]='TARGET_ABSENT';continue
        if len(matches)!=1:result[key]='CONFLICTING_TARGET';continue
        i,r=matches[0]
        if i==0:result[key]='TARGET_VOLUME_UNRELIABLE';continue
        try:volume=float(r['cntg_vol'])
        except (KeyError,TypeError,ValueError,OverflowError):result[key]='INVALID_VOLUME';continue
        if not math.isfinite(volume) or volume<0 or not volume.is_integer():result[key]='INVALID_VOLUME';continue
        result[key]='TARGET_POSITIVE_VOLUME' if volume>0 else 'TARGET_ZERO_VOLUME'
    return result
