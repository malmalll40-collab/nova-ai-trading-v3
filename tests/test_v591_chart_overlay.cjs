const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function harness(){
  const original=fs.readFileSync('static/candles.js','utf8');
  const src=original.replace('window.NOVACandles={draw,render,valid,selectPeriod};',
    'window.NOVACandles={draw,render,valid,selectPeriod,fixture:(b,c,t,r)=>{base=b;current=c;timeframe=t;researchLookup=new Map(r.map(x=>[x.date,x]));},matchedResearch};');
  const window={};
  vm.runInNewContext(src,{window,localStorage:{getItem(){return null},setItem(){}}});
  const dates=['2026-11-24','2026-11-25'];
  const chart={dates,open:[100,102],high:[105,106],low:[99,101],close:[102,105],volume:[1000,1500]};
  const rows=[{date:dates[0],completed:true,price_close:102,baseline:'BUY'},{date:dates[1],completed:true,price_close:105,baseline:'SELL'}];
  const base={mode:'스윙',chart,candle_research:{status:'READY',observations:rows}};
  const x=window.NOVACandles;
  return {x,chart,rows,base,apply:(t='1d',c=chart,b=base)=>x.fixture(b,{chart:c},t,rows)};
}
test('historical BUY and SELL renderable on exact daily and analysis bars',()=>{
  const h=harness();
  for(const t of ['1d','analysis']){h.apply(t);assert.equal(h.x.matchedResearch(h.chart,0).baseline,'BUY');assert.equal(h.x.matchedResearch(h.chart,1).baseline,'SELL');}
});
test('no markers on mismatched prices or volume, partial/display-only, weekly, or intraday',()=>{
  const h=harness();
  for(const c of [{...h.chart,close:[101,105]},{...h.chart,volume:[999,1500]},{...h.chart,partial:[true,false]},{...h.chart,display_only:[true,false]}]){h.apply('1d',c);assert.equal(h.x.matchedResearch(c,0),null);}
  for(const t of ['1w','1mo','1y']){h.apply(t);assert.equal(h.x.matchedResearch(h.chart,0),null);}
  h.apply('analysis',h.chart,{...h.base,mode:'5분'});assert.equal(h.x.matchedResearch(h.chart,0),null);
});
test('main chart includes synchronized toggle, analysis shortcut and versioned asset',()=>{
  const html=fs.readFileSync('static/index.html','utf8');
  const js=fs.readFileSync('static/candles.js','utf8');
  assert.ok(html.includes('id="candleChartOverlay" checked'));
  assert.ok(html.includes('id="candleChartExactSignals"'));
  assert.ok(html.includes('/static/candles.js?v=__NOVA_APP_VERSION__'));
  assert.ok(js.includes('const r=matchedResearch(x,i);if(!r)continue;'));
});
