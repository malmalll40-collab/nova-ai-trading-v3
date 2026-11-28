"""Offline research only; compare fixed risk sizing rules."""
import sys,json,hashlib,zipfile
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np,pandas as pd
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO/'research_v16'))
sys.path.insert(0,str(REPO/'research_v17'))
from quality import quality,FIELDS,SOURCE
from forward_shadow import forward as f
from regime import regime
RULES=('baseline','market_half','drawdown_half','combined')
def policy(rule,state,dd):
    if rule not in RULES or not np.isfinite(dd) or dd>1e-9:raise ValueError('INVALID_POLICY')
    market={'up':1.,'sideways':.5,'down':.5,'shock':0.,'unknown':0.}.get(state,0.) if rule in ('market_half','combined') else 1.
    return market*(.5 if rule in ('drawdown_half','combined') and dd<=-.05 else 1.)

def replay(days,bars,ranked,states,rule,fee):
    cash=f.CASH;positions={};eq=f.CASH;prev=None;queued=[];hist=[];events=[];entries=defaultdict(list);original=f.FEE;f.FEE=fee;peak=eq;factor=1.
    try:
        for seq,day in enumerate(days):
            today=bars[day]
            if prev is not None:
                cash,positions,ev,eq=f.step(cash,positions,eq*factor,[{'code':c} for c in queued] if factor>0 else [],today,prev,seq);events.extend(ev)
                for e in ev:
                    if e['event']=='ENTRY_ASSUMED':entries[e['code']].append(seq)
            locked=sum(p['mark'] for p in positions.values() if p['locked'])
            hist.append((day,eq,eq-locked,len(positions),sum(p['locked'] for p in positions.values()),factor));peak=max(peak,eq-locked)
            factor=policy(rule,states[day],(eq-locked)/peak-1);queued=ranked.get(day,[])[:2];prev=today
    finally:f.FEE=original
    h=pd.DataFrame(hist,columns=['day','equity','zero_locked','open','locked','entry_scale']);ex=[e['net_pct'] for e in events if e['event']=='EXIT_ASSUMED'];counts=Counter({k:len(v) for k,v in entries.items()});n=sum(counts.values());assert n==len(ex)+len(positions)
    def mdd(v):return float((v/v.cummax()-1).min()*100)
    return dict(strategy=rule,roundtrip_bps=int(fee*20000),entries=n,closed=len(ex),unique_symbols=len(counts),top2_entry_pct=100*sum(n for _,n in counts.most_common(2))/n if n else None,win_pct=100*sum(v>0 for v in ex)/len(ex) if ex else None,return_frozen_pct=100*(h.equity.iloc[-1]/f.CASH-1),return_zero_locked_pct=100*(h.zero_locked.iloc[-1]/f.CASH-1),mdd_frozen_pct=mdd(h.equity),mdd_zero_locked_pct=mdd(h.zero_locked),open_final=int(h.open.iloc[-1]),locked_final=int(h.locked.iloc[-1])),h

