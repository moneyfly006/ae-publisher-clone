"""Exercise the isolated local deployment with real HTTP, login, CSRF and signature checks."""
from pathlib import Path
import json,re,sys
import requests
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from aeclone.license import verify_voucher
from aeclone.licensing import verify_token
base='http://127.0.0.1:18764'
fixture=Path('/private/tmp/aurora-license-v2-fixture')
results=[]
def check(value,label):
    if not value: raise AssertionError(label)
    results.append(label)
s=requests.Session()
login=s.get(base+'/admin/login',timeout=10)
csrf=re.search(r'name="_csrf" value="([^"]+)"',login.text).group(1)
r=s.post(base+'/admin/login',data=dict(username='fixture-admin',password='fixture-pass-only',_csrf=csrf),timeout=10)
check(r.status_code==200 and '后台功能' in r.text,'administrator login and layout')
for path in ['/admin','/admin/shop-users','/admin/shop-users/1','/admin/customer-logs?user_id=1','/admin/customer-devices','/admin/licenses','/admin/userdata','/admin/logs','/admin/shop-licenses','/admin/shop-orders','/admin/shop-packages','/admin/security']:
    r=s.get(base+path,timeout=10)
    check(r.status_code==200 and 'Fatal error' not in r.text and 'Warning:' not in r.text, 'admin page '+path)
for identity,expected in [('OLD-ACTIVE',True),('OLD-REVOKED',False),('UNKNOWN-DEVICE',False),('AAAAAAAA-BBBBBBBB',True)]:
    r=requests.post(base+'/api/v1/license/verify',json=dict(identity=identity,client_type='local'),timeout=10)
    data=r.json();check(data.get('authorized') is expected,'verify '+identity)
    if expected:
        check(verify_voucher(data['voucher'],data['signature'],identity,public_key_pem=(fixture/'keys/public.pem').read_text()),'ECDSA voucher '+identity)
u=requests.Session()
r=u.post(base+'/api.php?action=login',json=dict(email='one@example.invalid',password='fixture-user-pass'),timeout=10).json();check(r.get('code')==0,'account login')
me=u.post(base+'/api.php?action=me',json={},timeout=10).json()['data']
check('alibaba' in me['license']['platforms'] and 'amazon' in me['license']['platforms'],'portal combined account rights')
for action in ('site','packages','devices','balance_info','status','orders','tickets'):
    response=u.post(base+'/api.php?action='+action,json={},timeout=10)
    value=response.json()
    check(value.get('code')==0,'portal API '+action)
logs=u.post(base+'/api.php?action=logs',json={},timeout=10).json()['data'];check(logs['total']==1,'portal own historical log')
lid=logs['rows'][0]['id']
row=u.post(base+'/api.php?action=log_detail',json=dict(id=lid),timeout=10).json();check(row['code']==0 and row['data']['content']=='safe fixture log','portal log detail')
v=requests.Session();v.post(base+'/api.php?action=login',json=dict(email='two@example.invalid',password='fixture-user-pass'),timeout=10)
row=v.post(base+'/api.php?action=log_detail',json=dict(id=lid),timeout=10).json();check(row['code']!=0,'other account direct log id denied')
raw=u.post(base+'/api.php?action=license',json={},timeout=10).json()['data']['token'];check(verify_token(raw) is not None,'PHP platform token accepted by Python client')
refresh=u.post(base+'/api/license/entitlements',json=dict(machine_code='AAAAAAAA-BBBBBBBB'),timeout=10).json()['data']['token'];check(verify_token(refresh) is not None,'machine entitlement refresh signature')
# Browser state is restricted to local fixtures, contains no production credentials.
for name,session in [('admin',s),('user',u)]:
    cookies=[dict(name=c.name,value=c.value,domain='127.0.0.1',path='/',expires=-1,httpOnly=True,secure=False,sameSite='Lax') for c in session.cookies]
    path=ROOT/'build/license-center-v2'/f'{name}-state.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(dict(cookies=cookies,origins=[])));path.chmod(0o600)
path=ROOT/'build/license-center-v2/local-http-results.json';path.write_text(json.dumps(dict(passed=len(results),checks=results),ensure_ascii=False,indent=2))
print(json.dumps(dict(passed=len(results),checks=results),ensure_ascii=False))
