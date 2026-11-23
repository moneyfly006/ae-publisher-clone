"""Use the existing Playwright CLI session to measure every admin page and capture review images."""
from pathlib import Path
import json,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
paths=['/admin','/admin/customers','/admin/licenses','/admin/licenses/new','/admin/licenses/1','/admin/licenses/1/edit',
 '/admin/shop-users','/admin/shop-users/1','/admin/shop-licenses','/admin/shop-packages','/admin/shop-packages?edit=1',
 '/admin/shop-orders','/admin/logs','/admin/logs/3','/admin/events','/admin/customer-logs?user_id=1',
 '/admin/customer-devices','/admin/userdata','/admin/userdata/OLD-ACTIVE','/admin/rules',
 '/admin/shop-tickets','/admin/shop-tickets/1','/admin/shop-coupons','/admin/shop-invites',
 '/admin/shop-reports','/admin/settings','/admin/shop-pay','/admin/security','/admin/admins','/admin/audit','/admin/platforms','/admin/legacy-dashboard']
paths += ['/admin/settings?section='+section for section in ['license','logs','rules','payment','mail','portal','access','password','security','platforms']]
sample=json.loads((ROOT/'build/admin-website/customer-log-browser-fixture.json').read_text())
customer_detail='/admin/customer-logs/1/'+str(sample['log_id'])
paths += ['/admin/customer-logs','/admin/customer-logs?user_q=one%40example.invalid','/admin/customer-logs?user_id=1&q=fixture-search-'+sample['case'],customer_detail]
capture={'/admin':'dashboard','/admin/licenses/new':'license-form','/admin/shop-users/1':'customer-detail',
 '/admin/shop-packages?edit=1':'package-editor','/admin/shop-pay':'payment-settings','/admin/shop-coupons':'coupons','/admin/events':'events'}
capture.update({'/admin/settings?section='+section:'settings-'+section for section in ['license','logs','payment','mail','portal','access','password','security','platforms']})
capture.update({'/admin/customer-logs':'log-user-search','/admin/customer-logs?user_id=1&q=fixture-search-'+sample['case']:'log-user-list',customer_detail:'log-diagnosis'})
script='''async (page) => {
const paths=PATHS,capture=CAPTURE,errors=[],rows=[];
page.on('pageerror',e=>errors.push(e.message));
for(const width of [1440,390]){
 await page.setViewportSize({width,height:1000});
 for(const path of paths){
  const before=errors.length,response=await page.goto('http://127.0.0.1:18764'+path);
  await page.waitForTimeout(45);
  const data=await page.evaluate(()=>{
   const visible=e=>e.getBoundingClientRect().width>0 && e.getBoundingClientRect().height>0;
   const controls=[...document.querySelectorAll('main input:not([type=hidden]):not([type=checkbox]):not([type=radio]),main select')].filter(visible);
   const badControls=controls.filter(e=>Math.abs(e.getBoundingClientRect().height-40)>1).map(e=>({name:e.name,height:e.getBoundingClientRect().height}));
   const textareas=[...document.querySelectorAll('main textarea')].filter(visible);
   const badTextareas=textareas.filter(e=>Math.abs(e.getBoundingClientRect().height-112)>1).map(e=>({name:e.name,height:e.getBoundingClientRect().height}));
   const nestedFrames=[...document.querySelectorAll('.card .card,.card .filter-bar,.card .toolbar,.card .right-card,.card .metric-card')].filter(e=>{
    const s=getComputedStyle(e);return visible(e)&&parseFloat(s.borderTopWidth)>0&&parseFloat(s.borderBottomWidth)>0&&parseFloat(s.borderLeftWidth)>0&&parseFloat(s.borderRightWidth)>0;
   }).map(e=>e.className);
   return {viewport:innerWidth,document:document.documentElement.scrollWidth,controls:controls.length,badControls,textareas:textareas.length,badTextareas,nestedFrames,stylesheets:[...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new URL(e.href).pathname)};
  });
  rows.push({path,width,status:response.status(),heading:await page.getByRole('heading',{level:1}).innerText(),errors:errors.slice(before),...data});
  if(capture[path])await page.screenshot({path:'output/playwright/admin-website/'+capture[path]+'-'+width+'.png',fullPage:true});
 }
}
return rows;
}'''.replace('PATHS',json.dumps(paths)).replace('CAPTURE',json.dumps(capture))
r=subprocess.run(['bash','/Users/apple/.codex/skills/playwright/scripts/playwright_cli.sh','-s=license-v2-admin','run-code',script],cwd=ROOT,capture_output=True,text=True,timeout=240)
folder=ROOT/'build/admin-website';folder.mkdir(parents=True,exist_ok=True)
(folder/'browser-pages.log').write_text(r.stdout+r.stderr)
if r.returncode:print(r.stdout+r.stderr);raise SystemExit(r.returncode)
match=r.stdout.split('### Result\n',1)
if len(match)!=2:raise RuntimeError('Browser tool did not return page measurements')
rows=json.loads(match[1].split('\n### ',1)[0].strip())
failures=[row for row in rows if row['status']!=200 or row['errors'] or row['document']>row['viewport']+1 or row['badControls'] or row['badTextareas'] or row['nestedFrames'] or row['stylesheets']!=['/assets/admin.css']]
result=dict(pages=len(paths),viewports=[1440,390],checks=len(rows),controls=sum(r['controls'] for r in rows),textarea_checks=sum(r['textareas'] for r in rows),failures=failures,rows=rows)
(folder/'browser-pages.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
if failures:raise SystemExit(1)
