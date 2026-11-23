"""Website-only acceptance against local synthetic accounts and original device fixtures."""
from pathlib import Path
import datetime,hashlib,json,re,sqlite3,subprocess,sys,uuid
import requests

ROOT=Path(__file__).resolve().parents[2]
fixture=Path('/private/tmp/aurora-license-v2-fixture')
base='http://127.0.0.1:18764'
results=[]
def check(ok,label):
    if not ok:raise AssertionError(label)
    results.append(label)
def static_licenses(path):
    with sqlite3.connect(path) as db:
        cols=[r[1] for r in db.execute('PRAGMA table_info(licenses)') if r[1] not in ('last_verified_at','verify_count')]
        return db.execute('SELECT '+','.join(cols)+' FROM licenses ORDER BY id').fetchall()
before=static_licenses(fixture/'legacy.sqlite')
s=requests.Session();login=s.get(base+'/admin/login',timeout=10)
csrf=re.search(r'name="_csrf" value="([^"]+)"',login.text).group(1)
r=s.post(base+'/admin/login',data=dict(username='fixture-admin',password='fixture-pass-only',_csrf=csrf),timeout=10)
check(r.status_code==200 and '客户运行与问题分析' in r.text,'new diagnostic dashboard login')
# Upload through the unmodified legacy API URL and existing client fields.
now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
case=uuid.uuid4().hex[:12]
message=f'{now} [ERROR] [amazon] Read timed out product 123456 case {case}\n{now} [INFO] [alibaba] 保存草稿 product 234567 case {case}'
payload=dict(identity='OLD-ACTIVE',filename='app.log',client_version='1.0.6-fixture',content=message)
first=requests.post(base+'/api/v1/log/upload',json=payload,timeout=10).json()
check(first.get('ok') is True,'existing software log upload accepted')
again=requests.post(base+'/api/v1/log/upload',json=payload,timeout=10).json()
check(again.get('duplicate') is True and first['id']==again['id'],'exact upload retry returns same log id')
changed=dict(payload,content=message+f'\n{now} [ERROR] [amazon] category permission denied product 345678')
third=requests.post(base+'/api/v1/log/upload',json=changed,timeout=10).json();check(third.get('ok') is True,'overlapping log tail accepted')
with sqlite3.connect(fixture/'legacy.sqlite') as db:
    count=db.execute("SELECT COUNT(*) FROM lc_diagnostic_events WHERE identity='OLD-ACTIVE' AND message LIKE ?",['%Read timed out product 123456 case '+case+'%']).fetchone()[0]
    check(count==1,'overlapping timestamped line counted once')
    issue=db.execute("SELECT fingerprint FROM lc_diagnostic_events WHERE identity='OLD-ACTIVE' AND error_type='network' ORDER BY id DESC LIMIT 1").fetchone()[0]
# Reserved v2 events authenticate with the existing signed ECDSA credential.
credential=requests.post(base+'/api/v1/license/verify',json=dict(identity='OLD-ACTIVE',client_type='local'),timeout=10).json()
v2=dict(voucher=credential['voucher'],signature=credential['signature'],events=[dict(event_id='fixture-event-'+case,platform='future_market',version='1.0.7-fixture',run_id='task-fixture-1',stage='submit',outcome='reviewing',message='waiting for platform review',occurred_at=datetime.datetime.now(datetime.timezone.utc).isoformat())])
a=requests.post(base+'/api/v2/client/events',json=v2,timeout=10);check(a.status_code==200 and a.json()['inserted']==1,'v2 signed event accepted for future platform id')
b=requests.post(base+'/api/v2/client/events',json=v2,timeout=10).json();check(b['inserted']==0 and b['duplicates']==1,'v2 retry deduplicated by device and event id')
bad=dict(v2,signature='invalid');response=requests.post(base+'/api/v2/client/events',json=bad,timeout=10);check(response.status_code==401,'v2 rejects invalid device credential')
bad=dict(v2,events=v2['events']+[dict(event_id='bad',platform='amazon',outcome='published')]);response=requests.post(base+'/api/v2/client/events',json=bad,timeout=10);check(response.status_code==400,'invalid event batch rejected')
# Add a safe synthetic ticket so every old detail route can be opened.
with sqlite3.connect(fixture/'shop.db') as db:
    db.execute("INSERT OR IGNORE INTO tickets(id,user_id,subject,category,status,created_at,updated_at) VALUES(1,1,'fixture issue','license','open',0,0)")
