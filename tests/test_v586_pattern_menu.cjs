const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function harness(){
  const nodes=new Map(),requests=[],handlers={};
  const el=id=>{
    if(!nodes.has(id))nodes.set(id,{id,hidden:false,disabled:id==='patternScan',value:id.endsWith('Depth')?'48':id.endsWith('Market')?'all':'auto',textContent:'',dataset:{},style:{},attrs:{},children:[],classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(){this.children=[];},append(...x){this.children.push(...x);},appendChild(x){this.append(x);}});
    return nodes.get(id);
  };
  const idle={status:'idle',fsc_configured:true};let states={full:idle,pattern:idle},networkFailure=false,rateLimited=false,clock=100000;
  const window={addEventListener:(k,cb)=>handlers[k]=cb,NOVAConnect:{currentView:()=> 'recommendations'},NOVAPatternResearch:{render:(target,res)=>target.append(res.signal_pattern_research)}};
  vm.runInNewContext(fs.readFileSync('static/recommendations.js','utf8'),{window,document:{getElementById:el,createElement:el,addEventListener(){},hidden:false},sessionStorage:{getItem(){return null;}},setInterval(){return 1;},clearInterval(){},fetch:async(url,options)=>{requests.push({url,options});if(networkFailure)throw Error('offline');if(rateLimited)return {ok:false,status:429,headers:{get:()=> '20'}};if(url.includes('profile=all'))return {ok:true,json:async()=>({profiles:states})};const p=url.includes('?profile=')?url.split('=')[1]:JSON.parse(options?.body||'{}').profile||'full';return {ok:true,json:async()=>states[p]};},Intl,Date:{now:()=>clock}});
  handlers.DOMContentLoaded();
  return {el,window,requests,setResponse:(p,x)=>states[p]=x,setNetworkFailure:x=>networkFailure=x,setRateLimited:x=>rateLimited=x,advance:ms=>clock+=ms};
}
test('pattern controls are always visible before search and initialize without network traffic',()=>{
  const html=fs.readFileSync('static/index.html','utf8');
  for(const id of ['patternMenu','patternMenuBody','patternScan','patternStatus','patternResults'])assert.ok(html.includes('id="'+id+'"'));
  assert.ok(html.indexOf('id="patternMenu"')<html.indexOf('id="popularPanel"'));
  assert.ok(!html.includes('id="patternMenuToggle"'));
  assert.ok(html.includes('id="patternMenuBody">'));
  const h=harness();
  assert.equal(h.el('patternMenuBody').hidden,false);
  assert.equal(h.el('patternScan').disabled,false);
  assert.equal(typeof h.el('patternScan').onclick,'function');
  assert.equal(h.requests.length,0);
});
test('both buttons accept independent requests after first submission; duplicate blocked',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'running',active_profile:'pattern',progress:{phase:'deep',done:2,total:48}});
  h.setResponse('full',{status:'idle',fsc_configured:true,active_profile:'pattern'});
  const first=h.el('patternScan').onclick();
  assert.equal(h.el('recommendScan').disabled,true);
  assert.equal(h.el('patternScan').disabled,true);
  await h.el('patternScan').onclick();
  await first;
  assert.equal(h.requests.filter(x=>x.url==='/api/recommendations/run').length,1);
  assert.equal(h.el('recommendScan').disabled,false);
  assert.ok(h.el('patternStatus').textContent.includes('검색 진행 중'));
  h.setResponse('full',{status:'queued',job_id:'f1',fsc_configured:true,active_profile:'pattern'});
  await h.el('recommendScan').onclick();
  assert.equal(h.requests.filter(x=>x.url==='/api/recommendations/run').length,2);
  assert.ok(h.el('recommendStatus').textContent.includes('독립 검색 대기'));
  assert.equal(h.el('recommendCancel').disabled,false);
});
test('changed options clear previously shown pattern results immediately',()=>{
  const h=harness();h.el('patternResults').append('old rank');
  h.el('patternDepth').onchange();
  assert.equal(h.el('patternResults').children.length,0);
});
test('complete result renders in dedicated menu and reset clears it',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'complete',fsc_configured:true,source:'fsc',depth:48,market:'all',job_id:'1',result:{data_date:'20261125',signal_pattern_research:{version:'5.8.4'},recommendations:[],interest_candidates:[]}});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('patternResults').children.length,1);
  assert.ok(h.el('patternStatus').textContent.includes('검색 완료'));
  h.setResponse('pattern',{status:'failed',fsc_configured:true,error:{message:'test failure'}});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('patternResults').children.length,0);
  assert.ok(h.el('patternStatus').textContent.includes('test failure'));
});

