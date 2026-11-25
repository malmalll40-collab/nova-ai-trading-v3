/* NOVA v4.2 Mobile: read-only PWA. Polling only while tab is visible; no persistent push. */
'use strict';
const $ = id => document.getElementById(id);
const PREVIEW = Boolean(window.NOVA_PREVIEW_DATA);
const safeStore = {
  get(key, fallback){ try { const v=localStorage.getItem(key); return v===null?fallback:JSON.parse(v); } catch {return fallback;} },
  set(key,val){ try {localStorage.setItem(key,JSON.stringify(val));} catch {} }
};
const safeSession = {get(key){try{return sessionStorage.getItem(key)||''}catch{return ''}},set(key,value){try{sessionStorage.setItem(key,value)}catch{}},remove(key){try{sessionStorage.removeItem(key)}catch{}}};
let config = safeStore.get('nova3-config',{source:'auto',mode:'스윙',cost:15,auto:false});
// KIS-only migration preserves all other device preferences and records.
if(!PREVIEW){config.source='kis';safeStore.set('nova3-config',config);}
if(PREVIEW) config={...config,source:'demo'};
let watchlist = safeStore.get('nova3-watch',[]);
watchlist = (Array.isArray(watchlist)?watchlist:[]).filter(s=>typeof s==='string'&&/^\d{6}\.(KS|KQ)$/.test(s));
let latest = null;
let pending = null;
let pendingInstall = null;
let refreshTimer = null;
let view = 'analyze';
let lookupTimer = null;
let lookupCtrl = null;
let selectedStock = safeStore.get('nova3-selected-stock',null);
if(selectedStock && !/^\d{6}\.(KS|KQ)$/.test(String(selectedStock.symbol||''))) selectedStock=null;
// v4.7.2: visible company identity is separate from the canonical ticker input.
// Do not write display labels into symbolInput: chart, quote, news and investor
// APIs all depend on the exact six-digit ticker with .KS/.KQ suffix.
const stockNames = safeStore.get('nova472-stock-names-v1',{});
const nameLookupPending = new Set();
const nameLookupFailed = new Set();
function cleanStockName(raw,symbol){
  const name=String(raw||'').trim().replace(/\s*·\s*(코스피|코스닥)\s*$/u,'');
  if(!name||name===symbol||name===symbol?.slice(0,6)||/^\d{6}(\.(KS|KQ))?$/.test(name))return '';
  return name.slice(0,70);
}
function rememberStockName(symbol,raw){
  if(!/^\d{6}\.(KS|KQ)$/.test(String(symbol||'')))return;
  const name=cleanStockName(raw,symbol);
  if(!name)return;
  stockNames[symbol]=name;
  const keys=Object.keys(stockNames);
  if(keys.length>150)keys.slice(0,keys.length-150).forEach(k=>delete stockNames[k]);
  if(!PREVIEW)safeStore.set('nova472-stock-names-v1',stockNames);
}
function getStockName(symbol){
  if(selectedStock?.symbol===symbol){
    const selectedName=cleanStockName(selectedStock.name,symbol);
    if(selectedName){rememberStockName(symbol,selectedName);return selectedName;}
  }
  return cleanStockName(stockNames[symbol],symbol);
}
function updateStockIdentity(symbol,{lookup=true}={}){
  const demo=symbol==='DEMO';
  const valid=/^\d{6}\.(KS|KQ)$/.test(String(symbol||''));
  const displayName=valid?getStockName(symbol):'';
  const shortCode=valid?symbol.slice(0,6):'';
  const label=demo?'DEMO · 가상 종목':displayName?`${displayName} · ${shortCode}`:valid?`종목코드 ${shortCode}`:'—';
  const tag=document.getElementById('selectedStockIdentity');
  if(tag){tag.hidden=!valid&&!demo;document.getElementById('selectedStockIdentityText').textContent=label;}
  const chart=document.getElementById('chartStockIdentity');
  if(chart)chart.textContent=label;
  if(latest?.symbol===symbol || demo){const title=document.getElementById('symbolName');if(title)title.textContent=label;}
  if(valid&&!displayName&&lookup)lookupStockIdentityName(symbol);
}
function lookupStockIdentityName(symbol){
  if(!/^\d{6}\.(KS|KQ)$/.test(symbol)||nameLookupPending.has(symbol)||nameLookupFailed.has(symbol)||getStockName(symbol))return;
  nameLookupPending.add(symbol);
  lookupNames(symbol.slice(0,6)).then(data=>{
    // Never apply a different exchange's name to the current symbol.
    const item=(data.results||[]).find(x=>x.symbol===symbol);
    if(item){rememberStockName(symbol,item.name);}
    else nameLookupFailed.add(symbol);
    // Avoid replacing a newer stock when slow search results arrive later.
    if(latest?.symbol===symbol)updateStockIdentity(symbol,{lookup:false});
  }).catch(()=>nameLookupFailed.add(symbol)).finally(()=>nameLookupPending.delete(symbol));
}
let authReady = false;
let financeSymbol = '';
let financePayload = null;
let financePending = null;
const ICON={BUY:'↗',SELL:'↘',HOLD:'—'};
const sanitize = s => String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const format = (n, c='') => n==null || !Number.isFinite(Number(n))?'미확인':new Intl.NumberFormat('ko-KR',{maximumFractionDigits:Number(n)<100?2:0}).format(Number(n))+(c?' '+c:'');
const pct = n => n==null || !Number.isFinite(Number(n))?'표본 없음':`${Number(n)>0?'+':''}${Number(n).toFixed(2)}%`;
const shortTime = text => text?String(text).replace('T',' ').replace(/\+\d\d:\d\d$/,'').slice(0,19):'미확인';
const setText = (id,t) => {$(id).textContent=t;};
function showMessage(text='',kind=''){
  const el=$('message');el.hidden=!text;el.className='screen-message '+kind;el.textContent=text;
}
function setConnection(label,active=false,fail=false){
  const badge=$('connectionBadge');badge.className='status-pill '+(fail?'offline':active?'online':'');
  badge.lastChild.textContent=label;
}
function storeConfig(){if(!PREVIEW)safeStore.set('nova3-config',config);}
function displayMode(){document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===config.mode));}
function selectView(next){
  view=next;
  if(next==='watchlist'||next==='research')$('results').hidden=false;
  else if(next==='guide'||next==='news'||next==='recommendations'||!latest)$('results').hidden=true;
  else $('results').hidden=false;
  document.body.classList.toggle('research-open',next==='research');
  document.body.classList.toggle('research-no-stock',next==='research'&&!latest);
  document.body.classList.toggle('finance-open',next==='finance');
  document.body.classList.toggle('watchlist-open',next==='watchlist');
  document.body.classList.toggle('guide-open',next==='guide');
  document.body.classList.toggle('news-open',next==='news');
  document.body.classList.toggle('recommendations-open',next==='recommendations');
  ['analyze','recommendations','backtest','research','finance','watchlist','news','guide'].forEach(x=>{
    const p=$('view-'+x);p.hidden=x!==next;
    document.querySelector(`[data-view="${x}"]`).classList.toggle('active',x===next);
  });
  window.NOVAV5?.onViewChange(next);
  if(next==='watchlist')renderWatchlist();
  if(next==='news')window.NOVANews?.load();
  window.NOVAScanner?.onViewChange(next);
  window.NOVAPopular?.onViewChange(next);
  window.NOVAFlow?.onViewChange(next);
  if(next==='analyze')requestAnimationFrame(()=>window.NOVACandles?.render());
  window.NOVAMonitor?.onViewChange(next);
  if(next==='finance')loadFinance();
  if((next==='backtest'||next==='research')&&latest){renderBacktest(latest);window.NOVAJournal?.render();}
  if(next==='guide'||next==='recommendations'||next==='research')window.scrollTo({top:0,behavior:'instant'});
}
function isTicker(s){return /^\d{6}\.(KS|KQ)$/.test(s.toUpperCase());}
function searchUI(items=[],notice=''){
  const el=$('searchSuggestions');
  el.replaceChildren();
  if(notice){const note=document.createElement('p');note.className='suggestion-note';note.textContent=notice;el.appendChild(note);}
  items.slice(0,12).forEach(item=>{
    const btn=document.createElement('button');btn.type='button';btn.className='suggestion-item';btn.setAttribute('role','option');
    const first=document.createElement('span');first.className='suggestion-name';first.textContent=item.name;
    const second=document.createElement('span');second.className='suggestion-meta';second.textContent=`${item.market} · ${item.code}`;
    btn.append(first,second);
    btn.addEventListener('click',()=>chooseStock(item,true));el.appendChild(btn);
  });
  el.hidden=!items.length&&!notice;
  $('symbolInput').setAttribute('aria-expanded',String(!el.hidden));
}
function chooseStock(item,run=false){
  selectedStock={name:item.name,symbol:item.symbol,market:item.market};
  rememberStockName(item.symbol,item.name);
  safeStore.set('nova3-selected-stock',selectedStock);
  $('symbolInput').value=item.symbol;
  safeStore.set('nova3-symbol',item.symbol);
  searchUI();
  if(config.source==='demo'&&!PREVIEW){config.source='kis';$('sourceSelect').value='kis';storeConfig();}
  if(run){selectView('analyze');analyze({jumpToChart:true});}
}
async function lookupNames(query,signal){
  if(PREVIEW)return {results:[],warning:'체험판에서는 서버 종목명 검색을 사용할 수 없습니다.'};
  const headers={};const token=safeSession.get('nova3-token');
  if(token)headers.Authorization='Bearer '+token;
  const m='kr';
  const r=await fetch('/api/search?q='+encodeURIComponent(query)+'&market='+encodeURIComponent(m),{cache:'no-store',headers,signal});
  if(!r.ok){const err=await r.json().catch(()=>({}));throw new Error(typeof err.detail==='string'?err.detail:`검색 오류 HTTP ${r.status}`);}
  return r.json();
}
function scheduleLookup(){
  if(lookupTimer)clearTimeout(lookupTimer);
  if(lookupCtrl){lookupCtrl.abort();lookupCtrl=null;}
  const query=$('symbolInput').value.trim();
  if(selectedStock && selectedStock.symbol!==query)selectedStock=null;
  // While a different name is typed, do not show the previous stock as current.
  const badge=document.getElementById('selectedStockIdentity');
  if(badge && (!latest || query!==latest.symbol))badge.hidden=true;
  if(query.length<2 || PREVIEW){searchUI();return;}
  lookupTimer=setTimeout(async()=>{
    const ctrl=new AbortController();lookupCtrl=ctrl;
    try{
      const data=await lookupNames(query,ctrl.signal);
      if(ctrl.signal.aborted||query!==$('symbolInput').value.trim())return;
      if(data.results.length)searchUI(data.results,data.limited?'전체 종목 검색 불가 · 주요 종목 목록만 표시':'');
      else searchUI([],data.limited?'종목명 검색이 불가능하거나 일치하는 주요 종목이 없습니다.':'검색 결과가 없습니다.');
    }catch(err){if(!ctrl.signal.aborted)searchUI([],err.message);}
    finally{if(lookupCtrl===ctrl)lookupCtrl=null;}
  },350);
}
async function resolveInput(raw){
  if(PREVIEW)return raw.toUpperCase();
  if(/^\d{6}$/.test(raw)){
    const data=await lookupNames(raw);
    const exact=(data.results||[]).filter(item=>item.code===raw && /^\d{6}\.(KS|KQ)$/.test(item.symbol));
    if(exact.length===1){chooseStock(exact[0]);return exact[0].symbol;}
    searchUI(data.results||[],'6자리 코드는 거래소를 확인해야 합니다. 검색 결과에서 선택하거나 005930.KS처럼 거래소를 지정해 주세요.');
    throw new Error('코스피·코스닥 시장 구분을 확인한 뒤 종목을 선택하세요.');
  }
  if(isTicker(raw))return raw.toUpperCase();
  const data=await lookupNames(raw);
  const exact=data.results.filter(x=>x.name.replace(/\s/g,'').toLocaleLowerCase()===raw.replace(/\s/g,'').toLocaleLowerCase());
  if(exact.length===1){chooseStock(exact[0]);return exact[0].symbol;}
  if(data.results.length===1){chooseStock(data.results[0]);return data.results[0].symbol;}
  searchUI(data.results,data.results.length?'동일한 이름의 후보가 있습니다. 분석할 종목을 먼저 선택하세요.':'일치하는 종목이 없습니다. 정확한 이름이나 종목코드로 검색해 주세요.');
  throw new Error('검색된 종목을 목록에서 선택한 뒤 분석하세요.');
}
async function copySiteLink(){
  const url=location.origin.startsWith('http')?location.origin+'/':'https://nova-ai-trading-v3-mobile.onrender.com/';
  $('installUrl').value=url;
  try{await navigator.clipboard.writeText(url);setText('installStatus','주소를 복사했습니다. Chrome 또는 삼성 인터넷 앱을 직접 실행해 주소창에 붙여넣으세요.');}
  catch{setText('installStatus','복사가 제한됐습니다. 위 주소를 길게 누르거나 선택해 복사하세요.');$('installUrl').focus();$('installUrl').select();}
}
function pwaStatus(){
  const installed=window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone===true;
  setText('installStatus',installed?'홈 화면 앱 모드로 실행 중입니다.':window.isSecureContext?'외부 Chrome/삼성 인터넷에서 열린 HTTPS 페이지의 메뉴에서 설치하세요.':'HTTPS 주소에서 다시 접속하세요.');
}

