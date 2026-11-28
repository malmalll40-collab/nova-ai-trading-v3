"""Read-only ledger integrity and archive metadata audit. No collection or adoption."""
import json, os, re
from datetime import datetime
from . import forward as f
import research_krx_archive as archive

def ledger(rows):
    prior_day=None;prior_candidates=set()
    prospective_days=signal_days=candidates=entries=exits=uncertain=corp=0
    for day,mode,observed_at,raw_candidates,raw_events in rows:
        if prior_day is not None and f.next_day(prior_day)!=day:raise ValueError('LEDGER_SESSION_GAP')
        cs=json.loads(raw_candidates);ev=json.loads(raw_events)
        if not isinstance(cs,list) or not isinstance(ev,list):raise ValueError('LEDGER_FORMAT')
        codes=[x['code'] for x in cs]
        if len(codes)>f.K or len(set(codes))!=len(codes) or any(not isinstance(x,str) or not re.fullmatch('[0-9A-Z]{6}',x) for x in codes):
            raise ValueError('LEDGER_CANDIDATES')
        if mode=='SEED':
            if cs or ev:raise ValueError('LEDGER_SEED_ACTIVITY')
        else:
            observed=datetime.fromisoformat(observed_at)
            if observed.utcoffset() is None or f.mode_for(day,observed)!=mode:raise ValueError('LEDGER_MODE_TIME')
            if mode!='PROSPECTIVE' and cs:raise ValueError('LEDGER_LATE_SIGNAL')
            if mode=='PROSPECTIVE':
                prospective_days+=1;signal_days+=bool(codes);candidates+=len(codes)
            opened=[e['code'] for e in ev if e['event']=='ENTRY_ASSUMED']
            if len(set(opened))!=len(opened) or not set(opened)<=prior_candidates:raise ValueError('LEDGER_ENTRY_WITHOUT_PRIOR_SIGNAL')
            entries+=len(opened);exits+=sum(e['event']=='EXIT_ASSUMED' for e in ev)
            uncertain+=sum(e['event'] in ('UNRESOLVED_MISSING','UNRESOLVED_PRICE','HALT_OR_NO_FILL') for e in ev)
            corp+=sum(e['event']=='SHARES_CHANGE_UNVERIFIED' for e in ev)
        prior_candidates=set(codes) if mode=='PROSPECTIVE' else set()
        prior_day=day
    return dict(ledger_integrity='CHECKED',prospective_signal_days=signal_days,
                prospective_ledger_sessions=prospective_days,prospective_candidates=candidates,
                entry_assumed_count=entries,exit_assumed_count=exits,
                uncertain_event_count=uncertain,corporate_review_event_count=corp,
                intake_timestamp_only=True,independent_commit_time_verified=False,
                model_prediction_verified=False,orders_enabled=False)

def archive_summary(dsn):
    # This is a read-only metadata check, not a fresh rehash of all 74 bodies.
    s=archive.read_status(dsn)
    return dict(status='VERIFIED_METADATA' if s['archive_verified'] else 'INCOMPLETE',
                archive_verified=bool(s['archive_verified']),stored_rows=s['stored_rows'],
                stored_chunks=s['stored_chunks'],verified_member_count=len(s['verified_members']),
                coverage_verified=bool(s['coverage_verified']),database_bytes=s['database_bytes'],
                local_guard_bytes=s['local_guard_bytes'],upload_enabled=os.getenv('NOVA_HISTORY_ARCHIVE_UPLOAD')=='1',
                fresh_full_body_rehash=False,training_ready=False,orders_enabled=False)