test('two buttons request distinct profiles with same shared scan lock',async()=>{
  for(const [id,profile] of [['recommendScan','full'],['patternScan','pattern']]){
    const h=harness();
    h.setResponse(profile,{status:'running',active_profile:profile,progress:{phase:'deep',done:1,total:48}});
    await h.el(id).onclick();
    const req=h.requests.find(x=>x.url==='/api/recommendations/run');
    assert.equal(JSON.parse(req.options.body).profile,profile);
  }
});
test('pattern-only completion does not claim zero strict candidates',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'complete',fsc_configured:true,source:'fsc',depth:48,market:'all',job_id:'p',result:{data_date:'20261125',scan_profile:'pattern',signal_pattern_research:{eligible_total:2},elapsed_seconds:12}});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('patternResults').children.length,1);
  assert.equal(h.el('recommendNumbers').hidden,true);
  assert.ok(h.el('patternStatus').textContent.includes('검색 완료'));
  assert.ok(h.el('patternStatus').textContent.includes('12초'));
  assert.ok(!h.el('patternStatus').textContent.includes('엄격 매수 0'));
});

test('cancel addresses only the running profile and exact job id',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'running',active_profile:'pattern',job_id:'p1',fsc_configured:true});
  h.setResponse('full',{status:'idle',active_profile:'pattern',fsc_configured:true});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('patternCancel').disabled,false);
  assert.equal(h.el('recommendCancel').disabled,true);
  assert.equal(h.el('recommendScan').disabled,false);
  h.setResponse('pattern',{status:'cancelling',active_profile:'pattern',job_id:'p1',fsc_configured:true});
  await h.el('patternCancel').onclick();
  const req=h.requests.find(x=>x.url==='/api/recommendations/cancel');
  assert.deepEqual(JSON.parse(req.options.body),{profile:'pattern',job_id:'p1'});
  assert.ok(h.el('patternStatus').textContent.includes('취소 처리 중'));
  h.setResponse('pattern',{status:'cancelled',fsc_configured:true});
  h.setResponse('full',{status:'idle',fsc_configured:true});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('recommendScan').disabled,false);
  assert.equal(h.el('patternScan').disabled,false);
});
test('independent completed results coexist, and options clear only their own result',async()=>{
  const h=harness();
  const done={status:'complete',fsc_configured:true,source:'fsc',depth:48,market:'all',job_id:'x'};
  h.setResponse('full',{...done,result:{scan_profile:'full',data_date:'20261125',recommendations:[],interest_candidates:[]}});
  h.setResponse('pattern',{...done,result:{scan_profile:'pattern',data_date:'20261125',signal_pattern_research:{eligible_total:2}}});
  await h.window.NOVAScanner.load();
  assert.equal(h.el('recommendNumbers').hidden,false);
  assert.equal(h.el('patternResults').children.length,1);
  h.el('patternDepth').value='24';
  await h.el('patternDepth').onchange();
  assert.equal(h.el('patternResults').children.length,0);
  assert.equal(h.el('recommendNumbers').hidden,false);
  assert.ok(h.el('patternStatus').textContent.includes('이전 검색 조건'));
});

