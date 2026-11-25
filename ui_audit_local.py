import http.server, socketserver, threading, json
from playwright.sync_api import sync_playwright
from pathlib import Path

ROOT=Path('/mnt/data/nova50_audit')
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,*args):pass
socketserver.TCPServer.allow_reuse_address=True
srv=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler)
threading.Thread(target=srv.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{srv.server_address[1]}/static/index.html'
def metric(ret=1.1,win=54,trades=7):return {"status":"READY","completed_trades":trades,"portfolio_return_pct":ret,"portfolio_mdd_pct":-3.2,"win_rate_pct":win,"buy_opportunities":12,"cost_bps_per_side":15}
research={"status":"READY","source":"kis","version":"5.0.0","validation":{"start":"2026-09-03","end":"2026-11-23","baseline":metric(0.5,50,4),"five_core":metric(1.0,55,3),"ai_model":{"status":"READY","train_samples":151,"validation_samples":50,"brier_score":0.248,"naive_train_rate_brier":0.241,"score_range":[0.35,0.68],"research_strategy":metric(0.9,51,3)}},"role_ablations":[{"role":f"Role {i+1}","return_difference_pp":(i-5)*0.02} for i in range(10)],"bias_warnings":["테스트용 가상 응답입니다"],"assumptions":"연구용 가상 가격과 데이터"}

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in (320,390,768):
        page=browser.new_page(viewport={"width":width,"height":810},device_scale_factor=1)
        errs=[];calls=[]
        page.on('pageerror',lambda error:errs.append(str(error)))
        def handler(route):
            path=route.request.url
            calls.append(path.split('/api/')[-1][:90])
            if '/api/research/v5' in path:r=research
            elif '/api/auth/status' in path:r={'authenticated':True,'configured':True}
            elif '/api/realtime/kis/status' in path:r={'enabled':False,'orders_enabled':False}
            else:r={'status':'ok','results':[]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(r,ensure_ascii=False))
        page.route('**/api/**',handler)
        page.goto(url,wait_until='domcontentloaded')
        page.wait_for_timeout(550)
        # Browser simulation: set analyzed stock manually (no live brokerage calls)
        page.evaluate("window.NOVAV5.setAnalysis({symbol:'005930.KS',mode:'스윙',is_demo:false,reference:71000})")
        page.locator('.bottom-nav [data-view="backtest"]').click()
        page.wait_for_timeout(150)
        research_btn=page.locator('#v5ResearchRun')
        disabled=research_btn.is_disabled()
        research_btn.click()
        page.wait_for_timeout(500)
        visible=page.locator('#v5ResearchContent').is_visible()
        cards=page.locator('#v5MethodCards .v5-method-card').count()
        ablations=page.locator('#v5RoleAblation .v5-role-row').count()
        status=page.locator('#v5ResearchStatus').inner_text()
        bodywidth=page.evaluate('document.documentElement.scrollWidth')
        page.locator('#v5MoreNav').click()
        more_open=page.locator('#v5MoreMenu').is_visible()
        page.locator('#v5MoreMenu [data-view="news"]').click()
        news_view=page.locator('#view-news').is_visible()
        print(json.dumps({'width':width,'research_button_disabled':disabled,'research_api_called':any(c.startswith('research/v5?') for c in calls),'research_visible':visible,'cards':cards,'role_ablation_rows':ablations,'status':status[:110],'scroll_width':bodywidth,'more_open':more_open,'news_view':news_view,'runtime_errors':errs[:6]},ensure_ascii=False))
        if width==390:
            page.locator('.bottom-nav [data-view="backtest"]').click()
            page.screenshot(path='/mnt/data/NOVA_v5_local_ui_audit.png',full_page=False)
        page.close()
    browser.close()
srv.shutdown()
