/* NOVA v4.5: on-demand, authenticated FSC T+1 or KRX EOD scanner. No orders. */
'use strict';
(()=>{
  const $ = id=>document.getElementById(id);
  const preview=Boolean(window.NOVA_PREVIEW_DATA);
  let timer=null, busy=false, lastJob='', shownResult='';
  const viewOpen=()=>window.NOVAConnect?.currentView()==='recommendations'&&!document.hidden;
  const won=v=>v==null||!Number.isFinite(Number(v))?'미확인':new Intl.NumberFormat('ko-KR',{maximumFractionDigits:0}).format(Number(v))+'원';
  const money=v=>v==null?'—':new Intl.NumberFormat('ko-KR',{maximumFractionDigits:1}).format(Number(v)/1e8)+'억원';
  const headers=()=>{const h={};try{const t=sessionStorage.getItem('nova3-token');if(t)h.Authorization='Bearer '+t;}catch{}return h;};
  const status=(message,warn=false)=>{const p=$('recommendStatus');if(p){p.textContent=message;p.classList.toggle('warn',warn);}};
  function setSearchState(kind,{message='',progress=null}={}){
    const panel=$('recommendScanState'),label=$('recommendScanLabel'),detail=$('recommendScanDetail');
    const icon=$('recommendScanIcon'),count=$('recommendScanCount'),bar=$('recommendProgressTrack'),fill=$('recommendProgressFill'),button=$('recommendScan');
    if(!panel||!label||!detail||!icon||!count||!bar||!fill||!button)return;
    const running=kind==='running',finished=kind==='complete';
    const phase=running?(progress?.phase==='deep'?'deep':'provider'):'none';
    const done=Math.max(0,Number(progress?.done)||0),total=Number(progress?.total)||0;
    const hasRealProgress=running&&phase==='deep'&&total>0;
    panel.dataset.state=kind;panel.dataset.phase=hasRealProgress?'deep':running?'provider':'none';
    const names={idle:'검색 대기',running:'검색 진행 중',complete:'검색 완료',failed:'검색 실패',unavailable:'검색 상태 확인 불가'};
    label.textContent=names[kind]||names.idle;
    icon.textContent=running?'↻':finished?'✓':kind==='failed'||kind==='unavailable'?'!':'○';
    detail.textContent=message||(running?'시세 및 분석 자료를 확인하고 있습니다.':finished?'최종 결과가 아래에 표시됩니다.':'검색 버튼을 누르면 진행 상황이 표시됩니다.');
    if(hasRealProgress){
      const bounded=Math.min(done,total),percent=Math.round(bounded/total*100);
      count.textContent=`${bounded} / ${total}`;count.hidden=false;
      fill.style.width=percent+'%';bar.setAttribute('aria-valuenow',String(percent));
      bar.setAttribute('aria-valuetext',`심층 분석 ${bounded} / ${total}종목 완료`);
    }else{
      count.hidden=true;fill.style.width=finished?'100%':'0%';
      bar.removeAttribute('aria-valuenow');bar.setAttribute('aria-valuetext',running?'시장 자료 확인 중':finished?'검색 완료':'검색 진행 전');
    }
    button.disabled=running||kind==='unavailable';
    button.setAttribute('aria-busy',String(running));
    button.textContent=running?(hasRealProgress?`↻ 심층 분석 중 · ${Math.min(done,total)}/${total}`:'↻ 시장자료 검색 중…'):'⌕ 전체 시장 검색 시작';
  }
  function cell(title,value){const card=document.createElement('div');card.className='recommend-stat';const l=document.createElement('span');l.textContent=title;const v=document.createElement('strong');v.textContent=String(value);card.append(l,v);return card;}
  function render(res){
    const nums=$('recommendNumbers'), list=$('recommendResults');if(!nums||!list)return;
    nums.replaceChildren();list.replaceChildren();nums.hidden=false;
    nums.append(cell(res.marketwide?'시장 전체 조회':'상장목록/시장 범위',res.total_market_rows!=null?`${res.total_market_rows}종목`:'제한 목록'),
      cell('1차 통과',`${res.screen_passed??0}종목`),
      cell('NOVA 심층 분석',`${res.deep_succeeded??0}/${res.deep_target??0}종목`),
      cell('엄격 매수 후보',`${res.recommended_count??0}종목`),
      cell('관심 후보 (매수 아님)',`${res.interest_count??0}종목`));
    const pre=document.createElement('div');pre.className='recommend-status';
    const m=(res.markets_scanned||[]).map(x=>x==='kospi'?'코스피':x==='kosdaq'?'코스닥':'국내 시장').join(' + ');
    pre.textContent=`${res.data_source||'일별 자료'} · 기준일 ${res.data_date||'미확인'} · ${m}\n${res.coverage_scope||'일별 스냅샷 기반 제한 분석'} · 1차 ${res.screen_passed??0}개 → ${res.deep_target??0}개 심층 검증 · 실패 ${res.deep_failed||0}개. 실시간 추천이 아닙니다.`;
    list.appendChild(pre);
    if(res.marketwide && res.rejection_codes){
      const groups=[
        ['시세·날짜 확인', ['PRICE_STALE','SNAPSHOT_DATE_LAG','ASOF_MISMATCH','BAR_NOT_CURRENT']],
        ['기술 매수 조건', ['TECHNICAL_NOT_BUY','TECHNICAL_SCORE','POLICY_NOT_BUY']],
        ['재무 확인·위험', ['FINANCIAL_UNVERIFIED','FINANCIAL_RISK']],
        ['과거 검증 부족', ['VALIDATION_UNVERIFIED','VALIDATION_WEAK']],
        ['손익비·가격 검증', ['RISK_REWARD','PRICE_STRUCTURE']],
        ['조회 오류', ['DEEP_ANALYSIS_ERROR']]
      ];
      const total=Object.values(res.rejection_codes).reduce((a,b)=>a+(Number(b)||0),0);
      const parts=groups.map(([name,codes])=>[name,codes.reduce((n,c)=>n+(Number(res.rejection_codes[c])||0),0)]).filter(x=>x[1]>0);
      const line=document.createElement('p');line.className='note';
      line.textContent=`심층 검토 제외 ${total}개: ${parts.map(([name,n])=>`${name} ${n}개`).join(' · ')||'분류된 사유 없음'}. 아래에서 종목별 정확한 이유를 확인할 수 있습니다.`;
      list.append(line);
    }
    if(res.snapshot_date_status==='OLDER_THAN_EXPECTED'){
      const warning=document.createElement('p');warning.className='note';
      warning.textContent=`공식 제공 예상 기준일 ${res.expected_source_date||'미확인'}보다 오래된 ${res.data_date} 자료를 사용했습니다. 휴장/공급 지연을 확인하기 전에는 확정 매수로 해석하지 마세요.`;
      list.append(warning);
    }
    if(res.selection_lanes){const p=document.createElement('p');p.className='note';p.textContent=`후보 구성: 유동성 ${res.selection_lanes['유동성']||0}개 · 거래활동 ${res.selection_lanes['거래활동']||0}개 · 수급 자료 확보 관심후보 ${res.flow_auxiliary_coverage||0}개. 수급은 매수 승인에 사용하지 않습니다.`;list.append(p);}
    if(res.scan_freshness_method){const p=document.createElement('p');p.className='note';p.textContent=`시세 신선도: ${res.scan_freshness_method} · 현재가가 아닌 기준일 스윙 연구`;list.append(p);}
    // Independent research display; failure must not hide legacy candidates.
    try{window.NOVAPatternResearch?.render(list,res);}catch{const p=document.createElement('p');p.className='note';p.textContent='신호패턴 연구 화면 표시 실패 · 기존 후보는 유지됩니다.';list.append(p);}
    const items=Array.isArray(res.recommendations)?res.recommendations:[];
    const strictHeading=document.createElement('h3');strictHeading.textContent=`엄격 매수 후보 · ${items.length}개`;list.append(strictHeading);
    if(items.length){const ranking=document.createElement('p');ranking.className='recommend-rank-intro';ranking.textContent='엄격 매수 순위: 최근 별도 검증의 Wilson 신뢰하한 → 평균 순수익 → 검증 표본 → 손익비 순. 같은 검색 결과 안에서의 상대 순위이며 실제 상승 확률이나 매수 우선 명령이 아닙니다.';list.append(ranking);}
    if(!items.length){const blank=document.createElement('div');blank.className='recommend-empty';
      blank.textContent=(res.deep_failed?'심층 자료 조회 실패가 있어 일부 평가가 불완전합니다. ':'')+'이번 검색에서 모든 기술·재무·검증 조건을 통과한 종목이 없습니다. 추천 개수를 맞추기 위해 무리하게 추가하지 않습니다.';
      list.appendChild(blank);}
    for(const [i,r] of items.entries()){
      const card=document.createElement('article');card.className='recommend-card';
      const head=document.createElement('div');head.className='recommend-title';
      const medal=document.createElement('span');medal.className='recommend-rank-number';medal.textContent=`${r.rank||i+1}위`;
      const name=document.createElement('strong');name.className='recommend-name';name.textContent=r.name;
      const tag=document.createElement('em');tag.textContent='엄격 매수';head.append(medal,name,tag);card.append(head);
      const meta=document.createElement('p');meta.className='recommend-meta';meta.textContent=`${r.symbol} · ${r.market==='kospi'?'코스피':'코스닥'} · 기준일 ${r.asof} · ${r.price_data_source || ('시장 종가 '+won(r.close_krx))} · NOVA 완료봉 ${r.last_completed_bar||'미확인'}`;card.append(meta);
      const reasons=document.createElement('ul');reasons.className='recommend-reasons';
      for(const t of r.reason||[]){const li=document.createElement('li');li.textContent=t;reasons.appendChild(li);}card.append(reasons);
      const prices=document.createElement('div');prices.className='recommend-prices';
      for(const [l,v] of [['분석 기준가',r.reference],['손절 참고',r.stop],['목표 참고',r.target]]){
        const box=document.createElement('div');const label=document.createElement('span');label.textContent=l;const val=document.createElement('strong');val.textContent=won(v);box.append(label,val);prices.append(box);
      }card.append(prices);
      const open=document.createElement('button');open.type='button';open.className='recommend-open';open.textContent='종목 상세 분석 열기 ›';open.onclick=()=>window.NOVAConnect?.openRecommendation(r.symbol,r.name);card.append(open);
      const flow=document.createElement('button');flow.type='button';flow.className='recommend-open';flow.textContent='외국인·기관 수급 / 현재가 보기';flow.onclick=()=>window.NOVAFlow?.openSymbol(r.symbol);card.append(flow);
      list.append(card);
    }
    const interests=Array.isArray(res.interest_candidates)?res.interest_candidates:[];
    const sub=document.createElement('h3');sub.textContent=`관심 후보 · ${interests.length}개 (매수신호 아님)`;list.append(sub);
    const note=document.createElement('p');note.className='note';note.textContent=`관심 후보 순위: 재무 확인(PASS) → 기술 조건 수 → 거래량·모멘텀 → 손익비. 검색 대상 중 ${res.interest_eligible_total??interests.length}개가 관심 조건을 충족했고 최대 20개만 표시합니다. 순위는 매수 신호가 아닙니다. 수급은 기존에 조회된 동일 기준일의 자료만 별도로 표시하며 순위에 사용하지 않습니다.`;list.append(note);
    if(!interests.length){const empty=document.createElement('p');empty.className='recommend-empty';empty.textContent='추세와 변동성 등 관심 조건을 충족한 종목이 없습니다. 관심 후보는 무리하게 늘리지 않습니다.';list.append(empty);}
    for(const [i,r] of interests.entries()){
      const card=document.createElement('article');card.className='recommend-card interest-ranked';
      const head=document.createElement('div');head.className='recommend-title';
      const medal=document.createElement('span');medal.className='recommend-rank-number';medal.textContent=`${r.rank||i+1}위`;
      const name=document.createElement('strong');name.className='recommend-name';name.textContent=r.name||r.symbol;
      const tag=document.createElement('em');tag.textContent='연구 관심 · BUY 아님';head.append(medal,name,tag);card.append(head);
      const meta=document.createElement('p');meta.className='recommend-meta';
      meta.textContent=`${r.symbol} · ${r.market==='kosdaq'?'코스닥':'코스피'} · 기준일 ${r.asof||'미확인'} · 평가 ${r.passed_count??0}/5 · 기준가 ${won(r.reference)} · 참고 손익비 ${r.risk_reward??'미확인'}`;
      card.append(meta);
      const reason=document.createElement('p');reason.className='note';reason.textContent=`매수 보류 이유: ${r.reason||'미확인'} · 재무검토 ${r.financial_gate||'미확인'} · 향후 수익확률 아님`;card.append(reason);
      if(r.technical_review){const t=document.createElement('p');t.className='note';const c=r.technical_review;t.textContent=`완료 일봉 기술 검사 · 추세 ${c.trend===true?'충족':'미충족'} · 거래량 ${c.participation===true?'충족':'미충족'} · 모멘텀 ${c.momentum===true?'충족':'미충족'} · 과열 제한 ${c.extension===true?'충족':'미충족'} · 선별경로 ${r.selection_lane||'—'}`;card.append(t);}
      const f=r.flow_auxiliary;if(f){const n=document.createElement('p');n.className='note';n.textContent=f.status==='OBSERVED_SHARES_5D'?`수급 연구 참고(매수조건 아님) · ${f.label} · 5일 외국인 ${f.foreign_5d?.toLocaleString('ko-KR')}주 / 기관 ${f.institution_5d?.toLocaleString('ko-KR')}주 · ${f.asof} 기준 · 독립 교차검증 전`:'수급 연구 참고 · 자료 미확인 (미조회/날짜 또는 수량 부족)';card.append(n);}
      const b=document.createElement('button');b.type='button';b.className='recommend-open';b.textContent='상세 기술 분석 열기 ›';
      b.onclick=()=>window.NOVAConnect?.openRecommendation(r.symbol,r.name);card.append(b);
      const flowButton=document.createElement('button');flowButton.type='button';flowButton.className='recommend-open';flowButton.textContent='투자자 수급 직접 확인 ›';flowButton.onclick=()=>window.NOVAFlow?.openSymbol(r.symbol);card.append(flowButton);
      list.append(card);
    }
    const failures=res.rejection_summary||{};
    if(Object.keys(failures).length){const info=document.createElement('details');info.className='install-guide recommend-help';
      const h=document.createElement('summary');h.textContent='심층 검토 제외 사유 (개별 원인별 건수)';info.append(h);
      for(const [why,count] of Object.entries(failures).sort((a,b)=>b[1]-a[1])){const p=document.createElement('p');p.textContent=`${why}: ${count}건`;info.append(p);}list.append(info);}
    const foot=document.createElement('p');foot.className='note';foot.textContent=`${res.data_lag||'장 마감 자료'} 기준. 조회된 날짜의 장 마감 자료 기준입니다. 총 ${res.total_market_rows}개 종목 각각에 대해 장기 기술적 백테스트를 실행한 것은 아닙니다. 종목별 과거 성과는 미래 확률이 아닙니다. 서버가 재시작되면 기록이 사라집니다.`;list.append(foot);
    if((res.rejected||[]).length){const info=document.createElement('details');info.className='install-guide recommend-help';const summ=document.createElement('summary');summ.textContent=`심층 검토에서 제외된 종목 ${(res.rejected||[]).length}개 보기`;info.append(summ);for(const x of res.rejected||[]){const p=document.createElement('p');
        p.textContent=`${x.name} (${x.symbol}): ${x.reason} [${x.reason_code||'미분류'}]${x.last_completed_bar?' · KIS 완료봉 '+x.last_completed_bar.slice(0,10):''}`;
        info.append(p);}list.append(info);}
  }
  async function fetchState(){
    if(preview){setSearchState('unavailable',{message:'체험판에서는 검색이 실행되지 않습니다.'});status('오프라인 체험판에서는 실데이터 전체 검색을 실행하지 않습니다.',true);return;}
    if(busy)return;
    busy=true;
    try{
      const r=await fetch('/api/recommendations/status',{headers:headers(),cache:'no-store'});
      const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:`상태 확인 실패 HTTP ${r.status}`);
      // A previous scan can still run when controls were changed in this tab.
      // Never claim 'idle' or enable the button while the server is scanning.
      if(d.status==='running'){
        if(shownResult){$('recommendNumbers').hidden=true;$('recommendResults').replaceChildren();shownResult='';}
        const mismatch=d.depth!==Number($('recommendDepth')?.value||48)||
          d.market!==($('recommendMarket')?.value||'all')||
          (d.source && $('recommendSource')?.value!=='auto'&&d.source!==$('recommendSource').value);
        const progress=d.progress||{};
        const suffix=mismatch?' · 현재 검색은 이전 선택값으로 실행 중입니다. 완료 후 다시 검색할 수 있습니다.':'';
        setSearchState('running',{message:(progress.message||'국내 종목 검색 중')+suffix,progress});
        status(`검색 진행 중 · ${progress.phase==='deep'?'종목별 심층 분석':'시장 일별 자료 확인'} · 완료 전 매수 후보가 확정되지 않습니다.`);
        startPoll();return;
      }
      stopPoll();
      const chosen=$('recommendSource')?.value||'auto';
      const isReady=chosen==='fsc'?d.fsc_configured:chosen==='krx'?d.krx_configured:(d.fsc_configured||d.krx_configured);
      if(!isReady){$('recommendNumbers').hidden=true;$('recommendResults').replaceChildren();shownResult='';setSearchState('unavailable',{message:'선택한 공급원의 인증키가 필요합니다.'});status('선택한 데이터 공급원의 인증키가 없습니다. FSC_STOCK_API_KEY 또는 KRX_API_KEY를 Render에 설정하세요.',true);return;}
      const requestedMarket=$('recommendMarket')?.value||'all';
      const selectedSource=chosen==='auto'?(d.fsc_configured?'fsc':'krx'):chosen;
      if((d.source&&d.source!==selectedSource)||(d.depth&&d.depth!==Number($('recommendDepth')?.value||48))||(d.market&&d.market!==requestedMarket)){
        setSearchState('idle',{message:'선택 조건이 이전 검색과 다릅니다. 새 검색을 실행하세요.'});
        status('이전 검색과 다른 조건을 선택했습니다. 현재 조건으로 새 검색을 실행할 수 있습니다.');return;
      }
      if(d.status==='failed'){
        $('recommendNumbers').hidden=true;$('recommendResults').replaceChildren();shownResult='';
        const message=d.error?.message||'공급원 오류';
        setSearchState('failed',{message});status(`검색 실패: ${message}`,true);return;
      }
      if(d.status==='complete'&&d.result){
        setSearchState('complete',{message:`${d.result.data_date||'기준일 미확인'} · 엄격 매수 ${d.result.recommended_count||0}개 · 관심 ${d.result.interest_count||0}개`});
        status(`검색 완료 · ${d.result.data_date} 기준 · ${d.result.data_source||'일별'} · 실시간 아님`);
        if(shownResult!==d.job_id){render(d.result);shownResult=d.job_id;}
        return;
      }
      setSearchState('idle',{message:'검색 준비 완료. 버튼을 누르면 전체 시장 분석을 시작합니다.'});
      status(`데이터 공급원 연결 준비 · ${chosen==='auto'?'금융위원회 우선 자동 선택':chosen==='fsc'?'금융위원회 T+1':'KRX 일별'} · 일별 기준 자료를 확인하세요.`);
    }catch(e){$('recommendNumbers').hidden=true;$('recommendResults').replaceChildren();shownResult='';setSearchState('unavailable',{message:'서버 연결 또는 인증 상태를 확인할 수 없습니다. 페이지를 새로고침해 주세요.'});status(e.message||'검색 상태 확인 실패',true);stopPoll();}
    finally{busy=false;}
  }
  async function scan(){
    if(preview)return;
    stopPoll();shownResult='';
    $('recommendNumbers').hidden=true;$('recommendResults').replaceChildren();
    setSearchState('running',{message:'검색 시작 요청 중… 시장별 일별 데이터를 확인합니다.',progress:{phase:'provider'}});
    status('검색 요청을 전송했습니다. 결과가 준비될 때까지 화면에 진행 상태가 표시됩니다.');
    try{
      const r=await fetch('/api/recommendations/run',{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({market:$('recommendMarket').value,source:$('recommendSource')?.value||'auto',depth:Number($('recommendDepth')?.value||48)}),cache:'no-store'});
      const d=await r.json().catch(()=>({}));
      if(!r.ok){const detail=d.detail;throw new Error(typeof detail==='string'?detail:(detail?.message||`검색 요청 실패 HTTP ${r.status}`));}
      if(d.status==='complete'&&d.result){
        setSearchState('complete',{message:`최근 ${d.result.data_date||'일별'} 검색 결과를 재사용했습니다.`});
        status('최근 20분 이내 동일 조건의 검색 결과를 불러왔습니다. 새로운 검색이 아닙니다.');
        render(d.result);shownResult=d.job_id;return;
      }
      setSearchState('running',{message:d.progress?.message||'전체 시장 검색 중…',progress:d.progress});
      status('검색 진행 중 · 완료 전의 결과는 확정 후보가 아닙니다.');
      startPoll();fetchState();
    }catch(e){setSearchState('failed',{message:e.message||'검색 시작 실패'});status(e.message||'검색을 시작할 수 없습니다.',true);}
  }
  async function diagnose(){
    const btn=$('recommendDiagnose'),out=$('recommendDiagnosis');
    if(!btn||!out)return;
    if(preview){out.hidden=false;out.textContent='체험판에서는 실제 KRX 연결을 진단하지 않습니다.';return;}
    btn.disabled=true;out.hidden=false;out.replaceChildren();out.textContent='KRX 운영 API를 코스피·코스닥별로 확인 중입니다…';
    try{
      const resp=await fetch('/api/recommendations/diagnose',{method:'POST',headers:headers(),cache:'no-store'});
      const d=await resp.json().catch(()=>({}));
      if(!resp.ok)throw new Error(typeof d.detail==='string'?d.detail:`진단 요청 실패 HTTP ${resp.status}`);
      out.replaceChildren();
      const top=document.createElement('p');top.textContent=`운영 API 진단 기준일: ${d.date||'미설정'} · 인증키: ${d.configured?'서버에 설정됨':'미설정'}`;out.append(top);
      for(const item of d.results||[]){
        const itemNode=document.createElement('p');
        const m=item.market==='kospi'?'코스피':'코스닥';
        const statusLabel=item.state==='success'?'정상 수신':item.state==='no_data'?'자료 없음':item.state==='auth_denied'?'인증/권한 실패':'연결 실패';
        const count=item.record_count!=null?` · 종목 ${item.record_count}건`:'';
        itemNode.textContent=`${m} (${item.api_id||'서비스'}): ${statusLabel} · HTTP ${item.http_status??'미확인'} · KRX 코드 ${item.krx_code??'없음'}${count} · ${item.detail||'원인 미확인'}`;
        out.append(itemNode);
      }
      const note=document.createElement('small');note.textContent='요청: 운영 API · GET · AUTH_KEY 헤더 전송(값 비공개). 인증 오류면 Render 개인 키 및 두 서비스 이용 승인을 확인하세요. 샘플 API 성공만으로 운영 인증은 확인되지 않습니다.';out.append(note);
    }catch(err){out.textContent=err.message||'진단을 수행할 수 없습니다.';}
    finally{btn.disabled=false;}
  }
  function stopPoll(){if(timer){clearInterval(timer);timer=null;}}
  function startPoll(){if(timer)return;timer=setInterval(()=>{if(viewOpen())fetchState();else if(document.hidden)stopPoll();},4500);}
  function onViewChange(next){
    if(next!=='recommendations'){stopPoll();return;}
    fetchState();
  }
  function init(){
    if(!$('recommendScan'))return;
    $('recommendScan').onclick=scan;
    const diag=$('recommendDiagnose');if(diag)diag.onclick=diagnose;
    const refreshOptions=()=>{shownResult='';const n=$('recommendNumbers');if(n)n.hidden=true;$('recommendResults').replaceChildren();fetchState();};
    const source=$('recommendSource');if(source)source.onchange=refreshOptions;
    if($('recommendDepth'))$('recommendDepth').onchange=refreshOptions;
    $('recommendMarket').onchange=refreshOptions;
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPoll();else if(viewOpen())fetchState();});
  }
  window.NOVAScanner={onViewChange,load:fetchState};
  window.addEventListener('DOMContentLoaded',init);
})();
