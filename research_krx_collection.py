"""Private KRX collection, independent of KIS adjusted data.
Owner-enabled staged rollout. Pilot first, bounded resume only after verified
pilot. No raw response logging, no orders or live model use. Successful
provider access plus strict cohort schema is required before storage.
Uncertain dispatched claims remain held; no automatic repeat on errors.
"""
from contextlib import contextmanager
from datetime import date,datetime
import json
import os
import threading
import time
import requests
from market_scanner import classify_krx_response
from research_history_checkpoint import execute
from research_krx_snapshot import SERVICES,TABLE,initialize,import_snapshot,coverage,manifest
from research_krx_access_probe import MAX_BYTES,PARSE_CODES
from research_history_status import START,END
from nova_v574_data_audit import session_dates,KST
from ops_preflight import JOBS,COUNTERS,TERMINAL
from nova_v576_expanded_coverage import corrected_sessions
from research_operational_activation import eligible

PILOT=date(2026,11,20)
ORDER=('kospi_dd_trd','kosdaq_dd_trd','stk_bydd_trd','ksq_bydd_trd')
URLS={s:'https://data-dbg.krx.co.kr/svc/apis/'+('idx/' if s in ORDER[:2] else 'sto/')+s for s in ORDER}
FLAG='NOVA_KRX_PRIVATE_COLLECTION'
PILOT_MODE='pilot-20261120-v1'
# Explicit owner request on 2026-11-27. Expires the same day, never grants batch admission.
PILOT_NOW_MODE='pilot-now-20261120-20261127-v1'
PILOT_NOW_DAY=date(2026,11,27)
BATCH_MODE='bounded-history-v1'
MODES=(PILOT_MODE,PILOT_NOW_MODE,BATCH_MODE)
CLAIMS='nova_krx_collection_claim_v1'
MAX_BATCH_CALLS=12
LOCK=601576049
PACE=5
BASE={'research_only':True,'adjustment_verified':False,'training_ready':False,
      'strategy_adoption':'HOLD','orders_enabled':False,'provider_quota_verified':False}

