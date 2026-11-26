/* Independent research ranking. Text-only DOM, no network or order actions. */
'use strict';
(()=>{
  const kinds={EARLY:'초기 상승형',ACCELERATING:'상승 가속형',CONTINUATION:'상승 지속형'};
  const reasons={SOURCE_FRESHNESS_OR_FINANCIAL_GATE:'시세·재무 확인 미충족',SNAPSHOT_DATE_LAG:'공식 자료 지연',SAME_DAY_UNVERIFIED:'당일 일봉 미검증',SESSION_MISMATCH:'거래일 누락·불일치',OHLCV_INVALID:'비정상 가격·거래량',ASOF_MISMATCH:'기준일 불일치',PATTERN_UNAVAILABLE:'패턴 자료 없음',HISTORY_SHORT:'과거 봉 부족',NO_RECENT_BUY:'최근 매수 신호 없음',RISK_OR_STALE_SIGNAL:'위험 또는 오래된 신호',WEAK_CLOSE_STRUCTURE:'상승 구조 약화',EXTENDED_MOVE:'고점 추격 제한',REPEAT_REQUIRED:'반복 신호 부족',TREND_UNCONFIRMED:'추세 미확인',PATTERN_INPUT_ERROR:'패턴 계산 오류',PATTERN_ERROR:'패턴 처리 오류',PATTERN_INTEGRITY:'패턴 무결성 오류'};
  const pct=v=>Number.isFinite(v)?(v>0?'+':'')+v.toFixed(2)+'%':'미확인';
  const count=v=>Number.isInteger(v)&&v>=0?String(v):'미확인';
  function node(tag,cls,text){
    const e=document.createElement(tag);
    if(cls)e.className=cls;
    if(text!==undefined)e.textContent=text;
    return e;
  }
  function render(parent,res){
    const box=node('section','recommend-pattern-panel');
    box.setAttribute('aria-label','AI 신호패턴 연구 순위');
    box.append(node('h3','','AI 신호패턴 추천 · 연구 전용'));
    box.append(node('p','note','13개 역할 기반 매수 신호의 반복 패턴을 분석합니다. 점수는 검증된 상승 확률이나 실제 매수 신호가 아닙니다.'));
    parent.append(box);
    const p=res?.signal_pattern_research;
    if(!p||p.version!=='5.8.4'||p.research_only!==true||p.asof!==res.data_date||p.status!=='RESEARCH_ONLY'){
      box.append(node('p','recommend-empty','신호패턴 연구 결과 미확인. 새 검색 또는 자료 검증이 필요합니다.'));return;
    }
    box.append(node('p','recommend-rank-intro','기준일 '+p.asof+' · 기존 심층 분석 '+count(p.evaluated)+'/'+count(p.deep_target)+'종목 · 조건 충족 '+count(p.eligible_total)+'개 · 최대 20개 표시. 전체 종목 심층 분석 또는 실시간 추천 아님.'));
    if(res.deep_failed>0||p.evaluated<p.deep_target)box.append(node('p','note','일부 종목의 분석 자료가 부족하거나 조회에 실패했습니다.'));
    const rows=Array.isArray(p.rows)?p.rows.filter(r=>r&&r.research_only===true&&r.not_actionable===true&&r.asof===p.asof&&Number.isFinite(r.score)&&r.score>=0&&r.score<=100&&Number.isInteger(r.rank)&&r.rank>0&&typeof r.symbol==='string'&&/^\d{6}\.(KS|KQ)$/.test(r.symbol)&&Object.prototype.hasOwnProperty.call(kinds,r.pattern)):[]; 
    if(!rows.length)box.append(node('p','recommend-empty','표시할 검증된 연구 후보가 없습니다. 부족한 자료를 추정하거나 순위를 임의로 만들지 않습니다.'));
    for(const r of rows.slice(0,20)){
      const card=node('article','recommend-card interest-ranked');
      const head=node('div','recommend-title');
      head.append(node('span','recommend-rank-number',r.rank+'위'),node('strong','recommend-name',r.name||r.symbol),node('em','',kinds[r.pattern]));
      card.append(head,node('p','recommend-meta',r.symbol+' · 기준일 '+r.asof+' · 연구 점수 '+r.score.toFixed(1)+'/100'));
      const stats=node('div','recommend-prices');
      for(const [label,key] of [['최근 5일','5'],['최근 10일','10'],['최근 20일','20']]){
        const item=node('div');item.append(node('span','',label+' 매수'),node('strong','',count(r.counts?.[key])+'회'));stats.append(item);
      }
      card.append(stats);
      card.append(node('p','note','연속 매수 '+count(r.streak)+'일 · 마지막 신호 '+count(r.last_buy_age)+'거래일 전 · 최근 20일 내 첫 매수 이후 '+pct(r.since_first_buy_pct)));
      card.append(node('p','note','최근 5일 가격 변화 '+pct(r.ret5_pct)+' · 20일 종가 고점 대비 '+pct(r.drawdown_from_20d_close_peak_pct)+' · 재무 PASS · 과열/약세 필터 통과 (연구 기준)'));
      const button=node('button','recommend-open','종목 상세 분석 열기 ›');button.type='button';
      button.onclick=()=>window.NOVAConnect?.openRecommendation(r.symbol,r.name||r.symbol);
      card.append(button);box.append(card);
    }
    const exclusions=Object.entries(p.exclusions||{}).filter(([k,n])=>Number.isInteger(n)&&n>0);
    if(exclusions.length){
      const info=node('details','install-guide recommend-help');
      info.append(node('summary','','신호패턴 제외 사유'));
      for(const [k,n] of exclusions)info.append(node('p','',(Object.prototype.hasOwnProperty.call(reasons,k)?reasons[k]:k)+': '+n+'건'));
      box.append(info);
    }
    box.append(node('p','note','기준일 달력 일치 및 과거 완료봉 재계산 결과입니다. 일봉 가격의 독립 교차검증이나 3년치 성과 검증은 아직 완료되지 않았습니다.'));
  }
  window.NOVAPatternResearch={render};
})();