test('network failure clears both previous result lists and disables unsafe controls until recovery',async()=>{
  const h=harness();
  const done={status:'complete',fsc_configured:true,source:'fsc',depth:48,market:'all',job_id:'x'};
  h.setResponse('full',{...done,result:{scan_profile:'full',data_date:'20261125',recommendations:[],interest_candidates:[]}});
  h.setResponse('pattern',{...done,result:{scan_profile:'pattern',data_date:'20261125',signal_pattern_research:{eligible_total:2}}});
  await h.window.NOVAScanner.load();
  assert.ok(h.el('recommendResults').children.length>0);
  assert.equal(h.el('patternResults').children.length,1);
  h.setNetworkFailure(true);
  await h.window.NOVAScanner.load();
  assert.equal(h.el('recommendResults').children.length,0);
  assert.equal(h.el('patternResults').children.length,0);
  assert.equal(h.el('recommendNumbers').hidden,true);
  for(const id of ['recommendScan','patternScan','recommendCancel','patternCancel'])assert.equal(h.el(id).disabled,true);
  assert.ok(h.el('patternStatus').textContent.includes('검색 상태 확인 실패'));
  h.setNetworkFailure(false);
  await h.window.NOVAScanner.load();
  assert.equal(h.el('recommendScan').disabled,false);
  assert.equal(h.el('patternScan').disabled,false);
  assert.equal(h.el('patternResults').children.length,1);
});

test('one combined status poll and explicit separate work states',async()=>{
  const h=harness();
  h.setResponse('full',{status:'idle',fsc_configured:true,active_profile:'pattern'});
  h.setResponse('pattern',{status:'running',active_profile:'pattern',fsc_configured:true,job_id:'p1'});
  await h.window.NOVAScanner.load();
  assert.equal(h.requests.filter(x=>x.url.includes('/status')).length,1);
  assert.ok(h.requests[0].url.includes('profile=all'));
  assert.ok(h.el('recommendStatus').textContent.includes('다른 검색 실행 중에도 독립 요청 가능'));
  assert.ok(h.el('patternStatus').textContent.includes('검색 진행 중'));
});
test('429 respects retry interval and reconnects without starting or cancelling any scan',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'running',active_profile:'pattern',fsc_configured:true,job_id:'p1'});
  await h.window.NOVAScanner.load();
  h.setRateLimited(true);
  await h.window.NOVAScanner.load();
  const n=h.requests.length;
  assert.ok(h.el('patternStatus').textContent.includes('작업 중단 여부 미확인'));
  h.setRateLimited(false);
  h.advance(19000);
  await h.window.NOVAScanner.load();
  assert.equal(h.requests.length,n);
  h.advance(1000);
  await h.window.NOVAScanner.load();
  assert.equal(h.requests.length,n+1);
  assert.ok(h.el('patternStatus').textContent.includes('검색 진행 중'));
  assert.equal(h.el('patternCancel').disabled,false);
  assert.ok(h.requests.every(x=>x.url.includes('/status')));
});

test('zero strict results distinguish valid no-buy from incomplete data',async()=>{
  const h=harness();
  const done={status:'complete',fsc_configured:true,source:'fsc',depth:48,market:'all',job_id:'f'};
  const base={data_date:'20261125',recommendations:[],interest_candidates:[],recommended_count:0,deep_target:60,deep_succeeded:60};
  h.setResponse('full',{...done,result:{...base,scan_assessment:{status:'NO_STRICT_CANDIDATES',data_limited_first_rejections:0,validation_first_rejections:15}}});
  await h.window.NOVAScanner.load();
  const text=n=>[n.textContent,...n.children.map(text)].join(' ');
  assert.ok(text(h.el('recommendResults')).includes('검토한 후보에서 모든 엄격 매수 조건'));
  h.setResponse('full',{...done,result:{...base,scan_assessment:{status:'DATA_LIMITED',data_limited_first_rejections:60,validation_first_rejections:0}}});
  await h.window.NOVAScanner.load();
  assert.ok(text(h.el('recommendResults')).includes('확정할 수 없습니다'));
});