class CollectorStore:
    def __init__(self,dsn,sessions,connect=None):
        if connect is None:
            import psycopg
            connect=psycopg.connect
        self.dsn=dsn;self.connect=connect;self.sessions=tuple(sessions)
        self.dataset=manifest(self.sessions)[0]
    @contextmanager
    def tx(self):
        with self.connect(self.dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '8s'")
                cur.execute("SET LOCAL lock_timeout = '2s'")
                yield cur
    def setup(self):
        with self.tx() as cur:
            assert initialize(cur,'postgresql',self.sessions)==self.dataset
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {CLAIMS} (
                dataset TEXT NOT NULL, service TEXT NOT NULL, day TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('CLAIMED','STORED','FAILED')),
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                PRIMARY KEY(dataset,service,day))""")
    def status(self):
        with self.tx() as cur:
            info=coverage(cur,'postgresql',self.dataset)
            cur.execute(f'SELECT service,day,state FROM {CLAIMS} WHERE dataset=%s',(self.dataset,))
            claims=cur.fetchall()
            cur.execute(f'SELECT service,day FROM {TABLE} WHERE dataset=%s',(self.dataset,))
            saved=set(cur.fetchall())
            for service,stamp,state in claims:
                if service not in ORDER or date.fromisoformat(stamp) not in self.sessions:
                    raise ValueError('KRX_CLAIM_CORRUPT')
                if (state=='STORED')!=((service,stamp) in saved):
                    raise ValueError('KRX_CLAIM_STORAGE_MISMATCH')
            return info,saved,{(s,d) for s,d,state in claims if state!='STORED'}
    def claim(self,service,day):
        if service not in ORDER or day not in self.sessions:raise ValueError('KRX_INVALID_REQUEST')
        with self.tx() as cur:
            # Transaction-scoped global lock works on pooler; no session
            # affinity assumption. Never dispatch while another claim held.
            cur.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
            cur.execute(f"SELECT 1 FROM {CLAIMS} WHERE dataset=%s AND state IN ('CLAIMED','FAILED') LIMIT 1",(self.dataset,))
            if cur.fetchone():return False
            cur.execute(f'SELECT 1 FROM {TABLE} WHERE dataset=%s AND service=%s AND day=%s',(self.dataset,service,day.isoformat()))
            if cur.fetchone():return False
            cur.execute(f"INSERT INTO {CLAIMS}(dataset,service,day,state) VALUES (%s,%s,%s,'CLAIMED') ON CONFLICT DO NOTHING",(self.dataset,service,day.isoformat()))
            return cur.rowcount==1
    def fail(self,service,day):
        with self.tx() as cur:
            cur.execute(f"UPDATE {CLAIMS} SET state='FAILED' WHERE dataset=%s AND service=%s AND day=%s AND state='CLAIMED'",(self.dataset,service,day.isoformat()))
            if cur.rowcount!=1:raise ValueError('KRX_CLAIM_LOST')
    def save(self,service,day,payload,*,authorized):
        with self.tx() as cur:
            cur.execute(f"SELECT state FROM {CLAIMS} WHERE dataset=%s AND service=%s AND day=%s FOR UPDATE",(self.dataset,service,day.isoformat()))
            if cur.fetchone()!=('CLAIMED',):raise ValueError('KRX_CLAIM_LOST')
            n=import_snapshot(cur,'postgresql',self.dataset,service,day,payload,authorized=authorized)
            if n!=len(SERVICES[service]):raise ValueError('KRX_UNEXPECTED_DUPLICATE')
            cur.execute(f"UPDATE {CLAIMS} SET state='STORED' WHERE dataset=%s AND service=%s AND day=%s AND state='CLAIMED'",(self.dataset,service,day.isoformat()))
            if cur.rowcount!=1:raise ValueError('KRX_CLAIM_LOST')
            return n

def read_one(session,key,service,day):
    if service not in URLS:raise ValueError('KRX_INVALID_SERVICE')
    try:
        with session.get(URLS[service],params={'basDd':day.strftime('%Y%m%d')},
            headers={'AUTH_KEY':key,'Accept':'application/json'},
            timeout=(5,14),allow_redirects=False,stream=True) as resp:
            chunks=[];size=0;deadline=time.monotonic()+25
            for chunk in resp.iter_content(chunk_size=65536):
                size+=len(chunk)
                if size>MAX_BYTES:return 'RESPONSE_TOO_LARGE',None
                if time.monotonic()>deadline:return 'NETWORK_ERROR',None
                chunks.append(chunk)
            try:p=json.loads(b''.join(chunks))
            except (ValueError,UnicodeError):p=None
            # Same KRX response envelope for both stock and index endpoints;
            # only use the classifier state, not its stock-specific labels.
            r=classify_krx_response(resp.status_code,p,'kospi')
            if r['state']!='success':return r['state'],None
            if not isinstance(p,dict) or not isinstance(p.get('OutBlock_1'),list):
                return 'provider_error',None
            return 'success',p
    except requests.RequestException:return 'NETWORK_ERROR',None

def plan(sessions,saved,mode):
    days=tuple(sessions)
    if PILOT not in days or mode not in MODES:raise ValueError('KRX_PLAN_INVALID')
    first=[(s,PILOT) for s in ORDER if (s,PILOT.isoformat()) not in saved]
    if first or mode in (PILOT_MODE,PILOT_NOW_MODE):return first
    return [(s,d) for d in reversed(days) if d!=PILOT for s in ORDER if (s,d.isoformat()) not in saved][:MAX_BATCH_CALLS]

def collect(store,session,key,mode,ready,wait,emit):
    def stop(reason):
        emit('krx_collection_stop',{**BASE,'reason':reason})
    if not ready() or not key or mode not in MODES:
        return stop('ADMISSION_BLOCKED')
    store.setup()
    info,saved,held=store.status()
    emit('krx_collection_coverage',{**BASE,**info})
    if held:return stop('UNRESOLVED_CLAIM')
    tasks=plan(store.sessions,saved,mode)
    for i,(service,day) in enumerate(tasks):
        if not ready():return stop('FOREGROUND_OR_WINDOW_BUSY')
        if not store.claim(service,day):return stop('SHARED_CLAIM_BLOCKED')
        if not ready():return stop('WINDOW_CHANGED_AFTER_CLAIM')
        state,payload=read_one(session,key,service,day)
        result={**BASE,'service':service,'day':day.isoformat(),'status':state,'new_rows':0}
        if state!='success':
            store.fail(service,day)
            emit('krx_collection_result',result)
            return stop('PROVIDER_OR_TRANSPORT_STOP')
        try:
            # Exact service and day, actual successful provider response,
            # explicitly enabled private collection only. No default True.
            n=store.save(service,day,payload,authorized=lambda s,d:
                mode in MODES and state=='success' and (s,d)==(service,day))
        except ValueError as exc:
            # save transaction rolled back, uncertain claim remains held
            code=str(exc)
            emit('krx_collection_result',{**result,'status':code if code in PARSE_CODES else 'CHECKPOINT_REJECTED'})
            return stop('SCHEMA_OR_CHECKPOINT_REJECTED')
        emit('krx_collection_result',{**result,'status':'STORED','new_rows':n})
        if i+1<len(tasks) and wait(PACE):return stop('STOP_REQUESTED')
    info,_,_=store.status()
    emit('krx_collection_coverage',{**BASE,**info})
    return stop('BOUNDED_PASS_COMPLETE')

def admitted(s,mode,now=None):
    """Owner's time-only exception, not a general override. Same-day, fresh
    local observations, all known jobs terminal and all KIS counters zero.
    Does not establish shared KRX/provider quota or atomic exclusion.
    """
    if mode!=PILOT_NOW_MODE:return mode in MODES and eligible(s)
    now=now or datetime.now(KST)
    if now.tzinfo is None or now.astimezone(KST).date()!=PILOT_NOW_DAY:return False
    stamp=datetime.fromisoformat(s.get('as_of',''))
    if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=60:return False
    jobs=s.get('jobs',{});load=s.get('kis_load',{})
    return (s.get('calendar_verified') is True and s.get('krx_regular_status') in ('OPEN','CLOSED')
        and s.get('scope')=='PROCESS_LOCAL_NON_ATOMIC'
        and set(jobs)==set(JOBS) and all(v in TERMINAL for v in jobs.values())
        and all(type(load.get(k)) is int and load[k]==0 for k in COUNTERS)
        and s.get('window_overflow') is False
        and s.get('orders_enabled') is False and s.get('krx_manual_busy') is False)


def worker(stop,snapshot,dsn,key,mode,*,wait=None,store_factory=CollectorStore,session_factory=requests.Session,emit=None):
    if emit is None:
        from ops_diagnostics import emit
    wait=wait or stop.wait
    def ready():
        try:return not stop.is_set() and admitted(snapshot(),mode)
        except Exception:return False
    if wait(120):return
    if not ready() or not dsn or not key:
        return emit('krx_collection_stop',{**BASE,'reason':'ADMISSION_BLOCKED'})
    try:
        if mode==PILOT_NOW_MODE:
            emit('krx_collection_admission',{**BASE,'mode':mode,'time_only_exception':True,'max_calls':4,'scope':'PROCESS_LOCAL_NON_ATOMIC'})
        days,_=corrected_sessions(session_dates(START,END),START,END)
        store=store_factory(dsn,days)
        with session_factory() as session:
            collect(store,session,key,mode,ready,wait,emit)
    except Exception:
        emit('krx_collection_stop',{**BASE,'reason':'COLLECTION_OR_CHECKPOINT_FAILED'})

def start_if_enabled(stop,snapshot):
    mode=os.getenv(FLAG)
    if mode not in MODES:return False
    threading.Thread(target=worker,args=(stop,snapshot,os.getenv('DATABASE_URL','').strip(),
        os.getenv('KRX_API_KEY','').strip(),mode),daemon=True,name='nova-krx-private-collector').start()
    return True
