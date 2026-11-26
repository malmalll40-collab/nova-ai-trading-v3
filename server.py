"""NOVA v3 mobile PWA API: read-only, no trading endpoints and no broker order capability.
Start locally with `python server.py`; see README for LAN/public access.
"""
from __future__ import annotations

import hmac
import logging
import hashlib
import secrets
import re
import math
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager, contextmanager
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from nova_release import APP_VERSION
from fastapi.staticfiles import StaticFiles
from engine import Config, feature_frame, decide
from structural_risk import evidence_from_features, reprice_from_evidence
from backtest import run_backtest
from data_sources import demo_data, kis_data, ticker_format, market_zone
from stock_lookup import search_stocks
from dart_financial import DARTError, get_client
from finance_gate import assess_financial
from signal_policy import resolve_final_signal
from signal_journal import evaluate_batch
from evidence_store import backend as evidence_backend, append_analysis, count_snapshots, list_snapshots
from market_sessions import krx_session
from completed_session import completed_swing_bars
from paper_lab import simulate_paper
from auto_evidence import (store_scan as store_auto_scan, evaluate_pending as evaluate_auto_pending,
                           report as auto_evidence_report, status as auto_evidence_status,
                           evaluate_server_observations as evaluate_auto_observed,
                           record_auto_attempt)
from news_sources import fetch_news, NewsUnavailable
from signal_quality import quality_gate
from high_precision_adoption import observe_high_precision
from shadow_context import build_shadow_inputs
from kis_reference import fetch_kis_reference
from quality_validation import quality_comparison
from signal_stats import signal_performance
from higher_timeframes import PERIODS, chart_data, fetch_long_daily, fetch_kis_native_period, latest_kis_period
from chart_display import display_daily
from trend_context import summarize_higher_trends
from market_scanner import run_market_scan, MarketScanError, diagnose_connection, completed_dates
from scan_freshness import assess_scan_sessions
from public_market_data import FSCStockClient
from recommendation_backtest import run_screen_history
from investor_flow import stock_code, get_flows, get_quote, get_intraday
from naver_investor import get_naver_flows, NaverFlowError
from popular_stocks import fetch_popularity
from regime_research import capture_market_context, regime_report
from core_comparison import compare as compare_research_engines
from candle_research import build_candle_research
from nova_v5_research import analyze as v5_research_lab
from nova_v53_audit import summarize_batch
from price_source_audit import audit as audit_price_sources, yahoo_price_bases
from kis_ws_readonly import status as kis_ws_status, relay as kis_ws_relay

KIS_CHART_LOG = logging.getLogger('nova.kis.chart')
BASE = Path(__file__).resolve().parent
ASSETS = BASE / 'static'
@asynccontextmanager
async def _app_lifespan(application):
    # Called by ASGI after the module is fully initialized, not during import.
    # Lifespan is supported by both older and newer FastAPI versions.
    try:
        _ops_startup()
    except Exception:
        logging.getLogger('uvicorn.error').warning('NOVA_OPS startup diagnostic failed; service remains available')
    stop_preflight = threading.Event()
    try:
        threading.Thread(target=_ops_preflight_loop,args=(stop_preflight,),daemon=True,name='nova-ops-preflight').start()
    except Exception:
        logging.getLogger('uvicorn.error').warning('NOVA_OPS preflight monitor unavailable')
    try:
        yield
    finally:
        stop_preflight.set()


app = FastAPI(title='NOVA AI Trading Mobile', docs_url=None, redoc_url=None, openapi_url=None, lifespan=_app_lifespan)
app.mount('/static', StaticFiles(directory=ASSETS), name='static')
CACHE_LOCK = threading.Lock()
# Exact-key locks coalesce identical requests without unrelated hash collisions.
# Reference counts include waiters; the registry is bounded by active requests.
CACHE_KEY_LOCKS = {}
CACHE = {}
RATE = {}
SCAN_STATUS_RATE = {}
CACHE_TTL = 52
FIN_CACHE_TTL = 6 * 3600
JOURNAL_CACHE_LOCK = threading.Lock()
JOURNAL_FRAMES = {}
JOURNAL_CACHE_TTL = 12 * 60
WATCH_CACHE_TTL = 90
MAX_CACHE = 72
KST = ZoneInfo('Asia/Seoul')
SCAN_LOCK = threading.RLock()
SCAN_STATE = {'status':'idle','progress':{},'result':None,'error':None,'started_monotonic':None,'market':'all'}
SCAN_JOB = None
SCAN_CANCEL = threading.Event()
SCAN_PROFILES = {'full': SCAN_STATE, 'pattern': dict(SCAN_STATE)}
SCAN_EXECUTING = ('running','cancelling','finalizing')
SCAN_ACTIVE = SCAN_EXECUTING + ('queued',)
SCAN_PENDING = {}
BACKTEST_LOCK = threading.RLock()
BACKTEST_STATE = {'status':'idle','result':None,'error':None,'progress':{},'market':'all','weeks':4}
BACKTEST_JOB = None
POPULAR_LOCK = threading.RLock()
POPULAR_STATE = {'status':'idle','source':None,'result':None,'error':None}
POPULAR_JOB = None
AUTO_LOCK = threading.RLock()
AUTO_JOB = None
AUTO_STATE = {'status':'idle','started_at':None,'completed_at':None,'error_code':None,'result':None}
LABEL = {'BUY':'매수 후보', 'SELL':'매도·보유분 축소 검토', 'HOLD':'관망'}
MODES = {'1분','5분','15분','스윙'}
SOURCES = {'kis','demo','auto'}
AUTH_COOKIE = 'nova3_auth'
REMEMBER_SECONDS = 30 * 24 * 3600
SESSION_SECONDS = 12 * 3600

if os.environ.get('NOVA_PUBLIC_DEPLOY') == '1' and len(os.environ.get('NOVA_ACCESS_TOKEN','')) < 16:
    raise RuntimeError('공개 서버 배포시 NOVA_ACCESS_TOKEN을 16자 이상으로 설정하세요.')


def _cookie_key(token: str) -> bytes:
    # Server-only key. Changing NOVA_ACCESS_TOKEN invalidates all remembered devices.
    return hashlib.sha256(('nova3-cookie:v1:' + token).encode('utf-8')).digest()


def _cookie_valid(value: str, token: str) -> bool:
    if not value or not token:
        return False
    try:
        version, expiry, nonce, signature = value.split('.')
        if version != 'v1' or not expiry.isdecimal() or len(nonce) != 32 or len(signature) != 64:
            return False
        if not all(ch in '0123456789abcdef' for ch in nonce + signature):
            return False
        exp = int(expiry)
        now = int(time.time())
        if not now < exp <= now + REMEMBER_SECONDS:
            return False
        message = f'{version}.{expiry}.{nonce}'
        expected = hmac.new(_cookie_key(token), message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False


def _is_authenticated(request: Request) -> bool:
    token = os.environ.get('NOVA_ACCESS_TOKEN', '')
    if not token:
        return True
    supplied = request.headers.get('Authorization', '')
    if supplied and hmac.compare_digest(supplied, 'Bearer ' + token):
        return True
    return _cookie_valid(request.cookies.get(AUTH_COOKIE, ''), token)


def _authorize(request: Request):
    if not _is_authenticated(request):
        raise HTTPException(401, '인증이 만료됐습니다. 설정에서 서버 접근 토큰을 입력하세요.')


def _secure_cookie(request: Request) -> bool:
    # Render sets NOVA_PUBLIC_DEPLOY=1. Local HTTP development continues to work.
    return os.environ.get('NOVA_PUBLIC_DEPLOY') == '1' or request.url.scheme == 'https'


@app.get('/api/auth/status')
def auth_status(request: Request):
    return JSONResponse({'authenticated': _is_authenticated(request),
                         'token_required': bool(os.environ.get('NOVA_ACCESS_TOKEN'))},
                        headers={'Cache-Control': 'no-store'})


@app.post('/api/auth/login')
def auth_login(request: Request, body: dict):
    _limit(request)
    expected = os.environ.get('NOVA_ACCESS_TOKEN', '')
    supplied = body.get('token', '')
    remember = body.get('remember') is True
    if not isinstance(supplied, str) or len(supplied) > 1024:
        raise HTTPException(400, '올바르지 않은 토큰 형식입니다.')
    if expected and not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, '서버 접근 토큰이 일치하지 않습니다.')
    response = JSONResponse({'authenticated': True, 'remembered': remember, 'days': 30 if remember else 0},
                            headers={'Cache-Control': 'no-store'})
    if expected:
        duration = REMEMBER_SECONDS if remember else SESSION_SECONDS
        expiry = str(int(time.time()) + duration)
        message = f'v1.{expiry}.{secrets.token_hex(16)}'
        signature = hmac.new(_cookie_key(expected), message.encode(), hashlib.sha256).hexdigest()
        response.set_cookie(AUTH_COOKIE, message + '.' + signature,
                            max_age=duration if remember else None,
                            httponly=True, secure=_secure_cookie(request),
                            samesite='lax', path='/')
    return response


@app.post('/api/auth/logout')
def auth_logout(request: Request):
    response = JSONResponse({'authenticated': False}, headers={'Cache-Control':'no-store'})
    response.delete_cookie(AUTH_COOKIE, path='/', secure=_secure_cookie(request), httponly=True, samesite='lax')
    return response


def _limit(request: Request):
    ip = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with CACHE_LOCK:
        ts = [x for x in RATE.get(ip,[]) if now-x < 60]
        if len(ts) >= 24:
            raise HTTPException(429, '요청이 너무 많습니다. 60초 후 다시 조회하세요.', headers={'Retry-After': '60'})
        ts.append(now)
        RATE[ip] = ts
        if len(RATE)>500:
            RATE.clear()


def _limit_scan_status(request: Request):
    """Bound cheap authenticated status reads separately; never relax the data API cap."""
    ip=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with CACHE_LOCK:
        for key, stamps in list(SCAN_STATUS_RATE.items()):
            if not stamps or now-stamps[-1]>=60:
                SCAN_STATUS_RATE.pop(key,None)
        ts=[x for x in SCAN_STATUS_RATE.get(ip,[]) if now-x<60]
        if len(ts)>=12 or (ip not in SCAN_STATUS_RATE and len(SCAN_STATUS_RATE)>=500):
            retry=max(1,math.ceil(60-(now-ts[0]))) if ts else 60
            raise HTTPException(429,'검색 상태 조회 제한입니다. 잠시 후 다시 확인합니다.',headers={'Retry-After':str(retry)})
        SCAN_STATUS_RATE[ip]=ts+[now]


@contextmanager
def _cache_key_lock(key):
    with CACHE_LOCK:
        entry = CACHE_KEY_LOCKS.get(key)
        if entry is None:
            entry = [threading.Lock(), 0]
            CACHE_KEY_LOCKS[key] = entry
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with CACHE_LOCK:
            entry[1] -= 1
            if entry[1] == 0:
                del CACHE_KEY_LOCKS[key]


def _cached(key, make, ttl=CACHE_TTL):
    # Double-check under an exact-key lock: concurrent requests for the same
    # symbol should not simultaneously hit Yahoo/Naver/KIS quota limits.
    now = time.monotonic()
    with CACHE_LOCK:
        entry = CACHE.get(key)
        if entry and now-entry[0] <= ttl:
            return dict(entry[1], cached=True, cache_age_seconds=round(now-entry[0]))
    with _cache_key_lock(key):
        now = time.monotonic()
        with CACHE_LOCK:
            entry = CACHE.get(key)
            if entry and now-entry[0] <= ttl:
                return dict(entry[1], cached=True, cache_age_seconds=round(now-entry[0]))
        # No fabricated fallback: a failed provider fetch remains an error.
        result = make()
        with CACHE_LOCK:
            if len(CACHE)>=MAX_CACHE:
                CACHE.clear()
            CACHE[key]=(time.monotonic(),result)
        return dict(result,cached=False,cache_age_seconds=0)


def _safe_float(n):
    try:
        v=float(n)
        return round(v,6) if math.isfinite(v) else None
    except (ValueError,TypeError):return None


def _last_age_minutes(bar:pd.Timestamp, ticker:str, mode:str)->float|None:
    try:
        stamp = pd.Timestamp(bar)
        if stamp.tzinfo is None:
            stamp=stamp.tz_localize(market_zone(ticker))
        if mode=='1분':stamp+=pd.Timedelta(minutes=1)
        elif mode=='5분':stamp+=pd.Timedelta(minutes=5)
        elif mode=='15분':stamp+=pd.Timedelta(minutes=15)
        else:stamp+=pd.Timedelta(hours=16 if market_zone(ticker).key=='America/New_York' else 15,minutes=30 if market_zone(ticker).key=='Asia/Seoul' else 0)
        return max(0,round((pd.Timestamp.now(tz='UTC')-stamp.tz_convert('UTC')).total_seconds()/60,1))
    except Exception: return None



def _get_financial_state(symbol: str, mode: str) -> tuple[str, str, int | None]:
    """Consistent DART assessment for both individual and watchlist signals.

    DART is a best-effort overlay. A missing/failed response MUST never
    promote a domestic swing BUY. We do not leak credentials or provider URLs.
    """
    if mode != '스윙':
        return 'NOT_APPLIED', '단타는 재무 필터를 적용하지 않습니다.', None
    if not symbol.endswith(('.KS', '.KQ')):
        return 'NOT_SUPPORTED', 'OpenDART 미지원 해외 종목입니다.', None
    try:
        client = get_client()
        fp = hashlib.sha256(client.key.encode()).hexdigest()[:12]
        overview = _cached(('dart', fp, symbol, 3),
                           lambda: client.overview(symbol, years=3), ttl=FIN_CACHE_TTL)
        gate = assess_financial(overview)
        return gate['gate'], gate['reason'], gate['as_of_annual_year']
    except Exception:
        # Never expose provider details, API key or auth headers in a response.
        return 'UNKNOWN', 'OpenDART 재무정보 미확인: 검증할 수 없습니다.', None



