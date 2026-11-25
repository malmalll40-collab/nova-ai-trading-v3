/* NOVA v5.2 – isolated observational research and on-demand trade feed.
   No write routes, no trading order, no overrides to the existing decision. */
(()=>{
'use strict';
const byId=id=>document.getElementById(id);
let current=null;
let coreEvidenceSeq=0;
let referenceClose=null;
let inflight=null;
let socket=null;
let wsTarget=null;
let wsConnected=false;
let currentResearch=null;
let batchPoll=null;
let batchJob=null;
let priceAuditBusy=false;
let priceAuditSeq=0;
let dailyIntegrityBusy=false;
let dailyIntegritySeq=0;
const money=v=>Number.isFinite(Number(v))?Math.round(Number(v)).toLocaleString('ko-KR')+'원':'미확인';
const percent=v=>typeof v==='number'&&Number.isFinite(v)?`${v>0?'+':''}${v.toFixed(2)}%`:'표본 없음';
const unsignedPct=v=>typeof v==='number'&&Number.isFinite(v)?`${v.toFixed(2)}%`:'미확인';
const errMsg=msg=>String(msg||'조회 실패').slice(0,180);
const el=(tag,className='',text='')=>{const x=document.createElement(tag);if(className)x.className=className;if(text)x.textContent=text;return x;};
function status(text,error=false){const s=byId('v5ResearchStatus');if(!s)return;s.textContent=text;s.classList.toggle('v5-error',error);}
function stopSocket(message){
 if(socket){const old=socket;socket=null;try{old.close(1000,'Stopped by user');}catch{}}
 const value=byId('v5LiveValue');if(value)value.hidden=true;
 wsTarget=null;wsConnected=false;
 const button=byId('v5LiveToggle');if(button)button.textContent='체결 관찰 시작';
 const label=byId('v5LiveState');if(label&&message)label.textContent=message;
}
function setAnalysis(data){
 const key=data?.symbol&&data?.mode==='스윙'&&!data.is_demo?data.symbol:null;
 if(key!==current){if(inflight){inflight.abort();inflight=null;}
   currentResearch=null;
   coreEvidenceSeq++;byId("core541EvidenceResult")?.replaceChildren();
   const content=byId('v5ResearchContent');if(content)content.hidden=true;
   const tradeRows=byId('v51TradeRows');if(tradeRows)tradeRows.replaceChildren();
   priceAuditSeq++;const priceResults=byId('v531SourceAuditResults');if(priceResults){priceResults.replaceChildren();priceResults.hidden=true;}
   const priceStatus=byId('v531SourceAuditStatus');if(priceStatus)priceStatus.textContent=key?'가격 기준 비교는 별도 버튼으로 실행합니다.':'스윙 종목 분석 후 사용하세요.';
   dailyIntegritySeq++;const dailyResult=byId('v532DailyIntegrityResult');if(dailyResult){dailyResult.replaceChildren();dailyResult.hidden=true;}
   const dailyStatus=byId('v532DailyIntegrityStatus');if(dailyStatus)dailyStatus.textContent=key?'최신 KIS 일봉은 별도 버튼으로 검증할 수 있어요.':'스윙 종목 분석 후 사용하세요.';
   status(key?`${key.slice(0,6)} 과거 완료봉 연구 준비 · 버튼을 누르면 조회합니다.`:'스윙 실종목의 완료봉에서만 연구할 수 있습니다.');
   if(socket)stopSocket('종목 또는 분석 주기가 바뀌어 실시간 관찰을 종료했습니다.');
 }
 if(key!==current)byId('core560EvidenceResult')?.replaceChildren();
 current=key;
 if(byId('core560EvidenceRun'))byId('core560EvidenceRun').disabled=!key;
 renderCore541Current(data?.core541_current,data?.signal_label);
 if(key!==null && byId("core541EvidenceRun"))byId("core541EvidenceRun").disabled=false;
 else if(byId("core541EvidenceRun"))byId("core541EvidenceRun").disabled=true;
 if(byId("core542LegacyEvidenceRun"))byId("core542LegacyEvidenceRun").disabled=!key;
 if(byId("core550LegacyEvidenceRun"))byId("core550LegacyEvidenceRun").disabled=!key;
 referenceClose=Number.isFinite(Number(data?.reference))&&Number(data.reference)>0?Number(data.reference):null;
 const button=byId('v5ResearchRun');if(button)button.disabled=!key;
 const priceButton=byId('v531SourceAuditRun');if(priceButton)priceButton.disabled=!key||priceAuditBusy;
 const dailyButton=byId('v532DailyIntegrityRun');if(dailyButton)dailyButton.disabled=!key||dailyIntegrityBusy;
}
function metric(label,value){const row=el('div','v5-metric');row.append(el('span','',label),el('b','',value));return row;}
function methodCard(label,stats,reason=''){
 const c=el('article','v5-method-card');c.appendChild(el('strong','',label));
 if(!stats||stats.status!=='READY'){
   c.appendChild(el('p','v5-note',reason||stats?.reason||'검증 불가 · 자료 미확인'));return c;
 }
 c.append(metric('실제 가상거래',`${stats.completed_trades??0}건`));
 c.append(metric('가상 순수익률',Number(stats.completed_trades)>0?percent(stats.portfolio_return_pct):'평가할 거래 없음'));
 c.append(metric('최대 낙폭',Number(stats.completed_trades)>0?percent(stats.portfolio_mdd_pct):'평가할 거래 없음'));
 c.append(metric('거래 승률',percent(stats.win_rate_pct)));
 c.append(metric('진입 조건',`${stats.buy_opportunities??0}회`));
 return c;
}
const coreNames={core:'기존 5-Core',strength:'추세 강도 필터',adaptive:'5-Core Adaptive',hybrid:'5-Core Hybrid'};
const core543Names={rs_required:'상대강도 필수',entry_wider:'눌림 진입 완화',risk_chase:'추격 한도 3 ATR',transition_soft:'Hybrid 전환 완화',range_guard:'횡보 돌파 방어',early_turn:'상승 초기 전환',regime_switch:'국면별 통합',rs_hybrid:'상대강도·Hybrid 결합',regime_router:'국면별 선택 v2'};
const coreRegimes={strong_up:'강한 상승',mild_up:'완만한 상승',range:'횡보·전환',down:'하락'};
function renderCore541Current(p,baseline){
 const box=byId('core541Current');if(!box)return;box.replaceChildren();
 box.append(el('p','v5-note',`기존 실전 판단: ${baseline||'미확인'} · 별도 연구 결과`));
 if(!p||p.status!=='READY'){box.append(el('p','v5-note',p?.reason||'최신 연구 신호 자료 부족'));return;}
 box.append(el('p','v51-batch-meta',`${p.day} 완료봉 · 종목 ${p.regime_label} · ${p.market_regime_label||'지수 미확인'} · ADX ${p.adx} · EMA20 5일 기울기 ${p.ema20_slope_pct}% · 밴드폭 ${p.bandwidth_pct}% · ${p.confidence_label||'전향 검증 대기'}`));
 const grid=el('div','v53-compare-grid');for(const [k,label] of Object.entries(coreNames)){
 const c=el('article','v5-method-card');c.append(el('strong','',label),el('p',(k==='hybrid'?p.hybrid_signal:p.signals?.[k])==='BUY'?'v51-positive':'v5-note',(k==='hybrid'?p.hybrid_signal:p.signals?.[k])==='BUY'?'매수 후보 (연구)':'관망 (연구)'));grid.append(c);}
 box.append(grid);if(p.v552_route)box.append(el('p','v5-note',`국면별 선택 v2 경로: ${core543Names[p.v552_route]||coreNames[p.v552_route]||p.v552_route} · 연구 전용`));if(p.candidates){const d=el('details','v53-item');d.append(el('summary','','v5.5.2 연구 후보 판단 (실전 미반영)'));for(const [k,n] of Object.entries(core543Names))if(k in p.candidates)d.append(el('p','v51-batch-meta',`${n}: ${p.candidates[k]==='BUY'?'매수 후보':'관망'}`));box.append(d);}
 box.append(el('p','v5-note',`Hybrid 등급 ${p.hybrid_grade||'미확인'} · 세부 국면 ${p.subregime||'미확인'} · 규칙 등급, 확률 아님`));
 const checks=el('details','v53-item');checks.append(el('summary','','5-Core 조건·추세 진단'));
 for(const x of p.checks||[])checks.append(el('p','v51-batch-meta',`${x.pass?'충족':'미충족'} · ${x.label} · ${x.detail}`));
 checks.append(el('p','v5-note',`+DI ${p.plus_di} / -DI ${p.minus_di} · 밴드 수렴 ${p.compressed?'예':'아니오'} · 거래량 돌파 ${p.breakout?'예':'아니오'} · 눌림 ${p.pullback?'예':'아니오'}`));box.append(checks);
}
function overlapPanel(report){
 const detail=el('details','v53-item');detail.append(el('summary','','전략 판단 중복 검증 (매수 신호 단위)'));
 if(!report||report.status!=='READY'){detail.append(el('p','v5-note','공통 신호 표본 부족'));return detail;}
 detail.append(el('p','v5-note',`공통 ${report.samples}시점/종목-일 · 모든 전략 쌍 ${report.pairs.length}개 · 동일 매수 벡터 ${report.pairs.filter(p=>p.identical_observed).length}쌍`));
 for(const p of report.pairs){const names={...coreNames,...core543Names};detail.append(el('p','v51-batch-meta',`${names[p.a]||p.a} ↔ ${names[p.b]||p.b} · 동시 매수 ${p.both_buy} · 좌/우 단독 ${p.only_a}/${p.only_b} · 매수 겹침 ${p.jaccard_pct??'표본 없음'}%${p.identical_observed?' · 표본 내 동일':p.near_duplicate?' · 중복 후보':''}`));}
 detail.append(el('p','v5-note',report.limitations||''));return detail;
}
function coreFocusPanel(box,d){
 const panel=el('section','v53-evidence');panel.append(el('strong','','v5.5.2 · 5-Core 집중 검증 / Hybrid'));box.append(panel);
 if(!d||d.status!=='READY'){panel.append(el('p','v5-note',d?.reason||'5-Core 공통 검증 불가'));return;}
 panel.append(el('p','v5-note',`${d.start} ~ ${d.end} · ${d.samples} 공통 시점 · 고정 규칙 · 전략 채택 보류`));
 const g=el('div','v53-compare-grid');for(const [k,label] of Object.entries(coreNames)){
 const a=d.strategies[k],o=a.opportunities||{},z=d.changes[k]||{};const c=methodCard(label,a.portfolio);
 c.append(metric('매수 신호 적중률',unsignedPct(o.precision_pct)),metric('매수 비율',unsignedPct(o.coverage_pct)),metric('놓친 수익 기회',`${o.missed_positive_opportunities??'—'}건`),metric('차단 손실 / 수익 신호',`${z.blocked_negative??'—'} / ${z.blocked_positive??'—'}`),metric('비용 2배',a.stress_portfolio?.completed_trades?percent(a.stress_portfolio.portfolio_return_pct):'평가 불가'));g.append(c);}
 panel.append(g);
 for(const [k,label] of Object.entries({...Object.fromEntries(Object.entries(coreRegimes).map(([k,v])=>[k,'종목 '+v])),idx_up:'지수 상승',idx_range:'지수 횡보·전환',idx_down:'지수 하락'})){
 const r=k.startsWith('idx_')?d.market_regimes?.[k.slice(4)]:d.regimes[k];if(!r)continue;const detail=el('details','v53-item');detail.append(el('summary','',`${label} · ${r.samples}시점`));
 for(const [m,n] of Object.entries(coreNames)){const a=r.strategies[m],p=a.portfolio,o=a.opportunities;detail.append(el('p','v51-batch-meta',p?`${n}: ${p.completed_trades??0}건 · 수익률 ${p.completed_trades?percent(p.portfolio_return_pct):'거래 없음'} · MDD ${p.completed_trades?percent(p.portfolio_mdd_pct):'—'} · 신호 적중 ${unsignedPct(o?.precision_pct)}`:`${n}: 평가 불가`));}panel.append(detail);}
 for(const [k,label] of Object.entries({sideways:'횡보',up_transition:'상승 전환'})){const r=d.subregimes?.[k];if(!r)continue;const detail=el('details','v53-item');detail.append(el('summary','',`${label} · ${r.samples}시점`));for(const [m,n] of Object.entries(coreNames)){const p=r.strategies[m]?.portfolio;detail.append(el('p','v51-batch-meta',`${n}: ${p?.completed_trades??0}건 · ${p?.completed_trades?percent(p.portfolio_return_pct):'거래 없음'}`));}panel.append(detail);}
 for(const [k,o] of Object.entries(d.grades||{}))panel.append(el('p','v51-batch-meta',`Hybrid ${k}등급 · ${o.buy_signals}신호 · 적중 ${unsignedPct(o.precision_pct)} (확률 보정 아님)`));
 const ab=el('details','v53-item');ab.append(el('summary','','5개 요소별 제거 실험'));
 for(const [k,label] of Object.entries({market:'시장',trend:'종목 추세',strength:'상대강도',entry:'진입',risk:'위험'})){
 const a=d.ablations[k],p=a.portfolio;ab.append(el('p','v51-batch-meta',`${label} 제외: ${p?.completed_trades??0}건 · ${p?.completed_trades?percent(p.portfolio_return_pct):'거래 없음'} · 신호 적중 ${unsignedPct(a.opportunities?.precision_pct)}`));}
 if(d.candidates){const c=el('details','v53-item');c.append(el('summary','','v5.5.2 고정 개선 후보 비교'));for(const [k,n] of Object.entries(core543Names)){const a=d.candidates[k],p=a?.portfolio;c.append(el('p','v51-batch-meta',`${n} · ${p?.completed_trades??0}건 · 수익률 ${percent(p?.portfolio_return_pct)} · MDD ${percent(p?.portfolio_mdd_pct)} · 적중 ${unsignedPct(a?.opportunities?.precision_pct)} · 비용 2배 ${percent(a?.stress_portfolio?.portfolio_return_pct)}`));}panel.append(c);}
 const periods=el('details','v53-item');periods.append(el('summary','','40시점 단위 신호 적중률 안정성'));
 for(const r of d.periods||[]){periods.append(el('p','v51-batch-meta',`${r.start} ~ ${r.end} · ${r.samples}시점 · `+Object.entries(coreNames).map(([k,n])=>`${n} ${unsignedPct(r.opportunities[k]?.precision_pct)} (${r.opportunities[k]?.buy_signals??0}회)`).join(' / ')));}
 const v=d.v550_probability;if(v)panel.append(el('p','v5-note',`v5.5 국면 조건부 확률 연구 · 공통 ${v.samples}시점 · Brier ${v.candidate_brier??'표본 부족'} / 단순 ${v.naive_brier??'표본 부족'} · 과거 재검증, 실전 미반영`));
 const audit=el('details','v53-item');audit.append(el('summary','','후보별 추가·차단 기회 / 국면별 성과'));
 for(const [k,z] of Object.entries(d.candidate_changes||{}))audit.append(el('p','v51-batch-meta',`${core543Names[k]||k} · 추가 수익/손실 ${z.added_positive}/${z.added_negative} · 차단 수익/손실 ${z.blocked_positive}/${z.blocked_negative}`));
 for(const [r,v] of Object.entries(d.v550_regimes||{}))for(const [k,a] of Object.entries(v.strategies||{}))audit.append(el('p','v51-batch-meta',`${r} (${v.samples}시점) · ${core543Names[k]} · ${a.portfolio?.completed_trades??0}거래 · ${percent(a.portfolio?.portfolio_return_pct)}`));
 panel.append(ab,periods,audit,overlapPanel(d.v552_overlap),el('p','v5-note',d.limitations||''));
}
async function coreEvidence(legacy=false){
 const target=current,seq=++coreEvidenceSeq,box=byId('core541EvidenceResult');if(!target||!box)return;
 box.textContent='관찰 결과 조회 중…';try{const r=await fetch(legacy===true?`/api/research/core541/evidence?symbol=${encodeURIComponent(target)}&hybrid=true`:legacy==='550'?`/api/research/core550/evidence?symbol=${encodeURIComponent(target)}`:`/api/research/core552/evidence?symbol=${encodeURIComponent(target)}`,{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(seq!==coreEvidenceSeq||target!==current)return;box.replaceChildren();if(!r.ok||d.status!=='READY'){box.append(el('p','v5-note',d.reason||d.detail||'조회 실패'));return;}
 box.append(el('p','v5-note',`${legacy===true?"v5.4.2":legacy==='550'?"v5.5.0":"v5.5.2"} · 관찰 ${d.observations}건 · 결과 확인 ${d.completed}건 · 대기 ${d.pending}건 · 자료 부족 ${d.missing}건 · 검증 가격 기록 ${d.verified_days}일`));
 for(const [k,label] of Object.entries({...coreNames,...core543Names})){const a=d.metrics[k];if(!a)continue;box.append(el('p','v51-batch-meta',`${label} · 매수 결과 ${a.completed_buy_signals}건 · 적중 ${unsignedPct(a.precision_pct)} · 놓친 수익 기회 ${a.missed_positive}건`));}
 const v=d.probability;if(v)box.append(el('p','v5-note',`고정 전향 확률 · 평가 ${v.samples}건 · Brier ${v.candidate_brier??'표본 부족'} / 단순 ${v.naive_brier??'표본 부족'}`));
 if(d.nonoverlap){box.append(el('p','v5-note',`고정 편도 ${d.cost_bps}bp · 가격만 기록된 날 ${d.signal_unavailable_days??0}일`));const z=d.nonoverlap;box.append(el('p','v5-note',`비중첩 5거래일 평가 ${z.samples}건 · ${d.validation_state==='COLLECTING'?'표본 수집 중':'검토 필요(자동 채택 없음)'}`));for(const [k,a] of Object.entries(z.metrics||{}))box.append(el('p','v51-batch-meta',`${coreNames[k]||core543Names[k]||k} · ${a.buys}개 · 양의 결과 ${a.positive}개 · 평균 순변화 ${percent(a.mean_net_pct)}`));}if(d.overlap)box.append(overlapPanel(d.overlap));if(d.by_regime){const r=el('details','v53-item');r.append(el('summary','','전향 국면별 신호 성적'));for(const [k,v] of Object.entries(d.by_regime))for(const [n,a] of Object.entries(v.metrics||{}))r.append(el('p','v51-batch-meta',`${coreRegimes[k]||k} · ${coreNames[n]||core543Names[n]||n} · ${a.positive}/${a.buys} 양의 결과 (공통 ${v.samples}시점)`));box.append(r);}
 box.append(el('p','v5-note','당일 관찰한 정규장 시가/종가만 사용. 중첩 신호 평가이며 실거래 승률이 아닙니다. 전략 채택 보류.'));
 }catch(e){if(seq===coreEvidenceSeq)box.textContent='관찰 조회 실패';}}
async function unifiedEvidence(legacy=false){
 const target=current,box=byId('core560EvidenceResult');if(!target||!box)return;box.textContent='조회 중…';
 try{const r=await fetch(`/api/research/${legacy?'core560':'core561'}/evidence?symbol=${encodeURIComponent(target)}`,{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(target!==current)return;box.replaceChildren();if(!r.ok||d.status!=='READY'){box.textContent=d.reason||d.detail||'조회 실패';return;}box.append(el('p','v5-note',`관찰 ${d.observations} · 완료 ${d.completed} · 대기 ${d.pending} · 누락 ${d.missing} · 검증 가격 ${d.verified_days}일`),el('p','v5-note',`비중첩 ${d.nonoverlap}건 · Brier ${d.brier??'표본 부족'} / 기준 ${d.naive_brier??'표본 부족'} (${d.scored}건)`));for(const [k,label] of [['core','기존 5-Core'],['unified','통합 판단']]){const x=d[k];box.append(el('p','v51-batch-meta',`${label} · 매수 ${x.buys} · 양의 결과 ${x.positive} · 평균 ${percent(x.mean_net_pct)}`));}if(d.latest)box.append(el('p','v5-note',`최근 관찰: ${d.latest.signal} / ${d.latest.state} · 기대 순변화 ${percent(d.latest.expected_net_pct)}`));if(d.latest)box.append(el('p','v5-note',`연구 비중 ${d.latest.research_position_pct??0}% · 후보 표본 ${JSON.stringify(d.latest.candidate_diagnostics||{})}`));box.append(el('p','v5-note',d.limitations));}catch(e){if(target===current)box.textContent='조회 실패';}
}
async function unifiedStatus(){const box=byId('core560StatusResult');box.textContent='조회 중…';try{const r=await fetch('/api/research/core561/status',{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(!r.ok)throw Error('조회 실패');box.replaceChildren(el('p','v5-note',`자동 수집 설정 ${d.enabled?'활성':'미설정'} · 최근 실행 ${d.job?.status||'idle'} · ${d.job?.day||'날짜 없음'}`));for(const [k,v] of Object.entries(d.job?.results||{}))box.append(el('p','v51-batch-meta',`${k}: ${v}`));box.append(el('p','v5-note',d.note));}catch(e){box.textContent='조회 실패';}}
function unifiedBatch(d){const panel=el('details','v53-item');panel.open=true;panel.append(el('summary','','v5.6.1 계층적 통합 판단 · 비용·기여도·포트폴리오'));if(!d){panel.append(el('p','v5-note','통합 연구 자료 부족'));return panel;}panel.append(el('p','v5-note',`공통 ${d.samples}시점 · 확률 추정 ${d.scored} · 매수 ${d.buy_signals} · Brier ${d.brier??'표본 부족'} / 기준 ${d.naive_brier??'표본 부족'} · 기대수익 MAE ${percent(d.expected_return_mae_pct)}`));panel.append(el('p','v5-note',`동일 연구군 제외 학습 확률 추정 ${d.holdout_scored??0}건 (과거 민감도 검증)`));for(const [k,v] of Object.entries(d.diagnostics||{}))panel.append(el('p','v51-batch-meta',`판단 사유 ${{DATA_MISSING:'자료 부족',RISK_OR_DATA_VETO:'위험 또는 데이터 차단',NO_BUY_CANDIDATE:'매수 후보 없음',INSUFFICIENT_POOL:'공통 학습 표본 부족',NONPOSITIVE_EV:'기대수익 0 이하',RESEARCH_ESTIMATE:'연구 매수 추정'}[k]||k}: ${v}건`));for(const [k,v] of Object.entries(d.routes||{}))panel.append(el('p','v51-batch-meta',`선택 후보 ${coreNames[k]||core543Names[k]||k}: ${v}건`));for(const [k,v] of Object.entries(d.size_research||{}))panel.append(el('p','v51-batch-meta',`연구 비중 ${k}%: ${v}건 (가상 비중 제안)`));for(const [k,v] of Object.entries(d.portfolio||{}))panel.append(el('p','v51-batch-meta',`${coreNames[k]||core543Names[k]||({unified:'통합 판단 (고정 25%)',unified_sized:'통합 판단 (가변 10/25%)',unified_holdout:'통합 판단 (동일 연구군 제외 학습)'}[k]||k)} · 동일비중 수익 ${percent(v.return_pct)} · 합산 MDD ${percent(v.mdd_pct)} · 비용 2배 ${percent(v.stress_return_pct)} · ${v.trades??0}거래`));for(const [title,items] of [['후보별 신호 수익·손실 크기',d.candidate_value],['판단 요소 제거 기여도',d.factor_value],['비중첩 후보별 손익 크기',d.nonoverlap_candidate_value],['비중첩 판단 요소 기여도',d.nonoverlap_factor_value]]){const x=el('details','v53-item');x.append(el('summary','',title));for(const [k,v] of Object.entries(items||{}))x.append(el('p','v51-batch-meta',`${coreNames[k]||core543Names[k]||k} · 추가 이익/손실 ${percent(v.added_gain_pct_sum)} / ${percent(v.added_loss_pct_sum)} · 차단 이익/손실 ${percent(v.blocked_gain_pct_sum)} / ${percent(v.blocked_loss_pct_sum)} · 순기회 차이 ${percent(v.delta_pct_sum)} · 100만원씩 가정 ${money(v.delta_krw)} (${title.startsWith('비중첩')?'종목별 비중첩':'중첩 허용'})`));panel.append(x);}const group=el('details','v53-item');group.append(el('summary','','등록 ETF군·연구 분류·반도체 제외 검증'));for(const [g,vals] of Object.entries(d.by_group||{}))for(const [k,v] of Object.entries(vals))group.append(el('p','v51-batch-meta',`${g} · ${coreNames[k]||core543Names[k]||k} · ${v.stocks}종목 · ${percent(v.mean_pct)}`));panel.append(group,el('p','v5-note',d.limitations));return panel;}
function accuracyPanel(box,data){
 if(!box)return;box.replaceChildren();
 if(!data||data.status!=='READY'){box.append(el('p','v5-note',data?.reason||'워크포워드 자료 부족'));return;}
 box.append(el('p','v5-note',`${data.start} ~ ${data.end} 신호 · 결과확정 ${data.last_outcome_date} · 공통 ${data.paired_samples}시점 / ${data.fold_count}구간 · 편도 ${data.cost_bps}bp`));
 const b=data.brier||{};
 box.append(el('p','v51-batch-meta',`Brier (낮을수록 좋음): 기존 AI ${b.legacy_ai??'—'} / 축소 AI ${b.shrink_ai??'—'} / 단순 기준 ${b.naive??'—'}`));
 box.append(el('p','v5-note',data.candidate_beats_naive?'축소 AI 오차가 단순 기준보다 낮음 · 우월성 미입증':'축소 AI 단순 기준 개선 미입증'));
 const grid=el('div','v53-compare-grid');
 for(const [id,label] of [['baseline','13개 역할'],['grouped','4그룹'],['five_core','5-Core'],['legacy_ai','기존 AI 재학습'],['shrink_ai','축소 AI 후보'],['naive','단순 빈도 기준'],['always_buy','매일 매수 기준']]){
   const a=data.strategies?.[id];if(!a)continue;
   const c=methodCard(label,a.portfolio);if(a.portfolio?.status!=='READY'){grid.append(c);continue;}const o=a.opportunities||{};
   c.append(metric('매수 신호 적중률',unsignedPct(o.precision_pct)),metric('매수 신호 비율',unsignedPct(o.coverage_pct)),metric('놓친 양의 수익 기회',`${o.missed_positive_opportunities??'—'}건`),metric(`비용 ${data.stress_cost_bps}bp 수익률`,a.stress_portfolio?.completed_trades?percent(a.stress_portfolio.portfolio_return_pct):'평가 불가'));
   grid.append(c);
 }
 box.append(grid);
 const folds=el('details','v53-item');folds.append(el('summary','','구간별 AI 오차·학습 마감일'));
 for(const f of data.folds||[])folds.append(el('p','v51-batch-meta',`${f.start} ~ ${f.end} · 학습 결과 확정 ${f.last_training_outcome} · 학습 ${f.train_count} / 평가 ${f.samples} · Brier 기존 ${f.brier?.legacy_ai} / 후보 ${f.brier?.shrink_ai} / 단순 ${f.brier?.naive}`));
 coreFocusPanel(box,data.core_focus);
 box.append(folds,el('p','v5-note',`전략 채택 보류 · ${data.limitations||''} 매수 신호 적중률은 가상 거래 승률과 다릅니다.`));
}
function renderAlignedResearch(aligned){
 const box=byId('v521AlignedCards'),note=byId('v521AlignedDisclaimer');
 if(!box||!note)return;
 box.replaceChildren();
 if(!aligned||aligned.status!=='READY'){
   box.append(el('p','v51-empty',`비교 보류 · ${aligned?.reason||'자료 부족'}`));return;
 }
 for(const [label,r] of [['5거래일 종가 청산',aligned.fixed_five],['위험 조기청산 (최대 5거래일)',aligned.risk_exit]]){
   const card=el('article','v521-aligned-card');
   card.append(el('small','',label));
   card.append(el('strong','',r?.completed_trades?percent(r.portfolio_return_pct):'평가할 거래 없음'));
   card.append(el('span','',`${r?.completed_trades??0}건 · 최대 낙폭 ${r?.completed_trades?percent(r.portfolio_mdd_pct):'미평가'} · 편도 비용 ${aligned.cost_bps_per_side??'—'}bp`));
   box.append(card);
 }
 note.textContent=`기간 ${aligned.validation_start||'—'} ~ ${aligned.validation_end||'—'} · 동일 진입 신호/비용/최대 25% 투자 · 위험 규칙은 조기 청산만 추가 · ${aligned.assessment||'추가 검증 필요'}. 이전 전체기간 백테스트와 같은 숫자가 아닙니다.`;
}
function renderFilterSummary(audit){
 const box=byId('v521FilterSummary');if(!box)return;box.replaceChildren();
 if(!audit||audit.status!=='READY'){
    box.append(el('p','v51-empty','연구 필터 비교를 수행할 수 없습니다.'));return;
 }
 const original=audit.baseline_validation_trades??0;
 for(const f of audit.filter_trials||[]){
   const row=el('div','v521-filter-line'+(f.status!=='READY'?' pending':''));
   row.append(el('strong','',f.label||f.key||'위험 필터'));
   if(f.status!=='READY'){
     row.append(el('p','',`평가 불가 · ${f.reason||'시세/시장지수 자료 미확인'}`));
   }else{
     const n=f.validation?.completed_trades??0;
     const delta=typeof f.validation_return_difference_pp==='number'?`${f.validation_return_difference_pp>0?'+':''}${f.validation_return_difference_pp.toFixed(3)}%p`:'미확인';
     row.append(el('p','',`기존 ${original}건 → 유지 ${n}건 · 제외 ${f.avoided_trade_count??0}건 (그중 수익 거래 ${f.avoided_winners??0}건) · 신규 ${f.new_trade_count??0}건`));
     row.append(el('p','',n?`가상 수익률 ${percent(f.validation?.portfolio_return_pct)} · 기존 대비 ${delta}`:'거래 0건 · 수익 개선으로 판정하지 않음'));
   }
   box.append(row);
 }
}
function renderRiskAudit(audit){
 const summary=byId('v52RiskSummary'),ledger=byId('v52RiskLedger');
 const trials=byId('v52FilterTrials'),overlap=byId('v52RoleOverlap');
 if(!summary||!ledger||!trials||!overlap)return;
 for(const element of [summary,ledger,trials,overlap])element.replaceChildren();
 if(!audit||audit.status!=='READY'){
   summary.append(el('p','v51-empty','위험 연구를 계산할 자료가 부족합니다. 실전 신호에는 영향이 없습니다.'));return;
 }
 const signalSummary=el('div','v52-summary-grid');
 for(const [label,value] of [['검증 거래 수',`${audit.baseline_validation_trades??0}건`],
   ['손실 거래',`${audit.loss_trade_count??0}건`],['연구 판단',audit.review_status||'검증 대기']]){
   const cell=el('div','v52-summary-cell');cell.append(el('small','',label),el('strong','',value));signalSummary.append(cell);
 }
 summary.append(signalSummary);
 renderFilterSummary(audit);
 summary.append(el('p','v5-note',audit.scope||'시장 환경·수급 확인과 추가 검증이 필요합니다.'));
 const history=audit.risk_ledger||[];
 if(!history.length)ledger.append(el('div','v51-empty','검증 구간에 평가 가능한 기존 13개 역할 가상거래가 없습니다.'));
 for(const x of history){
   const box=el('details','v52-risk-record');
   box.append(el('summary','',`${x.signal_date} 신호 → ${x.entry_date} 진입 · ${percent(x.trade_return_pct)} · 위험표시 ${(x.flagged_risks||[]).length}개`));
   const feat=x.signal_day_features||{};
   box.append(el('div','v52-risk-detail',`신호일 ATR/종가 ${unsignedPct(feat.atr_close_pct)} · EMA20 이격 ${typeof feat.ema20_distance_atr==='number'?feat.ema20_distance_atr.toFixed(2)+' ATR':'미확인'} · RSI ${typeof feat.rsi_value==='number'?feat.rsi_value.toFixed(1):'미확인'}`));
   box.append(el('div','v52-risk-detail',`전일 지수 20일 수익률 ${percent(feat.market_prior_20d_pct)} · 다음 시가 갭 ${percent(x.open_gap_pct)} (동일 시가 체결 보장 불가)`));
   box.append(el('div','v52-risk-detail',`경고: ${(x.flagged_risks||[]).join(', ')||'고정 조건 위반 확인 없음'} · 자료 미확인: ${(x.unknown_risks||[]).join(', ')||'없음'}`));
   box.append(el('div','v52-risk-disclaimer','위험표시는 사후 진단용이며 이 손실의 원인이나 미래 가격 예측이 아닙니다.'));
   ledger.append(box);
 }
 for(const f of audit.filter_trials||[]){
   const box=el('details','v52-filter-row');
   box.append(el('summary','',`${f.label} · ${f.status==='READY'?(f.validation?.completed_trades??0)+'건 비교':'자료 보류'}`));
   box.append(el('div','v52-risk-detail',f.rule));
   if(f.status!=='READY')box.append(el('div','v52-risk-detail',f.reason||'비교 데이터 부족'));
   else{
     const before=audit.baseline_validation_trades??0;
     box.append(el('div','v52-risk-detail',`검증 거래 ${before}건 → ${f.validation?.completed_trades??0}건 · 검증 총자산 수익률 차이 ${typeof f.validation_return_difference_pp==='number'?(f.validation_return_difference_pp>0?'+':'')+f.validation_return_difference_pp.toFixed(3)+'%p':'미확인'}`));
     box.append(el('div','v52-risk-detail',`형성 기간 거래 ${f.formation?.completed_trades??0}건 · 제거 거래 ${f.avoided_trade_count??0}건 (수익 거래 ${f.avoided_winners??0}건 포함) · 추가 거래 ${f.new_trade_count??0}건`));
     if((f.validation?.completed_trades??0)===0)box.append(el('div','v52-risk-disclaimer','가상 거래 0건은 성능 향상 또는 위험 제거 성공의 증거가 아닙니다.'));
     for(const t of (f.avoided_trade_examples||[]).slice(0,5))box.append(el('div','v52-risk-detail',`제외 ${t.signal_date} · ${percent(t.net_trade_return_pct)} (가상)`));
   }
   box.append(el('div','v52-risk-disclaimer','사전 고정 필터의 조건 비교이며, 최종 신호는 변경하지 않았습니다.'));
   trials.append(box);
 }
 const r=audit.role_overlap||{};
 overlap.append(el('p','v5-note',`검증 ${r.validation_sessions??0}개 시점 · 10개 독립 역할의 ${(r.evaluated_pairs??0)}쌍 중 기준을 충족한 중복 후보 ${(r.candidates||[]).length}쌍`));
 const pairs=(r.candidates||[]).length?r.candidates:r.most_observed_pairs||[];
 if(!pairs.length)overlap.append(el('p','v51-empty','역할 활동 표본이 부족합니다.'));
 for(const p of pairs.slice(0,8))overlap.append(el('div','v52-risk-detail',`역할 ${p.roles?.join(' ↔ ')||'—'} · 동시 활성 ${p.both_active??0}회 · 같은 방향 ${p.direction_agreement_pct==null?'미확인':p.direction_agreement_pct+'%'} · 활동 겹침 ${p.activity_overlap_pct==null?'미확인':p.activity_overlap_pct+'%'}${p.redundancy_candidate?' · 중복 가능성':''}`));
 overlap.append(el('p','v52-risk-disclaimer',r.interpretation||'역할 제거 권고 아님'));
}
function renderV53(audit){
 const gate=byId('v53DecisionGate'),quality=byId('v53MarketQuality');
 const grouped=byId('v53GroupedComparison'),paths=byId('v53LossPaths');
 if(!gate||!quality||!grouped||!paths)return;
 for(const node of [gate,quality,grouped,paths])node.replaceChildren();
 if(!audit){gate.append(el('p','v5-note','신규 v5.3 연구 데이터 미확인'));return;}
 const d=audit.research_evidence_gate||{};
 gate.append(el('strong','',`전략 승격 ${d.action==='ABSTAIN'?'보류':'심사 필요'} · 실제 매수 신호 미변경`));
 for(const reason of d.reasons||[])gate.append(el('p','',`• ${reason}`));
 const m=audit.market_data_audit||{};
 if(m.status==='UNAVAILABLE')quality.append(el('p','v5-note',`지수 평가 불가 · ${m.reason||'공급원 미수신'}`));
 else{
   quality.append(el('p','',`전일 지수 51봉 일치 ${m.matched_sessions??0}/${m.evaluation_sessions??0}개 시점 · 지수 ${m.reference_bars??0}봉 · 누락 날짜 ${m.missing_sessions??0}개`));
   quality.append(el('p','',`시장지수 범위 ${m.reference_first||'—'} ~ ${m.reference_last||'—'} · 시작일보다 이른 누락 ${m.before_reference_start_count??0}개`));
   if(m.missing_dates_examples?.length)quality.append(el('p','',`누락 예: ${m.missing_dates_examples.join(', ')}`));
 }
 quality.append(el('p','v5-note','KIS 과거 지수의 당시 공개시각은 검증되지 않았고, 누락 날짜는 보간하지 않습니다.'));
 const g=audit.grouped_vote_shadow||{};
 if(g.status==='READY'){
   const grid=el('div','v53-compare-grid');
   for(const [label,v] of [['기존 13역할',g.baseline],['중복 제거 4그룹 (연구)',g.grouped]]){
     const card=el('div','v53-compare-card');card.append(el('small','',label));
     card.append(el('strong','',v?.completed_trades?percent(v.portfolio_return_pct):'평가할 거래 없음'));
     card.append(el('span','',`${v?.completed_trades??0}건 · 최대 낙폭 ${v?.completed_trades?percent(v.portfolio_mdd_pct):'미평가'}`));grid.append(card);
   }
   grouped.append(grid);
   grouped.append(el('p','',`기존 거래 중 제외 ${g.avoided_trade_count??0}건 (그중 수익 거래 ${g.avoided_winners??0}건) · 신규 거래 ${g.added_trade_count??0}건`));
   for(const fold of g.validation_folds||[]){
     grouped.append(el('p','',`${fold.start} ~ ${fold.end}: 기존 ${fold.baseline?.completed_trades?percent(fold.baseline.portfolio_return_pct):'거래 없음'} / 4그룹 ${fold.grouped?.completed_trades?percent(fold.grouped.portfolio_return_pct):'거래 없음'}`));
   }
   grouped.append(el('p','v5-note',g.interpretation||'중복 제거는 연구 전용입니다.'));
 }else grouped.append(el('p','v5-note','중복 제거 후보 비교를 수행하지 못했습니다.'));
 const data=audit.retrospective_loss_paths||[];
 if(!data.length)paths.append(el('p','v5-note','평가 가능한 거래 경로가 없습니다.'));
 for(const t of data){
   const item=el('div','v53-path');
   item.append(el('strong','',`${t.entry_date} 진입 · 거래 손익 ${percent(t.net_trade_return_pct)}`));
   item.append(el('p','',`진입 후 최저 ${percent(t.max_adverse_excursion_pct)} (${t.worst_bar_date}) · 최고 ${percent(t.max_favorable_excursion_pct)} (${t.best_bar_date})`));
   item.append(el('p','v5-note','진입 후 알게 된 정보이므로 매수 시점 위험 신호 또는 손실 원인으로 사용하지 않았습니다.'));
   paths.append(item);
 }
}
function render(data){
 const container=byId('v5ResearchContent');const cards=byId('v5MethodCards');
 const roles=byId('v5RoleAblation');const aiBox=byId('v5AIDetails');const limits=byId('v5Limitations');
 if(!container||!cards||!roles||!aiBox||!limits)return;
 cards.replaceChildren();roles.replaceChildren();aiBox.replaceChildren();limits.replaceChildren();container.hidden=true;
 if(data.status!=='READY'){
   status(`연구 보류 · ${data.reason||'자료 부족'}`);return;
 }
 currentResearch=data;
 const phase=data.validation||{};
 const ai=phase.ai_model||{};
 const calendar=data.completed_session||{};
 if(calendar.stale){
   limits.append(el('p','v5-error',`KRX 거래일 확인 미완료 · ${calendar.reason||'자료/달력 미확인'} · 이는 매수 허용 신호가 아닙니다.`));
 }

 const period=byId('v5StudyPeriod');period.textContent=`검증 구간 ${phase.start||'—'} ~ ${phase.end||'—'} · 자료 ${data.source||'미확인'} · 비용 편도 ${phase.baseline?.cost_bps_per_side??15}bp${data.historical_session_verified===false?' · 이전 일봉의 정규장 범위·조정가격 일관성 미검증':''}`;
 cards.append(methodCard('기존 13개 역할',phase.baseline));
 cards.append(methodCard('연구 5-Core',phase.five_core));
 cards.append(methodCard('AI 연구 신호',ai.research_strategy,ai.reason||'학습/검증 표본 부족'));
 const reference=el('article','v5-method-card');reference.append(el('strong','','AI 모델 검증 상태'));
 reference.append(metric('학습 완료 표본',`${ai.train_samples??0}건`));
 reference.append(metric('검증 완료 표본',`${ai.validation_samples??0}건`));
 reference.append(metric('Brier 점수',typeof ai.brier_score==='number'?String(ai.brier_score):'검증 전'));
 reference.append(metric('단순 기준 Brier',typeof ai.naive_train_rate_brier==='number'?String(ai.naive_train_rate_brier):'검증 전'));
 cards.append(reference);
 let best=null;
 for(const item of data.role_ablations||[]){
   const r=el('details','v5-role-row v51-role-detail');
   const label=el('summary');const diff=item.return_difference_pp;
   label.append(el('span','',item.role),el('b','',diff===null?'계산 불가':`${diff>0?'+':''}${diff.toFixed(3)}%p`));
   r.append(label);
   const note=el('div','v51-role-note');
   note.append(el('div','',`기본 완료 ${item.base_completed_trades??0}건 → 제거 후 ${item.without_completed_trades??0}건 · 없어지는 거래 ${item.removed_trade_count??0}건 · 새로 생기는 거래 ${item.added_trade_count??0}건`));
   for(const x of (item.removed_trade_examples||[]))note.append(el('div','',`제외된 가상거래: ${x.signal_date} → ${x.exit_date} · ${percent(x.net_trade_return_pct)}`));
   for(const x of (item.added_trade_examples||[]))note.append(el('div','',`추가된 가상거래: ${x.signal_date} → ${x.exit_date} · ${percent(x.net_trade_return_pct)}`));
   note.append(el('div','',item.interpretation_warning||'역할 제거는 원인 분석이 아니라 조건 변화 실험입니다.'));
   r.append(note);roles.append(r);
   if(typeof diff==='number'&&(best===null||diff>best))best=diff;
 }
 aiBox.textContent=ai.status==='READY'?`AI 연구 표본: 학습 ${ai.train_samples}건 / 검증 ${ai.validation_samples}건. 점수 범위 ${ai.score_range?.join('~')||'—'} · 보정된 상승 확률 아님. 실전 신호 반영 없음.`:`AI 연구 보류: ${ai.reason||'평가 자료 부족'}. 현재 매매 판단에는 영향이 없습니다.`;
 if(best!==null){aiBox.appendChild(el('p','v5-note','역할을 제거했을 때의 수익률 차이는 인과적 기여도나 전략 채택 근거가 아닙니다. 표본과 시장 상황에 따라 결과가 달라집니다.'));}
 for(const warning of data.bias_warnings||[]){const p=el('p','',`• ${warning}`);limits.append(p);}
 limits.append(el('p','',data.assumptions||''));
 container.hidden=false;
 renderAlignedResearch(data.aligned_backtest_comparison);
 renderRiskAudit(data.risk_audit);
 renderV53(data.v53);
 accuracyPanel(byId('v54AccuracyResult'),data.v54_accuracy);
 showTrades();
 status(`검증 구간 분석 완료 · 연구 결과는 실제 매매 신호·주문·추천 순위를 변경하지 않습니다.${data.source_fallback_reason?' · '+data.source_fallback_reason:''}`);
}
function showTrades(){
 const box=byId('v51TradeRows');if(!box)return;
 box.replaceChildren();
 const which=byId('v51TradeMethod')?.value||'baseline';
 const ledger=currentResearch?.trade_diagnostics?.[which]||[];
 const model=which==='baseline'?currentResearch?.validation?.baseline:
             which==='five_core'?currentResearch?.validation?.five_core:
             currentResearch?.validation?.ai_model?.research_strategy;
 if(!model||model.status!=='READY'){
   box.append(el('p','v51-empty','연구 결과가 없거나 해당 엔진의 검증 자료가 부족합니다.'));return;
 }
 if(!ledger.length){box.append(el('p','v51-empty','평가할 가상거래 0건 · 수익률 0%를 검증된 안전 성적으로 해석하면 안 됩니다.'));return;}
 for(const x of ledger){
   const line=el('article','v51-trade');
   const net=x.net_trade_return_pct;const tone=net>=0?'v51-positive':'v51-negative';
   line.append(el('strong','',`신호 ${x.signal_date} · 가상 진입 ${x.entry_date} → 청산 ${x.exit_date}`));
   line.append(el('small','',`진입 ${money(x.entry_price)} · 청산 ${money(x.exit_price)} · ${x.quantity_shares}주 · 실거래 아님`));
   line.append(el('b',tone,`비용 차감 거래수익률 ${percent(net)} · 가상 손익 ${money(x.net_pnl_krw)}`));
   box.append(line);
 }
 if(model.trade_ledger_truncated)box.append(el('div','v51-empty','거래 로그는 최대 60건까지만 표시합니다.'));
}
const knownStockNames={'005930.KS':'삼성전자','000660.KS':'SK하이닉스','005380.KS':'현대차','247540.KQ':'에코프로비엠','086520.KQ':'에코프로','035420.KS':'NAVER','068270.KS':'셀트리온'};
function batchMetric(label,obj){
 const node=el('div','v51-batch-metric');node.append(el('small','',label));
 const n=obj?.completed_trades??0;
 node.append(el('strong','',obj?.status==='READY'?(n?percent(obj.portfolio_return_pct):'거래 0건'):'미확인'));
 return node;
}
function batchDisplay(state){
 const box=byId('v51BatchResults'),msg=byId('v51BatchStatus');if(!box||!msg)return;
 const rows=state.rows||[];box.hidden=false;box.replaceChildren();
 msg.textContent=state.status==='running'?`여러 종목 분석 중: ${state.finished||0}/${state.symbols?.length||0} 완료 · 시세 공급원 응답에 따라 지연될 수 있습니다.`:
 state.status==='complete'?`일괄 분석 완료: ${rows.filter(r=>r.status==='READY').length}/${rows.length} 종목 계산 완료 · 개별 검증 구간 확인`:
 '작업 대기. 서버 재시작·절전 시 진행 중 작업이 사라질 수 있습니다.';
 for(const r of rows){
   const wrap=el('details','v51-batch-stock');
   const headline=el('summary');headline.append(el('strong','',`${knownStockNames[r.symbol]||r.symbol} · ${r.symbol}`),el('span','',r.status==='READY'?'완료':'보류'));
   wrap.append(headline);
   if(r.status!=='READY')wrap.append(el('p','v5-note',r.reason||'자료 부족'));
   else{
     wrap.append(el('p','v51-batch-meta',`검증 ${r.validation_start||'—'} ~ ${r.validation_end||'—'} · ${r.source||'미확인'} 공급원 · ${r.source_fallback_reason||'각 종목별 시계열 별도 검증'}`));
     const cards=el('div','v51-batch-metrics');
     cards.append(batchMetric('13개 역할 가상 수익률',r.baseline),batchMetric('5-Core 가상 수익률',r.five_core),batchMetric('AI 가상 수익률',r.ai_model?.research_strategy));wrap.append(cards);
     const totals=el('p','v5-note',`거래 수: 13역할 ${r.baseline?.completed_trades??0}건 · 5-Core ${r.five_core?.completed_trades??0}건 · AI ${r.ai_model?.research_strategy?.completed_trades??0}건`);
     wrap.append(totals);
     const v53=r.v53||{};const mq=v53.market_data_audit||{};
     if(mq.status)wrap.append(el('p','v51-batch-meta',`v5.3 시장지수: ${mq.status==='READY'?'일치':'불완전'} · ${mq.matched_sessions??0}/${mq.evaluation_sessions??0}개 시점 확인 · 전략 채택 보류`));
     const grouped=v53.grouped_vote_shadow||{};
     if(grouped.status==='READY')wrap.append(el('p','v51-batch-meta',`중복 제거 4그룹: ${grouped.grouped?.completed_trades?percent(grouped.grouped.portfolio_return_pct):'평가할 거래 없음'} (${grouped.grouped?.completed_trades??0}건) · 기존 ${grouped.baseline?.completed_trades?percent(grouped.baseline.portfolio_return_pct):'거래 없음'} · 실전 신호 미변경`));
     const acc=el('details','v53-item');acc.append(el('summary','','v5.4 워크포워드 정확도 비교'));const accBody=el('div');accuracyPanel(accBody,r.v54_accuracy);acc.append(accBody);wrap.append(acc);
     const risk=r.risk_audit||{};if(risk.status==='READY'){
       wrap.append(el('p','v52-batch-risk',`손실 거래 ${risk.loss_trade_count??0}/${risk.baseline_validation_trades??0}건 · 실전 신호 미변경`));
       const subgroup=el('details','v521-batch-filters');
       subgroup.append(el('summary','',`위험 필터별 성적표 ${risk.filter_trials?.length??0}개 펼쳐보기`));
       for(const f of risk.filter_trials||[]){
         subgroup.append(el('p','v51-batch-meta',f.status==='READY'?`${f.label}: ${risk.baseline_validation_trades??0}→${f.validation?.completed_trades??0}건 · 제외 ${f.avoided_trade_count??0}건 · ${f.validation?.completed_trades?`수익률 ${percent(f.validation.portfolio_return_pct)} (${f.validation_return_difference_pp>=0?'+':''}${f.validation_return_difference_pp??'—'}%p)`:'거래 0건 · 평가 불가'}`:`${f.label}: 자료 보류 · ${f.reason||'미확인'}`));
       }
       wrap.append(subgroup);
     }
     const paired=r.aligned_backtest_comparison||{};
     if(paired.status==='READY')wrap.append(el('p','v51-batch-meta',`동일 신호·기간: 5일 보유 ${paired.fixed_five?.completed_trades?percent(paired.fixed_five.portfolio_return_pct):'거래 없음'} / 위험 조기청산 ${paired.risk_exit?.completed_trades?percent(paired.risk_exit.portfolio_return_pct):'거래 없음'}`));
     let aiB=r.ai_model?.brier_score,naive=r.ai_model?.naive_train_rate_brier;
     wrap.append(el('p','v5-note',typeof aiB==='number'&&typeof naive==='number'?`AI Brier ${aiB.toFixed(4)} / 단순 기준 ${naive.toFixed(4)} · ${aiB<naive?'단순 기준보다 낮은 오차 (우월성 미확정)':'AI 기준 모델보다 개선 미입증'}`:'AI 오차 지표 평가 대기'));
     const trades=(r.trade_diagnostics||{}).baseline||[];
     wrap.append(el('div','v51-batch-subtitle',`기존 13역할 가상 거래 내역 (${trades.length}건)`));
     for(const t of trades.slice(0,12))wrap.append(el('div','v51-batch-meta',`${t.signal_date} 신호 → ${t.entry_date} 진입 → ${t.exit_date} 청산 · ${percent(t.net_trade_return_pct)} · 실제 체결 아님`));
     if(trades.length>12)wrap.append(el('p','v5-note','화면에는 최대 12건을 표시합니다.'));
     const roles=(r.role_ablations||[]).filter(x=>x.removed_trade_count||x.added_trade_count);
     if(roles.length){
       wrap.append(el('div','v51-batch-subtitle','거래 변화가 확인된 역할 제거 실험'));
       for(const x of roles)wrap.append(el('div','v51-batch-meta',`${x.role}: 기본 ${x.base_completed_trades}건 → 제거 ${x.without_completed_trades}건 · 제외 ${x.removed_trade_count} / 추가 ${x.added_trade_count}건`));
     }
   }
   box.append(wrap);
 }
 if(state.status==='complete' && state.v53_batch_summary){
   const a=state.v53_batch_summary;
   const panel=el('div','v53-batch-summary');
   panel.append(el('strong','','v5.3 다종목 연구 증거 판정 · 전략 채택 보류'));
   panel.append(el('p','',`성공 ${a.stocks_completed}/${a.stocks_requested}종목 · 개별 가상 거래 ${a.validation_trades_across_separate_stocks}건 · AI 단순 기준 개선 ${a.ai_beats_naive_stocks}개 종목`));
   panel.append(el('p','',`자료 공급원 ${Object.entries(a.source_counts||{}).map(([key,v])=>key+': '+v+'개').join(', ')||'미확인'} · 검증 구간 일치 ${a.validation_ranges_identical?'예':'아니오'}`));
   panel.append(el('p','v5-note',a.caveat||'같은 종목·기간의 독립 재검증이 필요합니다.'));
   box.prepend(panel);
 }
 if(state.status==='complete' && state.core541_batch_summary){
 const d=state.core541_batch_summary,panel=el('div','v53-batch-summary');panel.append(el('strong','','5-Core 다종목 동일기간 그룹 · 연구 전용'),el('p','v5-note',`평가 가능 ${d.ready_stocks}/${d.requested_stocks}종목 · 기간·비용별 분리 집계`));
 for(const g of d.groups||[]){panel.append(el('p','v51-batch-meta',`${g.start} ~ ${g.end} · ${g.stocks}종목`));for(const [k,n] of Object.entries(coreNames)){const x=g.strategies[k];panel.append(el('p','v51-batch-meta',`${n}: 거래 발생 ${x.stocks_with_trades}종목 · 양의 수익 ${x.positive_stocks}종목 · ${x.trades}거래 · 거래 발생 종목 평균 ${percent(x.mean_return_pct)} · 무거래 현금 포함 전체 종목 평균 ${percent(x.all_stocks_cash_inclusive_mean_pct)}`));}}
 const candidates=rows.filter(r=>r.core541_current?.status==='READY'&&r.core541_current.signals?.adaptive==='BUY');panel.append(el('p','v5-note',`Adaptive 최신 연구 매수 후보: ${candidates.map(r=>`${knownStockNames[r.symbol]||r.symbol} (${r.core541_current.day})`).join(', ')||'없음 또는 자료 부족'} · 전체시장 추천 아님`));box.prepend(panel);
 }
 if(state.status==='complete' && state.core542_aligned){
 const a=state.core542_aligned,panel=el('div','v53-batch-summary');panel.append(el('strong','','v5.4.3 전체 종목 공통 날짜 재검증'));
 if(a.status!=='READY')panel.append(el('p','v5-note',a.reason||'공통 자료 부족'));else{panel.append(el('p','v5-note',`${a.requested_stocks}종목 · ${a.start} ~ ${a.end} · 청산 자료 ${a.outcome_end} · ${a.samples_per_stock}시점/종목`));
 for(const [k,n] of Object.entries(coreNames)){const x=a.strategies[k];panel.append(el('p','v51-batch-meta',`${n} · 평균 ${percent(x.mean_pct)} · 평균 MDD ${percent(x.mean_mdd_pct)} · ${x.trades??0}거래 · 수익 종목 ${x.positive_stocks??0} · 적중 ${unsignedPct(x.precision_pct)} · 비용 2배 ${percent(x.stress_mean_pct)}`));}
 for(const [k,label] of Object.entries({market:'시장',trend:'추세',strength:'상대강도',entry:'진입',risk:'위험'})){const x=a.ablations[k];panel.append(el('p','v51-batch-meta',`${label} 제거 · 평균 ${percent(x.mean_pct)} · 평균 MDD ${percent(x.mean_mdd_pct)} · ${x.trades??0}거래 · 비용 2배 ${percent(x.stress_mean_pct)}`));}
 const cand=el('details','v53-item');cand.append(el('summary','','v5.5.2 개선 후보 및 Hybrid 등급별 공통기간 성적'));for(const [k,n] of Object.entries(core543Names)){const x=a.candidates?.[k];if(x)cand.append(el('p','v51-batch-meta',`${n} · 평균 ${percent(x.mean_pct)} · 평균 MDD ${percent(x.mean_mdd_pct)} · ${x.trades}거래 · 수익 종목 ${x.positive_stocks} · 적중 ${unsignedPct(x.precision_pct)} · 비용 2배 ${percent(x.stress_mean_pct)}`));}for(const [k,x] of Object.entries(a.grades||{}))cand.append(el('p','v51-batch-meta',`Hybrid ${k}등급 단독 진입 · ${x.trades}거래 · 평균 ${percent(x.mean_pct)} · 평균 MDD ${percent(x.mean_mdd_pct)} · 적중 ${unsignedPct(x.precision_pct)}`));cand.append(el('p','v5-note','사전 고정 후보 · 기존 표본 재사용 연구 · 독립 미래 검증 아님. 자동 채택 없음.'));panel.append(cand);
 const audit=el('details','v53-item');audit.append(el('summary','','v5.5 공통기간 기회 및 횡보/전환 성과'));
 for(const [k,z] of Object.entries(a.candidate_changes||{}))audit.append(el('p','v51-batch-meta',`${core543Names[k]||k} · 추가 수익/손실 ${z.added_positive}/${z.added_negative} · 차단 수익/손실 ${z.blocked_positive}/${z.blocked_negative}`));
 for(const [r,v] of Object.entries(a.v550_regimes||{}))for(const [k,x] of Object.entries(v))audit.append(el('p','v51-batch-meta',`${r} · ${core543Names[k]} · ${x.evaluated_stock_days??0} 종목-일 · ${x.trades??0}거래 · 평균 ${percent(x.mean_pct)} · 비용2배 ${percent(x.stress_mean_pct)}`));panel.append(audit,overlapPanel(a.v552_overlap),unifiedBatch(a.v561_unified));
 for(const [k,r] of Object.entries(a.regimes||{})){const names={strong_up:'강한 상승',mild_up:'완만한 상승',range:'횡보·전환',down:'하락',sideways:'횡보',up_transition:'상승 전환'};panel.append(el('p','v51-batch-meta',`${names[k]} · `+Object.entries(coreNames).map(([m,n])=>`${n} ${percent(r[m]?.mean_pct)} (${r[m]?.trades??0}건)`).join(' / ')));}
 const detail=el('details','v53-item');detail.append(el('summary','','종목별 공통기간 성적'));for(const r of a.strategies.core.per_stock||[]){detail.append(el('p','v51-batch-meta',`${knownStockNames[r.symbol]||r.symbol} · `+Object.entries(coreNames).map(([m,n])=>`${n} ${percent(a.strategies[m].per_stock?.find(x=>x.symbol===r.symbol)?.return_pct)}`).join(' / ')));}panel.append(detail,el('p','v5-note',a.limitations));}box.prepend(panel);
 }
 if(state.status==='complete' && rows.length){
   box.append(el('p','v51-empty','종목별 투자금·검증일·시세 공급원 차이가 있습니다. 이 값들을 더해 실제 포트폴리오 수익률처럼 해석하지 마세요.'));
 }
}
function renderPriceAudit(data){
 const box=byId('v531SourceAuditResults');if(!box)return;
 box.hidden=false;box.replaceChildren();
 const verdict=el('div','v531-result-status');
 const labels={PRICE_BASIS_DIFFERENCE:'가격 기준 차이 감지 · 직접 성과 비교 보류',
   NO_LARGE_DIFFERENCE_OBSERVED:'큰 가격 차이 미발견 · 당시 가격 이력 검증은 별도',
   INSUFFICIENT_OVERLAP:'겹치는 거래일 부족 · 평가 보류',
   SOURCE_UNAVAILABLE:'공급원 수신 실패 · 평가 보류'};
 verdict.append(el('strong','',labels[data.decision]||'가격 신뢰도 검토 필요'),el('p','',data.reason||'가격 진단 정보가 부족합니다.'));
 box.append(verdict);
 const providers=data.providers||{},overlap=data.overlap||{};
 const summary=el('div','v531-summary');
 for(const [label,value] of [
   ['KIS 완료일봉',`${providers.kis?.rows??0}개 · ${providers.kis?.last||'날짜 미확인'}`],
   ['Yahoo 완료일봉',`${providers.yahoo?.rows??0}개 · ${providers.yahoo?.last||'날짜 미확인'}`],
   ['동일 거래일',`${overlap.count??0}개`],
   ['KIS / Yahoo 누락일',`${overlap.missing_from_kis_count??0} / ${overlap.missing_from_yahoo_count??0}개`]]){
   const c=el('div','v531-stat');c.append(el('small','',label),el('strong','',value));summary.append(c);
 }
 box.append(summary);
 if(data.status==='UNAVAILABLE'){
   box.append(el('p','v531-warning',`공급원 진단코드: KIS ${providers.kis?.error_code||'—'} · Yahoo ${providers.yahoo?.error_code||'—'} (인증정보 비공개)`));
 }
 for(const [title,key] of [['KIS ↔ Yahoo 기본 OHLC','kis_vs_yahoo_reference'],['KIS ↔ Yahoo 조정 OHLC','kis_vs_yahoo_adjusted']]){
   const m=data.comparison?.[key];if(!m)continue;
   const c=el('div','v531-day');
   c.append(el('strong','',title),el('div','',`시가 차이 중앙값 ${m.median_abs_pct?.Open??'—'}% · 종가 차이 중앙값 ${m.median_abs_pct?.Close??'—'}%`),
     el('div','',`종가 차이 최대 ${m.max_abs_pct?.Close??'—'}% · OHLC 중 0.5% 초과 날짜 ${m.days_any_ohlc_over_0p5pct??'—'}개`));
   box.append(c);
 }
 if((data.sample_days||[]).length){
   const details=el('details');details.append(el('summary','','가격 차이 큰 날짜·최근 거래일 자세히 보기'));
   for(const item of data.sample_days||[]){
     const day=el('div','v531-day');day.append(el('strong','',item.date||'날짜 미확인'));
     day.append(el('div','',`KIS 시가 ${money(item.kis_open)} / 종가 ${money(item.kis_close)}`),
       el('div','',`Yahoo 기본 시가 ${money(item.yahoo_reference_open)} / 종가 ${money(item.yahoo_reference_close)}`),
       el('div','',`Yahoo 조정 시가 ${money(item.yahoo_adjusted_open)} / 종가 ${money(item.yahoo_adjusted_close)}`),
       el('div','',`KIS 대비 Yahoo 기본 종가 차이 ${item.close_diff_reference_pct??'—'}% · Yahoo 조정 종가 차이 ${item.close_diff_adjusted_pct??'—'}%`));
     details.append(day);
   }
   box.append(details);
 }
 const gaps=el('details');gaps.append(el('summary','','공급원별 누락 날짜 확인'));
 gaps.append(el('p','v531-warning',`KIS에만 있는 날짜: ${(overlap.missing_from_yahoo_dates||[]).join(', ')||'없음'}`),
   el('p','v531-warning',`Yahoo에만 있는 날짜: ${(overlap.missing_from_kis_dates||[]).join(', ')||'없음'}`));
 box.append(gaps);
 for(const warning of data.warnings||[])box.append(el('p','v531-warning',`• ${warning}`));
 box.append(el('p','v531-warning',`비교 시점: ${data.assessed_at||'미확인'} · 실제 주문·매매 신호 및 Neon 기록에는 반영되지 않습니다.`));
}
async function runPriceAudit(){
 const msg=byId('v531SourceAuditStatus'),button=byId('v531SourceAuditRun');
 if(!current){if(msg)msg.textContent='먼저 스윙 종목을 분석하세요.';return;}
 if(priceAuditBusy)return;
 const target=current,seq=++priceAuditSeq;
 priceAuditBusy=true;if(button){button.disabled=true;button.textContent='KIS · Yahoo 가격 비교 중…';}
 if(msg)msg.textContent='KIS 수정주가 일봉과 Yahoo 두 가격 기준을 확인 중…';
 try{
   const r=await fetch(`/api/research/v5/source-audit?symbol=${encodeURIComponent(target)}`,
     {credentials:'same-origin',cache:'no-store'});
   const d=await r.json().catch(()=>({}));
   if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:`HTTP ${r.status}`);
   if(seq!==priceAuditSeq||current!==target)return;
   renderPriceAudit(d);
   if(msg)msg.textContent=`${target} 비교 결과 · ${d.overlap?.count??0}개 공통 거래일 · ${d.cached?'최근 진단 캐시':''}`;
 }catch(e){if(seq===priceAuditSeq&&msg)msg.textContent=`가격 비교 실패: ${errMsg(e.message)} · 기존 매매 신호는 유지됩니다.`;}
 finally{priceAuditBusy=false;if(button){button.disabled=!current;button.textContent='KIS · Yahoo 가격 비교 실행';}}
}
function renderDailyIntegrity(data){
 const box=byId('v532DailyIntegrityResult');if(!box)return;
 box.hidden=false;box.replaceChildren();
 const title={ARCHIVE_REUSED:'저장된 정규장 일봉 재사용',VERIFIED:'동일 날짜 시세 교차 확인',REQUERY_RECOVERED:'KIS 재조회로 봉 복구',REGULAR_RECONSTRUCTED:'KIS 정규장 일봉 재구성 완료',
   PRICE_CONFLICT:'가격 충돌 · 최신 봉 격리',UNVERIFIED:'검증 불가 · 최신 봉 격리',
   SETTLEMENT_PENDING:'당일 종가 확정 검증 대기',OLD_DATA:'일봉 거래일 경과 · 최신 여부 확인 필요',
   FUTURE_DATE:'미래 날짜 봉 격리',NOT_CHECKED:'검증 정보 없음'};
 const card=el('div','v531-result-status');
 card.append(el('strong','',title[data.state]||'일봉 확인 상태 미상'),
   el('p','',data.reason||'조회 결과를 확인할 수 없습니다.'));
 box.append(card);
 const info=el('div','v531-summary');
 for(const [label,v] of [['KIS 조회 마지막 봉',data.raw_latest_date||data.latest_date||'—'],
   ['분석에 사용 가능한 마지막 봉',data.safe_last_date||'미확인'],
   ['미완성 봉 제외',data.unfinished_date||'없음'],['격리 날짜',data.rejected_date||'없음'],['정규장 일봉 저장',data.archive_state||'미확인'],
   ['KIS 최신 봉 복구',data.session_reconstructed?'정규장 재구성':data.auto_recovered?'재조회 완료':'없음']]){
   const cell=el('div','v531-stat');cell.append(el('small','',label),el('strong','',v));info.append(cell);
 }
 box.append(info);
 for(const [label,d] of [['KIS 일별 시세',data.broker_reference_ohlc],['KIS 정규장 분봉 대조 OHLC',data.independent_reference_ohlc],['KIS 차트 원본 OHLC',data.primary_ohlc],['분석 적용 정규장 일봉',data.reconstructed_ohlcv]]){
   const line=el('div','v531-day');line.append(el('strong','',label),
     el('div','',d?`시가 ${money(d.Open)} · 종가 ${money(d.Close)} · 고가 ${money(d.High)} · 저가 ${money(d.Low)}${d.Volume!=null?' · 거래량 '+Number(d.Volume).toLocaleString('ko-KR')+'주':''}`:'같은 날짜 검증 자료 없음'));
   box.append(line);
 }
 if(data.errors?.length)box.append(el('p','v531-warning',`진단코드: ${data.errors.join(', ')}`));
 box.append(el('p','v531-warning',
   '가격 충돌·검증 미완료 시 KIS 신규 신호를 보류합니다. 정규장 분봉 OHLCV 확인 시 최신 봉 재구성. 이전 일봉의 정규장 범위는 미검증이며 Yahoo 자동 대체는 없습니다.'));
}
async function runDailyIntegrity(){
 const statusNode=byId('v532DailyIntegrityStatus'),button=byId('v532DailyIntegrityRun');
 if(!current){if(statusNode)statusNode.textContent='스윙 종목을 먼저 분석하세요.';return;}
 if(dailyIntegrityBusy)return;
 const target=current,seq=++dailyIntegritySeq;
 dailyIntegrityBusy=true;if(button){button.disabled=true;button.textContent='KIS 일봉 재검증 중…';}
 if(statusNode)statusNode.textContent='KIS 일봉·일별 시세·정규장 분봉을 확인합니다…';
 try{
   const response=await fetch(`/api/research/v5/daily-integrity?symbol=${encodeURIComponent(target)}`,
     {credentials:'same-origin',cache:'no-store'});
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:`HTTP ${response.status}`);
   if(seq!==dailyIntegritySeq||current!==target)return;
   renderDailyIntegrity(data);
   if(statusNode)statusNode.textContent=`${target} 확인 · ${data.state||'미확인'}${data.cached?' · 최근 검증 캐시':''}`;
   if(data.verified&&!data.signal_hold){const updated=await window.NOVAConnect?.refreshVerified?.(target);if(seq===dailyIntegritySeq&&current===target&&statusNode)statusNode.textContent+=updated?.daily_integrity?.verified&&String(updated?.last_completed_bar||'').slice(0,10)===data.safe_last_date?' · 분석·차트 자동 갱신 완료':' · 차트 갱신 미확인: 분석 버튼으로 재조회';}
 }catch(err){if(seq===dailyIntegritySeq&&statusNode)statusNode.textContent=`일봉 재검증 실패: ${errMsg(err.message)}`;}
 finally{dailyIntegrityBusy=false;if(button){button.disabled=!current;button.textContent='KIS 최신 일봉 재검증 실행';}}
}
function stopBatchPoll(){if(batchPoll){clearTimeout(batchPoll);batchPoll=null;}}
async function batchStatus(){
 const response=await fetch('/api/research/v5/batch/status',{credentials:'same-origin',cache:'no-store'});
 if(!response.ok)throw new Error(`진행 조회 HTTP ${response.status}`);
 const state=await response.json();
 if(!batchJob && state.status==='running' && state.job_id)batchJob=state.job_id;
 if(batchJob&&state.job_id!==batchJob){throw new Error('서버가 재시작되었거나 다른 작업이 시작되어 이전 작업 결과가 없습니다.');}
 batchDisplay(state);
 if(state.status==='running')batchPoll=setTimeout(()=>batchStatus().catch(batchFailure),5000);
 else{batchJob=null;const button=byId('v51BatchRun');if(button)button.disabled=false;}
}
function batchFailure(e){stopBatchPoll();batchJob=null;const b=byId('v51BatchRun');if(b)b.disabled=false;const p=byId('v51BatchStatus');if(p)p.textContent=`일괄 연구 상태 확인 실패: ${errMsg(e.message)} · 다시 실행 전 서버 상태를 확인하세요.`;}
async function runBatch(){
 if(batchJob){return;}
 const raw=byId('v51BatchSymbols')?.value||'';
 const symbols=raw.split(/[,\n;]/).map(x=>x.trim().toUpperCase()).filter(Boolean);
 const statusNode=byId('v51BatchStatus');
 if(symbols.length<2||symbols.length>25||symbols.some(x=>!/^\d{6}\.(KS|KQ)$/.test(x))||new Set(symbols).size!==symbols.length){
   statusNode.textContent='종목을 2~25개 입력하세요. 예: 005930.KS, 000660.KS · 중복이나 6자리/시장 구분 누락은 허용하지 않습니다.';return;
 }
 const b=byId('v51BatchRun');b.disabled=true;statusNode.textContent='연구 작업 등록 중…';
 try{
   const cost=window.NOVAConnect?.cost?.()??15;
   const response=await fetch('/api/research/v5/batch/run',{method:'POST',credentials:'same-origin',
     headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols,extended_core_study:symbols.length>5,source:'kis',cost_bps:Math.max(0,Math.min(100,Math.round(Number(cost)||15)))})});
   const result=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(result.detail||`HTTP ${response.status}`);
   batchJob=result.job_id;stopBatchPoll();await batchStatus();
 }catch(e){batchFailure(e);}
}
async function fetchResearch(){
 if(!current){status('스윙 종목 분석을 먼저 실행해 주세요.');return;}
 if(inflight){status('이미 연구를 조회 중입니다.');return;}
 const target=current;
 const controller=new AbortController();inflight=controller;
 const button=byId('v5ResearchRun');button.disabled=true;button.textContent='과거 성과 계산 중…';
 status('KIS 완료 일봉·시장지수 확인 및 시간순 연구 계산 중…');
 try{
   const source=window.NOVAConnect?.source?.()||'auto';
   const safeSource='kis';
   const cost=window.NOVAConnect?.cost?.()??15;
   const resp=await fetch(`/api/research/v5?symbol=${encodeURIComponent(target)}&source=${encodeURIComponent(safeSource)}&cost_bps=${Math.max(0,Math.min(100,Math.round(Number(cost)||15)))}`,
                          {credentials:'same-origin',cache:'no-store',signal:controller.signal});
   const result=await resp.json().catch(()=>({}));
   if(target!==current)return;
   if(!resp.ok)throw new Error(typeof result.detail==='string'?result.detail:`HTTP ${resp.status}`);
   render(result);
 }catch(e){if(e.name!=='AbortError'&&target===current)status(`연구 조회 실패 · ${errMsg(e.message)}. 기존 매매 결과는 유지됩니다.`,true);
 }finally{if(inflight===controller)inflight=null;button.disabled=!current;button.textContent='현재 종목 정밀 연구 실행';}
}
async function liveButton(){
 const btn=byId('v5LiveToggle'),label=byId('v5LiveState');
 if(socket){stopSocket('수동으로 실시간 관찰을 중단했습니다. 기존 REST 조회는 계속됩니다.');return;}
 if(!current){label.textContent='먼저 국내 종목의 스윙 분석을 해 주세요.';return;}
 btn.disabled=true;label.textContent='KIS 실시간 기능 활성화 상태 확인 중…';
 try{
   const r=await fetch('/api/realtime/kis/status',{credentials:'same-origin',cache:'no-store'});
   const body=await r.json().catch(()=>({}));
   if(!r.ok)throw new Error('접근 권한 확인 필요');
   if(!body.enabled){label.textContent='기본값은 비활성화입니다. Render에 NOVA_KIS_WS_ENABLED=1을 설정하고 KIS 권한을 확인해야 이용할 수 있습니다.';btn.textContent='연결 가능 여부 확인';return;}
   if(!('WebSocket' in window))throw new Error('이 브라우저는 WebSocket을 지원하지 않습니다.');
   const symbol=current,code=symbol.slice(0,6);
   const url=`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/realtime/kis/stream?symbol=${encodeURIComponent(symbol)}`;
   socket=new WebSocket(url);wsTarget=symbol;wsConnected=false;
   btn.textContent='관찰 중단';label.textContent='KIS 체결 채널 연결 시도 중…';
   socket.onmessage=event=>{
     if(wsTarget!==current){stopSocket('종목이 변경되어 관찰을 종료했습니다.');return;}
     let d;try{d=JSON.parse(event.data);}catch{return;}
     if(d.type==='trade'&&d.symbol===code){
       wsConnected=true;byId('v5LiveValue').hidden=false;
       byId('v5LivePrice').textContent=money(d.price);
       byId('v5LiveTime').textContent=`체결 ${d.trade_time_kst||'—'} · ${d.trade_volume??0}주`;
       const gap=referenceClose==null?null:100*(Number(d.price)/referenceClose-1);
       const flag=gap==null?'완료봉 비교 미확인':`${gap>=0?'+':''}${gap.toFixed(2)}% · ${Math.abs(gap)>=5?'큰 변동 관찰':Math.abs(gap)>=3?'변동 확대 관찰':'참고 범위'}`;
       label.textContent=`KIS 체결 틱 수신 · ${d.observed_at_kst||'—'} · 완료봉 대비 ${flag} · 기존 신호 미변경`;
     }else if(d.type==='error'){label.textContent=errMsg(d.message);stopSocket(label.textContent);}
     else if(d.message)label.textContent=errMsg(d.message);
   };
   socket.onclose=()=>{
     if(wsTarget===symbol){socket=null;wsTarget=null;btn.textContent='체결 관찰 시작';
       if(!wsConnected)label.textContent+=' · 체결 데이터 수신 전 종료';}
   };
   socket.onerror=()=>{label.textContent='실시간 체결 연결 오류 · REST 시세는 계속 사용합니다.';};
 }catch(e){label.textContent=errMsg(e.message);}
 finally{btn.disabled=false;}
}
function onViewChange(view){
 const menu=byId('v5MoreMenu'),more=byId('v5MoreNav');
 if(menu)menu.hidden=true;if(more){more.setAttribute('aria-expanded','false');more.classList.toggle('active',['finance','news','guide'].includes(view));}
 if(view!=='research'&&socket)stopSocket('종목 분석 화면을 벗어나 실시간 관찰을 종료했습니다.');
}
function init(){
 byId('core560EvidenceRun')?.addEventListener('click',()=>unifiedEvidence());byId('core560LegacyRun')?.addEventListener('click',()=>unifiedEvidence(true));if(byId('core560EvidenceRun'))byId('core560EvidenceRun').disabled=!current;
 byId('core560StatusRun')?.addEventListener('click',unifiedStatus);
 byId('core541EvidenceRun')?.addEventListener('click',()=>coreEvidence(false));
 byId('core542LegacyEvidenceRun')?.addEventListener('click',()=>coreEvidence(true));
 byId('core550LegacyEvidenceRun')?.addEventListener('click',()=>coreEvidence('550'));
 byId('core541Preset')?.addEventListener('click',()=>{const t=byId('v51BatchSymbols');if(t)t.value='005930.KS, 000660.KS, 005380.KS, 000270.KS, 035420.KS, 035720.KS, 068270.KS, 005490.KS, 051910.KS, 006400.KS, 105560.KS, 055550.KS, 012330.KS, 028260.KS, 247540.KQ, 086520.KQ, 293490.KQ, 196170.KQ, 263750.KQ, 058470.KQ';});
 const button=byId('v5ResearchRun');if(button)button.addEventListener('click',fetchResearch);
 const sourceButton=byId('v531SourceAuditRun');if(sourceButton)sourceButton.addEventListener('click',runPriceAudit);
 const dailyButton=byId('v532DailyIntegrityRun');if(dailyButton)dailyButton.addEventListener('click',runDailyIntegrity);
 const live=byId('v5LiveToggle');if(live)live.addEventListener('click',liveButton);
 const ledgerSelect=byId('v51TradeMethod');if(ledgerSelect)ledgerSelect.addEventListener('change',showTrades);
 const batchButton=byId('v51BatchRun');if(batchButton)batchButton.addEventListener('click',runBatch);
 const batchRefresh=byId('v51BatchRefresh');if(batchRefresh)batchRefresh.addEventListener('click',()=>{stopBatchPoll();batchStatus().catch(batchFailure);});
 const menu=byId('v5MoreMenu'),more=byId('v5MoreNav');
 if(more&&menu){more.addEventListener('click',()=>{
   const opening=menu.hidden;menu.hidden=!opening;more.setAttribute('aria-expanded',String(opening));
 });}
 document.addEventListener('visibilitychange',()=>{if(document.hidden&&socket)stopSocket('화면이 숨겨져 체결 관찰을 종료했습니다.');});
 window.addEventListener('pagehide',()=>{if(socket)stopSocket();stopBatchPoll();});
}
window.NOVAV5={setAnalysis,onViewChange,stopSocket};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
