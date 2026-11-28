"""Run only in an already-authorized environment, manually.
python -m research_v30.run_flow_probe
No order calls, persistent research writes, or raw response logging.
The broker client's ordinary token/cache and quota rules still apply.
"""
import json
import os
import sys
from research_v30.flow_probe import probe

DATES=("20261125","20241125","20231127")
CODE="005930"

def run(api):
    result=[]
    for target in DATES:
        try:
            rows=probe(api,CODE,target)
        except Exception as exc:
            # No exception payload: broker messages can contain sensitive details.
            result.append({"requested":target,"status":"FAILED","error_class":type(exc).__name__})
            break
        complete=[r for r in rows if r["foreign_shares"] is not None and r["institution_shares"] is not None]
        result.append({"requested":target,"status":"OK","returned_rows":len(rows),"complete_rows":len(complete),"earliest":min(r["day"] for r in rows),"latest":max(r["day"] for r in rows),"requested_date_complete":any(r["day"]==target for r in complete),"original_publication_verified":False})
    return result

def main():
    key=os.environ.get("KIS_APP_KEY","").strip()
    secret=os.environ.get("KIS_APP_SECRET","").strip()
    if not key or not secret:
        print(json.dumps({"status":"AUTH_ENV_UNAVAILABLE","requests":0}))
        return 2
    from data_sources import KIS
    try:
        rows=run(KIS(key,secret))
    except Exception as exc:
        print(json.dumps({"status":"INIT_FAILED","error_class":type(exc).__name__}))
        return 2
    print(json.dumps({"symbol":CODE,"results":rows,"orders_enabled":False,"historical_point_in_time_verified":False},ensure_ascii=False))
    return 0 if len(rows)==len(DATES) and all(r.get("requested_date_complete") for r in rows) else 1

if __name__=="__main__":
    sys.exit(main())