def _payload(symbol:str,mode:str,source:str,cost_bps:int,asof_date:str|None=None,*,scan_profile:str|None=None)->dict:
    if scan_profile is not None and (scan_profile not in ('full','pattern') or asof_date is None or mode!='스윙' or source not in ('kis','auto')):
        raise ValueError('Invalid scan profile')
    cfg = Config.for_mode(mode, cost_bps)
    resolved_source=source
    fallback_reason=None
    if source=='demo':
        market=demo_data(mode)
    elif source in ('auto','kis'):
        key,secret=_kis_creds()
        if scan_profile is not None:
            from nova_v593_scan_cache import CACHE as verified_cache
            account_scope=hashlib.sha256((key+'\0'+secret).encode()).hexdigest()
            market=verified_cache.fetch(symbol,asof_date,account_scope,lambda:kis_data(symbol,mode,key,secret,scan_asof=asof_date))
        else:
            market=kis_data(symbol,mode,key,secret)
        resolved_source='kis'
    else:
        raise ValueError('KIS 전용 분석입니다. Yahoo는 수동 비교 진단에서만 사용합니다.')
    scan_freshness=None
    if asof_date is not None:
        # Compare actual provider sessions BEFORE removing future bars. Historical
        # publication lag must never be mistaken for today's live-price freshness.
        scan_freshness=assess_scan_sessions(market.bars.index,asof_date)
        report=getattr(market,'daily_integrity',None) or {}
        if scan_profile is not None and report.get('scan_asof')==asof_date:
            raw=report.get('raw_scan_freshness')
            if isinstance(raw,dict):scan_freshness=dict(raw,scan_cache=report.get('scan_cache'))
            else:scan_freshness=dict(scan_freshness,stale=True,state='RAW_COVERAGE_MISSING')
            if report.get('signal_hold') or not report.get('verified'):
                scan_freshness=dict(scan_freshness,stale=True,state=report.get('state','INTEGRITY_UNVERIFIED'),reason=report.get('reason','기준일 봉 검증 실패'))
        # FSC T+1 / KRX snapshot alignment. Recompute ALL indicators, backtests
        # and signals after dropping future bars; don't just relabel a current signal.
        if mode!='스윙' or source not in ('kis','auto') or not re.fullmatch(r'\d{8}',asof_date):
            raise ValueError('일별 공식 시세의 완료봉 기준일이 잘못되었습니다.')
        historical=market.bars.loc[[str(pd.Timestamp(t).date()).replace('-','')<=asof_date for t in market.bars.index]].copy()
        if len(historical)<110:
            raise ValueError('시장 기준일에 맞는 과거 완료봉이 충분하지 않습니다.')
        if str(pd.Timestamp(historical.index[-1]).date()).replace('-','')!=asof_date:
            raise ValueError('KIS 완료봉과 공식 스냅샷 기준일이 일치하지 않습니다.')
        market.bars=historical
        market.quote=None  # The live quote is later than the as-of snapshot.
        market.quote_time=''  # Never label today's quote as historical.
        market.note=str(market.note or '') + ' · 공식 일별 기준일로 과거 완료봉 재계산'
    if resolved_source=='kis' and mode=='스윙' and asof_date is None:
        g=getattr(market,'daily_integrity',None)
        try:
            from kis_daily_archive import archive_verified, save_regular
            if g is not None:g['archive_state']=save_regular(symbol,market.bars,g)
            archive_verified(symbol,market.bars,g)
        except Exception:
            if g is not None:g['archive_state']='WRITE_FAILED'
            # archive failure never alters signals or existing evidence
    completed_session=None
    if mode=='스윙' and not market.synthetic and asof_date is None:
        # Strip any still-open daily bar *before* indicators/backtests/signals.
        # This also respects KRX's special session close (e.g. 16:30 exam day).
        market.bars, completed_session = completed_swing_bars(market.bars)
        if len(market.bars)<65:
            raise ValueError('확정 거래일 기준 완료봉이 65개 미만입니다. 분석을 보류합니다.')
    f=feature_frame(market.bars,mode)
    if scan_profile is None:
        higher_trend = (summarize_higher_trends(market.bars, '005930.KS' if market.synthetic else market.symbol, now=datetime.now(KST), historical=asof_date is not None)
                        if mode == '스윙' else None)
        d=decide(f,cfg)
        bt=run_backtest(market.bars,cfg)
        q=quality_gate(f,cfg,raw_signal=d.signal)
        compared=quality_comparison(f,cfg)
        signal_stats=signal_performance(f,cfg)
    age=_last_age_minutes(f.index[-1],market.symbol if not market.synthetic else '005930.KS',mode)
    fresh_limit={'1분':8, '5분':25, '15분':55, '스윙':3*24*60}[mode]
    live_price_stale = False if market.synthetic else age is None or age > fresh_limit
    # A market-wide as-of scan compares completed historical sessions; do NOT
    # apply a 72-hour wall clock to an older Friday snapshot on Monday.
    # Live swing analysis uses completed KRX sessions; intraday remains wall-clock based.
    integrity_state=getattr(market,'daily_integrity',None)
    integrity_hold=bool(integrity_state and integrity_state.get('signal_hold'))
    stale = integrity_hold or (bool(scan_freshness['stale']) if scan_freshness is not None
             else bool(completed_session['stale']) if completed_session is not None
             else live_price_stale)
    fgate, fnote, fyear = _get_financial_state(market.symbol, mode) if not market.synthetic else ('NOT_APPLIED', '가상 데이터', None)
    if scan_profile is not None:
        from nova_v588_scan_projection import project
        return project(f,cfg,market=market,source=resolved_source,stale=stale,asof=asof_date,
            financial_gate=fgate,finance_note=fnote,scan_freshness=scan_freshness,profile=scan_profile,
            decision=decide,quality=quality_gate,stats=signal_performance,policy=resolve_final_signal,
            safe_float=_safe_float)
    policy = resolve_final_signal(q['strict_signal'], mode=mode, symbol=market.symbol,
                                  financial_gate=fgate, finance_reason=fnote,
                                  stale=stale, is_demo=market.synthetic)
    # Live-observed reference context for read-only research only.  A past
    # market-scan date NEVER loads today's source data for historic decisions.
    # References are cached individually; even a provider outage may not change
    # the existing actionable BUY/SELL or prevent the original analysis.
    research_context = {'benchmark': None, 'sector': None, 'flows': None, 'diagnostics': {}}
    try:
        if mode == '스윙' and market.symbol.endswith(('.KS','.KQ')) and not market.synthetic:
            def cached_ref(ticker):
                return _cached(('research_ref_20261112',ticker),
                               lambda: {'frame':fetch_kis_reference(ticker)}, ttl=900)['frame']
            def cached_flow(code):
                # Reuse exactly the KIS/Naver result already fetched in the
                # dedicated investor-flow tab.  Do not trigger slow, repeated
                # unofficial scraping from the stock-analysis endpoint.
                with CACHE_LOCK:
                    c=CACHE.get(('v46_flow_per_investor_20261112',code))
                    return c[1] if c and time.monotonic()-c[0]<=600 else None
            research_context=build_shadow_inputs(market.symbol,now=datetime.now(KST),
                            ref_loader=cached_ref,flow_loader=cached_flow,
                            historical_asof=asof_date)
    except Exception:
        research_context['diagnostics']={'overall':{'status':'UNAVAILABLE',
               'detail':'연구 자료 조회 실패 (기존 매수 판단 영향 없음)'}}
    try:
        hp = observe_high_precision(features=f, cfg=cfg, symbol=market.symbol,
                                    mode=mode, is_demo=market.synthetic, is_stale=stale,
                                    financial_gate=fgate, decision_at=datetime.now(KST),
                                    benchmark=research_context.get('benchmark'),
                                    sector=research_context.get('sector'),
                                    flows=research_context.get('flows'))
        hp['context_data']=research_context['diagnostics']
    except Exception:
        # Research overlay must never interrupt the legacy analysis response.
        hp = {'adoption_mode':'SHADOW_ONLY', 'state':'RESEARCH_ERROR',
              'display':'고정밀 연구 오류 · 기존 신호는 유지',
              'baseline_signal_modified':False, 'live_orders_approved':False,
              'performance_advantage_verified':False, 'checks':[], 'reasons':['연구 관찰 처리 오류'],
              'disclaimer':'연구 관찰 전용 · 실제 주문 신호가 아닙니다.'}
    # v4.7.3: independent, read-only 13-role / 5-core side-by-side research.
    # Must never change policy, signals, database observations, or broker actions.
    try:
        core_comparison = compare_research_engines(
            f, cfg, benchmark=research_context.get('benchmark'), source=resolved_source,
            asof_date=asof_date, is_demo=market.synthetic)
    except Exception:
        logging.getLogger('nova.core').exception('Read-only core comparison unavailable')
        core_comparison = {'version':'4.7.3','status':'ERROR','research_only':True,
            'modifies_live_signal':False,'reason':'비교 연구 계산 오류. 기존 신호와 분석 결과는 유지됩니다.'}
    # v4.7.4: historical per-bar diagnostics, strictly observational.
    # Calculation failures must never change the final actionable signal.
    try:
        candle_research = build_candle_research(
            f, cfg, benchmark=research_context.get('benchmark'), source=resolved_source,
            asof_date=asof_date, is_demo=market.synthetic)
    except Exception:
        logging.getLogger('nova.candle_research').exception('Candle research unavailable')
        candle_research = {'version':'4.7.4','status':'ERROR','research_only':True,
            'modifies_live_signal':False,'reason':'캔들별 연구 계산 실패 · 기존 분석 신호는 유지됩니다.',
            'observations':[],'role_statistics':[]}
    # Separate as-of pattern overlay: reuse existing completed features only.
    signal_pattern_research=None
    if asof_date is not None:
        try:
            from nova_v584_pattern_scan import from_features
            signal_pattern_research=from_features(f,cfg,asof=asof_date,source=resolved_source,
                stale=stale,demo=market.synthetic,financial_gate=fgate)
        except Exception:
            signal_pattern_research={'status':'UNAVAILABLE','reason':'PATTERN_INPUT_ERROR'}
    core541_current={'status':'UNAVAILABLE','reason':'KIS 최신 검증 완료봉 스윙 연구 전용'}
    if (mode=='스윙' and resolved_source=='kis' and not market.synthetic and not stale and asof_date is None
            and integrity_state and integrity_state.get('verified') and integrity_state.get('regular_volume_verified')
            and integrity_state.get('verification_basis')=='KIS_REGULAR_MINUTE' and integrity_state.get('latest_date')==str(f.index[-1].date())):
        try:
            from nova_v541_core import latest as latest_core541
            core541_current=latest_core541(f,research_context.get('benchmark'))
        except Exception:
            core541_current={'status':'UNAVAILABLE','reason':'5-Core 최신 연구 계산 실패'}
    risk_structure = reprice_from_evidence(evidence_from_features(f, cfg), cost_bps=cost_bps, demo=market.synthetic, stale=stale)
    chart=f.iloc[-125:][['Open','High','Low','Close','Volume','ema20','ema50']]
    times=[x.isoformat() for x in chart.index]
    # Note: role #13 is a risk veto; it is NOT one of the 12 counted directional votes.
    return {
        'symbol': market.symbol,
        'mode':mode,'source':resolved_source,'requested_source':source,
        'source_fallback_reason':fallback_reason,'provider':market.provider,
        'is_demo':market.synthetic, 'is_stale':stale,
        'price_age_minutes':age, 'live_price_stale':live_price_stale,
        'scan_freshness':scan_freshness, 'completed_session':completed_session,
        'daily_integrity':getattr(market,'daily_integrity',None),
        'asof_evaluation_only':asof_date is not None,
        'fetched_at':market.retrieved_at,
        'quote_time':market.quote_time,'last_completed_bar':d.time,
        'freshness':market.freshness,
        'provider_note':market.note+((' · '+fallback_reason) if fallback_reason else ''),
        'currency':market.currency,
        'quote':_safe_float(market.quote),'reference':_safe_float(d.reference),
        'signal':policy['final_signal'],
        'signal_label':('관망 · 지난 완료봉' if stale else LABEL[policy['final_signal']]),
        'strict_signal':q['strict_signal'],
        'raw_signal':d.signal,'quality_gate':q,'quality_comparison':compared,
        'signal_performance':signal_stats,
        'higher_timeframe_context':higher_trend,
        'high_precision_research':hp,
        'core_comparison_research':core_comparison,'core541_current':core541_current,
        'candle_research':candle_research,'signal_pattern_research':signal_pattern_research,
        'signal_is_current':policy['signal_is_current'],
        'final_actionable_signal':policy['final_actionable_signal'],
        'historical_signal':policy['historical_signal'],
        'final_reason':policy['final_reason'],
        'financial_gate':fgate, 'finance_note':fnote,
        'financial_asof_year':fyear,
        'rsi':_safe_float(d.rsi),
        'atr':_safe_float(d.atr),
        'buy_votes':d.buy_votes,'sell_votes':d.sell_votes,'hold_votes':d.hold_votes,
        'risk_veto':d.risk_veto,'risk_message':d.risk_message,
        'long_stop_reference':_safe_float(d.stop),
        'long_target_reference':_safe_float(d.target),
        'long_risk_reward':_safe_float(d.risk_reward),
        'risk_structure':risk_structure,
        'cost_bps_one_side':cost_bps,
        'max_hold_bars':cfg.max_hold_bars,
        'backtest_exit_policy':'종료봉 종가에서 전략과 매수보유 모두 가상청산(편도 수수료 적용)',
        'votes':[{'role':x.role,'direction':x.direction,'label':x.label,'reason':x.reason,'category':x.category} for x in d.votes],
        'chart':{'dates':times,
                 'open':[_safe_float(x) for x in chart['Open']],
                 'high':[_safe_float(x) for x in chart['High']],
                 'low':[_safe_float(x) for x in chart['Low']],
                 'volume':[_safe_float(x) for x in chart['Volume']],
                 'close':[_safe_float(x) for x in chart['Close']],
                 'ema20':[_safe_float(x) for x in chart['ema20']],
                 'ema50':[_safe_float(x) for x in chart['ema50']]},
        'backtest':{
            'total_return_pct':_safe_float(bt.total_return*100),
            'benchmark_return_pct':_safe_float(bt.benchmark_return*100),
            'max_drawdown_pct':_safe_float(bt.max_drawdown*100),
            'closed_trades':bt.closed_trades,
            'win_rate_pct':_safe_float(bt.win_rate*100 if bt.win_rate is not None else None),
            'directional_hit_rate_pct':_safe_float(bt.directional_hit_rate*100 if bt.directional_hit_rate is not None else None),
            'directional_samples':bt.directional_samples,
            'last_20pct_rate_pct':_safe_float(bt.latest_20pct_hit_rate*100 if bt.latest_20pct_hit_rate is not None else None),
            'last_20pct_samples':bt.latest_20pct_samples,
            'buy_signals':bt.buy_signals,'sell_signals':bt.sell_signals,
            'curve':{'dates':[str(i) for i in bt.curve.index[::max(1,len(bt.curve)//90)]],
                      'strategy':[_safe_float(v) for v in bt.curve['전략 자산'].iloc[::max(1,len(bt.curve)//90)]],
                      'benchmark':[_safe_float(v) for v in bt.curve['비교 매수보유'].iloc[::max(1,len(bt.curve)//90)]]},
            'recent_trades':[
                {'entry':str(x['진입시점']),'exit':str(x['청산시점']),
                 'pnl_pct':_safe_float(x['수익률']*100),'reason':str(x['청산사유'])}
                for x in bt.trades.tail(8).iloc[::-1].to_dict('records')],
        },
        'disclaimer':'규칙 기반 분석이며 미래 수익률을 예측·보장하지 않습니다. 마지막 완료봉에 근거하며 매도는 보유분 축소 의견입니다.',
    }


@app.get('/')
def index():
    return HTMLResponse((ASSETS/'index.html').read_text(encoding='utf-8').replace('__NOVA_APP_VERSION__',APP_VERSION),headers={'Cache-Control':'no-cache'})

@app.get('/favicon.ico')
def favicon():
    # Some browsers request the legacy path even when a PNG icon link exists.
    return FileResponse(ASSETS/'icons/icon-192.png',media_type='image/png',headers={'Cache-Control':'public, max-age=86400'})

@app.get('/manifest.webmanifest')
def manifest():
    return FileResponse(ASSETS/'manifest.webmanifest',media_type='application/manifest+json')

@app.get('/sw.js')
def service_worker():
    return Response((ASSETS/'sw.js').read_text(encoding='utf-8').replace('__NOVA_APP_VERSION__',APP_VERSION),media_type='application/javascript',headers={'Cache-Control':'no-store','Service-Worker-Allowed':'/'})

@app.get('/healthz')
def healthz():
    return {'status':'ok','orders_enabled':False,'app_version':APP_VERSION,'pattern_research_ui':True,'v570_research':True,'v570_live_adoption':False,'ops_diagnostics':True,'v561_research':True,'v561_live_adoption':False,'v560_research':True,'v560_live_adoption':False,'v552_research':True,'v552_live_adoption':False,'analysis_chart_live':True,'v550_research':True,'v550_live_adoption':False,'analysis_modes':['1분','5분','15분','스윙'],'higher_trend_context_research_only':True,'release_tag':'4.5-stability-20261112','feature_tag':'4.5-domestic-only-20261112','scan_freshness_tag':'4.5-session-aware-rejection-20261112','flow_history_tag':'4.5-nav-flow-per-investor-20261112','stock_name_search':True,'dart_feature':True,'financial_risk_filter':True,'historical_fundamental_backtest':False,'forward_signal_journal':True,'evidence_lab_version':'4.7.0-auto-research','persistent_server_journal':evidence_backend()!='disabled','watchlist_visible_tab_only':True,'push_notifications':False,'strict_signal_quality':True,'quality_hit_rate_verified':False,'final_signal_policy':'financial-then-notification','intraday_journal_verified':False,'candlestick_chart':True,'stock_news':True,'news_affects_signal':False,'signal_role_statistics':True,'calibrated_buy_probability':False,'higher_timeframe_charts':True,'higher_timeframes_affect_signal':False,'market_recommendations':True,'nasdaq_search':False,'nasdaq_watchlist_scan':False,'us_tickers_enabled':False,'korean_market_only':True,'market_scope_tag':'4.5-domestic-only-20261112','domestic_interest_candidates':True,'market_scan_requires_krx_api':False,'market_scan_fsc_tplus1':True,'realtime_market_scan':False,'server_persistent_scan_results':evidence_backend()!='disabled','historical_market_screen_backtest':True,'full_recommendation_strategy_backtested':False,'high_precision_adoption_mode':'SHADOW_ONLY','high_precision_live_validated':False,'shadow_context_tag':'5.3.4-kis-index-etf-proxy','investor_flows_daily':True,'investor_flows_naver_fallback':True,'structure_levels_research_only':True,'krx_market_calendar':'XKRX_with_verified_special_hours','quote_rest_polling':True,'realtime_quote_websocket':False,'kis_ws_optional_readonly':True,'kis_ws_enabled':kis_ws_status()['enabled'],'v54_walk_forward_accuracy':True,'v542_hybrid_research':True,'v542_hybrid_live_adoption':False,'v541_core_adaptive_research':True,'v541_core_live_adoption':False,'v54_live_adoption':False,'v5_lab_enabled':True,'v51_batch_research':True,'v51_trade_ledger':True,'v52_risk_audit':True,'v52_filters_live_adopted':False,'v521_aligned_backtest':True,'v521_calendar_diagnostics':True,'v53_evidence_gate':True,'v53_shadow_role_groups':True,'v53_live_adoption':False,'v531_price_source_audit':True,'v532_kis_session_integrity_guard':True,'v532_kis_cross_checked_requery':True,'v531_price_reconciliation_live_adopted':False,'v5_ai_auto_adoption':False,'v5_ml_probability_calibrated':False,'v5_ui_tabs':6,'kis_period_rest_polling':True,'kis_period_native_ohlcv':True,'auto_swing_kis_first':True,'kis_regular_ohlcv_reconstruction':True,'kis_only_analysis':True,'yahoo_manual_audit_only':True,'completed_session_freshness':True,'kis_period_refresh_seconds':20,'domestic_activity_top20':True,'naver_popular_official_link_only':True,'recommendation_breadth':'24-48-60','market_regime_research':'observed_only_shadow_v47','market_regime_live_adoption':False,'candle_research_history':True,'core_comparison_research_only':True,'core_live_adoption':False,'strict_buy_policy_unchanged':True,'investor_flow_research_only':True,'recommendation_ui_release':'2026-11-20-status-rank-cachefix'}

@app.get('/api/status')
def status(request:Request):
    _authorize(request)
    return {'kis_configured':bool(os.environ.get('KIS_APP_KEY') and os.environ.get('KIS_APP_SECRET')),
            'dart_configured':len(os.environ.get('OPENDART_API_KEY','').strip())==40,
            'news_provider_configured':bool(os.environ.get('NAVER_NEWS_CLIENT_ID') and os.environ.get('NAVER_NEWS_CLIENT_SECRET')),
            'news_provider_fallback':'google_news_rss',
            'krx_configured':bool(os.environ.get('KRX_API_KEY','').strip()),
            'fsc_configured':bool(os.environ.get('FSC_STOCK_API_KEY','').strip()),
            'marketwide_scanner':'금융위원회 주식시세 T+1 우선, KRX 선택 지원',
            'server_time_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'release_tag':'4.5-domestic-only-20261112',
            'quote_provider':'KIS_ONLY', 'quote_requires_kis_credentials':True,
            'investor_flow_provider':'KIS_THEN_NAVER', 'kis_status':'CONFIGURED_NOT_VERIFIED' if (os.environ.get('KIS_APP_KEY','').strip() and os.environ.get('KIS_APP_SECRET','').strip()) else 'NOT_CONFIGURED',
            'yahoo_realtime_guaranteed':False,'streaming':False,'real_orders':False,
            'high_precision_adoption_mode':'SHADOW_ONLY','high_precision_live_validated':False,'investor_flow_enabled':True,'investor_flow_naver_public_fallback':True,'quote_snapshot_refresh_seconds':20,
            'lan_without_auth':os.environ.get('NOVA_BIND_HOST')=='0.0.0.0' and not bool(os.environ.get('NOVA_ACCESS_TOKEN'))}

# v4.5: read-only KIS investor flows and near-live REST quotation snapshots.
# They are not blended into the 1st-stage selection or historical backtest.
def _kis_creds():
    key, secret = os.environ.get('KIS_APP_KEY', '').strip(), os.environ.get('KIS_APP_SECRET', '').strip()
    if not key or not secret:
        raise HTTPException(503, 'KIS 앱 키가 미설정입니다. Render에 KIS_APP_KEY와 KIS_APP_SECRET을 설정하세요.')
    return key, secret

@app.get('/api/market/session')
def market_session(request: Request):
    """Regular KRX session only: read-only, never a trading permission."""
    _authorize(request)
    return JSONResponse(krx_session(), headers={'Cache-Control': 'no-store'})


@app.get('/api/market/flows')
def market_flows(request: Request, symbol: str = '005930'):
    _authorize(request)
    _limit(request)
    try: code = stock_code(symbol)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    def resolve_flow():
        # Existing KIS is retained; public Naver only when KIS is absent,
        # failed or did not supply completed data. No trading signals modified.
        key = os.environ.get('KIS_APP_KEY', '').strip()
        secret = os.environ.get('KIS_APP_SECRET', '').strip()
        kis_problem = 'KIS 미설정'
        if key and secret:
            try:
                kis = get_flows(key, secret, code)
                has_quantities = any(
                    any(row.get(name) is not None for name in ('foreign', 'institution', 'individual'))
                    for row in kis.get('daily', []) if isinstance(row, dict)
                )
                if kis.get('has_data') and has_quantities:
                    kis['provider'] = 'kis'
                    kis['independent_source_verified'] = False
                    rows = kis.get('daily') or []
                    if len(rows) < 20 or any(not a.get('complete') for a in kis.get('aggregate',[]) if a.get('period')==20):
                        kis['history_expansion_status'] = (
                            f'KIS 완료 일별 {len(rows)}건: KIS 조회 성공으로 네이버 대체 조회를 실행하지 않음. '
                            '원천 20일 부족·필드 누락은 미확인입니다.')
                    return kis
                kis_problem = 'KIS 완료 거래일 자료 없음'
            except Exception:
                kis_problem = 'KIS 인증·권한 또는 응답 오류'
        try:
            naver = get_naver_flows(code)
            naver['fallback_reason'] = kis_problem
            return naver
        except NaverFlowError as exc:
            raise HTTPException(502, f'수급 자료 미확인: {kis_problem}; {exc}') from None
        except Exception:
            raise HTTPException(502, f'수급 자료 미확인: {kis_problem}; 네이버 조회 처리 오류') from None

    result = _cached(('v46_flow_per_investor_20261112', code), resolve_flow, ttl=300)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})

@app.get('/api/market/intraday')
def market_intraday(request: Request, symbol: str = '005930'):
    _authorize(request)
    _limit(request)
    try: code = stock_code(symbol)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    key, secret = _kis_creds()
    try:
        result = _cached(('v45_intraday', code, datetime.now(KST).date().isoformat()),
                         lambda: get_intraday(key, secret, code), ttl=75)
    except Exception:
        raise HTTPException(502, 'KIS 당일 분봉 수신 실패. 서버 API 및 장중 데이터 제공 여부를 확인하세요.') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})

@app.get('/api/market/quote')
def market_quote(request: Request, symbol: str = '005930'):
    _authorize(request)
    _limit(request)
    try: code = stock_code(symbol)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    key, secret = _kis_creds()
    try:
        result = _cached(('v45_quote', code), lambda: get_quote(key, secret, code), ttl=10)
    except Exception:
        raise HTTPException(502, 'KIS 현재가 수신 실패. API 사용 승인·시장 시간·네트워크를 확인하세요.') from None
    result={**result,'price_scope':'KIS J 조회 시점 현재가 · 정규장 확정 종가와 다를 수 있음','regular_close_verified':False}
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})

@app.get('/api/news')
def news_search(request: Request, symbol: str = '', name: str = ''):
    """Read-only stock news, explicitly not used by the trade signal engine."""
    _authorize(request)
    _limit(request)
    try:
        if not symbol and not name:
            raise ValueError('종목명 또는 종목코드를 입력하세요.')
        # Do not resolve decommissioned US tickers via the news entry point.
        if symbol and not re.fullmatch(r'\d{6}(?:\.(?:KS|KQ))?',symbol.strip().upper()):
            raise ValueError('뉴스 조회는 국내 코스피·코스닥 종목코드만 지원합니다.')
        # TTL avoids provider overuse and prevents accidental real-time claims.
        query_key=(symbol.strip().upper(),name.strip())
        result=_cached(('v39_news',query_key),
                       lambda:fetch_news(*query_key),ttl=600)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    except NewsUnavailable as exc:
        raise HTTPException(503,str(exc)) from None
    except Exception:
        raise HTTPException(502,'뉴스 수신 실패. 원문 검색 링크를 이용해 주세요.') from None


@app.get('/api/chart/history')
def long_chart(request: Request, symbol: str = '005930.KS', source: str = 'kis', timeframe: str = '1w'):
    """Dedicated chart-only aggregation; DOES NOT change NOVA's trading signals."""
    _authorize(request)
    _limit(request)
    if source not in SOURCES:raise HTTPException(422,'KIS 또는 DEMO만 지원합니다.')
    if source=='auto':source='kis'
    if timeframe not in PERIODS:raise HTTPException(422,'차트 주기는 일봉·주봉·월봉·연봉만 지원합니다.')
    if source=='demo':symbol='DEMO'
    else:
        try:symbol=ticker_format(symbol)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if source!='demo' and not symbol.endswith(('.KS','.KQ')):
        raise HTTPException(422,'국내 코스피·코스닥 종목만 차트 조회를 지원합니다.')
    if source == 'kis':
        _kis_creds()
        try:
            # Native D/W/M/Y bars provide several years of weekly/monthly/
            # yearly history, unlike the short swing-only KIS daily series.
            # _cached() is a JSON-like dict cache: if given a DataFrame
            # directly, it converts it to {column: Series}, breaking
            # chart_data() and producing HTTP 502 even with valid KIS bars.
            # Keep the DataFrame intact inside a dict envelope.
            moment=datetime.now(KST)
            schedule=krx_session(moment)
            phase=('settled' if moment.hour>=18 else 'open' if schedule.get('is_open') else
                   'postclose' if schedule.get('close_kst') and moment.strftime('%H:%M')>=schedule['close_kst'] else 'preopen')
            cache_boundary=(moment.date().isoformat(),phase)
            cached_bars = _cached(('v551-kis-native-period-df', symbol, timeframe, cache_boundary),
                                  lambda: {'bars': fetch_kis_native_period(symbol, timeframe)}, ttl=900)
            bars = cached_bars['bars']
            integrity = None
            provisional = None
            completed_date = None
            if timeframe == '1d':
                # Chart-only display must not label a disputed same-day KIS
                # close as a completed historical candle. Cross-check using
                # the same guard as the swing signal input.
                from data_sources import KIS
                from daily_settlement_guard import verify_latest_kis_daily
                moment=datetime.now(KST)
                latest=pd.Timestamp(bars.index[-1]).date()
                if (moment.date()-latest).days > 10:
                    # Historical display does not require a new broker token;
                    # the existing session-freshness gate handles old bars.
                    api=None
                else:
                    key,secret=_kis_creds()
                    api=KIS(key,secret)
                completed,integrity=verify_latest_kis_daily(
                    bars,api=api,code=symbol[:6],symbol=symbol,now=moment)
                completed_date=(pd.Timestamp(completed.index[-1]).date().isoformat() if not completed.empty else None)
                bars,provisional=display_daily(bars,completed,moment,schedule=schedule)
            result = chart_data(bars, timeframe, symbol, source, now=moment)
            if provisional is not None:
                result['display_current_daily']=provisional
                result['last_completed_daily']=completed_date
                if provisional['status']=='PROVISIONAL':
                    result['chart']['partial'][-1]=True
                    result['partial_last']=True
            if integrity is not None:
                result['daily_integrity']=integrity
            result['provider'] = 'KIS 기간별 시세(KRX) · 이전 봉 세션 미검증'
            if integrity and integrity.get('session_reconstructed'):
                result['provider'] += ' · 최신 정규장 분봉 재구성'
            result['polling_supported'] = True
            result['refresh_seconds'] = 20
            result['streaming'] = False
            result['note'] = ('최신 일봉: KIS 정규장 분봉 재구성 · 이전 봉 세션 미검증. ' if integrity and integrity.get('session_reconstructed') else '') + 'KIS REST 기간별 OHLCV(15분 이내 캐시 가능). 장중 현재 봉은 별도 20초 조회로 갱신합니다. 분석·매매신호에는 미반영됩니다.' + (' · 분석용 최신 일봉 검증 보류: '+str(integrity.get('reason')) if integrity and integrity.get('signal_hold') else '')
            if provisional and provisional['status']=='PROVISIONAL':
                result['note'] += ' · 당일 KIS 원본 봉은 차트 전용 미확정 OHLCV이며 정규장 확정 종가가 아닙니다.'
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception as exc:
            # Preserve only a broker error CODE, not msg1, headers or secrets.
            # Previous release masked every exception as the same 502 and made
            # the DataFrame/dict cache bug impossible to identify from logs.
            match = re.search(r'KIS 시세 오류 ([A-Za-z0-9_-]{1,30}):', str(exc))
            # Distinguish HTTP throttling/server failure from upstream network
            # and payload errors without exposing sensitive broker responses.
            safe_category = re.fullmatch(
                r'KIS_(?:HTTP_[0-9]{1,3}|UPSTREAM_(?:TIMEOUT|NETWORK)|BAD_(?:JSON|PAYLOAD)|RETRY_EXHAUSTED)',
                str(exc))
            safe_code = (match.group(1) if match else
                         safe_category.group(0) if safe_category else type(exc).__name__[:32])
            KIS_CHART_LOG.warning('KIS historical candle request failed: period=%s type=%s code=%s',
                                  timeframe, type(exc).__name__, safe_code)
            raise HTTPException(502, f'KIS 기간별 차트 조회 실패 [{safe_code}]. 인증·시세 조회 권한·호출 제한을 확인하세요.') from None
    try:
        # Expensive max-history download shared across W/M/Y chart selections.
        hist=_cached(('v41-daily-history',source,symbol),lambda:{'bars':fetch_long_daily(symbol,source)},ttl=1800)['bars']
        result=chart_data(hist,timeframe,symbol,source,synthetic=(source=='demo'))
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except Exception as exc:
        # Never return provider request headers, tokens or tracebacks.
        raise HTTPException(502,f'장기 차트 자료 조회 실패 ({type(exc).__name__}). 공급원·종목·네트워크를 확인하세요.') from None


@app.get('/api/chart/analysis-live')
def analysis_live_chart(request: Request, symbol: str='005930.KS', mode: str='스윙'):
    _authorize(request);_limit(request)
    try:
        symbol=ticker_format(symbol)
        if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol) or mode not in ('스윙','1분','5분','15분'):raise ValueError('국내 종목 및 지원 분석주기만 조회 가능')
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    key,secret=_kis_creds()
    from analysis_chart_live import fetch
    try:
        data=_cached(('v552-analysis-display',symbol,mode,datetime.now(KST).date().isoformat()),lambda:fetch(symbol,mode,key,secret),ttl=12)
        return JSONResponse(data,headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(502,'KIS 분석주기 차트 조회 실패 · 마지막 확인 봉 유지') from None


@app.get('/api/chart/live')
def live_period_chart(request: Request, symbol: str = '005930.KS', timeframe: str = '1d'):
    # Read-only broker OHLCV snapshots; does not change trading signals.
    _authorize(request)
    _limit(request)
    if timeframe not in PERIODS:
        raise HTTPException(422, '차트 주기는 일봉·주봉·월봉·연봉만 지원합니다.')
    try:
        symbol = ticker_format(symbol)
        if not symbol.endswith(('.KS', '.KQ')):
            raise ValueError('국내 종목만 지원합니다.')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    _kis_creds()
    try:
        result = _cached(('v551-kis-live-period', symbol, timeframe, datetime.now(KST).date().isoformat()),
                         lambda: latest_kis_period(symbol, timeframe), ttl=12)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from None
    except Exception:
        raise HTTPException(502, 'KIS 장중 봉 조회 실패. 시세 공급원·조회 제한·장 운영 시간을 확인하세요.') from None

@app.get('/api/search')
def stock_search(request: Request, q: str = '', market: str = 'kr'):
    _authorize(request)
    _limit(request)
    if market!='kr':
        raise HTTPException(422,'한국 코스피·코스닥 종목 검색만 지원합니다.')
    if not 1 <= len(q.strip()) <= 40:
        raise HTTPException(422, '검색어를 1~40자로 입력하세요.')
    try:
        result = _cached(('stock_search', 'kr', q.strip().casefold()), lambda: search_stocks(q))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})




@app.get('/api/fundamentals')
def fundamentals(request: Request, symbol: str, years: int = 3):
    """Read-only official OpenDART; no historical trading-signal look-ahead."""
    _authorize(request)
    _limit(request)
    try:
        symbol=ticker_format(symbol)
        client=get_client()
        # Cache key includes a fingerprint of the configured key, not the key itself.
        key_fingerprint=hashlib.sha256(client.key.encode()).hexdigest()[:12]
        result=_cached(('dart',key_fingerprint,symbol,years),
                       lambda:client.overview(symbol,years=years),ttl=FIN_CACHE_TTL)
        # Pure current-time overlay. Deliberately not inserted into historical OHLC backtests.
        body=dict(result)
        body['financial_gate']=assess_financial(body)
        return JSONResponse(body,headers={'Cache-Control':'no-store'})
    except DARTError as exc:
        code=exc.code
        status=503 if code in ('no_key','network','registry_network','020','012','800') else 422
        raise HTTPException(status, {'code':code,'message':str(exc)}) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        # Do not accidentally leak key via requests exception's URL in response.
        raise HTTPException(502, 'DART 재무정보 처리 실패. 서버 로그를 확인하세요.') from None



def _journal_daily_bars(symbol: str, source: str) -> pd.DataFrame:
    """Use finished daily candles; never cache an unfinished in-session bar."""
    key = (symbol, source)
    now = time.monotonic()
    with JOURNAL_CACHE_LOCK:
        cached = JOURNAL_FRAMES.get(key)
        if cached is not None and now - cached[0] <= JOURNAL_CACHE_TTL:
            return cached[1].copy()
    if source == 'kis':
        key_id = os.environ.get('KIS_APP_KEY', '')
        secret = os.environ.get('KIS_APP_SECRET', '')
        if not key_id or not secret:
            raise ValueError('KIS API 키가 서버에 설정되지 않았습니다.')
        d = kis_data(symbol, '스윙', key_id, secret).bars
    else:
        raise ValueError('지원되지 않는 시세 공급원입니다.')
    with JOURNAL_CACHE_LOCK:
        if len(JOURNAL_FRAMES) >= 15:
            JOURNAL_FRAMES.clear()
        JOURNAL_FRAMES[key] = (time.monotonic(), d.copy())
    return d


@app.post('/api/journal/evaluate')
def journal_evaluate(request: Request, body: dict):
    """Evaluate browser-held snapshots, with no server-side journal persistence."""
    _authorize(request)
    _limit(request)
    try:
        return JSONResponse(evaluate_batch(body.get('records'), _journal_daily_bars),
                            headers={'Cache-Control': 'no-store'})
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None



@app.get('/api/evidence/status')
def evidence_status(request: Request):
    _authorize(request)
    enabled = evidence_backend() != 'disabled'
    response = {'enabled': enabled, 'storage': evidence_backend() if enabled else 'browser_only',
                'total': 0, 'scope': '분석 API를 실제 조회한 순간의 스윙 BUY/SELL만 기록',
                'automatic_history_reconstruction': False,
                'note': ('DB 미설정 · 기존 기기 신호 기록만 유지됩니다. Render 무료 파일시스템은 영구 보관용이 아닙니다.'
                         if not enabled else '서버 기록은 최초 관찰 시점에 고정되며, 자동 주문은 수행하지 않습니다.')}
    if enabled:
        try:
            response['total'] = count_snapshots()
        except Exception:
            response['storage_error'] = True
            response['note'] = 'DB 연결 오류 · 신호 기록 보장 불가, 관리자가 저장소를 확인하세요.'
    return JSONResponse(response, headers={'Cache-Control':'no-store'})


@app.post('/api/evidence/report')
def evidence_report(request: Request, body: dict):
    """On-demand prospective outcomes and no-fill single-position paper research."""
    _authorize(request)
    _limit(request)
    if evidence_backend() == 'disabled':
        raise HTTPException(503, '영구 DB가 연결되지 않아 서버 실전관찰 성적표를 생성할 수 없습니다.')
    n=body.get('limit',60)
    if type(n) is not int or not 1 <= n <= 60:
        raise HTTPException(422, '최근 기록 조회 범위는 1~60건입니다.')
    try:
        all_rows=list_snapshots(limit=n)
        total=count_snapshots()
    except Exception:
        raise HTTPException(503, '신호 기록 DB를 조회하지 못했습니다.') from None
    if not all_rows:
        return JSONResponse({'count':0,'total':total,'status':'no_observations',
                             'note':'DB 연결 이후 실제 분석에서 관찰된 신호가 아직 없습니다. 과거 추천을 소급 생성하지 않습니다.'},
                            headers={'Cache-Control':'no-store'})
    # The same bounded source set is used for directional evidence and paper lab.
    keys=[];rows=[]
    for rec in all_rows:
        if rec.get('source')!='kis':continue  # keep legacy records, never reprice with KIS
        pair=(rec['symbol'],rec['source'])
        if pair not in keys:
            if len(keys)>=4:continue
            keys.append(pair)
        rows.append(rec)
    # Sort chronologically for non-overlapping paper trades.
    rows.sort(key=lambda r:(r['observed_at'],r['id']))
    if not rows:
        return JSONResponse({'count':0,'total':total,'status':'no_observations','legacy_yahoo_observations_excluded':len(all_rows),'note':'KIS 관찰 표본이 아직 없습니다. 기존 Yahoo 기록은 보존합니다.'},headers={'Cache-Control':'no-store'})
    try:
        assessed=evaluate_batch(rows,_journal_daily_bars)
        paper=simulate_paper(rows,_journal_daily_bars,hold_days=5)
    except Exception:
        raise HTTPException(502,'성적표 계산 실패 · 완료 일봉 및 데이터 공급원을 확인하세요.') from None
    horizons={}
    for horizon in (1,3,5,10):
        completed=[r['horizons'][str(horizon)] for r in assessed['results']
                   if r.get('horizons',{}).get(str(horizon),{}).get('status')=='complete']
        hits=sum(bool(r.get('direction_hit')) for r in completed)
        buy_net=[r['buy_net_pct'] for r in completed if isinstance(r.get('buy_net_pct'),(int,float))]
        horizons[str(horizon)]={'complete':len(completed),'pending_or_unavailable':len(rows)-len(completed),
             'direction_hit_pct':round(hits/len(completed)*100,1) if completed else None,
             'mean_buy_net_pct':round(sum(buy_net)/len(buy_net),3) if buy_net else None,
             'buy_sample':len(buy_net)}
    return JSONResponse({'count':len(rows),'requested':len(all_rows),'total':total,
      'excluded_by_four_source_limit':sum(r.get('source')=='kis' for r in all_rows)-len(rows),
      'legacy_yahoo_observations_excluded':sum(r.get('source')!='kis' for r in all_rows),
      'status':'partial' if total>len(rows) else 'observed',
      'horizons':horizons,'paper':paper,
      'note':'KIS 관찰 표본만 계산합니다. 이전 Yahoo 기록은 보존하며 재평가하지 않습니다. 사용자가 조회한 종목에 편중될 수 있으며, 과거 전체 종목의 최종 추천 성능이 아닙니다. 자동 주문·24시간 자동 수집 없음.'},
      headers={'Cache-Control':'no-store'})


# v3.6: bounded foreground watchlist scan. Does not register cron jobs or trade orders.
# A scan generates CURRENT rule-based signals; its output is NOT a backtested probability.
def _watch_item(symbol: str, mode: str, source: str, cost_bps: int) -> dict:
    key = ('v381-watch-item', source, symbol, mode, cost_bps)

    def calculate() -> dict:
        if source != 'kis':
            raise ValueError('KIS_ONLY')
        else:
            app_key, secret = os.environ.get('KIS_APP_KEY', ''), os.environ.get('KIS_APP_SECRET', '')
            if not (app_key and secret):
                raise ValueError('KIS 연동 키가 설정되지 않았습니다.')
            market = kis_data(symbol, mode, app_key, secret)
        features = feature_frame(market.bars, mode)
        cfg=Config.for_mode(mode, cost_bps)
        decision = decide(features, cfg)
        q=quality_gate(features,cfg,raw_signal=decision.signal)
        age = _last_age_minutes(features.index[-1], market.symbol, mode)
        stale_limit = 8 if mode == '1분' else 25 if mode == '5분' else 3 * 24 * 60
        is_stale = age is None or age > stale_limit or bool((getattr(market,'daily_integrity',None) or {}).get('signal_hold'))
        if mode=='스윙' and not market.synthetic:
            _, session_check=completed_swing_bars(market.bars)
            is_stale=is_stale or bool(session_check.get('stale'))
        result = {
            'symbol': market.symbol, 'mode': mode, 'source': source,
            'status': 'ok', 'technical': decision.signal,
            'combined': q['strict_signal'], 'financial_gate': 'NOT_APPLIED',
            'quality_gate': q,
            'finance_note': '단타에는 현재 재무 위험 필터를 적용하지 않습니다.',
            'reference': _safe_float(decision.reference), 'currency': market.currency,
            'stop': _safe_float(decision.stop), 'target': _safe_float(decision.target),
            'risk_reward': _safe_float(decision.risk_reward),
            'bar_time': decision.time, 'provider': market.provider,
            'fetched_at': market.retrieved_at,
            'price_age_minutes': age, 'is_stale': is_stale or bool(market.synthetic), 'is_demo': bool(market.synthetic),
            'freshness': market.freshness, 'provider_note': market.note,
            'risk_veto': decision.risk_veto,
            'note': '완료봉 기반 규칙형 신호. 실시간 시세 보장 및 주문 실행 없음.'
        }
        financial_gate, finance_note, financial_year = _get_financial_state(symbol, mode)
        policy = resolve_final_signal(q['strict_signal'], mode=mode, symbol=symbol,
                                      financial_gate=financial_gate,
                                      finance_reason=finance_note, stale=is_stale,
                                      is_demo=bool(market.synthetic))
        result['combined'] = policy['final_signal']
        result['historical_signal'] = policy['historical_signal']
        result['final_actionable_signal'] = policy['final_actionable_signal']
        result['signal_is_current'] = policy['signal_is_current']
        result['final_reason'] = policy['final_reason']
        result['financial_gate'] = financial_gate
        result['finance_note'] = finance_note
        result['financial_asof_year'] = financial_year
        return result

    return _cached(key, calculate, ttl=WATCH_CACHE_TTL)


@app.post('/api/watch/scan')
def watch_scan(request: Request, body: dict):
    """Read-only bounded batch, no disk persistence, no orders, no background task."""
    _authorize(request)
    _limit(request)
    symbols = body.get('symbols')
    modes = body.get('modes', ['5분', '스윙'])
    source = body.get('source', 'kis')
    cost_bps = body.get('cost_bps', 15)
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 5 or not all(isinstance(x, str) for x in symbols):
        raise HTTPException(422, '한 번에 1~5개 종목만 감시할 수 있습니다.')
    if not isinstance(modes, list) or not 1 <= len(modes) <= 2 or not all(isinstance(x,str) for x in modes) or len(set(modes)) != len(modes) or any(x not in MODES for x in modes):
        raise HTTPException(422, '분석 주기는 1분·5분·스윙 중 중복 없이 최대 2개를 선택하세요.')
    if source not in ('auto','kis'):
        raise HTTPException(422, '관심종목 감시는 KIS만 지원합니다.')
    source='kis'
    if type(cost_bps) is not int or not 0 <= cost_bps <= 100:
        raise HTTPException(422, '거래비용은 편도 0~100bp로 지정하세요.')
    try:
        normalized = [ticker_format(x) for x in symbols]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if len(set(normalized)) != len(normalized):
        raise HTTPException(422, '중복 종목을 제거해 주세요.')
    if any(not s.endswith(('.KS', '.KQ')) for s in normalized):
        raise HTTPException(422, '관심종목 감시는 코스피·코스닥만 지원합니다.')
    tasks = [(symbol, mode) for symbol in normalized for mode in modes]
    rows = {}
    # Max 3 concurrent remote data calls for the small free Render instance.
    with ThreadPoolExecutor(max_workers=min(3, len(tasks))) as pool:
        futures = {pool.submit(_watch_item, s, m, source, cost_bps): (s, m) for s, m in tasks}
        for future in as_completed(futures):
            s, m = futures[future]
            try:
                rows[(s, m)] = future.result()
            except Exception as exc:
                # No provider URL or API credentials are included in client-facing errors.
                rows[(s, m)] = {'symbol': s, 'mode': m, 'source': source, 'status': 'error',
                                'error': f'시세 또는 재무자료 조회 실패 ({type(exc).__name__}).',
                                'is_stale': True, 'combined': None}
    results = [rows[key] for key in tasks]
    return JSONResponse({'status': 'ok', 'version': '3.8.1',
                         'scanned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                         'requested': len(tasks), 'successful': sum(x['status'] == 'ok' for x in results),
                         'results': results, 'polling': 'visible-tab-only', 'real_orders': False},
                        headers={'Cache-Control': 'no-store'})


# NOVA v5.1 read-only research. Never uses the live signal or evidence write path.
def _v5_compute(symbol: str, source: str, cost_bps: int) -> dict:
    if source not in ('auto','kis'):
        raise ValueError('KIS_ONLY')
    key,secret=_kis_creds()
    data=kis_data(symbol,'스윙',key,secret)
    actual_source='kis'
    fallback=None
    completed, session=completed_swing_bars(data.bars)
    if len(completed)<165:
        return {'status':'INSUFFICIENT','version':'5.4.3',
                'reason':f'완료봉 {len(completed)}개 · 최소 165개 필요',
                'research_only':True,'modifies_live_signal':False,
                'source':actual_source,'symbol':symbol,'completed_session':session}
    features=feature_frame(completed,'스윙')
    benchmark=None
    try:
        idx='^KS11' if symbol.endswith('.KS') else '^KQ11'
        benchmark=_cached(('v53_benchmark_2y',idx),lambda:{'frame':fetch_kis_reference(idx,period='2y')},ttl=900)['frame']
    except Exception:
        benchmark=None
    result=v5_research_lab(features,Config.for_mode('스윙',cost_bps),
                           benchmark=benchmark,source=actual_source)
    current_core={'status':'UNAVAILABLE','reason':'최신 정규장 완료봉 미검증'}
    integrity=getattr(data,'daily_integrity',None) or {}
    if not session.get('stale') and integrity.get('verified') and integrity.get('regular_volume_verified') and not integrity.get('signal_hold') and integrity.get('verification_basis')=='KIS_REGULAR_MINUTE' and integrity.get('latest_date')==str(completed.index[-1].date()):
        try:
            from nova_v541_core import latest as latest_core541
            current_core=latest_core541(features,benchmark)
        except Exception:current_core={'status':'UNAVAILABLE','reason':'최신 연구 계산 실패'}
    result['core541_current']=current_core
    return {**result,'version':'5.4.3','symbol':symbol,'source':actual_source,
            'stock_price_basis':'KIS_NATIVE_ADJUSTED_HISTORY_LATEST_REGULAR_SESSION','historical_session_verified':False,
            'cross_provider_price_equivalence':'NOT_VERIFIED',
            'cross_provider_research_adoption_blocked':True,
            'source_fallback_reason':fallback,'completed_session':session,
            'daily_integrity':getattr(data,'daily_integrity',None),
            'asof_last_bar':str(completed.index[-1]),
            'benchmark_status':'READY' if benchmark is not None else 'UNAVAILABLE',
            'powered_by':'NOVA v5.4.3 read-only strategy evidence audit'}


def _v5_one(symbol: str, source: str, cost_bps: int) -> dict:
    return _cached(('v5_lab',symbol,source,cost_bps),
                   lambda:_v5_compute(symbol,source,cost_bps),ttl=600)


@app.get('/api/research/v5')
def v5_research(request: Request, symbol: str = '005930.KS', source: str = 'auto', cost_bps: int = 15):
    _authorize(request)
    _limit(request)
    if source not in ('auto','kis') or not 0 <= cost_bps <= 100:
        raise HTTPException(422,'잘못된 연구 조건입니다.')
    try:
        symbol=ticker_format(symbol)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):
        raise HTTPException(422,'국내 6자리 KOSPI/KOSDAQ 주식만 연구할 수 있습니다.')
    try:
        result=_v5_one(symbol,source,cost_bps)
    except Exception:
        raise HTTPException(502,'연구 시세 조회 실패 · KIS 공급원 또는 네트워크를 확인하세요.') from None
    return JSONResponse(_public_research(result),headers={'Cache-Control':'no-store'})


def _public_research(result):
    accuracy=result.get('v54_accuracy')
    if not isinstance(accuracy,dict):return result
    focus=accuracy.get('core_focus')
    if not isinstance(focus,dict):return result
    return {**result,'v54_accuracy':{**accuracy,'core_focus':{k:v for k,v in focus.items() if k!='_replay'}}}


# v5.3.1 - separately requested, read-only KIS/Yahoo price-basis diagnostics.
# No writes to Neon, and no substitution into existing buy/sell/AI pipelines.
def _source_audit_compute(symbol: str) -> dict:
    from data_sources import KIS, KST, discard_unfinished
    from datetime import datetime as dt
    kis_frame=None
    yahoo_reference=None
    yahoo_adjusted=None
    failures={}
    now=dt.now(KST)
    key=os.environ.get('KIS_APP_KEY','').strip()
    secret=os.environ.get('KIS_APP_SECRET','').strip()
    if not key or not secret:
        failures['kis']='KIS_NOT_CONFIGURED'
    else:
        try:
            bars=KIS(key,secret).daily(symbol[:6],now)
            kis_frame=discard_unfinished(bars,'스윙',symbol,now)
            if kis_frame.empty:failures['kis']='KIS_NO_COMPLETED_BARS'
        except Exception:
            failures['kis']='KIS_HISTORY_UNAVAILABLE'
    try:
        import yfinance as yf
        history=yf.Ticker(symbol).history(period='2y',interval='1d',
            auto_adjust=False,prepost=False,raise_errors=True,timeout=12)
        reference,adjusted=yahoo_price_bases(history)
        yahoo_reference=discard_unfinished(reference,'스윙',symbol,now)
        yahoo_adjusted=discard_unfinished(adjusted,'스윙',symbol,now)
        if yahoo_reference.empty:failures['yahoo']='YAHOO_NO_COMPLETED_BARS'
    except Exception:
        failures['yahoo']='YAHOO_HISTORY_UNAVAILABLE'
    return audit_price_sources(kis_frame,yahoo_reference,yahoo_adjusted,
                               symbol=symbol,failures=failures)


@app.get('/api/research/v5/source-audit')
def v531_source_audit(request: Request, symbol: str='005930.KS'):
    _authorize(request)
    _limit(request)
    try: symbol=ticker_format(symbol)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):
        raise HTTPException(422,'국내 6자리 KOSPI/KOSDAQ 종목만 비교할 수 있습니다.')
    # One manual action per symbol / 15 minutes. KIS can issue up to 8 read-only
    # daily requests; avoid unnecessary token reissues and provider throttling.
    result=_cached(('v531_source_audit',symbol),lambda:_source_audit_compute(symbol),ttl=900)
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


