/* v4.5.1: Read-only, source-labelled KIS quote context.
 * Never modifies trading decisions, backtests, recommendations or recorded signals.
 * Uses existing 20-second KIS REST snapshot requests from investor_flow.js. */
'use strict';
(()=>{
  const byId=id=>document.getElementById(id);
  const won=(n)=>Number.isFinite(n)?`${new Intl.NumberFormat('ko-KR',{maximumFractionDigits:Math.abs(n)<100?2:0}).format(n)}원`:'미확인';
  const humanTime=t=>String(t||'').replace('T',' ').replace(/\+\d\d:\d\d$/,'').slice(0,19)||'미확인';
  const fmtPct=n=>`${n>0?'+':''}${n.toFixed(2)}%`;
  const stockCode=x=>{const m=/^(\d{6})(?:\.(KS|KQ))?$/i.exec(String(x||''));return m?m[1]:'';};
  let analysis=null, lastQuote=null, lastError='';

  // Interpret a price difference, not a predictive AI or executable trade order.
  function assess(a,q,now=Date.now()){
    if(!a||!stockCode(a.symbol))return {kind:'unavailable',label:'분석 대기',text:'분석 완료봉을 먼저 확인해 주세요.'};
    const ref=Number(a.reference);
    if(!Number.isFinite(ref)||ref<=0||a.is_demo)return {kind:'unavailable',label:'판단 불가',text:'실제 완료봉 종가가 없어 장중 차이를 계산하지 않습니다.'};
    if(!q)return {kind:'unavailable',label:'KIS 조회 대기',text:'KIS 현재가 수신 전 · 기존 완료봉 신호만 유효합니다.'};
    if(stockCode(q.symbol)!==stockCode(a.symbol))return {kind:'unavailable',label:'종목 불일치',text:'다른 종목의 시세를 사용할 수 없습니다.'};
    const price=Number(q.price);
    if(!Number.isFinite(price)||price<=0||q.realtime_stream===true)return {kind:'unavailable',label:'가격 미확인',text:'KIS REST 가격이 확인되지 않아 장중 차이 평가를 보류합니다.'};
    const retrieved=Date.parse(String(q.retrieved_at||''));
    const barTime=Date.parse(String(a.last_completed_bar||''));
    if(!Number.isFinite(retrieved)||!Number.isFinite(barTime)||retrieved<barTime||retrieved>now+60_000||now-retrieved>180_000){
      return {kind:'unavailable',label:'시점 미확인',text:'KIS 시세 조회시각 또는 완료봉 기준시각이 유효하지 않아 비교를 보류합니다.'};
    }
    const diff=price-ref;
    const percent=diff/ref*100;
    if(Math.abs(percent)>30){
      return {kind:'unavailable',label:'가격 비교 보류',text:'서로 다른 공급원의 가격 차이가 30%를 넘습니다. 수정주가·액면분할·기준 시점을 교차 확인한 뒤 판단하세요.',price,diff,percent};
    }
    const stop=Number(a.long_stop_reference);
    const crossed=Number.isFinite(stop)&&stop>0&&price<=stop;
    let kind='neutral',label='변동 확인',text='완료봉 종가 대비 변동 폭이 3% 미만입니다.';
    if(percent<=-5){kind='down';label='하락 급변동';text='완료봉 대비 5% 이상 하락했습니다. 장중 변동성 확대에 주의하세요.';}
    else if(percent<=-3){kind='down';label='하방 위험 관찰';text='완료봉 대비 3% 이상 하락했습니다. 이전 완료봉 신호의 가격 시차에 주의하세요.';}
    else if(percent>=5){kind='up';label='상승 급변동';text='완료봉 대비 5% 이상 상승했습니다. 추격 진입 및 가격 변동성에 유의하세요.';}
    else if(percent>=3){kind='up';label='상승 확대 관찰';text='완료봉 대비 3% 이상 상승했습니다. 기존 신호와 현재 가격의 차이를 확인하세요.';}
    if(crossed){kind='down';label='참고 손절선 아래';text+=' 완료봉 기반 참고 손절선을 하회했습니다. 실제 체결·손절 지시가 아닙니다.';}
    return {kind,label,text,price,diff,percent,stopCrossed:crossed};
  }
  function setValue(id,v){const el=byId(id);if(el)el.textContent=v;}
  function display(){
    if(!byId('intradayContext'))return;
    const a=analysis;
    const referenceProvider=String(a?.provider||a?.source||'기존 공급원');
    if(a){
      setValue('lastCloseLabel',`분석 완료봉 종가 · ${referenceProvider}`);
      setValue('quoteValueLabel',`분석 공급원 조회값 · ${referenceProvider}`);
      setValue('analysisPriceOrigin',`확정 신호 기준: ${humanTime(a.last_completed_bar)} · 분석 제공원 ${referenceProvider} · 오른쪽 시세 조회값은 KIS 장중 현재가가 아닐 수 있습니다.`);
    }
    const matching=a&&lastQuote&&stockCode(a.symbol)===stockCode(lastQuote.symbol);
    const result=assess(a,matching?lastQuote:null);
    const status=byId('intradayRiskState');
    if(status){status.textContent=lastError&&result.kind==='unavailable'?'현재가 미확인':result.label;status.className='intraday-risk-state '+(result.kind==='unavailable'?'':result.kind);}
    setValue('intradayQuotePrice',Number.isFinite(result.price)?won(result.price):'미확인');
    setValue('intradayQuoteDiff',Number.isFinite(result.diff)?`${result.diff>0?'+':''}${won(result.diff)} (${fmtPct(result.percent)})`:'미확인');
    setValue('intradayRiskNote',(lastError&&result.kind==='unavailable'?lastError+' · ':result.text+' · ')+'3%·5%는 설명용 변동 구간이며 예측 모델의 검증된 매매 기준이 아닙니다. 기존 13역할 BUY·SELL 판단, 손절선, 백테스트에는 미반영.');
    setValue('intradayQuoteMeta',matching&&lastQuote ? `KIS REST 조회 ${humanTime(lastQuote.retrieved_at)} KST · ${lastQuote.trade_time_kst?'체결 표기 '+lastQuote.trade_time_kst+' (거래일 별도 확인 필요)':'체결 시각 미확인'} · 정규장 확정 종가 미검증 · WebSocket 실시간 아님` : 'KIS REST 조회 대기 · 데이터 공급원·시점 확인 전');
  }
  function setAnalysis(d){
    const nextCode=stockCode(d?.symbol);
    if(lastQuote && stockCode(lastQuote.symbol)!==nextCode){lastQuote=null;lastError='';}
    analysis=d||null;
    display();
  }
  function setQuote(q){
    if(!q||!stockCode(q.symbol))return;
    // The KIS snapshot may arrive before the analysis response on first load.
    // Keep it by code and evaluate only when the matching analysis is ready.
    lastQuote=q;lastError='';display();
  }
  function setUnavailable(message){lastQuote=null;lastError=String(message||'KIS 현재가 미확인');display();}
  window.NOVAIntradayContext={setAnalysis,setQuote,setUnavailable,assess};
})();
