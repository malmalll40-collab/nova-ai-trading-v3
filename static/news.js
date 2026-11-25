/* NOVA v3.9: read-only stock headlines. No article text storage or trading influence. */
'use strict';
(()=>{
 const $=id=>document.getElementById(id);
 let lastKey='', pending=null, articles=null;
 const defaultName={'005930.KS':'삼성전자','000660.KS':'SK하이닉스', '086520.KQ':'에코프로','247540.KQ':'에코프로비엠'};
 const forbidden=/[^\w\s.&()+\-가-힣]/g;
 const getQuery=()=>{
   const info=window.NOVAConnect?.selected?.()||{};
   const entered=($('symbolInput')?.value||'').trim();
   const symbol=entered.toUpperCase();
   const stock=info.symbol===symbol?info:{};
   const name=stock.name?stock.name.split(' · ')[0]:defaultName[symbol]||(!/^[A-Z0-9.=-]{1,24}$/.test(symbol)?entered:'');
   return {symbol: /^[A-Z0-9^.=-]{1,24}$/.test(symbol)?symbol:'',name:String(name).replace(forbidden,'').slice(0,48)};
 };
 function externalLink(q){const term=(q.name||q.symbol||'주식');$('newsSearchExternal').href='https://search.naver.com/search.naver?where=news&query='+encodeURIComponent(term+' 주식');}
 function status(s){$('newsStatus').textContent=s;}
 function render(d){const list=$('newsList');list.replaceChildren();
   const items=Array.isArray(d.items)?d.items:[];
   for(const x of items){
     let url;try{url=new URL(x.link);if(url.protocol!=='https:')continue;}catch{continue;}
     const a=document.createElement('a');a.className='news-item';a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';
     const title=document.createElement('strong');title.textContent=String(x.title||'제목 없음');
     const meta=document.createElement('small');const parsed=x.published_at?new Date(x.published_at):null;const date=parsed&&!Number.isNaN(parsed.getTime())?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(parsed)+' KST':'발행시각 미확인';
     meta.textContent=`${x.publisher||'언론사 미확인'} · ${date} · 기사 원문 ↗`;a.append(title,meta);list.append(a);
   }
   if(!list.childElementCount){const note=document.createElement('div');note.className='news-empty';note.textContent='해당 검색어의 뉴스가 없습니다. 직접 검색 링크에서 확인해 주세요.';list.append(note);}
   const fetched=d.retrieved_at?new Date(d.retrieved_at):null;const ftime=fetched&&!Number.isNaN(fetched.getTime())?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(fetched)+' KST':'미확인';
   status(`${d.query||'종목'} · ${list.querySelectorAll('.news-item').length}건 · ${d.cached?'서버 캐시':'새로 조회'} · 조회 ${ftime}`);
   $('newsMeta').textContent=`${d.provider||'뉴스 제공원 미확인'} · ${d.warning||'뉴스는 매매 신호에 반영되지 않습니다.'}`;
 }
 async function load(force=false){
   const q=getQuery();externalLink(q);
   if(!q.name&&!q.symbol){status('종목 검색창에 종목명을 입력하거나 검색 결과에서 종목을 선택하세요.');return;}
   const key=q.symbol+'|'+q.name;
   if(!force && key===lastKey && articles){render(articles);return;}
   if(pending){pending.abort();pending=null;}
   if(window.NOVA_PREVIEW_DATA){status('오프라인 체험판에서는 실제 뉴스 수집을 실행하지 않습니다. 서버에서 종목을 선택해 조회하세요.');$('newsList').replaceChildren();return;}
   const ctrl=new AbortController();pending=ctrl;
   $('newsRefresh').disabled=true;status('실제 종목뉴스를 조회하는 중입니다…');
   try{
     const p=new URLSearchParams(q);const r=await fetch('/api/news?'+p,{cache:'no-store',credentials:'same-origin',signal:ctrl.signal});
     const result=await r.json().catch(()=>({}));if(!r.ok)throw new Error(typeof result.detail==='string'?result.detail:`HTTP ${r.status}`);
     if(ctrl.signal.aborted)return;
     articles=result;lastKey=key;render(result);
   }catch(e){if(ctrl.signal.aborted)return;
     lastKey='';articles=null;$('newsList').replaceChildren();
     const alert=document.createElement('div');alert.className='news-empty';alert.textContent='뉴스 조회 실패: '+e.message+' · 아래 네이버 뉴스 검색 링크에서 직접 확인할 수 있습니다.';$('newsList').append(alert);
     status('뉴스 조회 실패 또는 공급원 제한');
   }finally{if(pending===ctrl)pending=null;$('newsRefresh').disabled=false;}
 }
 document.addEventListener('DOMContentLoaded',()=>{const b=$('newsRefresh');if(b)b.onclick=()=>load(true);externalLink(getQuery());});
 window.NOVANews={load};
})();