def main(root,out):
    assert hashlib.sha256(Path(f.__file__).read_bytes()).hexdigest()=='55faf64cc2f02175b3fc0c424f75d1211e504d35264dfae9d7092af81cbdd6f6'
    src=root/'NOVA_KRX_20231126_20261125.zip'
    with src.open('rb') as fp:assert hashlib.file_digest(fp,'sha256').hexdigest()==SOURCE
    with zipfile.ZipFile(src) as z:d=pd.concat([pd.read_csv(z.open(n),usecols=['market','BAS_DD','ISU_CD',*FIELDS],dtype={'BAS_DD':str,'ISU_CD':str}) for n in z.namelist() if n.startswith(('KOSPI_','KOSDAQ_')) and n.endswith('.csv')],ignore_index=True)
    days=sorted(d.BAS_DD.unique());assert len(days)==729 and len(d)==1995617 and not d.duplicated(['BAS_DD','ISU_CD']).any()
    indices=[]
    expected={'data_0200_20261127.xlsx':'7900f49e1556bd501e44b24b86d16c2a30375c4c39f79133943a8a0b02648a40','data_0622_20261127.xlsx':'e070548261adc959502bbd9795ccc79c3e373f4d49d00fe5509f2c0f5334edae','data_2426_20261127.xlsx':'e175b9c640b7c442baa785b508ff6467979760f3d42530c5e3ee0053166cafea'}
    for names in [['data_0200_20261127.xlsx'],['data_0622_20261127.xlsx','data_2426_20261127.xlsx']]:
        for name in names:assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected[name]
        a=pd.concat([pd.read_excel(root/name,usecols=['일자','종가']) for name in names]);a['day']=pd.to_datetime(a['일자']).dt.strftime('%Y%m%d');a=a[a.day.isin(days)].set_index('day').sort_index();assert list(a.index)==days and a['종가'].gt(0).all();indices.append(a['종가']/a['종가'].iloc[0])
    states=regime((indices[0]+indices[1])/2).to_dict()
    parts=[];codes=d.ISU_CD.unique()
    for i in range(0,len(codes),200):
        q,_=quality(d[d.ISU_CD.isin(codes[i:i+200])],days,(363,545,728));parts.append(q[q.eligible&~q.purged][['code','day','median_turnover20']])
    q=pd.concat(parts,ignore_index=True);assert len(q)==415309
    top=q.sort_values(['day','median_turnover20','code'],ascending=[True,False,True]).groupby('day').head(2);ranked=top.groupby('day').code.apply(list).to_dict();del q,parts
    d=d.rename(columns=dict(zip(['market','BAS_DD','ISU_CD',*FIELDS],['market','day','code','close','delta','open','high','low','volume','turnover','cap','shares'])))
    codes={c for a in ranked.values() for c in a};bars={k:v.reset_index(drop=True) for k,v in d[d.code.isin(codes)].groupby('day')};del d
    rows=[];curves=[]
    for phase,ds in [('development',days[:364]),('validation',days[364:546]),('reviewed_holdout',days[546:])]:
        for rule in RULES:
            for fee in (.003,.006):
                r,h=replay(ds,bars,ranked,states,rule,fee);r['phase']=phase;rows.append(r);curves.append(h.assign(phase=phase,strategy=rule,roundtrip_bps=r['roundtrip_bps']));print(r,flush=True)
    result=pd.DataFrame(rows)
    old=pd.read_csv(REPO/'research_v19/v18_baseline.csv');a=result[result.strategy=='baseline'].merge(old[old.strategy=='liquidity'],on=['phase','roundtrip_bps'],suffixes=('_new','_old'),validate='one_to_one');assert len(a)==6 and np.allclose(a.return_frozen_pct_new,a.return_frozen_pct_old,rtol=0,atol=1e-8) and (a.closed_new==a.closed_old).all()
    out.mkdir(parents=True,exist_ok=True)
    result.to_csv(out/'risk_budget_comparison.csv',index=False);pd.concat(curves).to_csv(out/'equity_curves.csv.gz',index=False)
    gates={}
    for rule in RULES[1:]:
        a=result[result.strategy==rule].merge(result[result.strategy=='baseline'],on=['phase','roundtrip_bps'],suffixes=('_new','_base'));a=a[a.phase.isin(['development','validation'])]
        checks=dict(positive_return=bool((a.return_zero_locked_pct_new>0).all()),return_not_worse=bool((a.return_zero_locked_pct_new>=a.return_zero_locked_pct_base).all()),drawdown_not_worse=bool((a.mdd_zero_locked_pct_new>=a.mdd_zero_locked_pct_base).all()),min_closed=bool((a.closed_new>=30).all()),no_unresolved_final=bool((a.locked_final_new==0).all()));gates[rule]={**checks,'pass':all(checks.values())}
    (out/'summary.json').write_text(json.dumps(dict(source_sha256=SOURCE,baseline_parity=6,gate=gates,orders_enabled=False,adoption='HOLD',reviewed_history_not_independent=True,notes='Fixed prior-close sizing on normalized 50/50 price index regime and drawdown; no forced liquidation, no post-dispatch replacement; existing positions continue original exits. No verified corporate adjustment, dividends, or terminal liquidation. Gate on development and validation, already reviewed; no adoption.'),indent=2));print(gates,flush=True)
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