async function checkAuthStatus(){
  if(PREVIEW){setText('authState','오프라인 체험판 · 서버 인증 없음');return;}
  try{
    const r=await fetch('/api/auth/status',{cache:'no-store',credentials:'same-origin'});
    if(!r.ok)throw new Error('접속 확인 실패');
    const d=await r.json();authReady=d.authenticated;
    setText('authState',!d.token_required?'서버 접근 토큰 필요 없음':d.authenticated?'✓ 로그인 유지 중 · 토큰 재입력 불필요':'로그아웃 상태 · 서버 접근 토큰을 한 번 입력하세요.');
  }catch{setText('authState','서버에 연결할 수 없어 인증 상태를 확인하지 못했습니다.');}
}
async function saveLoginAndSettings(){
  const btn=$('saveSettings');btn.disabled=true;
  try{
    const token=$('tokenInput').value.trim();
    if(token&&!PREVIEW){
      const response=await fetch('/api/auth/login',{method:'POST',credentials:'same-origin',cache:'no-store',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({token,remember:$('rememberDevice').checked})});
      if(!response.ok){const err=await response.json().catch(()=>({}));throw new Error(err.detail||'로그인에 실패했습니다.');}
      // Once a secure cookie is issued, remove the earlier v3.1 tab token.
      safeSession.remove('nova3-token');$('tokenInput').value='';authReady=true;
      setText('authState',$('rememberDevice').checked?'✓ 30일 로그인 유지 설정 완료':'✓ 현재 브라우저 세션 로그인 완료');
    }
    config.source=$('sourceSelect').value;config.cost=Number($('costInput').value);config.auto=$('autoRefresh').checked;
    if(PREVIEW){config.source='demo';config.cost=15;config.auto=false;$('sourceSelect').value='demo';}
    storeConfig();scheduleRefresh();$('settingsDialog').close();window.NOVAFlow?.onViewChange(view);analyze();
  }catch(err){setText('authState',err.message);showMessage(err.message,'error');}
  finally{btn.disabled=false;}
}
async function logoutDevice(){
  if(PREVIEW)return;
  try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',cache:'no-store'});}catch{}
  safeSession.remove('nova3-token');$('tokenInput').value='';authReady=false;
  setText('authState','로그아웃 완료 · 다음 분석 전에 토큰을 입력하세요.');
  showMessage('이 기기에서 로그아웃했습니다.','');
}
function compareIcon(signal){return ICON[signal]??'—';}
function renderChart(container, series, opts={}){
  const lines=(series??[]).filter(x=>x.values&&x.values.length);
  if(!lines.length){container.textContent='차트 데이터가 없습니다.';return;}
  const values=lines.flatMap(x=>x.values).map(Number).filter(Number.isFinite);
  if(!values.length){container.textContent='차트 데이터가 없습니다.';return;}
  let min=Math.min(...values),max=Math.max(...values);
  const p=Math.max(0.001,(max-min)*.14);min-=p;max+=p;
  const left=16,top=16,w=648,h=198;
  const count=Math.max(...lines.map(x=>x.values.length));
  const X=(i,len)=>left+(len<2?w:(i/(len-1)*w));
  const Y=v=>top+h-(Number(v)-min)/(max-min)*h;
  let paths='';
  for(const line of lines){
    const segments=[];let current=[];
    line.values.forEach((v,i)=>{
      if(v==null||!Number.isFinite(Number(v))){if(current.length)segments.push(current);current=[];return;}
      current.push(`${current.length?'L':'M'}${X(i,line.values.length).toFixed(1)},${Y(v).toFixed(1)}`);
    });
    if(current.length)segments.push(current);
    for(const seg of segments){paths+=`<path d="${seg.join(' ')}" fill="none" stroke="${line.color}" stroke-width="${line.width||2.1}" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round" />`;}
  }
  const grid=Array.from({length:4},(_,i)=>{let y=top+i*h/3;return `<line x1="${left}" x2="${left+w}" y1="${y}" y2="${y}" stroke="#ffffff15" stroke-width="1" />`}).join('');
  const label=Number.isFinite(max)?format(max):'—';
  container.innerHTML=`<svg viewBox="0 0 680 240" role="img" aria-label="${sanitize(opts.label||'가격 그래프')}" preserveAspectRatio="xMidYMid meet"><defs><linearGradient id="graphFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#58e2c2" stop-opacity=".13"/><stop offset="100%" stop-color="#58e2c2" stop-opacity="0"/></linearGradient></defs>${grid}${paths}<text x="668" y="13" text-anchor="end" font-size="11" fill="#83aab5">${sanitize(label)}</text><text x="668" y="232" text-anchor="end" font-size="11" fill="#83aab5">${sanitize(format(min))}</text></svg>`;
}
function renderRoles(d){
  const box=$('roleList');box.innerHTML='';
  d.votes.forEach(x=>{
    const row=document.createElement('div');row.className='role';
    row.innerHTML=`<div class="role-text"><div class="role-title">${sanitize(x.role)}</div><div class="role-reason">${sanitize(x.reason)}</div></div><span class="role-tag ${x.direction===1?'buy':x.direction===-1?'sell':''}">${sanitize(x.label)}</span>`;
    box.appendChild(row);
  });
}
function renderCoreComparison(data){
  const panel=$('coreComparisonPanel');if(!panel)return;
  const status=$('coreComparisonStatus');const content=$('coreComparisonContent');
  const grid=$('coreComparisonStats');const details=$('coreComparisonDetails');
  grid.replaceChildren();details.replaceChildren();content.hidden=true;
  if(!data || data.status!=='READY'){
    const reason=data?.reason || '이 종목의 연구 데이터가 아직 없습니다.';
    status.textContent=`비교 보류 · ${reason}`;
    return;
  }
  const verify=data.validation||{};
  const base=verify.baseline||{},alt=verify.five_core||{};
  const metric=(v,suffix='%')=>typeof v==='number'&&Number.isFinite(v)?`${v.toFixed(2)}${suffix}`:'미평가';
  status.textContent=`최근 검증 ${verify.eligible_sessions||0}거래일 · ${verify.period_start||'—'} ~ ${verify.period_end||'—'} · ${data.conclusion||'추가 검증 필요'}`;
  function card(title,row){
    const el=document.createElement('div');el.className='core-compare-card';
    const name=document.createElement('strong');name.className='core-card-name';name.textContent=title;
    const items=[['BUY 관찰',String(row.buy_opportunities??0)+'건'],
       ['실제 가상거래',String(row.completed_trades??0)+'건'],
       ['가상 거래 승률',metric(row.win_rate_pct)],
       ['평균 순변화',metric(row.mean_trade_net_pct)],
       ['25% 편입 가상 수익률',metric(row.portfolio_return_pct)],
       ['가상 자산 최대 낙폭',metric(row.portfolio_mdd_pct)]];
    el.appendChild(name);
    for(const [label,value] of items){const line=document.createElement('div');line.className='core-compare-metric';
      const l=document.createElement('span');l.textContent=label;
      const v=document.createElement('b');v.textContent=value;
      line.append(l,v);el.appendChild(line);
    }
    return el;
  }
  grid.append(card('기존 13개 역할',base),card('연구용 5-Core',alt));
  const note=document.createElement('p');note.className='core-compare-foot';
  note.textContent=`동일 기준일 ${data.eligible_sessions||0}개 · ${data.data_source||'시세 미확인'} 완료봉 · 겹치는 거래는 제외. 매수 이외 신호는 단기 공매도로 환산하지 않음. 샘플이 적으면 어느 쪽도 우수하다고 판단하지 마세요.`;
  details.append(note);
  const factors=data.latest_core?.checks||[];
  if(factors.length){
    const title=document.createElement('strong');title.className='core-factor-title';title.textContent='마지막 비교 기준일의 5-Core 조건 (과거 진단용)';
    details.append(title);
    const box=document.createElement('div');box.className='core-factor-grid';
    for(const c of factors){const part=document.createElement('div');part.className='core-factor';
      part.textContent=`${c.pass?'✓':'—'} ${c.label} · ${c.detail||'자료 미확인'}`;
      box.append(part);
    }
    details.append(box);
  }
  content.hidden=false;
}
function renderBacktest(d){
  window.NOVAV5?.setAnalysis(d);
  renderCoreComparison(d.core_comparison_research);
  const t=d.backtest;
  setText('directionAccuracy',pct(t.directional_hit_rate_pct));
  setText('directionSamples',`과거 신호 ${t.directional_samples}건`);
  setText('tradeWinRate',pct(t.win_rate_pct));
  setText('tradeCount',`청산 거래 ${t.closed_trades}건`);
  setText('strategyReturn',pct(t.total_return_pct));
  setText('maxDrawdown',pct(t.max_drawdown_pct));
  setText('benchReturn',pct(t.benchmark_return_pct));
  const problem = t.directional_samples<30||t.closed_trades<10||t.last_20pct_samples<20;
  $('sampleWarning').hidden=!problem;
  renderChart($('equityChart'),[
    {values:t.curve.strategy,color:'#58e2c2',width:2.9},
    {values:t.curve.benchmark,color:'#81a6f9',width:2.2}
  ],{label:'모의 자산 곡선'});
  const list=$('tradeList');list.innerHTML='';
  if(!t.recent_trades.length){list.innerHTML='<div class="watch-empty">청산 거래 기록이 없습니다.</div>';return;}
  t.recent_trades.forEach(tr=>{
    const row=document.createElement('div');row.className='trade-item';
    row.innerHTML=`<div><div class="trade-reason">${sanitize(tr.reason)}</div><div class="trade-date">${sanitize(shortTime(tr.entry))} → ${sanitize(shortTime(tr.exit))}</div></div><div class="trade-pnl ${tr.pnl_pct>=0?'positive':'negative'}">${sanitize(pct(tr.pnl_pct))}</div>`;
    list.appendChild(row);
  });
}
function renderWatchlist(){
  const el=$('watchList');el.innerHTML='';
  if(!watchlist.length){el.innerHTML='<div class="watch-empty">아직 저장한 종목이 없습니다.<br>분석 화면에서 ☆ 관심 버튼을 눌러 추가하세요.</div>';return;}
  watchlist.forEach(symbol=>{
    const row=document.createElement('div');row.className='watch-item';
    const btn=document.createElement('button');btn.className='watch-target';btn.type='button';btn.textContent='↗  '+(getStockName(symbol)?`${getStockName(symbol)} · ${symbol.slice(0,6)}`:symbol);
    btn.onclick=()=>{ $('symbolInput').value=symbol;if(selectedStock?.symbol!==symbol)selectedStock=null;if(config.source==='demo'){$('sourceSelect').value='kis';config.source='kis';}selectView('analyze');analyze({jumpToChart:true}); };
    const remove=document.createElement('button');remove.className='watch-remove';remove.type='button';remove.textContent='삭제';
    remove.onclick=()=>{watchlist=watchlist.filter(x=>x!==symbol);safeStore.set('nova3-watch',watchlist);renderWatchlist();updateWatchButton();window.NOVAMonitor?.afterChange();};
    row.append(btn,remove);el.appendChild(row);
  });
}
function updateWatchButton(){
  const symbol=$('symbolInput').value.trim().toUpperCase();
  const on=watchlist.includes(symbol);
  const b=$('watchButton');b.classList.toggle('saved',on);b.innerHTML=(on?'★':'☆')+' <span>관심</span>';b.setAttribute('aria-label',on?'관심종목 삭제':'관심종목 추가');
}
function maybeNotify(d){
  // Never alert on stale/demo/unverified data or before the combined final decision.
  const final=d.final_actionable_signal;
  if(d.is_demo||d.is_stale||!d.signal_is_current||
     !['BUY','SELL','HOLD'].includes(final)||
     !('Notification' in window)||Notification.permission!=='granted')return;
  const bar=String(d.last_completed_bar||'');
  if(!bar)return;
  const key=`${d.source}/${d.symbol}/${d.mode}`;
  const saved=safeStore.get('nova3-last-signals',{});
  const was=saved[key];
  // Migration from v3.8 single string state: establish a fresh baseline, no retroactive alert.
  if(was && typeof was==='object' && was.bar && was.bar < bar && was.signal !== final){
    try{new Notification(`NOVA ${d.symbol}: ${d.signal_label}`,{
      body:`${d.mode} 완료봉 ${shortTime(bar)} 기준 · ${format(d.reference,d.currency)} · 참고 정보`,
      icon:'/static/icons/icon-192.png'});}catch{}
  }
  if(!was || typeof was!=='object' || !was.bar || was.bar<=bar){
    saved[key]={signal:final,bar};
  }
  if(Object.keys(saved).length>60)Object.keys(saved).slice(0,20).forEach(k=>delete saved[k]);
  safeStore.set('nova3-last-signals',saved);
}

