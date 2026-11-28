"""Offline historical replay and concentration audit. No production adoption."""
import sys,hashlib,json,zipfile
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np,pandas as pd
REPO=Path(__file__).resolve().parent.parent
for p in ('research_v16','research_v17'):sys.path.insert(0,str(REPO/p))
from quality import quality,FIELDS,SOURCE
from regime import regime
from forward_shadow import forward as f
FROZEN='55faf64cc2f02175b3fc0c424f75d1211e504d35264dfae9d7092af81cbdd6f6'
INDEX_HASHES={'data_0200_20261127.xlsx':'7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40','data_0622_20261127.xlsx':'e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','data_2426_20261127.xlsx':'e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'}
VERIFIED=[('20241120','003920',679731,6797310,'https://kind.krx.co.kr/external/2024/11/15/000107/20241115000180/68155.htm'),('20241120','003925',200000,2000000,'https://kind.krx.co.kr/external/2024/11/15/000107/20241115000180/68155.htm'),('20260413','010120',30000000,150000000,'https://kind.krx.co.kr/external/2026/04/10/000814/20260410001941/68155.htm')]
def audit(d,days):
    x=d.sort_values(['ISU_CD','BAS_DD']);p=x.groupby('ISU_CD',sort=False)[['BAS_DD','TDD_CLSPRC','LIST_SHRS']].shift();cal=dict(zip(days,range(len(days))));adj=x.BAS_DD.map(cal)-p.BAS_DD.map(cal)==1
    ref=x.TDD_CLSPRC-x.CMPPREVDD_PRC;br=adj&ref.ne(p.TDD_CLSPRC);sh=adj&x.LIST_SHRS.ne(p.LIST_SHRS)
    z=pd.DataFrame({'day':x.BAS_DD,'code':x.ISU_CD,'market':x.market,'prior_close':p.TDD_CLSPRC,'reference':ref,'prior_shares':p.LIST_SHRS,'shares':x.LIST_SHRS,'reference_break':br,'shares_changed':sh})[br|sh].copy()
    z['ratio_match_1pct']=(z.reference.gt(0)&z.prior_close.gt(0)&z.shares.gt(0)&z.prior_shares.gt(0)&((z.reference*z.shares/(z.prior_close*z.prior_shares)-1).abs()<=.01))&z.reference_break&z.shares_changed
    z['independent_event_verified']=False
    return z,{'reference_break':int(br.sum()),'shares_changed':int(sh.sum()),'both':int((br&sh).sum()),'reference_only':int((br&~sh).sum()),'shares_only':int((sh&~br).sum()),'ratio_match_hypothesis':int(z.ratio_match_1pct.sum())}
def proof(z,cases=VERIFIED):
    out=[]
    for day,code,pre,post,url in cases:
        r=z[(z.day==day)&(z.code==code)]
        if not (len(r)==1 and r.iloc[0].ratio_match_1pct and r.iloc[0].prior_shares==pre and r.iloc[0].shares==post):
            raise ValueError('DISCLOSURE_DATA_MISMATCH')
        out.append(dict(day=day,code=code,prior_shares=pre,shares=post,ratio=post/pre,disclosure=url,verified_event=True,adjusted_price_verified=False))
    return pd.DataFrame(out)
