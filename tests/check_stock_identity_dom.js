'use strict';
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(__dirname+'/../static/app.js','utf8');
function env(saved={}, fetchFn=null){
 const elements={};
 for(const id of ['selectedStockIdentity','selectedStockIdentityText','chartStockIdentity','symbolName','symbolInput'])
   elements[id]={textContent:'',hidden:false,value:''};
 const savedData={};for(const [k,v] of Object.entries(saved))savedData[k]=JSON.stringify(v);
 const ctx={
  window:{addEventListener:()=>{},NOVA_PREVIEW_DATA:null},
  localStorage:{getItem:k=>savedData[k]??null,setItem:(k,v)=>{savedData[k]=String(v)}},
  sessionStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
  document:{getElementById:id=>elements[id]||null},
  fetch:fetchFn||(()=>Promise.resolve({ok:true,json:async()=>({results:[]})})),
  console,URLSearchParams,setTimeout,clearTimeout,AbortController,
 };
 vm.createContext(ctx);vm.runInContext(source,ctx,{filename:'app.js'});
 return {ctx,elements,savedData,run:(v)=>vm.runInContext(v,ctx)};
}
async function settle(){await new Promise(r=>setTimeout(r,15));}
(async()=>{
 let x=env({'nova3-selected-stock':{name:'삼성전자',symbol:'005930.KS',market:'코스피'}});
 x.run("latest={symbol:'005930.KS'};updateStockIdentity('005930.KS',{lookup:false})");
 assert.equal(x.elements.selectedStockIdentityText.textContent,'삼성전자 · 005930');
 assert.equal(x.elements.chartStockIdentity.textContent,'삼성전자 · 005930');
 assert.equal(x.elements.symbolName.textContent,'삼성전자 · 005930');
 assert.equal(x.elements.selectedStockIdentity.hidden,false);
 assert.equal(JSON.parse(x.savedData['nova472-stock-names-v1'])['005930.KS'],'삼성전자');
 
 x=env({},()=>Promise.resolve({ok:true,json:async()=>({results:[{name:'타사',code:'000660',symbol:'000660.KQ'}, {name:'SK하이닉스',code:'000660',symbol:'000660.KS'}]})}));
 x.run("latest={symbol:'000660.KS'};updateStockIdentity('000660.KS')");
 assert.equal(x.elements.chartStockIdentity.textContent,'종목코드 000660');
 await settle();
 assert.equal(x.elements.chartStockIdentity.textContent,'SK하이닉스 · 000660');
 assert.equal(x.elements.symbolName.textContent,'SK하이닉스 · 000660');
 
 x=env({},()=>Promise.resolve({ok:true,json:async()=>({results:[{name:'같은 코드 다른 거래소',symbol:'123456.KQ',code:'123456'}]})}));
 x.run("latest={symbol:'123456.KS'};updateStockIdentity('123456.KS')");await settle();
 assert.equal(x.elements.selectedStockIdentityText.textContent,'종목코드 123456');
 
 let resolveFetch;
 x=env({},()=>new Promise(r=>{resolveFetch=r}));
 x.run("latest={symbol:'111111.KS'};updateStockIdentity('111111.KS')");
 x.run("latest={symbol:'222222.KS'};updateStockIdentity('222222.KS',{lookup:false})");
 resolveFetch({ok:true,json:async()=>({results:[{name:'오래된 종목',symbol:'111111.KS',code:'111111'}]})});
 await settle();
 assert.equal(x.elements.chartStockIdentity.textContent,'종목코드 222222');
 
 x=env();x.run("latest={symbol:'DEMO'};updateStockIdentity('DEMO',{lookup:false})");
 assert.equal(x.elements.chartStockIdentity.textContent,'DEMO · 가상 종목');
 console.log('STOCK IDENTITY UI TESTS: 5 PASS (selected, exact symbol search, mismatched exchange, stale response, demo)');
})().catch(e=>{console.error(e);process.exit(1)});
