import pandas as pd
import pytest
import kis_minute_coverage as k
import kis_gap_probe as gp
import kis_zero_gap as zg
import kis_turnover_proof as tp
from tests.test_turnover_proof import T,C,D,NOW
START=pd.Timestamp('2026-11-25 09:00',tz='Asia/Seoul')
TIMES=list(pd.date_range(START,C-pd.Timedelta(minutes=11),freq='min'))+[C]
class API:
    def __init__(self,mode,targets=('132100',)):
        self.mode=mode;self.targets=targets;self.calls=[]
    def get(self,path,tr,p):
        cursor=p['FID_INPUT_HOUR_1'];self.calls.append((tr,cursor))
        cumulative=1000;money=100000;raw=[]
        for t in TIMES:
            s=t.strftime('%H%M%S');v=0 if s in self.targets else 10;cumulative+=v;money+=v*101
            row=dict(stck_bsop_date='20261125',stck_cntg_hour=s,stck_oprc='100',stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol=str(v),acml_tr_pbmn=str(money))
            if self.mode=='original_volume':row['acml_vol']=str(cumulative)
            if self.mode=='invalid_volume' and s=='132200':row['acml_vol']='bad'
            if self.mode=='no_turnover':row.pop('acml_tr_pbmn')
            if tr=='FHKST03010200' and self.mode=='changed' and s==self.targets[-1]:row['acml_tr_pbmn']=str(money+1)
            if tr=='FHKST03010230' and s in self.targets:continue
            raw.append(row)
        return {'output2':[r for r in reversed(raw) if r['stck_cntg_hour']<=cursor][:120 if tr=='FHKST03010230' else 30]}
@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(k,'compare_same_day',lambda *a:gp.compare_same_day(*a,now=NOW))
    monkeypatch.setattr(k,'certify_zero_gaps',lambda *a:zg.certify_zero_gaps(*a,now=NOW))
    monkeypatch.setattr(k,'verify_turnover',lambda *a:tp.verify(*a,now=NOW))
@pytest.mark.parametrize('targets',[('132100',),('092800','102500')])
def test_full_collector(targets):
    v,d=k.collect(API('ok',targets),'086520',D,START,C)
    assert v['Volume']==(381-len(targets))*10
    assert d['certified_zero_count']==len(targets) and d['unresolved_count']==0
    assert zg.sanitized_proofs(d['zero_proofs'])==d['zero_proofs']
    assert all(p['method']==tp.METHOD for p in d['zero_proofs'].values())
@pytest.mark.parametrize('mode',['changed','no_turnover','invalid_volume'])
def test_rejected_all_or_nothing(mode):
    v,d=k.collect(API(mode,('092800','132100')),'086520',D,START,C)
    assert v is None and d['certified_zero_count']==0 and not d['zero_proofs']
def test_existing_volume_proof():
    v,d=k.collect(API('original_volume'),'086520',D,START,C)
    assert v['Volume']==3800 and 'method' not in d['zero_proofs']['13:21']
    assert zg.sanitized_proofs(d['zero_proofs'])==d['zero_proofs']