// v4.0: descriptive role-by-role historical statistics; never mutate final action.
function renderSignalPerformance(d){
  const all=$('signalPerformancePanel'),rank=$('signalRankingPanel');
  const v=d.signal_performance;
  all.hidden=!v;rank.hidden=!v;
  if(!v)return;
  const rows=Array.isArray(v.roles)?v.roles:[];
  const active=rows.filter(r=>r.active_now);
  const fmt=n=>n==null?'표본 부족':Number(n).toFixed(1)+'%';
  const net=n=>n==null?'—':(Number(n)>0?'+':'')+Number(n).toFixed(2)+'%';
  const allowed=d.final_actionable_signal==='BUY'&&!d.is_stale&&!d.is_demo;
  setText('rolePerformanceSummary',`${d.mode} · 현재 기술적 매수 조건 ${active.length}/10개 · ${v.horizon_bars}봉 후 평가 · 최종 판단 ${d.signal_label}`);
  const selected=rows.find(r=>r.id===v.selected_id);
  const feature=$('rolePerformanceSelected');feature.replaceChildren();
  const heading=document.createElement('strong');const text=document.createElement('p');
  if(selected){
    heading.textContent='훈련 구간 상위 조건 · '+selected.name;
    const val=selected.validation;
    const outcome=!selected.validation_sufficient?'최근 구간 표본 부족':selected.validation_positive?'최근 구간 성과 양호':'최근 구간 성과 부진';
    text.textContent=`훈련 ${fmt(selected.train.hit_pct)} (${selected.train.samples}건) · 최근 검증 ${fmt(val.hit_pct)} (${val.samples}건) · ${outcome}. ${selected.active_now?'현재 조건 충족.':'현재 조건 미충족.'} ${allowed&&selected.active_now&&selected.validation_positive?'최종 신호도 매수 후보입니다.':'현재 승인된 매수 추천으로 해석하지 마세요.'}`;
    if(!selected.validation_positive||!selected.active_now||!allowed)text.className='signal-perf-muted';
  }else{
    heading.textContent='검증 가능한 종목별 우수 조건 없음';
    text.textContent=`훈련 구간에서 최소 ${v.min_train}건 이상의 표본과 양(+)의 평균 순변화를 만족한 조건이 없습니다. 실제 매수 성공 확률은 알 수 없습니다.`;
  }
  feature.append(heading,text);
  function createCard(r,i,sortMode){
    const el=document.createElement('article');el.className='signal-perf-item'+(r.active_now?' active-now':'');
    const head=document.createElement('header');const label=document.createElement('h4');label.textContent=r.name.replace(/^\d{2} /,'');
    const tag=document.createElement('span');tag.className='signal-rank-tag';tag.textContent=sortMode?`훈련 ${i+1}위`:'현재 충족';head.append(label,tag);
    const score=document.createElement('div');score.className='signal-perf-score';score.textContent=r.validation_sufficient?fmt(r.validation.hit_pct):(r.validation.samples?'참고 '+fmt(r.validation.hit_pct):'표본 없음');
    const sample=document.createElement('small');sample.textContent=`최근 검증: ${r.validation.samples}건 · Wilson 하한 ${fmt(r.validation.wilson_lower_pct)} · 평균 순변화 ${net(r.validation.mean_net_pct)}`;
    const training=document.createElement('small');training.textContent=`훈련: ${fmt(r.train.hit_pct)} / ${r.train.samples}건`;
    const limit=document.createElement('span');limit.className='signal-perf-verified';
    limit.textContent=!r.train_sufficient?'훈련 표본 부족':!r.validation_sufficient?'최근 검증 표본 부족':r.validation_positive?'최근 검증 구간 수익 양호':'최근 검증 구간 수익 불충분';
    el.append(head,score,sample,training,limit);return el;
  }
  const current=$('rolePerformanceCurrent');current.replaceChildren();
  if(active.length){active.forEach((r,i)=>current.appendChild(createCard(r,i,false)));}
  else{const empty=document.createElement('div');empty.className='watch-empty';empty.textContent='현재 완료봉에서 매수 방향인 개별 기술 조건이 없습니다.';current.appendChild(empty);}
  setText('signalRankingSummary',`${d.symbol} · ${d.mode} · 훈련 70% / 최근 검증 30% · 편도 비용 ${d.cost_bps_one_side}bp · ${v.horizon_bars}봉 평가`);
  const list=$('signalRankingList');list.replaceChildren();rows.forEach((r,i)=>list.appendChild(createCard(r,i,true)));
}

