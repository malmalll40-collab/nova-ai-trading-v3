"""NOVA 5.6.2 bounded, read-only operations diagnostics.
Log ONLY explicit allowlisted fields. Never emit provider payloads, keys, URLs,
account IDs, raw exceptions, or request headers. No order or adoption paths.
"""
import json
import logging
import math
import os
import re
from numbers import Real
from datetime import datetime, timezone
from pathlib import Path

VERSION = '5.7.0'
LOG = logging.getLogger('uvicorn.error')
STATES = {'DATA_MISSING','RISK_OR_DATA_VETO','NO_BUY_CANDIDATE','INSUFFICIENT_POOL','NONPOSITIVE_EV','RESEARCH_ESTIMATE'}
CAL_CODES = {'XKRX_IMPORT_UNAVAILABLE','XKRX_LOAD_FAILED','XKRX_DATE_OUT_OF_RANGE','XKRX_SESSION_CHECK_FAILED','XKRX_PREVIOUS_SESSION_FAILED'}
PORTFOLIOS = ('core','adaptive','rs_required','transition_soft','rs_hybrid','risk_chase','unified','unified_sized','unified_holdout')


def emit(event, summary):
    # All callers construct summaries with fixed fields below; do not log exceptions.
    LOG.info('NOVA_OPS %s', json.dumps({'app_version':VERSION,'event':event,**summary}, ensure_ascii=True, allow_nan=False, separators=(',',':')))


def configured():
    return {'kis_configured':bool(os.getenv('KIS_APP_KEY','').strip() and os.getenv('KIS_APP_SECRET','').strip()),
            'db_configured':bool(os.getenv('DATABASE_URL','').strip() or os.getenv('NOVA_EVIDENCE_SQLITE_PATH','').strip()),
            'evidence_enabled':os.getenv('NOVA_CORE541_EVIDENCE_ENABLED','').lower() in ('1','true','yes'),
            'collector_enabled':os.getenv('NOVA_CORE560_AUTO_ENABLED')=='1' and os.getenv('NOVA_AUTOTRACK_ENABLED')=='1' and len(os.getenv('NOVA_AUTOTRACK_TOKEN',''))>=24,
            'prospective_enabled':os.getenv('NOVA_CORE566_AUTO_ENABLED')=='1', 'prospective_token_configured':len(os.getenv('NOVA_AUTOTRACK_TOKEN',''))>=24,
            'startup_full_enabled':os.getenv('NOVA_OPS_DIAGNOSTICS_ON_START')=='1','orders_enabled':False}


def num(x):
    if isinstance(x, bool) or not isinstance(x, Real):return None
    try:
        value=float(x)
        return (int(x) if isinstance(x, int) else value) if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):return None


def day(x):
    try:
        s=str(x)[:10]
        return datetime.strptime(s,'%Y-%m-%d').date().isoformat() if re.fullmatch(r'\d{4}-\d{2}-\d{2}',s) else None
    except (ValueError,TypeError):return None


def calendar_summary(s):
    return {'day':day(s.get('day')),'verified':s.get('calendar_verified') is True,'open':s.get('is_open') is True,
            'session_exists':bool(s.get('open_kst') and s.get('close_kst')),
            'error_code':s.get('calendar_error_code') if s.get('calendar_error_code') in CAL_CODES else None}


def valid_bar(bar):
    try:
        o,h,l,c,v=(num(bar.get(k)) for k in ('open','high','low','close','volume'))
        return all(x is not None for x in (o,h,l,c,v)) and min(o,h,l,c,v)>0 and h>=max(o,c) and l<=min(o,c)
    except (AttributeError,TypeError):return False


def chart_summary(result, today, expected, analysis=False):
    if analysis:
        rows=result.get('rows') or []
        r=rows[-1] if rows else {}; date=day(r.get('date')); bar=r.get('bar')
    else:
        date=day(result.get('trading_date') or result.get('date'));bar=result.get('bar')
    available=result.get('available') is True and valid_bar(bar) and date==today
    return {'status':'PASS' if available else ('NOT_AVAILABLE' if expected else 'OUTSIDE_WINDOW'),
            'expected_today':expected,'last_date':date,'today_valid_ohlcv':available,
            'display_only':True}


# Allow only stable, non-secret error identifiers; never log exception text.
DB_ERROR_TYPES = {'OperationalError','InterfaceError','ProgrammingError','DatabaseError',
                  'ConnectionTimeout','ConnectionFailure','InvalidPassword','InvalidAuthorizationSpecification',
                  'InsufficientPrivilege','UndefinedTable','QueryCanceled','InvalidCatalogName',
                  'InvalidParameterValue','FeatureNotSupported','AdminShutdown','CannotConnectNow',
                  'TooManyConnections','ModuleNotFoundError','ImportError','TimeoutError',
                  'Error','IntegrityError','DataError','InternalError','NotSupportedError'}


