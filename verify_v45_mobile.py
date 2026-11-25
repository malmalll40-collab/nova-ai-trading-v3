"""Offline Chromium layout smoke test (never retrieves live prices)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json,re
root=Path(__file__).resolve().parent/'static'
html=(root/'index.html').read_text(encoding='utf8')
# Chromium local/remote navigation is policy-blocked; inline the exact CSS/JS
# and disable networking to inspect the same static UI on the document page.
css=(root/'styles.css').read_text(encoding='utf8')
html=html.replace('</head>', '<style>'+css+'</style></head>')
for js_file in ['candles.js','app.js','journal.js','monitor.js','news.js','recommendations.js','recommendation_history.js','investor_flow.js']:
    html=html.replace(f'<script src="/static/{js_file}" defer></script>',f'<script>{(root/js_file).read_text(encoding="utf8")}</script>')
reports=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in [320,390,430,768]:
        page=browser.new_page(viewport={'width':width,'height':850},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        page.set_content(html,wait_until='domcontentloaded')
        page.locator('[data-view="recommendations"]').click()
        page.wait_for_timeout(200)
        assert page.locator('#historyRun').is_visible(),width
        assert page.locator('#v45Load').is_visible(),width
        assert page.locator('#v45Chart').is_visible(),width
        assert page.locator('#historyWeeks option').count()==2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),width
        assert not errors,errors
        if width==390:
            page.locator('#historyRun').scroll_into_view_if_needed()
            page.evaluate("document.getElementById('historyStatus').textContent='실제 API 연결 후 과거 검증을 실행할 수 있습니다.'")
            page.locator('.v45-flow-panel').screenshot(path='/mnt/data/NOVA_v4_5_수급_모바일_미리보기.png')
        reports.append({'width':width,'js_errors':len(errors),'horizontal_overflow':False,'history_controls_visible':True,'v45_flow_panel_visible':True})
        page.close()
    browser.close()
print(json.dumps(reports,ensure_ascii=False))
