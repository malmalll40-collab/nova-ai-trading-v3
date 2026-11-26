"""NOVA v4.2: KRX market-wide EOD screener. Read-only, no trade execution.

All KOSPI/KOSDAQ securities are inspected via a same-day KRX bulk snapshot;
only a small, explicitly reported candidate subset receives expensive NOVA
symbol-by-symbol analysis. A one-day KRX snapshot does NOT support historical
trend inference, and today's intraday prices are NEVER presented as completed EOD.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Callable

import requests

KST = ZoneInfo('Asia/Seoul')
URLS = {'kospi': 'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd',
        'kosdaq': 'https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd'}
MAX_DEEP = 48
DEEP_OPTIONS = (24, 48, 60)
SNAPSHOT_RANKING_VERSION = "2pool-eod-20261120"
MAX_DISPLAY = 20


class MarketScanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _number(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n=float(str(v).replace(',', '').strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _code(value):
    s = str(value or '').strip()
    if re.fullmatch(r'\d{6}', s):
        return s
    # KRX sometimes distributes an ISIN alongside the short code.
    if re.fullmatch(r'KR7\d{6}[0-9A-Z]{3}', s):
        return s[3:9]
    return None


def parse_rows(payload, market: str, date: str):
    """Strict response validation; an authentication error is not an empty market."""
    if not isinstance(payload, dict):
        raise MarketScanError('provider_format', 'KRX 데이터 형식을 확인할 수 없습니다.')
    rows = payload.get('OutBlock_1')
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise MarketScanError('provider_auth_or_format', 'KRX 서비스 접근 권한 또는 응답 형식을 확인하세요.')
    result=[]
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _code(row.get('ISU_CD'))
        name = str(row.get('ISU_NM') or '').strip()
        price, volume = _number(row.get('TDD_CLSPRC')), _number(row.get('ACC_TRDVOL'))
        amount, cap, change = (_number(row.get(k)) for k in ('ACC_TRDVAL','MKTCAP','FLUC_RT'))
        if not code or not name:
            continue
        source_date = str(row.get('BAS_DD') or date).replace('-', '')
        if source_date != date:
            continue
        # KRX provides the six-digit domestic short code, with an explicit suffix.
        result.append({'symbol': code + ('.KS' if market=='kospi' else '.KQ'),
                       'name':name,'market':market,'close':price or 0.,
                       'volume':int(volume or 0),'turnover_krw':int(amount or 0),
                       'market_cap_krw':int(cap or 0),'change_pct':round(change or 0.,3)})
    return result


class KRXClient:
    def __init__(self, api_key: str, session=None):
        if not api_key or len(api_key.strip()) < 12:
            raise MarketScanError('not_configured','Render에 KRX_API_KEY를 등록해 주세요.')
        self.key = api_key.strip()
        self.session = session or requests.Session()

    def fetch_day(self, date: str, market: str):
        if market not in URLS or not re.fullmatch(r'\d{8}',date):
            raise MarketScanError('invalid_input','KRX 시장 또는 조회일이 올바르지 않습니다.')
        try:
            resp=self.session.get(URLS[market],params={'basDd':date},headers={'AUTH_KEY':self.key,
                  'Accept':'application/json'},timeout=(5,14))
            try: payload=resp.json() if callable(getattr(resp,'json',None)) else None
            except (ValueError, TypeError): payload=None
            # KRX may report respCode 401 in a JSON body even when the HTTP
            # envelope is 200. Distinguish these cases without echoing secrets.
            info=classify_krx_response(resp.status_code, payload, market)
            if info['state'] != 'success':
                msg=info['detail']
                code='not_approved' if info['state']=='auth_denied' else info['error_code']
                if info['state']=='no_data':
                    return []
                raise MarketScanError(code, msg)
            return parse_rows(payload, market, date)
        except MarketScanError:
            raise
        except requests.RequestException:
            # Never echo URLs containing credentials or raw response bodies.
            raise MarketScanError('provider_network','KRX 서버 통신에 실패했습니다.') from None


def completed_dates(now: datetime):
    """Only EOD records plausibly published by the exchange."""
    now=now.astimezone(KST)
    date = now.date()
    # Allow time for exchange-side daily data publication after Korean close.
    if (now.hour,now.minute)<(18,30):
        date -= timedelta(days=1)
    for _ in range(10):
        if date.weekday()<5:
            yield date.strftime('%Y%m%d')
        date -= timedelta(days=1)


def screen(rows: list[dict]):
    """Cheap *one-day* liquidity screen, not an EMA-based recommendation."""
    seen=set()
    selected=[]
    excluded={'duplicate':0,'invalid_or_halted':0,'illiquid':0,'price_or_cap':0,'extreme_move':0}
    for item in rows:
        sym=item['symbol']
        if sym in seen:
            excluded['duplicate']+=1
            continue
        seen.add(sym)
        if item['close'] <= 0 or item.get('volume',0) <= 0 or item['market_cap_krw'] <= 0:
            excluded['invalid_or_halted']+=1;continue
        if item['close'] < 1000 or item['market_cap_krw'] < 100_000_000_000:
            excluded['price_or_cap']+=1;continue
        if item['turnover_krw']<3_000_000_000:
            excluded['illiquid']+=1;continue
        if item['change_pct'] < -5 or item['change_pct'] > 12:
            excluded['extreme_move']+=1;continue
        # Ranking is transparent liquidity+moderate price action, not a probability.
        turnover = min(50., max(0., math.log10(max(1,item['turnover_krw'])/1e9)*20))
        moderate = max(0.,20-abs(item['change_pct']-2.0)*3)
        size = min(15., math.log10(max(1,item['market_cap_krw'])/1e11+1)*12)
        item=dict(item,screen_score=round(turnover+moderate+size,1))
        selected.append(item)
    selected.sort(key=lambda r:(r['screen_score'],r['turnover_krw'],r['symbol']), reverse=True)
    return selected,excluded




def select_deep_candidates(selected: list[dict], depth: int = MAX_DEEP) -> list[dict]:
    """A bounded, transparent two-lane *one-day* breadth shortlist.

    Half by established EOD liquidity rank, half by daily turnover relative
    to market capitalization, favoring non-extreme price movement. Neither
    lane claims to measure multi-day EMA/relative volume from one-day data.
    No BUY rules or suitability probabilities are changed here.
    """
    if type(depth) is not int or depth not in DEEP_OPTIONS:
        raise ValueError('심층 분석 수는 24/48/60 중에서 선택하세요.')
    main=selected[:(depth+1)//2]
    seen={v['symbol'] for v in main}
    def opportunity(x):
        ratio=x['turnover_krw']/max(1.,x['market_cap_krw'])
        # Ratios are capped; turnover is NOT equated to confirmed buying.
        activity=min(45.,max(0.,ratio)*1200.)
        calm=max(0.,30-abs(x['change_pct']-2.0)*5)
        liquidity=min(15.,max(0.,math.log10(max(x['turnover_krw'],1)/3e9))*8)
        return round(activity+calm+liquidity,2)
    main=[dict(x,selection_lane='유동성',snapshot_activity_score=opportunity(x)) for x in main]
    remaining=sorted((x for x in selected if x['symbol'] not in seen),
        key=lambda x:(opportunity(x),x['screen_score'],x['symbol']),reverse=True)
    extra=[dict(x,selection_lane='거래활동',snapshot_activity_score=opportunity(x))
           for x in remaining[:max(0,depth-len(main))]]
    return main+extra


def summarize_auxiliary_flow(payload: dict | None, asof: str) -> dict:
    """Observed read-only shares, never research/backtest point-in-time truth.

    This auxiliary indicator is attached only to INTEREST cards and NEVER to
    evaluate_candidate or the core rank. Missing/mismatched days fail closed.
    """
    unavailable={'status':'NO_VERIFIED_FLOW','label':'수급 미확인',
        'not_actionable':True,'used_for_buy':False,'not_backtested':True}
    if not isinstance(payload,dict) or payload.get('unit')!='shares' or not re.fullmatch(r'\d{8}',str(asof)):
        return dict(unavailable)
    records=payload.get('daily')
    if not isinstance(records,list):return dict(unavailable)
    clean={}
    for rec in records[:100]:
        if not isinstance(rec,dict):continue
        day=str(rec.get('date') or '')
        if not re.fullmatch(r'\d{8}',day) or day>asof:continue
        if day in clean: return dict(unavailable,reason='거래일 중복')
        foreign=rec.get('foreign');institution=rec.get('institution')
        if any(v is not None and (type(v) is not int or abs(v)>10**13) for v in (foreign,institution)):
            return dict(unavailable,reason='일별 수량 단위·형식 확인 불가')
        clean[day]=(foreign,institution)
    days=sorted(clean,reverse=True)
    if not days or days[0]!=asof:return dict(unavailable,reason='공식 기준일과 수급 날짜 불일치')
    sample=days[:5]
    foreign=[clean[d][0] for d in sample];institution=[clean[d][1] for d in sample]
    a=sum(foreign) if len(foreign)==5 and all(x is not None for x in foreign) else None
    b=sum(institution) if len(institution)==5 and all(x is not None for x in institution) else None
    if a is None or b is None:
        return dict(unavailable,reason='외국인·기관 동시 확인 5거래일 부족',
                    foreign_days=sum(x is not None for x in foreign),
                    institution_days=sum(x is not None for x in institution))
    label=('동반 순매수' if a>0 and b>0 else '동반 순매도' if a<0 and b<0 else '수급 혼조')
    return {'status':'OBSERVED_SHARES_5D','label':label,'unit':'shares',
        'foreign_5d':a,'institution_5d':b,'asof':asof,
        'source':str(payload.get('source') or '기존 조회 수급 캐시')[:90],
        'not_actionable':True,'used_for_buy':False,'not_backtested':True,
        'warning':'현재 관측된 수급이며 과거 시점 사전확인·독립 교차검증을 뜻하지 않습니다.'}


# Stable codes make rejected cases auditable even when Korean text changes.
REJECTION_LABELS={
    'DEMO':'가상 데이터',
    'PRICE_STALE':'실제 시세 자료 지연',
    'SNAPSHOT_DATE_LAG':'공식 스냅샷 기준일 확인 필요',
    'ASOF_MISMATCH':'공식 기준일·기술적 완료봉 날짜 불일치',
    'FINANCIAL_RISK':'재무 위험 필터 미통과',
    'FINANCIAL_UNVERIFIED':'재무정보 미확인',
    'NOT_SWING':'스윙 분석 결과가 아님',
    'BAR_NOT_CURRENT':'기준일 완료봉 신호 미확인',
    'TECHNICAL_NOT_BUY':'안정형 기술 매수 조건 미충족',
    'TECHNICAL_SCORE':'안정형 기술 세부 조건 미충족',
    'VALIDATION_UNVERIFIED':'개별 매수 조건의 최근 독립 검증 부족',
    'VALIDATION_WEAK':'검증 표본 또는 순수익 부족',
    'POLICY_NOT_BUY':'최종 정책 BUY 승인 조건 미충족',
    'RISK_REWARD':'목표 손익비·손절/목표가 미확인',
    'PRICE_STRUCTURE':'손절·기준가·목표가 정합성 실패',
    'DEEP_ANALYSIS_ERROR':'심층 시세·재무 조회 실패',
}


def classify_rejection(detail: dict, asof: str):
    """Return (stable code, exact primary reason); never silently merge stale/HOLD."""
    if not isinstance(detail,dict):
        return 'DEEP_ANALYSIS_ERROR', '심층 분석 결과가 올바르지 않습니다.'
    if detail.get('is_demo'):
        return 'DEMO', '가상 데이터는 매수 후보에 사용할 수 없습니다.'
    scan=detail.get('scan_freshness') or {}
    if detail.get('is_stale'):
        msg=str(scan.get('reason') or '시세 공급일 또는 완료봉이 오래됐습니다.')
        return 'PRICE_STALE', '실제 시세 지연: '+msg[:150]
    expected=detail.get('scan_expected_asof')
    if expected and expected!=asof:
        return 'SNAPSHOT_DATE_LAG', f'공식 제공 예정 기준일 {expected}보다 수신한 {asof} 자료가 오래됐습니다. 휴장일/공급 지연 확인 필요'
    last=str(detail.get('last_completed_bar') or '')[:10].replace('-','')
    if last!=asof:
        return 'ASOF_MISMATCH', 'KIS 완료봉과 공식 스냅샷 기준일이 일치하지 않습니다.'
    if detail.get('mode')!='스윙':
        return 'NOT_SWING', '스윙 분석 기준이 아닙니다.'
    fgate=detail.get('financial_gate')
    if fgate in ('FAIL','BLOCK'):
        return 'FINANCIAL_RISK', '재무 위험 검증에서 통과하지 못했습니다.'
    if fgate!='PASS':
        return 'FINANCIAL_UNVERIFIED', 'OpenDART 재무 위험 통과 여부가 확인되지 않았습니다.'
    if detail.get('signal_is_current') is not True:
        return 'BAR_NOT_CURRENT', '공식 기준일에 유효한 완료봉 매수신호가 아닙니다.'
    quality=detail.get('quality_gate') or {}
    if quality.get('strict_signal')!='BUY':
        return 'TECHNICAL_NOT_BUY', '안정형 매수신호 자체가 BUY가 아닙니다.'
    if (quality.get('passed_count') or 0)<4:
        return 'TECHNICAL_SCORE', '안정형 기술 조건이 4/5 미만입니다.'
    stats=detail.get('signal_performance') or {}
    if not (stats.get('selected_validation_positive') and stats.get('selected_active_now')):
        return 'VALIDATION_UNVERIFIED', '개별 매수 조건의 최근 독립 검증이 부족합니다.'
    role=next((x for x in stats.get('roles',[]) if x.get('id')==stats.get('selected_id')),None)
    val=(role or {}).get('validation') or {}
    if (val.get('samples') or 0)<(stats.get('min_validation') or 15) or (_number(val.get('mean_net_pct')) or 0)<=0:
        return 'VALIDATION_WEAK', '개별 검증 표본 수 또는 비용 차감 순수익이 부족합니다.'
    if detail.get('final_actionable_signal')!='BUY':
        return 'POLICY_NOT_BUY', '기존 최종 위험관리 정책에서 BUY를 승인하지 않았습니다.'
    rr=_number(detail.get('long_risk_reward'))
    stop,target=(_number(detail.get(k)) for k in ('long_stop_reference','long_target_reference'))
    price=_number(detail.get('reference'))
    if rr is None or rr<1.3 or stop is None or target is None or price is None:
        return 'RISK_REWARD', '손익비 1.3 또는 참고 손절·목표 가격이 확인되지 않았습니다.'
    if not (0<stop<price<target):
        return 'PRICE_STRUCTURE', '참고 손절가·기준가·목표가의 가격 순서가 잘못됐습니다.'
    return None,None


def evaluate_candidate(snapshot: dict, detail: dict, asof: str):
    """Strict BUY criteria unchanged; classify WHY each non-BUY was excluded."""
    code,reason=classify_rejection(detail,asof)
    if code:
        return None,reason
    stats=detail['signal_performance']
    quality=detail['quality_gate']
    role=next(x for x in stats['roles'] if x.get('id')==stats.get('selected_id'))
    val=role['validation']
    rr=_number(detail['long_risk_reward'])
    price=_number(detail['reference'])
    stop=_number(detail['long_stop_reference'])
    target=_number(detail['long_target_reference'])
    reasons=[f'안정형 기술 조건 {quality["passed_count"]}/5 충족',
             'OpenDART 재무 위험 필터 통과',
             f'{stats.get("selected_name") or "매수 조건"}: 최근 별도 검증 {val["samples"]}건, 적중 {val.get("hit_pct")}%, 평균 순수익 {val.get("mean_net_pct")}%',
             f'손절·목표 참고 손익비 약 {rr:.1f}:1']
    return ({'symbol':snapshot['symbol'],'name':snapshot['name'],'market':snapshot['market'],
        'asof':asof,'close_krx':snapshot['close'],'change_pct':snapshot['change_pct'],
        'turnover_krw':snapshot['turnover_krw'],'screen_score':snapshot['screen_score'],
        'reference':round(price,3),'stop':round(stop,3),'target':round(target,3),
        'risk_reward':round(rr,2),'selected_condition':stats.get('selected_name'),
        'validation':{'samples':val['samples'],'hit_pct':val.get('hit_pct'),
           'mean_net_pct':val.get('mean_net_pct'),'wilson_lower_pct':val.get('wilson_lower_pct')},
        'reason':reasons,'financial_gate':'PASS','signal':'BUY',
        'asof_evaluation_only':True,'not_realtime_order_signal':True,
        'last_completed_bar':detail['last_completed_bar'],'provider':detail.get('provider','unknown'),
        'not_probability':True},None)


def interest_candidate(snapshot: dict, detail: dict, asof: str, why: str | None):
    """Strictly research-only: never upgrade a HOLD to BUY and never imply probability."""
    if not isinstance(detail,dict) or detail.get('is_demo') or detail.get('is_stale'):
        return None
    if detail.get('financial_gate') == 'FAIL' or detail.get('mode')!='스윙':
        return None
    bar=str(detail.get('last_completed_bar') or '')[:10].replace('-','')
    if bar!=asof:
        return None  # Never mix different observation dates.
    q=detail.get('quality_gate') or {}
    checks=q.get('checks') or []
    passed={x.get('key'):x.get('passed') is True for x in checks if isinstance(x,dict)}
    if not (passed.get('trend') and passed.get('viable')) or int(q.get('passed_count') or 0)<3:
        return None
    reference=_number(detail.get('reference'))
    rr=_number(detail.get('long_risk_reward'))
    if not reference or reference<=0 or rr is None or rr<1:
        return None
    return {'symbol':snapshot['symbol'],'name':snapshot['name'],'market':snapshot['market'],
        'asof':asof,'reference':round(reference,3),'passed_count':int(q['passed_count']),
        'checks':checks,'risk_reward':round(rr,2),'financial_gate':detail.get('financial_gate') or 'UNKNOWN',
        'last_completed_bar':detail.get('last_completed_bar'),'reason':why or '엄격 매수 미승인',
        'classification':'INTEREST_ONLY','not_probability':True,'not_actionable':True,
        'data_source':'NOVA 기술지표 (일별 시장 스냅샷과 기준일 일치)',
        'disclaimer':'관심 후보이며 BUY가 아니고 수익 가능성·매수 적합성을 보증하지 않습니다.'}


def strict_candidate_rank_key(candidate: dict):
    """Observed validation strength, not a calibrated forecast or a BUY rule.

    Wilson confidence lower bound is favored over raw hit rate; tie-breaks
    prefer mean net return, sample count, then reference risk/reward. Missing
    validation ranks last rather than creating a fictitious score.
    """
    val=candidate.get('validation') or {}
    def desc(v):
        n=_number(v)
        return -n if n is not None else float('inf')
    return (desc(val.get('wilson_lower_pct')),
            desc(val.get('mean_net_pct')),
            desc(val.get('samples')),
            desc(candidate.get('risk_reward')),
            str(candidate.get('symbol','')))


def interest_candidate_rank_key(candidate: dict):
    """Unapproved observation priority; never a BUY probability or signal."""
    q=candidate.get('technical_review') or {}
    def ok(key): return int(q.get(key) is True)
    rr=_number(candidate.get('risk_reward'))
    return (-int(candidate.get('financial_gate')=='PASS'),
            -int(candidate.get('passed_count') or 0),
            -ok('participation'),-ok('momentum'),
            -(rr if rr is not None else -1.),str(candidate.get('symbol','')))


def assess_scan_outcome(result:dict)->dict:
    """Explain strict zero without turning incomplete observations into a market verdict."""
    if result.get('scan_profile')=='pattern':
        return {'status':'PATTERN_ONLY'}
    target=result['deep_target']; success=result['deep_succeeded']; failed=result['deep_failed']
    strict=result['recommended_count']; codes=result['rejection_codes']
    uncertain={'DEMO','PRICE_STALE','SNAPSHOT_DATE_LAG','ASOF_MISMATCH','NOT_SWING',
               'FINANCIAL_UNVERIFIED','BAR_NOT_CURRENT','DEEP_ANALYSIS_ERROR','UNKNOWN'}
    data_limited=sum(n for k,n in codes.items() if k in uncertain)
    # First rejection only; separate data uncertainty from model eligibility, not all gates.
    consistent=success+failed==target and strict+sum(codes.values())==target
    if not consistent: status='COUNT_MISMATCH'
    elif not target: status='NO_SCREENED_CANDIDATES'
    elif failed or data_limited or result.get('snapshot_date_status')=='OLDER_THAN_EXPECTED':
        status='DATA_LIMITED'
    elif strict: status='STRICT_CANDIDATES'
    else: status='NO_STRICT_CANDIDATES'
    return {'status':status,'deep_target':target,'deep_succeeded':success,'deep_failed':failed,
            'strict_count':strict,'interest_count':result['interest_count'],
            'data_limited_first_rejections':data_limited,
            'validation_first_rejections':sum(codes.get(k,0) for k in ('VALIDATION_UNVERIFIED','VALIDATION_WEAK')),
            'count_consistent':consistent,'scope':'BOUNDED_DEEP_SCAN','first_rejection_only':True}


def run_market_scan(*, market: str, api_key: str, analyze: Callable[[str],dict],
                    analyze_asof: Callable[[str,str],dict]|None=None,
                    progress: Callable[[dict],None]|None=None, now: datetime|None=None,
                    client:KRXClient|None=None, depth: int = MAX_DEEP,
                    auxiliary_flow:Callable[[str],dict|None]|None=None, pattern_only:bool=False,
                    check_cancel:Callable[[],None]|None=None):
    """Run complete KRX universe snapshot and explicit limited deep investigation."""
    if type(pattern_only) is not bool or (pattern_only and analyze_asof is None):
        raise MarketScanError('invalid_profile','신호패턴 검색은 기준일 분석이 필요합니다.')
    if type(depth) is not int or depth not in DEEP_OPTIONS:
        raise MarketScanError('invalid_depth','심층 분석 수는 24/48/60 중에서 선택하세요.')
    if market not in ('all','kospi','kosdaq'):
        raise MarketScanError('invalid_input','시장 선택은 전체·코스피·코스닥 중 하나여야 합니다.')
    checkpoint=check_cancel or (lambda: None)
    checkpoint()
    client=client or KRXClient(api_key)
    markets=['kospi','kosdaq'] if market=='all' else [market]
    moment=now or datetime.now(tz=KST)
    asof,whole=None,None
    last_issue='';calls=0
    dates = client.candidate_dates(moment) if callable(getattr(client, 'candidate_dates', None)) else completed_dates(moment)
    dates=list(dates)[:10]
    expected_asof=dates[0] if dates else None
    for date in dates:
        try:
            per=[]
            for m in markets:
                checkpoint()
                per.append(client.fetch_day(date,m))
                checkpoint()
            calls+=len(per)
        except MarketScanError as exc:
            # Retry only a genuinely unpublished date. Never retry authentication,
            # pagination loss, provider rejection, or unexpected response errors.
            if exc.code != 'no_published_data':
                raise
            last_issue = str(exc)
            continue
        if all(per):
            asof=date;whole=[x for batch in per for x in batch];break
    if not whole or not asof:
        raise MarketScanError('no_published_data',last_issue or '요청한 시장의 같은 날짜 공식 일별 자료를 찾지 못했습니다.')
    # Do not misrepresent an old past day as today's new recommendations.
    if (moment.astimezone(KST).date() - datetime.strptime(asof,'%Y%m%d').date()).days > 6:
        raise MarketScanError('stale_snapshot','KRX 마지막 공식 일별 자료가 오래됐습니다.')
    selected, exclusions=screen(whole)
    top=select_deep_candidates(selected,depth)
    fsc = getattr(client, 'source_label', '') == '금융위원회 주식시세정보 (T+1)'
    label = getattr(client, 'source_label', 'KRX 일별매매정보')
    result={'status':'complete','data_source':label + ' + KIS / NOVA / OpenDART',
        'data_date':asof,'expected_source_date':expected_asof,
        'snapshot_date_status':'ON_SCHEDULE' if expected_asof==asof else 'OLDER_THAN_EXPECTED',
        'scan_freshness_method':'공식 기준일과 KIS 실제 거래일 목록 비교(72시간 자동 제외하지 않음)',
        'market':market,'markets_scanned':markets,
        'data_source_id':'fsc' if fsc else 'krx','data_lag':'T+1 영업일 오후 1시 이후' if fsc else '장 마감 이후',
        'total_market_rows':len(whole),'screen_passed':len(selected),'deep_target':len(top),
        'deep_succeeded':0,'deep_failed':0,'scan_cache_counts':{'HIT':0,'MISS':0,'BYPASS':0},'recommended_count':0,'recommendations':[],
        'interest_candidates':[],'interest_count':0,'interest_only':False,
        'rejection_summary':{},'coverage_scope':f'1차 통과 종목 중 최대 {depth}개 · 유동성/거래활동 두 경로 심층 분석',
        'rejected':[],'rejection_codes':{},'exclusion_counts':exclusions,'scan_limit':depth,'marketwide':True,
        'selection_version':SNAPSHOT_RANKING_VERSION,'selection_lanes':{},'technical_assessment_source':'각 종목의 완료 일봉/20일 거래량',
        'realtime':False,'method':label + f' 전체 일별 스냅샷 1차(거래대금/시총 활동률 분산) → 최대 {depth}개 후보 NOVA 스윙/재무/과거별도검증 심층 선별',
        'risk_note':'매수 후보는 미래 수익 확률이 아니며 투자 주문을 실행하지 않습니다. 1차 선별을 통과한 전체 종목을 심층 분석한 것은 아닙니다.',
        'persistence':'Render 프로세스 메모리에만 저장; 서버 재시작 시 결과 소멸'}
    for row in top:
        lane=row['selection_lane'];result['selection_lanes'][lane]=result['selection_lanes'].get(lane,0)+1
    from nova_v584_pattern_scan import candidate as pattern_candidate, finish as pattern_finish
    pattern_rows=[];pattern_exclusions={}
    for i,row in enumerate(top):
        checkpoint()
        if progress: progress({'phase':'deep','done':i,'total':len(top),
            'message':f'전체 {len(whole)}종목 중 1차 후보 {len(selected)}종목, 심층 분석 {i}/{len(top)}'})
        try:
            detail=(analyze_asof(row['symbol'],asof) if analyze_asof else analyze(row['symbol']))
            if isinstance(detail,dict):
                detail=dict(detail,scan_expected_asof=expected_asof)
                state=((detail.get('scan_freshness') or {}).get('scan_cache') or {}).get('state')
                if state in result['scan_cache_counts']:result['scan_cache_counts'][state]+=1
            result['deep_succeeded']+=1
            # Failures in the research overlay cannot change existing candidates.
            try:
                p,reason=pattern_candidate(row,detail,asof,expected_asof)
                if p is not None:pattern_rows.append(p)
                else:pattern_exclusions[reason]=pattern_exclusions.get(reason,0)+1
            except Exception:
                pattern_exclusions['PATTERN_ERROR']=pattern_exclusions.get('PATTERN_ERROR',0)+1

            if pattern_only:
                continue
            rec,why=evaluate_candidate(row,detail,asof)
            if rec:
                if fsc:
                    # FSC source has non-redistribution restrictions. Serve only
                    # derived NOVA signals, not raw FSC market quotes/turnover.
                    rec['close_krx'] = None
                    rec['turnover_krw'] = None
                    rec['change_pct'] = None
                    rec['price_data_source'] = 'NOVA/KIS 기술적 분석'
                result['recommendations'].append(rec)
            else:
                reason_code,_=classify_rejection(detail,asof)
                result['rejected'].append({'symbol':row['symbol'],'name':row['name'],
                                           'reason_code':reason_code or 'POLICY_NOT_BUY','reason':why,
                                           'last_completed_bar':detail.get('last_completed_bar') if isinstance(detail,dict) else None,
                                           'scan_freshness':detail.get('scan_freshness') if isinstance(detail,dict) else None})
                interest=interest_candidate(row,detail,asof,why)
                if interest:
                    interest['selection_lane']=row.get('selection_lane')
                    interest['snapshot_activity_score']=row.get('snapshot_activity_score')
                    interest['technical_review']={
                        c['key']: c.get('passed') for c in (detail.get('quality_gate',{}).get('checks') or [])
                        if isinstance(c,dict) and c.get('key') in ('trend','momentum','participation','extension','viable')}

                if interest:
                    result['interest_candidates'].append(interest)
        except Exception as exc:
            result['deep_failed']+=1
            code='ASOF_MISMATCH' if str(exc)=='SCAN_ASOF_BAR_MISSING' else 'DEEP_ANALYSIS_ERROR'
            result['rejected'].append({'symbol':row['symbol'],'name':row['name'],
                'reason_code':code,
                'reason':'공식 기준일 봉이 없습니다.' if code=='ASOF_MISMATCH' else '심층 시세·재무 조회에 실패했습니다 (인증·공급원·요청 제한 가능).'})
    checkpoint()
    # Rank AFTER every deep candidate was evaluated. A discovery-order list
    # would mislead users and truncating before ranking could hide better cards.
    result['recommendations'].sort(key=strict_candidate_rank_key)
    for position,rec in enumerate(result['recommendations'],start=1):
        rec['rank']=position
        rec['rank_basis']='최근 별도 검증 Wilson 하한 → 평균 순수익 → 검증 표본 → 손익비'
    result['recommended_count']=len(result['recommendations'])
    result['interest_candidates'].sort(key=interest_candidate_rank_key)
    result['interest_eligible_total']=len(result['interest_candidates'])
    result['interest_candidates']=result['interest_candidates'][:MAX_DISPLAY]
    for position,interest in enumerate(result['interest_candidates'],start=1):
        interest['rank']=position
        interest['rank_basis']='재무 PASS → 기술 조건 → 거래량·모멘텀 → 참고 손익비'
    result['ranking_version']='validation-vs-observation-v1'
    result['ranking_disclaimer']='엄격 매수/관심 별도 관찰 순위, 과거 검증 및 기술 조건 기준이며 미래 수익 확률이 아닙니다.' 
    # Only already-observed cached flows are attached AFTER ranking; no new
    # provider requests, no retrospectively backfilled investability signal.
    for interest in result['interest_candidates']:
        payload=None
        if auxiliary_flow is not None:
            try: payload=auxiliary_flow(interest['symbol'])
            except Exception: pass
        interest['flow_auxiliary']=summarize_auxiliary_flow(payload,asof)
    result['flow_auxiliary_coverage']=sum(x['flow_auxiliary']['status']=='OBSERVED_SHARES_5D'
        for x in result['interest_candidates'])
    result['flow_note']='캐시된 기존 수급만 관심 후보에 후행 표시. 종목추천 BUY 결정·과거 검증에 영향 없음'
    result['interest_count']=len(result['interest_candidates'])
    for failure in result['rejected']:
        reason=failure['reason']
        result['rejection_summary'][reason]=result['rejection_summary'].get(reason,0)+1
        code=failure.get('reason_code') or 'UNKNOWN'
        result['rejection_codes'][code]=result['rejection_codes'].get(code,0)+1
    result['analyzed_fraction']=round(len(top)/len(selected),3) if selected else 0.0
    result['incomplete']=result['deep_failed']>0
    result['signal_pattern_research']=pattern_finish(pattern_rows,pattern_exclusions,asof=asof,target=len(top))
    result['scan_profile']='pattern' if pattern_only else 'full'
    if pattern_only:
        result['method']='동일 일별 스냅샷 및 후보 선별 → 신호패턴 전용 검증 (엄격 매수/관심 순위 미실행)'
        result['recommended_count']=None
        result['interest_count']=None
    result['scan_assessment']=assess_scan_outcome(result)

    checkpoint()
    if progress:progress({'phase':'complete','done':len(top),'total':len(top),
        'message':(f'신호패턴 분석 완료: {len(pattern_rows)}개 연구 후보' if pattern_only else f'분석 완료: {len(result["recommendations"])}개 매수 후보')+f', 심층 조회 실패 {result["deep_failed"]}개'})
    return result


def classify_krx_response(http_status: int, payload, market: str):
    """Classify the vendor status without returning raw vendor texts or credentials.

    KRX sometimes responds HTTP 200 with {respCode: "401", respMsg: ...}.
    That must never be treated as a successful market snapshot.
    """
    if market not in URLS:
        raise ValueError('unsupported market')
    label = '코스피' if market == 'kospi' else '코스닥'
    api_id = 'stk_bydd_trd' if market == 'kospi' else 'ksq_bydd_trd'
    code_raw = str(payload.get('respCode') or '').strip() if isinstance(payload, dict) else ''
    code = code_raw if re.fullmatch(r'\d{1,4}',code_raw) else None
    msg = str(payload.get('respMsg') or '').lower()[:120] if isinstance(payload,dict) else ''
    rows = payload.get('OutBlock_1') if isinstance(payload,dict) else None
    if isinstance(rows,dict): rows=[rows]
    count = len(rows) if isinstance(rows,list) else None

    # Explicit HTTP and vendor-level failure codes take precedence over rows.
    if http_status == 401 or code == '401' or 'unauthorized api call' in msg or 'unauthorized key' in msg:
        state='auth_denied'
        if 'unauthorized key' in msg:
            err='unauthorized_key'; detail=f'{label}: 인증키 인식 실패 (Unauthorized Key)'
        elif 'unauthorized api call' in msg:
            err='unauthorized_api_call'; detail=f'{label}: API 호출 권한 거부 (Unauthorized API Call)'
        else:
            err='unauthorized'; detail=f'{label}: KRX 인증 거부 (401)'
    elif http_status == 403 or code == '403':
        state='auth_denied'; err='forbidden';detail=f'{label}: 접근 권한 거부 (403)'
    elif http_status == 429 or code == '429':
        state='rate_limited';err='provider_rate_limited';detail=f'{label}: 요청 횟수 제한 (429)'
    elif http_status >= 500:
        state='provider_error';err='provider_unavailable';detail=f'{label}: 서버 오류 (HTTP {http_status})'
    elif http_status != 200:
        state='provider_error';err='provider_unavailable';detail=f'{label}: HTTP 오류 ({http_status})'
    elif code and code not in ('0','00','000','200'):
        state='provider_error';err='provider_rejected';detail=f'{label}: KRX 응답 오류 (코드 {code})'
    elif isinstance(rows,list):
        state='success' if count else 'no_data'
        err='ok' if count else 'no_published_data'
        detail=f'{label}: 실제 종목 자료 {count}건 수신' if count else f'{label}: 해당 기준일 종목 자료 없음'
    else:
        state='provider_error';err='provider_format';detail=f'{label}: JSON 응답의 OutBlock_1 형식 확인 필요'
    return {'market':market,'api_id':api_id,'request_type':'GET',
            'auth_header_sent':True,'http_status':int(http_status),
            'krx_code':code,'state':state,'error_code':err,'detail':detail,
            'record_count':count if state in ('success','no_data') else None}


def diagnostic_dates(now: datetime, basis_date=None):
    """Manual date: one completed weekday. Auto: latest and one earlier
    weekday. These are candidates, not a verified holiday/publication calendar.
    """
    candidates=tuple(completed_dates(now))
    if not candidates:raise ValueError('KRX_NO_COMPLETED_DATE')
    if basis_date is None or basis_date=='':
        return candidates[:2]
    if not isinstance(basis_date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',basis_date):
        raise ValueError('KRX_INVALID_DATE')
    try:day=datetime.strptime(basis_date,'%Y-%m-%d').date()
    except ValueError:raise ValueError('KRX_INVALID_DATE') from None
    stamp=day.strftime('%Y%m%d')
    if day.weekday()>=5 or not '20100104'<=stamp<=candidates[0]:
        raise ValueError('KRX_INVALID_DATE')
    return (stamp,)


def diagnose_bounded(api_key,dates,*,probe=None):
    """At most two dates / four requests; only an explicit empty result may
    qualify for an earlier date. No automatic retry on access/transport or
    rate-limit errors. A successful market is never requested a second time.
    No raw provider data is returned or stored.
    """
    if probe is None:probe=diagnose_connection
    dates=tuple(dates)
    if not 1<=len(dates)<=2 or len(set(dates))!=len(dates):
        raise ValueError('KRX_INVALID_DIAGNOSTIC_WINDOW')
    remaining=['kospi','kosdaq'];all_results=[];checked=[];notes=''
    for day in dates:
        if not remaining:break
        result=probe(api_key,day,remaining)
        if not result.get('configured'):
            return {'configured':False,'date':dates[0],'checked_dates':[],'results':[],
                    'notes':'KRX 인증키 미설정'}
        rows=result.get('results',[])
        checked.append(day)
        all_results.extend({**r,'date':day} for r in rows)
        notes=result.get('notes','')
        if (len(rows)!=len(remaining) or set(r.get('market') for r in rows)!=set(remaining)
            or any(r.get('state') not in ('success','no_data') for r in rows)):
            break
        remaining=[m for m in remaining if any(r.get('market')==m and r.get('state')=='no_data' for r in rows)]
    return {'configured':True,'date':dates[0],'checked_dates':checked,'results':all_results,
            'notes':notes}


def diagnose_connection(api_key: str, date: str, markets: list[str], session=None):
    """Probe exact production endpoints with the same header as scanner.

    Deliberately no authentication secret, full response, or URL in result.
    """
    if not api_key or not api_key.strip():
        return {'date':date,'configured':False,'results':[]}
    if not re.fullmatch(r'\d{8}',date):
        raise ValueError('bad date')
    client_session=session or requests.Session()
    results=[]
    for market in markets:
        if market not in URLS: raise ValueError('bad market')
        try:
            response=client_session.get(URLS[market],params={'basDd':date},
                headers={'AUTH_KEY':api_key.strip(),'Accept':'application/json'},timeout=(5,14),allow_redirects=False)
            try: payload=response.json() if callable(getattr(response,'json',None)) else None
            except (ValueError,TypeError): payload=None
            results.append(classify_krx_response(response.status_code,payload,market))
        except requests.RequestException:
            results.append({'market':market,'api_id':'stk_bydd_trd' if market=='kospi' else 'ksq_bydd_trd',
                'request_type':'GET','auth_header_sent':True,'state':'network_error','http_status':None,
                'krx_code':None,'error_code':'network_error',
                'detail':'KRX 통신 실패: DNS, 네트워크 또는 연결 시간 초과',
                'record_count':None})
    return {'date':date,'configured':True,'results':results,
            'notes':'운영 API 직접 호출 결과. 인증키는 반환·기록하지 않습니다. HTTP 200만으로 데이터 성공을 의미하지 않습니다.'}
