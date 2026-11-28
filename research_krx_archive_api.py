"""Explicit bearer authentication; bounded private archive transfer."""
import hmac,os,asyncio
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from forward_shadow.api import lock
import research_krx_archive as a

router=APIRouter(prefix='/api/research/history-archive')
def enabled():return os.getenv('NOVA_HISTORY_ARCHIVE_UPLOAD')=='1'
def auth(req):
    token=os.getenv('NOVA_ACCESS_TOKEN','')
    if not token.strip():raise HTTPException(503,'ACCESS_TOKEN_REQUIRED')
    if not hmac.compare_digest(req.headers.get('Authorization','').encode(),('Bearer '+token).encode()):
        raise HTTPException(401,'UNAUTHORIZED')
def call(fn,*args):
    try:return fn(os.getenv('DATABASE_URL','').strip(),*args)
    except a.ArchiveError as exc:raise HTTPException(409,str(exc)) from None
    except Exception:raise HTTPException(503,'ARCHIVE_DATABASE_UNAVAILABLE') from None
def out(data):return JSONResponse(data,headers={'Cache-Control':'no-store'})
@router.get('/status')
def status(req:Request):
    auth(req)
    return out({**call(a.read_status),'upload_enabled':enabled()})

async def transfer(req,fn,limit,*args,ctype='application/octet-stream'):
    auth(req)
    if not enabled():raise HTTPException(503,'ARCHIVE_UPLOAD_DISABLED')
    if req.headers.get('content-type','').split(';')[0].strip().lower()!=ctype:raise HTTPException(415,'CONTENT_TYPE')
    if not lock.acquire(blocking=False):raise HTTPException(409,'ARCHIVE_BUSY')
    try:
        data=bytearray()
        async def receive():
            async for piece in req.stream():
                if len(data)+len(piece)>limit:raise HTTPException(413,'UPLOAD_SIZE')
                data.extend(piece)
        try:await asyncio.wait_for(receive(),timeout=30)
        except asyncio.TimeoutError:raise HTTPException(408,'UPLOAD_TIMEOUT') from None
        if not data:raise HTTPException(400,'EMPTY_UPLOAD')
        return out(await run_in_threadpool(call,fn,*args,bytes(data)))
    finally:lock.release()

@router.post('/manifest')
async def register(req:Request):return await transfer(req,a.register,a.MAX_MANIFEST)
@router.post('/coverage')
async def coverage(req:Request):return await transfer(req,a.put_coverage,a.MAX_COVERAGE)
@router.post('/chunk/{name}')
async def chunk(name:str,req:Request):return await transfer(req,a.put,a.MAX_CHUNK,name,ctype='application/gzip')
@router.post('/verify/{name}')
def verify(name:str,req:Request):
    auth(req)
    if not enabled():raise HTTPException(503,'ARCHIVE_UPLOAD_DISABLED')
    if not lock.acquire(blocking=False):raise HTTPException(409,'ARCHIVE_BUSY')
    try:return out(call(a.verify_member,name))
    finally:lock.release()