paths=['/admin','/admin/legacy-dashboard','/admin/customers','/admin/customers?scope=legacy&q=OLD','/admin/customers?scope=accounts',
 '/admin/events','/admin/events?platform=amazon&version=1.0.6-fixture','/admin/events?q=no-such-result&page=999','/admin/issues/'+issue,
 '/admin/platforms','/admin/licenses','/admin/licenses/new','/admin/licenses/1','/admin/licenses/1/edit','/admin/audit','/admin/settings',
 '/admin/publickey','/admin/logs','/admin/logs/'+str(first['id']),'/admin/userdata','/admin/userdata/OLD-ACTIVE',
 '/admin/admins','/admin/rules','/admin/shop-packages','/admin/shop-pay','/admin/shop-orders','/admin/shop-tickets',
 '/admin/shop-tickets/1','/admin/security','/admin/shop-reports','/admin/shop-export?type=orders','/admin/shop-invites','/admin/shop-users',
 '/admin/shop-users/1','/admin/shop-licenses','/admin/customer-logs?user_id=1','/admin/customer-devices','/admin/shop-coupons']
for path in paths:
    r=s.get(base+path,timeout=15)
    check(r.status_code==200 and not re.search(r'(Fatal error|Warning:|Parse error|Uncaught|view not found)',r.text),'page '+path)
page=s.get(base+'/admin/issues/'+issue,timeout=10).text
csrf=re.search(r'name="_csrf" value="([^"]+)"',page).group(1)
r=s.post(base+'/admin/issues/'+issue+'/update',data=dict(_csrf=csrf,status='investigating',note='fixture reproduction; api_key=secret-fixture-do-not-store'),timeout=10)
check(r.status_code==200 and '排查状态已保存' in r.text,'issue triage state and note saved')
with sqlite3.connect(fixture/'legacy.sqlite') as db:
    note=db.execute('SELECT note FROM lc_issue_notes WHERE fingerprint=?',(issue,)).fetchone()[0]
    check('secret-fixture-do-not-store' not in note,'issue note sensitive value redacted')
platforms=requests.get(base+'/api/v2/platforms',timeout=10).json()
check(platforms['schema_version']==2 and len(platforms['platforms'])==5,'dynamic platform registry capability endpoint')
package_page=s.get(base+'/admin/shop-packages',timeout=10).text
csrf=re.search(r'name="_csrf" value="([^"]+)"',package_page).group(1)
package_data=dict(_csrf=csrf,name='isolated-'+case,price='12.34',days=37,device_limit=3,platforms='amazon',features='listing,ai_text',active=1)
created_package=s.post(base+'/admin/shop-packages',data=package_data,timeout=10)
with sqlite3.connect(fixture/'shop.db') as account_db:
    package=account_db.execute('SELECT id,price_cents,days,device_limit FROM packages WHERE name=?',[package_data['name']]).fetchone()
check(created_package.status_code==200 and package and package[1:]==(1234,37,3),'redesigned package create form persists fields')
package_data.update(id=package[0],price='21.00',active=0)
edited_package=s.post(base+'/admin/shop-packages',data=package_data,timeout=10)
with sqlite3.connect(fixture/'shop.db') as account_db:
    updated=account_db.execute('SELECT price_cents,active FROM packages WHERE id=?',[package[0]]).fetchone()
check(edited_package.status_code==200 and updated==(2100,0),'separate package editor preserves save and disable')
deleted_package=s.post(base+'/admin/shop-packages',data=dict(_csrf=csrf,delete_id=package[0]),timeout=10)
with sqlite3.connect(fixture/'shop.db') as account_db:
    check(deleted_package.status_code==200 and account_db.execute('SELECT COUNT(*) FROM packages WHERE id=?',[package[0]]).fetchone()[0]==0,'package delete operation retained')