def db_error(exc, stage):
    name=type(exc).__name__
    code=getattr(exc,'sqlstate',None)
    code=code if isinstance(code,str) and re.fullmatch(r'[0-9A-Z]{5}',code) else None
    return {'status':'CONNECT_FAILED' if stage=='connect' else 'READ_FAILED',
            'failure_stage':stage,'error_type':name if name in DB_ERROR_TYPES else 'OTHER',
            'sqlstate':code}


def database_probe():
    """Use the same connection arguments as persistence, but never write/create tables."""
    from evidence_store import backend
    kind=backend(); result={'backend':kind,'status':'NOT_CONFIGURED','observation_table_exists':None,'observations':None}
    if kind=='disabled':return result
    stage='connect'
    try:
        if kind=='postgresql':
            import psycopg
            # Neon/pooler startup parameters can differ from a direct connection.
            # Set the timeout inside the transaction, not the connection startup packet.
            with psycopg.connect(os.environ['DATABASE_URL'].strip(),connect_timeout=5) as db:
                with db.cursor() as cur:
                    stage='read_only';cur.execute('SET TRANSACTION READ ONLY')
                    stage='timeout';cur.execute('SET LOCAL statement_timeout = 5000')
                    stage='ping';cur.execute('SELECT 1');cur.fetchone()
                    stage='table_lookup';cur.execute("SELECT to_regclass('nova_core561_observations') IS NOT NULL")
                    result['observation_table_exists']=bool(cur.fetchone()[0])
                    if result['observation_table_exists']:
                        stage='count';cur.execute('SELECT COUNT(*) FROM nova_core561_observations');result['observations']=int(cur.fetchone()[0])
                    stage='transaction_exit'
        else:
            import sqlite3
            p=Path(os.environ['NOVA_EVIDENCE_SQLITE_PATH']).expanduser().resolve()
            if not p.is_file():return {**result,'status':'FILE_NOT_CREATED'}
            with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                cur=db.cursor();stage='ping';cur.execute('SELECT 1');cur.fetchone()
                stage='table_lookup';cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nova_core561_observations'")
                result['observation_table_exists']=cur.fetchone() is not None
                if result['observation_table_exists']:
                    stage='count';cur.execute('SELECT COUNT(*) FROM nova_core561_observations');result['observations']=int(cur.fetchone()[0])
                stage='transaction_exit'
        return {**result,'status':'READ_OK'}
    except Exception as exc:return {**result,**db_error(exc,stage)}


def progress_summary(phase, requested, finished, ready, elapsed, symbol=None, symbol_status=None, symbol_elapsed=None):
    return {'phase':phase,'requested':requested,'finished':finished,'ready':ready,
            'elapsed_seconds':round(max(0,elapsed),1),
            'symbol':symbol if isinstance(symbol,str) and re.fullmatch(r'\d{6}\.(KS|KQ)',symbol) else None,
            'symbol_status':symbol_status if symbol_status in ('READY','INSUFFICIENT','UNAVAILABLE') else None,
            'symbol_elapsed_seconds':round(max(0,symbol_elapsed),1) if symbol_elapsed is not None else None}


V570_STRATEGIES = ('core','strength','adaptive','hybrid','rs_required','transition_soft','rs_hybrid','risk_chase','range_guard','regime_switch','sideways_guard','v570')
V570_FIELDS = ('trades','dates','symbols','hit_pct','mean_net_pct','median_net_pct','stress_mean_pct','tail10_pct','worst_pct','profit_factor')
V570_REGIMES = ('strong_up','mild_up','sideways','up_transition','down','unknown')
V570_MARKETS = ('up','range','down','unknown')
V570_STATES = ('FILTERED','INSUFFICIENT_POOL','RESEARCH_CANDIDATE','RISK_OR_EV_HOLD')


def accuracy_summary(data):
    def mapping(x):return x if isinstance(x,dict) else {}
    d=mapping(data)
    base={'status':'READY' if d.get('status')=='READY' else 'UNAVAILABLE','historical_only':True,'strategy_adoption':'HOLD'}
    if base['status']!='READY':return base
    def fields(x,keys):return {k:num(mapping(x).get(k)) for k in keys}
    def table(x):return {k:fields(v,V570_FIELDS) for k,v in mapping(x).items() if k in V570_STRATEGIES and isinstance(v,dict)}
    def groups(x,keys):return {k:table(v) for k,v in mapping(x).items() if k in keys}
    return {**base,'start':day(d.get('start')),'end':day(d.get('end')),'outcome_end':day(d.get('outcome_end')),
            **fields(d,('cost_bps','completed','shadow_completed','market_known_completed')),
            'strategies':table(d.get('strategies')),'guard_comparison':table(d.get('guard_comparison')),'shadow_comparison':table(d.get('shadow_comparison')),
            'by_regime':groups(d.get('by_regime'),V570_REGIMES),'by_market':groups(d.get('by_market'),V570_MARKETS),'shadow_by_regime':groups(d.get('shadow_by_regime'),V570_REGIMES),
            'changes':{k:fields(v,('blocked_wins','blocked_losses','added_wins','added_losses')) for k,v in mapping(d.get('changes')).items() if k in ('sideways_guard','v570')},
            'calibration':fields(d.get('calibration'),('scored','brier','naive_brier')),'states':{k:num(v) for k,v in mapping(d.get('states')).items() if k in V570_STATES}}


