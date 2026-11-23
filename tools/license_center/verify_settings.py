"""Independent settings forms, validation and compatibility; synthetic local data only."""
from pathlib import Path
import json,re,sqlite3
import requests
ROOT=Path(__file__).resolve().parents[2]
fixture=Path('/private/tmp/aurora-license-v2-fixture')
base='http://127.0.0.1:18764'
checks=[]
def check(ok,label):
    if not ok:raise AssertionError(label)
    checks.append(label)
def settings(database):
    with sqlite3.connect(fixture/database) as db:
        return dict(db.execute('SELECT key,value FROM settings' if database=='legacy.sqlite' else 'SELECT k,v FROM settings'))
def license_rows(database):
    with sqlite3.connect(fixture/database) as db:
        return db.execute('SELECT * FROM licenses ORDER BY id').fetchall()
def restore(database,values):
    with sqlite3.connect(fixture/database) as db:
        db.execute('DELETE FROM settings')
        db.executemany('INSERT INTO settings VALUES(?,?)',list(values.items()))
s=requests.Session();login=s.get(base+'/admin/login',timeout=10)
csrf=re.search(r'name="_csrf" value="([^"]+)"',login.text).group(1)
s.post(base+'/admin/login',data=dict(username='fixture-admin',password='fixture-pass-only',_csrf=csrf),timeout=10)
def post(path,**fields):
    page=s.get(base+'/admin/settings',timeout=10)
    token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
    return s.post(base+path,data=dict(_csrf=token,**fields),timeout=10)
old=settings('legacy.sqlite');shop=settings('shop.db')
old_licenses=license_rows('legacy.sqlite');shop_licenses=license_rows('shop.db')
try:
    sections=['license','logs','rules','payment','mail','portal','access','password','security','platforms']
    for section in sections:
        page=s.get(base+'/admin/settings?section='+section,timeout=10)
        check(page.status_code==200 and '系统设置 · ' in page.text and 'settings-tabs' in page.text and not re.search(r'Fatal error|Warning:|Parse error',page.text),'settings section renders '+section)
        if section in ('payment','mail','portal'):
            forms=re.findall(r'<input[^>]+name="settings_group"[^>]+value="([^"]+)"',page.text)
            check(forms==[section],section+' has only its own save group')
            check(('name="smtp_host"' in page.text)==(section=='mail') and ('name="epay_gateway"' in page.text)==(section=='payment') and ('name="announcement"' in page.text)==(section=='portal'),section+' excludes other sections fields')
    post('/admin/settings',settings_group='access',api_key='fixture-key-preserve')
    l=settings('legacy.sqlite');a=settings('shop.db')
    response=post('/admin/settings',settings_group='license',default_days='55',api_key='unexpected',api_key_enabled='1',log_keep_days='1')
    n=settings('legacy.sqlite')
    check(n==dict(l,default_days='55') and settings('shop.db')==a,'license save ignores unrelated fields and preserves API key/log/mail')
    check(response.url.endswith('?section=license'),'license save returns to its section')
    l=n.copy()
    response=post('/admin/settings',settings_group='logs',log_keep_days='7',log_rules='{bad json')
    check(settings('legacy.sqlite')==l and '未保存' in response.text,'invalid log rules reject entire submission')
    with sqlite3.connect(fixture/'legacy.sqlite') as db:log_count=db.execute('SELECT COUNT(*) FROM app_logs').fetchone()[0]
    post('/admin/settings',settings_group='logs',log_keep_days='0',log_rules='{"error":["fixture-error"]}')
    with sqlite3.connect(fixture/'legacy.sqlite') as db:after=db.execute('SELECT COUNT(*) FROM app_logs').fetchone()[0]
    check(log_count==after and settings('legacy.sqlite')['log_keep_days']=='0','saving log retention does not delete existing logs')
    post('/admin/settings',settings_group='logs',log_rules='',log_keep_days='30')
    check(settings('legacy.sqlite')['log_rules']=='','blank rules restore built-in rules')
    post('/admin/settings',settings_group='access',api_key='',api_key_enabled='1')
    check(settings('legacy.sqlite')['api_key']=='fixture-key-preserve' and settings('legacy.sqlite')['api_key_enabled']=='1','blank API key preserves existing key')
    post('/admin/settings',settings_group='access',api_key='')
    check(settings('legacy.sqlite')['api_key_enabled']=='0','unchecked API switch persists disabled')
    before=settings('shop.db');legacy=settings('legacy.sqlite')
    post('/admin/shop-pay',settings_group='payment',epay_gateway='https://pay.example.invalid',epay_pid='fixture-pid',epay_key='fixture-secret',epay_type='alipay',allow_mock_pay='1',smtp_host='unexpected')
    p=settings('shop.db')
    check(p.get('allow_mock_pay')=='1' and p.get('smtp_host')==before.get('smtp_host') and settings('legacy.sqlite')==legacy,'payment save preserves mail and old authorization settings')
    response=post('/admin/shop-pay',settings_group='payment',epay_type='wxpay')
    p=settings('shop.db');check(p['allow_mock_pay']=='0' and p['epay_pid']=='fixture-pid' and response.url.endswith('?section=payment'),'unchecked mock payment persists off without losing other fields')
    response=post('/admin/shop-pay',settings_group='mail',smtp_host='smtp.example.invalid',smtp_port='70000',smtp_secure='ssl')
    check(settings('shop.db')==p and '未保存' in response.text,'invalid mail port rejects full submission')
    post('/admin/shop-pay',settings_group='mail',smtp_host='smtp.example.invalid',smtp_port='465',smtp_secure='ssl',smtp_from='one@example.invalid',require_verify='1',debug_codes='1')
    m=settings('shop.db');check(m['require_verify']=='1' and m['debug_codes']=='1' and m['epay_pid']=='fixture-pid','mail saves registration switches without changing payment')
    response=post('/admin/shop-pay',settings_group='mail',smtp_port='587',smtp_secure='tls')
    m=settings('shop.db');check(m['require_verify']=='0' and m['debug_codes']=='0' and m['smtp_host']=='smtp.example.invalid' and response.url.endswith('?section=mail'),'unchecked mail switches persist off')
    post('/admin/shop-pay',settings_group='portal',site_name='Fixture site',announcement='Fixture announcement',epay_key='unexpected')
    check(settings('shop.db')==dict(m,site_name='Fixture site',announcement='Fixture announcement'),'portal save cannot change payment fields')
    response=post('/admin/shop-pay',settings_group='payment',epay_gateway='javascript:alert(1)')
    check('未保存' in response.text and settings('shop.db')['epay_gateway']=='https://pay.example.invalid','invalid gateway scheme rejected')
    check(post('/admin/settings',settings_group='unknown').status_code==400 and post('/admin/shop-pay',settings_group='unknown').status_code==400,'unknown save groups rejected')
    check(s.get(base+'/admin/settings?section=unknown',timeout=10).status_code==200,'unknown section falls back without exception')
    aliases={'shop-pay':'支付网关','security':'两步验证','rules':'日志识别效果','platforms':'平台与接口'}
    for alias,title in aliases.items():check('系统设置 · '+title in s.get(base+'/admin/'+alias,timeout=10).text,'old settings URL preserved '+alias)
    check(license_rows('legacy.sqlite')==old_licenses and license_rows('shop.db')==shop_licenses,'all existing old and package licenses unchanged')
finally:
    restore('legacy.sqlite',old);restore('shop.db',shop)
result={'passed':len(checks),'checks':checks,'fixture_settings_restored':True}
(ROOT/'build/admin-website/settings-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
