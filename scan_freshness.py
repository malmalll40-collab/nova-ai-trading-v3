"""Session-aware validation of official as-of stock scan dates.

The official FSC T+1 snapshot is a historical research evaluation, NOT a
current tick. Never allow this module to relax ordinary live-price staleness.
"""
from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")


def assess_scan_sessions(raw_index, asof: str, *, now: datetime | None = None) -> dict:
    """Fail closed if a scan date is missing, old, future, or >1 real bar behind.

    A raw KIS bar after asof is discarded by the caller BEFORE any indicator
    calculation; this function only inspects the provider's raw date coverage.
    Weekends/holidays add NO synthetic sessions. Publication calendars remain
    unverified; the authoritative as-of date is supplied by FSC/KRX.
    """
    moment=(now or datetime.now(KST)).astimezone(KST)
    today=moment.date()
    try:
        cutoff=date.fromisoformat(asof[:4]+'-'+asof[4:6]+'-'+asof[6:8])
        if len(asof)!=8 or not asof.isdigit():raise ValueError('invalid date')
    except (ValueError,TypeError,AttributeError):
        return {'state':'INVALID_ASOF','stale':True,'session_gap':None,'latest_raw_bar':None,
                'reason':'공식 기준일 형식을 확인할 수 없습니다.'}
    try:
        dates=set()
        for item in raw_index:
            if hasattr(item,'date'):
                day=item.date()
            else:
                day=date.fromisoformat(str(item)[:10])
            if isinstance(day,date): dates.add(day)
    except (TypeError,ValueError,AttributeError):
        return {'state':'INVALID_BARS','stale':True,'session_gap':None,'latest_raw_bar':None,
                'reason':'시세 제공일 목록을 검증할 수 없습니다.'}
    latest=max(dates) if dates else None
    new_dates=sorted(day for day in dates if cutoff < day <= today)
    info={'session_gap':len(new_dates),'latest_raw_bar':latest.isoformat() if latest else None,
          'asof':cutoff.isoformat(),'scan_is_historical':True}
    if cutoff>today:
        return dict(info,state='FUTURE_ASOF',stale=True,reason='미래 공식 기준일은 평가할 수 없습니다.')
    if cutoff not in dates:
        return dict(info,state='ASOF_BAR_MISSING',stale=True,reason='공식 기준일과 일치하는 KIS 완료봉이 없습니다.')
    if any(day>today for day in dates):
        return dict(info,state='FUTURE_PROVIDER_BAR',stale=True,reason='KIS 응답에 미래 일봉 날짜가 포함되어 있습니다.')
    if (today-cutoff).days>6:
        return dict(info,state='OLD_SNAPSHOT',stale=True,reason='공식 일별 기준일이 6일보다 오래됐습니다.')
    if len(new_dates)>=2:
        return dict(info,state='PROVIDER_BEHIND_SESSIONS',stale=True,
                    reason=f'공식 기준일 이후 KIS 거래일 자료가 {len(new_dates)}개 더 있어 시세 확인이 필요합니다.')
    if len(new_dates)==1:
        return dict(info,state='T_PLUS_ONE_ALIGNED',stale=False,
                    reason='공식 기준일 다음 거래일 자료가 1개 존재합니다. 분석은 공식 기준일까지로 제한됩니다.')
    return dict(info,state='ASOF_ALIGNED',stale=False,
                reason='공식 기준일과 KIS의 최근 거래일 날짜가 일치합니다.')
