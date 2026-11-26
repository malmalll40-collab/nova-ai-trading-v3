"""Fixed-cohort historical research pipeline, explicit invocation only.

Reads one consistent checkpoint snapshot. Reuses existing causal 5-Core rules,
v5.7.1 frozen chronological comparison and v5.7.3 walk-forward ML.
No production registration, scheduler, order path or live strategy adoption.
"""
from datetime import date
from math import isfinite
from research_history_checkpoint import DATASETS,TABLE,manifest,saved
from nova_v576_expanded_coverage import SYMBOLS,corrected_sessions
from nova_v574_data_audit import INDEX_CODES,session_dates
from research_history_status import START,END

HORIZON=5
COST_BPS=15.0
MIN_WARMUP=80
MIN_SIGNAL_DATES=100
BASE={'research_only':True,'strategy_adoption':'HOLD','orders_enabled':False,
      'execution_authorized':False,'independent_future_verified':False,
      'historical_calendar_fully_verified':False,'point_in_time_universe_verified':False}

def read_panel(dsn,sessions,*,connect=None):
    """All streams read in a single repeatable-read transaction. No writes."""
    days=tuple(sessions);dataset,_=manifest(days)
    base={**BASE,'dataset':dataset,'expected_rows':len(days)*len(SYMBOLS),
          'stored_rows':None,'complete_streams':0}
    if connect is None:
        import psycopg
        connect=psycopg.connect
    try:
        with connect(dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute('SELECT to_regclass(%s),to_regclass(%s)',(DATASETS,TABLE))
                tables=cur.fetchone()
                if tables==(None,None):
                    return {**base,'status':'NOT_INITIALIZED','stored_rows':0},None
                if None in tables:raise ValueError('INCOMPLETE_SCHEMA')
                cur.execute(f'SELECT 1 FROM {DATASETS} WHERE id=%s',(dataset,))
                if cur.fetchone() is None:
                    return {**base,'status':'NOT_INITIALIZED','stored_rows':0},None
                panel={s:saved(cur,'postgresql',dataset,s) for s in SYMBOLS}
                count=sum(map(len,panel.values()))
                cur.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE dataset=%s',(dataset,))
                if cur.fetchone()!=(count,):raise ValueError('UNEXPECTED_ROWS')
                complete=sum(set(v)==set(days) for v in panel.values())
                result={**base,'stored_rows':count,'complete_streams':complete}
                if complete!=len(SYMBOLS):
                    return {**result,'status':'PARTIAL'},None
                return {**result,'status':'COVERAGE_COMPLETE'},panel
    except Exception:
        return {**base,'status':'UNAVAILABLE','reason':'SNAPSHOT_INTEGRITY_FAILED'},None

def build_frames(panel,sessions,*,cost=COST_BPS):
    """Exact complete dates only; no forward filling, no current-day index data.

    Signals use the signal day's close. Outcome: next session open through
    fifth session close, estimated friction per side. Zero-volume assumed
    fills are not silently accepted; flag data for separate liquidity audit.
    """
    import pandas as pd
    from engine import feature_frame,WARMUP
    from nova_v541_core import point,indicators
    from nova_v573_ml import feature_vector
    days=tuple(sessions);manifest(days)
    if type(cost) not in (int,float) or not isfinite(cost) or not 0<=cost<=100:
        raise ValueError('INVALID_COST')
    if set(panel)!=set(SYMBOLS) or any(set(v)!=set(days) for v in panel.values()):
        raise ValueError('INCOMPLETE_PANEL')
    first=max(MIN_WARMUP,WARMUP+1)
    if len(days)-first-HORIZON<MIN_SIGNAL_DATES:raise ValueError('INSUFFICIENT_SESSIONS')
    index=pd.to_datetime(days)
    span=[d.isoformat() for d in days[first:]]
    markets={s:{d.isoformat():panel[s][d]['close'] for d in days} for s in INDEX_CODES}
    symbols=[s for s in SYMBOLS if s not in INDEX_CODES];frames=[]
    for symbol in symbols:
        raw=pd.DataFrame([panel[symbol][d] for d in days],index=index).rename(
            columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
        if (raw.Volume.iloc[first+1:]<=0).any():raise ValueError('ZERO_VOLUME_FILL_UNVERIFIED')
        f=feature_frame(raw,'스윙')
        if len(f)!=len(days) or not f.index.equals(index):raise ValueError('FEATURE_DATES_CHANGED')
        ind=indicators(f);market=markets['^KS11' if symbol.endswith('.KS') else '^KQ11']
        pts=[];fee=cost/10000
        for i in range(first,len(days)-HORIZON):
            p=point(f,i,market,ind)
            if p.get('status')!='READY':raise ValueError('POINT_UNAVAILABLE')
            x=feature_vector(f,i,market,ind)
            if x is None:raise ValueError('FEATURES_UNAVAILABLE')
            net=float(f.Close.iloc[i+HORIZON]*(1-fee)/(f.Open.iloc[i+1]*(1+fee))-1)
            if not isfinite(net) or net<=-1:raise ValueError('INVALID_OUTCOME')
            pts.append({'signals':{**p['signals'],'hybrid':p['hybrid_signal']},
                'candidates':p['candidates'],'route':p['v552_route'],
                'subregime':p['subregime'],'market_regime':p['market_regime'],
                'net':net,'ml_features':x,
                'ml_stock_risk_ok':next((a['pass'] is True for a in p['checks'] if a['key']=='risk'),False)})
        frames.append((span,f[['Open','Close']].iloc[first:].copy(),pts))
    return frames,symbols

def evaluate(panel,sessions,*,cost=COST_BPS):
    from nova_v570_accuracy import metrics,MARKETS,REGIMES
    from nova_v571_policy import historical as policy
    from nova_v573_ml import historical as ml
    frames,symbols=build_frames(panel,sessions,cost=cost)
    rows=[]
    for (dates,_,pts),symbol in zip(frames,symbols):
        for i,p in enumerate(pts):
            rows.append({'day':dates[i],'exit_day':dates[i+HORIZON],'symbol':symbol,
                'net_pct':100*p['net'],'market':p['market_regime'],'regime':p['subregime'],
                'signals':{**p['signals'],**p['candidates']}})
    keys=sorted(set.intersection(*(set(r['signals']) for r in rows)))
    def table(sub):return {k:metrics([r for r in sub if r['signals'][k]=='BUY'],cost) for k in keys}
    p=policy(frames,symbols,HORIZON,cost)
    m=ml(frames,symbols,HORIZON,cost)
    return {**BASE,'status':'RESEARCH_COMPLETE' if p['frozen_holdout']['status']=='READY' and m['status']=='READY' else 'INSUFFICIENT_EVIDENCE',
        'basis':'FIXED_POST_HOC_COHORT','cost_bps_per_side':cost,'holding_sessions':HORIZON,
        'symbols':len(symbols),'signal_dates':len(frames[0][2]),'opportunities':len(rows),
        'start':frames[0][0][0],'end':frames[0][0][-HORIZON-1],'outcome_end':frames[0][0][-1],
        'strategies':table(rows),'by_market':{g:table([r for r in rows if r['market']==g]) for g in MARKETS},
        'by_regime':{g:table([r for r in rows if r['regime']==g]) for g in REGIMES},
        'policy':p,'ml':m,'training_ready':False,
        'limitations':['Retrospective adjusted prices; fixed present-day universe; not point-in-time verified.',
                       'Nonoverlapping per-symbol opportunities, not shared-capital portfolio or actual fills.',
                       'Frozen historical holdout and walk-forward are not independent future outcomes.']}

def run(dsn,*,collection=None,allow_collection=False,calendar=session_dates,reader=read_panel,evaluator=evaluate):
    """One explicit bounded collection pass if independently authorized, then
    evaluate only a complete consistent snapshot. No implicit collector.
    The injected collector must enforce the existing execution/admission gates.
    """
    try:days,audit=corrected_sessions(calendar(START,END),START,END)
    except Exception:return {**BASE,'status':'BLOCKED','reason':'CALENDAR_UNAVAILABLE'}
    try:
        status,panel=reader(dsn,days)
        progress=None
        if status.get('status') in ('NOT_INITIALIZED','PARTIAL') and allow_collection is True and callable(collection):
            progress=collection()
            status,panel=reader(dsn,days)
        report={**BASE,'coverage':{k:status.get(k) for k in ('status','dataset','stored_rows','expected_rows','complete_streams')},
            'calendar_corrections':audit['corrections_applied']}
        if isinstance(progress,dict):
            report['collection']={k:progress[k] for k in ('status','reason','new_rows','logical_calls') if k in progress}
        if status.get('status')!='COVERAGE_COMPLETE' or panel is None:
            return {**report,'status':'BLOCKED','reason':'COMPLETE_SNAPSHOT_REQUIRED'}
        try:
            result=evaluator(panel,days)
        except ValueError as exc:
            allowed={'ZERO_VOLUME_FILL_UNVERIFIED','FEATURES_UNAVAILABLE','POINT_UNAVAILABLE',
                     'INSUFFICIENT_SESSIONS','INVALID_OUTCOME','INCOMPLETE_PANEL','FEATURE_DATES_CHANGED'}
            code=str(exc)
            return {**report,'status':'BLOCKED','reason':code if code in allowed else 'EVALUATION_FAILED'}
        except Exception:
            return {**report,'status':'BLOCKED','reason':'EVALUATION_FAILED'}
        return {**report,'status':result['status'],'evaluation':{**result,**BASE}}
    except Exception:
        return {**BASE,'status':'BLOCKED','reason':'PIPELINE_FAILED'}


def run_bounded(pooled_dsn,*,kis,telemetry,coordinator,session_mode_confirmed,
                now,calendar,allow_collection=False,historical_calendar=session_dates,
                max_calls=None,max_seconds=None):
    """Wire explicit collection to the existing quota and run-lock admission.
    No production caller is installed. A successful standalone DB probe is
    not a session-mode authorization. Missing configuration stays blocked.
    """
    from research_db_endpoint import direct_candidate
    from research_history_execution import execute
    dsn=direct_candidate(pooled_dsn)
    def collect():
        if dsn is None:
            return {'status':'BLOCKED','reason':'DIRECT_CANDIDATE_UNAVAILABLE','new_rows':0}
        if getattr(telemetry,'_shared_gate',None) is None:
            return {'status':'BLOCKED','reason':'SHARED_GATE_NOT_INSTALLED','new_rows':0}
        limits={k:v for k,v in [('max_calls',max_calls),('max_seconds',max_seconds)] if v is not None}
        return execute(dsn,kis=kis,telemetry=telemetry,coordinator=coordinator,
            session_mode_confirmed=session_mode_confirmed,now=now,calendar=calendar,
            historical_calendar=historical_calendar,**limits)
    return run(pooled_dsn,collection=collect,allow_collection=allow_collection,
               calendar=historical_calendar)
