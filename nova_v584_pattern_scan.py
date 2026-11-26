"""Separate observational signal-pattern scan using existing deep-analysis bars only."""
from datetime import date,datetime,timedelta
from engine import WARMUP,decide
from core_comparison import _day
from nova_v574_data_audit import session_dates,KST
from nova_v576_expanded_coverage import corrected_sessions
from nova_v582_signal_pattern import pattern,finite

VERSION='5.8.4'
MAX_DISPLAY=20

def unavailable(reason):
    return {'version':VERSION,'status':'UNAVAILABLE','reason':reason,'research_only':True,
            'strategy_adoption':'HOLD','probability':None}

def from_features(f,cfg,*,asof,source,stale,demo,financial_gate,now=None,calendar=session_dates):
    """Use only 13-role baseline and existing features, no fresh provider call.
    Calendar match is not certification of provider OHLCV authenticity.
    Conservative previous-day cutoff prevents using today's incomplete bars.
    """
    if source!='kis' or stale is not False or demo is not False or cfg.mode!='스윙' or financial_gate!='PASS':
        return unavailable('SOURCE_FRESHNESS_OR_FINANCIAL_GATE')
    try:
        cutoff=datetime.strptime(asof,'%Y%m%d').date()
        if cutoff.isoformat().replace('-','')!=asof:return unavailable('ASOF_INVALID')
        now=now or datetime.now(KST)
        if now.tzinfo is None or cutoff>=now.astimezone(KST).date():return unavailable('SAME_DAY_UNVERIFIED')
        if len(f)<WARMUP+19:return unavailable('HISTORY_SHORT')
        # Reject duplicate, out-of-order or future feature rows.
        labels=[_day(t) for t in f.index]
        if labels!=sorted(set(labels)) or labels[-1]!=cutoff.isoformat():return unavailable('ASOF_MISMATCH')
        start=cutoff-timedelta(days=60)
        expected,_=corrected_sessions(calendar(start,cutoff),start,cutoff)
        if len(expected)<20 or [d.isoformat() for d in expected[-20:]]!=labels[-20:]:
            return unavailable('SESSION_MISMATCH')
        rows=[]
        for i in range(len(f)-20,len(f)):
            r=f.iloc[i]
            o,h,l,c,v=(float(r[k]) for k in ('Open','High','Low','Close','Volume'))
            if not all(finite(x) and x>0 for x in (o,h,l,c,v)) or not l<=min(o,c)<=max(o,c)<=h:
                return unavailable('OHLCV_INVALID')
            d=decide(f,cfg,i)
            rows.append({'date':labels[i],'completed':True,'price_close':c,'baseline':d.signal,'risk_veto':bool(d.risk_veto)})
        p=pattern(rows,'baseline')
        return {**p,'version':VERSION,'calendar_match':True,'calendar_fully_verified':False,
                'price_integrity_independently_verified':False,'basis':'KIS_HISTORICAL_ASOF_REPLAY',
                'financial_gate':'PASS','asof_evaluation_only':True}
    except Exception:
        return unavailable('PATTERN_INPUT_ERROR')

def candidate(snapshot,detail,asof,expected_asof):
    """Isolate scan decoration; never change the strict/interest candidate lists."""
    if not isinstance(detail,dict):return None,'ANALYSIS_UNAVAILABLE'
    if asof!=expected_asof:return None,'SNAPSHOT_DATE_LAG'
    if (detail.get('source')!='kis' or detail.get('is_stale') is not False or
            detail.get('is_demo') is not False or detail.get('mode')!='스윙' or
            detail.get('financial_gate')!='PASS' or detail.get('asof_evaluation_only') is not True):
        return None,'SOURCE_FRESHNESS_OR_FINANCIAL_GATE'
    if str(detail.get('last_completed_bar') or '')[:10].replace('-','')!=asof:return None,'ASOF_MISMATCH'
    p=detail.get('signal_pattern_research')
    if not isinstance(p,dict) or p.get('version')!=VERSION:return None,'PATTERN_UNAVAILABLE'
    if p.get('status')!='RESEARCH_CANDIDATE':return None,p.get('reason') or p.get('status','UNKNOWN')
    if (p.get('as_of','').replace('-','')!=asof or p.get('calendar_match') is not True or
            p.get('research_only') is not True or p.get('family')!='baseline' or
            not finite(p.get('score')) or not 0<=p['score']<=100):
        return None,'PATTERN_INTEGRITY'
    return {'symbol':snapshot['symbol'],'name':snapshot['name'],'market':snapshot['market'],
            'asof':asof,'score':p['score'],'pattern':p.get('pattern'),'counts':p.get('counts'),
            'streak':p.get('streak'),'last_buy_age':p.get('last_buy_age'),
            'since_first_buy_pct':p.get('since_first_buy_pct'),'ret5_pct':p.get('ret5_pct'),
            'drawdown_from_20d_close_peak_pct':p.get('drawdown_from_20d_close_peak_pct'),
            'financial_gate':'PASS','research_only':True,'not_actionable':True,'probability':None},None

def finish(candidates,exclusions,*,asof,target):
    ranked=sorted(candidates,key=lambda r:(-r['score'],r['symbol']))
    evaluated=len(ranked)+sum(exclusions.values())
    return {'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','probability_calibrated':False,
            'status':'RESEARCH_ONLY' if evaluated else 'UNAVAILABLE','asof':asof,
            'deep_target':target,'evaluated':evaluated,'eligible_total':len(ranked),
            'rows':[{'rank':i+1,**r} for i,r in enumerate(ranked[:MAX_DISPLAY])],
            'exclusions':dict(exclusions),'scope':'EXISTING_BOUNDED_DEEP_SCAN',
            'disclaimer':'13개 역할 기반 과거 완료봉 재계산 점수. 미래 상승확률·실매수 신호 아님. 전체 시장 심층 분석 아님. 달력 일치가 일봉 가격의 독립 검증을 뜻하지 않음.'}
