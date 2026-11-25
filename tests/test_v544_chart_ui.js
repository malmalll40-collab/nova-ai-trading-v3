/* Node VM regression harness for the actual canvas controller; no network/DOM browser. */
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/candles.js','utf8').replace('window.NOVACandles={draw,render,valid,selectPeriod};',`window.NOVACandles={draw,render,valid,selectPeriod,applySnapshot,setTestState:(d,t)=>{current=d;timeframe=t;},getTestState:()=>current};`);
const context={window:{},document:{getElementById:()=>null},requestAnimationFrame:()=>{},console};
vm.runInNewContext(source,context);
const api=context.window.NOVACandles;
const chart=()=>({chart:{dates:['2026-11-23','2026-11-24'],open:[100,101],high:[105,106],low:[99,100],close:[103,104],volume:[100,200],partial:[false,false],ema20:[100,101],ema50:[99,100]}});
const bar={open:105,high:110,low:103,close:109,volume:300};
const snapshot=(date,b=bar)=>({available:true,date,bar:b});
let d=chart();api.setTestState(d,'1d');
assert.equal(api.applySnapshot(snapshot('2026-11-24')),false); // verified daily must never be overwritten
assert.equal(api.applySnapshot(snapshot('2026-11-25')),true); // append
assert.equal(d.chart.dates.length,3);assert.equal(d.chart.partial[2],true);assert.equal(d.chart.close[2],109);
assert.equal(api.applySnapshot(snapshot('2026-11-25',{...bar,close:108,volume:400})),true);
assert.equal(d.chart.dates.length,3);assert.equal(d.chart.close[2],108);
assert.equal(api.applySnapshot(snapshot('2026-11-25',{...bar,volume:200})),false); // old volume
assert.equal(api.applySnapshot(snapshot('2026-11-26',{...bar,volume:0})),false);
assert.equal(api.applySnapshot(snapshot('2026-11-23')),false); // stale date
for(const t of ['1w','1mo','1y']){d=chart();api.setTestState(d,t);assert.equal(api.applySnapshot(snapshot('2026-11-24')),true);assert.equal(d.chart.dates.length,2);assert.equal(d.chart.partial[1],true);}
console.log('10+ candle controller assertions passed');
