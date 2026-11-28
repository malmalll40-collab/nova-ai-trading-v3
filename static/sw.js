/* Keep an offline shell, but fetch frequently changing JS/CSS from network first. Never cache API/market responses. */
const CACHE_NAME='nova3-shell-v45-korea-only-chartfirst-popular-breadth-status-rank-20261120-kis-four-period-rest-four-analysis-modes-kis-default-v2-evidence451-kis-intraday-context-20261123-session-aware-auto-analysis-20261123-evidence460-merged-regime470-regime471-click-ready-stock-identity472-core-compare473-candle-research474-v5-lab-ui-v534-regular-session-reconstruction-20261124-r2-v543-session-research-v544-chart-display-v550-research-v551-chart-probe-v552-analysis-regime-v561-hierarchical-v570-causal-risk-research-v585-pattern-ui-app-__NOVA_APP_VERSION__-krx-date-probe-20261127-forward-dashboard-v14';
const FILES=['/static/forward_shadow.js','/','/static/styles.css','/static/app.js','/static/candles.js','/static/ops_diagnostics.js','/static/news.js','/static/pattern_research.js','/static/recommendations.js','/static/popular.js','/static/recommendation_history.js','/static/investor_flow.js','/static/intraday_context.js','/static/journal.js','/static/v5_dashboard.js','/static/monitor.js','/manifest.webmanifest','/static/icons/icon-192.png','/static/icons/icon-512.png'];
const RELEASE='__NOVA_APP_VERSION__';
const RELEASED_JS=['/static/forward_shadow.js','/static/pattern_research.js','/static/recommendations.js','/static/candles.js','/static/styles.css','/static/app.js','/static/v5_dashboard.js'];
const INSTALL_FILES=FILES.map(path=>RELEASED_JS.includes(path)?path+'?v='+RELEASE:path);
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(INSTALL_FILES.map(path=>new Request(path,{cache:'reload'})))).then(()=>self.skipWaiting()));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
 if(e.request.method!=='GET')return;
 const url=new URL(e.request.url);
 if(url.origin!==self.location.origin)return;
 if(url.pathname.startsWith('/api/')||url.pathname==='/healthz')return; // network-only, no stale trading data
 if(e.request.mode==='navigate'){
   e.respondWith(fetch(e.request,{cache:'no-store'}).catch(async()=>{const cache=await caches.open(CACHE_NAME);return (await cache.match('/'))||Response.error();}));return;
 }
 if(FILES.includes(url.pathname)){
   // JS and CSS are updated frequently; network-first prevents an installed PWA
   // from serving stale market-scan UI after a code release. Keep offline fallback.
   if(url.pathname.endsWith('.js')||url.pathname.endsWith('.css')){
     e.respondWith((async()=>{
       try{
         const response=await fetch(e.request,{cache:'no-store'});
         if(response.ok){
           try{const cache=await caches.open(CACHE_NAME);await cache.put(e.request,response.clone());}catch{}
         }
         return response;
       }catch{const cache=await caches.open(CACHE_NAME);return (await cache.match(e.request))||Response.error();}
     })());
   }else{
     e.respondWith(caches.match(e.request).then(x=>x||fetch(e.request)));
   }
 }
});
