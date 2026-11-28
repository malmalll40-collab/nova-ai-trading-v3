"""Fixed exposure rules; private offline research only."""
from pathlib import Path
import sys,hashlib,json,zipfile
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'research_v16'))
from quality import quality,FIELDS,SOURCE

def regime(prices):
    r20=prices.pct_change(20,fill_method=None);r5=prices.pct_change(5,fill_method=None)
    return pd.Series(np.select([r20.isna()|r5.isna(),r5<=-.03,r20>=.02,r20<=-.02],['unknown','shock','up','down'],default='sideways'),index=prices.index)

def compare(q,days,idx):
    q=q.merge(idx,on=['market','day'],how='left',validate='many_to_one');assert q.regime.notna().all()
    top=q.sort_values(['day','median_turnover20','code'],ascending=[True,False,True]).groupby('day',sort=False).head(2).copy()
    ranges={'development':days[:359],'validation':days[364:541],'reviewed_holdout':days[546:724]};rows=[];details=[]
    for phase,d in ranges.items():
        z=top[top.phase==phase];cal=pd.Index(d,name='day')
        for tag,mask in [('liquidity',pd.Series(True,index=z.index)),('trend_only',z.regime.eq('up')),('avoid_down_shock',z.regime.isin(['up','sideways']))]:
            t=z[mask];known=t.net_pct.dropna();daily=t.assign(pess=t.net_pct.fillna(-100),neutral=t.net_pct.fillna(0)).groupby('day')[['pess','neutral']].sum().reindex(cal,fill_value=0)/2
            rows.append(dict(phase=phase,strategy=tag,calendar_days=len(cal),signals=len(t),signal_days=t.day.nunique(),unknown=int(t.net_pct.isna().sum()),known_mean_pct=known.mean(),win_pct=100*known.gt(0).mean(),loss5_pct=100*known.le(-5).mean(),daily_slot_mean_unknown_zero=daily.neutral.mean(),daily_slot_mean_unknown_minus100=daily.pess.mean()))
        for k,t in z.groupby('regime'):
            y=t.net_pct.dropna();details.append(dict(phase=phase,regime=k,signals=len(t),known=len(y),unknown=len(t)-len(y),mean_net_pct=y.mean(),loss5_pct=y.le(-5).mean()*100))
    return pd.DataFrame(rows),pd.DataFrame(details)

def main(root,out):
    source=root/'NOVA_KRX_20231126_20261125.zip'
    with source.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(source) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=['market','BAS_DD','ISU_CD',*FIELDS],dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    days=sorted(d.BAS_DD.unique());assert len(days)==729 and len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any()
    idx=[];files=[('KOSPI',['data_0200_20261127.xlsx']),('KOSDAQ',['data_0622_20261127.xlsx','data_2426_20261127.xlsx'])]
    expected={'data_0200_20261127.xlsx':'7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40','data_0622_20261127.xlsx':'e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','data_2426_20261127.xlsx':'e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'}
    for market,names in files:
        for n in names:assert hashlib.sha256((root/n).read_bytes()).hexdigest()==expected[n]
        a=pd.concat([pd.read_excel(root/f,usecols=['일자','종가']) for f in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all()
        idx.append(pd.DataFrame({'market':market,'day':a.index,'regime':regime(a['종가']).values}))
    codes=d.ISU_CD.unique();parts=[]
    for i in range(0,len(codes),200):
        q,_=quality(d[d.ISU_CD.isin(codes[i:i+200])],days,(363,545,728));parts.append(q[q.eligible&~q.purged])
    q=pd.concat(parts,ignore_index=True);assert len(q)==415309
    out.mkdir(parents=True,exist_ok=True)
    m,b=compare(q,days,pd.concat(idx,ignore_index=True));m.to_csv(out/'comparison.csv',index=False);b.to_csv(out/'regimes.csv',index=False);print(m.to_string(index=False));print(b.to_string(index=False))
    (out/'protocol.json').write_text(json.dumps({'source_sha256':SOURCE,'index_file_sha256':expected,'rule':'fixed 20-day ±2%, 5-day <=-3% shock, completed closes only, no replacement','reviewed_history_not_independent':True,'training_ready':False,'strategy_adoption':'HOLD','orders_enabled':False,'metric':'daily two-slot opportunity average with cash zero, not portfolio return; unknown zero and -100% sensitivities; overlapping 5-day outcomes'},indent=2))
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
