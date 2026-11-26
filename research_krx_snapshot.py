"""Offline-only KRX historical research snapshot validation/checkpoint.

No HTTP client, credentials, automatic collection, public endpoint, or
production caller. Import only provider-authorized private research data.
KRX native prices have no verified adjustment basis. Never merge with KIS
adjusted history or run live strategy evaluation on this snapshot.
Official service catalog:
https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
Terms:
https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp
Field mappings require actual approved-response verification.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import math
import re
from research_history_checkpoint import canonical, digest, execute, manifest as kis_manifest
from nova_v576_expanded_coverage import SYMBOLS

BASIS='KRX_NATIVE_DAILY_ADJUSTMENT_UNVERIFIED'
DATASETS='nova_krx_research_manifest_v1'
TABLE='nova_krx_research_snapshot_v1'
SERVICES={
    'kospi_dd_trd':('^KS11',),
    'kosdaq_dd_trd':('^KQ11',),
    'stk_bydd_trd':tuple(s for s in SYMBOLS if s.endswith('.KS')),
    'ksq_bydd_trd':tuple(s for s in SYMBOLS if s.endswith('.KQ')),
}
INDEX_NAMES={'kospi_dd_trd':{'코스피','KOSPI'},'kosdaq_dd_trd':{'코스닥','KOSDAQ'}}
MARKETS={'stk_bydd_trd':'KOSPI','ksq_bydd_trd':'KOSDAQ'}
STOCK_FIELDS={'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC',
              'close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}
SCHEMA=(
    f'CREATE TABLE IF NOT EXISTS {DATASETS} (id TEXT PRIMARY KEY, manifest TEXT NOT NULL)',
    f'CREATE TABLE IF NOT EXISTS {TABLE} (dataset TEXT NOT NULL, service TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(dataset,service,day), FOREIGN KEY(dataset) REFERENCES {DATASETS}(id))',
)

def manifest(sessions):
    days=tuple(sessions)
    kis_manifest(days)  # reuse calendar input validation, not KIS identity or data
    obj={'schema':1,'basis':BASIS,'provider':'KRX','sessions':[d.isoformat() for d in days],
         'services':{k:list(v) for k,v in SERVICES.items()},'research_only':True,
         'adjustment_verified':False,'point_in_time_universe_verified':False,
         'historical_calendar_fully_verified':False,'strategy_adoption':'HOLD'}
    raw=canonical(obj)
    return digest(raw),raw

def _numeric(value, *, integer=False, positive=True):
    # Missing, dash, malformed grouping, NaN, overflow and fractional
    # share counts are errors, never silently mapped to 0.
    if type(value) not in (str,int,float):
        raise ValueError('KRX_INVALID_NUMBER')
    text=str(value).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',text):
        raise ValueError('KRX_INVALID_NUMBER')
    try:n=Decimal(text.replace(',',''))
    except InvalidOperation:raise ValueError('KRX_INVALID_NUMBER') from None
    if not n.is_finite() or n<0 or (positive and n==0) or n>10**18:
        raise ValueError('KRX_INVALID_NUMBER')
    if integer:
        if n!=n.to_integral_value():raise ValueError('KRX_INVALID_NUMBER')
        return int(n)
    return float(n)

def _row(service, row):
    if service in INDEX_NAMES:
        return {'close':_numeric(row.get('CLSPRC_IDX'))}
    result={key:_numeric(row.get(field),integer=key in ('volume','turnover'),
                         positive=key not in ('volume','turnover'))
            for key,field in STOCK_FIELDS.items()}
    if result['high']<max(result['open'],result['close'],result['low']) or result['low']>min(result['open'],result['close']):
        raise ValueError('KRX_INVALID_OHLC')
    return result

def parse_snapshot(service,day,payload):
    """Exact service/day cohort only. Fail closed on incomplete selected rows.

    No provider entitlement inferred from successful parsing. Fixtures/sample
    payloads never establish actual coverage or authorization.
    """
    if service not in SERVICES or type(day) is not date:
        raise ValueError('KRX_INVALID_REQUEST')
    if not isinstance(payload,dict) or ('respCode' in payload and str(payload['respCode']) not in ('0','00','200')):
        raise ValueError('KRX_PROVIDER_RESPONSE_INVALID')
    rows=payload.get('OutBlock_1')
    if not isinstance(rows,list) or not rows or len(rows)>10000:
        raise ValueError('KRX_PROVIDER_RESPONSE_INVALID')
    expected=set(SERVICES[service]);result={}
    for row in rows:
        if not isinstance(row,dict) or row.get('BAS_DD')!=day.strftime('%Y%m%d'):
            raise ValueError('KRX_DATE_MISMATCH')
        if service in INDEX_NAMES:
            if row.get('IDX_NM') not in INDEX_NAMES[service]:continue
            symbol=SERVICES[service][0]
        else:
            code=str(row.get('ISU_CD','')).strip()
            if re.fullmatch(r'KR7[0-9]{6}[0-9A-Z]{3}',code):code=code[3:9]
            symbol=code+('.KS' if service=='stk_bydd_trd' else '.KQ')
            if symbol not in expected:continue
            if row.get('MKT_NM')!=MARKETS[service]:
                raise ValueError('KRX_MARKET_MISMATCH')
        if symbol in result:raise ValueError('KRX_DUPLICATE_SYMBOL')
        result[symbol]=_row(service,row)
    if set(result)!=expected:raise ValueError('KRX_COHORT_INCOMPLETE')
    return result

def _snapshot_raw(service,records):
    if service not in SERVICES or not isinstance(records,dict) or set(records)!=set(SERVICES[service]):
        raise ValueError('KRX_COHORT_INCOMPLETE')
    for row in records.values():
        keys={'close'} if service in INDEX_NAMES else set(STOCK_FIELDS)
        if not isinstance(row,dict) or set(row)!=keys:raise ValueError('KRX_INVALID_FIELDS')
        for k,v in row.items():
            if k in ('volume','turnover'):
                if type(v) is not int or not 0<=v<=10**18:raise ValueError('KRX_INVALID_NUMBER')
            elif type(v) is not float or not math.isfinite(v) or not 0<v<=10**18:
                raise ValueError('KRX_INVALID_NUMBER')
        if service not in INDEX_NAMES and (row['high']<max(row['open'],row['close'],row['low']) or row['low']>min(row['open'],row['close'])):
            raise ValueError('KRX_INVALID_OHLC')
    return canonical(records)

def _load_manifest(cur,kind,dataset):
    execute(cur,kind,f'SELECT manifest FROM {DATASETS} WHERE id=?',(dataset,))
    row=cur.fetchone()
    if row is None or digest(row[0])!=dataset:raise ValueError('KRX_MANIFEST_CORRUPT')
    obj=json.loads(row[0])
    days=tuple(date.fromisoformat(x) for x in obj['sessions'])
    if manifest(days)!=(dataset,row[0]):raise ValueError('KRX_MANIFEST_CORRUPT')
    return days

def initialize(cur,kind,sessions):
    """Explicit caller-owned transaction only; no network or startup hook."""
    sessions=tuple(sessions)
    dataset,raw=manifest(sessions)
    for sql in SCHEMA:execute(cur,kind,sql)
    execute(cur,kind,f'INSERT INTO {DATASETS}(id,manifest) VALUES(?,?) ON CONFLICT(id) DO NOTHING',(dataset,raw))
    if _load_manifest(cur,kind,dataset)!=tuple(sessions):raise ValueError('KRX_MANIFEST_CORRUPT')
    return dataset

def import_snapshot(cur,kind,dataset,service,day,payload,*,authorized):
    """Store one complete service/day atomically. Caller must have independently
    verified entitlement, actual provider provenance and private use.
    This explicit guard is not independent proof; do not wire a default True.
    Caller owns transaction and must roll back on error. Never overwrite.
    """
    if not callable(authorized) or authorized(service,day) is not True:
        raise ValueError('KRX_IMPORT_NOT_AUTHORIZED')
    if day not in _load_manifest(cur,kind,dataset):raise ValueError('KRX_UNEXPECTED_SESSION')
    raw=_snapshot_raw(service,parse_snapshot(service,day,payload))
    args=(dataset,service,day.isoformat(),raw,digest(raw))
    execute(cur,kind,f'INSERT INTO {TABLE}(dataset,service,day,payload,digest) VALUES(?,?,?,?,?) ON CONFLICT(dataset,service,day) DO NOTHING',args)
    inserted=cur.rowcount
    execute(cur,kind,f'SELECT payload,digest FROM {TABLE} WHERE dataset=? AND service=? AND day=?',args[:3])
    if cur.fetchone()!=args[3:]:raise ValueError('KRX_SNAPSHOT_CONFLICT')
    return len(SERVICES[service]) if inserted==1 else 0

def coverage(cur,kind,dataset):
    """Private aggregate coverage only; verify content on every read."""
    days=set(_load_manifest(cur,kind,dataset))
    execute(cur,kind,f'SELECT service,day,payload,digest FROM {TABLE} WHERE dataset=?',(dataset,))
    counts={s:0 for s in SERVICES};seen=set()
    for service,stamp,raw,checksum in cur.fetchall():
        try:
            d=date.fromisoformat(stamp)
            key=(service,d)
            valid=(service in SERVICES and d in days and stamp==d.isoformat() and key not in seen
                   and digest(raw)==checksum and _snapshot_raw(service,json.loads(raw))==raw)
        except (ValueError,TypeError,OverflowError,KeyError):
            valid=False
        if not valid:raise ValueError('KRX_SNAPSHOT_CORRUPT')
        seen.add(key)
        counts[service]+=1
    expected=len(days)*len(SYMBOLS)
    stored=sum(counts[s]*len(SERVICES[s]) for s in counts)
    return {'basis':BASIS,'stored_rows':stored,'expected_rows':expected,
            'complete_streams':sum(len(SERVICES[s]) for s in counts if counts[s]==len(days)),
            'service_days':counts,'status':'COVERAGE_COMPLETE' if stored==expected else 'PARTIAL',
            'research_only':True,'adjustment_verified':False,'training_ready':False,
            'strategy_adoption':'HOLD','orders_enabled':False}
