/* NOVA v4.4: point-in-time first-stage candidate backtest (NOT final NOVA). */
'use strict';
(()=>{
  const $=id=>document.getElementById(id);
  const preview=Boolean(window.NOVA_PREVIEW_DATA);
  let poller=null, running=false;
  const signed=n=>n==null||!Number.isFinite(Number(n))?'—':`${n>0?'+':''}${Number(n).toFixed(2)}%`;
  const safeInt=n=>Number.isFinite(Number(n))?String(Number(n)):'—';
  const headers=()=>{const h={};try{const t=sessionStorage.getItem('nova3-token');if(t)h.Authorization='Bearer '+t;}catch{}return h;};
  function status(msg,warn=false){const e=$('historyStatus');if(e){e.textContent=msg;e.classList.toggle('warn',warn);}}
  function box(l,v){const e=document.createElement('div');e.className='recommend-stat';const label=document.createElement('span');label.textContent=l;const value=document.createElement('strong');value.textContent=v;e.append(label,value);return e;}
  function row(cols){const tr=document.createElement('tr');for(const [s,head] of cols){const cell=document.createElement(head?'th':'td');cell.textContent=s;tr.append(cell);}return tr;}
  function render(data){
    const nums=$('historyNumbers'),content=$('historyResults');if(!nums||!content)return;
    nums.replaceChildren();content.replaceChildren();nums.hidden=false;
    const detail=data.summary?.['5']||{};
    nums.append(box('검증 대상 후보',`${safeInt(data.trades_total)}건`),
       box('10거래일 결과 확인',`${safeInt(data.fully_verified)}/${safeInt(data.trades_total)}건`),
       box('5거래일 적중률',detail.hit_pct==null?'미확인':`${Number(detail.hit_pct).toFixed(1)}%`),
       box('5거래일 평균 순수익',signed(detail.mean_net_pct)));
    const note=document.createElement('p');note.className='recommend-status';
    note.textContent=`${data.market==='all'?'코스피 + 코스닥':data.market==='kospi'?'코스피':'코스닥'} · 과거 ${data.analyzed_weeks}개 주간 기준일 · 편도 ${data.cost_bps_per_side}bp, 왕복 ${data.cost_bps_roundtrip}bp · ${data.status==='complete'?'모든 10거래일 평가 완료':'자료 부족 / 일부 미확인'}\n${data.time_rule}`;
    content.append(note);
    const heading=document.createElement('h4');heading.textContent='과거 후보별 보유기간 성과';content.append(heading);
    const outer=document.createElement('div');outer.className='history-table-wrap';
    const table=document.createElement('table');table.className='history-table';
    const head=document.createElement('thead');head.append(row([['보유기간',true],['완료',true],['적중률',true],['평균 순수익',true],['최저 순수익',true]]));table.append(head);
    const tbody=document.createElement('tbody');
    for(const h of [1,3,5,10]){const s=data.summary?.[String(h)]||{};
      tbody.append(row([[`${h}거래일`,false],[`${safeInt(s.samples)}건`,false],[s.hit_pct==null?'미확인':Number(s.hit_pct).toFixed(1)+'%',false],[signed(s.mean_net_pct),false],[signed(s.worst_net_pct),false]]));
    }table.append(tbody);outer.append(table);content.append(outer);
    if(data.market==='all'&&data.by_market){
      const byHead=document.createElement('h4');byHead.textContent='시장별 5거래일 비교';content.append(byHead);
      const markets=document.createElement('div');markets.className='recommend-numbers';
      for(const [market,label] of [['kospi','코스피'],['kosdaq','코스닥']]){
        const metric=data.by_market?.[market]?.['5']||{};
        markets.append(box(`${label} 검증 ${safeInt(metric.samples)}건`,metric.hit_pct==null?'미확인':`${Number(metric.hit_pct).toFixed(1)}% 적중 · ${signed(metric.mean_net_pct)}`));
      }
      content.append(markets);
    }
    const x=document.createElement('details');x.className='install-guide recommend-help';
    const summary=document.createElement('summary');summary.textContent='기준일별 검증 내용 보기';x.append(summary);
    for(const item of data.by_date||[]){const d=document.createElement('p'),s=item.session_5||{};
      d.textContent=`${item.date} · 상위 ${item.selected}개 후보 · 5거래일 확인 ${s.samples}건 · 적중 ${s.hit_pct==null?'미확인':s.hit_pct+'%'} · 평균 순수익 ${signed(s.mean_net_pct)}`;x.append(d);}
    content.append(x);
    const warn=document.createElement('p');warn.className='note';
    warn.textContent='중요: 이 통계는 최종 NOVA 매수 추천이 아닌 1차 유동성 후보의 과거 결과입니다. 데이터 누락, 상장폐지·생존자 편향, KIS 가격 조정, 적은 표본과 시장상황에 주의하세요. 누적 포트폴리오 수익률·MDD는 계산하지 않았습니다. 미래 수익 확률이 아닙니다.';
    content.append(warn);
    if(data.status!=='complete'){
      const bad=document.createElement('p');bad.className='recommend-status warn';
      bad.textContent=`불완전한 평가: ${safeInt(data.fully_verified)}/${safeInt(data.trades_total)}건만 10거래일 평가 가능. 누락 종목의 실제 결과는 알 수 없어 전체 적중률로 해석할 수 없습니다.`;
      content.append(bad);
    }
  }
  function stop(){if(poller){clearInterval(poller);poller=null;}}
  async function getState(){
    if(preview){status('오프라인 체험판은 실데이터 과거 검증을 수행하지 않습니다.');return;}
    try{
      const response=await fetch('/api/recommendations/backtest/status',{headers:headers(),credentials:'same-origin',cache:'no-store'});
      const d=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof d.detail==='string'?d.detail:`상태 조회 HTTP ${response.status}`);
      if(!d.fsc_configured){status('금융위원회 API 키가 설정되지 않았습니다.',true);stop();return;}
      if(d.status==='running'){
        running=true;$('historyRun').disabled=true;
        status(d.progress?.message||'과거 주간 시세를 검증 중입니다.');if(!poller)poller=setInterval(()=>{if(!document.hidden&&window.NOVAConnect?.currentView()==='research')getState();else stop();},5500);return;
      }
      running=false;stop();$('historyRun').disabled=false;
      if(d.status==='failed'){status(`과거 검증 실패: ${d.error?.message||'공급원 조회 오류'}`,true);return;}
      if(d.status==='complete'&&d.result){status(`백테스트 완료 · 과거 ${d.result.analyzed_weeks}개 주간 기준일 · ${d.result.trades_total}개 후보 중 ${d.result.fully_verified}개 평가 완료`);render(d.result);return;}
      status('과거 검증 준비 완료 · 검증 기간을 선택한 뒤 실행하세요.');
    }catch(e){running=false;stop();$('historyRun').disabled=false;status(e.message||'과거 검증 상태 조회에 실패했습니다.',true);}
  }
  async function run(){
    if(preview){status('오프라인 체험판에서는 실데이터 검증이 불가능합니다.');return;}
    if(running)return;
    $('historyRun').disabled=true;status('과거 주간 기준일의 전체 시장 시세를 수집합니다.');
    $('historyNumbers').hidden=true;$('historyResults').replaceChildren();
    try{
      const response=await fetch('/api/recommendations/backtest/run',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({market:$('historyMarket')?.value||'all',weeks:Number($('historyWeeks')?.value||4),cost_bps:Number($('costInput')?.value||15)})});
      const d=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof d.detail==='string'?d.detail:(d.detail?.message||`HTTP ${response.status}`));
      if(d.status==='complete'&&d.result){render(d.result);status('20분 이내 검증 결과 재사용');$('historyRun').disabled=false;return;}
      running=true;getState();
    }catch(e){running=false;$('historyRun').disabled=false;status(e.message||'검증 요청 실패',true);}
  }
  window.addEventListener('DOMContentLoaded',()=>{
    if(!$('historyRun'))return;
    $('historyRun').onclick=run;
    document.querySelector('[data-view="research"]')?.addEventListener('click',()=>{if(!running)getState();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(running&&window.NOVAConnect?.currentView()==='research')getState();});
  });
})();
