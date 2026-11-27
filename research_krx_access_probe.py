"""Explicit one-shot private KRX production access check, not collection.

Only the two stock services the owner previously reported approved. No
index service until separately authorized. No raw payload, key or provider
message persistence/logging, no retries, no redirects, no KIS use.
A durable per-service/date attempt marker is committed before network I/O;
uncertain attempts are not repeated even after process restart.
"""
from datetime import date
from contextlib import contextmanager
import os
import threading
import requests
from market_scanner import URLS,classify_krx_response
from research_krx_snapshot import parse_snapshot,SERVICES
from research_operational_activation import historical_eligible

DAY=date(2026,11,25)
MARKETS=('kospi','kosdaq')
SERVICE={'kospi':'stk_bydd_trd','kosdaq':'ksq_bydd_trd'}
TABLE='nova_krx_access_probe_v1'
FLAG='NOVA_KRX_ACCESS_PROBE'
FLAG_VALUE='2026-11-25-stock-only-v1'
MAX_BYTES=8*1024*1024
PARSE_CODES={'KRX_INVALID_REQUEST','KRX_PROVIDER_RESPONSE_INVALID','KRX_DATE_MISMATCH',
    'KRX_MARKET_MISMATCH','KRX_DUPLICATE_SYMBOL','KRX_COHORT_INCOMPLETE','KRX_INVALID_NUMBER','KRX_INVALID_OHLC'}
STATES={'success','no_data','auth_denied','rate_limited','provider_error'}
ERROR_CODES={'ok','no_published_data','unauthorized_key','unauthorized_api_call','unauthorized','forbidden',
    'provider_rate_limited','provider_unavailable','provider_rejected','provider_format'}

class Attempts:
    def __init__(self,dsn,connect=None):
        if connect is None:
            import psycopg
            connect=psycopg.connect
        self.dsn=dsn;self.connect=connect
    @contextmanager
    def tx(self):
        with self.connect(self.dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute("SET LOCAL lock_timeout = '2s'")
                yield cur
    def initialize(self):
        with self.tx() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                service TEXT NOT NULL, day TEXT NOT NULL,
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                PRIMARY KEY(service,day))""")
    def claim(self,market):
        if market not in MARKETS:raise ValueError('KRX_INVALID_SERVICE')
        with self.tx() as cur:
            cur.execute(f'INSERT INTO {TABLE}(service,day) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                        (SERVICE[market],DAY.isoformat()))
            claimed=cur.rowcount==1
        return claimed

def check_one(session,key,market):
    if market not in MARKETS:raise ValueError('KRX_INVALID_SERVICE')
    service=SERVICE[market]
    base={'service':service,'day':DAY.isoformat(),'access_verified':False,'schema_verified':False,
          'selected_rows':0,'expected_selected_rows':len(SERVICES[service]),'adjustment_verified':False,
          'stored_rows':0,'research_only':True,'orders_enabled':False}
    try:
        # Fixed HTTPS provider host, no redirects, one physical request.
        with session.get(URLS[market],params={'basDd':DAY.strftime('%Y%m%d')},
                         headers={'AUTH_KEY':key,'Accept':'application/json'},
                         timeout=(5,14),allow_redirects=False,stream=True) as resp:
            import json
            chunks=[];size=0
            for chunk in resp.iter_content(chunk_size=65536):
                size+=len(chunk)
                if size>MAX_BYTES:return {**base,'status':'RESPONSE_TOO_LARGE'}
                chunks.append(chunk)
            try:payload=json.loads(b''.join(chunks))
            except (ValueError,UnicodeError):payload=None
            result=classify_krx_response(resp.status_code,payload,market)
        state=result['state'];code=result['error_code']
        out={**base,'status':state if state in STATES else 'provider_error',
             'http_status':result['http_status'],'provider_code':result['krx_code'],
             'error_code':code if code in ERROR_CODES else 'provider_format'}
        if state!='success':return out
        # Transport access and actual selected-cohort data integrity are distinct.
        out['access_verified']=True
        try:records=parse_snapshot(service,DAY,payload)
        except ValueError as exc:
            reason=str(exc)
            return {**out,'schema_reason':reason if reason in PARSE_CODES else 'KRX_SCHEMA_UNVERIFIED'}
        return {**out,'schema_verified':True,'selected_rows':len(records)}
    except requests.RequestException:return {**base,'status':'NETWORK_ERROR'}
    except Exception:return {**base,'status':'PROBE_FAILED'}

def worker(stop,snapshot,dsn,key,*,wait=None,attempts=None,session_factory=requests.Session,emit=None):
    if wait is None:wait=stop.wait
    if emit is None:
        from ops_diagnostics import emit
    def ready():
        try:return not stop.is_set() and historical_eligible(snapshot())
        except Exception:return False
    def blocked(reason):
        emit('krx_access_probe_stop',{'reason':reason,'orders_enabled':False,'stored_rows':0})
    if wait(120):return
    if not ready():return blocked('WINDOW_OR_FOREGROUND_BUSY')
    if not key or not dsn:return blocked('CONFIGURATION_MISSING')
    try:
        ledger=attempts if attempts is not None else Attempts(dsn)
        ledger.initialize()
        with session_factory() as session:
            for market in MARKETS:
                if not ready():return blocked('WINDOW_OR_FOREGROUND_BUSY')
                if not ledger.claim(market):
                    emit('krx_access_probe_skip',{'service':SERVICE[market],'reason':'ALREADY_ATTEMPTED',
                         'orders_enabled':False,'stored_rows':0})
                    continue
                if not ready():return blocked('WINDOW_OR_FOREGROUND_BUSY_AFTER_CLAIM')
                result=check_one(session,key,market)
                emit('krx_access_probe',result)
                if result.get('status') in ('rate_limited','NETWORK_ERROR','PROBE_FAILED','RESPONSE_TOO_LARGE'):
                    return blocked('PROVIDER_OR_TRANSPORT_STOP')
                if wait(2):return
    except Exception:return blocked('ATTEMPT_LEDGER_OR_WORKER_FAILED')

def start_if_enabled(stop,snapshot):
    if os.getenv(FLAG)!=FLAG_VALUE:return False
    threading.Thread(target=worker,args=(stop,snapshot,os.getenv('DATABASE_URL','').strip(),
                     os.getenv('KRX_API_KEY','').strip()),daemon=True,name='nova-krx-access-probe').start()
    return True