invalid=s.post(base+'/admin/shop-packages',data=dict(package_data,days=-1,id=0),timeout=10)
check('请填写有效的套餐名称' in invalid.text,'package backend rejects invalid settings')
# Exercise original authorization-code operations on a temporary fixture only.
form=s.get(base+'/admin/licenses/new',timeout=10).text
csrf=re.search(r'name="_csrf" value="([^"]+)"',form).group(1)
identity='CRUD-'+case.upper()
data=dict(_csrf=csrf,identity=identity,client_type='local',plan='fixture-plan',expires_at='2090-01-01',status='active',note='isolated CRUD verification')
created=s.post(base+'/admin/licenses',data=data,timeout=10)
check(created.status_code==200 and re.search(r'/admin/licenses/\d+$',created.url),'original code create operation')
lid=int(created.url.rsplit('/',1)[1])
renewed=s.post(base+f'/admin/licenses/{lid}/renew',data=dict(_csrf=csrf,days=7),timeout=10)
check(renewed.status_code==200 and '2090-01-08' in renewed.text,'original code renew operation')
data.update(expires_at='2090-01-08',status='disabled')
edited=s.post(base+f'/admin/licenses/{lid}/update',data=data,timeout=10)
verify=requests.post(base+'/api/v1/license/verify',json=dict(identity=identity),timeout=10).json()
check(edited.status_code==200 and verify.get('authorized') is False,'original code edit and disable')
data['status']='active';s.post(base+f'/admin/licenses/{lid}/update',data=data,timeout=10)
verify=requests.post(base+'/api/v1/license/verify',json=dict(identity=identity),timeout=10).json()
check(verify.get('authorized') is True,'original code reactivation')
revoked=s.post(base+f'/admin/licenses/{lid}/revoke',data=dict(_csrf=csrf),timeout=10)
verify=requests.post(base+'/api/v1/license/verify',json=dict(identity=identity),timeout=10).json()
check(revoked.status_code==200 and verify.get('authorized') is False,'original code revoke operation')
deleted=s.post(base+f'/admin/licenses/{lid}/delete',data=dict(_csrf=csrf),timeout=10)
with sqlite3.connect(fixture/'legacy.sqlite') as db:
    check(deleted.status_code==200 and db.execute('SELECT COUNT(*) FROM licenses WHERE id=?',[lid]).fetchone()[0]==0,'original code delete operation')
check(before==static_licenses(fixture/'legacy.sqlite'),'all original device license fields retained')
with sqlite3.connect(fixture/'legacy.sqlite') as db:
    coverage=db.execute('SELECT COUNT(*) FROM lc_diagnostic_events').fetchone()[0]
    review=db.execute("SELECT outcome FROM lc_diagnostic_events WHERE platform='future_market'").fetchone()[0]
    check(review=='reviewing','reviewing event remains separate from published')
# Inspect source route preservation, not just pages with fixtures.
original=json.loads((ROOT/'build/admin-website/original-routes.json').read_text())
current=re.findall(r"\['(GET|POST)',\s*'([^']+)',\s*'([^']+)'\]",(ROOT/'deploy/license-center-v2/public/index.php').read_text())
signatures={(method,path) for method,path,_ in current}
check(all((x['method'],x['path']) in signatures for x in original if 'shop-refunds' not in x['path'] and 'shop-invoices' not in x['path']),'all original routes retained except explicitly removed refund and invoice routes')
for method,path in [('GET','/admin/shop-refunds'),('POST','/admin/shop-refunds'),('POST','/admin/shop-refunds/1/complete'),('GET','/admin/shop-invoices'),('POST','/admin/shop-invoices/1/issue'),('GET','/admin/shop-export?type=refunds')]:
    response=s.request(method,base+path,timeout=10)
    check(response.status_code==404,'removed route unavailable '+method+' '+path)
portal=requests.get(base+'/',timeout=10).text
check('p-invoice' not in portal and 'loadInvoices' not in portal,'invoice portal markup and handlers removed')
for action in ('invoices','invoice_request'):
    response=requests.post(base+'/api.php?action='+action,json={},timeout=10).json()
    check(response.get('code')!=0,'removed portal action unavailable '+action)
with sqlite3.connect(fixture/'shop.db') as account_db:
    tables={row[0] for row in account_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check('refunds' in tables and 'invoices' in tables,'pre-existing historical tables retained without active feature logic')
cookies=[dict(name=c.name,value=c.value,domain='127.0.0.1',path='/',expires=-1,httpOnly=True,secure=False,sameSite='Lax') for c in s.cookies]
state=ROOT/'build/admin-website/admin-state.json';state.write_text(json.dumps(dict(cookies=cookies,origins=[])));state.chmod(0o600)
result=dict(passed=len(results),checks=results,issue_fingerprint=issue,sample_log_id=first['id'],diagnostic_events=coverage,original_routes=len(original),current_routes=len(current))
(ROOT/'build/admin-website/http-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
