/* NOVA v3.8.1: forward-only, device-local signal journal; no unattended server jobs. */
'use strict';
(function () {
 const STORAGE_KEY='nova-v35-journal-v1';
 const LEGACY_KEY='nova-v34-prospective-v1';
 const MAX_LOCAL_RECORDS=800;
 const HORIZONS=[1,3,5,10];
 const $=id=>document.getElementById(id);
 const esc=text=>String(text??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
 const pct=n=>Number.isFinite(Number(n))?(Number(n)>=0?'+':'')+Number(n).toFixed(2)+'%':'—';
 const safeGet=(key,fallback)=>{try{const x=JSON.parse(localStorage.getItem(key)||'null');return x??fallback;}catch{return fallback;}};
 const safeSet=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}};
 let journal=[];
 let busy=false;
 let lastRefresh=new Map();

 function validRecord(r){
   return r && typeof r==='object' && typeof r.id==='string' && r.id.length<161 &&
     /^\d{6}\.(KS|KQ)$|^[A-Z][A-Z0-9^.=\-]{0,23}$/.test(r.symbol||'') &&
     ['1분','5분','15분','스윙'].includes(r.mode) && ['yahoo','kis'].includes(r.source) &&
     ['BUY','SELL'].includes(r.technical) && ['BUY','SELL','HOLD'].includes(r.filtered) &&
     /^\d{4}-\d{2}-\d{2}$/.test(r.bar_day||'') &&
     Number.isFinite(Number(r.reference)) && Number(r.reference)>0;
 }
 function normalize(r){
   return {...r, cost_bps:Math.min(100,Math.max(0,Number(r.cost_bps)||0)),
     reference:Number(r.reference),gate:['PASS','CAUTION','BLOCK','UNKNOWN'].includes(r.gate)?r.gate:'UNKNOWN',
     outcomes:r.outcomes && typeof r.outcomes==='object'?r.outcomes:{}};
 }
 function load(){
   journal=(Array.isArray(safeGet(STORAGE_KEY,[]))?safeGet(STORAGE_KEY,[]):[]).filter(validRecord).map(normalize).slice(-MAX_LOCAL_RECORDS);
   // Upgrade v3.4 once. Never overwrite or invent outcomes from earlier 5d record.
   const previous=safeGet(LEGACY_KEY,[]);
   if(!safeGet('nova-v35-migrated',false) && Array.isArray(previous)){
     for(const old of previous){
       if(!old || !old.symbol || !old.bar_day || !['BUY','SELL'].includes(old.technical))continue;
       const candidate={id:`legacy|${old.symbol}|스윙|${old.bar_day}`,symbol:old.symbol,
         source:'yahoo',mode:'스윙',bar_day:old.bar_day,bar_time:old.bar_day,
         observed_at:old.observed_at||'',reference:old.reference,technical:old.technical,
         filtered:old.filtered||old.technical,gate:old.gate||'UNKNOWN',cost_bps:15,outcomes:{}};
       if(validRecord(candidate)&&!journal.some(x=>x.id===candidate.id))journal.push(normalize(candidate));
     }
     journal=journal.slice(-MAX_LOCAL_RECORDS);
     save();safeSet('nova-v35-migrated',true);
   }
 }
 function save(){return safeSet(STORAGE_KEY,journal);}
 function signalId(d){
   const date=String(d.last_completed_bar||'').slice(0,10);
   // One objective observation per source / ticker / mode / trading day.
   return `${d.source}|${d.symbol}|${d.mode}|${date}`;
 }
 function record(d){
   // Intraday signals are NOT evaluated against 1/3/5/10 daily closes.
   // Preserve legacy intraday entries in backups but exclude from accuracy stats.
   if(!d || d.mode!=='스윙' || d.is_demo || d.is_stale || !d.signal_is_current || !['BUY','SELL'].includes(d.raw_signal||d.signal))return;
   if(!d.reference || !d.last_completed_bar || !['kis','yahoo'].includes(d.source))return;
   const id=signalId(d),bar_day=String(d.last_completed_bar).slice(0,10);
   if(!/^\d{4}-\d{2}-\d{2}$/.test(bar_day) || journal.some(x=>x.id===id))return;
   if(journal.length>=MAX_LOCAL_RECORDS){setStatus('기록 상한 800건에 도달했습니다. JSON 백업 후 기존 기록을 정리하세요.');return;}
   const row={id,source:d.source,symbol:d.symbol,mode:d.mode,bar_day,
     bar_time:d.last_completed_bar,observed_at:new Date().toISOString(),reference:Number(d.reference),
     technical:d.raw_signal||d.signal,filtered:d.signal,gate:d.financial_gate||'UNKNOWN',cost_bps:Number(d.cost_bps_one_side??15),outcomes:{}};
   if(validRecord(row)){journal.push(row);if(!save())setStatus('저장 공간 부족으로 추적 기록을 저장하지 못했습니다.');render();}
 }
 function updateFinance(d, final, gate){
   if(!d || d.mode!=='스윙')return;
   const id=signalId(d),r=journal.find(x=>x.id===id);
   if(!r)return;
   if(['PASS','CAUTION','BLOCK'].includes(gate) && ['BUY','SELL','HOLD'].includes(final)){
     r.filtered=final;r.gate=gate;save();render();
   }
 }
 function setStatus(s){if($('journalStatus'))$('journalStatus').textContent=s;}
 const rate=(rows,key='direction_hit')=>rows.length?Number((rows.filter(x=>x[key]).length/rows.length*100).toFixed(1)):null;
 const mean=(rows,key)=>{const vals=rows.map(x=>x[key]).filter(x=>typeof x==='number'&&Number.isFinite(x));return vals.length?Number((vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(2)):null;};
 function eligible(){
   const mode=$('journalModeFilter')?.value||'all';
   return journal.filter(x=>x.mode==='스윙' && (mode==='all'||x.mode===mode));
 }
 function render(){
   if(!$('journalMetrics'))return;
   const records=eligible();
   const grid=$('journalMetrics');grid.replaceChildren();
   for(const h of HORIZONS){
     const complete=records.map(r=>({...r,ev:r.outcomes?.[String(h)]}))
       .filter(x=>x.ev?.status==='complete');
     const wins=complete.filter(x=>x.ev.direction_hit).length;
     const hit=complete.length?wins/complete.length*100:null;
     const signed=complete.map(x=>Number(x.ev.directional_score_pct)).filter(Number.isFinite);
     const average=signed.length?signed.reduce((a,b)=>a+b,0)/signed.length:null;
     const buy=complete.filter(x=>x.technical==='BUY'&&typeof x.ev.buy_net_pct==='number');
     const buyAvg=mean(buy.map(x=>x.ev),'buy_net_pct');
     const c=document.createElement('div');c.className='journal-metric';
     c.innerHTML=`<span>${h}거래일 후</span><strong>${hit==null?'미확인':hit.toFixed(1)+'%'}</strong>
        <small>방향 적중 ${wins}/${complete.length}건</small>
        <small>평균 방향 변화 ${average==null?'—':pct(average)}</small>
        <small>매수 신호 순변화 ${buyAvg==null?'—':pct(buyAvg)}</small>`;
     grid.appendChild(c);
   }
   const outcome5=records.map(r=>({...r,event:r.outcomes?.['5']})).filter(x=>x.event?.status==='complete');
   const buyOnly=outcome5.filter(x=>x.technical==='BUY');
   const verified=buyOnly.filter(x=>['PASS','CAUTION','BLOCK'].includes(x.gate));
   const hold=verified.filter(x=>x.filtered==='HOLD');
   const a=verified.map(x=>Number(x.event.buy_net_pct)).filter(Number.isFinite);
   const b=verified.map(x=>x.filtered==='BUY'?Number(x.event.buy_net_pct):0).filter(Number.isFinite);
   const ma=a.length?a.reduce((x,y)=>x+y,0)/a.length:null;
   const mb=b.length?b.reduce((x,y)=>x+y,0)/b.length:null;
   const adverse=buyOnly.map(x=>x.event.close_path_drawdown_pct).filter(v=>typeof v==='number');
   const worst=adverse.length?Math.min(...adverse):null;
   $('journalAB').textContent=`5거래일 필터 A/B (재무·안정형 합산) · 동일한 검증 대상 매수 신호 ${verified.length}건 · 차단 ${hold.length}건\n`
     +`기술적 매수 평균 순변화 ${ma==null?'—':pct(ma)} / 필터 적용(관망은 0%) ${mb==null?'—':pct(mb)}\n`
     +`매수 신호 종가 경로 최악 낙폭 ${worst==null?'—':pct(worst)} (포트폴리오 MDD가 아님)`;
   const list=$('journalList');list.replaceChildren();
   for(const row of records.slice().reverse().slice(0,45)){
     const d=document.createElement('div');d.className='journal-item';
     const h=row.outcomes?.['5'];
     const details=HORIZONS.map(n=>{
       const r=row.outcomes?.[String(n)];
       return `${n}일 ${r?.status==='complete'?pct(r.asset_change_pct):r?.status==='unavailable'?'미확인':'대기'}`;
     }).join(' · ');
     const title=document.createElement('strong');title.textContent=`${row.symbol} · ${row.mode} · ${row.bar_day} · ${row.technical==='BUY'?'매수':'보유축소'}`;
     const sub=document.createElement('small');sub.textContent=`${details} | 재무 ${row.gate||'UNKNOWN'}`;
     d.append(title,sub);list.appendChild(d);
   }
   if(!records.length)list.textContent='관찰 기록이 없습니다. 새로운 실시간 시세의 매수·매도 신호가 발생하면 자동 기록됩니다.';
   const complete10=records.filter(r=>r.outcomes?.['10']?.status==='complete').length;
   const oldIntraday=journal.filter(x=>x.mode!=='스윙').length;
   setStatus(`스윙 평가 ${records.length}건 · 완료 ${complete10}건 · 이전 단타 ${oldIntraday}건은 오분류 방지를 위해 집계 제외(백업 보존) · 기기 저장소만 사용`);
 }
 async function refresh({onlySymbol=null,manual=false}={}){
   if(window.NOVA_PREVIEW_DATA){setStatus('오프라인 체험판에서는 실제 시세 평가를 지원하지 않습니다.');return;}
   if(busy)return;
   const candidates=journal.filter(x=>x.source==='kis' && x.mode==='스윙' && (!onlySymbol||x.symbol===onlySymbol));
   if(!candidates.length){render();setStatus('평가할 KIS 기록 없음 · 기존 Yahoo 기록은 보존합니다.');return;}
   if(!manual && onlySymbol && Date.now()-(lastRefresh.get(onlySymbol)||0)<10*60*1000)return;
   busy=true;
   const button=$('journalRefresh');if(button)button.disabled=true;
   let evaluated=0,failures=0;
   try{
     // Render free-tier and data-provider limits: 4 symbols per request, max 60 records.
     const batches=[];
     const groups=new Map();
     for(const r of candidates){
       const key=r.symbol+'|'+r.source;
       if(!groups.has(key))groups.set(key,[]);
       groups.get(key).push(r);
     }
     let current=[],count=0;
     for(const group of groups.values()){
       for(let i=0;i<group.length;i+=60){
         const piece=group.slice(i,i+60);
         if(current.length && (count>=4 || current.length+piece.length>60)){
           batches.push(current);current=[];count=0;
         }
         current.push(...piece);count++;
       }
     }
     if(current.length)batches.push(current);
     for(const rows of batches){
       setStatus(`미래 종가 확인 중 · ${evaluated}건 처리 · 조회 중...`);
       const token=(()=>{try{return sessionStorage.getItem('nova3-token')||'';}catch{return '';}})();
       const response=await fetch('/api/journal/evaluate',{method:'POST',credentials:'same-origin',cache:'no-store',
         headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},
         body:JSON.stringify({records:rows.map(x=>({id:x.id,symbol:x.symbol,source:x.source,mode:x.mode,
           bar_day:x.bar_day,reference:x.reference,technical:x.technical,filtered:x.filtered,gate:x.gate,cost_bps:x.cost_bps}))})});
       if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:`HTTP ${response.status}`);}
       const body=await response.json();
       for(const result of body.results||[]){
         const rec=journal.find(x=>x.id===result.id);
         if(!rec)continue;
         // Closed horizons remain visible if the provider has a temporary outage.
         for(const [h,value] of Object.entries(result.horizons||{})){
           if(value.status==='complete' || !rec.outcomes[h] || rec.outcomes[h].status!=='complete')rec.outcomes[h]=value;
         }
         rec.last_checked_at=new Date().toISOString();evaluated++;
         if(result.error)failures++;
       }
       if(!save())throw new Error('휴대폰 저장 공간이 부족합니다. 먼저 JSON 백업을 다운로드하세요.');
       render();
     }
     if(onlySymbol)lastRefresh.set(onlySymbol,Date.now());
     setStatus(`평가 확인 ${evaluated}건 · 데이터 조회 실패 ${failures}건 · 앱 실행 중에만 갱신됩니다. 기존 Yahoo 기록은 보존합니다.`);
   }catch(e){setStatus(`미래 종가 조회 실패: ${e.message}. 기기에 저장된 이전 평가 결과는 유지됩니다.`);}
   finally{busy=false;if(button)button.disabled=false;}
 }
 function csvCell(t){return '"'+String(t??'').replace(/"/g,'""')+'"';}
 function download(name,content,type){
   const blob=new Blob([content],{type});const a=document.createElement('a');const url=URL.createObjectURL(blob);
   a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
 }
 function exportCsv(){
   const rows=[['signal_date','symbol','mode','source','technical','filtered','gate','reference','cost_bps','horizon_days','status','settlement_date','settlement_close','asset_change_pct','directional_hit','directional_score_pct','hypothetical_buy_net_pct','buy_close_path_drawdown_pct']];
   for(const r of journal)for(const h of HORIZONS){const x=r.outcomes?.[String(h)]||{};
     rows.push([r.bar_day,r.symbol,r.mode,r.source,r.technical,r.filtered,r.gate,r.reference,r.cost_bps,h,x.status||'pending',x.settlement_day,x.settlement_close,x.asset_change_pct,x.direction_hit,x.directional_score_pct,x.buy_net_pct,r.technical==='BUY'?x.close_path_drawdown_pct:'']);
   }
   download('NOVA_v35_signal_results.csv','\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\n'),'text/csv;charset=utf-8');
 }
 function exportJson(){
   download('NOVA_v35_journal_backup.json',JSON.stringify({version:'3.5',exported_at:new Date().toISOString(),records:journal},null,2),'application/json;charset=utf-8');
 }
 async function importJson(file){
   if(!file)return;
   if(file.size>2_000_000){setStatus('백업 파일이 2MB를 초과합니다.');return;}
   try{
     const obj=JSON.parse(await file.text());
     if(obj?.version!=='3.5'||!Array.isArray(obj.records)||obj.records.length>MAX_LOCAL_RECORDS)throw new Error('올바른 NOVA v3.5 백업 파일이 아닙니다.');
     const rows=obj.records.filter(validRecord).map(normalize);
     const ids=new Map(journal.map(x=>[x.id,x]));
     for(const row of rows)ids.set(row.id,row);
     if(ids.size>MAX_LOCAL_RECORDS)throw new Error('기록이 800건을 넘습니다. 먼저 기록을 정리하세요.');
     if(!window.confirm(`유효한 기록 ${rows.length}건을 이 기기의 기존 기록과 합칠까요?`))return;
     journal=[...ids.values()];if(!save())throw new Error('저장공간이 부족합니다.');render();
   }catch(e){setStatus('백업 불러오기 실패: '+e.message);}
 }
 async function initEvidence(){
   const status=$('evidenceStatus'),button=$('evidenceReportButton'),out=$('evidenceSummary');
   if(!status||!button||!out)return;
   if(window.NOVA_PREVIEW_DATA){status.textContent='오프라인 데모에서는 서버 실전 성적표를 사용할 수 없습니다.';return;}
   const auth=()=>{let t='';try{t=sessionStorage.getItem('nova3-token')||'';}catch{}return t?{'Authorization':'Bearer '+t}:{}};
   try{
     const response=await fetch('/api/evidence/status',{credentials:'same-origin',cache:'no-store',headers:auth()});
     const d=await response.json().catch(()=>({}));
     if(!response.ok)throw new Error(typeof d.detail==='string'?d.detail:`HTTP ${response.status}`);
     if(!d.enabled){status.textContent='서버 영구 DB 미연결 · 기존 기기 신호 기록은 그대로 유지됩니다.';return;}
     if(d.storage_error){status.textContent='서버 DB 연결 실패 · 새 신호 보관 보장 불가';return;}
     status.textContent=`서버 실제 관찰 신호 ${d.total}건 · ${d.storage} · 기존 기기 기록과 별개`;
     button.disabled=d.total===0;
   }catch(e){status.textContent=`서버 상태 조회 실패 · ${e.message}`;}
   button.onclick=async()=>{
     button.disabled=true;out.replaceChildren();status.textContent='완료된 일봉으로 성적표 계산 중…';
     try{
       const response=await fetch('/api/evidence/report',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{...auth(),'Content-Type':'application/json'},body:JSON.stringify({limit:60})});
       const d=await response.json().catch(()=>({}));
       if(!response.ok)throw new Error(typeof d.detail==='string'?d.detail:`HTTP ${response.status}`);
       if(!d.count){status.textContent=d.note||'서버 관찰 기록이 없습니다.';return;}
       const add=(label,value)=>{const c=document.createElement('div');c.className='journal-metric';
         const a=document.createElement('span'),b=document.createElement('strong');a.textContent=label;b.textContent=value;c.append(a,b);out.append(c);};
       status.textContent=`서버 관찰 ${d.count}/${d.total}건 평가 · 최대 4개 종목/공급원 · 사용자가 조회한 종목 표본`;
       const five=d.horizons?.['5']||{};
       add('5거래일 방향 적중률',five.direction_hit_pct==null?'미확인':`${five.direction_hit_pct}% · ${five.complete}건`);
       add('5거래일 매수 순변화',five.mean_buy_net_pct==null?'미확인':pct(five.mean_buy_net_pct));
       add('가상투자 완료 거래',`${d.paper?.trades_count??0}건`);
       add('가상투자 연구 수익률',d.paper?.trades_count?pct(d.paper.cumulative_net_pct):'미확인');
       const note=$('evidenceNote');if(note)note.textContent=`주의: ${d.note} 가상거래는 신호 관찰 다음 거래일 시가에 진입, 5거래일 종가에 청산한다고 가정한 연구 결과입니다. 청산 시점만 평가하는 낙폭은 실제 MDD가 아닙니다. 표본 제외 ${d.excluded_by_four_source_limit||0}건 · 미완료 ${d.paper?.pending||0}건.`;
     }catch(e){status.textContent=`서버 성적표 조회 실패 · ${e.message}`;}
     finally{button.disabled=false;}
   };
 }

 async function initAutoEvidence(){
   const status=$('autoEvidenceStatus'),button=$('autoEvidenceButton'),out=$('autoEvidenceSummary');
   if(!status||!button||!out)return;
   if(window.NOVA_PREVIEW_DATA){status.textContent='오프라인 데모에서는 자동 검증을 조회할 수 없습니다.';return;}
   const auth=()=>{let t='';try{t=sessionStorage.getItem('nova3-token')||'';}catch{}return t?{'Authorization':'Bearer '+t}:{};};
   try{
     const resp=await fetch('/api/autotrack/status',{credentials:'same-origin',cache:'no-store',headers:auth()});
     const d=await resp.json().catch(()=>({}));
     if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
     if(d.storage_error){status.textContent='자동 검증 DB 조회 오류 · 기존 기록은 보존됩니다.';return;}
     const schedule=d.configured?'서버 예약 실행 허용':'예약 실행 미설정';
     status.textContent=`${schedule} · 예약 실행 최근 ${d.recent_auto_attempts||0}회 (실패·부분 ${d.recent_attempt_failures||0}회) · 검색 기록 ${d.scan_runs||0}회 · 후보 ${d.candidates||0}건 · 완료 기간 평가 ${d.completed_horizons||0}건 · 마지막 시장 검색 ${d.latest_scan?.observed_at||'없음'}`;
     button.disabled=!d.database_connected;
   }catch(e){status.textContent=`자동 검증 상태 확인 실패 · ${e.message}`;}
   button.onclick=async()=>{
     button.disabled=true;out.replaceChildren();status.textContent='저장된 시장 검색 성적표 조회 중…';
     try{
       const resp=await fetch('/api/autotrack/report',{credentials:'same-origin',cache:'no-store',headers:auth()});
       const d=await resp.json().catch(()=>({}));
       if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
       const metric=(label,value)=>{const c=document.createElement('div');c.className='journal-metric';
         const a=document.createElement('span'),b=document.createElement('strong');a.textContent=label;b.textContent=value;c.append(a,b);out.append(c);};
       const names={'1':'1위','2-3':'2~3위','4-10':'4~10위','11+':'11위 이하'};
       const groups=(d.ranking||[]).filter(x=>x.lane==='STRICT_BUY');
       metric('저장된 후보 수',`${d.total_candidates||0}건 (관심 후보 포함)`);
       metric('가상 매매 완료',`${d.paper?.trades_count||0}건`);
       metric('가상 누적 순수익률',d.paper?.trades_count?pct(d.paper.cumulative_net_pct):'미확인');
       metric('가상 청산일 MDD',d.paper?.trades_count?pct(d.paper.realized_exit_only_mdd_pct):'미확인');
       for(const g of groups){const five=g.horizons?.['5']||{};
         metric(`엄격 매수 ${names[g.rank_group]||g.rank_group} · ${g.candidates}건`,
           five.complete?`${five.direction_hit_pct}% · ${five.complete}건 · 평균 가상 순변화 ${pct(five.average_net_long_pct)}`:'아직 5거래일 평가 없음');}
       status.textContent=`시장 검색 ${d.scan_runs_visible||0}회 기록 · 관찰된 엄격 매수만 적중률 계산`;
       const note=$('autoEvidenceNote');if(note)note.textContent=d.note||'';
     }catch(e){status.textContent=`자동 추천 성적 조회 실패 · ${e.message}`;}
     finally{button.disabled=false;}
   };
 }


 let regimeButtonInitialized=false;
 async function initRegimeResearch(){
   const status=$('regimeResearchStatus'),button=$('regimeResearchButton');
   const summary=$('regimeResearchSummary'),rows=$('regimeResearchRows'),note=$('regimeResearchNote');
   if(!status||!button||!summary||!rows||regimeButtonInitialized)return;
   regimeButtonInitialized=true;
   if(window.NOVA_PREVIEW_DATA){button.disabled=true;status.textContent='오프라인 데모에서는 조회할 수 없습니다.';return;}
   const auth=()=>{let token='';try{token=sessionStorage.getItem('nova3-token')||'';}catch{}
     return token?{'Authorization':'Bearer '+token}:{};};
   const metric=(label,value)=>{const box=document.createElement('div');box.className='journal-metric';
     const caption=document.createElement('span');caption.textContent=label;
     const val=document.createElement('strong');val.textContent=value;box.append(caption,val);summary.append(box);};
   const sub=(title,detail)=>{const box=document.createElement('div');box.className='regime-result';
     const strong=document.createElement('strong');strong.textContent=title;
     const small=document.createElement('small');small.textContent=detail;box.append(strong,small);rows.append(box);};
   const trend={UP:'상승',DOWN:'하락',SIDEWAYS:'횡보'};
   const vol={HIGH:'고변동',NORMAL:'보통 변동'};
   const originalLabel=button.textContent;
   button.onclick=async()=>{
     button.disabled=true;button.textContent='조회 중…';summary.replaceChildren();rows.replaceChildren();
     status.textContent='시장 상황별 연구 서버에 요청 중…';
     try{
       const resp=await fetch('/api/research/market-regimes',{cache:'no-store',credentials:'same-origin',headers:auth()});
       const data=await resp.json().catch(()=>({}));
       if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
       const cnt=Number(data.total_candidates||0),valid=Number(data.context_recorded||0);
       status.textContent=`누적 후보 ${cnt}건 중 국면 확인 ${valid}건 · 연구 전용 · 실제 신호 변경 없음`;
       metric('국면 기록 완료',`${valid}건`);
       metric('기존 미분류 기록',`${data.legacy_unrecorded||0}건`);
       metric('지수 수집 불가',`${data.provider_unavailable||0}건`);
       const all=(data.regime_groups||[]).filter(x=>x.horizon===5&&x.lane==='STRICT_BUY');
       metric('5거래일 완료 표본',`${all.reduce((sum,x)=>sum+Number(x.completed||0),0)}건`);
       const snapshots=(data.latest_market_snapshots||[]);
       const newest=snapshots.find(x=>x.regimes_recorded);
       if(newest){
         const codes={'^KS11':'코스피','^KQ11':'코스닥'};
         const states=newest.market_regimes||{};
         for(const key of ['^KS11','^KQ11']){
           const item=states[key]||{};
           const desc=item.status==='READY'
              ?`${trend[item.trend]||'미확인'} · ${vol[item.volatility]||'미확인'} · 지수 기준 ${item.asof||'미확인'}`
              :`수집 미확인 (${item.reason||'출처 확인 필요'})`;
           sub(`마지막 기록 ${codes[key]} 시장 국면`,desc+` · 저장시각 ${newest.observed_at||'미확인'}`);
         }
       }else if(snapshots.length){sub('기존 기록에는 국면이 없습니다','현재 관찰 시각에 확인된 지수 자료가 없어 과거 결과를 소급 분류하지 않았습니다.');}
       if(!valid){sub('연구 표본 대기','v4.7 이후 새로 관찰되는 추천만 당시 시장 상황이 함께 기록됩니다. 기존 기록은 소급 분류하지 않습니다.');}
       for(const x of all){
         const label=`${x.name||x.benchmark} · ${trend[x.trend]||'미확인'} / ${vol[x.volatility]||'미확인'}`;
         const complete=`5일 완료 ${x.completed}건 · 관찰일 ${x.unique_days}일`;
         const performance=` · 매수 순변화 ${x.mean_net_long_pct==null?'미확인':pct(x.mean_net_long_pct)}`;
         const certainty=x.statistical_confidence==='INSUFFICIENT'?' · 표본 부족(판단 보류)':' · 기술 통계(확률 아님)';
         sub(label,complete+performance+certainty);
       }
       const trial=data.shadow_policy_5d||{};
       if(trial.status==='RESEARCH_ONLY'){
         const test=trial.later||{};
         sub('연구 가정: 하락장·고변동 시 매수 보류',
           `최근 별도 기간 ${test.completed||0}건 · 보류 ${test.skipped||0}건 · 가상 평균 순변화 차이 ${test.difference_pp==null?'미확인':Number(test.difference_pp).toFixed(3)+'%p'} · 실거래 신호 미반영`);
       }else sub('시장 상황별 가상 필터 검증 대기','독립된 관찰일 12일과 사후평가 완료 매수 30건 이상이 필요합니다.');
       if(note)note.textContent=data.limitations||'시장 국면 분석은 규칙 기반 연구이며 매수 신호를 변경하지 않습니다.';
     }catch(err){status.textContent=`시장 상황 연구 조회 실패 · ${err.message}`;}
     finally{button.disabled=false;button.textContent=originalLabel;}
   };
   // Independent fallback: app.js initialization may stop before journal.init().
   status.textContent='연구 결과 조회 준비 완료 · 버튼을 누르면 서버에서 결과를 불러옵니다.';
   // Load once on first entry into Backtest. The button remains available for manual retry.
   let firstOpen=true;
   document.addEventListener('click',(event)=>{
     if(!firstOpen)return;
     if(event.target.closest('[data-view="backtest"], [data-guide-target="backtest"]')){
       firstOpen=false;
       if(!button.disabled)button.click();
     }
   });
 }

 function init(){
   load();render();void initEvidence();void initAutoEvidence();void initRegimeResearch();
   $('journalModeFilter')?.addEventListener('change',render);
   $('journalRefresh')?.addEventListener('click',()=>refresh({manual:true}));
   $('journalExportCsv')?.addEventListener('click',exportCsv);
   $('journalBackup')?.addEventListener('click',exportJson);
   $('journalRestore')?.addEventListener('change',e=>{importJson(e.target.files?.[0]);e.target.value='';});
   $('journalClear')?.addEventListener('click',()=>{if(window.confirm('이 휴대폰에 저장된 모든 NOVA 신호 기록을 삭제할까요? 복원은 JSON 백업만 가능합니다.')){journal=[];save();render();}});
   // Reconcile on return; NO server cron or push and no recording of missing past signals.
   document.addEventListener('visibilitychange',()=>{if(!document.hidden && journal.length)refresh({manual:false});});
 }
 window.NOVAJournal={init,record,updateFinance,refresh,render};
 // Install the research button even when the main application fails to initialize.
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{void initRegimeResearch();},{once:true});
 else void initRegimeResearch();
})();
