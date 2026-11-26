"""Research-only resumable group audits. No live scheduling, storage or order hooks."""
from datetime import datetime, date, timedelta, timezone
from hashlib import sha256
import json
import re
import time
from nova_v574_data_audit import KST, PILOT_DAYS, INDEX_CODES, MAX_INDEX_PAGES, MAX_PAGES, Budget, ProbeError, collect, session_dates
from nova_v576_expanded_coverage import corrected_sessions, CLOSURES
from nova_v560_unified import PRESET

VERSION='5.7.7'
GROUPS=(tuple(INDEX_CODES), *(tuple(PRESET[i:i+5]) for i in range(0,len(PRESET),5)))
SYMBOLS=tuple(s for group in GROUPS for s in group)
COOLDOWN=300
MAX_SECONDS=150
MAX_TELEMETRY_AGE=5
MIN_HEADROOM=3
PAGE_LIMITS=tuple(sum(MAX_INDEX_PAGES if s in INDEX_CODES else MAX_PAGES for s in g) for g in GROUPS)


def utc(dt):
    if not isinstance(dt,datetime) or dt.tzinfo is None:raise ValueError('TIMEZONE_REQUIRED')
    return dt.astimezone(timezone.utc)


def digest(obj):
    return sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def new_plan(now,calendar=session_dates):
    end=utc(now).astimezone(KST).date()-timedelta(days=1)
    start=end-timedelta(days=PILOT_DAYS-1)
    expected,audit=corrected_sessions(calendar(start,end),start,end)
    plan={'version':VERSION,'start':start.isoformat(),'end':end.isoformat(),
          'expected_dates':[d.isoformat() for d in expected],
          'calendar':audit,'groups':[list(g) for g in GROUPS],
          'price_basis':'KIS_NATIVE_ADJUSTED_HISTORY'}
    return {'plan':plan,'plan_id':digest(plan),'completed':[], 'not_before':None}


def validate_checkpoint(cp):
    # Check consistency, not authentication: this is internal trusted state only.
    if not isinstance(cp,dict) or set(cp)!={'plan','plan_id','completed','not_before'}:
        raise ValueError('INVALID_CHECKPOINT')
    p=cp['plan']
    if (not isinstance(p,dict) or cp['plan_id']!=digest(p) or p.get('version')!=VERSION
        or p.get('groups')!=[list(g) for g in GROUPS] or p.get('price_basis')!='KIS_NATIVE_ADJUSTED_HISTORY'):
        raise ValueError('PLAN_MISMATCH')
    start,end=map(date.fromisoformat,(p['start'],p['end']))
    dates=tuple(map(date.fromisoformat,p['expected_dates']))
    if (end-start).days!=PILOT_DAYS-1 or not dates or tuple(sorted(set(dates)))!=dates or any(not start<=d<=end or d in CLOSURES for d in dates):
        raise ValueError('CALENDAR_MISMATCH')
    done=cp['completed']
    if not isinstance(done,list) or len(done)>len(SYMBOLS):raise ValueError('INVALID_PROGRESS')
    for i,row in enumerate(done):
        if (not isinstance(row,dict) or row.get('symbol')!=SYMBOLS[i] or row.get('status')!='READY'
            or row.get('reason')!='WINDOW_REACHED' or row.get('observed_sessions')!=len(dates)
            or row.get('expected_sessions')!=len(dates) or row.get('missing_sessions')!=0
            or row.get('unexpected_sessions')!=0 or row.get('duplicate_rows')!=0
            or not re.fullmatch(r'[a-f0-9]{64}',str(row.get('sha256','')))):
            raise ValueError('INVALID_PROGRESS')
    if cp['not_before'] is not None:utc(datetime.fromisoformat(cp['not_before']))
    return start,end,dates


