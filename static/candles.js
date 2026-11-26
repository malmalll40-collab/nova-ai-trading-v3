/* NOVA v4.1 — Self-contained OHLC canvas, exchange-calendar W/M/Y bars.
 * Chart timeframe is READ-ONLY and is independent of the trading signal mode.
 * Public API data are never written to browser storage; only the user's
 * timeframe selection is remembered. No CDN or remote JS dependencies.
 */
'use strict';
(()=>{
 let base=null,current=null,mode='candles',visible=65,offset=0,hovered=null;
 let dragging=null,pinch=null,bound=false,request=null,serial=0;
 let liveTimer=null,liveController=null,livePending=false;
 let researchLookup=new Map();
 let lastSessionPhase=null,analysisPhase=null;
 // KIS is the default *display* source. The independent analysis
 // provider (and therefore trading signal computation) remains unchanged.
 let provider='kis';
 const recent=new Map();
 const byId=id=>document.getElementById(id);
 const clamp=(x,min,max)=>Math.max(min,Math.min(max,x));
 const fmt=n=>Number(n).toLocaleString('ko-KR',{maximumFractionDigits:Math.abs(n)<100?2:0});
 const timeframeLabel={'analysis':'분석 주기','1d':'일봉','1w':'주봉','1mo':'월봉','1y':'연봉'};
 const periods=new Set(Object.keys(timeframeLabel));
 let timeframe='1d';
 // One-time migration: an existing default of 'analysis' or '기존 공급원'
 // should not prevent KIS from appearing automatically on first launch.
 // After migration, honour any manual choices across subsequent visits.
 try {
   const providerKey='nova4-chart-provider', periodKey='nova4-chart-timeframe';
   const migrationKey='nova45-kis-chart-default-v1';
   const oldProvider=localStorage.getItem(providerKey);
   const oldPeriod=localStorage.getItem(periodKey);
   if(localStorage.getItem(migrationKey)==='1'){
     if(oldProvider==='analysis'||oldProvider==='kis')provider=oldProvider;
     if(periods.has(oldPeriod))timeframe=oldPeriod;
   }else{
     if(periods.has(oldPeriod)&&oldPeriod!=='analysis')timeframe=oldPeriod;
     localStorage.setItem(providerKey,'kis');
     localStorage.setItem(periodKey,timeframe);
     localStorage.setItem(migrationKey,'1');
   }
 }catch{}
 function valid(data){
   if(!data?.chart)return false;
   const x=data.chart,n=x.dates?.length||0;
   return n>=2 && ['open','high','low','close','volume'].every(k=>Array.isArray(x[k])&&x[k].length===n)
       && x.close.every((v,i)=>Number.isFinite(v)&&v>0&&Number.isFinite(x.open[i])&&Number.isFinite(x.high[i])&&Number.isFinite(x.low[i])
                    && Number.isFinite(x.volume[i])&&x.volume[i]>=0&&x.low[i]<=Math.min(v,x.open[i])&&x.high[i]>=Math.max(v,x.open[i]));
 }
 function setStatus(str,type=''){
   const el=byId('candleStatus');if(!el)return;
   el.textContent=str;el.className='candle-status '+type;
 }
 function liveStatus(message,variant=''){
   const el=byId('candleLiveStatus');if(!el)return;
   el.textContent=message;
   el.className='candle-live-status '+variant;
 }
 function stopLive(){
   if(liveTimer!==null){clearTimeout(liveTimer);liveTimer=null;}
   if(liveController){liveController.abort();liveController=null;}
   livePending=false;
 }
 const getAuth=()=>{const headers={};try{const token=sessionStorage.getItem('nova3-token')||'';if(token)headers.Authorization='Bearer '+token;}catch{}return headers;};
 // Server-side KRX calendar prevents holiday/exam-day false live polling.
 // Unknown means unknown, not a presumed trading day.
 let sessionCache=null,sessionAt=0;
 async function marketSession(){
   if(sessionCache && Date.now()-sessionAt<45_000)return sessionCache;
   try{
     const response=await fetch('/api/market/session', {headers:getAuth(),credentials:'same-origin',cache:'no-store'});
     if(!response.ok)throw new Error(`HTTP ${response.status}`);
     const data=await response.json();
     sessionCache=data;sessionAt=Date.now();return data;
   }catch{
     return {status:'UNKNOWN',is_open:false,reason:'거래소 운영일 확인 실패 · 자동 갱신 보류'};
   }
 }
 function updateEmaOnCurrent(x,i){
   for(const [key,span] of [['ema20',20],['ema50',50]]){
     if(!Array.isArray(x[key]))continue;
     const prior=i>0?x[key][i-1]:null;
     x[key][i]=prior!=null&&Number.isFinite(prior)?x.close[i]*(2/(span+1))+prior*(1-2/(span+1)):null;
   }
 }
 function applySnapshot(res){
   if(!res?.available||!res.bar||!current||!valid(current))return false;
   const x=current.chart, i=x.dates.length-1, day=String(res.date||'');
   if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<x.dates[i])return false;
   const b=res.bar;
   if(!['open','high','low','close','volume'].every(k=>Number.isFinite(b[k]))||b.open<=0||b.close<=0||b.volume<=0||b.low<=0||b.high<Math.max(b.open,b.close)||b.low>Math.min(b.open,b.close))return false;
   if(day===x.dates[i]&&timeframe==='1d'&&!x.partial?.[i])return false; // Never overwrite a completed regular daily candle with a quote snapshot.
   if(day===x.dates[i]&&x.partial?.[i]&&Number(x.volume[i])>b.volume)return false;
   if(day>x.dates[i]){
     x.dates.push(day);
     for(const k of ['open','high','low','close','volume'])x[k].push(b[k]);
     if(!Array.isArray(x.partial))x.partial=Array(x.dates.length-1).fill(false);
     x.partial.push(true);
     for(const k of ['ema20','ema50'])if(Array.isArray(x[k]))x[k].push(null);
     if(x.dates.length>240){for(const k of ['dates','open','high','low','close','volume','partial','ema20','ema50'])if(Array.isArray(x[k]))x[k].shift();}
   }else{
     for(const k of ['open','high','low','close','volume'])x[k][i]=b[k];
     if(!Array.isArray(x.partial))x.partial=Array(x.dates.length).fill(false);
     x.partial[i]=true;
   }
   updateEmaOnCurrent(x,x.dates.length-1);
   if(byId('chartEnd'))byId('chartEnd').textContent=`${timeframeLabel[timeframe]} · ${periodStamp(x.dates[x.dates.length-1])}`;
   // Do not reset zoom, drag position or the user's currently hovered candle.
   if(valid(current))requestAnimationFrame(render);
   return true;
 }
 function scheduleLive(id,ms=20000){
   if(serial!==id||!base||document.hidden||window.NOVA_PREVIEW_DATA)return;
   if(liveTimer!==null)clearTimeout(liveTimer);
   liveTimer=setTimeout(()=>{liveTimer=null;void pollLive(id);},ms);
 }
 function analysisKey(stamp){const t=String(stamp||'').replace(' ','T');return base?.mode==='스윙'?t.slice(0,10):t.slice(0,16);}
 function applyAnalysisRows(res){
   if(!current||!valid(current)||!base||res?.symbol!==base.symbol||res?.mode!==base.mode||!Array.isArray(res.rows))return false;
   const x=current.chart,cut=analysisKey(base.chart.dates.at(-1)),day=String(res.retrieved_at||'').slice(0,10);
   if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return false;
   let changed=false;
   if(!Array.isArray(x.partial))x.partial=Array(x.dates.length).fill(false);
   if(!Array.isArray(x.display_only))x.display_only=Array(x.dates.length).fill(false);
   const keys=['open','high','low','close','volume'];
   for(const r of [...res.rows].sort((a,b)=>String(a.date).localeCompare(String(b.date)))){
     const k=analysisKey(r.date),b=r.bar;
     if(k<=cut||k.slice(0,10)!==day||!b||!keys.every(a=>Number.isFinite(b[a]))||Math.min(b.open,b.high,b.low,b.close,b.volume)<=0||b.high<Math.max(b.open,b.close)||b.low>Math.min(b.open,b.close))continue;
     let i=x.dates.findIndex(v=>analysisKey(v)===k);
     if(i>=0){if(!x.display_only[i]||x.volume[i]>b.volume||(!x.partial[i]&&r.partial))continue;}
     else {i=x.dates.findIndex(v=>analysisKey(v)>k);if(i<0)i=x.dates.length;x.dates.splice(i,0,r.date);x.display_only.splice(i,0,true);x.partial.splice(i,0,true);for(const a of [...keys,'ema20','ema50'])if(Array.isArray(x[a]))x[a].splice(i,0,null);}
     for(const a of keys)x[a][i]=b[a];x.partial[i]=!!r.partial;changed=true;
   }
   if(changed){for(let i=0;i<x.dates.length;i++)if(x.display_only[i])updateEmaOnCurrent(x,i);
     if(byId('chartEnd'))byId('chartEnd').textContent=`분석 주기 · ${periodStamp(x.dates.at(-1))}`;
     requestAnimationFrame(render);}
   return changed;
 }
 async function pollAnalysis(id){
   if(serial!==id||document.hidden||!base||base.source!=='kis'||window.NOVA_PREVIEW_DATA)return;
   const session=await marketSession();if(serial!==id||document.hidden||!base)return;
   const stamp=String(session.as_of||'');const phase=stamp.slice(0,10)+'/'+(session.is_open?'open':session.status==='UNKNOWN'?'unknown':stamp.slice(11,16)>='18:00'?'settled':'closed');
   if(!session.is_open&&session.status!=='UNKNOWN'&&analysisPhase===phase){scheduleLive(id,60000);return;}
   if(livePending){scheduleLive(id);return;}
   livePending=true;const ctrl=new AbortController();liveController=ctrl;
   const timeout=setTimeout(()=>ctrl.abort(),38000);let delay=20000;
   try{
     const qs=new URLSearchParams({symbol:base.symbol,mode:base.mode});
     const response=await fetch('/api/chart/analysis-live?'+qs,{headers:getAuth(),credentials:'same-origin',cache:'no-store',signal:ctrl.signal});
     if(!response.ok)throw new Error(`HTTP ${response.status}`);
     const data=await response.json();if(serial!==id||ctrl.signal.aborted)return;
     const updated=applyAnalysisRows(data);analysisPhase=phase;
     liveStatus(`${updated?'● KIS 차트 전용 봉 갱신':'KIS 당일 추가 봉 미확인 또는 동일 봉'} · AI 완료봉 ${periodStamp(base.chart.dates.at(-1),true)} 유지${data.calendar_verified===false?' · 거래일 달력 미확인':''}`,updated?'active':'unavailable');
     if(!session.is_open&&session.status!=='UNKNOWN')delay=60000;
   }catch(e){if(serial===id&&!ctrl.signal.aborted)liveStatus(`분석주기 차트 조회 실패 · ${e.message} · 기존 봉 유지`,'unavailable');delay=60000;}
   finally{clearTimeout(timeout);if(liveController===ctrl){liveController=null;livePending=false;}if(serial===id&&!liveController)scheduleLive(id,delay);}
 }
 async function pollLive(id){
   if(timeframe==='analysis'){await pollAnalysis(id);return;}
   if(serial!==id||document.hidden||!base||timeframe==='analysis'||(provider!=='kis'&&base.source!=='kis'))return;
   const session=await marketSession();
   if(serial!==id||document.hidden||!base)return;
   if(!session.is_open&&session.status!=='UNKNOWN'){
     const stamp=String(session.as_of||'');
     const phase=stamp.slice(0,10)+'/'+(stamp.slice(11,16)>='18:00'?'settled':session.close_kst&&stamp.slice(11,16)>=session.close_kst?'postclose':'closed');
     if(timeframe==='1d'&&session.calendar_verified&&lastSessionPhase!==null&&lastSessionPhase!==phase){
       lastSessionPhase=phase;
       void selectPeriod(true);return;
     }
     if(session.calendar_verified)lastSessionPhase=phase;
     liveStatus(`${session.reason||'정규장 종료'} · KRX 정규장 자동 갱신 대기 · 마지막 확인 봉 유지`);
     scheduleLive(id,60000);return;
   }
   if(session.is_open)lastSessionPhase=String(session.as_of||'').slice(0,10)+'/open';
   if(livePending){scheduleLive(id);return;}
   livePending=true;
   const ctrl=new AbortController();liveController=ctrl;
   const timeout=setTimeout(()=>ctrl.abort(),38000);
   liveStatus(`${timeframeLabel[timeframe]} KIS 장중 봉 조회 중 · 20초 REST 갱신`, 'pending');
   let needsRetry=true;
   try{
     const qs=new URLSearchParams({symbol:base.symbol,timeframe});
     const resp=await fetch('/api/chart/live?'+qs,{headers:getAuth(),cache:'no-store',credentials:'same-origin',signal:ctrl.signal});
     const json=await resp.json().catch(()=>({}));
     if(!resp.ok)throw new Error(typeof json.detail==='string'?json.detail:`HTTP ${resp.status}`);
     if(serial!==id||ctrl.signal.aborted)return;
     const stamp=String(json.retrieved_at||'').replace('T',' ').slice(0,19);
     if(json.available&&applySnapshot(json)){
       liveStatus(`● KIS REST 장중 봉 갱신 · ${stamp} KST · 20초 간격 · WebSocket 아님${json.calendar_verified===false?' · 거래일 달력 미확인(차트 전용)':''}`, 'active');
     }else{
       liveStatus(`${json.reason||'새로운 현재 봉 미확인'} · 최근 조회 ${stamp||'미확인'} · 과거 봉 유지`, 'unavailable');
     }
   }catch(e){
     if(serial===id&&!ctrl.signal.aborted){
       liveStatus(`KIS 장중 연결 실패 · ${e.message} · 시세 최신성 미확인`,'unavailable');
       // Bad credentials and revoked permissions are not retried every 20s.
       if(/HTTP (401|403|503)|앱 키가 미설정/.test(e.message))needsRetry=false;
     }
   }finally{
     clearTimeout(timeout);
     if(liveController===ctrl){liveController=null;livePending=false;}
     if(needsRetry&&serial===id&&(!liveController))scheduleLive(id);
   }
 }
 function startLive(id){
   if(!base||(timeframe==='analysis'?base.source!=='kis':provider!=='kis'&&base.source!=='kis')||window.NOVA_PREVIEW_DATA){
     liveStatus('기존 과거봉 표시 · KIS 연결을 선택하면 장중 20초 REST 갱신');return;
   }
   void pollLive(id);
 }
 function getResearchRows(){
   const r=base?.candle_research;
   return r?.status==='READY'&&Array.isArray(r.observations)?r.observations:[];
 }
 function researchReady(){return base?.mode==='스윙' && ['analysis','1d'].includes(timeframe) && current?.chart && getResearchRows().length>0;}
 function matchedResearch(x,i){
   if(!researchReady()||x.partial?.[i]||x.display_only?.[i])return null;
   const day=chartDate(x.dates[i]),r=researchLookup.get(day),b=base.chart;
   if(!r||r.completed!==true||!Number.isFinite(r.price_close))return null;
   const idx=b.dates.findIndex(t=>chartDate(t)===day);
   if(idx<0||b.partial?.[idx]||b.display_only?.[idx])return null;
   const same=(a,z)=>Number.isFinite(a)&&Number.isFinite(z)&&Math.abs(a-z)<=Math.max(1e-6,Math.abs(z)*1e-8);
   if(!['open','high','low','close','volume'].every(key=>same(x[key]?.[i],b[key]?.[idx]))||!same(r.price_close,b.close[idx]))return null;
   return r;
 }
 function chartDate(s){return String(s||'').slice(0,10);}
 function makeResearchElement(tag,klass,text){
   const el=document.createElement(tag);if(klass)el.className=klass;
   if(text!==undefined)el.textContent=text;return el;
 }
 function resetResearch(){
   researchLookup=new Map();
   const rows=getResearchRows();
   for(const o of rows)researchLookup.set(o.date,o);
   const sel=byId('candleResearchDate');if(sel){
     sel.replaceChildren();
     sel.append(makeResearchElement('option','',rows.length?'차트에서 날짜를 선택하세요':'분석 자료 없음'));
     sel.firstChild.value='';
     for(const o of [...rows].reverse()){
       const option=makeResearchElement('option','',`${o.date} · 13역할 ${o.baseline==='BUY'?'매수':o.baseline==='SELL'?'매도':'관망'}${o.five_core==='BUY'?' / 5-Core 매수':''}`);
       option.value=o.date;sel.append(option);
     }
   }
   const status=byId('candleResearchStatus');
   const r=base?.candle_research;
   if(status)status.textContent=r?.status==='READY'?
     `과거 완료 일봉 ${rows.length}개 분석 · 분석 주기와 일봉에서 동일 완료봉(OHLCV 일치)에만 신호를 표시합니다. 5-Core 시장지수 ${r.market_reference_status==='READY'?'조회 가능':'미확인'}.`:
     `과거 캔들 연구 ${r?.reason||'자료가 아직 없습니다.'}`;
   const roles=byId('candleResearchRoleStats');if(roles){roles.replaceChildren();
     for(const a of r?.role_statistics||[]){
       const item=makeResearchElement('div','candle-role-item');
       const strong=makeResearchElement('strong','',a.role);
       const note=a.kind==='risk_veto'?'위험 차단 역할 · 적중률 산출 대상 아님':
         `${a.kind==='derived'?'재집계 역할 · ':''}매수 ${a.buy_count}회 · 매도 ${a.sell_count}회 · 평가 ${a.directional_samples}회 · ${a.directional_hit_pct==null?'표본 부족':a.directional_hit_pct.toFixed(1)+'% 방향 적중'}`;
       item.append(strong,makeResearchElement('span','',note));roles.append(item);
     }
   }
   showResearch(null);
 }
 function showResearch(date){
   const result=byId('candleResearchResult'),votes=byId('candleResearchVotes');
   const status=byId('candleResearchStatus'),select=byId('candleResearchDate');
   if(!result||!votes)return;
   result.replaceChildren();votes.replaceChildren();result.hidden=true;
   if(!researchReady()){
     if(status&&base?.candle_research?.status==='READY')status.textContent=`분석 완료봉의 신호만 검증합니다. '과거 신호 보기'를 눌러 주세요. ${getResearchRows().length}개 기록 준비됨.`;
     if(select)select.disabled=true;
     return;
   }
   if(select)select.disabled=false;
   const idx=current.chart.dates.findIndex(x=>chartDate(x)===date);
   const r=idx<0?null:matchedResearch(current.chart,idx);
   if(!r){if(status)status.textContent='분석 주기 또는 일봉의 일치하는 완료봉을 선택하세요. 봉 자료가 다르면 신호를 표시하지 않습니다.';return;}
   if(select)select.value=r.date;
   if(status)status.textContent=`${r.date} 완료봉 · 당시 13개 역할 판단 및 연구용 5-Core 결과 (나중 가격 정보는 성과 평가에만 사용)`;
   const dir=s=>s==='BUY'?'매수':s==='SELL'?'매도':s==='HOLD'?'관망':'미확인';
   const heading=makeResearchElement('strong','',`${r.date} · 13역할 ${dir(r.baseline)} / 5-Core ${dir(r.five_core)}`);
   const k=makeResearchElement('p','','13역할: 매수 '+r.buy_votes+' · 매도 '+r.sell_votes+' · 관망 '+r.hold_votes+(r.risk_veto?' · 위험 차단 발동':''));
   const core=makeResearchElement('p','',r.five_core_status==='READY'?
      '5-Core 조건은 아래에서 확인할 수 있습니다.':'5-Core: 동일 날짜의 전일 시장지수 자료 미확인 · 신호 판단 보류');
   const move=r.outcome_status==='EVALUATED'&&typeof r.price_change_pct_t5==='number'?
      `이후 가격: 다음 거래일 시가 ${fmt(r.next_open)} → 5거래일 뒤 종가 ${fmt(r.exit_close_t5)} · ${r.price_change_pct_t5>0?'+':''}${r.price_change_pct_t5.toFixed(2)}% (수수료 제외 가격 변화)`:
      '5거래일 후 평가 대기 · 미래 결과를 임의 보충하지 않습니다.';
   const resultRow=makeResearchElement('p','',move);
   result.append(heading,k,core,resultRow);
   if(r.baseline==='BUY'&&typeof r.hypothetical_buy_net_pct_t5==='number')result.append(makeResearchElement('p','',`13역할 BUY 가상 1회 거래 순변화: ${r.hypothetical_buy_net_pct_t5>0?'+':''}${r.hypothetical_buy_net_pct_t5.toFixed(2)}% · 실제 주문 아님`));
   if(r.baseline==='SELL')result.append(makeResearchElement('p','',`13역할 SELL은 보유축소 검토이며 공매도 진입·이익으로 계산하지 않습니다.`));
   if(r.five_core_checks?.length){
     const box=makeResearchElement('div','candle-checks');
     for(const c of r.five_core_checks)box.append(makeResearchElement('span','',`${c.pass?'✓':'—'} ${c.label}: ${c.detail}`));
     result.append(box);
   }
   for(const v of r.vote_details||[]){
     const item=makeResearchElement('div','candle-role-item');
     const label=makeResearchElement('strong','',`${v.role} · ${dir(v.direction)}`);
     const reason=makeResearchElement('span','',v.reason+(v.kind==='derived'?' · 앞선 역할 결과 재집계':v.kind==='risk_veto'?' · 매수 차단용':' '));
     item.append(label,reason);votes.append(item);
   }
   result.hidden=false;
 }
 function chooseResearchDate(day){
   if(!researchReady())return;
   const idx=current.chart.dates.findIndex(x=>chartDate(x)===day);
   if(idx<0){showResearch(null);return;}
   const n=current.chart.dates.length;
   if(idx<n-offset-visible)offset=clamp(n-idx-visible,0,Math.max(0,n-visible));
   if(idx>=n-offset)offset=clamp(n-1-idx,0,Math.max(0,n-visible));
   hovered=idx;showResearch(day);render();
 }
 function blank(msg){
   current=null;hovered=null;
   resetResearch();
   const canvas=byId('candleCanvas');if(canvas){const ctx=canvas.getContext('2d');ctx?.clearRect(0,0,canvas.width,canvas.height);}
   if(byId('candleDetails'))byId('candleDetails').textContent=msg;
   if(byId('chartEnd'))byId('chartEnd').textContent='—';
 }
 function periodStamp(s,detail=false){
   const t=String(s||'').replace('T',' ');
   const selected=current?.chart_timeframe||'analysis';
   if(selected==='1y')return t.slice(0,4)+'년';
   if(selected==='1mo')return t.slice(0,7);
   if(selected==='1w')return t.slice(0,10)+(detail?' 시작 주':'');
   if(selected==='1d'||current?.mode==='스윙')return t.slice(0,10);
   return detail?t.slice(0,16):t.slice(5,16);
 }
 function range(){const x=current.chart,n=x.dates.length,cnt=Math.min(visible,n),end=Math.max(0,n-offset),start=Math.max(0,end-cnt);return {x,n,start,end,cnt:end-start};}
 function selectCandle(px){
   if(!current)return;
   const canvas=byId('candleCanvas'),{start,cnt}=range();if(!cnt)return;
   const left=10,right=64,width=Math.max(5,canvas.clientWidth-left-right);
   hovered=clamp(start+Math.floor((px-left)/width*cnt),start,start+cnt-1);render();
 }
 function install(){
   const canvas=byId('candleCanvas');if(!canvas||bound)return;
   bound=true;
   const tf=byId('candleTimeframe');if(tf){tf.value=timeframe;tf.addEventListener('change',e=>{
     timeframe=periods.has(e.target.value)?e.target.value:'analysis';
     try{localStorage.setItem('nova4-chart-timeframe',timeframe);}catch{}
     selectPeriod();
   });}
   const cp=byId('candleProvider');if(cp){cp.value=provider;cp.addEventListener('change',e=>{
     provider=e.target.value==='kis'?'kis':'analysis';
     try{localStorage.setItem('nova4-chart-provider',provider);}catch{}
     selectPeriod();
   });}
   document.addEventListener('visibilitychange',()=>{
     if(document.hidden){stopLive();return;}
     if(base&&(timeframe==='analysis'?base.source==='kis':provider==='kis'||base.source==='kis')){stopLive();startLive(serial);}
   });
   byId('candleMode').addEventListener('change',e=>{mode=e.target.value;render();});
   const jump=byId('candleResearchJump'),chartJump=byId('candleChartExactSignals');const jumpToSignals=()=>{
     document.querySelector('[data-view="analyze"]')?.click();
     timeframe='analysis';const el=byId('candleTimeframe');if(el)el.value='analysis';
     try{localStorage.setItem('nova4-chart-timeframe','analysis');}catch{}
     const researchToggle=byId('candleResearchOverlay'),priceToggle=byId('candleChartOverlay');if(researchToggle)researchToggle.checked=true;if(priceToggle)priceToggle.checked=true;
     void selectPeriod();
   };
   if(jump)jump.onclick=jumpToSignals;if(chartJump)chartJump.onclick=jumpToSignals;
   const toggle=byId('candleResearchOverlay'),chartToggle=byId('candleChartOverlay');
   if(toggle)toggle.addEventListener('change',()=>{if(chartToggle)chartToggle.checked=toggle.checked;render();});
   if(chartToggle)chartToggle.addEventListener('change',()=>{if(toggle)toggle.checked=chartToggle.checked;render();});
   const day=byId('candleResearchDate');if(day)day.addEventListener('change',e=>chooseResearchDate(e.target.value));

   byId('candleZoomIn').onclick=()=>{visible=clamp(Math.round(visible*.72),5,125);offset=clamp(offset,0,Math.max(0,(current?.chart?.dates.length||0)-visible));render();};
   byId('candleZoomOut').onclick=()=>{visible=clamp(Math.round(visible*1.4),5,125);offset=clamp(offset,0,Math.max(0,(current?.chart?.dates.length||0)-visible));render();};
   byId('candleReset').onclick=()=>{visible=65;offset=0;hovered=null;render();};
   canvas.addEventListener('keydown',e=>{
     if(!current)return;
     const n=current.chart.dates.length;
     if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
       e.preventDefault();const {start,end}=range();
       hovered=clamp((hovered==null?end-1:hovered)+(e.key==='ArrowRight'?1:-1),0,n-1);
       if(hovered<start)offset=clamp(offset+1,0,Math.max(0,n-visible));
       if(hovered>=end)offset=clamp(offset-1,0,Math.max(0,n-visible));
       render();
     }else if(e.key==='Home'){e.preventDefault();hovered=null;offset=0;render();}
   });
   canvas.addEventListener('wheel',e=>{e.preventDefault();visible=clamp(Math.round(visible*(e.deltaY>0?1.16:.86)),5,125);
     offset=clamp(offset,0,Math.max(0,(current?.chart?.dates.length||0)-visible));render();},{passive:false});
   canvas.addEventListener('pointerdown',e=>{if(e.pointerType==='touch')return;
     dragging={x:e.clientX,base:offset,moved:false};canvas.setPointerCapture?.(e.pointerId);});
   canvas.addEventListener('pointermove',e=>{
     if(!dragging||!current||e.pointerType==='touch')return;
     const dist=e.clientX-dragging.x;if(Math.abs(dist)>6)dragging.moved=true;
     offset=clamp(Math.round(dragging.base-dist*visible/(canvas.clientWidth||300)),0,Math.max(0,current.chart.dates.length-visible));render();
   });
   canvas.addEventListener('pointerup',e=>{if(dragging&&!dragging.moved)selectCandle(e.offsetX);dragging=null;});
   canvas.addEventListener('pointercancel',()=>{dragging=null;});
   canvas.addEventListener('touchstart',e=>{
     if(e.touches.length===2){pinch={distance:Math.abs(e.touches[1].clientX-e.touches[0].clientX),base:visible};dragging=null;}
     else if(e.touches.length===1){dragging={x:e.touches[0].clientX,base:offset,moved:false};pinch=null;}
   },{passive:true});
   canvas.addEventListener('touchmove',e=>{
     if(!current)return;
     if(e.touches.length===2&&pinch){e.preventDefault();const d=Math.abs(e.touches[1].clientX-e.touches[0].clientX);
       visible=clamp(Math.round(pinch.base*pinch.distance/Math.max(10,d)),5,125);
       offset=clamp(offset,0,Math.max(0,current.chart.dates.length-visible));render();}
     else if(e.touches.length===1&&dragging){e.preventDefault();const dist=e.touches[0].clientX-dragging.x;
       if(Math.abs(dist)>5)dragging.moved=true;
       offset=clamp(Math.round(dragging.base-dist*visible/(canvas.clientWidth||300)),0,Math.max(0,current.chart.dates.length-visible));render();}
   },{passive:false});
   canvas.addEventListener('touchend',e=>{if(dragging&&!dragging.moved&&e.changedTouches.length){
     const rect=canvas.getBoundingClientRect();selectCandle(e.changedTouches[0].clientX-rect.left);
   }dragging=null;pinch=null;});
   if('ResizeObserver'in window)new ResizeObserver(()=>render()).observe(canvas);
   window.addEventListener('resize',render);
 }
 function render(){
   const canvas=byId('candleCanvas');if(!canvas||!current||!valid(current)||canvas.clientWidth<60)return;
   const device=Math.min(window.devicePixelRatio||1,2.5),width=canvas.clientWidth,height=canvas.clientHeight||315;
   canvas.width=Math.round(width*device);canvas.height=Math.round(height*device);
   const ctx=canvas.getContext('2d');ctx.setTransform(device,0,0,device,0,0);ctx.clearRect(0,0,width,height);
   const {x,start,end,cnt}=range(),left=10,right=64,top=18,bottom=27,volHeight=52,sep=11;
   const cw=width-left-right,pBottom=height-bottom-volHeight-sep,pHeight=pBottom-top;
   if(cw<=20||pHeight<=30||cnt===0)return;
   const lo=Math.min(...x.low.slice(start,end)),hi=Math.max(...x.high.slice(start,end)),pad=Math.max((hi-lo)*.12,hi*.0001);
   const min=lo-pad,max=hi+pad,y=v=>top+(max-v)/(max-min)*pHeight;
   const bx=i=>left+((i-start)+.5)*cw/cnt,step=cw/cnt;
   ctx.font='11px system-ui, sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
   for(let i=0;i<5;i++){
     const yy=top+i*pHeight/4;ctx.strokeStyle='rgba(140,185,195,.14)';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(width-right+3,yy);ctx.stroke();ctx.fillStyle='#83a5b4';ctx.fillText(fmt(max-i*(max-min)/4),width-4,yy);
   }
   const maxVol=Math.max(1,...x.volume.slice(start,end)),vy=height-bottom;
   for(let i=start;i<end;i++){
     const open=x.open[i],close=x.close[i],high=x.high[i],low=x.low[i],up=close>=open,xx=bx(i),color=up?'#29d6b3':'#f07888',partial=!!x.partial?.[i],displayOnly=!!x.display_only?.[i];
     const bodyWidth=Math.max(2,Math.min(18,step*.68));
     ctx.globalAlpha=(partial||displayOnly)?.60:1;
     if(mode==='candles'){
       ctx.strokeStyle=color;ctx.lineWidth=1.2;if(partial)ctx.setLineDash([2,3]);
       ctx.beginPath();ctx.moveTo(xx,y(high));ctx.lineTo(xx,y(low));ctx.stroke();ctx.setLineDash([]);
       ctx.fillStyle=color;
       const yy=Math.min(y(open),y(close)),hh=Math.max(1.5,Math.abs(y(open)-y(close)));
       ctx.fillRect(xx-bodyWidth/2,yy,bodyWidth,hh);
       if(partial||displayOnly){ctx.strokeStyle=displayOnly?'#e8bc71':'#d9f2ed';ctx.setLineDash([2,3]);ctx.strokeRect(xx-bodyWidth/2,yy,bodyWidth,hh);ctx.setLineDash([]);}
     }
     ctx.fillStyle=up?'rgba(41,214,179,.38)':'rgba(240,120,136,.38)';
     const h=Number(x.volume[i])/maxVol*volHeight,vwidth=Math.max(1.5,Math.min(16,step*.56));
     ctx.fillRect(xx-vwidth/2,vy-h,vwidth,h);
     ctx.globalAlpha=1;
   }
   const drawLine=(vals,color,lineWidth)=>{
     ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.lineJoin='round';ctx.beginPath();let started=false;
     for(let i=start;i<end;i++){const v=vals?.[i];if(v==null||!Number.isFinite(v)){started=false;continue;}
       const xx=bx(i);if(started)ctx.lineTo(xx,y(v));else{ctx.moveTo(xx,y(v));started=true;}}
     ctx.stroke();
   };
   if(mode==='line')drawLine(x.close,'#36e1bb',2.4);
   drawLine(x.ema20,'#9eb8ff',1.4);drawLine(x.ema50,'#e8bc71',1.4);
   // Only annotate exact matching completed daily OHLCV. Never project onto W/M/Y or partial bars.
   if(researchReady()&&byId('candleResearchOverlay')?.checked){
     for(let i=start;i<end;i++){
       const r=matchedResearch(x,i);if(!r)continue;
       const xx=bx(i),above=clamp(y(x.high[i])-10,top+5,pBottom-7),below=clamp(y(x.low[i])+11,top+6,pBottom-6);
       if(r.baseline==='BUY'||r.baseline==='SELL'){
         const buy=r.baseline==='BUY';ctx.beginPath();ctx.arc(xx,buy?below:above,3.6,0,Math.PI*2);
         ctx.fillStyle=buy?'#22dcc0':'#ff8790';ctx.fill();ctx.strokeStyle='#061b27';ctx.lineWidth=1.2;ctx.stroke();
       }
       if(r.five_core==='BUY'){
         ctx.beginPath();ctx.arc(xx,clamp(above-8,top+4,pBottom-7),3.1,0,Math.PI*2);
         ctx.fillStyle='#8ab4ff';ctx.fill();ctx.strokeStyle='#061b27';ctx.lineWidth=1;ctx.stroke();
       }
     }
   }

   [['long_stop_reference','#ee7a8a','손절'],['long_target_reference','#45c995','목표']].forEach(([k,color,label])=>{
     const v=current[k];if(!Number.isFinite(v)||v<min||v>max)return;
     ctx.setLineDash([4,5]);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.setLineDash([]);
     ctx.fillStyle=color;ctx.font='10px system-ui';ctx.textAlign='left';ctx.fillText(label,left+4,y(v)-7);ctx.textAlign='right';
   });
   // Structure+ATR references are visible only for the analysis candle period.
   // They are distinct from the legacy ATR lines and never authorize orders.
   if(timeframe==='analysis' && current.risk_structure){
     [['stop','#ffb85f','구조 손절'],['target','#9ee0ff','구조 목표']].forEach(([key,color,label])=>{
       const v=current.risk_structure[key],old=current[key==='stop'?'long_stop_reference':'long_target_reference'];
       if(!Number.isFinite(v)||v<min||v>max||Math.abs(v-old)<(max-min)*.003)return;
       ctx.setLineDash([2,4]);ctx.strokeStyle=color;ctx.lineWidth=1.4;ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.setLineDash([]);
       ctx.fillStyle=color;ctx.font='10px system-ui';ctx.textAlign='left';ctx.fillText(label,left+4,y(v)+10);
     });
   }
   ctx.fillStyle='#779aa9';ctx.textAlign='left';ctx.font='10px system-ui';ctx.fillText('거래량',left+4,vy-volHeight+8);
   [start,Math.floor((start+end-1)/2),end-1].forEach((i,j)=>{if(i<start||i>=end)return;
     ctx.textAlign=j===0?'left':j===2?'right':'center';ctx.fillText(periodStamp(x.dates[i]),bx(i),height-10);});
   if(hovered!=null&&hovered>=start&&hovered<end){const xx=bx(hovered);
     ctx.strokeStyle='rgba(220,244,245,.5)';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,vy);ctx.stroke();ctx.setLineDash([]);}
   const endIndex=hovered!=null&&hovered<end&&hovered>=start?hovered:end-1;
   const incomplete=(x.partial?.[endIndex]?' · 미완성 봉(현재 기간)':'')+(x.display_only?.[endIndex]?' · 차트 전용 / 분석 미반영':'');
   const indicators=(x.ema20?.[endIndex]==null?' · EMA20 자료 부족':'')+(x.ema50?.[endIndex]==null?' · EMA50 자료 부족':'');
   const historicalSignal=matchedResearch(x,endIndex);
   const signalNote=historicalSignal&&byId('candleResearchOverlay')?.checked?' · 과거 13역할 '+({BUY:'매수',SELL:'매도',HOLD:'관망'}[historicalSignal.baseline]||'미확인')+' / 5-Core '+({BUY:'매수',SELL:'매도',HOLD:'관망'}[historicalSignal.five_core]||'미확인')+' (재구성 연구)':'';
   byId('candleDetails').textContent=`${periodStamp(x.dates[endIndex],true)}${incomplete} · 시 ${fmt(x.open[endIndex])} / 고 ${fmt(x.high[endIndex])} / 저 ${fmt(x.low[endIndex])} / 종 ${fmt(x.close[endIndex])} · 거래량 ${fmt(x.volume[endIndex])}${indicators}${signalNote}`;
   showResearch(x.display_only?.[endIndex]?null:chartDate(x.dates[endIndex]));
 }
 function setCurrent(data){
   current=valid(data)?data:null;hovered=null;offset=0;visible=65;
   resetResearch();
   if(!current){blank('OHLC 데이터가 없거나 유효하지 않아 차트를 표시하지 않습니다.');return;}
   const last=current.chart.dates[current.chart.dates.length-1];
   if(byId('chartEnd'))byId('chartEnd').textContent=
      `${timeframe==='analysis'?'분석봉':timeframeLabel[timeframe]} · ${periodStamp(last)}`;
   requestAnimationFrame(render);
 }
 async function selectPeriod(force=false){
   stopLive();
   if(request){request.abort();request=null;}
   const id=++serial;
   if(!force){lastSessionPhase=null;analysisPhase=null;}
   if(!base)return;
   if(timeframe==='analysis'){
     liveStatus('분석 완료봉 + KIS 차트 전용 추가 봉 · AI 판단은 변경하지 않음');
     setCurrent({...base,chart:Object.fromEntries(Object.entries(base.chart).map(([k,v])=>[k,Array.isArray(v)?v.slice():v])),chart_timeframe:'analysis'});
     startLive(id);
     setStatus(`${base.mode==='스윙'?'일봉':base.mode+'봉'} · 분석 완료봉 + 차트 전용 추가 봉(금색 점선) · 실제 판단은 기존 완료봉만 사용${base.daily_integrity?.session_reconstructed?' · 최신 정규장 일봉 재구성 / 이전 봉 세션 미검증':''}${base.daily_integrity?.signal_hold?' · 최신 봉 제외: '+base.daily_integrity.reason:''}`,base.daily_integrity?.signal_hold?'error':undefined);
     return;
   }
   const actualSource=provider==='kis'?'kis':base.source;
   const key=`${actualSource}/${base.symbol}/${timeframe}`;
   let data;
   if(window.NOVA_PREVIEW_DATA){data=window.NOVA_PREVIEW_LONG_CHARTS?.[timeframe];
     if(!data){blank('오프라인 데모의 장기 가상자료가 없습니다.');setStatus('가상 장기 봉 자료 없음','error');return;}
   }else{
     const cached=recent.get(key);
     if(actualSource!=='kis'&&cached&&Date.now()-cached.time<3*60*1000)data=cached.data;
     else{
       if(!force)blank(`${timeframeLabel[timeframe]} 과거 일봉을 조회해 합산 중…`);
       setStatus(`${timeframeLabel[timeframe]} 조회 중 · 신호는 변경되지 않습니다`,'loading');
       const ctrl=new AbortController();request=ctrl;
       const timeout=setTimeout(()=>ctrl.abort(),25000);
       try{
         const params=new URLSearchParams({symbol:base.symbol,source:actualSource,timeframe});
         const headers=getAuth();
         const response=await fetch('/api/chart/history?'+params,{headers,credentials:'same-origin',cache:'no-store',signal:ctrl.signal});
         if(!response.ok){const j=await response.json().catch(()=>({}));throw new Error(typeof j.detail==='string'?j.detail:`HTTP ${response.status}`);}
         data=await response.json();
         if(!valid(data))throw new Error('봉 배열에 누락되거나 비정상적인 가격이 있습니다.');
         recent.set(key,{time:Date.now(),data});if(recent.size>20)recent.clear();
       }catch(err){if(serial!==id)return;
         if(force&&current&&valid(current)){
           liveStatus('마감 봉 재조회 실패 · 마지막 조회 봉 유지 · 확정 여부 미확인','unavailable');
           lastSessionPhase='retry';scheduleLive(id,60000);return;
         }
         blank(`${timeframeLabel[timeframe]} 표시 불가 · ${err.name==='AbortError'?'조회 시간이 초과됐거나 취소됐습니다.':err.message}`);
         liveStatus(actualSource==='kis'?'KIS 차트 연결 실패 · 환경변수 KIS_APP_KEY / KIS_APP_SECRET 및 조회 권한을 확인하세요':'장중 봉 연결 해제 · 현재 선택한 공급원은 실시간 연동이 아닙니다','unavailable');
         setStatus(`${timeframeLabel[timeframe]} 데이터를 확인할 수 없습니다. 분석 주기 또는 시세 공급원을 변경하세요.`,'error');return;
       }finally{clearTimeout(timeout);if(request===ctrl)request=null;}
     }
   }
   if(serial!==id||!base)return;
   setCurrent({...base,chart:Object.fromEntries(Object.entries(data.chart).map(([k,v])=>[k,Array.isArray(v)?v.slice():v])),chart_timeframe:timeframe});
   startLive(id);
   setStatus(`${timeframeLabel[timeframe]} ${data.chart.dates.length}개 표시 / 전체 ${data.count??data.chart.dates.length}개 · ${timeframe==='1d'?'분석용 완료봉':'기간 자료 날짜'} ${String(data.last_completed_daily||'').slice(0,10)||'미확인'}${data.partial_last?' · 차트 전용 미완성 봉 포함':''}${data.display_current_daily?.status==='PROVISIONAL'?' · KIS 원본 당일 봉(정규장 확정 전)'+(data.display_current_daily.calendar_verified===false?' · 거래일 달력 미확인':''):''} · 신호 계산과 분리${data.daily_integrity?.session_reconstructed?' · 최신 정규장 일봉 재구성 / 이전 봉 세션 미검증':''}${data.daily_integrity?.signal_hold?' · '+(data.daily_integrity.rejected_date||'최신')+' 분석용 봉 제외: '+(data.daily_integrity.reason||'검증 보류'):''}`,data.daily_integrity?.signal_hold?'error':undefined);
 }
 function draw(data){install();base=valid(data)?data:null;
   if(!base){stopLive();if(request)request.abort();serial++;blank('분석 시세가 없어서 차트를 표시하지 않습니다.');setStatus('차트 자료 없음','error');return;}
   selectPeriod();
 }
 window.NOVACandles={draw,render,valid,selectPeriod};
})();
