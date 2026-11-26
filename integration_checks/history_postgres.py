"""Standalone integration check, run only against disposable CI PostgreSQL."""
import os
from datetime import date,timedelta
import psycopg
from research_history_checkpoint import initialize,append,saved,missing_windows,TABLE
from research_history_postgres import transaction_factory
from research_history_resume import resume
from nova_v576_expanded_coverage import SYMBOLS
from nova_v574_data_audit import KST
from datetime import datetime

def main():
    dsn=os.environ['NOVA_TEST_POSTGRES_DSN']
    tx=transaction_factory(dsn)
    days=tuple(date(2026,11,23)+timedelta(days=i) for i in range(3))
    symbol=SYMBOLS[0]
    with tx() as (cur,kind):
        dataset=initialize(cur,kind,days)
        assert append(cur,kind,dataset,symbol,{days[1]:{'close':100}})==1
    with tx() as (cur,kind):
        assert saved(cur,kind,dataset,symbol)=={days[1]:{'close':100.0}}
        assert missing_windows(cur,kind,dataset,symbol)==[(days[0],),(days[2],)]
        assert append(cur,kind,dataset,symbol,{days[1]:{'close':100}})==0
    try:
        with tx() as (cur,kind):
            append(cur,kind,dataset,symbol,{days[0]:{'close':100}})
            raise RuntimeError('ROLLBACK_TEST')
    except RuntimeError as exc:
        assert str(exc)=='ROLLBACK_TEST'
    else:
        raise AssertionError('missing rollback')
    with tx() as (cur,kind):
        assert len(saved(cur,kind,dataset,symbol))==1
    class Fake:
        def __init__(self): self.calls=[]
        def get(self,path,tr,params):
            self.calls.append(params)
            return {'output2':[{'stck_bsop_date':d.strftime('%Y%m%d'),'bstp_nmix_prpr':'100'} for d in days if params['FID_INPUT_DATE_1']<=d.strftime('%Y%m%d')<=params['FID_INPUT_DATE_2']]}
    api=Fake()
    def run():
        return resume(api,days,transaction=tx,now=lambda:datetime(2026,11,26,21,tzinfo=KST),
            calendar=lambda t:{'calendar_verified':True,'status':'CLOSED','open_kst':'09:00','close_kst':'15:30'},
            admission=lambda:True,pause=lambda n:None,max_windows=1)
    a=run()
    assert a['new_rows']==1 and a['logical_calls']==1
    b=run()
    assert b['new_rows']==1 and b['logical_calls']==1
    assert [(x['FID_INPUT_DATE_1'],x['FID_INPUT_DATE_2']) for x in api.calls]==[('20261123','20261124'),('20261124','20261125')]
    assert a['overlap_anchors_checked']==b['overlap_anchors_checked']==1
    with tx() as (cur,kind):
        assert missing_windows(cur,kind,dataset,symbol)==[]
        assert len(saved(cur,kind,dataset,symbol))==3
        cur.execute(f'SELECT COUNT(*) FROM {TABLE}')
        assert cur.fetchone()[0]==3
    print('PostgreSQL integration PASS: durable resume, idempotence, rollback, isolated rows')
if __name__=='__main__':
    main()
