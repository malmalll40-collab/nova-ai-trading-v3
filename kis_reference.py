"""Read-only KIS market index and ETF reference. No Yahoo fallback."""
from datetime import datetime,timedelta
import os,math
import pandas as pd
from data_sources import KIS,KST
from shadow_context import ETF_PROXIES

def fetch_kis_reference(ticker, now=None, *, period='9mo', api=None):
    if period not in ('9mo','2y'):raise ValueError('KIS_REFERENCE_PERIOD')
    now=(now or datetime.now(KST)).astimezone(KST)
    api=api or KIS(os.environ['KIS_APP_KEY'],os.environ['KIS_APP_SECRET'])
    start=now.date()-timedelta(days=730 if period=='2y' else 275)
    end=now.date()-timedelta(days=1) # always exclude today's index; no session assumption
    index_codes={'^KS11':'0001','^KQ11':'1001'}
    values={}
    if ticker in index_codes:
        for _ in range(10):
            response=api.get('/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice','FHKUP03500100',{
                'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':index_codes[ticker],
                'FID_INPUT_DATE_1':start.strftime('%Y%m%d'),'FID_INPUT_DATE_2':end.strftime('%Y%m%d'),'FID_PERIOD_DIV_CODE':'D'})
            rows=response.get('output2') or []
            dates=[]
            for row in rows:
                day=datetime.strptime(row['stck_bsop_date'],'%Y%m%d').date();close=float(row['bstp_nmix_prpr'])
                if not math.isfinite(close) or close<=0:raise ValueError('KIS_INDEX_INVALID')
                if start<=day<=end:values[day.isoformat()]=close;dates.append(day)
            if not dates or min(dates)<=start:break
            end=min(dates)-timedelta(days=1)
    elif ticker in {x[0] for x in ETF_PROXIES.values()}:
        rows=api.period_bars(ticker[:6],'1d',now=now)
        for t,row in rows.iterrows():
            day=pd.Timestamp(t).date();v=float(row['Close'])
            if start<=day<=end and math.isfinite(v) and v>0:values[day.isoformat()]=v
    else:raise ValueError('KIS_REFERENCE_UNKNOWN')
    if not values:raise ValueError('KIS_REFERENCE_EMPTY')
    frame=pd.DataFrame({'Close':pd.Series(values,dtype=float)}).sort_index();frame['available_at']=now.isoformat();return frame
