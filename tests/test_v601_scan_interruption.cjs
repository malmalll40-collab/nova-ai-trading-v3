'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),test=require('node:test');
const original=fs.readFileSync('static/recommendations.js','utf8');
const source=original.replace('window.NOVAScanner={onViewChange,load:fetchState};','window.NOVAScanner={reconcile,remember,clearInterruption,tracked};');
const A='0123456789abcdef',B='abcdef0123456789';
function setup(store=new Map()){
  const ctx={window:{addEventListener(){}},document:{readyState:'loading'},sessionStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}};
  vm.runInNewContext(source,ctx);
  return {api:ctx.window.NOVAScanner,store};
}
test('active job missing after reload reports unconfirmed completion',()=>{
  const {api,store}=setup();
  assert.equal(api.reconcile('pattern',{status:'running',job_id:A}),false);
  const fresh=setup(store).api;
  assert.equal(fresh.reconcile('pattern',{status:'idle'}),true);
  assert.equal(fresh.reconcile('pattern',{status:'idle'}),true);
  assert.equal(fresh.reconcile('full',{status:'idle'}),false);
});
test('matching terminal state is not an interruption',()=>{
  const {api,store}=setup();
  api.reconcile('full',{status:'running',job_id:A});
  assert.equal(api.reconcile('full',{status:'complete',job_id:A}),false);
  assert.equal(store.has('nova-scan-active-full'),false);
  assert.equal(api.reconcile('full',{status:'idle'}),false);
});
test('replacement job warns and separately tracks new job',()=>{
  const {api}=setup();
  api.reconcile('full',{status:'queued',job_id:A});
  assert.equal(api.reconcile('full',{status:'running',job_id:B}),true);
  assert.equal(api.tracked.full,B);
  api.clearInterruption('full');
  assert.equal(api.reconcile('full',{status:'running',job_id:B}),false);
});
test('invalid identifiers and unconfirmed snapshots do not erase tracked job',()=>{
  const {api,store}=setup();
  api.reconcile('pattern',{status:'running',job_id:A});
  assert.equal(api.reconcile('pattern',{status:'UNKNOWN'}),false);
  assert.equal(api.reconcile('pattern',{status:'complete',job_id:'invalid'}),false);
  assert.equal(store.get('nova-scan-active-pattern'),A);
});
