/* Read only. No automatic polling, no market requests, no HTML injection. */
'use strict';
(()=>{
 const num=(v,d=0)=>Number.isFinite(v)?v.toLocaleString('ko-KR',{maximumFractionDigits:d,minimumFractionDigits:d}):'—';
 const pct=v=>Number.isFinite(v)?(v>0?'+':'')+num(v,2)+'%':'미측정';
 const day=v=>typeof v==='string'&&/^\d{8}$/.test(v)?v.slice(0,4)+'.'+v.slice(4,6)+'.'+v.slice(6):'—';
 const won=v=>num(v)+'원';
 const node=(tag,cls,t)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(t!==undefined)e.textContent=t;return e;};
 function render(root,d){
  root.replaceChildren();
  if(!d||d.status!=='VERIFIED'||d.orders_enabled!==false||d.research_only!==true||d.strategy_adoption!=='HOLD')throw Error('unverified');
  const box=node('div','structure-grid');
  for(const [k,v] of [['가상 평가금액',won(d.equity_frozen_mark)],['누적 가상 수익률',pct(d.return_pct)],['최대 낙폭',pct(d.mdd_pct)],['청산 거래 승률',pct(d.win_rate_pct)],['청산 거래',num(d.closed_assumed)+'건'],['신규 검증 거래일',num(d.prospective_sessions)+' / 60일'],['보유 종목',num(d.open_positions)+'개'],['미해결 보유',num(d.locked_positions)+'개']]){
   const tile=node('div');tile.append(node('small','',k),node('strong','',v));box.append(tile);
  }
  root.append(box);
  const states={CURRENT:'최신 거래일 대기',BUSY:'수집 작업 중',BLOCKED_UNCERTAIN:'미확정 호출 · 추가 수집 차단',OVERDUE:'자료 지연 · 확인 필요'};
  root.append(node('p','note','수집 상태: '+(states[d.collection_status]||'미확인')+' · 예약 설정 '+(d.schedule_configured?'ON':'OFF')+' · 실제 예약 성공은 별도 로그 확인'),
   node('p','note','마지막 저장 '+day(d.last_day)+' · 다음 거래일 '+day(d.next_session)+' · 저장 버전 '+num(d.revision)),
   node('p','note','총 일봉 '+num(d.total_bars)+'건 / '+num(d.total_sessions)+'거래일 · 코스피 '+num(d.market_bars?.KOSPI)+' · 코스닥 '+num(d.market_bars?.KOSDAQ)+' · 미확정 호출 '+num(d.unresolved_calls)+'건'),
   node('p','note','초기/보충 데이터는 신규 전략 검증에 포함하지 않습니다. 청산 기록이 없으면 승률은 미측정이며 수익률 0%는 검증된 수익성이 아닙니다.'),
   node('p','note','채택 보류 · 최소 60 신규 거래일 및 30 청산 거래, 기업행위와 시장지수 비교 검증 필요. 수익 보장 없음.'));
  const pos=Array.isArray(d.positions)?d.positions:[];
  root.append(node('h4','','보유 종목 · 모의계좌'));
  if(!pos.length)root.append(node('p','note','현재 보유 기록 없음.'));
  for(const p of pos.slice(0,10))root.append(node('p','',p.code+' · '+num(p.shares)+'주 · '+won(p.mark_krw)+(p.locked?' · 미해결 평가':' · 가상 평가')));
  if(d.locked_positions>0)root.append(node('p','note','미해결 종목 가치 0원 가정 평가금액 '+won(d.equity_zero_locked_sensitivity)));
  const curve=Array.isArray(d.equity_curve)?d.equity_curve.filter(x=>x.mode==='PROSPECTIVE'&&Number.isFinite(x.equity_krw)):[];
  root.append(node('h4','','신규 검증 평가금액 추이'));
  if(!curve.length)root.append(node('p','note','아직 신규 검증 거래일이 없습니다.'));
  else{
   const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
   svg.setAttribute('viewBox','0 0 320 112');svg.setAttribute('role','img');svg.setAttribute('aria-label','가상 평가금액 추이');
   svg.style.width='100%';svg.style.maxHeight='180px';
   const values=[d.initial_cash_krw,...curve.map(x=>x.equity_krw)];const lo=Math.min(...values),hi=Math.max(...values);
   const line=document.createElementNS(svg.namespaceURI,'polyline');
   line.setAttribute('points',values.map((v,i)=>(8+i*304/(values.length-1))+','+(hi===lo?56:104-(v-lo)*96/(hi-lo))).join(' '));
   line.setAttribute('fill','none');line.setAttribute('stroke','currentColor');line.setAttribute('stroke-width','2');svg.append(line);root.append(svg);
   root.append(node('p','note',day(curve[0].day)+' ~ '+day(curve.at(-1).day)+' · '+won(curve.at(-1).equity_krw)));
  }
  root.append(node('p','note','조회 검증 시각 '+(typeof d.read_at==='string'?d.read_at:'—')+(d.cached?' · 20초 이내 조회 재사용':'')));
 }
 const btn=document.getElementById('forwardShadowRefresh'),status=document.getElementById('forwardShadowStatus'),result=document.getElementById('forwardShadowResult');
 if(!btn||!status||!result)return;
 btn.addEventListener('click',async()=>{
  btn.disabled=true;result.replaceChildren();status.textContent='저장된 모의투자 기록 검증 중…';
  const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),45000);
  try{
   let token='';try{token=sessionStorage.getItem('nova3-token')||'';}catch{}
   const res=await fetch('/api/research/forward/performance',{credentials:'same-origin',cache:'no-store',signal:ctrl.signal,headers:token?{'Authorization':'Bearer '+token}:{}});
   if(res.status===401){status.textContent='설정에서 서버 접근 인증을 확인하세요.';return;}
   if(!res.ok)throw Error('unavailable');
   const d=await res.json();render(result,d);status.textContent='저장본 검증 완료 · 연구 전용';
  }catch{result.replaceChildren();status.textContent='조회 불가 또는 검증 실패 · 임의 성과를 표시하지 않습니다.';}
  finally{clearTimeout(timer);btn.disabled=false;}
 });
})();
