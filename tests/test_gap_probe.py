from datetime import date, datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from kis_gap_probe import alternate_queries, compare_same_day
D=date(2026,11,25); T=pd.Timestamp('2026-11-25 13:21',tz='Asia/Seoul'); C=pd.Timestamp('2026-11-25 15:30',tz='Asia/Seoul'); N=datetime(2026,11,25,20,tzinfo=ZoneInfo('Asia/Seoul'))
def test_alternate_cursor_and_bound():
    assert alternate_queries([T],C)==[(T,'132200')]
    assert alternate_queries([T]*5,C)==[]
    assert alternate_queries([C],C)==[(C,'153000')]
def row(t,volume='3',day='20261125'):
    return {'stck_bsop_date':day,'stck_cntg_hour':t,'cntg_vol':volume}
class API:
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def get(self,path,tr,params):
        assert tr=='FHKST03010200' and params['FID_INPUT_HOUR_1']=='132200' and params['FID_COND_MRKT_DIV_CODE']=='J'
        self.calls.append(params);return {'output2':self.rows}
@pytest.mark.parametrize('rows,expected', [([row('132200'),row('132100')],'TARGET_POSITIVE_VOLUME'),([row('132200'),row('132100','0')],'TARGET_ZERO_VOLUME'),([row('132100')],'TARGET_VOLUME_UNRELIABLE'),([row('132200'),row('132100',day='20261124')],'TARGET_ABSENT'),([row('132200'),row('132100','bad')],'INVALID_VOLUME'),([row('132200'),row('132100'),row('132100')],'CONFLICTING_TARGET')])
def test_compare(rows,expected):
    assert compare_same_day(API(rows),'086520',D,[T],C,now=N)=={'13:21':expected}
def test_wrong_day_does_not_query():
    a=API([]);assert compare_same_day(a,'086520',D,[T],C,now=N+pd.Timedelta(days=1))=={'13:21':'NOT_SAME_DAY'} and not a.calls