def permit(sample,now):
    """Fail closed on missing, stale or unverified shared provider telemetry.
    Headroom must include physical attempts and other consumers, not logical GETs.
    This function does not measure quota or guarantee provider-wide priority itself.
    """
    if not isinstance(sample,dict) or sample.get('verified') is not True:return 'TELEMETRY_UNVERIFIED'
    try:
        age=(utc(now)-utc(datetime.fromisoformat(sample['at']))).total_seconds()
    except (KeyError,TypeError,ValueError):return 'TELEMETRY_INVALID'
    if not 0<=age<=MAX_TELEMETRY_AGE:return 'TELEMETRY_STALE'
    for key in ('foreground_active','foreground_waiting','physical_headroom','recent_provider_errors'):
        if type(sample.get(key)) is not int or sample[key]<0:return 'TELEMETRY_INVALID'
    if sample['foreground_active'] or sample['foreground_waiting']:return 'FOREGROUND_BUSY'
    if sample['recent_provider_errors']:return 'PROVIDER_UNHEALTHY'
    if sample['physical_headroom']<MIN_HEADROOM:return 'QUOTA_HEADROOM_LOW'
    return None


class SplitBudget(Budget):
    def __init__(self,telemetry,now,**kw):
        super().__init__(**kw)
        self.telemetry=telemetry;self.now=now
    def take(self):
        try:reason=permit(self.telemetry(),self.now())
        except Exception:reason='TELEMETRY_UNAVAILABLE'
        if reason:raise ProbeError(reason)
        super().take()


def run_group(api,checkpoint,*,telemetry,now=lambda:datetime.now(timezone.utc),
              clock=time.monotonic,pause=time.sleep,progress=None):
    """At most one group per invocation; only fully audited streams are resumable.
    api is an injected read-only client. No automatic retry, scheduler or persistence.
    A partially collected symbol is restarted, never treated as completed.
    """
    cp=json.loads(json.dumps(checkpoint,allow_nan=False))
    start,end,expected=validate_checkpoint(cp)
    completed=cp['completed']; offset=len(completed)
    result={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD',
            'persistent_storage':False,'training_performed':False,
            'plan_id':cp['plan_id'],'requested_streams':len(SYMBOLS),
            'expected_sessions':len(expected),'group_count':len(GROUPS)}
    def report(status,reason,group=None,calls=0,attempted=None):
        return {**result,'status':status,'reason':reason,'group':group,
                'logical_calls':calls,'completed_streams':len(completed),
                'observed_rows':sum(s['observed_sessions'] for s in completed),
                'attempted':attempted or [],'checkpoint':cp}
    if offset==len(SYMBOLS):return report('READY',None)
    moment=utc(now())
    if cp['not_before'] and moment<utc(datetime.fromisoformat(cp['not_before'])):
        return report('PAUSED','COOLDOWN')
    index=next(i for i in range(len(GROUPS)) if offset<sum(len(g) for g in GROUPS[:i+1]))
    group=GROUPS[index]; begin=sum(len(g) for g in GROUPS[:index])
    budget=SplitBudget(telemetry,now,max_calls=PAGE_LIMITS[index],max_seconds=MAX_SECONDS,clock=clock)
    attempted=[]
    for s in group[offset-begin:]:
        stats,records=collect(api,s,start,end,expected,budget,
            max_pages=MAX_INDEX_PAGES if s in INDEX_CODES else MAX_PAGES,pause=lambda _:pause(2.0))
        stats['official_closure_conflicts']=[d.isoformat() for d in sorted(set(records)&set(CLOSURES))]
        if stats['official_closure_conflicts'] or stats['duplicate_rows']:
            stats['status']='PARTIAL'
        if s not in INDEX_CODES and (stats['turnover_available']!=stats['observed_sessions'] or stats['zero_volume_sessions']!=0):
            stats['status']='PARTIAL'
        attempted.append(stats)
        if progress:progress(dict(stats))
        if stats['status']!='READY':
            cp['not_before']=(utc(now())+timedelta(seconds=COOLDOWN)).isoformat()
            return report('PAUSED',stats['reason'] if stats['reason']!='WINDOW_REACHED' else 'QUALITY_GATE',index+1,budget.calls,attempted)
        completed.append(stats)
        # Pace symbol boundaries too, not just pagination.
        if s!=group[-1]:pause(2.0)
    cp['not_before']=(utc(now())+timedelta(seconds=COOLDOWN)).isoformat()
    return report('READY' if len(completed)==len(SYMBOLS) else 'GROUP_READY',None,index+1,budget.calls,attempted)
