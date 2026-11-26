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
  const idle={status:'idle',fsc_configured:true};let states={full:idle,pattern:idle};
  const window={addEventListener:(k,cb)=>handlers[k]=cb,NOVAConnect:{currentView:()=> 'recommendations'},NOVAPatternResearch:{render:(target,res)=>target.append(res.signal_pattern_research)}};
  vm.runInNewContext(fs.readFileSync('static/recommendations.js','utf8'),{window,document:{getElementById:el,createElement:el,addEventListener(){},hidden:false},sessionStorage:{getItem(){return null;}},setInterval(){return 1;},clearInterval(){},fetch:async(url,options)=>{requests.push({url,options});const p=url.includes('?profile=')?url.split('=')[1]:JSON.parse(options?.body||'{}').profile||'full';return {ok:true,json:async()=>states[p]};},Intl});
  handlers.DOMContentLoaded();
  return {el,window,requests,setResponse:(p,x)=>states[p]=x};
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
test('both buttons share the single scan and progress; second click blocked',async()=>{
  const h=harness();
  h.setResponse('pattern',{status:'running',active_profile:'pattern',progress:{phase:'deep',done:2,total:48}});
  h.setResponse('full',{status:'idle',fsc_configured:true,active_profile:'pattern'});
  const first=h.el('patternScan').onclick();
  assert.equal(h.el('recommendScan').disabled,true);
  assert.equal(h.el('patternScan').disabled,true);
  await h.el('recommendScan').onclick();
  await first;
  assert.equal(h.requests.filter(x=>x.url==='/api/recommendations/run').length,1);
  assert.ok(h.el('patternStatus').textContent.includes('검색 진행 중'));
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
  assert.equal(h.el('recommendScan').disabled,true);
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
