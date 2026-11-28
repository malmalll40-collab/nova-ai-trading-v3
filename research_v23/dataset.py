"""Same fixed 100-symbol cohort as v22; private files required."""
import hashlib,zipfile
import numpy as np,pandas as pd
from research_v22.role_audit import NAMES,FACTORS,signals,votes
SOURCE='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
FIELDS=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS']
def load(root):
    src=root/'NOVA_KRX_20231126_20261125.zip'
    with src.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=FIELDS,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any();days=sorted(d.BAS_DD.unique());assert len(days)==729
    first=d[d.BAS_DD==days[0]].sort_values(['market','ACC_TRDVAL','ISU_CD'],ascending=[True,False,True]);universe=first.groupby('market').head(50).ISU_CD.tolist();assert len(set(universe))==100
    d=d[d.ISU_CD.isin(universe)].rename(columns=dict(zip(FIELDS[3:],['Close','Delta','Open','High','Low','Volume','Turnover','Cap','Shares']))).copy()
    market=[]
    expected={'data_0200_20261127.xlsx':'7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40','data_0622_20261127.xlsx':'e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','data_2426_20261127.xlsx':'e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'}
    for m,names in [('KOSPI',['data_0200_20261127.xlsx']),('KOSDAQ',['data_0622_20261127.xlsx','data_2426_20261127.xlsx'])]:
        for n in names:assert hashlib.sha256((root/n).read_bytes()).hexdigest()==expected[n]
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();p=a['종가'].shift(1);r=p/p.shift(20)-1;v=p.pct_change().rolling(20).std(ddof=0)
        market.append(pd.DataFrame({'market':m,'BAS_DD':p.index,'index_ret20':r.values,'market_ok':((p>p.rolling(20).mean())&(p.rolling(20).mean()>p.rolling(50).mean())&(r>=0)&(v<=.04)).values}))
    d=d.merge(pd.concat(market),on=['market','BAS_DD'],validate='many_to_one');seqmap=dict(zip(days,range(len(days))));result=[]
    for code,g in d.groupby('ISU_CD'):
        g=g.sort_values('BAS_DD').reset_index(drop=True);seq=g.BAS_DD.map(seqmap)
        valid=(g.Close>0)&(g.Open>0)&(g.Low>0)&(g.Low<=g[['Open','Close']].min(axis=1))&(g.High>=g[['Open','Close']].max(axis=1))&(g.Volume>0)&(g.Shares>0)&(g.Cap==g.Close*g.Shares)
        boundary=(seq.diff()!=1)|(g.Close-g.Delta!=g.Close.shift())|(g.Shares!=g.Shares.shift())|(g.market!=g.market.shift())|~valid|~valid.shift(fill_value=False);seg=boundary.cumsum()
        for _,x in g[valid].groupby(seg):
            if len(x)<66:continue
            x=x.reset_index(drop=True);seq=x.BAS_DD.map(seqmap);sig,a=signals(x);_,veto,z=votes(x);a=a.assign(**{'factor_'+k:z[k].astype(int) for k in FACTORS});c=x.Close.astype(float);a['ret5']=c.pct_change(5);a['ret20']=c.pct_change(20);a['vol20']=c.pct_change().rolling(20).std();a['turnover_log']=np.log1p(x.Turnover.rolling(20).median());a['index_ret20']=x.index_ret20;a['relative20']=a.ret20-x.index_ret20
            net=(x.Close.shift(-5)/x.Open.shift(-1)*(.997/1.003)-1)*100;phase=np.select([seq<=363,seq<=545],['development','validation'],default='reviewed_holdout');limit=pd.Series(phase).map({'development':363,'validation':545,'reviewed_holdout':728});ok=(x.index>=65)&(seq+5<=limit)&x.index_ret20.notna()
            result.append(pd.concat([sig,a],axis=1).assign(day=x.BAS_DD,code=code,phase=phase,net_pct=net,safe=~veto)[ok])
    q=pd.concat(result,ignore_index=True);assert len(q)==47603 and q.net_pct.notna().sum()==46946 and not q.duplicated(['code','day']).any();return q
