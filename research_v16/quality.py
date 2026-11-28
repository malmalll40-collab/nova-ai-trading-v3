"""Offline quality gate, not production training. Point-in-time eligibility."""
import hashlib,json,zipfile,sys
from pathlib import Path
import numpy as np,pandas as pd
SOURCE='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
FIELDS=['TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS']
def quality(d,days,cutoffs):
    d=d.sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True).copy()
    calendar=dict(zip(days,range(len(days))));seq=d.BAS_DD.map(calendar)
    g=d.groupby('ISU_CD',sort=False);p=g[['BAS_DD','TDD_CLSPRC','LIST_SHRS','market']].shift();adj=seq-p.BAS_DD.map(calendar)==1
    c,o,h,l,v,t,cap,s=(d[k] for k in ['TDD_CLSPRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS'])
    valid=(c>0)&(o>0)&(h>=np.maximum(c,o))&(l>0)&(l<=np.minimum(c,o))&(v>0)&(t>0)&(s>0)&(cap==c*s)
    ref=adj&(c-d.CMPPREVDD_PRC!=p.TDD_CLSPRC);shares=adj&(s!=p.LIST_SHRS);market=adj&(d.market!=p.market);gap=p.BAS_DD.notna()&~adj
    boundary=~adj|ref|shares|market|~valid|~valid.shift(fill_value=False)
    seg=boundary.cumsum();age=d.groupby(seg).cumcount()
    med=t.groupby(seg).rolling(20).median().reset_index(level=0,drop=True).sort_index()
    eligible=valid&(age>=65)&(c>=1000)&(med>=1e9)
    end=g.BAS_DD.shift(-5);entry=g.TDD_OPNPRC.shift(-1);exit_=g.TDD_CLSPRC.shift(-5)
    known=eligible&seg.eq(seg.shift(-5))&(end.map(calendar)==seq+5)&(entry>0)&(exit_>0)
    net=(exit_/entry*(1-.003)/(1+.003)-1)*100
    phase=np.select([seq<=cutoffs[0],seq<=cutoffs[1]],['development','validation'],default='reviewed_holdout')
    limit=pd.Series(phase).map(dict(zip(['development','validation','reviewed_holdout'],cutoffs)))
    purge=eligible&(seq+5>limit)
    result=pd.DataFrame({'code':d.ISU_CD,'day':d.BAS_DD,'market':d.market,'phase':phase,'eligible':eligible,'purged':purge,'known':known&~purge,'net_pct':net.where(known&~purge),'median_turnover20':med})
    flags={'zero_volume':v.eq(0),'active_invalid':(v>0)&~valid,'reference_break':ref,'shares_changed':shares,'market_changed':market,'nonadjacent':gap}
    return result,{k:int(x.sum()) for k,x in flags.items()}
def main(root,out):
    source=root/'NOVA_KRX_20231126_20261125.zip'
    with source.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(source) as z:
        frames=[pd.read_csv(z.open(n),usecols=['market','BAS_DD','ISU_CD',*FIELDS],dtype={'BAS_DD':str,'ISU_CD':str},keep_default_na=False) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')]
    d=pd.concat(frames,ignore_index=True);del frames
    for k in FIELDS:d[k]=pd.to_numeric(d[k],errors='raise')
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any()
    days=sorted(d.BAS_DD.unique());assert len(days)==729
    for files in [('data_0200_20261127.xlsx',),('data_0622_20261127.xlsx','data_2426_20261127.xlsx')]:
        idx=pd.concat([pd.read_excel(root/f,usecols=['일자','종가']) for f in files]);idx['day']=pd.to_datetime(idx['일자']).dt.strftime('%Y%m%d');idx=idx[idx.day.between('20231126','20261125')];assert not idx.day.duplicated().any() and set(idx.day)==set(days)
    reports=[];totals={};eligible=purged=0;codes=d.ISU_CD.unique()
    for i in range(0,len(codes),200):
        res,flags=quality(d[d.ISU_CD.isin(codes[i:i+200])],days,(363,545,728))
        for k,v in flags.items():totals[k]=totals.get(k,0)+v
        eligible+=int(res.eligible.sum());purged+=int(res.purged.sum());reports.append(res[res.eligible & ~res.purged].copy())
    del d;q=pd.concat(reports,ignore_index=True);report=[]
    for name,g in q.groupby('phase',sort=False):report.append({'phase':name,'eligible_opportunities':len(g),'known_outcomes':int(g.known.sum()),'unresolved_outcomes':int((~g.known).sum()),'symbols':g.code.nunique(),'days':g.day.nunique()})
    summary={'source_sha256':SOURCE,'raw_rows':1995617,'flags':totals,'eligible_before_purge':eligible,'purged':purged,'phase_counts':report,'adjusted_prices_verified':False,'corporate_actions_verified':False,'calendar_date_concordance':True,'index_identity_independently_verified':False,'training_ready':False,'production_adoption':'HOLD','orders_enabled':False,'method':'65 clean bars after boundary, median turnover20 >=1bn, close >=1000; 5 sessions, next open to close, 30bps each side; unknown labels retained; chronological phase purge; reviewed holdout not independent.'}
    out.mkdir(exist_ok=True);(out/'quality_summary.json').write_text(json.dumps(summary,indent=2));pd.DataFrame(report).to_csv(out/'phase_counts.csv',index=False);print(json.dumps(summary,indent=2))
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
