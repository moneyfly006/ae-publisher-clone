"""Read-only production acceptance over the owner's existing SSH maintenance access."""
from pathlib import Path
import argparse,hashlib,json,subprocess
import requests
ROOT=Path(__file__).resolve().parents[2]
folder=ROOT/'build/admin-website'
parser=argparse.ArgumentParser();parser.add_argument('--backup',default='/www/backups/license-rebuild/deploy-20261122-233119');parser.add_argument('--css-version',default='6');args=parser.parse_args()
import re
if not re.fullmatch(r'/www/backups/license-rebuild/deploy-[0-9]{8}-[0-9]{6}',args.backup):raise ValueError('Unexpected backup path')
ssh=['ssh','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile=/private/tmp/aurora-vps-known-hosts','-o','ConnectTimeout=15','root@132.226.1.44']
def remote(command,outfile):
    result=subprocess.run(ssh+[command],capture_output=True,text=True,timeout=120)
    (folder/(outfile+'.stderr')).write_text(result.stderr)
    if result.returncode:raise RuntimeError('Production check failed: '+result.stderr+result.stdout)
    data=json.loads(result.stdout)
    (folder/outfile).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    return data
pages=remote('/www/server/php/74/bin/php /tmp/verify_admin_remote.php','production-page-renders.json')
assert not pages['failures']
licenses=remote('python3 /tmp/verify_production_remote.py','production-legacy-api.json')
assert licenses['all_results_match'] and licenses['original_license_fields_unchanged'] and licenses['original_public_key_unchanged']
backup=remote('cat '+args.backup+'/result.json','production-deploy.json')
assert backup['deployed'] and backup['legacy_count']==43 and backup['account_license_count']==3 and backup['signing_keys_unchanged']
assert backup['legacy_hash']=='4ae354a28519c9a94c1ab746f9e184f57feec31f1954a011246b33cbe42505e7'
assert backup['account_hash']=='571f07315fc7f5355829b4741cd05a8bb4d91a6ffcc470d55e0fb91acfe7c5c8'
code="""from pathlib import Path;import json,sqlite3
app=Path('/www/server/ae-license/app');public=Path('/www/wwwroot/license.moneyfly.club/public')
c=sqlite3.connect('file:/www/server/ae-license/storage/license.sqlite?mode=ro',uri=True)
functions=(app/'shop_ops.php').read_text();routes=(public/'index.php').read_text();portal=(public/'shop/index.php').read_text();api=(public/'shop/api.php').read_text()
result=dict(removed_files=all(not (app/'views'/name).exists() for name in ['shop_refunds.php','shop_invoices.php']),removed_handlers=all('function '+name+'(' not in functions for name in ['page_shop_refunds','page_shop_refund_create','page_shop_refund_complete','page_shop_invoices','page_shop_invoice_issue','user_invoice_request','epay_refund_order']),removed_routes='shop-refunds' not in routes and 'shop-invoices' not in routes,removed_user_invoice='p-invoice' not in portal and 'loadInvoices' not in portal and 'invoice_request' not in api,real_software_log_exists=c.execute('SELECT COUNT(*) FROM app_logs WHERE id=491').fetchone()[0],real_log_diagnostic_events=c.execute('SELECT COUNT(*) FROM lc_diagnostic_events WHERE log_id=491').fetchone()[0],raw_log_count=c.execute('SELECT COUNT(*) FROM app_logs').fetchone()[0],diagnostic_event_count=c.execute('SELECT COUNT(*) FROM lc_diagnostic_events').fetchone()[0])
print(json.dumps(result))"""
import shlex
features=remote('python3 -c '+shlex.quote(code),'production-features.json')
assert all(features[k] for k in ['removed_files','removed_handlers','removed_routes','removed_user_invoice','real_software_log_exists'])
coverage=remote('/www/server/php/74/bin/php /tmp/verify_log_coverage_remote.php','production-log-coverage.json')
assert coverage['all_sample_events_covered'] and coverage['backfill_complete']
css=requests.get('https://license.moneyfly.club/assets/admin.css?v='+args.css_version,timeout=30);css.raise_for_status()
local=(ROOT/'deploy/license-center-v2/public/assets/admin.css').read_bytes()
assert hashlib.sha256(css.content).digest()==hashlib.sha256(local).digest()
(folder/'production-admin.css').write_bytes(css.content)
http={}
for path,status in [('/',200),('/admin/shop-refunds',404),('/admin/shop-invoices',404),('/shop/data/license.db',403)]:
    response=requests.get('https://license.moneyfly.club'+path,timeout=30)
    http[path]=response.status_code
    assert response.status_code==status,(path,response.status_code)
result=dict(deployed=True,rendered_pages=pages['pages'],render_failures=0,max_render_ms=max(r['milliseconds'] for r in pages['rows']),legacy_api_checked=licenses['legacy_api_checked'],existing_licenses_unchanged=True,existing_signing_keys_unchanged=True,stylesheet_matches=True,http=http,features=features,log_coverage=coverage)
(folder/'production-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
# Advance the code baseline only after deployment and all acceptance checks succeed.
baseline_path=ROOT/'build/license-center-v2/production-code-baseline.json'
baseline=json.loads(baseline_path.read_text())
for row in json.loads((ROOT/'build/license-center-v2/release/manifest.json').read_text()):baseline[row['target']]=row['after']
baseline_path.write_text(json.dumps(baseline,indent=2))
