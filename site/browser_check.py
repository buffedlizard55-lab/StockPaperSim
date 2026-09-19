from playwright.sync_api import sync_playwright
import json
import os
from pathlib import Path

# Optional verification tooling; not imported by the dependency-free simulator.
# Install Playwright in an isolated environment and supply CHROMIUM_EXECUTABLE
# if the normal Playwright browser installation is unavailable.
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
Path("scratch").mkdir(exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--single-process','--no-zygote'])
 page=b.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(BASE+'/');page.wait_for_url('**/docs/desk/index.html')
 page.locator('.strategy').last.wait_for()
 assert page.locator('.strategy:visible').count()==51
 page.screenshot(path='scratch/desk-desktop.png',full_page=False)
 page.locator('#search').fill('fda');assert page.locator('.strategy:visible').count()==5
 page.locator('#status').select_option('PROTOTYPE_BLOCKED_DATA');assert page.locator('.strategy:visible').count()==0
 assert page.locator('#empty').is_visible()
 page.locator('#reset').click();assert page.locator('.strategy:visible').count()==51
 page.locator('#status').select_option('PROTOTYPE_BLOCKED_DATA');assert page.locator('.strategy:visible').count()==18
 page.locator('#reset').click()
 page.locator('a[href="#orders"]').first.click();assert page.locator('#orders').is_visible()
 assert page.request.get(BASE+'/docs/desk/trade-audit.csv').status==200
 assert page.request.get(BASE+'/docs/desk/snapshot.json').status==200
 page.set_viewport_size({'width':390,'height':844});page.goto(BASE+'/docs/desk/index.html')
 page.screenshot(path='scratch/desk-mobile.png',full_page=False)
 assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile viewport overflow'
 assert not errors,errors
 print(json.dumps({'desktop': '1440x1000', 'mobile':'390x844','strategies':51,'prototypes':18,'console_errors':errors,'search_filters_reset_downloads_mobile_overflow':'PASS'}))
 b.close()
