"""Prospective, durable market-scan evidence. No orders, no reconstructed calls.

Market scan observations retain their *actual* first observation timestamp. Candidate
outcomes use NEXT SESSION OPEN (strictly later than observation date), never the
price of an already-closed historical signal bar as a hypothetical executable fill.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from zoneinfo import ZoneInfo

import pandas as pd
from evidence_store import backend, connection

KST = ZoneInfo('Asia/Seoul')
HORIZONS = (1, 3, 5, 10)
RUNS = '''CREATE TABLE IF NOT EXISTS nova_auto_scan_runs (
 run_key TEXT PRIMARY KEY, observed_at TEXT NOT NULL, source_day TEXT NOT NULL,
 summary_json TEXT NOT NULL, sha256 TEXT NOT NULL)'''
CANDIDATES = '''CREATE TABLE IF NOT EXISTS nova_auto_candidates (
 id TEXT PRIMARY KEY, run_key TEXT NOT NULL, observed_at TEXT NOT NULL,
 symbol TEXT NOT NULL, lane TEXT NOT NULL, rank_no INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL, sha256 TEXT NOT NULL)'''
JOBS = '''CREATE TABLE IF NOT EXISTS nova_auto_jobs (
 job_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
 status TEXT NOT NULL, summary_json TEXT NOT NULL, sha256 TEXT NOT NULL)'''
OBS_OUTCOMES = '''CREATE TABLE IF NOT EXISTS nova_observed_outcomes (
 observation_id TEXT NOT NULL, horizon INTEGER NOT NULL, checked_at TEXT NOT NULL,
 outcome_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(observation_id,horizon))'''
OUTCOMES = '''CREATE TABLE IF NOT EXISTS nova_auto_outcomes (
 candidate_id TEXT NOT NULL, horizon INTEGER NOT NULL, checked_at TEXT NOT NULL,
 outcome_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(candidate_id,horizon))'''


def _pack(row):
    raw=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return raw,sha256(raw.encode()).hexdigest()


def _integrity(raw,digest):
    if sha256(raw.encode()).hexdigest()!=digest:
        raise ValueError('자동 관찰 기록 무결성 검증 실패')
    return json.loads(raw)


def _tables(db, kind):
    for schema in (RUNS,CANDIDATES,OUTCOMES,OBS_OUTCOMES,JOBS):
        if kind=='postgresql':
            with db.cursor() as cur: cur.execute(schema)
        else: db.execute(schema)


def _execute(db,kind,sql,params=(),*,fetch=False):
    if kind=='postgresql':
        with db.cursor() as cur:
            cur.execute(sql.replace('?', '%s'),params)
            return cur.fetchall() if fetch else cur.rowcount
    cur=db.execute(sql,params)
    return cur.fetchall() if fetch else cur.rowcount


def _valid_symbol(s):
    return isinstance(s,str) and bool(re.fullmatch(r'\d{6}\.(KS|KQ)',s))


def store_scan(result, observed_at=None, *, market_context=None):
    """First successful observation of each dated market scan wins; no retrospective overwrite."""
    if backend()=='disabled':return {'stored':False,'reason':'no_durable_db'}
    if not isinstance(result,dict) or result.get('status')!='complete':
        raise ValueError('완료된 시장 검색만 저장합니다')
    day=str(result.get('data_date') or '')
    if not re.fullmatch(r'\d{8}',day):raise ValueError('시장 스냅샷 날짜 누락')
    provider=result.get('data_source_id')
    market=result.get('market')
    depth=result.get('scan_limit')
    if provider not in ('fsc','krx') or market not in ('all','kospi','kosdaq') or depth not in (24,48,60):
        raise ValueError('알 수 없는 시장 검색 설정')
    instant=observed_at or datetime.now(timezone.utc)
    if instant.tzinfo is None:raise ValueError('관찰 시각 timezone 필수')
    observed=instant.astimezone(timezone.utc).isoformat(timespec='seconds')
    # Do not retain provider market data or keys: store derived NOVA classifications only.
    run_key=f'{day}|{provider}|{market}|{depth}'
    summary={'run_key':run_key,'observed_at':observed,'source_day':day,'provider':provider,
             'market':market,'depth':depth,'deep_target':int(result.get('deep_target') or 0),
             'deep_succeeded':int(result.get('deep_succeeded') or 0),
             'deep_failed':int(result.get('deep_failed') or 0),
             'snapshot_date_status':result.get('snapshot_date_status'),
             'recommended_count':len(result.get('recommendations') or []),
             'interest_count':len(result.get('interest_candidates') or []),
             'selection_scope':'최대 심층 대상 종목 중 확인된 후보만. 시장 전체 매수 신호가 아님',
             'method':'당시 관찰된 결과만 저장. 미래 시세는 이 기록에 포함하지 않음',
             'selection_version':str(result.get('selection_version') or '')[:60],
             'ranking_version':str(result.get('ranking_version') or '')[:60]}
    # Observed and immutable reference-index context. Never reconstruct old scans.
    from regime_research import BENCHMARKS, context_for_symbol
    summary['market_regimes']={k:(market_context.get(k) if isinstance(market_context,dict) else None) for k in BENCHMARKS}
    raw,dig=_pack(summary)
    candidates=[]
    for lane,key in (('STRICT_BUY','recommendations'),('INTEREST_ONLY','interest_candidates')):
        for item in result.get(key) or []:
            if not isinstance(item,dict) or not _valid_symbol(item.get('symbol')):continue
            ref=item.get('reference')
            try: ref=float(ref)
            except (ValueError,TypeError):continue
            if not isfinite(ref) or ref<=0:continue
            rank=item.get('rank')
            if not isinstance(rank,int) or rank<1:continue
            if str(item.get('asof')) != day:continue
            rec={'id':f'{run_key}|{lane}|{item["symbol"]}',
                 'run_key':run_key,'symbol':item['symbol'],'name':str(item.get('name') or '')[:64],
                 'lane':lane,'rank':rank,'reference':ref,'source_day':day,
                 'observed_at':observed,'data_source':'kis',
                 'basis':'STRICT_BUY' if lane=='STRICT_BUY' else 'INTEREST_ONLY',
                 'cost_bps_per_side':15,'slippage_bps_per_side':5,
                 'selection_scope':summary['selection_scope'],
                 'market_regime':context_for_symbol(item['symbol'],market_context)}
            why=item.get('reason')
            if isinstance(why,list):rec['reasons']=[str(x)[:160] for x in why[:6]]
            elif isinstance(why,str):rec['reasons']=[why[:300]]
            else:rec['reasons']=[]
            rec['selected_condition']=str(item.get('selected_condition') or '')[:80]
            rec['financial_gate']=str(item.get('financial_gate') or '')[:20]
            validation=item.get('validation')
            if isinstance(validation,dict):
                rec['validation']={k:validation.get(k) for k in
                     ('samples','hit_pct','mean_net_pct','wilson_lower_pct')
                     if isinstance(validation.get(k),(int,float)) and isfinite(validation.get(k))}
            for k in ('risk_reward','stop','target','passed_count'):
                v=item.get(k)
                if isinstance(v,(int,float)) and isfinite(v):rec[k]=v
            candidates.append(rec)
    with connection() as (db,kind):
        _tables(db,kind)
        # An existing run is never re-recorded under a different observed_at.
        inserted=_execute(db,kind,'''INSERT INTO nova_auto_scan_runs
             (run_key,observed_at,source_day,summary_json,sha256) VALUES (?,?,?,?,?)
             ON CONFLICT (run_key) DO NOTHING''', (run_key,observed,day,raw,dig))
        if inserted:
            for row in candidates:
                body,hashcode=_pack(row)
                _execute(db,kind,'''INSERT INTO nova_auto_candidates
                (id,run_key,observed_at,symbol,lane,rank_no,snapshot_json,sha256)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING''',
                (row['id'],run_key,observed,row['symbol'],row['lane'],row['rank'],body,hashcode))
        db.commit()
    return {'stored':bool(inserted),'run_key':run_key,'candidates':len(candidates) if inserted else 0,
            'strict':sum(x['lane']=='STRICT_BUY' for x in candidates) if inserted else 0,
            'interest':sum(x['lane']=='INTEREST_ONLY' for x in candidates) if inserted else 0}


def _rows(sql, params=()):
    with connection() as (db,kind):
        _tables(db,kind)
        return _execute(db,kind,sql,params,fetch=True)


def latest_scan_regimes(limit=3):
    """Read-only original scan-run context; previous unclassified runs stay unclassified."""
    if not 1<=limit<=5:raise ValueError('limit range')
    rows=_rows('SELECT summary_json,sha256 FROM nova_auto_scan_runs '
               'ORDER BY observed_at DESC LIMIT ?',(limit,))
    snapshots=[]
    for raw,digest in rows:
        value=_integrity(raw,digest)
        snapshots.append({'observed_at':value.get('observed_at'),
                          'source_day':value.get('source_day'),
                          'market_regimes':value.get('market_regimes'),
                          'regimes_recorded':(isinstance(value.get('market_regimes'),dict) and any(
                              isinstance(v,dict) for v in value['market_regimes'].values()))})
    return snapshots


def list_candidates(limit=500):
    if not 1<=limit<=1000:raise ValueError('limit range')
    data=_rows('''SELECT snapshot_json,sha256 FROM nova_auto_candidates
                  ORDER BY observed_at DESC,id DESC LIMIT ?''',(limit,))
    return [_integrity(*x) for x in data]


def list_completed():
    rows=_rows('''SELECT candidate_id,horizon,outcome_json,sha256 FROM nova_auto_outcomes''')
    return {(id_,int(h)):_integrity(raw,sha) for id_,h,raw,sha in rows}


def evaluate_candidate(rec, frame, now=None):
    """Strict prospective fill: future open after observation date, finished future closes only."""
    try:
        observed=datetime.fromisoformat(rec['observed_at'])
        if observed.tzinfo is None:raise ValueError('observed_at must be timezone aware')
        obs_day=observed.astimezone(KST).date()
        source_day=datetime.strptime(rec['source_day'],'%Y%m%d').date()
        if source_day>obs_day:raise ValueError('source date in future of observation')
        today=(now or datetime.now(KST)).astimezone(KST).date()
        anchor=[];future=[]
        if not isinstance(frame,pd.DataFrame) or not {'Open','Close'}<=set(frame.columns):
            raise ValueError('OHLC missing')
        for index,r in frame.iterrows():
            d=pd.Timestamp(index).date()
            try: o=float(r['Open']);c=float(r['Close'])
            except (TypeError,ValueError):continue
            if not all(isfinite(x) and x>0 for x in (o,c)):continue
            if d==source_day:anchor.append((d,c))
            if obs_day<d<today:future.append((d,o,c))
        # Source candle must match captured reference: otherwise corporate action/adjustment can mislead.
        if not anchor or abs(anchor[-1][1]/rec['reference']-1)>.05:
            return {'status':'unavailable','reason':'source_anchor_missing_or_adjusted'}
        future.sort()
        if not future:return {'status':'pending','reason':'no_completed_future_session'}
        result={}
        for h in HORIZONS:
            if len(future)<h:
                result[h]={'status':'pending'}
                continue
            entry=future[0];exitday=future[h-1]
            if entry[1]<=0 or exitday[2]<=0:continue
            slip=rec['slippage_bps_per_side']/10000
            fee=rec['cost_bps_per_side']/10000
            net=((exitday[2]*(1-slip)*(1-fee))/(entry[1]*(1+slip)*(1+fee))-1)*100
            raw=(exitday[2]/entry[1]-1)*100
            result[h]={'status':'complete','horizon':h,'entry_day':entry[0].isoformat(),
                       'entry_open':round(entry[1],5),'exit_day':exitday[0].isoformat(),
                       'exit_close':round(exitday[2],5),'asset_change_pct':round(raw,5),
                       'net_long_pct':round(net,5),
                       'direction_hit':raw>0 if rec['lane']=='STRICT_BUY' else None,
                       'note':'매수후보: 방향적중 및 가상 순변화; 관심후보: 가격 변화 연구만'}
        return result
    except Exception:
        return {'status':'unavailable','reason':'invalid_or_missing_ohlc'}


def store_completed(candidate_id, outcomes, checked_at=None):
    checked=(checked_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')
    count=0
    with connection() as (db,kind):
        _tables(db,kind)
        for h,res in outcomes.items():
            if h not in HORIZONS or res.get('status')!='complete':continue
            raw,dig=_pack(res)
            count+=_execute(db,kind,'''INSERT INTO nova_auto_outcomes
              (candidate_id,horizon,checked_at,outcome_json,sha256) VALUES (?,?,?,?,?)
              ON CONFLICT (candidate_id,horizon) DO NOTHING''',(candidate_id,h,checked,raw,dig))
        db.commit()
    return count


def evaluate_pending(loader, *, now=None, max_symbols=12, max_rows=450):
    """Bound daily market-data load. Retry failures on later cron runs; no bogus completion."""
    candidates=list_candidates(max_rows)
    complete=list_completed()
    needed=[x for x in reversed(candidates) if x.get('data_source')=='kis' and any((x['id'],h) not in complete for h in HORIZONS)]
    symbols=[]
    for r in needed:
        if r['symbol'] not in symbols: symbols.append(r['symbol'])
    # Rotate bounded API requests across symbols so old unresolvable records cannot
    # starve every new recommendation indefinitely.
    current=(now or datetime.now(KST)).astimezone(KST).date()
    shift=(current.toordinal()*max_symbols)%len(symbols) if symbols else 0
    chosen=(symbols[shift:]+symbols[:shift])[:max_symbols]
    frames={};errors=0;writes=0
    for symbol in chosen:
        try:frames[symbol]=loader(symbol,'kis')
        except Exception:errors+=1
    for rec in needed:
        if rec['symbol'] not in frames:continue
        evaluated=evaluate_candidate(rec,frames[rec['symbol']],now=now)
        if isinstance(evaluated,dict) and all(isinstance(k,int) for k in evaluated):
            writes+=store_completed(rec['id'],evaluated,checked_at=now)
        elif evaluated.get('status')=='unavailable':errors+=1
    return {'candidates_checked':sum(r['symbol'] in frames for r in needed),
            'symbols_checked':len(frames),'symbols_deferred':max(0,len(symbols)-len(chosen)),
            'outcomes_new':writes,'errors_or_missing':errors,
            'candidate_total_in_window':len(candidates),'bounded':len(candidates)==max_rows}



def evaluate_server_observations(loader, *, now=None, max_symbols=12, max_rows=200):
    """Persist future-completed outcomes for actual observed /api/analysis signals."""
    from evidence_store import list_snapshots
    from signal_journal import evaluate_record
    rows=list_snapshots(limit=max_rows)
    seen=_rows('SELECT observation_id,horizon FROM nova_observed_outcomes')
    completed=set((i,int(h)) for i,h in seen)
    needed=[r for r in reversed(rows) if r.get('source')=='kis' and any((r['id'],h) not in completed for h in HORIZONS)]
    symbols=[]
    for r in needed:
        pair=(r['symbol'],r['source'])
        if pair not in symbols:symbols.append(pair)
    today=(now or datetime.now(KST)).astimezone(KST).date()
    shift=(today.toordinal()*max_symbols)%len(symbols) if symbols else 0
    chosen=(symbols[shift:]+symbols[:shift])[:max_symbols]
    frames={};errors=0
    for pair in chosen:
        try:frames[pair]=loader(*pair)
        except Exception:errors+=1
    written=0
    with connection() as (db,kind):
        _tables(db,kind)
        for r in needed:
            pair=(r['symbol'],r['source'])
            if pair not in frames:continue
            try:
                observed_time=datetime.fromisoformat(r['observed_at']).astimezone(KST)
                observed=observed_time.date()
                from market_sessions import krx_session
                same_day_future=krx_session(observed_time).get('status')=='OPEN'
                data=evaluate_record(r,frames[pair])
                for h in HORIZONS:
                    if (r['id'],h) in completed:continue
                    value=data.get('horizons',{}).get(str(h),{})
                    if value.get('status')!='complete':continue
                    # Only completed closes strictly after actual observation day.
                    if (value.get('settlement_day','') < observed.isoformat() or
                        (value.get('settlement_day','')==observed.isoformat() and not same_day_future)):continue
                    raw,digest=_pack(value)
                    written+=_execute(db,kind,'INSERT INTO nova_observed_outcomes '
                       '(observation_id,horizon,checked_at,outcome_json,sha256) VALUES (?,?,?,?,?) '
                       'ON CONFLICT (observation_id,horizon) DO NOTHING',
                       (r['id'],h,datetime.now(timezone.utc).isoformat(timespec='seconds'),raw,digest))
            except Exception:errors+=1
        db.commit()
    return {'observations_in_window':len(rows),'symbols_checked':len(frames),
            'symbols_deferred':max(0,len(symbols)-len(chosen)),
            'outcomes_new':written,'errors_or_missing':errors}




def record_auto_attempt(*, started_at, completed_at, state, summary):
    if state not in ('complete','partial','failed'):raise ValueError('invalid automation state')
    if started_at.tzinfo is None or completed_at.tzinfo is None:
        raise ValueError('timezone-aware dates required')
    began=started_at.astimezone(timezone.utc).isoformat(timespec='microseconds')
    ended=completed_at.astimezone(timezone.utc).isoformat(timespec='seconds')
    payload={'started_at':began,'completed_at':ended,'status':state,'summary':summary}
    raw,digest=_pack(payload)
    with connection() as (db,kind):
        _tables(db,kind)
        _execute(db,kind,'INSERT INTO nova_auto_jobs (job_id,started_at,completed_at,status,summary_json,sha256) VALUES (?,?,?,?,?,?) ON CONFLICT(job_id) DO NOTHING',
                (began,began,ended,state,raw,digest))
        db.commit()


def _bucket(rank):
    return '1' if rank==1 else '2-3' if rank<=3 else '4-10' if rank<=10 else '11+'


def report(limit=500):
    rows=list_candidates(limit)
    results=list_completed()
    groups=defaultdict(lambda:{'candidates':0,'rank1':0,'completed':0,'hits':0,
                                'total_net':0.0,'total_raw':0.0,'last_observed':None})
    for r in rows:
        key=(r['lane'],_bucket(r['rank']))
        group=groups[key];group['candidates']+=1
        group['last_observed']=max(group['last_observed'] or '',r['observed_at'])
        if r['rank']==1:group['rank1']+=1
        for h in HORIZONS:
            x=results.get((r['id'],h))
            if not x:continue
            g=groups[(r['lane'],f'h{h}')];g['completed']+=1
            g['total_net']+=x['net_long_pct'];g['total_raw']+=x['asset_change_pct']
            if r['lane']=='STRICT_BUY' and x['direction_hit']:g['hits']+=1
            rb=groups[(r['lane'],_bucket(r['rank']),h)]
            rb['completed']+=1;rb['total_net']+=x['net_long_pct'];rb['total_raw']+=x['asset_change_pct']
            if r['lane']=='STRICT_BUY' and x['direction_hit']:rb['hits']+=1
    summary=[]
    for lane in ('STRICT_BUY','INTEREST_ONLY'):
        for group_name in ('1','2-3','4-10','11+'):
            row=groups[(lane,group_name)]
            horizons={}
            for h in HORIZONS:
                a=groups[(lane,group_name,h)]
                n=a['completed']
                horizons[str(h)]={'complete':n,'pending_or_unavailable':row['candidates']-n,
                   'direction_hit_pct':round(100*a['hits']/n,1) if lane=='STRICT_BUY' and n else None,
                   'average_asset_change_pct':round(a['total_raw']/n,3) if n else None,
                   'average_net_long_pct':round(a['total_net']/n,3) if lane=='STRICT_BUY' and n else None}
            summary.append({'lane':lane,'rank_group':group_name,'candidates':row['candidates'],
                            'horizons':horizons})
    trades=[];prior_exit=None;balance=10_000_000.0
    for r in sorted((x for x in rows if x['lane']=='STRICT_BUY'),key=lambda x:(x['observed_at'],x['id'])):
        x=results.get((r['id'],5))
        if not x:continue
        if prior_exit is not None and x['entry_day']<=prior_exit:continue
        balance*=1+x['net_long_pct']/100
        prior_exit=x['exit_day']
        trades.append({'symbol':r['symbol'],'entry_day':x['entry_day'],
                       'exit_day':x['exit_day'],'net_pct':x['net_long_pct'],
                       'equity_after':round(balance)})
    peak=10_000_000.;dd=0.
    for t in trades:
        peak=max(peak,t['equity_after'])
        dd=min(dd,(t['equity_after']/peak-1)*100)
    runs=_rows('''SELECT summary_json,sha256 FROM nova_auto_scan_runs
                   ORDER BY observed_at DESC LIMIT 30''')
    latest_runs=[_integrity(*x) for x in runs]
    return {'status':'observed' if rows else 'no_observations',
            'total_candidates':len(rows),'scan_runs_visible':len(latest_runs),
            'latest_runs':latest_runs[:5],'ranking':summary,
            'paper':{'initial_capital':10000000,'final_equity':round(balance),
                     'trades_count':len(trades),'cumulative_net_pct':round((balance/10000000-1)*100,3),
                     'realized_exit_only_mdd_pct':round(dd,3),'trades':trades[-15:]},
            'selection_scope':'실제 실행한 시장 검색의 심층 대상에서 발생한 엄격 매수 및 관심 후보만 기록',
            'note':'관심후보는 BUY가 아니므로 방향 적중률을 표시하지 않습니다. 매수 성과는 관찰 다음 거래일 시가→완료 종가의 가상 결과(편도 15bp 수수료+5bp 슬리피지). 같은 종목/신호·겹치는 구간은 독립 표본이 아님. 매도/손절/실제 주문 미반영. 청산 시점 MDD는 실제 최대 낙폭이 아님. 누락된 검색·시장 전체 비추천은 성과에 포함되지 않습니다.'}


def status():
    counts=_rows('''SELECT (SELECT COUNT(*) FROM nova_auto_scan_runs),
        (SELECT COUNT(*) FROM nova_auto_candidates),
        (SELECT COUNT(*) FROM nova_auto_outcomes),
        (SELECT COUNT(*) FROM nova_observed_outcomes)''')
    recent=_rows('''SELECT summary_json,sha256 FROM nova_auto_scan_runs
                  ORDER BY observed_at DESC LIMIT 1''')
    jobs=_rows('SELECT summary_json,sha256 FROM nova_auto_jobs ORDER BY started_at DESC LIMIT 20')
    attempts=[_integrity(*j) for j in jobs]
    return {'recent_auto_attempts':len(attempts),'last_auto_attempt':attempts[0] if attempts else None,
            'recent_attempt_failures':sum(a['status']!='complete' for a in attempts),
            'scan_runs':int(counts[0][0]),'candidates':int(counts[0][1]),
            'completed_horizons':int(counts[0][2]),'observed_signal_completed_horizons':int(counts[0][3]),
            'latest_scan':_integrity(*recent[0]) if recent else None}