def replay(days,bars,top,phase,strategy,fee):
    if strategy not in ('liquidity','trend_only','avoid_down_shock'):raise ValueError('STRATEGY')
    cash=f.CASH;positions={};eq=f.CASH;prev=None;queued=[];hist=[];events=[];entries_by_code=Counter();pnl_by_code=defaultdict(float);original=f.FEE;f.FEE=fee
    try:
        for seq,day in enumerate(days):
            today=bars[day];before={k:v['cost'] for k,v in positions.items()};exposure=sum(v['mark'] for v in positions.values())/eq if eq else 0
            if prev is not None:
                cash,positions,ev,eq=f.step(cash,positions,eq,queued,today,prev,seq);events.extend(ev)
                for e in ev:
                    if e['event']=='ENTRY_ASSUMED':entries_by_code[e['code']]+=1
                    if e['event']=='EXIT_ASSUMED':pnl_by_code[e['code']]+=before[e['code']]*e['net_pct']/100
            locked=sum(p['mark'] for p in positions.values() if p['locked'])
            hist.append(dict(day=day,prior_exposure=exposure,equity=eq,zero_locked=eq-locked,cash=cash,open=len(positions),locked=sum(p['locked'] for p in positions.values())))
            queued=[{'code':a['code']} for a in top.get(day,[]) if strategy=='liquidity' or (strategy=='trend_only' and a['regime']=='up') or (strategy=='avoid_down_shock' and a['regime'] in ('up','sideways'))];prev=today
    finally:f.FEE=original
    h=pd.DataFrame(hist);ex=[e['net_pct'] for e in events if e['event']=='EXIT_ASSUMED'];entries=sum(e['event']=='ENTRY_ASSUMED' for e in events)
    def mdd(s):return float((s/s.cummax()-1).min()*100)
    n=sum(entries_by_code.values());top2=entries_by_code.most_common(2);pnl_top=sorted((max(0,v) for v in pnl_by_code.values()),reverse=True)[:2];pos=sum(max(0,v) for v in pnl_by_code.values());total=sum(pnl_by_code.values())
    if not positions:assert abs(total-(eq-f.CASH))<1e-5
    return dict(unique_symbols=len(entries_by_code),top2_entry_codes=';'.join(c for c,_ in top2),top2_entry_pct=100*sum(v for _,v in top2)/n if n else None,top2_positive_pnl_pct=100*sum(pnl_top)/pos if pos else None,realized_pnl_krw=total,realized_pnl_ex_top2_krw=total-sum(pnl_top),mean_prior_exposure_pct=100*h.prior_exposure.mean(),phase=phase,strategy=strategy,roundtrip_bps=int(fee*20000),sessions=len(days),entries=entries,closed=len(ex),win_pct=100*sum(x>0 for x in ex)/len(ex) if ex else None,return_frozen_pct=100*(h.equity.iloc[-1]/f.CASH-1),return_zero_locked_pct=100*(h.zero_locked.iloc[-1]/f.CASH-1),mdd_frozen_pct=mdd(h.equity),mdd_zero_locked_pct=mdd(h.zero_locked),open_final=int(h.open.iloc[-1]),locked_final=int(h.locked.iloc[-1])),h
def benchmark(h,ip):
    if list(ip.index)!=list(h.day) or ip.isna().any().any() or not ip.gt(0).all().all():raise ValueError('INDEX_DATES_OR_VALUES')
    w=h.prior_exposure.to_numpy()
    if not np.isfinite(w).all() or not ((w>=0)&(w<=1+1e-10)).all():raise ValueError('EXPOSURE_RANGE')
    norm=ip/ip.iloc[0];b=norm.mean(axis=1);ret=ip.pct_change(fill_method=None).mean(axis=1).fillna(0).to_numpy()
    matched=pd.Series((1+w*ret).cumprod());r=100*(h.equity.iloc[-1]/f.CASH-1)
    return dict(kospi_price_return_pct=100*(norm.KOSPI.iloc[-1]-1),kosdaq_price_return_pct=100*(norm.KOSDAQ.iloc[-1]-1),benchmark_return_pct=100*(b.iloc[-1]-1),excess_pp=r-100*(b.iloc[-1]-1),benchmark_mdd_pct=100*(b/b.cummax()-1).min(),exposure_matched_index_return_pct=100*(matched.iloc[-1]-1),exposure_matched_index_mdd_pct=100*(matched/matched.cummax()-1).min(),excess_vs_exposure_matched_pp=r-100*(matched.iloc[-1]-1))
