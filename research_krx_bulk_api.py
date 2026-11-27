"""Owner-only transport for one fixed KRX history and the private verified export."""
import hashlib
import hmac
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from research_history_status import END
from research_krx_bulk import SourceError, scope
from research_krx_bulk_store import download, run_step, status

MODE = 'bulk-download-v1'
HEADERS = {'Cache-Control': 'private, no-store'}


def ticket_valid(header, setting, now):
    """Only a hash and an absolute expiry are configured on the server."""
    match = re.fullmatch(r'([0-9]{10}):([0-9a-f]{64})', setting)
    if not match or not now < int(match[1]) <= now + 86400:
        return False
    if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43,128}', header):
        return False
    return hmac.compare_digest(hashlib.sha256(header[7:].encode()).hexdigest(), match[2])


def router(authenticated):
    api = APIRouter(prefix='/api/research/krx/bulk')

    def authorize(request, write=False):
        header = request.headers.get('Authorization', '')
        temporary = ticket_valid(header, os.getenv('NOVA_KRX_TRANSFER_GRANT', ''), time.time())
        owner = bool(os.getenv('NOVA_ACCESS_TOKEN')) and (header.startswith('Bearer ') or not write) and authenticated(request)
        if not (temporary or owner):
            raise HTTPException(401, 'KRX_OWNER_AUTH_REQUIRED', headers=HEADERS)
        if write and os.getenv('NOVA_KRX_PRIVATE_COLLECTION') != MODE:
            raise HTTPException(409, 'KRX_BULK_MODE_INACTIVE', headers=HEADERS)
        dsn = os.getenv('DATABASE_URL', '').strip()
        if not dsn:
            raise HTTPException(503, 'KRX_DATABASE_UNAVAILABLE', headers=HEADERS)
        return dsn

    @api.get('')
    def get_status(request: Request):
        dsn = authorize(request)
        with psycopg.connect(dsn, connect_timeout=8) as db, db.cursor() as cur:
            return JSONResponse(status(cur, *scope()), headers=HEADERS)

    @api.post('')
    def advance(request: Request):
        dsn = authorize(request, write=True)
        key = os.getenv('KRX_API_KEY', '').strip()
        if not key or END >= datetime.now(ZoneInfo('Asia/Seoul')).date():
            raise HTTPException(409, 'KRX_KEY_OR_PERIOD_UNAVAILABLE', headers=HEADERS)
        try:
            result = run_step(dsn, key, *scope())
        except SourceError as exc:
            raise HTTPException(409, exc.details, headers=HEADERS) from None
        return JSONResponse(result, headers=HEADERS)

    @api.get('/download')
    def get_download(request: Request):
        dsn = authorize(request)
        try:
            with psycopg.connect(dsn, connect_timeout=8) as db, db.cursor() as cur:
                data = download(cur, *scope())
        except SourceError as exc:
            raise HTTPException(409, exc.details, headers=HEADERS) from None
        return Response(data, media_type='application/zip', headers={
            **HEADERS, 'Content-Disposition': 'attachment; filename="NOVA_KRX_20231126_20261125.zip"'})
    return api