# v5.3.2: manual read-only session-settlement verification; distinct from the
# long-history research. The identical guard is applied to live KIS swing bars
# via data_sources.kis_data, and an unverified most-recent bar is quarantined.
@app.get('/api/research/v5/daily-integrity')
def v532_daily_integrity(request: Request, symbol: str='005930.KS'):
    _authorize(request)
    _limit(request)
    try: symbol=ticker_format(symbol)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):
        raise HTTPException(422,'국내 코스피·코스닥 종목만 검증할 수 있습니다.')
    key,secret=_kis_creds()
    def _inspect():
        from data_sources import KIS
        from daily_settlement_guard import verify_latest_kis_daily
        now=datetime.now(KST)
        api=KIS(key,secret)
        data=api.daily(symbol[:6],now)
        if data.empty:raise RuntimeError('KIS_NO_DAILY')
        verified,report=verify_latest_kis_daily(data,api=api,code=symbol[:6],symbol=symbol,now=now,force=True)
        archive_state='DISABLED'
        try:
            from kis_daily_archive import save_regular
            archive_state=save_regular(symbol,verified,report)
        except Exception:archive_state='WRITE_FAILED'
        report={**report,'symbol':symbol,'research_only':True,
                'places_orders':False,'writes_neon':archive_state=='SAVED','archive_state':archive_state,
                'safe_last_close':float(verified['Close'].iloc[-1]) if not verified.empty else None,
                'safe_last_date':str(verified.index[-1].date()) if not verified.empty else None}
        return report
    try:
        result=_cached(('v534_daily_integrity',symbol),_inspect,ttl=10)
        # Drop stale chart and analysis payloads after a manual verification.
        with CACHE_LOCK:
            for k in list(CACHE):
                if isinstance(k,tuple) and symbol in k and k[0]!='v534_daily_integrity':
                    CACHE.pop(k,None)
    except Exception:
        raise HTTPException(502,'KIS 완료봉 재검증 실패. KIS 상태·호출 제한을 확인하세요.') from None
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


