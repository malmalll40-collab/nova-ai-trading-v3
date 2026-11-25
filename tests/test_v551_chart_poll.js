'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/candles.js','utf8').replace('window.NOVACandles={draw,render,valid,selectPeriod};','window.NOVACandles={pollLive,setState:(d)=>{base=d;current=d;timeframe="1d";serial=1;}};');
async function check(status,expected){
 const calls=[];const ctx={window:{},document:{getElementById:()=>null,hidden:false},requestAnimationFrame:()=>{},console,AbortController,URLSearchParams,setTimeout:()=>1,clearTimeout:()=>{},fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url.startsWith('/api/market/session')?{status,is_open:status==='OPEN',calendar_verified:status!=='UNKNOWN',as_of:'2026-11-25T10:00:00+09:00'}:{available:true,date:'2026-11-25',bar:{open:105,high:110,low:103,close:109,volume:300},calendar_verified:false}};}};
 vm.runInNewContext(source,ctx);const d={symbol:'005930.KS',source:'kis',chart:{dates:['2026-11-23','2026-11-24'],open:[100,101],high:[105,106],low:[99,100],close:[103,104],volume:[100,200],partial:[false,false],ema20:[100,101],ema50:[99,100]}};
 const api=ctx.window.NOVACandles;api.setState(d);await api.pollLive(1);assert.equal(calls.some(u=>u.startsWith('/api/chart/live')),expected);assert.equal(d.chart.dates.length,expected?3:2);
}
(async()=>{await check('UNKNOWN',true);await check('CLOSED',false);await check('OPEN',true);console.log('UNKNOWN / CLOSED / OPEN polling: 3 checks passed');})().catch(e=>{console.error(e);process.exitCode=1;});
