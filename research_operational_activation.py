"""Owner-authorized bounded production research rollout, OFF by default.

All instrumented REST physical calls share a PostgreSQL reservation ledger.
The chosen cap is an internal conservative policy, NOT a verified provider
entitlement. No orders; no live strategy adoption. A failed initialization
does not change existing foreground behavior. Uncertain leases stay held.
"""
import os
import time
import threading
from datetime import datetime
from types import SimpleNamespace
from nova_v574_data_audit import KST
from ops_preflight import TERMINAL
from nova_v578_kis_telemetry import KIS_LOAD
from nova_v579_admission import PROVIDER_STOPS
from research_kis_shared_quota import SharedQuotaLedger
from research_kis_transport_gate import SharedRequestGate

CAP=3
DRAIN_SECONDS=120
MAX_PASSES=6

def eligible(s):
    # Ignore previous research request counts, not current work/errors.
    return (s.get('quiet_window') is True and s.get('calendar_verified') is True
        and len(s.get('jobs',{}))==8 and all(v in TERMINAL for v in s['jobs'].values())
        and s.get('window_overflow') is False
        and all(s.get('kis_load',{}).get(k)==0 for k in
                ('foreground_active','physical_active','research_active','recent_provider_errors')))

def summarize(r):
    c=r.get('coverage',{});a=r.get('collection',{});e=r.get('evaluation',{})
    out={'status':r.get('status'),'reason':r.get('reason'),
         'stored_rows':c.get('stored_rows'),'expected_rows':c.get('expected_rows'),
         'complete_streams':c.get('complete_streams'),'collection_status':a.get('status'),
         'collection_reason':a.get('reason'),'new_rows':a.get('new_rows',0),
         'logical_calls':a.get('logical_calls',0),'strategy_adoption':'HOLD','orders_enabled':False,
         'account_quota_verified':False,'independent_future_verified':False}
    diagnostic=a.get('window_diagnostic')
    if isinstance(diagnostic,dict):
        out['window_diagnostic']={k:diagnostic[k] for k in ('symbol','reason','pages','expected_sessions',
            'observed_sessions','unexpected_sessions','missing_sessions','first','last','anchors_expected','anchors_observed') if k in diagnostic}
    if e:
        out['evaluation_status']=e.get('status')
        out['signal_dates']=e.get('signal_dates');out['opportunities']=e.get('opportunities')
        out['core']=e.get('strategies',{}).get('core')
        h=e.get('policy',{}).get('frozen_holdout',{})
        out['frozen_holdout']={k:h.get(k) for k in ('status','start','end','signal_dates','training_rows','model_updates_in_holdout','overall')}
        out['ml_status']=e.get('ml',{}).get('status')
    return out