# Shared account-level on-demand batch. One short-lived job at a time to avoid
# exhausting the Render free instance. Only explicit manual jobs may save historical checkpoints.
BATCH_RESEARCH_LOCK=threading.RLock()
BATCH_RESEARCH_STATE={
    'status':'idle','job_id':None,'symbols':[], 'rows':[],
    'finished':0,'started_at':None,'ended_at':None,
    'research_only':True,'places_orders':False,'modifies_live_signal':False
}


def _batch_retry_wait():
    time.sleep(2)


def _v5_batch_fetch(symbol: str, source: str, cost_bps: int) -> dict:
    """One bounded retry for transient unavailability; never change provider or cost."""
    from ops_diagnostics import emit
    for attempt in range(2):
        try:
            data=_v5_one(symbol,source,cost_bps)
        except Exception:
            if attempt:raise
            reason='EXCEPTION'
        else:
            if data.get('status')!='UNAVAILABLE' or attempt:return data
            reason='UNAVAILABLE'
        # A cached failure must not defeat the retry. Preserve a concurrent success.
        key=('v5_lab',symbol,source,cost_bps)
        with CACHE_LOCK:
            entry=CACHE.get(key)
            if entry and entry[1].get('status')=='UNAVAILABLE':CACHE.pop(key,None)
        try:emit('research_retry',{'symbol':symbol,'attempt':2,'reason':reason})
        except Exception:pass
        _batch_retry_wait()


