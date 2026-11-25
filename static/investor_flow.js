/* NOVA v4.5: KIS + public Naver read-only flows and REST quote snapshots.
   No client-side API keys. Refresh only in visible tab. */
'use strict';
(()=>{
  const $=id=>document.getElementById(id);
  const preview=Boolean(window.NOVA_PREVIEW_DATA);
  const won=n=>n==null?'미확인':`${new Intl.NumberFormat('ko-KR',{maximumFractionDigits:0}).format(n)}원`;
  const qty=n=>n==null?'미확인':`${n>0?'+':''}${new Intl.NumberFormat('ko-KR').format(n)}`;
  const auth=()=>{try{const key=sessionStorage.getItem('nova3-token');return key?{Authorization:'Bearer '+key}:{};}catch{return {};}};
  let selected='', quoteTimer=null, bars=[], quote=null, loadId=0;
  let quotePending=null, intradayPending=null, flowPending=null, cooldownUntil=0, kisUnavailable=false;
  const flowsVisible=()=>window.NOVAConnect?.currentView()==='recommendations'&&!document.hidden;
  const analyzeVisible=()=>window.NOVAConnect?.currentView()==='analyze'&&!document.hidden;
  const codeOf=s=>{const m=/^(\d{6})(?:\.(?:KS|KQ))?$/i.exec(String(s||'').trim());return m?m[1]:'';};
  function setStatus(msg,warning=false){const e=$('v45Status');if(e){e.textContent=msg;e.classList.toggle('warn',warning);}}
  async function request(path,code,signal){
    if(Date.now()<cooldownUntil)throw Error('요청 제한 보호 중: 잠시 후 다시 조회하세요.');
    const r=await fetch(path+'?symbol='+encodeURIComponent(code),{headers:auth(),cache:'no-store',signal});
    if(r.status===429)cooldownUntil=Date.now()+60000;
    let body={};try{body=await r.json();}catch{}
    if(!r.ok){
      const detail=typeof body.detail==='string'?body.detail:`HTTP ${r.status}`;
      if(r.status===503 && detail.includes('KIS 앱 키가 미설정'))kisUnavailable=true;
      throw Error(detail);
    }
    return body;
  }
  function row(container,values){const tr=document.createElement('tr');for(const v of values){const td=document.createElement('td');td.textContent=String(v);tr.append(td);}container.append(tr);}
  function renderFlows(res){
    const sums=$('v45Sums'),daily=$('v45Daily');
    if(!sums||!daily)return;
    sums.replaceChildren();daily.replaceChildren();
    for(const d of res.aggregate||[]){
      const per=d.complete_by_investor||{};
      const hasField=k=>per[k]===true||(d.complete===true&&!d.complete_by_investor);
      const values=['foreign','institution','individual'].map(k=>hasField(k)?qty(d[k]):'미확인');
      const partial=values.some(v=>v==='미확인');
      const label=!partial?`${d.period}거래일`:values.every(v=>v==='미확인')?
        `${d.period}거래일 · 부족 ${d.count}/${d.period}`:`${d.period}거래일 · 일부 항목 미확인`;
      row(sums,[label,...values]);
    }
    for(const d of res.daily||[]){row(daily,[d.date,qty(d.foreign),qty(d.institution),qty(d.individual)]);}
    if(!(res.daily||[]).length)row(daily,['완료 거래일 데이터 없음','—','—','—']);
    const row20=(res.aggregate||[]).find(x=>x.period===20)||{};
    const counts=row20.count_by_investor||{};
    const countLabel=key=>`${Number(counts[key]||0)}/20일`;
    const hint=$('v45FlowCoverage');
    if(hint)hint.textContent=`20일 기준 확보: 외국인 ${countLabel('foreign')} · 기관 ${countLabel('institution')} · 개인 ${countLabel('individual')}. 미확인 항목은 추정하지 않음.`;
    const provider=res.provider==='naver'?'네이버 증권 공개자료(비공식)':res.provider==='kis'?'KIS KRX':'제공원 미확인';
    const sourceInfo=res.source_detail?` · ${res.source_detail}`:'';
    const fallback=res.fallback_reason?` · 전환 사유 ${res.fallback_reason}`:'';
    const coverage=res.history_expansion_status?` · ${res.history_expansion_status}`:'';
    const rowCoverage=res.history_coverage_note?` · 원천 항목별 확보: ${res.history_coverage_note}`:'';
    $('v45FlowNote').textContent=`자료 기준일 ${res.as_of||'미확인'} · ${provider}${sourceInfo}${fallback}${coverage}${rowCoverage} · 투자자별 순매수 수량(주) · ${res.partial_today_excluded?'당일 잠정치 제외 · ':''}누적기간 자료 부족/필드 누락 시 미확인 · 교차검증 미실시 · 수급과 추천 적중확률은 별개입니다.`;
  }
  function renderQuote(res,target){
    const price=won(res.price),chg=res.change==null?'등락 미확인':`${res.change>0?'+':''}${new Intl.NumberFormat('ko-KR').format(res.change)}원`,
      pct=res.change_pct==null?'등락률 미확인':`${res.change_pct>0?'+':''}${Number(res.change_pct).toFixed(2)}%`;
    const trade=res.trade_time_kst?`체결 ${res.trade_time_kst}`:'체결 시각 미확인';
    const fetched=String(res.retrieved_at||'').replace('T',' ').slice(0,19);
    const cached=res.cached?` · 캐시 ${res.cache_age_seconds||0}초`:'';
    const message=`${res.symbol} · ${price} · ${chg} (${pct}) · ${trade} · 조회 ${fetched}${cached} · REST 20초 갱신 · 정규장 확정 종가 아님`;
    if(target==='recommendations'){
      const el=$('v45Quote');if(el){el.replaceChildren();const strong=document.createElement('strong');strong.textContent=price;
        const subtitle=document.createElement('span');subtitle.textContent=`${chg} · ${pct} · ${trade}`;
        const foot=document.createElement('small');foot.textContent=`REST 시세 스냅샷 · 조회 ${fetched}${cached} · 정규장 확정 종가 미검증 · 실시간 WebSocket 아님`;
        el.append(strong,subtitle,foot);}
    }
    const inline=$('v45ChartQuote');if(inline&&target==='analyze')inline.textContent=message;
    if(target==='analyze')window.NOVAIntradayContext?.setQuote(res);
  }
  async function loadQuote(code,target){
    if(kisUnavailable){
      if(target==='recommendations'&&code===selected&&$('v45Quote'))$('v45Quote').textContent='현재가 미확인(KIS) · 서버 API 키 미설정. 네이버 수급 조회와는 별개입니다.';
      if(target==='analyze'&&code===codeOf($('symbolInput')?.value)){
        if($('v45ChartQuote'))$('v45ChartQuote').textContent='현재가 미확인(KIS) · 서버 API 키 미설정';
        window.NOVAIntradayContext?.setUnavailable('KIS 키 미설정 · 현재가 차이 평가 불가');
      }
      return;
    }
    if(quotePending?.code===code && quotePending?.target===target)return;
    if(quotePending)quotePending.controller.abort();
    const run={code,target,controller:new AbortController()};quotePending=run;
    try{
      const data=await request('/api/market/quote',code,run.controller.signal);
      if(quotePending!==run)return;
      if(target==='recommendations'&&code!==selected)return;
      if(target==='analyze'&&code!==codeOf($('symbolInput')?.value))return;
      quote=data;renderQuote(data,target);
      if(target==='recommendations')draw();
    }catch(e){
      if(run.controller.signal.aborted||quotePending!==run)return;
      if(target==='recommendations'&&code===selected){const el=$('v45Quote');if(el)el.textContent=`현재가 미확인(KIS) · ${e.message}`;}
      if(target==='analyze'&&code===codeOf($('symbolInput')?.value)){
        if($('v45ChartQuote'))$('v45ChartQuote').textContent=`장중 현재가 미확인(KIS 전용) · ${e.message}`;
        window.NOVAIntradayContext?.setUnavailable('KIS 가격 조회 실패 · 현재가 차이 평가 불가');
      }
    }finally{if(quotePending===run)quotePending=null;}
  }
  async function loadIntraday(code){
    if(kisUnavailable){
      if(code===selected&&$('v45ChartNote'))$('v45ChartNote').textContent='당일 분봉 미확인(KIS) · 서버 API 키 미설정';
      return;
    }
    if(intradayPending?.code===code)return;
    if(intradayPending)intradayPending.controller.abort();
    const run={code,controller:new AbortController()};intradayPending=run;
    try{
      const data=await request('/api/market/intraday',code,run.controller.signal);
      if(intradayPending!==run||code!==selected)return;
      bars=Array.isArray(data.points)?data.points:[];draw();
      $('v45ChartNote').textContent=bars.length?`${data.date} · ${bars.length}개 1분 가격 · 마지막 ${bars[bars.length-1].time} · 실시간 봉 동기화 보장 없음`:
        `${data.date} 당일 분봉 없음 · 장외/휴장 또는 제공 지연일 수 있습니다.`;
    }catch(e){if(!run.controller.signal.aborted&&intradayPending===run&&code===selected){bars=[];draw();$('v45ChartNote').textContent=`당일 분봉 미확인(KIS 전용): ${e.message}`;}}
    finally{if(intradayPending===run)intradayPending=null;}
  }
  function draw(){
    const canvas=$('v45Chart');if(!canvas)return;
    const ctx=canvas.getContext('2d');if(!ctx)return;
    const w=canvas.width,h=canvas.height;
    ctx.clearRect(0,0,w,h);
    ctx.fillStyle='#142237';ctx.fillRect(0,0,w,h);
    const prices=bars.map(x=>Number(x.price)).filter(Number.isFinite);
    if(!prices.length){ctx.fillStyle='#b5c2d5';ctx.font='16px sans-serif';ctx.fillText('당일 실제 분봉 데이터가 없습니다',22,42);return;}
    const pQuote=quote?.symbol===selected&&Number.isFinite(Number(quote.price))?Number(quote.price):null;
    const ext=pQuote==null?prices:[...prices,pQuote];let lo=Math.min(...ext),hi=Math.max(...ext);
    const pad=(hi-lo||lo*0.003||1)*0.15;lo-=pad;hi+=pad;
    const L=48,R=18,T=20,B=28;
    const x=i=>L+(w-L-R)*i/Math.max(prices.length-1,1),y=p=>T+(h-T-B)*(hi-p)/(hi-lo);
    ctx.strokeStyle='#36485f';ctx.lineWidth=1;ctx.font='12px sans-serif';ctx.fillStyle='#bdc9d9';
    for(let i=0;i<3;i++){const py=T+(h-T-B)*i/2;ctx.beginPath();ctx.moveTo(L,py);ctx.lineTo(w-R,py);ctx.stroke();ctx.fillText(Math.round(hi-(hi-lo)*i/2).toLocaleString('ko-KR'),4,py+4);}
    ctx.beginPath();prices.forEach((p,i)=>i?ctx.lineTo(x(i),y(p)):ctx.moveTo(x(i),y(p)));
    ctx.strokeStyle='#66c5fc';ctx.lineWidth=2.5;ctx.stroke();
    const last=bars[bars.length-1];ctx.fillStyle='#aebed4';ctx.fillText((bars[0]?.time||'09:00'),L,h-8);ctx.fillText((last?.time||'15:30'),w-75,h-8);
    if(pQuote!=null){const yq=y(pQuote);ctx.strokeStyle='#f5b85e';ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(L,yq);ctx.lineTo(w-R,yq);ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle='#f5b85e';ctx.font='bold 13px sans-serif';ctx.fillText('REST 현재가',w-118,Math.max(16,yq-8));}
  }
  async function loadFlows(code){
    if(preview){setStatus('체험판에서는 실제 투자자 수급 조회를 지원하지 않습니다.',true);return;}
    const id=++loadId;
    if(flowPending)flowPending.abort();
    if(quotePending)quotePending.controller.abort();
    if(intradayPending)intradayPending.controller.abort();
    const controller=new AbortController();flowPending=controller;
    selected=code;bars=[];quote=null;draw();if($('v45Quote'))$('v45Quote').textContent='';
    if($('v45Sums'))$('v45Sums').replaceChildren();
    if($('v45Daily'))$('v45Daily').replaceChildren();
    if($('v45FlowNote'))$('v45FlowNote').textContent='새 종목 데이터를 조회하고 있습니다.';
    if($('v45FlowCoverage'))$('v45FlowCoverage').textContent='투자자별 5·10·20일 자료 조회 중';
    if($('v45ChartNote'))$('v45ChartNote').textContent='새 종목 분봉 조회 대기 중';
    setStatus(`${code} · 투자자별 수급 조회 중 (KIS, 실패 시 네이버 증권)`);
    try{
      const data=await request('/api/market/flows',code,controller.signal);if(id!==loadId)return;
      renderFlows(data);setStatus(`${code} · ${data.provider==='naver'?'네이버(비공식)':data.provider==='kis'?'KIS':'제공원 미확인'} 수급 조회 완료 · 기준일 ${data.as_of||'미확인'} · 장중 수급 확정치 아님`);
     }catch(e){if(id===loadId){
      const sums=$('v45Sums'),daily=$('v45Daily');if(sums)sums.replaceChildren();if(daily)daily.replaceChildren();
      if($('v45FlowNote'))$('v45FlowNote').textContent='수급 자료 미확인 · 이전 종목의 데이터를 표시하지 않습니다.';
      if($('v45FlowCoverage'))$('v45FlowCoverage').textContent='수급 자료 미확인 · 원본 제공원 진단 필요';
      setStatus(`수급 조회 실패: ${e.message}`,true);
    }}
    if(id!==loadId)return; // An outdated request must not start quote/intraday for an old symbol.
    if(flowPending===controller)flowPending=null;
    await Promise.all([loadQuote(code,'recommendations'),loadIntraday(code)]);
    if(id===loadId)beginTimer();
  }
  function stopTimer(){if(quoteTimer){clearInterval(quoteTimer);quoteTimer=null;}}
  function beginTimer(){stopTimer();if(preview||document.hidden)return;
    quoteTimer=setInterval(()=>{
      if(Date.now()<cooldownUntil||kisUnavailable)return;
      if(flowsVisible()&&selected)loadQuote(selected,'recommendations');
      else if(analyzeVisible()){
        const code=codeOf($('symbolInput')?.value);if(code)loadQuote(code,'analyze');
      }else stopTimer();
    },20000);
  }
  function openSymbol(symbol){const code=codeOf(symbol);if(!code)return;
    $('v45Symbol').value=code;loadFlows(code);$('v45Symbol').scrollIntoView({behavior:'smooth',block:'center'});
  }
  function onViewChange(view){stopTimer();if(preview||document.hidden)return;
    if(view==='recommendations'){
      if(selected){loadQuote(selected,'recommendations');loadIntraday(selected);beginTimer();}
    }else if(view==='analyze'){
      const code=codeOf($('symbolInput')?.value);
      if(code){loadQuote(code,'analyze');beginTimer();}
      else if($('v45ChartQuote'))$('v45ChartQuote').textContent='KIS 현재가는 국내 6자리 종목만 제공합니다.';
    }
  }
  function init(){if(!$('v45Load'))return;
    $('v45Load').addEventListener('click',()=>{const code=codeOf($('v45Symbol').value);if(!code){setStatus('국내 주식 종목코드 6자리를 입력하세요.',true);return;}loadFlows(code);});
    $('v45Symbol').addEventListener('keydown',e=>{if(e.key==='Enter'){$('v45Load').click();}});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTimer();else onViewChange(window.NOVAConnect?.currentView());});
    onViewChange(window.NOVAConnect?.currentView());
  }
  // After a new analysis has rendered, refresh the matching REST snapshot.
  // Do not reuse the previous symbol's data or change the independent signal.
  function onAnalysis(symbol){
    if(preview||!analyzeVisible())return;
    const code=codeOf(symbol);
    if(code)void loadQuote(code,'analyze');
  }
  window.NOVAFlow={openSymbol,onViewChange,onAnalysis};
  window.addEventListener('DOMContentLoaded',init);
})();
