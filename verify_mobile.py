from playwright.sync_api import sync_playwright
from pathlib import Path
import json
p=Path(__file__).resolve().parent
with sync_playwright() as x:
 browser=x.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content((p/'NOVA_v3_모바일_체험판.html').read_text(encoding='utf-8'),wait_until='load')
 page.wait_for_timeout(200)
 page.locator('[data-view="guide"]').click()
 page.wait_for_timeout(100)
 page.screenshot(path=str(p/'NOVA_v3_8_1_mobile_preview.png'),full_page=True)
 print(json.dumps({'title':page.title(),'guide_visible':page.locator('#view-guide').is_visible(),
  'nav_items':page.locator('.nav-item').count(), 'body_scroll_width':page.evaluate('document.body.scrollWidth'),
  'viewport_width':page.evaluate('window.innerWidth'),'errors':errors},ensure_ascii=False))
 browser.close()