def _v5_batch_worker(job_id: str, symbols: list[str], source: str, cost_bps: int):
    from ops_diagnostics import progress_summary, emit
    batch_start=time.monotonic()
    from research_checkpoint import scope_for, eligible_data, load as load_checkpoint, save as save_checkpoint
    with BATCH_RESEARCH_LOCK:
        enabled=BATCH_RESEARCH_STATE.get('checkpoint_enabled') is True
        resume=BATCH_RESEARCH_STATE.get('resume_checkpoint') is True
    scope=None; restored={}; saved=0; errors=0; skipped=0; restored_count=0
    if enabled and evidence_backend()!='disabled':
        try:
            scope=scope_for(symbols,source,cost_bps)
            if scope and resume:restored=load_checkpoint(scope)
        except Exception:
            errors+=1
    def checkpoint_status():
        return {'enabled':enabled,'eligible':scope is not None,'restored':restored_count,
                'saved':saved,'skipped':skipped,'errors':errors,'ttl_hours':2,'manual_resume':True}
    def progress(phase, symbol=None, symbol_status=None, symbol_elapsed=None):
        with BATCH_RESEARCH_LOCK:
            if BATCH_RESEARCH_STATE.get('job_id')!=job_id:return False
            rows=BATCH_RESEARCH_STATE['rows']
            payload=progress_summary(phase,len(symbols),len(rows),sum(r.get('status')=='READY' for r in rows),time.monotonic()-batch_start,symbol,symbol_status,symbol_elapsed)
            BATCH_RESEARCH_STATE['progress']=payload
            if enabled:BATCH_RESEARCH_STATE['checkpoint']=checkpoint_status()
        try:emit('research_progress',payload)
        except Exception:pass
        return True
    if not progress('batch_start'):return
    for symbol in symbols:
        if not progress('symbol_start',symbol):return
        symbol_start=time.monotonic()
        if symbol in restored:
            with BATCH_RESEARCH_LOCK:
                if BATCH_RESEARCH_STATE.get('job_id')!=job_id:return
                BATCH_RESEARCH_STATE['rows'].append(restored[symbol])
                restored_count+=1
                BATCH_RESEARCH_STATE['finished']=len(BATCH_RESEARCH_STATE['rows'])
            try:emit('research_checkpoint',{'symbol':symbol,'status':'RESTORED'})
            except Exception:pass
            if not progress('symbol_done',symbol,'READY',time.monotonic()-symbol_start):return
            continue
        try:
            data=_v5_batch_fetch(symbol,source,cost_bps)
            if data.get('status')=='READY':
                check=data.get('validation',{})
                baseline=check.get('baseline',{})
                five=check.get('five_core',{})
                ai=check.get('ai_model',{})
                row={'symbol':symbol,'status':'READY','source':data.get('source'),
                     'asof_last_bar':data.get('asof_last_bar'),
                     'validation_start':check.get('start'), 'validation_end':check.get('end'),
                     'source_fallback_reason':data.get('source_fallback_reason'),
                     'stock_price_basis':data.get('stock_price_basis'),
                     'cross_provider_price_equivalence':data.get('cross_provider_price_equivalence'),
                     'baseline':baseline,'five_core':five,
                     'ai_model':{'status':ai.get('status'),
                                 'brier_score':ai.get('brier_score'),
                                 'naive_train_rate_brier':ai.get('naive_train_rate_brier'),
                                 'research_strategy':ai.get('research_strategy')},
                     'role_ablations':data.get('role_ablations',[]),
                     'risk_audit':data.get('risk_audit',{}),
                     'v53':data.get('v53',{}),'v54_accuracy':data.get('v54_accuracy',{}),'core541_current':data.get('core541_current',{}),
                     'aligned_backtest_comparison':data.get('aligned_backtest_comparison',{}),
                     'trade_diagnostics':data.get('trade_diagnostics',{})}
                if scope and eligible_data(data,scope):
                    row['checkpoint_day']=scope['day']
                    try:
                        if save_checkpoint(scope,row):
                            saved+=1
                            emit('research_checkpoint',{'symbol':symbol,'status':'SAVED'})
                        else:skipped+=1
                    except Exception:
                        errors+=1
                        try:emit('research_checkpoint',{'symbol':symbol,'status':'WRITE_FAILED'})
                        except Exception:pass
                elif scope:skipped+=1
            else:
                row={'symbol':symbol,'status':data.get('status','UNAVAILABLE'),
                     'source':data.get('source'), 'reason':data.get('reason','자료 불충분')}
        except Exception:
            row={'symbol':symbol,'status':'UNAVAILABLE',
                 'reason':'해당 종목 시세 조회 또는 백테스트 계산 실패 · 공급원/거래일 확인'}
        with BATCH_RESEARCH_LOCK:
            if BATCH_RESEARCH_STATE.get('job_id')!=job_id:
                return
            BATCH_RESEARCH_STATE['rows'].append(row)
            BATCH_RESEARCH_STATE['finished']=len(BATCH_RESEARCH_STATE['rows'])
        if not progress('symbol_done',symbol,row.get('status'),time.monotonic()-symbol_start):return
    if not progress('aggregate_start'):return
    with BATCH_RESEARCH_LOCK:
        if BATCH_RESEARCH_STATE.get('job_id')!=job_id:return
        complete_rows=list(BATCH_RESEARCH_STATE['rows'])
    try:
        from nova_v542_aligned import aligned_summary
        aligned=aligned_summary(complete_rows)
    except Exception:
        aligned={'status':'UNAVAILABLE','reason':'공통구간 재계산 실패','strategy_adoption':'HOLD'}
    if not progress('aggregate_done'):return
    with BATCH_RESEARCH_LOCK:
        if BATCH_RESEARCH_STATE.get('job_id')==job_id:
            BATCH_RESEARCH_STATE['core542_aligned']=aligned
            BATCH_RESEARCH_STATE['status']='complete'
            BATCH_RESEARCH_STATE['ended_at']=datetime.now(timezone.utc).isoformat(timespec='seconds')
            try:
                from ops_diagnostics import emit, batch_summary
                emit('research_batch',batch_summary(BATCH_RESEARCH_STATE))
                if enabled:emit('research_checkpoint_summary',checkpoint_status())
            except Exception:pass # diagnostics must not affect research


@app.post('/api/research/v5/batch/run')
def v5_batch_start(request: Request, body: dict):
    _authorize(request)
    _limit(request)
    source=body.get('source','auto')
    cost=body.get('cost_bps',15)
    raw=body.get('symbols',[])
    if source not in ('auto','kis') or type(cost) is not int or not 0<=cost<=100:
        raise HTTPException(422,'시세 공급원 또는 거래비용 설정 오류')
    max_stocks=25 if body.get('extended_core_study') is True else 5
    if not isinstance(raw,list) or not 2<=len(raw)<=max_stocks:
        raise HTTPException(422,f'동시에 2~{max_stocks}개 종목만 검증할 수 있습니다.')
    symbols=[]
    for value in raw:
        if not isinstance(value,str) or len(value)>24:
            raise HTTPException(422,'종목 형식 오류')
        try:
            sym=ticker_format(value)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        if not re.fullmatch(r'\d{6}\.(KS|KQ)',sym):
            raise HTTPException(422,'국내 6자리 상장 종목만 지원합니다.')
        if sym in symbols:
            raise HTTPException(422,'중복 종목을 제거하세요.')
        symbols.append(sym)
    with BATCH_RESEARCH_LOCK:
        if BATCH_RESEARCH_STATE['status']=='running':
            raise HTTPException(409,'이미 진행 중인 일괄 연구가 있습니다. 진행 상태를 확인하세요.')
        job_id=secrets.token_hex(10)
        BATCH_RESEARCH_STATE.update(status='running',job_id=job_id,symbols=symbols,
            rows=[],core542_aligned=None,progress=None,finished=0,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            ended_at=None,source_requested=source,cost_bps_per_side=cost,
            checkpoint_enabled=bool(os.getenv('NOVA_ACCESS_TOKEN','').strip()),
            resume_checkpoint=body.get('resume_checkpoint') is True,checkpoint=None)
        threading.Thread(target=_v5_batch_worker,args=(job_id,symbols,source,cost),daemon=True).start()
    return JSONResponse({'status':'running','job_id':job_id,'requested':len(symbols),
             'research_only':True},headers={'Cache-Control':'no-store'})


@app.get('/api/research/v5/batch/status')
def v5_batch_status(request: Request):
    _authorize(request)
    with BATCH_RESEARCH_LOCK:
        snap={**BATCH_RESEARCH_STATE,'rows':list(BATCH_RESEARCH_STATE['rows']),
              'symbols':list(BATCH_RESEARCH_STATE['symbols'])}
    if snap.get('status') == 'complete' and snap.get('rows'):
        snap['v53_batch_summary'] = summarize_batch(snap['rows'])
        from nova_v541_core import batch_summary as core_batch_summary
        snap['core541_batch_summary']=core_batch_summary(snap['rows'])
    snap['rows']=[_public_research(r) for r in snap['rows']]
    snap['notes']=['동일 편도 비용 및 5거래일 가정이지만 종목별 검증 날짜·공급원이 다를 수 있습니다.',
                   '종목별 총자산 가상 수익률은 단일 통합 포트폴리오 수익률이 아닙니다.',
                   '생존 종목 편향·수정주가·체결 가능성·데이터 탐색 편향 미검증.',
                   '서버 재시작 후 같은 거래일·종목 순서·비용으로 다시 실행하면 검증된 2시간 이내 체크포인트만 복구합니다. 미저장 자료는 다시 계산합니다.',
                   '과거 신호 연구일 뿐 실제 추천 성적이나 주문이 아닙니다.']
    return JSONResponse(snap,headers={'Cache-Control':'no-store'})


@app.get('/api/realtime/kis/status')
def kis_ws_capabilities(request: Request):
    _authorize(request)
    return JSONResponse(kis_ws_status(),headers={'Cache-Control':'no-store'})


@app.websocket('/api/realtime/kis/stream')
async def kis_ws_readonly_stream(ws: WebSocket):
    # Browser WebSockets send HttpOnly same-site cookies automatically. No
    # credential query strings. Reject cross-origin socket hijacking.
    from urllib.parse import urlsplit
    origin=ws.headers.get('origin','')
    host=ws.headers.get('host','').split(':')[0].lower()
    o=urlsplit(origin)
    if not _is_authenticated(ws) or o.scheme not in ('https','http') or o.hostname != host:
        await ws.close(code=1008)
        return
    symbol=ws.query_params.get('symbol','')
    try:code=stock_code(symbol) if isinstance(symbol,str) else ''
    except ValueError:code=''
    if not re.fullmatch(r'\d{6}',code):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        await kis_ws_relay(ws,code)
    except Exception:
        # Broker/runtime details never reach the browser.
        try:await ws.send_json({'type':'error','message':'실시간 데이터 연결이 중단됐습니다. REST 시세는 계속 사용할 수 있습니다.'})
        except Exception:pass
    finally:
        try:await ws.close()
        except Exception:pass


@app.get('/api/analysis')
def analysis(request:Request,symbol:str='005930.KS',mode:str='스윙',source:str='kis',cost_bps:int=15,entry_price:float|None=None):
    _authorize(request)
    _limit(request)
    if mode not in MODES:raise HTTPException(422,'분석 주기는 1분, 5분, 스윙 중에서 선택하세요.')
    if source not in SOURCES:raise HTTPException(422,'KIS 전용 분석입니다.')
    if source=='auto':source='kis'
    if not 0<=cost_bps<=100:raise HTTPException(422,'거래비용은 편도 0~100bp로 입력하세요.')
    if source=='demo':symbol='DEMO'
    else:
        try: symbol=ticker_format(symbol)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    if source!='demo' and not symbol.endswith(('.KS','.KQ')):
        raise HTTPException(422,'국내 코스피·코스닥 종목만 분석할 수 있습니다.')
    # A cached pre-close Friday bar must never remain classified as current
    # after Monday's close. Change the swing cache key at the official KRX
    # close boundary (including the known 16:30 exam-day exception).
    close_boundary=None
    if mode=='스윙' and source!='demo':
        now_kst=datetime.now(KST)
        session=krx_session(now_kst)
        close_text=session.get('close_kst') or '15:30'
        try:
            close_min=int(close_text[:2])*60+int(close_text[3:])
        except (ValueError,TypeError):
            close_min=15*60+30
        close_boundary=(now_kst.date().isoformat(), now_kst.hour*60+now_kst.minute>=close_min,now_kst.hour>=18)
    try:
        value=_cached((source,symbol,mode,cost_bps,close_boundary),lambda:_payload(symbol,mode,source,cost_bps))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f'시세 조회/분석 실패 ({type(exc).__name__}). 종목·데이터 기간·공급원/네트워크를 확인하세요.') from exc
    if entry_price is not None:
        # Optional user fill/what-if only recomputes the READ-ONLY risk overlay.
        # It never changes legacy signal, backtest, chart or ATR reference lines.
        evidence=value.get('risk_structure',{}).get('evidence',{})
        custom=reprice_from_evidence(evidence, entry_price=entry_price, cost_bps=cost_bps,
                                     demo=value.get('is_demo',False), stale=value.get('is_stale',False))
        if custom['state']=='INVALID_ENTRY':
            raise HTTPException(422,custom['reason'])
        value={**value,'risk_structure':custom}
    # Persist only a genuine current analysis observation if durable storage
    # is explicitly configured. Failure must never alter trading decisions.
    if evidence_backend() != 'disabled':
        try:
            append_analysis(value)
        except Exception:
            logging.getLogger('nova.evidence').warning('Evidence database write failed: check DATABASE_URL or persistent disk')
    if entry_price is None:
        try:
            from core541_evidence import record as record_core541, enabled as core541_enabled
            if core541_enabled():
                record_core541(value)
                record_core541(value,hybrid=True)
        except Exception:
            logging.getLogger('nova.core541').warning('Core observation write unavailable')
        try:
            from core550_evidence import record as record_core550
            record_core550(value)
        except Exception:
            logging.getLogger('nova.core550').warning('v550 observation write unavailable')
        try:
            from core552_evidence import record as record_core552
            record_core552(value)
        except Exception:
            logging.getLogger('nova.core552').warning('v552 observation write unavailable')
        try:
            from core560_evidence import record as record_core560
            record_core560(value)
        except Exception:
            logging.getLogger('nova.core560').warning('v560 observation write unavailable')
        try:
            from core561_evidence import record as record_core561
            record_core561(value)
        except Exception:
            logging.getLogger('nova.core561').warning('v561 observation write unavailable')
    return JSONResponse(value,headers={'Cache-Control':'no-store'})

@app.get('/api/research/core541/evidence')
def core541_evidence_report(request: Request, symbol: str='005930.KS', hybrid: bool=False):
    _authorize(request);_limit(request)
    try:symbol=ticker_format(symbol)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise HTTPException(422,'국내 종목만 지원')
    from core541_evidence import report
    try:return JSONResponse(report(symbol,hybrid=hybrid),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'5-Core 관찰 DB 조회 실패') from None

