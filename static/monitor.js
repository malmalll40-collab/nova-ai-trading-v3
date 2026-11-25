/* NOVA v3.8.1 foreground-only watchlist monitor. No unattended scans / no push. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const key = 'nova-v37-monitor-state';
  const intervalMs = 15 * 60 * 1000;
  const maxStocks = 5;
  const validSignals = new Set(['BUY','SELL','HOLD']);
  const storage = {
    get(k, fallback){try { const v = JSON.parse(localStorage.getItem(k) || 'null');return v ?? fallback;} catch{return fallback;}},
    set(k, v){try{localStorage.setItem(k,JSON.stringify(v));}catch{}}
  };
  let stored = storage.get(key, {});
  if(!stored || typeof stored !== 'object') stored = {};
  const previous = storage.get('nova-v37-previous-signals', {});
  let busy = false, lastAttempt = 0, timer = null;
  const formatPrice = (value, currency = '') => value == null || !Number.isFinite(Number(value)) ? '미확인'
    : new Intl.NumberFormat('ko-KR',{maximumFractionDigits: Number(value) < 100 ? 2 : 0}).format(Number(value))+(currency ? ' '+currency : '');
  const sigLabel = signal => ({BUY:'매수 후보',SELL:'보유 축소 검토',HOLD:'관망'})[signal] || '미확인';
  const modeSort = ['1분','5분','15분','스윙'];
  const isPreview = Boolean(window.NOVA_PREVIEW_DATA);
  const els = () => ['monitorButton','monitorStatus','monitorResults','monitorMode','monitorAuto'].every(id => $(id));
  const visible = () => !document.hidden && window.NOVAConnect?.currentView() === 'watchlist';
  const selectedModes = () => ($('monitorMode')?.value === '1분+5분' ? ['1분','5분']
    : $('monitorMode')?.value === '1분+스윙' ? ['1분','스윙']
    : $('monitorMode')?.value === '5분+15분' ? ['5분','15분']
    : $('monitorMode')?.value === '15분+스윙' ? ['15분','스윙'] : ['5분','스윙']);
  const tracked = () => window.NOVAConnect?.watchlist()?.slice(0,maxStocks) || [];
  const status = message => { if($('monitorStatus'))$('monitorStatus').textContent = message; };
  const fmtBar = s => s ? String(s).replace('T',' ').slice(0,19) : '확인 불가';

  // On Android, new Notification() can throw; prefer SW showNotification.
  async function notify(row, was) {
    if(!('Notification' in window) || Notification.permission !== 'granted')return;
    const title = `NOVA ${row.symbol} · ${row.mode}`;
    const msg = `${sigLabel(was)} → ${sigLabel(row.combined)} · 완료봉 ${fmtBar(row.bar_time)}`;
    try {
      if('serviceWorker' in navigator && window.isSecureContext) {
        const reg = await navigator.serviceWorker.ready;
        if(reg.showNotification) {
          await reg.showNotification(title, {body:msg,tag:`nova-v37-${row.symbol}-${row.mode}`,renotify:false,
            icon:'/static/icons/icon-192.png',data:{symbol:row.symbol}});
          return;
        }
      }
      new Notification(title,{body:msg, icon:'/static/icons/icon-192.png'});
    } catch(e){status('신호가 변경됐지만 브라우저 알림은 제한됐습니다.');}
  }

  function maybeAlert(rows) {
    for(const row of rows) {
      if(row.status !== 'ok' || row.is_stale || !row.signal_is_current ||
         !validSignals.has(row.final_actionable_signal) || !row.bar_time)continue;
      // A first observation establishes a baseline. Never retroactively invent an alert.
      const id=`${row.source}/${row.symbol}/${row.mode}`;
      const was = previous[id];
      if(was && String(row.bar_time) > String(was.bar_time) && was.signal !== row.final_actionable_signal) {
        notify(row,was.signal); // direct observation in the open tab, not an autonomous push
      }
      if(!was || String(row.bar_time) >= String(was.bar_time)) {
        previous[id] = {signal:row.final_actionable_signal,bar_time:row.bar_time};
      }
    }
    let keys = Object.keys(previous);
    if(keys.length > 80){keys.slice(0,keys.length - 80).forEach(k => delete previous[k]);}
    storage.set('nova-v37-previous-signals',previous);
  }

  function itemCard(r) {
    const card=document.createElement('div');card.className='monitor-item';
    const title=document.createElement('div');title.className='monitor-item-top';
    const name=document.createElement('strong');name.textContent=`${r.mode} · ${sigLabel(r.combined)}`;
    const badge=document.createElement('span');badge.className='monitor-pill '+
      (r.status!=='ok'?'monitor-muted':r.is_stale?'monitor-muted':r.combined==='BUY'?'monitor-buy':r.combined==='SELL'?'monitor-sell':'monitor-hold');
    badge.textContent=r.status!=='ok'?'조회 실패':r.is_stale?'지난 봉':r.financial_gate==='UNKNOWN'?'재무 미확인':'조회됨';
    title.append(name,badge);card.appendChild(title);
    const lines=document.createElement('div');lines.className='monitor-line';
    if(r.status!=='ok')lines.textContent=r.error||'시세 데이터 조회 실패';
    else {
      const detail=[];
      detail.push(`기준가 ${formatPrice(r.reference,r.currency)} · 손절 참고 ${formatPrice(r.stop,r.currency)} · 목표 참고 ${formatPrice(r.target,r.currency)}`);
      detail.push(`완료봉 ${fmtBar(r.bar_time)} · ${r.provider||'시세 제공처 미확인'} · ${r.price_age_minutes??'—'}분 경과`);
      if(r.quality_gate)detail.push(`엄격 선별 ${r.quality_gate.passed_count}/5 · ${r.quality_gate.reason||'판단 근거 미확인'}`);
      if(r.mode==='스윙')detail.push(`재무: ${r.financial_gate||'미확인'} · ${r.finance_note||'상세 정보 없음'}`);
      lines.textContent=detail.join('\n');
    }
    card.append(lines);
    return card;
  }
  function renderResults(rows) {
    const box=$('monitorResults');if(!box)return;
    box.replaceChildren();
    const syms=tracked();
    if(!syms.length){box.textContent='관심종목이 없습니다. 먼저 종목 분석 화면에서 ★ 관심을 눌러주세요.';return;}
    if(!rows?.length){box.textContent='아래 버튼을 눌러 관심종목을 한 번에 분석하세요.';return;}
    for(const symbol of syms){
      const wrap=document.createElement('section');wrap.className='monitor-stock';
      const header=document.createElement('div');header.className='monitor-stock-head';
      const stock=document.createElement('strong');stock.textContent=symbol;
      const open=document.createElement('button');open.type='button';open.className='monitor-open';open.textContent='상세 분석 ›';
      open.onclick=()=>window.NOVAConnect?.open(symbol,'스윙');
      header.append(stock,open);wrap.appendChild(header);
      const filtered=rows.filter(x=>x.symbol===symbol);
      if(!filtered.length){const blank=document.createElement('p');blank.className='note';blank.textContent='새로운 조회가 필요합니다.';wrap.append(blank);}
      else filtered.sort((a,b)=>modeSort.indexOf(a.mode)-modeSort.indexOf(b.mode)).forEach(x=>wrap.appendChild(itemCard(x)));
      box.appendChild(wrap);
    }
  }
  async function scan({manual = false} = {}) {
    if(!els() || busy || !visible())return;
    if(isPreview){status('오프라인 체험판에서는 실제 종목 스캔이 실행되지 않습니다.');return;}
    const symbols=tracked();
    if(!symbols.length){renderResults([]);status('관심종목 등록이 필요합니다.');return;}
    if(!manual && (!($('monitorAuto')?.checked) || Date.now() - lastAttempt < intervalMs)) return;
    if(manual && Date.now() - lastAttempt < 90_000){status('반복 조회를 줄이기 위해 이전 스캔 후 90초가 지나야 다시 요청할 수 있습니다.');return;}
    busy=true;lastAttempt=Date.now();$('monitorButton').disabled=true;
    status(`${symbols.length}개 종목 × ${selectedModes().length}개 주기 조회 중… (시세 제공처 사정에 따라 실패 가능)`);
    try {
      const headers={'Content-Type':'application/json'};
      try{const token=sessionStorage.getItem('nova3-token');if(token)headers.Authorization='Bearer '+token;}catch{}
      const ctrl=new AbortController();const kill=setTimeout(()=>ctrl.abort(),90_000);
      let response;
      try { response=await fetch('/api/watch/scan',{method:'POST',headers,body:JSON.stringify({symbols,modes:selectedModes(),
        source:'kis',cost_bps:window.NOVAConnect?.cost()??15}),
        signal:ctrl.signal,credentials:'same-origin',cache:'no-store'}); }
      finally{clearTimeout(kill);}
      const data=await response.json().catch(()=>({}));
      if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${response.status}`);
      const rows=Array.isArray(data.results)?data.results:[];
      // Failed / stale rows are visible, but never cause notifications.
      maybeAlert(rows);
      renderResults(rows);
      stored={at:Date.now(),rows,mode:$('monitorMode').value,source:window.NOVAConnect?.source()};
      storage.set(key,stored);
      status(`마지막 조회 ${new Date().toLocaleTimeString('ko-KR')} · 성공 ${data.successful??0}/${data.requested??0} · 지연/오류 항목은 알림 제외`);
    }catch(err){status('관심종목 조회 실패: '+(err.name==='AbortError'?'시간 초과':err.message)+' · 기존 화면은 유지됩니다.');}
    finally{busy=false;$('monitorButton').disabled=false;}
  }
  function afterChange(){
    renderResults(Array.isArray(stored.rows)?stored.rows:[]);
    const count=tracked().length;
    if(!count)status('관심종목을 등록해 주세요.');
    else if(count<(window.NOVAConnect?.watchlist().length||0))status(`상위 ${count}종목만 스캔합니다. 나머지는 개별 분석으로 확인하세요.`);
  }
  function onViewChange(next){
    if(next!=='watchlist')return;
    afterChange();
    // Only scan when visible, at most once per 15 minutes, never in background.
    if(Date.now()-lastAttempt>=intervalMs)scan({manual:false});
  }
  function init(){
    if(!els())return;
    if(['5분+스윙','1분+5분','1분+스윙','5분+15분','15분+스윙'].includes(stored.mode))$('monitorMode').value=stored.mode;
    $('monitorButton').addEventListener('click',()=>scan({manual:true}));
    $('monitorMode').addEventListener('change',()=>{stored.rows=[];renderResults([]);status('분석 주기를 변경했습니다. 새로 조회하세요.');});
    $('monitorAuto').addEventListener('change',()=>{storage.set('nova-v37-monitor-auto',$('monitorAuto').checked);if($('monitorAuto').checked)scan({manual:false});});
    $('monitorAuto').checked=storage.get('nova-v37-monitor-auto',false)===true;
    if(Array.isArray(stored.rows)){
      renderResults(stored.rows);
      status(`이전 저장 조회 ${stored.at?new Date(stored.at).toLocaleString('ko-KR'):'시각 미확인'} · 최신 결과가 아닐 수 있습니다. 새 조회를 실행하세요.`);
    }else renderResults([]);
    if(isPreview)status('가상 화면입니다. 실제 검색·감시 및 알림은 운영 서버에서만 지원됩니다.');
    document.addEventListener('visibilitychange',()=>{if(visible() && $('monitorAuto').checked)scan({manual:false});});
    timer=setInterval(()=>{if(visible() && $('monitorAuto').checked)scan({manual:false});},60_000);
  }
  window.NOVAMonitor={init,onViewChange,afterChange};
  document.addEventListener('DOMContentLoaded',init);
})();
