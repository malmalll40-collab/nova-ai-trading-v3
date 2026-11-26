"""Research-only local admission and single-attempt quotation adapter.

No production coordinator, route, scheduler, token renewal or account entitlement.
A trusted coordinator must supply evidence; a caller-asserted value is not proof.
Existing foreground calls retain their normal behavior.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

VERSION='5.7.9'
MAX_PROOF_AGE=5.0
MIN_HEADROOM=3
MIN_INTERVAL=2.0
MAX_PER_MINUTE=12
TIMEOUT=3.0
STOCK_PATH='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
INDEX_PATH='/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice'


class ResearchBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class QuotaEvidence:
    observed_at:datetime
    physical_headroom:int
    covers_all_consumers:bool=False
    reserved_for_research:bool=False


def admission_reason(proof,now,*,foreground,physical,research,errors,overflow,starts,monotonic_now):
    if not isinstance(proof,QuotaEvidence) or proof.covers_all_consumers is not True or proof.reserved_for_research is not True:
        return 'ACCOUNT_QUOTA_UNVERIFIED'
    try:
        if proof.observed_at.tzinfo is None or now.tzinfo is None:return 'ACCOUNT_QUOTA_UNVERIFIED'
        age=(now-proof.observed_at).total_seconds()
    except (AttributeError,TypeError,ValueError):return 'ACCOUNT_QUOTA_UNVERIFIED'
    if not 0<=age<=MAX_PROOF_AGE:return 'ACCOUNT_QUOTA_STALE'
    if type(proof.physical_headroom) is not int or proof.physical_headroom<MIN_HEADROOM:return 'QUOTA_HEADROOM_LOW'
    return local_admission_reason(foreground=foreground,physical=physical,research=research,
        errors=errors,overflow=overflow,starts=starts,monotonic_now=monotonic_now)


def local_admission_reason(*,foreground,physical,research,errors,overflow,starts,monotonic_now):
    if foreground:return 'FOREGROUND_BUSY'
    if research or physical:return 'PHYSICAL_REQUEST_BUSY'
    if overflow or errors:return 'PROVIDER_UNHEALTHY'
    if len(starts)>=MAX_PER_MINUTE:return 'RESEARCH_MINUTE_LIMIT'
    if starts and monotonic_now-starts[-1]<MIN_INTERVAL:return 'RESEARCH_PACING'
    return None


def valid_request(path,tr_id,params):
    if not isinstance(params,dict):return False
    if path==STOCK_PATH and tr_id=='FHKST03010100':
        expected={'FID_COND_MRKT_DIV_CODE':'J','FID_ORG_ADJ_PRC':'0'}
        extra={'FID_ORG_ADJ_PRC'}
        code=r'\d{6}'
    elif path==INDEX_PATH and tr_id=='FHKUP03500100':
        expected={'FID_COND_MRKT_DIV_CODE':'U'}
        extra=set()
        code=r'\d{4}'
    else:return False
    fields={'FID_COND_MRKT_DIV_CODE','FID_INPUT_ISCD','FID_INPUT_DATE_1','FID_INPUT_DATE_2','FID_PERIOD_DIV_CODE'}|extra
    if set(params)!=fields or any(params.get(k)!=v for k,v in expected.items()) or params.get('FID_PERIOD_DIV_CODE')!='D':return False
    if not re.fullmatch(code,str(params.get('FID_INPUT_ISCD',''))):return False
    try:
        a=datetime.strptime(params['FID_INPUT_DATE_1'],'%Y%m%d').date()
        b=datetime.strptime(params['FID_INPUT_DATE_2'],'%Y%m%d').date()
    except (ValueError,TypeError):return False
    return 0<=(b-a).days<=1095


class ResearchHistoryClient:
    """Injected existing KIS client and trusted proof source; not wired to production.

    Never creates a session/token, never retries, only accepts historical GETs.
    Local admission protects the next request; an in-flight GET is not preemptible.
    """
    def __init__(self,kis,telemetry,proof_source=None):
        self._kis=kis;self._telemetry=telemetry;self._proof=proof_source

    def get(self,path,tr_id,params):
        if not valid_request(path,tr_id,params):raise ResearchBlocked('RESEARCH_ENDPOINT_BLOCKED')
        if self._proof is None:raise ResearchBlocked('ACCOUNT_QUOTA_UNVERIFIED')
        try:proof=self._proof()
        except Exception:raise ResearchBlocked('ACCOUNT_QUOTA_UNVERIFIED') from None
        k=self._kis
        if not k.token:raise ResearchBlocked('TOKEN_UNAVAILABLE')
        with self._telemetry.research_attempt(proof):
            try:
                r=k.session.get(k.BASE+path,headers={'authorization':f'Bearer {k.token}',
                    'appkey':k.key,'appsecret':k.secret,'tr_id':tr_id,'custtype':'P',
                    'content-type':'application/json; charset=utf-8'},params=params,timeout=TIMEOUT,
                    allow_redirects=False)
            except Exception:raise ResearchBlocked('RESEARCH_PROVIDER_FAILURE') from None
        try:
            if not 200<=int(r.status_code)<300:raise ResearchBlocked('RESEARCH_HTTP_FAILURE')
            data=r.json()
            if not isinstance(data,dict) or data.get('rt_cd')!='0':raise ResearchBlocked('RESEARCH_PAYLOAD_FAILURE')
            return data
        except ResearchBlocked:
            self._telemetry.failure()
            raise
        except Exception:
            self._telemetry.failure()
            raise ResearchBlocked('RESEARCH_PROVIDER_FAILURE') from None