@app.get('/api/research/core550/evidence')
def core550_evidence_report(request: Request, symbol: str='005930.KS'):
    _authorize(request);_limit(request)
    try:symbol=ticker_format(symbol)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise HTTPException(422,'국내 종목만 지원')
    from core550_evidence import report
    try:return JSONResponse(report(symbol),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'v5.5 관찰 DB 조회 실패') from None

@app.get('/api/research/core552/evidence')
def core552_evidence_report(request: Request, symbol: str='005930.KS'):
    _authorize(request);_limit(request)
    try:symbol=ticker_format(symbol)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise HTTPException(422,'국내 종목만 지원')
    from core552_evidence import report
    try:return JSONResponse(report(symbol),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'v5.5.2 관찰 DB 조회 실패') from None

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host=os.environ.get('NOVA_BIND_HOST','127.0.0.1'),port=int(os.environ.get('PORT','8000')))


# v4.2 — Market-wide EOD recommendation screener. No automated orders.
def _scan_snapshot(profile='full'):
    with SCAN_LOCK:
        state=dict(SCAN_PROFILES[profile])
        state['active_profile']=next((p for p,v in SCAN_PROFILES.items() if v.get('status') in SCAN_EXECUTING),None)
    state.pop('started_monotonic', None)
    state['krx_configured']=bool(os.environ.get('KRX_API_KEY','').strip())
    state['fsc_configured']=bool(os.environ.get('FSC_STOCK_API_KEY','').strip())
    state['available_sources']=['fsc','krx']
    state['storage']='server_memory_plus_ranked_evidence' if evidence_backend()!='disabled' else 'server_memory_only'
    return state


def _cached_research_flow(symbol: str) -> dict | None:
    # No new scraping/requests during broad market scan. Reuse ONLY fresh
    # existing user-observed flow data; never backdate the observation.
    code=symbol[:6]
    with CACHE_LOCK:
        item=CACHE.get(('v46_flow_per_investor_20261112',code))
    if not item or time.monotonic()-item[0]>300:
        return None
    result=item[1]
    return result if isinstance(result,dict) and result.get('symbol')==code else None


def _start_queued_scan_locked():
    """At most one physical scanner. A separate pending job per profile; no secrets in snapshots."""
    global SCAN_JOB
    if any(v.get('status') in SCAN_EXECUTING for v in SCAN_PROFILES.values()):
        return
    while SCAN_PENDING:
        profile=next(iter(SCAN_PENDING))
        args=SCAN_PENDING.pop(profile)
        state=SCAN_PROFILES[profile]
        if state.get('status')!='queued' or state.get('job_id')!=args[1]:
            continue
        SCAN_CANCEL.clear()
        state.update(status='running',started_monotonic=time.monotonic(),
                     started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     progress={'phase':'provider','done':0,'total':None,'message':'일별 시장자료 확인 중'})
        try:
            SCAN_JOB=threading.Thread(target=_scan_worker,args=args,daemon=True,name='nova-market-eod-scan')
            SCAN_JOB.start()
        except Exception:
            state.update(status='failed',result=None,error={'code':'worker_start','message':'검색 작업 시작 실패'})
            continue
        return


