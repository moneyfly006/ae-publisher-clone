"""Customer search -> filtered logs -> diagnosis -> preserved return context, isolated fixtures."""
from pathlib import Path
import datetime,html,json,re,sqlite3,uuid
from urllib.parse import urlparse,parse_qs
import requests
ROOT=Path(__file__).resolve().parents[2];fixture=Path('/private/tmp/aurora-license-v2-fixture');base='http://127.0.0.1:18764'
checks=[]
def check(ok,label):
    if not ok:raise AssertionError(label)
    checks.append(label)
s=requests.Session();page=s.get(base+'/admin/login',timeout=10)
csrf=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
s.post(base+'/admin/login',data=dict(_csrf=csrf,username='fixture-admin',password='fixture-pass-only'),timeout=10)
case=uuid.uuid4().hex[:10];identity='LOG-CHECK-'+case;other='LOG-OTHER-'+case;now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
with sqlite3.connect(fixture/'shop.db') as db:
    for i in range(25):db.execute('INSERT INTO users(email,pass,name,created_at,verified) VALUES(?,?,?,?,1)',(f'{case}-{i}@example.invalid','unused','检索测试-'+case,0))
    db.execute('INSERT INTO lc_links(identity,user_id,created_at,actor) VALUES(?,1,0,?)',(identity,'isolated-test'))
    db.execute('INSERT INTO lc_links(identity,user_id,created_at,actor) VALUES(?,2,0,?)',(other,'isolated-test'))
with sqlite3.connect(fixture/'legacy.sqlite') as db:
    for i in range(21):db.execute('INSERT INTO app_logs(identity,filename,content,created_at,client_version,error_count,event_count) VALUES(?,?,?,?,?,1,2)',(identity,'diagnosis-'+case+'.log','[ERROR] Read timed out\n[ERROR] category permission denied\nCookie=private-fixture-secret\n<script>alert(1)</script>',now,'fixture-search-'+case))
    log_id=db.execute('SELECT MAX(id) FROM app_logs WHERE identity=?',[identity]).fetchone()[0]
    db.execute('INSERT INTO app_logs(identity,filename,content,created_at) VALUES(?,?,?,?)',(other,'other.log','private-other-customer-content',now))
try:
    response=s.get(base+'/admin/customer-logs',params=dict(user_q=case),timeout=10)
    check(response.status_code==200 and '共 25 位用户' in response.text and response.text.count('查看用户日志')==20,'user search is server paginated')
    response=s.get(base+'/admin/customer-logs',params=dict(user_q=case,user_page=999),timeout=10)
    check('第 2 / 2 页' in response.text and response.text.count('查看用户日志')==5,'user page clamps')
    for query,label in [('one@example.invalid','email'),('测试客户','name'),('1','user id')]:
        response=s.get(base+'/admin/customer-logs',params=dict(user_q=query),timeout=10)
        check(response.status_code==200 and 'one@example.invalid' in response.text,'search by '+label)
    response=s.get(base+'/admin/customer-logs',params=dict(user_q='no-match-'+case),timeout=10)
    check('没有匹配的套餐用户' in response.text,'empty user search explained')
    response=s.get(base+'/admin/customer-logs',params=dict(user_q="%_' OR 1=1 --"),timeout=10)
    check(response.status_code==200 and '没有匹配的套餐用户' in response.text,'user search treats SQL and wildcard characters literally')
    params=dict(user_id=1,user_q='one@example.invalid',q='fixture-search-'+case,err=1,page=2)
    response=s.get(base+'/admin/customer-logs',params=params,timeout=10)
    check(response.status_code==200 and '共 21 条' in response.text and '第 2 / 2 页' in response.text,'selected user log keyword/error/page filters')
    links=re.findall(r'href="([^"]+)"[^>]*>详情与诊断',response.text)
    check(len(links)==1,'detail button exists per log row')
    detail=html.unescape(links[0]);response=s.get(base+detail,timeout=10)
    check(response.status_code==200 and '目标类目或店铺发布资格不匹配' in response.text and '网络连接超时或远端服务不可用' in response.text,'detail shows multiple evidence-based diagnoses above log')
    check(response.text.index('LOG DIAGNOSIS')<response.text.index('id="raw-log"') and '解决步骤' in response.text and '验证方法' in response.text and '第 1 行' in response.text,'diagnosis appears first with evidence, steps and verification')
    check('private-fixture-secret' not in response.text and '<script>alert(1)</script>' not in response.text and '&lt;script&gt;' in response.text,'historic content redacted and HTML escaped on display')
    back=html.unescape(re.search(r'href="([^"]+)"[^>]*>返回用户日志',response.text).group(1));query=parse_qs(urlparse(back).query)
    check(query['user_id']==['1'] and query['page']==['2'] and query['q']==[params['q']] and query['err']==['1'] and query['user_q']==[params['user_q']],'return preserves user/search/error/page context')
    check(s.get(base+back,timeout=10).status_code==200,'return link opens list')
    check(s.get(base+f'/admin/customer-logs/2/{log_id}',timeout=10).status_code==404,'other user cannot open log by id')
    check(s.get(base+f'/admin/customer-logs/999999/{log_id}',timeout=10).status_code==404,'nonexistent user detail rejected')
    check(s.get(base+'/admin/customer-logs?user_id=999999',timeout=10).status_code==404,'nonexistent selected user rejected')
    check(s.get(base+'/admin/customer-logs?user_id=2&q=no-match',timeout=10).status_code==200,'empty user log selection has no exception')
    response=s.get(base+'/admin/customer-logs',params=dict(user_id=1,q='fixture-search-'+case,page=999),timeout=10)
    check('第 2 / 2 页' in response.text,'log page clamps')
    cookies=[dict(name=c.name,value=c.value,domain='127.0.0.1',path='/',expires=-1,httpOnly=True,secure=False,sameSite='Lax') for c in s.cookies]
    state=ROOT/'build/admin-website/admin-state.json';state.write_text(json.dumps(dict(cookies=cookies,origins=[])));state.chmod(0o600)
    # Preserve one synthetic sample after this test so the browser can verify click-through.
    (ROOT/'build/admin-website/customer-log-browser-fixture.json').write_text(json.dumps({'case':case,'log_id':log_id,'identity':identity}))
finally:
    with sqlite3.connect(fixture/'shop.db') as db:db.execute('DELETE FROM users WHERE email LIKE ?',[case+'-%@example.invalid'])
result=dict(passed=len(checks),checks=checks,environment='isolated local fixture')
(ROOT/'build/admin-website/customer-log-http.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
