import json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('/mnt/data/nova50_audit')
html=(root/'static/index.html').read_text()
css=(root/'static/styles.css').read_text()
html=re.sub(r'<link[^>]+href="/static/styles.css"[^>]*>',lambda m:'<style>'+css+'</style>',html)
def metric(ret,trades=7):return {"status":"READY","completed_trades":trades,"portfolio_return_pct":ret,"portfolio_mdd_pct":-3.2,"win_rate_pct":53,"buy_opportunities":12,"cost_bps_per_side":15}
research={"status":"READY","source":"kis","version":"5.0.0","validation":{"start":"2026-09-03","end":"2026-11-23","baseline":metric(0.5,4),"five_core":metric(1.0,3),"ai_model":{"status":"READY","train_samples":151,"validation_samples":50,"brier_score":0.248,"naive_train_rate_brier":0.241,"score_range":[0.35,0.68],"research_strategy":metric(0.9,3)}},"role_ablations":[{"role":f"Role {i+1}","return_difference_pp":(i-5)*0.02} for i in range(10)],"bias_warnings":["테스트용 가상 응답입니다"],"assumptions":"연구용 가상 데이터"}
fake_js='''window.__calls=[];window.fetch=async function(u,init){let v=String(u);window.__calls.push(v);let obj=v.includes('/api/research/v5')?RESEARCH:v.includes('/api/auth/status')?{authenticated:true,token_required:true}:v.includes('/api/realtime/kis/status')?{enabled:false,orders_enabled:false}:{status:'ok',results:[]};return {ok:true,status:200,json:async()=>obj,text:async()=>JSON.stringify(obj)};};'''.replace('RESEARCH',json.dumps(research,ensure_ascii=False))
html=html.replace('</head>','<script>'+fake_js+'</script></head>')
def embed(m):
 name=m.group(1)
 js=(root/'static'/name).read_text()
 return '<script>'+js.replace('</script','<\\/script')+'</script>'
html=re.sub(r'<script src="/static/([\w.]+)" defer></script>',embed,html)
print('html_kb',round(len(html)/1024), 'script_count',html.count('<script>'))
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for width in (320,390,768):
  page=browser.new_page(viewport={"width":width,"height":810},device_scale_factor=1)
  errs=[]
  page.on('pageerror',lambda e:errs.append(str(e)))
  page.set_content(html,wait_until='load',timeout=30000)
  page.wait_for_timeout(700)
  before=page.evaluate('({js: typeof window.NOVAV5, button: !!document.getElementById("v5ResearchRun"), nav: !!document.querySelector("[data-view=backtest]")})')
  page.locator('.bottom-nav [data-view="backtest"]').click()
  page.evaluate("window.NOVAV5.setAnalysis({symbol:'005930.KS',mode:'스윙',is_demo:false,reference:71000})")
  page.wait_for_timeout(100)
  research_btn=page.locator('#v5ResearchRun')
  disabled=research_btn.is_disabled()
  print("DBG",width,"before",before,"errors",errs[:3],"set",page.evaluate("({status:document.getElementById('v5ResearchStatus').textContent,disabled:document.getElementById('v5ResearchRun').disabled})"))
  if not disabled: research_btn.click()
  page.wait_for_timeout(250)
  visible=page.locator('#v5ResearchContent').is_visible()
  cards=page.locator('#v5MethodCards .v5-method-card').count()
  ablations=page.locator('#v5RoleAblation .v5-role-row').count()
  status=page.locator('#v5ResearchStatus').inner_text()
  docwidth=page.evaluate('document.documentElement.scrollWidth')
  api=page.evaluate('window.__calls.filter(c=>c.includes("/api/research/v5"))')
  page.locator('#v5MoreNav').click()
  more_open=page.locator('#v5MoreMenu').is_visible()
  page.locator('#v5MoreMenu [data-view="news"]').click()
  news_view=page.locator('#view-news').is_visible()
  print(json.dumps({'width':width,'load':before,'disabled':disabled,'research_api_calls':api,'research_visible':visible,'cards':cards,'ablation_rows':ablations,'status':status[:110],'scroll_width':docwidth,'more_open':more_open,'news_view':news_view,'errors':errs[:6]},ensure_ascii=False))
  if width==390:
   page.locator('.bottom-nav [data-view="backtest"]').click()
   page.screenshot(path='/mnt/data/NOVA_v5_local_ui_audit.png',full_page=False)
  page.close()
 browser.close()