def batch_summary(state):
    try:emit_accuracy(state)
    except Exception:pass # reporting must not affect research
    try:emit_policy571(state)
    except Exception:pass # reporting must not affect research
    rows=state.get('rows') or [];aligned=state.get('core542_aligned') or {};u=aligned.get('v561_unified') or {}
    portfolio=u.get('portfolio') or {}; groups=u.get('by_group') or {}
    def perf(p):return {k:num(p.get(k)) for k in ('return_pct','mdd_pct','trades','stress_return_pct')}
    def group(g):return {k:{'stocks':num(v.get('stocks')),'mean_pct':num(v.get('mean_pct'))} for k,v in g.items() if k in PORTFOLIOS and isinstance(v,dict)}
    return {'requested':len(state.get('symbols') or []),'ready':sum(r.get('status')=='READY' for r in rows),
            'kis_ready':sum(r.get('status')=='READY' and r.get('source')=='kis' for r in rows),
            'aligned_status':'READY' if aligned.get('status')=='READY' else 'UNAVAILABLE',
            'start':day(aligned.get('start')),'end':day(aligned.get('end')),'samples':num(u.get('samples')),
            'scored':num(u.get('scored')),'buy_signals':num(u.get('buy_signals')),'brier':num(u.get('brier')),
            'naive_brier':num(u.get('naive_brier')),'expected_return_mae_pct':num(u.get('expected_return_mae_pct')),
            'holdout_scored':num(u.get('holdout_scored')),'diagnostics':{k:num(v) for k,v in (u.get('diagnostics') or {}).items() if k in STATES},
            'portfolio':{k:perf(v) for k,v in portfolio.items() if k in PORTFOLIOS and isinstance(v,dict)},
            'semiconductor_registered_excluded':group(groups.get('반도체 ETF 등록 종목 제외') or {}),
            'semiconductor_research_excluded':group(groups.get('연구 분류 반도체 제외') or {}),
            'nonoverlap_candidate_value':{k:{f:num(v.get(f)) for f in ('added_count','blocked_count','delta_pct_sum')} for k,v in (u.get('nonoverlap_candidate_value') or {}).items() if k in ('rs_required','entry_wider','risk_chase','transition_soft','rs_hybrid','regime_router','early_turn','range_guard','regime_switch') and isinstance(v,dict)},
            'historical_only':True,'strategy_adoption':'HOLD'}


def emit_accuracy(state):
    summary=accuracy_summary((state.get('core542_aligned') or {}).get('v570_accuracy'))
    groups={k:summary.pop(k,{}) for k in ('by_regime','by_market','shadow_by_regime')}
    emit('v570_historical',summary)
    for kind,group in groups.items():
        for regime,metrics in group.items():
            emit('v570_historical_regime',{'kind':kind,'regime':regime,'metrics':metrics,'historical_only':True,'strategy_adoption':'HOLD'})

def emit_policy571(state):
    d=(state.get('core542_aligned') or {}).get('v571_policy') or {}
    base={'model_version':'5.7.1','status':'READY' if d.get('status')=='READY' else 'UNAVAILABLE',
          'historical_only':True,'strategy_adoption':'HOLD'}
    if base['status']!='READY':
        emit('v571_historical',base);return
    states=('FILTERED','INSUFFICIENT_POOL','NONPOSITIVE_CORE','ROUTE_FILTER','SELECTED_ROUTE','CORE_FALLBACK')
    def fields(x,keys):return {k:num((x or {}).get(k)) for k in keys}
    def summary(x):
        return {**fields(x,('opportunities','core_candidates','eligible_candidates','blocked_wins','blocked_nonpositive','scored','brier','naive_brier')),
                **{k:fields(x.get(k),V570_FIELDS) for k in ('core','v570','v571')},
                'matched_calibration':fields(x.get('matched_calibration'),('scored','v570','v571','v570_naive','v571_naive')),
                'states':fields(x.get('states'),states)}
    emit('v571_historical',{**base,'start':day(d.get('start')),'end':day(d.get('end')),
         'outcome_end':day(d.get('outcome_end')),'market_coverage':fields(d.get('market_coverage'),V570_MARKETS),
         'overall':summary(d.get('overall') or {})})
    for kind,keys in (('by_regime',V570_REGIMES),('by_market',V570_MARKETS)):
        for k in keys:
            emit('v571_historical_regime',{**base,'kind':kind,'regime':k,'metrics':summary((d.get(kind) or {}).get(k) or {})})
