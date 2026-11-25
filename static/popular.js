/* NOVA v4.5 market-activity observation. Naver popularity is official external link only. */
'use strict';
(()=>{
  const $=id=>document.getElementById(id);
  const preview=Boolean(window.NOVA_PREVIEW_DATA);
  const headers=()=>{const out={};try{const token=sessionStorage.getItem('nova3-token');if(token)out.Authorization='Bearer '+token;}catch{}return out;};
  const fmt=new Intl.NumberFormat('ko-KR',{maximumFractionDigits:1});
  const compact=n=>Number(n)>=1e12?fmt.format(Number(n)/1e12)+'조 원':fmt.format(Number(n)/1e8)+'억 원';
  let state=null,timer=null,polling=false;
  const visible=()=>window.NOVAConnect?.currentView()==='recommendations'&&Boolean($('popularPanel')?.open)&&!document.hidden;
  const say=(message,warn=false)=>{const x=$('popularStatus');if(!x)return;x.textContent=message;x.classList.toggle('warn',warn);};
  const resolvedSource=()=>{
    const chosen=$('popularSource')?.value||'auto';
    if(chosen!=='auto')return chosen;
    return state?.fsc_configured?'fsc':'krx';
  };
  function render(){
    const target=$('popularResults');if(!target)return;
    target.replaceChildren();
    const result=state?.result;
    if(state?.status!=='complete'||!result)return;
    if(state.source!==resolvedSource()){
      say('이전 조회는 다른 시세 공급원입니다. 현재 설정으로 다시 조회하세요.');return;
    }
    const market=$('popularMarket').value,kind=$('popularKind').value;
    const items=result.rankings?.[market]?.[kind];
    if(!Array.isArray(items)){say('시장 활동 순위의 응답 형식을 확인할 수 없습니다.',true);return;}
    say(`기준일 ${result.data_date} · ${result.source} · ${result.universe_count}종목 기준 · 실시간/네이버 조회 인기 아님${result.snapshot_date_status==='OLDER_THAN_EXPECTED'?' · 공식 예상일보다 오래된 자료':''}`,
      result.snapshot_date_status==='OLDER_THAN_EXPECTED');
    const note=document.createElement('p');note.className='popular-note';note.textContent=result.ranking_definitions?.[kind]||'NOVA 시장 활동 순위';target.append(note);
    if(!items.length){const p=document.createElement('p');p.className='recommend-empty';p.textContent='이 조건의 종목이 없습니다. 추천 종목이 0개라는 뜻은 아닙니다.';target.append(p);return;}
    for(const x of items){
      const article=document.createElement('article');article.className='popular-card';
      const heading=document.createElement('div');heading.className='popular-head';
      const rank=document.createElement('span');rank.className='popular-rank';rank.textContent=String(x.rank);
      const symbol=document.createElement('div');symbol.className='popular-symbol';
      const name=document.createElement('strong');name.textContent=x.name;
      const sub=document.createElement('small');sub.textContent=`${x.symbol} · ${x.market==='kospi'?'코스피':'코스닥'}`;
      symbol.append(name,sub);
      const value=document.createElement('strong');value.className='popular-value';
      value.textContent=kind==='turnover'?compact(x.turnover_krw):kind==='volume'?fmt.format(x.volume)+'주':`${x.change_pct>0?'+':''}${fmt.format(x.change_pct)}%`;
      heading.append(rank,symbol,value);article.append(heading);
      const button=document.createElement('button');button.type='button';button.className='recommend-open';button.textContent='종목 차트 · 매수 판단 보기';
      button.addEventListener('click',()=>window.NOVAConnect?.openRecommendation(x.symbol,x.name));article.append(button);
      target.append(article);
    }
    const footer=document.createElement('p');footer.className='note';footer.textContent='순위는 조회량·검색량이 아닌 거래 활동 기준. 매수 추천이나 적중률을 의미하지 않으며 당일 실시간 시세도 아닙니다.';target.append(footer);
  }
  function stop(){if(timer){clearInterval(timer);timer=null;}}
  async function requestState(){
    if(polling||!visible())return;
    if(preview){say('오프라인 미리보기에서 실제 시세를 조회하지 않습니다. 네이버 공식 링크를 사용하세요.');return;}
    polling=true;
    try{
      const r=await fetch('/api/popular/status',{headers:headers(),cache:'no-store'});
      const d=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:`상태 조회 HTTP ${r.status}`);
      state=d;
      if(d.status==='running'){
        say('코스피·코스닥 공식 일별 시세를 조회하고 있어요. 기존 추천 신호는 바뀌지 않습니다.');
        if(!timer)timer=setInterval(()=>{if(visible())requestState();else stop();},6000);
      }else{
        stop();
        if(d.status==='failed')say(`순위 조회 실패: ${d.error?.message||'공급원 응답 오류'}`,true);
        else if(d.status==='complete')render();
        else say('거래대금·거래량·등락률 기준 TOP 20을 조회할 수 있습니다. 최근 장 마감 자료로 제공하며 실시간 순위는 아닙니다.');
      }
    }catch(e){stop();say(e.message||'순위 조회 상태를 확인할 수 없습니다.',true);}
    finally{polling=false;}
  }
  async function run(){
    if(preview){say('오프라인 체험판에서는 실제 시장 순위를 수집하지 않습니다.',true);return;}
    const button=$('popularLoad');if(button)button.disabled=true;
    say('전체 시장 일별 주식시세를 확인 중입니다.');
    try{
      const r=await fetch('/api/popular/run',{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({source:$('popularSource').value}),cache:'no-store'});
      const d=await r.json().catch(()=>({}));
      if(!r.ok){const detail=d.detail;throw new Error(typeof detail==='string'?detail:(detail?.message||`순위 조회 HTTP ${r.status}`));}
      state=d;
      if(d.status==='complete')render();else{say(d.notice||'시장 자료 수집을 시작했습니다.');stop();timer=setInterval(()=>{if(visible())requestState();else stop();},6000);}
    }catch(e){say(e.message||'순위를 수집하지 못했습니다.',true);}
    finally{if(button)button.disabled=false;}
  }
  function onViewChange(next){
    if(next==='recommendations'&&$('popularPanel')?.open)requestState();else stop();
  }
  window.NOVAPopular={onViewChange};
  window.addEventListener('DOMContentLoaded',()=>{
    const b=$('popularLoad');if(!b)return;
    $('popularPanel').addEventListener('toggle',()=>{if(visible())requestState();else stop();});
    b.addEventListener('click',run);
    $('popularKind').addEventListener('change',render);
    $('popularMarket').addEventListener('change',render);
    $('popularSource').addEventListener('change',()=>{state=null;stop();$('popularResults').replaceChildren();say('공급원을 변경했어요. 새 순위를 조회하세요.');});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(visible())requestState();});
  });
})();
