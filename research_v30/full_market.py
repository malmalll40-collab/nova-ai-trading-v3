"""Offline full-universe replay; same temporal cutoffs and features as v30."""
import sys,hashlib,zipfile,json
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
F=['ret5','ret20','relative20','vol20','turnover_log','turnover_ratio','compression','range_position','distance_high','close_location']
P=dict(n_estimators=100,learning_rate=.03,num_leaves=7,min_child_samples=200,reg_lambda=5,random_state=0,n_jobs=2,verbosity=-1)
def load(root, extended=False):
 src=root/'NOVA_KRX_20231126_20261125.zip'; assert hashlib.file_digest(src.open('rb'),'sha256').hexdigest()=='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
 fields=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS']
 with zipfile.ZipFile(src) as z: d=pd.concat([pd.read_csv(z.open(n),usecols=fields,dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
 assert len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any();days=sorted(d.BAS_DD.unique());assert len(days)==729
 d=d.rename(columns=dict(zip(fields[3:],['Close','Delta','Open','High','Low','Volume','Turnover','Cap','Shares'])))
 idx=[]
 expected={'data_0200_20261127.xlsx':'7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40','data_0622_20261127.xlsx':'e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','data_2426_20261127.xlsx':'e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'}
 for name,digest in expected.items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
 for m,names in [('KOSPI',['data_0200_20261127.xlsx'] ),('KOSDAQ',['data_0622_20261127.xlsx','data_2426_20261127.xlsx'])]:
  a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();p=a['종가'].shift(1);idx.append(pd.DataFrame({'market':m,'BAS_DD':p.index,'index_ret20':(p/p.shift(20)-1).values}))
 d=d.merge(pd.concat(idx),on=['market','BAS_DD'],validate='many_to_one').sort_values(['ISU_CD','BAS_DD']).reset_index(drop=True);raw_codes=d.ISU_CD.nunique();d['seq']=d.BAS_DD.map(dict(zip(days,range(len(days)))))
 valid=(d.Close>0)&(d.Open>0)&(d.Low>0)&(d.Low<=d[['Open','Close']].min(axis=1))&(d.High>=d[['Open','Close']].max(axis=1))&(d.Volume>0)&(d.Shares>0)&(d.Cap==d.Close*d.Shares)
 boundary=(d.ISU_CD!=d.ISU_CD.shift())|(d.seq.diff()!=1)|(d.Close-d.Delta!=d.Close.shift())|(d.Shares!=d.Shares.shift())|(d.market!=d.market.shift())|~valid|~valid.shift(fill_value=False)
 d['seg']=boundary.cumsum();d=d[valid].copy().reset_index(drop=True);g=d.groupby('seg',sort=False);c=d.Close.astype(float);r=c/g.Close.shift(1)-1
 def roll(s,n,op):return getattr(s.groupby(d.seg,sort=False).rolling(n),op)().reset_index(level=0,drop=True).reindex(d.index)
 d['ret5']=c/g.Close.shift(5)-1;d['ret20']=c/g.Close.shift(20)-1;d['relative20']=d.ret20-d.index_ret20;d['vol20']=roll(r,20,'std');d['turnover_log']=np.log1p(roll(d.Turnover,20,'median'));d['turnover_ratio']=roll(d.Turnover,3,'mean')/roll(g.Turnover.shift(3),20,'median').replace(0,np.nan);d['compression']=roll(r,5,'std')/roll(r.groupby(d.seg).shift(5),20,'std').replace(0,np.nan)
 hi=roll(g.High.shift(1),20,'max');lo=roll(g.Low.shift(1),20,'min');d['range_position']=(c-lo)/(hi-lo).replace(0,np.nan);d['distance_high']=c/hi.replace(0,np.nan)-1;d['close_location']=(c-d.Low)/(d.High-d.Low).replace(0,np.nan)
 atr=roll(pd.concat([d.High-d.Low,(d.High-g.Close.shift(1)).abs(),(d.Low-g.Close.shift(1)).abs()],axis=1).max(axis=1),14,'mean')/c;d['gross1']=100*(g.Close.shift(-1)/g.Open.shift(-1)-1)
 d['phase']=np.select([d.seq<=363,d.seq<=545],['development','validation'],default='reviewed_holdout');limit=d.phase.map({'development':363,'validation':545,'reviewed_holdout':728});eligible=(g.cumcount()>=65)&(d.seq+5<=limit)&d.index_ret20.notna();safe=atr.gt(0)&atr.le(.10)&np.isfinite(d[F]).all(axis=1)
 extra=[]
 if extended:
  for n in (5,10,20):
   k=f'turnover_ratio{n}';d[k]=roll(d.Turnover,n,'mean')/roll(g.Turnover.shift(n),20,'median').replace(0,np.nan);extra.append(k)
  rank_mask=(g.cumcount()>=65)&d.index_ret20.notna()&safe
  for src_col in ('ret5','relative20','turnover_ratio','turnover_log'):
   k='rank_'+src_col;d[k]=d[src_col].where(rank_mask).groupby(d.BAS_DD).rank(pct=True);extra.append(k)
   ch=k+'_change5';d[ch]=d[k]-d[k].groupby(d.seg).shift(5);extra.append(ch)
 q=d.loc[eligible&safe,['BAS_DD','ISU_CD','phase','gross1',*F,*extra]].rename(columns={'BAS_DD':'day','ISU_CD':'code'}).copy();meta=dict(raw_rows=1995617,raw_codes=int(raw_codes),market_days=len(days),eligible_before_safe=int(eligible.sum()),safe_rows=len(q),safe_codes=int(q.code.nunique()),unknown=int(q.gross1.isna().sum()),phase_counts=q.groupby('phase').size().to_dict());return q,meta,days

def compare(q,days):
 net=100*((1+q.gross1/100)*.997/1.003-1);tr=q.phase.eq('development')&net.notna();x=q.loc[tr,F];y=net[tr]
 for name,event in [('up',y.ge(3)),('down',y.le(-5))]:
  assert event.nunique()==2 and event.value_counts().min()>=20;q[name]=LGBMClassifier(**P).fit(x,event.astype(int)).predict_proba(q[F])[:,1]
 q['mean']=LGBMRegressor(**P).fit(x,y).predict(q[F]);q['joint']=q['mean']+3*q.up-5*q.down;rows=[]
 for phase,phase_days in [('development',days[65:359]),('validation',days[364:541])]:
  p=q[q.phase.eq(phase)];known=p[p.gross1.notna()];label=100*((1+known.gross1/100)*.997/1.003-1)>=3;auc=roc_auc_score(label,known.up);ap=average_precision_score(label,known.up)
  for rule in ['liquidity','up','mean','joint','joint_abstain']:
   z=p[(p['mean']>.61)&(p.up>p.down)] if rule=='joint_abstain' else p;key={'liquidity':'turnover_log','joint_abstain':'joint'}.get(rule,rule);t=z.sort_values(['day',key,'code'],ascending=[True,False,True]).groupby('day').head(2)
   for fee in (.003,.006):
    a=100*((1+t.gross1/100)*(1-fee)/(1+fee)-1);k=a.dropna();daily=a.fillna(-100).groupby(t.day).sum().reindex(phase_days,fill_value=0)/2;rows.append(dict(phase=phase,rule=rule,fee_bps=round(fee*20000),signals=len(t),known=len(k),unknown=len(t)-len(k),precision=k.ge(3).sum()/len(t) if len(t) else np.nan,mean_net_pct=k.mean(),loss5=k.le(-5).mean(),conservative_daily=daily.mean(),up_auc=auc,up_ap=ap,base_rate=label.mean()))
 return pd.DataFrame(rows)
if __name__=='__main__':
 q,meta,days=load(Path(sys.argv[1]));print(meta,flush=True);out=Path(sys.argv[2]);out.mkdir(exist_ok=True,parents=True);m=compare(q,days);m.to_csv(out/'nova_v30_full_market.csv',index=False);(out/'nova_v30_full_market_meta.json').write_text(json.dumps(meta,indent=2));print(m.to_string(index=False));print('HOLD: reviewed data, no independent future validation')