def main(root,out):
    assert hashlib.sha256(Path(f.__file__).read_bytes()).hexdigest()==FROZEN
    src=root/'NOVA_KRX_20231126_20261125.zip'
    with src.open('rb') as fp:assert hashlib.file_digest(fp,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=['market','BAS_DD','ISU_CD',*FIELDS],dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    days=sorted(d.BAS_DD.unique());assert len(days)==729 and len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any()
    out.mkdir(parents=True,exist_ok=True)
    a,counts=audit(d,days);p=proof(a);p.to_csv(out/'verified_event_samples.csv',index=False);a.to_csv(out/'corporate_event_candidates.csv.gz',index=False);del a
    idx=[];index_prices={}
    for market,names in [('KOSPI',['data_0200_20261127.xlsx']),('KOSDAQ',['data_0622_20261127.xlsx','data_2426_20261127.xlsx'])]:
        for n in names:assert hashlib.sha256((root/n).read_bytes()).hexdigest()==INDEX_HASHES[n]
        a=pd.concat([pd.read_excel(root/n,usecols=['일자','종가']) for n in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all()
        index_prices[market]=a['종가'];idx.append(pd.DataFrame({'market':market,'day':a.index,'regime':regime(a['종가']).values}))
    parts=[];codes=d.ISU_CD.unique()
    for i in range(0,len(codes),200):
        q,_=quality(d[d.ISU_CD.isin(codes[i:i+200])],days,(363,545,728));parts.append(q[q.eligible&~q.purged][['code','day','market','median_turnover20']])
    q=pd.concat(parts,ignore_index=True);assert len(q)==415309
    q=q.merge(pd.concat(idx),on=['market','day'],validate='many_to_one').sort_values(['day','median_turnover20','code'],ascending=[True,False,True]).groupby('day').head(2)
    top={k:v[['code','regime']].to_dict('records') for k,v in q.groupby('day')};del q,parts
    d=d.rename(columns=dict(zip(['market','BAS_DD','ISU_CD',*FIELDS],['market','day','code','close','delta','open','high','low','volume','turnover','cap','shares'])))
    codes={x['code'] for a in top.values() for x in a};bars={k:v.reset_index(drop=True) for k,v in d[d.code.isin(codes)].groupby('day')};del d
    results=[];curves=[]
    for phase,ds in [('development',days[:364]),('validation',days[364:546]),('reviewed_holdout',days[546:])]:
        for strategy in ('liquidity','trend_only','avoid_down_shock'):
            for fee in (.003,.006):
                r,h=replay(ds,bars,top,phase,strategy,fee);r.update(benchmark(h,pd.DataFrame(index_prices).loc[ds]));results.append(r);curves.append(h.assign(phase=phase,strategy=strategy,roundtrip_bps=r['roundtrip_bps']))
    new=pd.DataFrame(results)
    # Compare against the v18 account returns and trade counts recorded from private local run.
    baseline=pd.read_csv(Path(__file__).with_name('v18_baseline.csv'))
    a=new.merge(baseline,on=['phase','strategy','roundtrip_bps'],suffixes=('_new','_old'),validate='one_to_one')
    assert len(a)==18 and np.allclose(a.return_frozen_pct_new,a.return_frozen_pct_old,rtol=0,atol=1e-8) and (a.closed_new==a.closed_old).all()
    new.to_csv(out/'benchmark_concentration.csv',index=False);pd.concat(curves).to_csv(out/'equity_curves.csv.gz',index=False)
    (out/'summary.json').write_text(json.dumps({'source_sha256':SOURCE,'audit':counts,'verified_disclosure_samples':len(p),'unverified_ratio_candidates':counts['ratio_match_hypothesis']-len(p),'orders_enabled':False,'adoption':'HOLD','corporate_actions_verified':False,'reviewed_history_not_independent':True,'training_ready':False,'baseline_parity_rows':len(a),'limitations':'Price indices not independently identified, dividends and benchmark costs omitted. Exposure matched proxy uses prior close holding weight times 50/50 daily index change; not investable order simulation. Top-two positive P&L removal is accounting sensitivity, not counterfactual portfolio. No terminal liquidation or corporate event adjustment, same ex-post fill assumption and historical quality filters as v18. Not exact production ranking.'},indent=2))
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
