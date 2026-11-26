"""Optional strictly read-only KIS domestic trade feed (research-only).

Single on-demand connection, no 24h worker, no broker orders, no changes to
production signals. Supports only the official KRX H0STCNT0 trade payload.
Approval credentials stay entirely on the server. Disabled by default.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from nova_v578_kis_telemetry import KIS_LOAD

KST=ZoneInfo('Asia/Seoul')
_WS_LOCK=threading.Lock()
_APPROVAL=None
_APPROVAL_AT=0.
_BUSY=threading.BoundedSemaphore(1)
_WS_HOST='ws://ops.koreainvestment.com:21000'


def enabled():
    return (os.environ.get('NOVA_KIS_WS_ENABLED','0')=='1'
            and bool(os.environ.get('KIS_APP_KEY','').strip())
            and bool(os.environ.get('KIS_APP_SECRET','').strip()))


def status():
    return {'configured':bool(os.environ.get('KIS_APP_KEY') and os.environ.get('KIS_APP_SECRET')),
            'enabled':enabled(),'mode':'on-demand','active_subscription_limit':1,
            'trade_feed':'KRX / H0STCNT0','fallback':'20s REST remains primary',
            'orders_enabled':False,'modifies_live_signal':False,
            'provider_connection_validated':False,
            'note':'사용자가 화면에서 직접 연결할 때만 최대 3분간 관찰합니다. 거래소·KIS 실측 연결 검증 전에는 실시간 체결 성공을 보장하지 않습니다.'}


def approval_key() -> str:
    # The worker can outlive the async wait timeout; retain its own foreground scope.
    with KIS_LOAD.foreground():
        return _approval_key()


def _approval_key() -> str:
    global _APPROVAL,_APPROVAL_AT
    if not enabled():raise RuntimeError('WEBSOCKET_DISABLED')
    with _WS_LOCK:
        if _APPROVAL and time.monotonic()-_APPROVAL_AT < 5*3600:
            return _APPROVAL
        with KIS_LOAD.physical(token=True):
            r=requests.post('https://openapi.koreainvestment.com:9443/oauth2/Approval',
                            json={'grant_type':'client_credentials',
                                  'appkey':os.environ['KIS_APP_KEY'],
                                  'secretkey':os.environ['KIS_APP_SECRET']},timeout=12,**KIS_LOAD.transport_options())
            r.raise_for_status()
            key=r.json().get('approval_key')
            if not isinstance(key,str) or len(key)<15:raise RuntimeError('KIS_APPROVAL_UNAVAILABLE')
        _APPROVAL=key
        _APPROVAL_AT=time.monotonic()
        return key


def trade_frame(raw:str,code:str)->dict|None:
    """KIS 0|H0STCNT0|count|csv caret fields; strictly parse only allowed indexes.

    Index 0=ticker, 1=trade time, 2=current trade price, 12=trade size,
    13=cumulative volume. Control messages, other ticker, malformed frames ignored.
    """
    if not isinstance(raw,str) or not re.fullmatch(r'\d{6}',code):return None
    if not raw.startswith('0|H0STCNT0|'):return None
    parts=raw.split('|',3)
    if len(parts)!=4:return None
    fields=parts[3].split('^')
    if len(fields)<14 or fields[0]!=code:return None
    clock=fields[1]
    if not re.fullmatch(r'\d{6}',clock):return None
    try:
        hh,mm,ss=int(clock[:2]),int(clock[2:4]),int(clock[4:6])
        price=int(fields[2]);size=int(fields[12]);cum=int(fields[13])
        if hh>23 or mm>59 or ss>59 or price<=0 or size<0 or cum<0:return None
    except (TypeError, ValueError, OverflowError):return None
    return {'type':'trade','symbol':code,'price':price,'trade_time_kst':f'{hh:02d}:{mm:02d}:{ss:02d}',
            'trade_volume':size,'cumulative_volume':cum,
            'observed_at_kst':datetime.now(KST).isoformat(timespec='seconds'),
            'source':'KIS WS KRX H0STCNT0','research_only':True,
            'complete_candle':False,'affects_model_or_orders':False}


async def relay(client,code:str):
    """Observe the entire user-requested subscription, including approval wait."""
    if not enabled() or not re.fullmatch(r'\d{6}',code):
        return await _relay(client,code)
    with KIS_LOAD.foreground():
        return await _relay(client,code)


async def _relay(client,code:str):
    """Requires an already accepted authenticated FastAPI WebSocket."""
    if not enabled():
        await client.send_json({'type':'error','message':'실시간 체결 실험 기능이 비활성화되어 있습니다.'})
        return
    if not re.fullmatch(r'\d{6}',code):
        await client.send_json({'type':'error','message':'정확한 6자리 국내 종목코드가 필요합니다.'})
        return
    if not _BUSY.acquire(blocking=False):
        await client.send_json({'type':'error','message':'기존 실시간 관찰 연결이 사용 중입니다.'})
        return
    try:
        try:
            key=await asyncio.wait_for(asyncio.to_thread(approval_key),timeout=18)
        except Exception:
            await client.send_json({'type':'error','message':'KIS 실시간 접속키를 확인할 수 없습니다. 서버 설정과 KIS 서비스 상태를 확인하세요.'})
            return
        import websockets
        # Official KIS connection endpoint published as ws://, not wss://.
        # Never send account identifiers or order instructions.
        try:
            async with websockets.connect(_WS_HOST,open_timeout=12,close_timeout=3,
                                          max_size=262144,ping_interval=20) as broker:
                subscription={'header':{'approval_key':key,'custtype':'P','tr_type':'1',
                                        'content-type':'utf-8'},
                              'body':{'input':{'tr_id':'H0STCNT0','tr_key':code}}}
                await broker.send(json.dumps(subscription,separators=(',',':')))
                await client.send_json({'type':'connected','symbol':code,
                                        'message':'KIS 연결 요청 완료 · 실제 체결 수신 여부 확인 중',
                                        'research_only':True})
                expires=time.monotonic()+180
                last_sent=0.
                sent=0
                while time.monotonic()<expires and sent<500:
                    try:
                        raw=await asyncio.wait_for(broker.recv(),timeout=16)
                    except asyncio.TimeoutError:
                        await client.send_json({'type':'heartbeat','message':'체결 데이터 대기 · 장외 또는 거래 없음'})
                        continue
                    if not isinstance(raw,str):continue
                    event=trade_frame(raw,code)
                    if event and time.monotonic()-last_sent>.3:
                        await client.send_json(event)
                        last_sent=time.monotonic();sent+=1
                    elif raw.startswith('{'):
                        try:
                            info=json.loads(raw)
                            rsp=info.get('body',{}).get('rt_cd')
                            if str(rsp) not in ('0','None'):
                                await client.send_json({'type':'error','message':'KIS 구독을 승인하지 않았습니다. 서비스 권한을 확인하세요.'})
                                return
                        except (ValueError,AttributeError):pass
                await client.send_json({'type':'complete','message':'연구용 실시간 관찰 시간이 종료됐습니다.'})
        except Exception:
            await client.send_json({'type':'error','message':'KIS 실시간 연결을 완료하지 못했습니다. 기존 REST 시세를 사용하세요.'})
    finally:
        _BUSY.release()
