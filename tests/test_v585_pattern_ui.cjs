const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.textContent='';this.attrs={};}
  append(...nodes){this.children.push(...nodes);}
  setAttribute(k,v){this.attrs[k]=v;}
}
function env(){
  const opens=[];
  const window={NOVAConnect:{openRecommendation:(...a)=>opens.push(a)}};
  vm.runInNewContext(fs.readFileSync('static/pattern_research.js','utf8'),{window,document:{createElement:t=>new Element(t)}});
  return {window,opens,parent:new Element('div')};
}
function all(n){return [n,...n.children.flatMap(all)];}
function text(n){return all(n).map(x=>x.textContent).join(' ');}
function fixture(){
  const asof='20261125';
  const row={symbol:'005930.KS',name:'<sample>',rank:1,asof,score:87.2,pattern:'EARLY',counts:{'5':2,'10':3,'20':3},
    streak:1,last_buy_age:0,since_first_buy_pct:4,ret5_pct:1,drawdown_from_20d_close_peak_pct:-1,
    research_only:true,not_actionable:true,probability:null};
  return {data_date:asof,deep_failed:1,signal_pattern_research:{version:'5.8.4',status:'RESEARCH_ONLY',research_only:true,asof,
    deep_target:24,evaluated:23,eligible_total:1,rows:[row],exclusions:{EXTENDED_MOVE:3}}};
}
test('renders independent rank and explicit non-probability language',()=>{
  const {window,parent}=env();window.NOVAPatternResearch.render(parent,fixture());
  const t=text(parent);
  for(const word of ['AI 신호패턴 추천','1위','<sample>','87.2/100','초기 상승형','최근 5일','2회','3회','고점 추격 제한','상승 확률','일부 종목','20261125'])assert.ok(t.includes(word),word);
  assert.equal(all(parent).filter(n=>n.tagName==='article').length,1);
});
test('detail button invokes existing stock detail only',()=>{
  const {window,parent,opens}=env();window.NOVAPatternResearch.render(parent,fixture());
  all(parent).find(n=>n.tagName==='button').onclick();
  assert.deepEqual(opens,[['005930.KS','<sample>']]);
});
test('unavailable, old-version and mismatched-date results do not show ranks',()=>{
  for(const res of [{},{...fixture(),data_date:'20261124'},{...fixture(),signal_pattern_research:{...fixture().signal_pattern_research,version:'old'}}]){
    const {window,parent}=env();window.NOVAPatternResearch.render(parent,res);
    assert.ok(text(parent).includes('연구 결과 미확인'));
    assert.equal(all(parent).filter(n=>n.tagName==='article').length,0);
  }
});
test('empty and malformed candidate lists do not invent cards',()=>{
  for(const rows of [[],[null,{...fixture().signal_pattern_research.rows[0],score:101}],[{...fixture().signal_pattern_research.rows[0],not_actionable:false}]]){
    const {window,parent}=env(),res=fixture();res.signal_pattern_research.rows=rows;
    window.NOVAPatternResearch.render(parent,res);
    assert.equal(all(parent).filter(n=>n.tagName==='article').length,0);
    assert.ok(text(parent).includes('임의로 만들지 않습니다'));
  }
});
test('rank display bounded to 20',()=>{
  const {window,parent}=env(),res=fixture();
  const r=res.signal_pattern_research.rows[0];
  res.signal_pattern_research.rows=Array.from({length:30},(_,i)=>({...r,rank:i+1,symbol:String(i).padStart(6,'0')+'.KS'}));
  window.NOVAPatternResearch.render(parent,res);
  assert.equal(all(parent).filter(n=>n.tagName==='article').length,20);
});
test('shell includes renderer before legacy recommendations and network-first cache asset',()=>{
  const html=fs.readFileSync('static/index.html','utf8'),sw=fs.readFileSync('static/sw.js','utf8'),js=fs.readFileSync('static/recommendations.js','utf8');
  assert.ok(html.includes('/static/pattern_research.js'));
  assert.ok(html.indexOf('/static/pattern_research.js')<html.indexOf('/static/recommendations.js'));
  assert.ok(sw.includes('/static/pattern_research.js'));
  assert.ok(sw.includes('v585-pattern-ui'));
  assert.ok(js.includes('window.NOVAPatternResearch.render(target,res)'));
  assert.ok(js.includes("res.scan_profile==='pattern'"));
  assert.ok(js.includes('const items=Array.isArray(res.recommendations)'));
  assert.ok(js.includes('const interests=Array.isArray(res.interest_candidates)'));
});

test('actual Python adapter and rank output renders without hand-written result keys',()=>{
  const {execFileSync}=require('node:child_process');
  const script=`import json
from pytest import MonkeyPatch
from tests.test_v584_pattern_scan import candidate_input
from nova_v584_pattern_scan import candidate, finish
with MonkeyPatch.context() as m:
    s,d=candidate_input(m)
    row,reason=candidate(s,d,'20261125','20261125')
    assert row is not None and reason is None
    p=finish([row],{'SESSION_MISMATCH':1},asof='20261125',target=2)
    print(json.dumps({'data_date':'20261125','deep_failed':0,'signal_pattern_research':p},allow_nan=False))
`;
  const res=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',script],{encoding:'utf8'}));
  const {window,parent}=env();
  window.NOVAPatternResearch.render(parent,res);
  const t=text(parent);
  assert.equal(all(parent).filter(n=>n.tagName==='article').length,1);
  for(const word of ['sample','1위','10회','거래일 누락·불일치','연구 점수','상승 지속형','20261125'])assert.ok(t.includes(word),word);
  assert.ok(!t.includes('미확인'));
});