function renderHigherTrend(d){
  const panel=$('higherTrendPanel');if(!panel)return;
  const c=d.mode==='스윙'?d.higher_timeframe_context:null;
  panel.hidden=!c;
  if(!c)return;
  const renderOne=(kind,stateId,noteId)=>{
    const x=c[kind]||{};
    const state=x.state||'INSUFFICIENT';
    setText(stateId,x.label||'자료 부족');
    const badge=$(stateId);
    badge.className='higher-trend-state '+(state==='UP'?'up':state==='DOWN'?'down':'neutral');
    setText(noteId,(x.period_start?`기준 시작일 ${x.period_start} · `:'')+(x.description||'자료 부족'));
  };
  renderOne('weekly','weeklyTrendState','weeklyTrendNote');
  renderOne('monthly','monthlyTrendState','monthlyTrendNote');
  setText('higherTrendDate',`일봉 완료 시점 ${c.last_completed_daily||'미확인'} · 진행 중 주·월봉 제외 · 참고용 (BUY/SELL·순위 미반영)${d.is_demo?' · 가상 시계열':''}`);
}

function renderQuality(d){
  const panel=$('qualityPanel');if(!panel)return;
  const q=d.quality_gate;
  panel.hidden=!q;
  if(!q)return;
  setText('qualityStatus',q.raw_signal==='BUY'?(q.approved_buy?'통과':'매수 보류'):'신규 매수 없음');
  setText('qualityDescription',`원래 신호 ${({BUY:'매수',SELL:'보유 축소',HOLD:'관망'})[q.raw_signal]||'미확인'} → 안정형 ${({BUY:'매수',SELL:'보유 축소',HOLD:'관망'})[q.strict_signal]||'미확인'} · ${q.passed_count}/5 조건 충족. ${q.reason}`);
  const items=$('qualityChecks');items.replaceChildren();
  for(const check of q.checks||[]){
    const row=document.createElement('div');row.className='quality-check';
    const marker=document.createElement('span');marker.textContent=check.passed?'✓':'×';marker.className=check.passed?'quality-ok':'quality-no';
    const text=document.createElement('div');const strong=document.createElement('strong');strong.textContent=check.name;
    const small=document.createElement('small');small.textContent=check.detail;text.append(strong,small);row.append(marker,text);items.appendChild(row);
  }
}
function renderHighPrecision(d){
  const panel=$('highPrecisionPanel');if(!panel)return;
  const hp=d.high_precision_research;
  panel.hidden=!hp;if(!hp)return;
  setText('highPrecisionStatus','연구 전용');
  const pattern=hp.pattern?` · 관찰 패턴: ${hp.pattern}`:'';
  const unlinked=(hp.checks||[]).filter(x=>x.state==='UNAVAILABLE').length;
  const availability=unlinked?` · 외부 데이터 미연동/미검증 ${unlinked}항목 (✕와 구분)` : '';
  setText('highPrecisionDescription',`${hp.display||'평가 보류'}${pattern}${availability} · 기존 v4.5 신호 유지`);
  const sources=hp.context_data||{};
  const sourceParts=[['benchmark','시장'],['sector','업종'],['flows','수급']].map(([key,title])=>{
    const item=sources[key]||{};
    return `${title}: ${item.status==='READY'?'연동':item.status==='PARTIAL'?'부분 확보':'미확인'} · ${item.name||item.detail||'자료 없음'}${item.as_of?' · 기준 '+item.as_of:''}`;
  });
  setText('highPrecisionContextSources','연구 자료 출처 · '+sourceParts.join(' / ')+' · 시세·수급의 출처와 시점이 다를 수 있으며 실거래 판정에 미반영');
  const wrap=$('highPrecisionChecks');wrap.replaceChildren();
  for(const check of hp.checks||[]){
    const row=document.createElement('div');row.className='quality-check';
    const marker=document.createElement('span');
    marker.textContent=check.state==='PASS'?'✓':check.state==='UNAVAILABLE'?'—':'×';
    marker.className=check.state==='PASS'?'quality-ok':check.state==='UNAVAILABLE'?'quality-unknown':'quality-no';
    marker.setAttribute('aria-label',check.state==='UNAVAILABLE'?'외부 자료 미연동/미확인':check.state==='PASS'?'조건 통과':'조건 미충족');
    const details=document.createElement('div');const title=document.createElement('strong');
    title.textContent=(check.name||'검증 항목')+(check.state==='UNAVAILABLE'?' · 자료 미연동':'');
    const sub=document.createElement('small');sub.textContent=check.detail||check.state||'확인 필요';
    details.append(title,sub);row.append(marker,details);wrap.appendChild(row);
  }
}
function renderQualityBacktest(d){
  const c=d.quality_comparison;const p=$('qualityBacktestPanel');if(!p)return;
  p.hidden=!c;if(!c)return;
  const formatRate=v=>v==null?'표본 없음':Number(v).toFixed(1)+'%';
  const formatNet=v=>v==null?'표본 없음':(v>0?'+':'')+Number(v).toFixed(2)+'%';
  setText('qualityRawRate',formatRate(c.raw_buy.hit_pct));
  setText('qualityStrictRate',formatRate(c.strict_buy.hit_pct));
  setText('qualityRawSample',`${c.raw_buy.samples}건 (Wilson 하한 ${formatRate(c.raw_buy.wilson_95pct_lower)})`);
  setText('qualityStrictSample',`${c.strict_buy.samples}건 (Wilson 하한 ${formatRate(c.strict_buy.wilson_95pct_lower)})`);
  setText('qualityRawMean',formatNet(c.raw_buy.mean_net_pct));
  setText('qualityStrictMean',formatNet(c.strict_buy.mean_net_pct));
  setText('qualityCoverage',`선별 비율 ${formatRate(c.coverage_pct)} · 비교 ${c.horizon_bars}봉 후`);
  $('qualitySampleWarning').hidden=c.sufficient_sample===true;
  setText('qualityHoldoutNote',`${c.label} ${c.limitations}`);
}
function render(d){
  latest=d;
  document.body.classList.remove('research-no-stock');
  $('results').hidden=['guide','news','recommendations'].includes(view);
  updateStockIdentity(d.is_demo?'DEMO':d.symbol);
  setText('dataMode',d.mode==='스윙'?'일봉 스윙 분석':d.mode+'봉 분석');
  setText('signalLabel',d.signal_label);
  setText('signalDescription',d.final_reason || (d.is_stale?'지난 완료봉 참고 · 실시간 매매 판단 아님':'현재 분석 결과는 미래 수익을 보장하지 않습니다.'));
  setText('signalTimestamp',shortTime(d.last_completed_bar));
  setText('signalCircle',compareIcon(d.signal));
  const sig=document.querySelector('.signal-card');sig.classList.toggle('sell',d.signal==='SELL');sig.classList.toggle('hold',d.signal==='HOLD');
  $('demoWarning').hidden=!d.is_demo;
  $('staleWarning').hidden=!d.is_stale;
  if(d.daily_integrity?.signal_hold){
    setText('staleWarning',`KIS 확정 종가 검증 보류 [${d.daily_integrity.state}]: ${d.daily_integrity.reason} 신규 매수·매도 추천을 보류했습니다.`);
  }else if(d.is_stale && d.completed_session?.reason){
    setText('staleWarning',`${d.completed_session.reason} 신규 매수·매도 추천을 보류했습니다. 마지막 완료봉은 참고용입니다.`);
  }else{
    setText('staleWarning','시세가 오래되어 신규 매수·매도 추천을 보류했습니다. 아래 정보는 지난 봉 참고용입니다.');
  }
  setText('lastClose',format(d.reference,d.currency));
  setText('quoteValue',format(d.quote,d.currency));
  // Analysis data and the separately polled KIS snapshot are different clocks.
  // This visualization never changes any decision, journal, backtest, or levels.
  window.NOVAIntradayContext?.setAnalysis(d);
  window.NOVAFlow?.onAnalysis?.(d.symbol);
  const total=Math.max(1,d.buy_votes+d.sell_votes+d.hold_votes);
  for(const [id,value] of [['voteBuyBar',d.buy_votes],['voteHoldBar',d.hold_votes],['voteSellBar',d.sell_votes]])$(id).style.width=(100*value/total)+'%';
  setText('voteBuy',d.buy_votes);setText('voteSell',d.sell_votes);setText('voteHold',d.hold_votes);
  setText('stopPrice',format(d.long_stop_reference,d.currency));setText('targetPrice',format(d.long_target_reference,d.currency));
  setText('riskReward',`참고 손익비 ${d.long_risk_reward==null?'미확인':d.long_risk_reward.toFixed(2)} : 1`);
  const integrityMeta=d.daily_integrity ? ` · KIS 일봉 교차확인 ${d.daily_integrity.state||'미확인'}${d.daily_integrity.session_reconstructed?' · 정규장 일봉 재구성 · 이전 봉 세션 미검증':d.daily_integrity.auto_recovered?' · KIS 재조회 복구':''}` : '';
  const sessionMeta=d.completed_session?
    ` · KRX 최신 완료 거래일 ${d.completed_session.expected_completed_session||'미확인'} · 분석봉 ${d.completed_session.latest_completed_session||'미확인'} · ${d.completed_session.reason}`:'';
  setText('metaData',`${d.provider} · ${d.freshness} · 조회: ${shortTime(d.fetched_at)} · 완료봉 경과 ${d.price_age_minutes??'미확인'}분${sessionMeta}${integrityMeta} · ${d.provider_note}`);
  setText('chartEnd',shortTime(d.last_completed_bar));
  renderRiskStructure(d);
  window.NOVACandles?.draw(d);
  renderHigherTrend(d);renderQuality(d);renderHighPrecision(d);renderQualityBacktest(d);renderSignalPerformance(d);renderRoles(d);renderBacktest(d);updateWatchButton();if(!d.risk_structure?.user_entry_provided){maybeNotify(d);window.NOVAJournal?.record(d);}
  const state = d.is_demo?'가상 데이터':d.daily_integrity?.state==='SETTLEMENT_PENDING'?'당일 종가 검증 대기':d.is_stale?'봉 업데이트 지연':d.daily_integrity?.settlement_pending_today?'전일 완료봉 정상':'데이터 조회됨';setConnection(state,!d.is_stale&&!d.is_demo,false);
  if(financeSymbol!==d.symbol){financePayload=null;financeSymbol='';$('financeContent').hidden=true;}
  if($('fusionPanel'))renderFusion(null);
  if(view==='finance')loadFinance();
  window.NOVAJournal?.refresh({onlySymbol:d.symbol});
}
let riskEntryContext = '';
function renderRiskStructure(d){
  const el=$('riskStructurePanel');if(!el)return;
  const v=d.risk_structure;el.hidden=!v;if(!v)return;
  const key=[d.symbol,d.source,d.mode].join('|');
  if(riskEntryContext!==key){$('riskEntryInput').value='';riskEntryContext=key;}
  const values={riskStructureEntry:format(v.entry,d.currency),
    riskStructureSupportRes:(v.support==null?'미확인':format(v.support,d.currency))+' / '+(v.resistance==null?'미확인':format(v.resistance,d.currency)),
    riskStructureStop:format(v.stop,d.currency),riskStructureTarget:format(v.target,d.currency),
    riskStructureRR:v.risk_reward_net==null?'미확인':v.risk_reward_net.toFixed(2)+' : 1'};
  for(const [id,value] of Object.entries(values))setText(id,value);
  const state={'RESEARCH_REFERENCE_ONLY':'연구 참고','UNFAVORABLE':'손익비 불리','REFERENCE_ONLY':'자료 부분 확인',
     'NO_VALID_PLAN':'계획 불가','INSUFFICIENT_DATA':'자료 부족','INVALID_ENTRY':'진입가 오류'};
  setText('riskStructureState',state[v.state]||'연구 참고');
  setText('riskStructureNote',v.reason+(v.user_entry_provided?' · 입력 진입가 반영':' · 완료봉 종가 기준')+
      (v.reason_codes?.length?' · '+v.reason_codes.join(', '):''));
}
async function analyze({jumpToChart=false}={}){
  if(pending){pending.abort();pending=null;}
  let symbol=$('symbolInput').value.trim();
  if(!symbol && config.source!=='demo'){showMessage('종목명 또는 종목코드를 입력해 주세요.','error');return;}
  showMessage('마지막 완료봉과 과거 데이터를 분석 중입니다.','loading');
  setConnection('조회 중');
  const button=document.querySelector('#searchForm button');button.disabled=true;
  const ctrl=new AbortController();pending=ctrl;
  let timedOut=false;
  const kill=setTimeout(()=>{timedOut=true;ctrl.abort();},30000);
  try{
    if(config.source!=='demo')symbol=await resolveInput(symbol);
    let data;
    if(PREVIEW){
      if(config.source!=='demo'){throw new Error('이 HTML은 가상 데이터 데모입니다. 실제 종목 분석은 배포된 NOVA 서버에서 이용하세요.');}
      data=window.NOVA_PREVIEW_DATA[config.mode];
      if(!data)throw new Error('해당 주기 데모 데이터가 없습니다.');
    }else{
      const params=new URLSearchParams({symbol,mode:config.mode,source:config.source,cost_bps:String(config.cost)});
      const entryInput=$('riskEntryInput');
      if(latest && latest.symbol===symbol && (latest.requested_source||latest.source)===config.source && latest.mode===config.mode && entryInput && entryInput.value.trim()!==''){
        const price=Number(entryInput.value);
        if(!Number.isFinite(price)||price<=0)throw new Error('진입가는 양수로 입력하세요.');
        params.set('entry_price',String(price));
      }
      const token=safeSession.get('nova3-token');
      const response=await fetch('/api/analysis?'+params,{headers:token?{'Authorization':'Bearer '+token}:{},cache:'no-store',signal:ctrl.signal});
      if(!response.ok){
        const err=await response.json().catch(()=>({}));
        const detail=typeof err.detail==='string'?err.detail:`HTTP ${response.status}`;
        throw new Error(detail);
      }
      data=await response.json();
    }
    if(ctrl.signal.aborted)return;
    showMessage('');
    render(data);
    // Only intentional stock lookups jump to the chart. Silent refresh never moves the page.
    if(jumpToChart){
      requestAnimationFrame(()=>requestAnimationFrame(()=>$('chartPanel')?.scrollIntoView({behavior:'smooth',block:'start'})));
    }
    return data;
  }catch(err){
    if(ctrl.signal.aborted&&!timedOut)return;
    $('results').hidden=!['research','watchlist'].includes(view);
    setConnection('조회 실패',false,true);
    showMessage('데이터를 가져오지 못했습니다. '+(timedOut?'조회 시간이 초과되었습니다.':err.message)+'\n데이터가 없을 때는 실제 신호를 생성하지 않습니다.','error');
  }finally{clearTimeout(kill);if(pending===ctrl)pending=null;button.disabled=false;}
}

