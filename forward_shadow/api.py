"""Authenticated opt-in shadow execution. No scheduler or startup execution."""
import hmac, os, threading
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router=APIRouter()
lock=threading.Lock()

@router.post('/api/research/forward/run')
def run_forward(request:Request):
    token=os.getenv('NOVA_FORWARD_RUN_TOKEN','')
    if len(token)<32:raise HTTPException(503,'FORWARD_TOKEN_REQUIRED')
    if not hmac.compare_digest(request.headers.get('X-Nova-Forward-Token',''),token):
        raise HTTPException(401,'UNAUTHORIZED')
    from . import runner
    if not runner.eligible_env():raise HTTPException(503,'FORWARD_DISABLED')
    if not lock.acquire(blocking=False):raise HTTPException(409,'FORWARD_BUSY')
    try:
        try:data=runner.execute()
        except Exception:
            from ops_diagnostics import emit
            emit('forward_run',{'status':'BLOCKED','orders_enabled':False})
            raise HTTPException(503,'FORWARD_BLOCKED') from None
        from ops_diagnostics import emit
        emit('forward_run',data)
        return JSONResponse(data,headers={'Cache-Control':'no-store'})
    finally:lock.release()
