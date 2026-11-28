"""Bounded official KRX collection, with persistent response claims in PostgreSQL."""
import io, json, math
import pandas as pd
from . import forward as f
from research_krx_collection import read_one, ORDER

FROZEN='55faf64cc2f02175b3fc0c424f75d1211e504d35264dfae9d7092af81cbdd6f6'
SERVICES={'stk_bydd_trd':('sto','KOSPI'),'ksq_bydd_trd':('sto','KOSDAQ'),
          'kospi_dd_trd':('idx','KOSPI'),'kosdaq_dd_trd':('idx','KOSDAQ')}

def verify_frozen():
    from pathlib import Path
    if f.sha(Path(f.__file__).read_bytes())!=FROZEN:raise ValueError('FROZEN_CODE_MISMATCH')

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')

def get_payload(session,service,day,key):
    status,p=read_one(session,key,service,f.dayfmt(day))
    if status!='success':raise ValueError('PROVIDER_RESPONSE_UNAVAILABLE')
    return canonical(p)

def parse_payload(body,day):
    p=json.loads(body)
    rows=p.get('OutBlock_1') if isinstance(p,dict) else None
    if not isinstance(rows,list) or not rows or len(rows)>10000:raise ValueError('EMPTY_OR_BAD_PAYLOAD')
    if any(not isinstance(r,dict) or r.get('BAS_DD')!=day for r in rows):raise ValueError('DATE_MISMATCH')
    return rows

def convert(payloads,day,prior_counts):
    rows=[]; idx={}
    for service,(folder,market) in SERVICES.items():
        raw=parse_payload(payloads[service],day)
        if folder=='idx':
            names={'KOSPI':{'코스피','KOSPI'},'KOSDAQ':{'코스닥','KOSDAQ'}}[market]
            match=[r for r in raw if r.get('IDX_NM') in names]
            if len(match)!=1:raise ValueError('INDEX_NOT_UNIQUE_'+market)
            x=float(str(match[0]['CLSPRC_IDX']).replace(',',''))
            if not math.isfinite(x) or x<=0:raise ValueError('INVALID_INDEX')
            idx[market]=x;continue
        for r in raw:
            if r.get('MKT_NM')!=market:raise ValueError('MARKET_MISMATCH')
            row={k:r[k] for k in f.COLS if k!='market'};row['market']=market
            for k in f.COLS[3:]:row[k]=str(row[k]).replace(',','')
            rows.append(row)
    b=pd.DataFrame(rows,columns=f.COLS).sort_values(['market','ISU_CD']).to_csv(index=False).encode()
    q=f.read_snapshot(io.BytesIO(b));counts=q.groupby('market').size().to_dict()
    if set(counts)!=set(prior_counts) or set(counts)!={'KOSPI','KOSDAQ'}:raise ValueError('MARKET_COVERAGE')
    for m,n in counts.items():
        if not .90*prior_counts[m]<=n<=1.10*prior_counts[m]:raise ValueError('COUNT_JUMP_'+m)
    if (q.volume.gt(0)&~f.active(q)).any():raise ValueError('INVALID_TRADED_BAR')
    if (q.close.gt(0)&q.shares.gt(0)&q.cap.ne(q.close*q.shares)).any():raise ValueError('CAP_MISMATCH')
    manifest={'day':day,'sha256':f.sha(b),'market_counts':counts,'source':'KRX_OFFICIAL','complete':True,
              'source_note':'Four official KRX endpoint responses; exact date, prior-day count tolerance and integrity checks passed. Completeness attestation is provisional, not independent verification.',
              'index_close':idx,'response_sha256':{s:f.sha(v) for s,v in payloads.items()}}
    return b,manifest
