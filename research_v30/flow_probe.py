"""Read-only one-request research probe; not a historical point-in-time feed."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
KST=ZoneInfo("Asia/Seoul")
PATH="/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
TR="FHPTJ04160001"
def request_params(code,day):
    if not re.fullmatch(r"\d{6}",code) or not re.fullmatch(r"\d{8}",day):
        raise ValueError("KEY")
    datetime.strptime(day,"%Y%m%d")
    return {"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code,"FID_INPUT_DATE_1":day,"FID_ORG_ADJ_PRC":"","FID_ETC_CLS_CODE":""}
def shares(raw):
    if raw is None or str(raw).strip()=="":return None
    value=str(raw).strip().replace(",","")
    if not re.fullmatch(r"[+-]?\d+",value) or abs(int(value))>10**15:raise ValueError("SHARES")
    return int(value)
def parse(payload,code,requested_day,observed_at):
    request_params(code,requested_day)
    if observed_at.tzinfo is None:raise ValueError("TIMEZONE")
    today=observed_at.astimezone(KST).strftime("%Y%m%d")
    if requested_day>=today:raise ValueError("COMPLETED_DAY_ONLY")
    if payload.get("rt_cd")!="0":raise ValueError("UPSTREAM")
    rows=payload.get("output2")
    if not isinstance(rows,list) or not rows:raise ValueError("EMPTY_OR_SCHEMA")
    unique={}
    for r in rows:
        day=str(r.get("stck_bsop_date",""))
        if not re.fullmatch(r"\d{8}",day):raise ValueError("DATE")
        datetime.strptime(day,"%Y%m%d")
        if day>requested_day:raise ValueError("UNEXPECTED_LATER_DATE")
        item={"day":day,"code":code,"foreign_shares":shares(r.get("frgn_ntby_qty")),"institution_shares":shares(r.get("orgn_ntby_qty")),"published_at":observed_at.isoformat(),"source":"KIS dated API; first observed timestamp, original publication unverified","scope":"KRX"}
        if day in unique and unique[day]!=item:raise ValueError("CONFLICT")
        unique[day]=item
    return [unique[k] for k in sorted(unique)]
def probe(api,code,day):
    params=request_params(code,day)
    if day>=datetime.now(KST).strftime("%Y%m%d"):raise ValueError("COMPLETED_DAY_ONLY")
    payload=api.get(PATH,TR,params)
    return parse(payload,code,day,datetime.now(KST))