def worker(stop,snapshot,calendar,gate,dsn,*,wait=None,client_factory=None,runner=None,session_probe=None,emit=None):
    if wait is None:wait=stop.wait
    if emit is None:
        from ops_diagnostics import emit
    if runner is None:
        from research_history_pipeline import run_bounded as runner
    if session_probe is None:
        from research_db_session_probe import probe as session_probe
    def available():
        try:return not stop.is_set() and eligible(snapshot())
        except Exception:return False
    if wait(DRAIN_SECONDS):
        return
    if not available():
        emit('research_collection_stop',{'reason':'DRAIN_OR_WINDOW_BUSY','orders_enabled':False})
        return
    p=session_probe(dsn)
    if p.get('direct_connection_status')!='READ_OK' or p.get('session_probe_verified') is not True:
        emit('research_collection_stop',{'reason':'DIRECT_SESSION_UNAVAILABLE','orders_enabled':False})
        return
    # Inspect outstanding reservations before token or collection requests.
    try:
        quota=gate.status()
        emit('research_quota_probe',quota)
        if quota.get('undispatched_research',0) and available():
            cancelled=gate.recover_undispatched_research()
            emit('research_undispatched_recovery',{'cancelled':cancelled,'orders_enabled':False})
            quota=gate.status()
            emit('research_quota_probe',quota)
        if quota['unfinished_foreground'] or quota['unfinished_research']:
            emit('research_collection_stop',{'reason':'UNFINISHED_SHARED_RESERVATION','orders_enabled':False})
            return
    except Exception:
        emit('research_collection_stop',{'reason':'SHARED_QUOTA_STATUS_UNAVAILABLE','orders_enabled':False})
        return
    # Existing KIS encrypted token cache handles issuance if expired; no new
    # credential access, no token values logged. Only one client per worker.
    if client_factory is None:
        from data_sources import KIS
        client_factory=lambda:KIS(os.getenv('KIS_APP_KEY',''),os.getenv('KIS_APP_SECRET',''))
    try:kis=client_factory()
    except Exception:
        emit('research_collection_stop',{'reason':'KIS_CLIENT_UNAVAILABLE','orders_enabled':False})
        return
    coordinator=SimpleNamespace(available=available,reserve=gate.reserve)
    try:
        if wait(2):return  # allow any completed token request's quota buffer to expire
        for i in range(MAX_PASSES):
            if not available():
                emit('research_collection_stop',{'reason':'FOREGROUND_OR_WINDOW_BUSY','orders_enabled':False})
                break
            limits={'max_calls':4,'max_seconds':45} if i==0 else {}
            r=runner(dsn,kis=kis,telemetry=KIS_LOAD,coordinator=coordinator,
                     session_mode_confirmed=True,now=lambda:datetime.now(KST),
                     calendar=calendar,allow_collection=True,**limits)
            out=summarize(r);emit('research_collection_pass',{'pass':i+1,**out})
            evaluation=r.get('evaluation',{})
            if evaluation:
                for k,metrics in evaluation.get('strategies',{}).items():
                    emit('research_strategy_result',{'strategy':k,'metrics':metrics,'research_only':True})
                for group in ('by_market','by_regime'):
                    for k,metrics in evaluation.get(group,{}).items():
                        emit('research_regime_result',{'group':group,'regime':k,'metrics':metrics,'research_only':True})
                for k,metrics in evaluation.get('ml',{}).get('cohorts',{}).items():
                    emit('research_ml_result',{'cohort':k,'overall':metrics.get('overall'),'research_only':True})
            full=out['expected_rows'] is not None and out['stored_rows']==out['expected_rows']
            if (out['collection_reason'] in PROVIDER_STOPS
                    or out['collection_reason'] in ('WINDOW_INTEGRITY_REJECTED','SHARED_QUOTA_RELEASE_UNVERIFIED','SHARED_QUOTA_DISPATCH_UNVERIFIED',
                        'ADJUSTED_HISTORY_OVERLAP_CONFLICT','OVERLAP_ANCHOR_MISSING')):
                emit('research_collection_stop',{'reason':out['collection_reason'],'orders_enabled':False})
                break
            if full or out['status'] in ('RESEARCH_COMPLETE','INSUFFICIENT_EVIDENCE') or out['new_rows']<=0:
                break
            if wait(5):break
    except Exception:
        emit('research_collection_stop',{'reason':'WORKER_FAILED','orders_enabled':False})
    finally:
        try:kis.session.close()
        except Exception:pass

def install_and_start(stop,snapshot,calendar):
    from ops_diagnostics import emit
    dsn=os.getenv('DATABASE_URL','').strip()
    if not dsn or not os.getenv('KIS_APP_KEY','').strip() or not os.getenv('KIS_APP_SECRET','').strip():
        emit('research_activation',{'status':'BLOCKED','reason':'CONFIGURATION_MISSING','orders_enabled':False});return
    ledger=SharedQuotaLedger(dsn)
    ledger.initialize(CAP)
    gate=SharedRequestGate(ledger,CAP,operational_policy=True)
    KIS_LOAD.install_shared_gate(gate)
    emit('research_activation',{'status':'ACTIVE','policy_cap':CAP,
        'policy_basis':'OWNER_EXCLUSIVE_APPKEY_CONSERVATIVE_LIVE_REST_CAP',
        'account_quota_verified':False,'scope':'INSTRUMENTED_REST_SHARED_LEDGER',
        'research_delay_seconds':DRAIN_SECONDS,'orders_enabled':False})
    threading.Thread(target=worker,args=(stop,snapshot,calendar,gate,dsn),
                     daemon=True,name='nova-history-research').start()
