"""Authenticated seed API, disabled by default. No KRX or trading calls."""
import hmac
import os
import threading
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import research_forward_seed as seed

router = APIRouter()
lock = threading.Lock()

def auth(request):
    token = os.getenv('NOVA_ACCESS_TOKEN', '')
    if not token or not token.strip():
        raise HTTPException(503, 'ACCESS_TOKEN_REQUIRED')
    supplied = request.headers.get('Authorization', '')
    if not hmac.compare_digest(supplied, 'Bearer ' + token):
        raise HTTPException(401, 'UNAUTHORIZED')

def result(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})

def safe_call(fn, *args):
    try:
        return fn(*args)
    except seed.SeedError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        # Never return or log a connection string, SQL arguments or upload bytes.
        raise HTTPException(503, 'DATABASE_OPERATION_UNAVAILABLE') from None

@router.get('/api/research/forward-seed/status')
def status(request: Request):
    auth(request)
    data = safe_call(seed.status, os.getenv('DATABASE_URL', '').strip())
    return result({**data, 'upload_enabled': os.getenv('NOVA_FORWARD_SEED_ENABLED') == '1'})

@router.post('/api/research/forward-seed')
async def upload(request: Request):
    auth(request)
    if os.getenv('NOVA_FORWARD_SEED_ENABLED') != '1':
        raise HTTPException(503, 'SEED_UPLOAD_DISABLED')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/gzip':
        raise HTTPException(415, 'GZIP_REQUIRED')
    if not lock.acquire(blocking=False):
        raise HTTPException(409, 'SEED_BUSY')
    try:
        length = request.headers.get('content-length')
        if length is not None:
            try:
                if not 0 < int(length) <= seed.MAX_UPLOAD:
                    raise ValueError()
            except ValueError:
                raise HTTPException(413, 'UPLOAD_SIZE') from None
        blob = bytearray()
        async for chunk in request.stream():
            if len(blob) + len(chunk) > seed.MAX_UPLOAD:
                raise HTTPException(413, 'UPLOAD_SIZE')
            blob.extend(chunk)
        data = await run_in_threadpool(safe_call, seed.initialize,
                                      os.getenv('DATABASE_URL', '').strip(), bytes(blob))
        return result(data)
    finally:
        lock.release()