// Official OpenDART statements. Currency is KRW (not USD).
const won100m = n => n == null ? '미확인' : new Intl.NumberFormat('ko-KR',{
  maximumFractionDigits:1}).format(Number(n)/1e8)+'억 원';
const percentValue = n => n == null ? '미확인' : `${n>0?'+':''}${Number(n).toFixed(1)}%`;
function financeElement(tag, className, value){
  const el=document.createElement(tag);if(className)el.className=className;
  if(value!==undefined)el.textContent=String(value);return el;
}
async function loadFinance(){
  const el=$('financeStatus');
  if(view!=='finance')return;
  if(PREVIEW){el.hidden=false;el.textContent='오프라인 미리보기에는 재무·공시 실제 데이터가 포함되지 않습니다. NOVA 서버와 OpenDART 인증키 연결 후 사용하세요.';return;}
  const entered=$('symbolInput').value.trim().toUpperCase();
  const symbol=/^\d{6}\.(KS|KQ)$/.test(entered)?entered:(selectedStock?.symbol===entered?entered:'');
  $('financeContent').hidden=true;
  if(!symbol){el.textContent='먼저 국내 종목을 검색하여 목록에서 선택하거나 005930.KS처럼 종목코드를 입력해 주세요. 시세 분석 없이 재무정보만 조회할 수 있습니다.';return;}
  if(!/^\d{6}\.(KS|KQ)$/.test(symbol)){
    el.textContent='재무·공시는 코스피·코스닥 종목만 지원합니다.';return;
  }
  if(financePayload && financeSymbol===symbol){renderFinance(financePayload);return;}
  if(financePending){financePending.abort();financePending=null;}
  financeSymbol=symbol;
  renderFusion(null);  // avoid showing the previous stock's final decision in this tab
  el.hidden=false;el.textContent=`${symbol} OpenDART 공시·재무자료를 조회 중입니다.`;
  const ctrl=new AbortController();financePending=ctrl;
  try{
    const token=safeSession.get('nova3-token');
    const r=await fetch('/api/fundamentals?'+new URLSearchParams({symbol,years:'3'}),{
      cache:'no-store',credentials:'same-origin',signal:ctrl.signal,
      headers:token?{'Authorization':'Bearer '+token}:{}
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const detail=typeof d.detail==='string'?d.detail:d.detail?.message||`HTTP ${r.status}`;
      throw new Error(detail);
    }
    if(ctrl.signal.aborted || financeSymbol!==symbol || view!=='finance')return;
    financePayload=d;renderFinance(d);
  }catch(e){
    if(ctrl.signal.aborted)return;
    el.hidden=false;el.textContent='재무자료 조회 불가: '+e.message+' · OpenDART 키 설정 및 API 응답을 확인하세요.';
  }finally{if(financePending===ctrl)financePending=null;}
}
function signalKo(signal){return ({BUY:'매수 후보',SELL:'보유 축소',HOLD:'관망'})[signal]||'미확인';}
function renderFusion(_fund){
  const panel=$('fusionPanel');
  if(!latest||latest.mode!=='스윙'||!/^[0-9]{6}\.(KS|KQ)$/.test(latest.symbol)||latest.is_demo||
     (view==='finance' && financeSymbol && financeSymbol!==latest.symbol)){
    panel.hidden=true;return;
  }
  // Exactly the server's final decision. The finance tab can render independently,
  // but it can never recompute or overwrite the signal that triggered notifications.
  panel.hidden=false;
  setText('fusionTechnical',signalKo(latest.strict_signal||latest.signal));
  const gate=latest.financial_gate||'UNKNOWN';
  setText('fusionGate',({PASS:'위험 필터 통과',CAUTION:'재무 주의',BLOCK:'재무 위험',UNKNOWN:'재무 미확인',NOT_SUPPORTED:'지원 대상 아님'})[gate]||'자료 미확인');
  setText('fusionCombined',signalKo(latest.signal));
  setText('fusionReason',latest.final_reason||latest.finance_note||'신뢰할 수 있는 재무 판단 자료가 없습니다.');
}
function renderFinance(d){
  $('financeStatus').hidden=true;
  $('financeContent').hidden=false;
  setText('financeCompany',`${d.company_name} · ${d.market} · ${d.ticker}`);
  setText('financeTime',`DART 조회 ${shortTime(d.retrieved_at)} / 기준 사업연도 ${d.annual_latest??'미확인'}`);
  const gate=d.financial_gate;
  setText('financialScore',gate?.score==null?'평가 불충분':gate.score.toFixed(1)+' / 100');
  setText('financialCoverage',gate?'확인 항목 가중치 '+gate.coverage_pct+'% · '+gate.gate_label:'평가 데이터 없음');
  setText('financialGateReason',gate?.reason||'확인된 재무 정보가 없습니다.');
  const factors=$('financialFactorList');factors.replaceChildren();
  for(const factor of gate?.factors||[]){
    const row=financeElement('div','factor-item');
    const val=factor.value==null?'미확인':factor.key==='revenue'||factor.key==='net_income'||factor.key==='operating_cashflow'?won100m(factor.value):percentValue(factor.value);
    row.append(financeElement('span','',factor.label),financeElement('strong','',factor.grade+' · '+val));factors.appendChild(row);
  }
  renderFusion(d);

  const grid=$('financeMetrics');grid.replaceChildren();
  const m=d.metrics||{};
  const stats=[['매출액',won100m(m.revenue)],['영업이익',won100m(m.operating_profit)],
    ['당기순이익',won100m(m.net_income)],['영업현금흐름',won100m(m.operating_cashflow)],
    ['영업이익률',percentValue(m.operating_margin_pct)],['부채비율',percentValue(m.debt_ratio_pct)],
    ['매출 전년비',percentValue(m.revenue_yoy_pct)],['영업이익 전년비',percentValue(m.operating_profit_yoy_pct)]];
  for(const [label,value] of stats){const card=financeElement('div','finance-stat');card.append(
    financeElement('span','',label),financeElement('strong','',value));grid.appendChild(card);}
  const notices=$('financeFindings');notices.replaceChildren();
  for(const item of d.findings||[]){notices.appendChild(financeElement('div','finance-finding '+
    (item.level==='risk'?'risk':item.level==='caution'?'caution':''),item.message));}
  if(!d.findings?.length)notices.appendChild(financeElement('p','text-subtle','공시된 연간 재무제표를 찾지 못했습니다.'));
  const tbody=$('financeAnnual');tbody.replaceChildren();
  for(const item of d.annual||[]){const tr=document.createElement('tr');
    [item.year+' · '+item.fs_div,won100m(item.metrics?.revenue),won100m(item.metrics?.operating_profit),won100m(item.metrics?.net_income)]
      .forEach(v=>tr.appendChild(financeElement('td','',v)));tbody.appendChild(tr);}
  if(!d.annual?.length){const tr=document.createElement('tr');
    const td=financeElement('td','', '공시된 사업보고서 재무자료가 없습니다.');td.colSpan=4;tr.appendChild(td);tbody.appendChild(tr);}
  setText('financeInterim',d.interim?
    `최근 조회된 분기·반기: ${d.interim.year}년 ${d.interim.type} (${d.interim.fs_div}) · ${d.interim.note}`:
    '분기·반기 재무제표는 확인되지 않았습니다.');
  const filings=$('financeFilings');filings.replaceChildren();
  for(const filing of d.disclosures||[]){
    // Only trusted official DART URL with validated 14-digit receipt number.
    if(!/^\d{14}$/.test(filing.receipt_no||''))continue;
    const a=financeElement('a','finance-filing');a.href='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+filing.receipt_no;
    a.target='_blank';a.rel='noopener noreferrer';
    a.append(financeElement('span','finance-filing-date',filing.date),financeElement('span','finance-filing-title',filing.title),financeElement('span','','↗'));
    filings.appendChild(a);
  }
  if(!filings.children.length)filings.appendChild(financeElement('p','text-subtle','최근 180일의 공시가 확인되지 않았습니다.'));
}

function scheduleRefresh(){
  if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null;}
  if(config.auto&&!PREVIEW)refreshTimer=setInterval(()=>{
    if(!document.hidden&&!pending)analyze();
  },60000);
}
function init(){
  const savedSymbol=safeStore.get('nova3-symbol','005930.KS');
  const oldSymbol=typeof savedSymbol==='string'&&/^\d{6}\.(KS|KQ)$/.test(savedSymbol)?savedSymbol:'005930.KS';
  if(oldSymbol!==savedSymbol)safeStore.set('nova3-symbol',oldSymbol);
  $('symbolInput').value=PREVIEW?'DEMO':oldSymbol;
  if(selectedStock?.symbol===oldSymbol)rememberStockName(oldSymbol,selectedStock.name);
  $('symbolInput').addEventListener('input',scheduleLookup);

  $('symbolInput').addEventListener('keydown',e=>{if(e.key==='Escape')searchUI();});
  $('riskEntryApply')?.addEventListener('click',()=>{
    if(!latest){showMessage('먼저 종목 분석을 실행하세요.','error');return;}
    if(PREVIEW){showMessage('가상 미리보기에서는 진입가를 변경할 수 없습니다.','error');return;}
    analyze();
  });
  document.addEventListener('click',e=>{if(!e.target.closest('.query-panel'))searchUI();});
  $('copyInstallLink').onclick=copySiteLink;
  pwaStatus();
  config.mode=['1분','5분','15분','스윙'].includes(config.mode)?config.mode:'스윙';
  $('sourceSelect').value=config.source;
  if(PREVIEW){$('sourceSelect').value='demo';$('sourceSelect').disabled=true;$('costInput').disabled=true;$('autoRefresh').disabled=true;}
  $('costInput').value=config.cost;
  $('costDisplay').textContent=`편도 ${config.cost}bp`;
  $('autoRefresh').checked=config.auto;
  $('tokenInput').value=safeSession.get('nova3-token');
  checkAuthStatus();
  displayMode();
  $('searchForm').addEventListener('submit',e=>{e.preventDefault();safeStore.set('nova3-symbol',$('symbolInput').value.trim());selectView('analyze');analyze({jumpToChart:true});});
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{config.mode=b.dataset.mode;storeConfig();displayMode();analyze();});
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>selectView(b.dataset.view));
  document.querySelectorAll('[data-research-target]').forEach(b=>b.onclick=()=>{if(view!=='research')selectView('research');$(b.dataset.researchTarget)?.scrollIntoView({behavior:'smooth',block:'start'});});
  document.querySelectorAll('[data-scroll-target]').forEach(b=>b.addEventListener('click',()=>{
    if(view!=='analyze')selectView('analyze');
    const target=$(b.dataset.scrollTarget);
    if(!target)return;
    if(target.tagName==='DETAILS')target.open=true;
    target.scrollIntoView({behavior:'smooth',block:'start'});
  }));
  // The guide lives outside #results so it can be opened before the first stock analysis.
  document.querySelectorAll('[data-guide-target]').forEach(b=>b.onclick=()=>{
    const target=b.dataset.guideTarget;
    if(['backtest','finance'].includes(target)&&!latest){
      selectView('analyze');showMessage('먼저 종목을 분석한 뒤 이 메뉴를 확인해 주세요.');
    }else selectView(target);
    window.scrollTo({top:0,behavior:'smooth'});
  });
  document.querySelectorAll('[data-guide-action="settings"]').forEach(b=>b.onclick=()=>$('settingsDialog').showModal());
  document.querySelectorAll('[data-symbol]').forEach(b=>b.onclick=()=>{if(PREVIEW){showMessage('가상 데이터 미리보기에서는 실제 종목을 조회할 수 없습니다.','error');return;}$('symbolInput').value=b.dataset.symbol;selectedStock={name:b.textContent.trim(),symbol:b.dataset.symbol};rememberStockName(b.dataset.symbol,selectedStock.name);safeStore.set('nova3-selected-stock',selectedStock);safeStore.set('nova3-symbol',b.dataset.symbol);searchUI();if(config.source==='demo'){config.source='kis';$('sourceSelect').value='kis';}storeConfig();selectView('analyze');analyze({jumpToChart:true});});
  $('watchButton').onclick=()=>{
    if(!latest)return;
    if(latest.is_demo){showMessage('가상 데이터는 관심종목으로 등록할 수 없습니다.');return;}
    const symbol=latest.symbol;
    if(watchlist.includes(symbol))watchlist=watchlist.filter(x=>x!==symbol);
    else watchlist.unshift(symbol);
    watchlist=watchlist.slice(0,30);
    safeStore.set('nova3-watch',watchlist);updateWatchButton();window.NOVAMonitor?.afterChange();
  };
  $('settingsButton').onclick=()=>$('settingsDialog').showModal();
  $('costInput').oninput=()=>setText('costDisplay',`편도 ${$('costInput').value}bp`);
  $('saveSettings').onclick=saveLoginAndSettings;
  $('logoutButton').onclick=logoutDevice;
  $('sourceSelect').onchange=()=>{
    const val=$('sourceSelect').value;
    const msg=val==='demo'?'가상 데이터 미리보기':'KIS 전용 시세. Yahoo 자동 대체 없음. 현재가와 정규장 일봉은 다를 수 있습니다.';
    setText('sourceHint',msg);
  };
  $('sourceSelect').dispatchEvent(new Event('change'));
  $('notificationButton').onclick=async()=>{
    if(!('Notification'in window)||!window.isSecureContext){showMessage('알림은 HTTPS 등 보안 연결에서 지원됩니다.','error');return;}
    const perm=await Notification.requestPermission();showMessage(perm==='granted'?'신호 변경 알림이 허용되었습니다. 브라우저가 열려 있을 때 선택 종목만 감시합니다.':'알림이 허용되지 않았습니다.');
  };
  $('installButton').onclick=async()=>{
    if(pendingInstall){
      try{await pendingInstall.prompt();await pendingInstall.userChoice;}catch{}
      pendingInstall=null;
    }else{
      $('installGuide').open=true;
      $('installGuide').scrollIntoView({behavior:'smooth',block:'center'});
      showMessage('앱 내부 브라우저에서는 설치가 제한될 수 있습니다. 설치 안내에서 주소를 복사해 외부 Chrome/삼성 인터넷으로 접속하세요.');
    }
    $('settingsDialog').close();
  };
  window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();pendingInstall=e;pwaStatus();});
  window.addEventListener('appinstalled',()=>{pendingInstall=null;pwaStatus();showMessage('홈 화면 앱 설치가 완료됐습니다.');});
  if('serviceWorker'in navigator&&!PREVIEW&&(location.protocol==='https:'||location.hostname==='localhost'||location.hostname==='127.0.0.1'))navigator.serviceWorker.register('/sw.js').catch(()=>{});
  window.addEventListener('online',()=>setConnection('네트워크 연결됨'));
  window.addEventListener('offline',()=>setConnection('오프라인',false,true));
  window.NOVAJournal?.init();
  scheduleRefresh();renderWatchlist();window.NOVAMonitor?.afterChange();analyze();
}
window.NOVAConnect={
  refreshVerified:async(symbol)=>{
    if(pending||latest?.symbol!==symbol||config.mode!=='스윙'||$('symbolInput').value.trim().toUpperCase()!==symbol.toUpperCase())return null;
    return await analyze();
  },
  watchlist:()=>Array.isArray(watchlist)?[...watchlist]:[],
  selected:()=>selectedStock?{...selectedStock}:null,
  source:()=>config.source,cost:()=>config.cost,currentView:()=>view,
  openRecommendation:(symbol,name)=>{
    if(!/^\d{6}\.(KS|KQ)$/.test(String(symbol||'')))return;
    $('symbolInput').value=symbol;
    selectedStock={symbol,name:String(name||symbol),market:symbol.endsWith('.KS')?'코스피':'코스닥'};
    rememberStockName(symbol,selectedStock.name);
    safeStore.set('nova3-selected-stock',selectedStock);
    config.mode='스윙';if(config.source==='demo')config.source='kis';
    $('sourceSelect').value=config.source;storeConfig();displayMode();
    selectView('analyze');analyze({jumpToChart:true});
  },
  open:(symbol,mode='스윙')=>{
    if(!watchlist.includes(symbol))return;
    $('symbolInput').value=symbol;
    if(selectedStock?.symbol!==symbol)selectedStock=null;
    config.mode=['1분','5분','15분','스윙'].includes(mode)?mode:'스윙';
    if(config.source==='demo')config.source='kis';
    $('sourceSelect').value=config.source;storeConfig();displayMode();
    selectView('analyze');analyze({jumpToChart:true});
  }
};
window.addEventListener('DOMContentLoaded',init);
