"""Offline research import only. No live calls, DB writes or orders."""
import pandas as pd
import numpy as np
FLOW=['day','code','foreign_shares','institution_shares','published_at','source','scope']
INDUSTRY=['day','code','industry','published_at','source','taxonomy']
def validate(frame,kind):
    required=FLOW if kind=='flow' else INDUSTRY if kind=='industry' else None
    if required is None or not set(required)<=set(frame):raise ValueError('SCHEMA')
    x=frame[required].copy()
    x['day']=x.day.astype(str)
    x['code']=x.code.astype(str)
    if not x.day.str.fullmatch(r'\d{8}').all() or not x.code.str.fullmatch(r'\d{6}').all():raise ValueError('KEY')
    pd.to_datetime(x.day,format='%Y%m%d',errors='raise')
    if x.duplicated(['day','code']).any():raise ValueError('DUPLICATE')
    # Explicit offset mandatory. Never infer publication time from trading date.
    stamp=x.published_at.astype(str)
    if not stamp.str.contains(r'(Z|[+-]\d{2}:\d{2})$',regex=True).all():raise ValueError('TIMEZONE')
    x['published_at']=pd.to_datetime(stamp,utc=True,errors='raise')
    if x.source.isna().any() or x.source.astype(str).str.strip().eq('').any():raise ValueError('SOURCE')
    if kind=='flow':
        if not x.scope.eq('KRX').all():raise ValueError('SCOPE')
        for col in ['foreign_shares','institution_shares']:
            raw=x[col];n=pd.to_numeric(raw,errors='coerce')
            if (raw.notna()&n.isna()).any() or (n.notna()&(~np.isfinite(n)|n.mod(1).ne(0)|n.abs().gt(10**15))).any():raise ValueError('SHARES')
            x[col]=n
    else:
        for col in ['industry','taxonomy']:
            if x[col].isna().any() or x[col].astype(str).str.strip().eq('').any():raise ValueError('INDUSTRY')
        if x.taxonomy.nunique()!=1:raise ValueError('MIXED_TAXONOMY')
    return x
def join_asof_day(cohort,records,kind):
    """Exact historical date membership; no current industry backfill.
    decision_at must be timezone-aware and describe the actual prediction cutoff.
    """
    x=validate(records,kind)
    q=cohort.copy()
    if q.duplicated(['day','code']).any():raise ValueError('COHORT_DUPLICATE')
    stamp=q.decision_at.astype(str)
    if not stamp.str.contains(r'(Z|[+-]\d{2}:\d{2})$',regex=True).all():raise ValueError('DECISION_TIMEZONE')
    cutoff=pd.to_datetime(stamp,utc=True,errors='raise')
    q['decision_at']=cutoff
    z=q.merge(x,on=['day','code'],how='left',validate='one_to_one')
    available=z.published_at.notna()&z.published_at.le(z.decision_at)
    cols=['foreign_shares','institution_shares'] if kind=='flow' else ['industry']
    for col in cols:z[col]=z[col].where(available)
    z[kind+'_available']=available&z[cols].notna().all(axis=1)
    return z
