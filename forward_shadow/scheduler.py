"""Opt-in scheduled research only; fixed bridge cutoff, no broker calls."""
import os
from datetime import datetime,time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from . import runner, forward as f, api, oidc
from research_forward_seed import NS

router=APIRouter()
BRIDGE_LAST='20261127'
snapshot=None

def install_snapshot(fn):
    global snapshot
    snapshot=fn

def admitted():
    from research_operational_activation import historical_eligible
    try:
        s=snapshot()
        return s.get('krx_regular_status')=='CLOSED' and historical_eligible(s)
    except Exception:return False

def claim_identity(digest):
    import psycopg
    dsn=os.getenv('DATABASE_URL','').strip()
    with psycopg.connect(dsn,connect_timeout=10) as db,db.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10s'")
        cur.execute(f'CREATE TABLE IF NOT EXISTS {NS}.scheduler_claim (digest text PRIMARY KEY, claimed_at timestamptz NOT NULL DEFAULT now())')
        cur.execute(f'INSERT INTO {NS}.scheduler_claim(digest) VALUES (%s) ON CONFLICT DO NOTHING',(digest,))
        return cur.rowcount==1

def log(data):
    from ops_diagnostics import emit
    emit('forward_scheduler',data)

def bridge(stop,*,run=runner.execute,admit=admitted,wait=None,emit=log):
    wait=wait or stop.wait
    if os.getenv('NOVA_FORWARD_BRIDGE_ON_START')!='1' or not runner.eligible_env():return
    if wait(45):return
    if not api.lock.acquire(blocking=False):
        emit({'state':'BLOCKED','reason':'LOCAL_BUSY','orders_enabled':False});return
    try:
        # At most two days. Repeated boot cannot pass the 2026-11-27 cutoff.
        for _ in range(2):
            if stop.is_set() or not admit():
                emit({'state':'BLOCKED','reason':'LOCAL_ADMISSION','orders_enabled':False});break
            result=run(latest_day=BRIDGE_LAST,admit=lambda:not stop.is_set() and admit())
            emit(result)
            if result.get('state')!='INGESTED' or result.get('day')==BRIDGE_LAST:break
            if wait(5):break
    except Exception as exc:
        # Only a small allowlist of nonsecret codes; never provider bodies or DSNs.
        emit({'state':'BLOCKED','reason':reason(exc),'orders_enabled':False})
    finally:api.lock.release()

def reason(exc):
    allowed={'KRX_KEY_REQUIRED','LOCAL_ADMISSION_BLOCKED','UNCERTAIN_CALL','OWNER_LOST',
             'BUSY','BUSY_OR_UNINITIALIZED','LOCAL_STORAGE_GUARD','STATE_HASH',
             'FROZEN_CODE_MISMATCH','PROVIDER_RESPONSE_UNAVAILABLE','DATE_MISMATCH',
             'INVALID_TRADED_BAR','CAP_MISMATCH','MARKET_MISMATCH','STATE_REVISION',
             'EXPLICIT_ENABLE_AND_LEGACY_OFF_REQUIRED','PACKED_SIZE','RAW_SIZE'}
    return str(exc) if isinstance(exc,ValueError) and str(exc) in allowed else 'FORWARD_BLOCKED'

@router.post('/api/research/forward/scheduled')
def scheduled(request:Request):
    if os.getenv('NOVA_FORWARD_SCHEDULE_ENABLED')!='1' or not runner.eligible_env():
        raise HTTPException(503,'SCHEDULE_DISABLED')
    header=request.headers.get('Authorization','')
    if not header.startswith('Bearer '):raise HTTPException(401,'UNAUTHORIZED')
    try:digest=oidc.authenticate(header[7:])
    except ValueError:raise HTTPException(401,'UNAUTHORIZED') from None
    local=f.now()
    if local.weekday()>=5 or local.time()<time(18,30):
        return JSONResponse({'state':'OUTSIDE_WINDOW','orders_enabled':False},headers={'Cache-Control':'no-store'})
    if not api.lock.acquire(blocking=False):raise HTTPException(409,'FORWARD_BUSY')
    try:
        if not admitted():raise HTTPException(409,'LOCAL_ADMISSION_BLOCKED')
        if not claim_identity(digest):raise HTTPException(409,'IDENTITY_ALREADY_USED')
        data=runner.execute(latest_day=local.strftime('%Y%m%d'),admit=admitted)
        log(data)
        # Report only aggregate metrics; no raw market rows or signals.
        return JSONResponse(data,headers={'Cache-Control':'no-store'})
    except HTTPException:raise
    except Exception as exc:
        log({'state':'BLOCKED','reason':reason(exc),'orders_enabled':False})
        raise HTTPException(503,'FORWARD_BLOCKED') from None
    finally:api.lock.release()