def _scan_worker(market: str, job_id: str, provider: str, api_key: str, depth: int=48, profile: str='full'):
    state=SCAN_PROFILES[profile]
    started=time.monotonic()
    def checkpoint():
        if SCAN_CANCEL.is_set():
            raise MarketScanError('cancelled','사용자가 검색을 취소했습니다.')
    def progress(p):
        with SCAN_LOCK:
            if state.get('job_id')==job_id:
                state['progress']=p
    try:
        client = FSCStockClient(api_key) if provider == 'fsc' else None
        result=run_market_scan(market=market,api_key=api_key,client=client,
                     analyze=lambda sym:_payload(sym,'스윙','kis',15),
                     analyze_asof=lambda sym,date:_payload(sym,'스윙','kis',15,asof_date=date,scan_profile=profile),
                     progress=progress,depth=depth,auxiliary_flow=_cached_research_flow if profile=='full' else None,
                     pattern_only=profile=='pattern',check_cancel=checkpoint)
        checkpoint()
        result['scan_profile']=profile
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        # Store the first actually observed ranked market candidates, without
        # changing existing recommendation decisions or screen response timing.
        with SCAN_LOCK:
            checkpoint()
            state['status']='finalizing'
        if profile=='full' and evidence_backend() != 'disabled':
            try:
                contexts=capture_market_context()
                store_auto_scan(result,market_context=contexts)
            except Exception:
                logging.getLogger('nova.autotrack').warning('Market scan evidence persistence failed')
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        logging.getLogger('uvicorn.error').info('NOVA_SCAN profile=%s depth=%s asof=%s elapsed_seconds=%.2f deep_target=%s deep_succeeded=%s deep_failed=%s strict=%s interest=%s assessment=%s exclusion_codes=%s cache=%s',
            profile,depth,result.get('data_date'),result['elapsed_seconds'],result.get('deep_target'),result.get('deep_succeeded'),
            result.get('deep_failed'),result.get('recommended_count'),result.get('interest_count'),
            result.get('scan_assessment'),result.get('rejection_codes'),result.get('scan_cache_counts'))
        with SCAN_LOCK:
            if state.get('job_id')==job_id:
                state.update(status='complete',result=result,error=None,
                   completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   completed_monotonic=time.monotonic())
    except MarketScanError as err:
        with SCAN_LOCK:
            if state.get('job_id')==job_id:
                state.update(status='cancelled' if err.code=='cancelled' else 'failed',result=None,
                  error={'code':err.code,'message':str(err)},
                  completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    except Exception:
        with SCAN_LOCK:
            if state.get('job_id')==job_id:
                state.update(status='failed',result=None,
                  error={'code':'internal','message':'시장 검색에 실패했습니다. 서버 로그와 시세 공급원을 확인하세요.'},
                  completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    finally:
        with SCAN_LOCK:
            _start_queued_scan_locked()


def _auto_auth(request: Request):
    """Dedicated cron-only secret; never accept browser login as cron permission."""
    token=os.environ.get('NOVA_AUTOTRACK_TOKEN','')
    enabled=os.environ.get('NOVA_AUTOTRACK_ENABLED','')=='1'
    if not enabled or len(token)<24:
        raise HTTPException(503,'자동 추적 예약 실행이 설정되지 않았습니다.')
    provided=request.headers.get('X-Nova-Autotrack-Token','')
    if not hmac.compare_digest(provided,token):
        raise HTTPException(401,'자동 추적 예약 실행 인증 실패')
    if evidence_backend()=='disabled':
        raise HTTPException(503,'영구 DB가 필요합니다.')


def _auto_worker():
    started=datetime.now(timezone.utc)
    summary={'scan':None,'evaluation':None}
    errors=[]
    def set_phase(state):
        with AUTO_LOCK: AUTO_STATE['status']=state
    try:
        # Market screening may require tens of Yahoo/OpenDART calls. Run once
        # after market close; skip absent provider credentials instead of inventing recommendations.
        provider='fsc' if os.environ.get('FSC_STOCK_API_KEY','').strip() else 'krx'
        api_key=os.environ.get('FSC_STOCK_API_KEY' if provider=='fsc' else 'KRX_API_KEY','').strip()
        if api_key:
            try:
                set_phase('scanning')
                client=FSCStockClient(api_key) if provider=='fsc' else None
                result=run_market_scan(market='all',api_key=api_key,client=client,
                    analyze=lambda symbol:_payload(symbol,'스윙','kis',15),
                    analyze_asof=lambda symbol,date:_payload(symbol,'스윙','kis',15,asof_date=date),
                    depth=24,auxiliary_flow=None)
                contexts=capture_market_context()
                summary['scan']=store_auto_scan(result,market_context=contexts)
                summary['scan'].update(source_date=result.get('data_date'),
                      deep_target=result.get('deep_target'),deep_failed=result.get('deep_failed'),
                      status='partial' if result.get('incomplete') else 'complete')
            except MarketScanError as exc:
                summary['scan']={'status':'failed','code':exc.code}
                errors.append('scan_provider_or_snapshot')
            except Exception:
                summary['scan']={'status':'failed','code':'SCAN_FAILED'}
                errors.append('scan_unavailable')
        else:
            summary['scan']={'status':'skipped','code':'NO_FSC_OR_KRX_API_KEY'}
            errors.append('scan_provider_not_configured')
        # Post-trade observations are independent of the marketwide scan.
        # Retry failed lookups on the next scheduled day (and retain raw evidence).
        set_phase('evaluating')
        try:
            summary['evaluation']=evaluate_auto_pending(_journal_daily_bars,max_symbols=12)
        except Exception:
            summary['evaluation']={'status':'failed','code':'EVALUATION_FAILED'}
            errors.append('evaluation_unavailable')
        try:
            summary['observed_signals']=evaluate_auto_observed(_journal_daily_bars,max_symbols=12)
        except Exception:
            summary['observed_signals']={'status':'failed','code':'OBSERVATION_EVALUATION_FAILED'}
            errors.append('observed_evaluation_unavailable')
    except Exception:
        errors.append('internal_worker_failure')
    finally:
        finished=datetime.now(timezone.utc)
        state='partial' if errors else 'complete'
        try:
            record_auto_attempt(started_at=started,completed_at=finished,state=state,summary=summary)
        except Exception:
            logging.getLogger('nova.autotrack').warning('Cannot persist automation job audit record')
            errors.append('audit_store_failed')
        with AUTO_LOCK:
            AUTO_STATE.update(status='partial' if errors else 'complete',
                completed_at=finished.isoformat(timespec='seconds'),
                result=summary,error_code=','.join(errors) if errors else None)


@app.post('/api/autotrack/run')
def autotrack_run(request: Request):
    _auto_auth(request)
    global AUTO_JOB
    with AUTO_LOCK:
        if AUTO_STATE.get('status') in ('scanning','evaluating','queued'):
            return JSONResponse({'status':'already_running','started_at':AUTO_STATE['started_at']},
                                headers={'Cache-Control':'no-store'})
        AUTO_STATE.update(status='queued',started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                          completed_at=None,error_code=None,result=None)
        AUTO_JOB=threading.Thread(target=_auto_worker,daemon=True,name='nova-auto-evidence')
        AUTO_JOB.start()
        return JSONResponse({'status':'queued','started_at':AUTO_STATE['started_at']},
                            headers={'Cache-Control':'no-store'})


@app.get('/api/autotrack/job')
def autotrack_job(request: Request):
    _auto_auth(request)
    with AUTO_LOCK: state=dict(AUTO_STATE)
    return JSONResponse(state,headers={'Cache-Control':'no-store'})


# Dedicated research-only schedule; no market scan or order endpoints.
CORE560_LOCK=threading.Lock()
CORE560_JOB={'status':'idle','results':{},'day':None}

def _core560_worker():
    from nova_v560_unified import PRESET
    from core560_evidence import record, enabled
    results={}
    try:
        if not enabled():raise RuntimeError('storage_disabled')
        now=datetime.now(KST);session=krx_session(now);day=now.date().isoformat()
        if not session.get('calendar_verified'):
            with CORE560_LOCK:CORE560_JOB.update(status='failed',reason='거래일 달력 확인 실패')
            return
        if now.hour<18 or not session.get('close_kst') or session.get('is_open'):
            with CORE560_LOCK:CORE560_JOB.update(status='skipped',reason='당일 정규장 확정·18시 이후 조건 미충족')
            return
        for symbol in PRESET:
            try:results[symbol]=record(_payload(symbol,'스윙','kis',15))
            except Exception:results[symbol]='ERROR'
            with CORE560_LOCK:CORE560_JOB['results']=dict(results)
        with CORE560_LOCK:CORE560_JOB.update(status='complete' if all(v in ('SAVED','EXISTS') for v in results.values()) else 'partial')
    except Exception:
        with CORE560_LOCK:CORE560_JOB.update(status='failed',reason='거래일·저장 설정 또는 수집 실패')

@app.post('/api/research/core560/collect')
def core560_collect(request:Request):
    _auto_auth(request)
    if os.environ.get('NOVA_CORE560_AUTO_ENABLED')!='1':raise HTTPException(503,'NOVA_CORE560_AUTO_ENABLED=1 필요')
    with CORE560_LOCK:
        if CORE560_JOB['status']=='running':return {'status':'running'}
        CORE560_JOB.update(status='running',results={},day=datetime.now(KST).date().isoformat(),reason=None)
        threading.Thread(target=_core560_worker,daemon=True,name='nova-core560').start()
    return {'status':'running'}

@app.get('/api/research/core560/job')
def core560_job(request:Request):
    _auto_auth(request)
    with CORE560_LOCK:return dict(CORE560_JOB)

@app.get('/api/research/core560/evidence')
def core560_report(request:Request,symbol:str='005930.KS'):
    _authorize(request);_limit(request)
    try:symbol=ticker_format(symbol)
    except ValueError:raise HTTPException(422,'국내 종목 코드 필요') from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise HTTPException(422,'국내 종목만 지원')
    from core560_evidence import report
    try:return JSONResponse(report(symbol),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'v5.6.0 관찰 DB 조회 실패') from None

@app.get('/api/research/core560/status')
def core560_status(request:Request):
    _authorize(request);_limit(request)
    from core560_evidence import enabled
    with CORE560_LOCK:job=dict(CORE560_JOB)
    return {'enabled':os.environ.get('NOVA_CORE560_AUTO_ENABLED')=='1' and os.environ.get('NOVA_AUTOTRACK_ENABLED')=='1' and len(os.environ.get('NOVA_AUTOTRACK_TOKEN',''))>=24 and enabled(),'job':job,'note':'최근 실행 상태는 서버 재시작 시 초기화. 영구 기록은 종목별 관찰 조회.'}

CORE561_LOCK=threading.Lock()
CORE561_JOB={'status':'idle','results':{},'day':None}

def _core561_worker():
    from nova_v561_hierarchical import PRESET
    from core561_evidence import record, enabled
    results={}
    try:
        if not enabled():raise RuntimeError('storage_disabled')
        now=datetime.now(KST);session=krx_session(now);day=now.date().isoformat()
        if not session.get('calendar_verified'):
            with CORE561_LOCK:CORE561_JOB.update(status='failed',reason='거래일 달력 확인 실패')
            return
        if now.hour<18 or not session.get('close_kst') or session.get('is_open'):
            with CORE561_LOCK:CORE561_JOB.update(status='skipped',reason='당일 정규장 확정·18시 이후 조건 미충족')
            return
        for symbol in PRESET:
            try:results[symbol]=record(_payload(symbol,'스윙','kis',15))
            except Exception:results[symbol]='ERROR'
            with CORE561_LOCK:CORE561_JOB['results']=dict(results)
        with CORE561_LOCK:CORE561_JOB.update(status='complete' if all(v in ('SAVED','EXISTS') for v in results.values()) else 'partial')
    except Exception:
        with CORE561_LOCK:CORE561_JOB.update(status='failed',reason='거래일·저장 설정 또는 수집 실패')
    finally:
        try:
            from ops_diagnostics import emit, day
            with CORE561_LOCK:job=dict(CORE561_JOB)
            emit('collector_run',{'status':job.get('status'),'day':day(job.get('day')),'counts':{k:sum(v==k for v in results.values()) for k in ('SAVED','EXISTS','PRICE_ONLY','UNVERIFIED','DISABLED','ERROR')}})
        except Exception:pass

@app.post('/api/research/core561/collect')
def core561_collect(request:Request):
    _auto_auth(request)
    if os.environ.get('NOVA_CORE560_AUTO_ENABLED')!='1':raise HTTPException(503,'NOVA_CORE560_AUTO_ENABLED=1 필요')
    with CORE561_LOCK:
        if CORE561_JOB['status']=='running':return {'status':'running'}
        CORE561_JOB.update(status='running',results={},day=datetime.now(KST).date().isoformat(),reason=None)
        threading.Thread(target=_core561_worker,daemon=True,name='nova-core561').start()
    return {'status':'running'}

@app.get('/api/research/core561/job')
def core561_job(request:Request):
    _auto_auth(request)
    with CORE561_LOCK:return dict(CORE561_JOB)

# v5.6.6: independent scheduler opt-in; legacy auto endpoints remain unchanged.
def _core566_auth(request: Request):
    from core561_evidence import enabled
    token=os.environ.get('NOVA_AUTOTRACK_TOKEN','')
    if os.environ.get('NOVA_CORE566_AUTO_ENABLED')!='1' or len(token)<24 or not enabled():
        raise HTTPException(503,'전향 수집 예약 실행 설정 필요')
    if not hmac.compare_digest(request.headers.get('X-Nova-Autotrack-Token',''),token):
        raise HTTPException(401,'전향 수집 예약 인증 실패')


def _core566_worker(day):
    from prospective_collection import run, existing_for_day, ACCEPTED
    from collection_audit import audit_day, unverified_reason, integrity_detail, retry_integrity_once
    from daily_settlement_guard import discard_failed_regular_reference
    from core561_evidence import record
    from nova_v561_hierarchical import PRESET
    from ops_diagnostics import emit
    results={}; details={}; active=None
    def fetch(symbol):
        nonlocal active
        active=symbol
        try:
            def fresh(s):return _payload(s,'스윙','kis',15)
            payload=fresh(symbol)
            before=integrity_detail(payload)
            payload,retry=retry_integrity_once(symbol,day,payload,fetch=fresh,
                now=lambda:datetime.now(KST),calendar=krx_session,clear=discard_failed_regular_reference,wait=lambda:time.sleep(2))
            if before['signal_hold'] or (before.get('minute_coverage') or {}).get('zero_proofs'):
                try:emit('prospective_integrity',{'day':day,'symbol':symbol,'before':before,
                    'after':integrity_detail(payload),'retry':retry})
                except Exception:pass
            return payload
        except Exception:
            details[symbol]='FETCH_FAILED'
            raise
    def save(payload,now):
        try:status=record(payload,now=now)
        except Exception:
            details[active]='RECORD_FAILED'
            raise
        if status=='UNVERIFIED':
            try:details[active]=unverified_reason(payload,now)
            except Exception:details[active]='SNAPSHOT_REJECTED_OTHER'
        return status
    def progress(rows):
        symbol=next(reversed(rows)); status=rows[symbol]
        reason=details.get(symbol,status)
        with CORE561_LOCK:
            CORE561_JOB['results']=rows
            CORE561_JOB['details']=dict(details)
        try:emit('prospective_symbol',{'day':day,'symbol':symbol,'status':status,
            'reason':reason,'processed':len(rows),'requested':len(PRESET)})
        except Exception:pass
    try:
        status,results=run(day,PRESET,fetch=fetch,record=save,existing=existing_for_day,
            now=lambda:datetime.now(KST),calendar=krx_session,progress=progress)
    except Exception:
        status='failed'
        with CORE561_LOCK:results=dict(CORE561_JOB.get('results') or {})
    audit=audit_day(day,PRESET)
    if status=='complete' and (audit.get('status')!='READ_OK' or audit.get('verified_signals')!=len(PRESET)):
        status='partial'
    with CORE561_LOCK:CORE561_JOB.update(status=status,results=results,details=details,persistence=audit)
    try:
        emit('prospective_collection',{'day':day,'status':status,'requested':len(PRESET),
            'counts':{k:sum(v==k for v in results.values()) for k in ACCEPTED},'orders_enabled':False})
        emit('observation_day_audit',audit)
    except Exception:pass


@app.post('/api/research/core566/collect')
def core566_collect(request:Request):
    from collection_gate import inspect_gate
    from ops_diagnostics import emit
    _core566_auth(request)
    now=datetime.now(KST);day=now.date().isoformat()
    gate=inspect_gate(day,now,krx_session)
    try:emit('collection_gate',gate)
    except Exception:pass
    if gate['reason']!='PERMITTED':
        return {'status':'skipped' if gate['reason']=='HOLIDAY' else 'blocked',**gate}
    with CORE561_LOCK:
        if CORE561_JOB['status']=='running':return {'status':'running','day':CORE561_JOB['day']}
        CORE561_JOB.update(status='running',results={},details={},persistence=None,day=day,reason=None)
        threading.Thread(target=_core566_worker,args=(day,),daemon=True,name='nova-core566').start()
    return {'status':'running','day':day}


@app.get('/api/research/core566/job')
def core566_job(request:Request):
    _core566_auth(request)
    with CORE561_LOCK:return dict(CORE561_JOB)


@app.get('/api/research/core566/status')
def core566_status(request:Request):
    _authorize(request);_limit(request)
    from core561_evidence import enabled
    from prospective_collection import existing_for_day
    day=datetime.now(KST).date().isoformat()
    with CORE561_LOCK:job=dict(CORE561_JOB)
    configured=os.environ.get('NOVA_CORE566_AUTO_ENABLED')=='1' and len(os.environ.get('NOVA_AUTOTRACK_TOKEN',''))>=24 and enabled()
    # Do not create tables during a disabled status check.
    results={};state='DISABLED'
    if configured:
        try:results=existing_for_day(day);state='READ_OK'
        except Exception:state='READ_FAILED'
    return {'configured':configured,'day':day,'persistent_status':state,'verified_signals':sum(v=='EXISTS' for v in results.values()),
        'price_only':sum(v=='PRICE_ONLY' for v in results.values()),'job':job,'orders_enabled':False,'strategy_adoption':'HOLD'}


@app.get('/api/research/core570/report')
def core570_report(request:Request):
    _authorize(request);_limit(request)
    from core570_report import report
    try:return JSONResponse(report(),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'v5.7.0 관찰 DB 조회 실패') from None


@app.get('/api/research/core561/evidence')
def core561_report(request:Request,symbol:str='005930.KS'):
    _authorize(request);_limit(request)
    try:symbol=ticker_format(symbol)
    except ValueError:raise HTTPException(422,'국내 종목 코드 필요') from None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',symbol):raise HTTPException(422,'국내 종목만 지원')
    from core561_evidence import report
    try:return JSONResponse(report(symbol),headers={'Cache-Control':'no-store'})
    except Exception:raise HTTPException(503,'v5.6.1 관찰 DB 조회 실패') from None

@app.get('/api/research/core561/status')
def core561_status(request:Request):
    _authorize(request);_limit(request)
    from core561_evidence import enabled
    with CORE561_LOCK:job=dict(CORE561_JOB)
    return {'enabled':os.environ.get('NOVA_CORE560_AUTO_ENABLED')=='1' and os.environ.get('NOVA_AUTOTRACK_ENABLED')=='1' and len(os.environ.get('NOVA_AUTOTRACK_TOKEN',''))>=24 and enabled(),'job':job,'note':'최근 실행 상태는 서버 재시작 시 초기화. 영구 기록은 종목별 관찰 조회.'}

# v5.6.3: bounded, authenticated diagnostics. Reuse the existing read-only
# research worker and reserve its lock; no parallel duplicate batch.
OPS_LOCK=threading.RLock()
OPS_STATE={'status':'idle','result':None,'started_at':None}
OPS_LAST_START=None


def _ops_worker(job_id, full):
    from ops_diagnostics import configured, calendar_summary, chart_summary, database_probe, batch_summary, emit, num
    from market_sessions import chart_probe_allowed
    from analysis_chart_live import fetch
    result={'config':configured()}
    started=time.monotonic();stage='connections'
    try:
        now=datetime.now(KST);schedule=krx_session(now);today=now.date().isoformat()
        result['calendar']=calendar_summary(schedule)
        result['database']=database_probe()
        emit('connections',result)
        stage='chart_probe'
        result['charts']={}
        expected=chart_probe_allowed(now,schedule,provisional=True)
        if result['config']['kis_configured']:
            try:
                key,secret=_kis_creds();q=get_quote(key,secret,'005930',now)
                price=num(q.get('price'));result['kis_quote']={'status':'PASS' if price is not None and price>0 else 'NO_PRICE','date_verified':False}
            except Exception:result['kis_quote']={'status':'PROBE_FAILED','date_verified':False}
            for mode in ('1d','스윙','1분','5분','15분'):
                try:
                    if mode=='1d':
                        r=latest_kis_period('005930.KS','1d',now=now)
                        result['charts'][mode]=chart_summary(r,today,chart_probe_allowed(now,schedule))
                    else:
                        key,secret=_kis_creds()
                        r=fetch('005930.KS',mode,key,secret,now=now)
                        result['charts'][mode]=chart_summary(r,today,expected,analysis=True)
                except Exception:
                    result['charts'][mode]={'status':'PROBE_FAILED','today_valid_ohlcv':False}
        else:
            result['charts']['status']='NOT_CONFIGURED';result['kis_quote']={'status':'NOT_CONFIGURED'}
        emit('chart_probe',{'charts':result['charts'],'kis_quote':result['kis_quote'],'symbol':'005930.KS'})
        stage='research_batch'
        if full and result['config']['kis_configured']:
            from nova_v561_hierarchical import PRESET
            _v5_batch_worker(job_id,list(PRESET),'kis',15)
            with BATCH_RESEARCH_LOCK:result['research']=batch_summary(BATCH_RESEARCH_STATE)
        elif full:result['research']={'status':'KIS_NOT_CONFIGURED'}
        else:result['research']={'status':'NOT_REQUESTED'}
        stage='collector_status'
        with CORE561_LOCK:
            job=CORE561_JOB.copy()
        result['collector']={'status':job.get('status') if job.get('status') in ('idle','running','complete','partial','failed','skipped') else 'unknown',
                             'saved_or_exists':sum(v in ('SAVED','EXISTS') for v in (job.get('results') or {}).values()),'scheduler_installation':'NOT_VERIFIED_BY_APP'}
        final='complete'
    except Exception:
        result['error']='DIAGNOSTIC_FAILED';result['failure_stage']=stage;final='failed'
    finally:
        if full:
            with BATCH_RESEARCH_LOCK:
                if BATCH_RESEARCH_STATE.get('job_id')==job_id and BATCH_RESEARCH_STATE.get('status')=='running':
                    BATCH_RESEARCH_STATE.update(status='failed',ended_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        result['elapsed_seconds']=round(time.monotonic()-started,1)
        with OPS_LOCK:OPS_STATE.update(status=final,result=result)
        emit('diagnostic_end',{'status':final,**result})


def _start_ops(full=True):
    global OPS_LAST_START
    from nova_v561_hierarchical import PRESET
    with OPS_LOCK:
        if OPS_STATE['status']=='running':return 'running'
        if OPS_LAST_START is not None and time.monotonic()-OPS_LAST_START<600:return 'cooldown'
        job_id=secrets.token_hex(10)
        if full:
            with BATCH_RESEARCH_LOCK:
                if BATCH_RESEARCH_STATE['status']=='running':return 'batch_busy'
                BATCH_RESEARCH_STATE.update(status='running',job_id=job_id,symbols=list(PRESET),rows=[],core542_aligned=None,progress=None,finished=0,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),ended_at=None,source_requested='kis',cost_bps_per_side=15,checkpoint_enabled=False,resume_checkpoint=False,checkpoint=None)
        OPS_LAST_START=time.monotonic()
        OPS_STATE.update(status='running',result=None,job_id=job_id,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        threading.Thread(target=_ops_worker,args=(job_id,full),daemon=True,name='nova-ops563').start()
        return 'running'


def _start_v574_data_probe(version='574'):
    """Startup-only, explicit research diagnostic mode. Never runs full batch."""
    from ops_diagnostics import emit
    if version=='576':
        from nova_v576_expanded_coverage import pilot, quiet_window, GuardedBudget, MAX_CALLS, MAX_SECONDS
    elif version=='575':
        from nova_v575_calendar_coverage import pilot
    elif version=='574':
        from nova_v574_data_audit import pilot
    else:raise ValueError('unknown data probe')
    event='v'+version+'_data'
    from data_sources import KIS
    now=datetime.now(KST)
    schedule=krx_session(now)
    if not schedule.get('calendar_verified') or schedule.get('status')!='CLOSED':
        emit(event+'_end',{'status':'SKIPPED','reason':'MARKET_OPEN_OR_UNVERIFIED','strategy_adoption':'HOLD'})
        return 'skipped'
    if version=='576' and not quiet_window(now,schedule):
        emit(event+'_end',{'status':'SKIPPED','reason':'QUIET_WINDOW_REQUIRED','strategy_adoption':'HOLD'})
        return 'skipped'
    with OPS_LOCK:
        if OPS_STATE['status']=='running':return 'running'
        with BATCH_RESEARCH_LOCK:
            if BATCH_RESEARCH_STATE['status']=='running':return 'batch_busy'
            previous=dict(BATCH_RESEARCH_STATE)
            job=secrets.token_hex(10)
            BATCH_RESEARCH_STATE.update(status='running',job_id=job,symbols=[],rows=[],finished=0,progress=None)
        OPS_STATE.update(status='running',result=None,job_id=job,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    def worker():
        result={'status':'UNAVAILABLE','reason':'PROBE_FAILED','strategy_adoption':'HOLD'}
        started=time.monotonic()
        try:
            key,secret=_kis_creds()
            kwargs={}
            if version=='576':
                from nova_v578_kis_telemetry import KIS_LOAD
                from nova_v574_data_audit import ProbeError
                def guard():
                    current=datetime.now(KST)
                    if not quiet_window(current,krx_session(current)):return False
                    local=KIS_LOAD.snapshot()
                    if local['foreground_active'] or local['physical_active'] or local['research_active']:
                        raise ProbeError('FOREGROUND_BUSY')
                    if local['window_overflow'] or local['recent_provider_errors']:
                        raise ProbeError('PROVIDER_UNHEALTHY')
                    return True
                try:
                    if not guard():raise ProbeError('QUIET_WINDOW_ENDED')
                except ProbeError as exc:
                    result={'status':'SKIPPED','reason':str(exc),'strategy_adoption':'HOLD'}
                    return
                kwargs['budget']=GuardedBudget(guard,max_calls=MAX_CALLS,max_seconds=MAX_SECONDS)
                kwargs['grouped']=True
            result=pilot(KIS(key,secret),now,progress=lambda stats:emit(event+'_stream',stats),**kwargs)
        except Exception:
            pass # never log provider exceptions
        finally:
            result['elapsed_seconds']=round(time.monotonic()-started,1)
            with OPS_LOCK:
                with BATCH_RESEARCH_LOCK:
                    if BATCH_RESEARCH_STATE.get('job_id')==job:
                        BATCH_RESEARCH_STATE.clear();BATCH_RESEARCH_STATE.update(previous)
                OPS_STATE.update(status='complete' if result.get('status')=='READY' else 'failed',result=result)
            emit(event+'_end',result)
    try:threading.Thread(target=worker,daemon=True,name='nova-v'+version+'-data-probe').start()
    except Exception:
        with OPS_LOCK:
            with BATCH_RESEARCH_LOCK:
                if BATCH_RESEARCH_STATE.get('job_id')==job:
                    BATCH_RESEARCH_STATE.clear();BATCH_RESEARCH_STATE.update(previous)
            OPS_STATE.update(status='failed',result={'status':'UNAVAILABLE','reason':'THREAD_START_FAILED','strategy_adoption':'HOLD'})
        emit(event+'_end',{'status':'UNAVAILABLE','reason':'THREAD_START_FAILED','strategy_adoption':'HOLD'})
        return 'failed'
    return 'running'


def _ops_preflight_snapshot():
    from ops_preflight import snapshot
    from nova_v578_kis_telemetry import KIS_LOAD
    now=datetime.now(KST)
    schedule=krx_session(now)
    with SCAN_LOCK:
        jobs={f'scan_{p}':SCAN_PROFILES[p].get('status') for p in ('full','pattern')}
    for name,lock,state in (
        ('backtest',BACKTEST_LOCK,BACKTEST_STATE),
        ('popular',POPULAR_LOCK,POPULAR_STATE),
        ('autotrack',AUTO_LOCK,AUTO_STATE),
        ('prospective',CORE561_LOCK,CORE561_JOB),
        ('batch',BATCH_RESEARCH_LOCK,BATCH_RESEARCH_STATE),
        ('diagnostics',OPS_LOCK,OPS_STATE),
    ):
        with lock:
            jobs[name]=state.get('status')
    return snapshot(now,schedule,KIS_LOAD.snapshot(),jobs)


def _ops_preflight_loop(stop):
    """Observe only while process is alive; never wake a suspended service."""
    from ops_diagnostics import emit
    first=True
    while not stop.is_set():
        try:
            now=datetime.now(KST)
            if first or now.hour>=20 or now.hour<=7:
                emit('ops_preflight',_ops_preflight_snapshot())
        except Exception:
            emit('ops_preflight',{'local_preflight':'BLOCKED','reason':'SNAPSHOT_UNAVAILABLE','execution_authorized':False,'orders_enabled':False})
        first=False
        if stop.wait(60):
            break


def _ops_startup():
    from ops_diagnostics import configured, emit
    emit('startup',configured())
    from nova_v578_kis_telemetry import KIS_LOAD
    emit('v578_kis_telemetry',KIS_LOAD.snapshot())
    def probe():
        from prospective_collection import checkpoint_probe
        try:emit('checkpoint_probe',checkpoint_probe())
        except Exception:pass
        try:
            from collection_gate import inspect_gate
            from ops_diagnostics import database_probe
            now=datetime.now(KST)
            emit('collection_gate_probe',inspect_gate(now.date().isoformat(),now,krx_session))
            emit('observation_probe',database_probe())
        except Exception:pass
        try:
            from collection_audit import audit_day
            from nova_v561_hierarchical import PRESET
            day=datetime.now(KST).date().isoformat()
            emit('observation_day_audit',audit_day(day,PRESET))
        except Exception:pass
        try:
            from core570_report import report
            p=report()
            emit('v570_validation_probe',{k:p.get(k) for k in ('status','stored_records','verified_records','observed_days','completed','pending','missing_outcomes','shadow_frozen','shadow_completed','validation_state','strategy_adoption')})
        except Exception:
            emit('v570_validation_probe',{'status':'UNAVAILABLE','strategy_adoption':'HOLD'})
    threading.Thread(target=probe,daemon=True,name='nova-checkpoint-probe').start()
    if os.getenv('NOVA_OPS_DIAGNOSTICS_ON_START')=='1':_start_ops(True)
    elif os.getenv('NOVA_OPS_DIAGNOSTICS_ON_START')=='574':_start_v574_data_probe()
    elif os.getenv('NOVA_OPS_DIAGNOSTICS_ON_START')=='575':_start_v574_data_probe('575')
    elif os.getenv('NOVA_OPS_DIAGNOSTICS_ON_START')=='576':_start_v574_data_probe('576')




@app.post('/api/ops/diagnostics/run')
def ops_run(request:Request):
    _authorize(request);_limit(request)
    # Local unauthenticated deployments cannot trigger expensive operations.
    if not os.getenv('NOVA_ACCESS_TOKEN','').strip():raise HTTPException(503,'서버 접근 인증 설정 필요')
    status=_start_ops(True)
    return JSONResponse({'status':status},headers={'Cache-Control':'no-store'})


@app.get('/api/ops/diagnostics/status')
def ops_status(request:Request):
    _authorize(request);_limit(request)
    if not os.getenv('NOVA_ACCESS_TOKEN','').strip():raise HTTPException(503,'서버 접근 인증 설정 필요')
    with OPS_LOCK:snapshot=dict(OPS_STATE)
    with BATCH_RESEARCH_LOCK:
        if snapshot.get('job_id') and snapshot.get('job_id')==BATCH_RESEARCH_STATE.get('job_id'):
            snapshot['progress']=BATCH_RESEARCH_STATE.get('progress')
    snapshot.pop('job_id',None)
    from nova_v578_kis_telemetry import KIS_LOAD
    snapshot['kis_load']=KIS_LOAD.snapshot()
    return JSONResponse(snapshot,headers={'Cache-Control':'no-store'})


@app.get('/api/autotrack/status')
def autotrack_status(request: Request):
    _authorize(request)
    configured=os.environ.get('NOVA_AUTOTRACK_ENABLED')=='1' and len(os.environ.get('NOVA_AUTOTRACK_TOKEN',''))>=24
    response={'configured':configured,'database_connected':evidence_backend()!='disabled',
              'scheduler':'external_github_actions_required',
              'collection':'full-market snapshot screened, top 24 deep-analyzed when FSC/KRX key exists',
              'orders_enabled':False}
    with AUTO_LOCK:
        response['local_job_status']=AUTO_STATE.get('status')
        response['last_local_run']=AUTO_STATE.get('completed_at')
    if evidence_backend()!='disabled':
        try:response.update(auto_evidence_status())
        except Exception:response['storage_error']=True
    return JSONResponse(response,headers={'Cache-Control':'no-store'})


@app.get('/api/autotrack/report')
def autotrack_report(request: Request):
    _authorize(request)
    _limit(request)
    if evidence_backend()=='disabled':
        raise HTTPException(503,'영구 DB 미연결')
    try:return JSONResponse(auto_evidence_report(),headers={'Cache-Control':'no-store'})
    except Exception:
        raise HTTPException(503,'자동 추천 검증 기록을 조회하지 못했습니다.') from None


@app.get('/api/research/market-regimes')
def research_market_regimes(request: Request):
    """Research-only: immutable scan-time regimes and subsequent outcome comparison."""
    _authorize(request)
    _limit(request)
    if evidence_backend() == 'disabled':
        raise HTTPException(503,'영구 DB 연결 후 시장 상황별 연구가 가능합니다.')
    try:
        from auto_evidence import list_candidates, list_completed, latest_scan_regimes
        payload=regime_report(list_candidates(500),list_completed())
        payload['latest_market_snapshots']=latest_scan_regimes(3)
        return JSONResponse(payload,headers={'Cache-Control':'no-store'})
    except Exception:
        logging.getLogger('nova.regime').exception('Market-regime read-only report failed')
        raise HTTPException(503,'시장 상황 연구 결과를 조회하지 못했습니다.') from None


# Domestic market-activity rankings; independent from NOVA BUY/SELL.
# Naver popularity is linked on the client, never scraped or redistributed.
def _popular_snapshot():
    with POPULAR_LOCK:
        state = dict(POPULAR_STATE)
    state.pop('completed_monotonic', None)
    state['fsc_configured'] = bool(os.environ.get('FSC_STOCK_API_KEY','').strip())
    state['krx_configured'] = bool(os.environ.get('KRX_API_KEY','').strip())
    state['naver_direct_link'] = 'https://stock.naver.com/market/stock/kr/stocklist/top'
    state['not_buy_signal'] = True
    state['not_realtime'] = True
    return state


def _popular_worker(job_id: str, source: str, key: str):
    try:
        result = fetch_popularity(source=source,key=key)
        with POPULAR_LOCK:
            if POPULAR_STATE.get('job_id') == job_id:
                POPULAR_STATE.update(status='complete',result=result,error=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    completed_monotonic=time.monotonic())
    except MarketScanError as exc:
        with POPULAR_LOCK:
            if POPULAR_STATE.get('job_id') == job_id:
                POPULAR_STATE.update(status='failed',result=None,
                    error={'code':exc.code,'message':str(exc)})
    except Exception:
        with POPULAR_LOCK:
            if POPULAR_STATE.get('job_id') == job_id:
                POPULAR_STATE.update(status='failed',result=None,
                    error={'code':'provider_error','message':'시장 활동 순위 자료를 확인할 수 없습니다. 공급원 연결을 점검하세요.'})


@app.get('/api/popular/status')
def popular_status(request: Request):
    _authorize(request)
    _limit(request)
    return JSONResponse(_popular_snapshot(),headers={'Cache-Control':'no-store'})


@app.post('/api/popular/run')
def popular_run(request: Request, body: dict):
    _authorize(request)
    _limit(request)
    source = body.get('source','auto')
    if source not in ('auto','fsc','krx'):
        raise HTTPException(422,'데이터 공급원은 auto, fsc, krx 중 하나여야 합니다.')
    if source == 'auto':
        source = 'fsc' if os.environ.get('FSC_STOCK_API_KEY','').strip() else 'krx'
    name = 'FSC_STOCK_API_KEY' if source == 'fsc' else 'KRX_API_KEY'
    key = os.environ.get(name,'').strip()
    if not key:
        raise HTTPException(503,{'code':'source_unavailable',
          'message':'국내 시장 활동 순위에는 FSC_STOCK_API_KEY 또는 KRX_API_KEY 설정이 필요합니다.'})
    global POPULAR_JOB
    with POPULAR_LOCK:
        if POPULAR_STATE.get('status') == 'running':
            return JSONResponse(dict(_popular_snapshot(),notice='현재 인기종목 순위 자료를 확인 중입니다.'),headers={'Cache-Control':'no-store'})
        if POPULAR_STATE.get('status') == 'complete' and POPULAR_STATE.get('source') == source and \
           POPULAR_STATE.get('completed_monotonic') and time.monotonic()-POPULAR_STATE['completed_monotonic'] < 30*60:
            return JSONResponse(dict(_popular_snapshot(),notice='30분 이내 조회 결과를 재사용합니다.'),headers={'Cache-Control':'no-store'})
        job_id = secrets.token_hex(8)
        POPULAR_STATE.clear()
        POPULAR_STATE.update(status='running',source=source,job_id=job_id,result=None,error=None,
          started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        POPULAR_JOB = threading.Thread(target=_popular_worker,args=(job_id,source,key),daemon=True,
                                     name='nova-market-activity-rank')
        POPULAR_JOB.start()
    return JSONResponse(_popular_snapshot(),headers={'Cache-Control':'no-store'})


@app.get('/api/recommendations/status')
def recommendations_status(request: Request, profile: str='full'):
    _authorize(request)
    if profile!='all' and profile not in SCAN_PROFILES:
        raise HTTPException(422,'잘못된 검색 유형')
    _limit_scan_status(request)
    if profile=='all':
        # One consistent snapshot; no market data requests, job creation or writes.
        with SCAN_LOCK:
            payload={'profiles':{p:_scan_snapshot(p) for p in ('full','pattern')}}
        return JSONResponse(payload,headers={'Cache-Control':'no-store'})
    return JSONResponse(_scan_snapshot(profile),headers={'Cache-Control':'no-store'})


@app.post('/api/recommendations/run')
def recommendations_run(request: Request, body: dict):
    _authorize(request)
    _limit(request)
    profile=body.get('profile','full')
    if profile not in ('full','pattern'):
        raise HTTPException(422,'검색 유형은 full 또는 pattern이어야 합니다.')
    depth=body.get('depth',48)
    if type(depth) is not int or depth not in (24,48,60):
        raise HTTPException(422,'심층 분석 수는 24/48/60 중에서 선택하세요.')
    market=body.get('market','all')
    if market not in ('all','kospi','kosdaq'):
        raise HTTPException(422,'시장 구분은 all, kospi, kosdaq 중 하나여야 합니다.')
    provider=body.get('source','auto')
    if provider not in ('auto','fsc','krx'):
        raise HTTPException(422, '데이터 공급원은 auto, fsc, krx 중 하나여야 합니다.')
    if provider=='auto':
        provider='fsc' if os.environ.get('FSC_STOCK_API_KEY','').strip() else 'krx'
    keyname='FSC_STOCK_API_KEY' if provider=='fsc' else 'KRX_API_KEY'
    key=os.environ.get(keyname,'').strip()
    if not key:
        if provider=='fsc':
            raise HTTPException(503, {'code':'fsc_not_configured',
                'message':'공공데이터포털 금융위원회_주식시세정보를 활용 신청하고 Render의 FSC_STOCK_API_KEY를 설정하세요.'})
        raise HTTPException(503, {'code':'krx_not_configured',
            'message':'FSC_STOCK_API_KEY 또는 KRX_API_KEY가 없습니다. 금융위원회 주식시세 API 활용신청을 권장합니다.'})
    with BACKTEST_LOCK:
        if BACKTEST_STATE.get('status') == 'running':
            raise HTTPException(409,'과거 검증이 진행 중입니다. 종료 후 전체 시장 검색을 실행하세요.')
    global SCAN_JOB
    state=SCAN_PROFILES[profile]
    with SCAN_LOCK:
        current=state.get('status')
        if current in SCAN_ACTIVE:
            return JSONResponse(dict(_scan_snapshot(profile),notice='같은 검색이 이미 등록되어 있습니다.'),
                                headers={'Cache-Control':'no-store'})
        if (current=='complete' and state.get('market')==market and state.get('source')==provider and
            state.get('depth')==depth and state.get('scan_profile','full')==profile and
            state.get('completed_monotonic') and
            time.monotonic()-state['completed_monotonic']<20*60):
            return JSONResponse(dict(_scan_snapshot(profile),notice='20분 이내 완료한 검색 결과를 재사용합니다.'),
                                headers={'Cache-Control':'no-store'})
        job_id=secrets.token_hex(8)
        state.clear()
        label='금융위원회 T+1' if provider=='fsc' else 'KRX 장 마감'
        state.update(status='queued',market=market,source=provider,depth=depth,job_id=job_id,scan_profile=profile,
            progress={'phase':'queued','done':0,'total':None,'message':f'{label} 독립 검색 대기 · 다른 검색 종료 후 자동 시작'},
            result=None,error=None,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            started_monotonic=time.monotonic())
        SCAN_PENDING[profile]=(market,job_id,provider,key,depth,profile)
        _start_queued_scan_locked()
    return JSONResponse(_scan_snapshot(profile),headers={'Cache-Control':'no-store'})


@app.post('/api/recommendations/cancel')
def recommendations_cancel(request: Request, body: dict):
    _authorize(request)
    _limit(request)
    profile=body.get('profile','full')
    if profile not in SCAN_PROFILES: raise HTTPException(422,'잘못된 검색 유형')
    state=SCAN_PROFILES[profile]
    with SCAN_LOCK:
        if body.get('job_id') != state.get('job_id') or not body.get('job_id'):
            raise HTTPException(409,'현재 검색 작업과 일치하지 않습니다.')
        if state.get('status')=='queued':
            SCAN_PENDING.pop(profile,None)
            state.update(status='cancelled',result=None,error={'code':'cancelled','message':'대기 중인 검색을 취소했습니다.'},
                         completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            return JSONResponse(_scan_snapshot(profile),headers={'Cache-Control':'no-store'})
        if state.get('status') not in ('running','cancelling'):
            raise HTTPException(409,'취소 가능한 검색이 없습니다.')
        SCAN_CANCEL.set()
        state['status']='cancelling'
        return JSONResponse(_scan_snapshot(profile),headers={'Cache-Control':'no-store'})


@app.post('/api/recommendations/diagnose')
def recommendations_diagnose(request: Request):
    """Auth-protected, user-triggered KRX production connection check.

    No raw keys, headers, provider response, or arbitrary URLs disclosed.
    """
    _authorize(request)
    _limit(request)
    key=os.environ.get('KRX_API_KEY','').strip()
    if not key:
        return JSONResponse({'configured':False,'date':None,'results':[],
            'notes':'Render 환경변수 KRX_API_KEY가 설정되지 않았습니다.'},
            headers={'Cache-Control':'no-store'})
    date=next(completed_dates(datetime.now(KST)))
    details=diagnose_connection(key,date,['kospi','kosdaq'])
    return JSONResponse(details,headers={'Cache-Control':'no-store'})


# v4.4 historical baseline: authenticate, run a bounded weekly FSC screen
# on past snapshots, evaluate only after publication, and never call this
# "final NOVA recommendation" performance.
def _history_snapshot():
    with BACKTEST_LOCK:
        data = dict(BACKTEST_STATE)
    data.pop('started_monotonic', None)
    data['fsc_configured'] = bool(os.environ.get('FSC_STOCK_API_KEY','').strip())
    data['scope'] = 'first_stage_only'
    return data


def _history_worker(job_id: str, market: str, weeks: int, cost_bps: int, key: str):
    def on_progress(info):
        with BACKTEST_LOCK:
            if BACKTEST_STATE.get('job_id') == job_id:
                BACKTEST_STATE['progress'] = info
    try:
        result = run_screen_history(market=market, api_key=key, weeks=weeks,
                                    cost_bps=cost_bps, progress=on_progress, load_bars=lambda sym:_journal_daily_bars(sym,'kis'))
        with BACKTEST_LOCK:
            if BACKTEST_STATE.get('job_id') == job_id:
                BACKTEST_STATE.update(status='complete',result=result,error=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    completed_monotonic=time.monotonic())
    except MarketScanError as exc:
        with BACKTEST_LOCK:
            if BACKTEST_STATE.get('job_id') == job_id:
                BACKTEST_STATE.update(status='failed',result=None,
                    error={'code':exc.code,'message':str(exc)},
                    completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    except Exception:
        # Never expose provider URLs: FSC serviceKey can be embedded in exception.
        with BACKTEST_LOCK:
            if BACKTEST_STATE.get('job_id') == job_id:
                BACKTEST_STATE.update(status='failed',result=None,
                    error={'code':'history_failed',
                           'message':'과거 시세를 검증할 수 없습니다. 공급원 상태와 서버 로그를 확인하세요.'},
                    completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


@app.get('/api/recommendations/backtest/status')
def recommendation_backtest_status(request: Request):
    _authorize(request)
    _limit(request)
    return JSONResponse(_history_snapshot(),headers={'Cache-Control':'no-store'})


@app.post('/api/recommendations/backtest/run')
def recommendation_backtest_run(request: Request, body: dict):
    _authorize(request)
    _limit(request)
    market = body.get('market','all')
    weeks = body.get('weeks',4)
    cost = body.get('cost_bps',15)
    if market not in ('all','kospi','kosdaq') or type(weeks) is not int or weeks not in (4,8) or type(cost) is not int or not 0<=cost<=100:
        raise HTTPException(422,'허용 범위: 시장 all/kospi/kosdaq, 과거 4/8주, 편도 비용 0~100bp')
    key = os.environ.get('FSC_STOCK_API_KEY','').strip()
    if not key:
        raise HTTPException(503, {'code':'fsc_not_configured',
              'message':'Render에 FSC_STOCK_API_KEY를 먼저 설정하세요.'})
    with SCAN_LOCK:
        if any(v.get('status') in SCAN_ACTIVE for v in SCAN_PROFILES.values()):
            raise HTTPException(409,'현재 종목추천 검색 중입니다. 검색이 종료된 후 백테스트를 실행하세요.')
    global BACKTEST_JOB
    with BACKTEST_LOCK:
        if BACKTEST_STATE.get('status') == 'running':
            return JSONResponse(dict(_history_snapshot(),notice='과거 검증이 이미 진행 중입니다.'),headers={'Cache-Control':'no-store'})
        if (BACKTEST_STATE.get('status') == 'complete' and
            BACKTEST_STATE.get('market') == market and
            BACKTEST_STATE.get('weeks') == weeks and
            BACKTEST_STATE.get('cost_bps') == cost and
            BACKTEST_STATE.get('completed_monotonic') and
            time.monotonic()-BACKTEST_STATE['completed_monotonic'] < 20*60):
            return JSONResponse(dict(_history_snapshot(),notice='20분 이내 완료된 과거 검증을 재사용합니다.'),headers={'Cache-Control':'no-store'})
        job_id=secrets.token_hex(8)
        BACKTEST_STATE.clear()
        BACKTEST_STATE.update(status='running',market=market,weeks=weeks,cost_bps=cost,
              job_id=job_id,result=None,error=None,started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
              started_monotonic=time.monotonic(),
              progress={'phase':'starting','done':0,'total':weeks,'message':'과거 일별 전체시장 스냅샷 조회 시작'})
        BACKTEST_JOB=threading.Thread(target=_history_worker,args=(job_id,market,weeks,cost,key),daemon=True,name='nova-market-history')
        BACKTEST_JOB.start()
    return JSONResponse(_history_snapshot(),headers={'Cache-Control':'no-store'})
