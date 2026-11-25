(()=>{
 const run=document.getElementById('opsDiagRun'), status=document.getElementById('opsDiagStatus'), result=document.getElementById('opsDiagResult');
 if(!run||!status||!result)return;
 let timer=null;
 async function refresh(){
   try{
     const r=await fetch('/api/ops/diagnostics/status',{credentials:'same-origin',cache:'no-store'});
     if(!r.ok)throw Error('status');const d=await r.json();
     if(d.status==='running'){const p=d.progress||{}; const phase=p.phase==='aggregate_start'?'공통구간 재계산':p.phase==='aggregate_done'?'결과 정리':p.symbol||'연결·차트 검사'; result.textContent=`운영 진단 실행 중 · ${p.finished??0}/${p.requested??20} · ${phase} · 경과 ${p.elapsed_seconds??0}초 (최근 진행 이벤트 기준)`;run.disabled=true;timer=setTimeout(refresh,10000);return;}
     run.disabled=false;
     const x=d.result;
     if(!x){result.textContent='진단 대기';return;}
     const q=x.research||{}, b=x.database||{}, c=x.calendar||{};
     result.textContent=`진단 ${d.status} · 달력 ${c.verified?'확인':'미확인'} · DB ${b.status||'미확인'}${b.failure_stage?' / '+b.failure_stage:''}${b.sqlstate?' / '+b.sqlstate:''} · 완료 ${q.ready??'—'}/${q.requested??'—'} · 확률 ${q.scored??'—'}건 · 매수 ${q.buy_signals??'—'}건 · Render 로그 NOVA_OPS 확인`;
   }catch(_){run.disabled=false;result.textContent='진단 상태 조회 실패 · 인증 또는 서버 상태 확인';}
 }
 status.addEventListener('click',()=>{clearTimeout(timer);refresh();});
 run.addEventListener('click',async()=>{clearTimeout(timer);run.disabled=true;result.textContent='진단 시작 요청 중…';try{const r=await fetch('/api/ops/diagnostics/run',{method:'POST',credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('run');const d=await r.json();if(d.status==='batch_busy'||d.status==='cooldown'){run.disabled=false;result.textContent=d.status==='batch_busy'?'기존 일괄 연구 진행 중':'10분 이내 재실행 제한 · 상태 조회 이용';return;}refresh();}catch(_){run.disabled=false;result.textContent='진단 요청 실패 · 인증 또는 서버 상태 확인';}});
})();
