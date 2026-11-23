"""Run on the VPS: verify existing device API behavior without changing license rights."""
from pathlib import Path
import datetime,hashlib,http.client,json,sqlite3,ssl
database=Path('/www/server/ae-license/storage/license.sqlite')
c=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute('SELECT * FROM licenses ORDER BY id')]
settings=dict(c.execute('SELECT key,value FROM settings'))
columns=[r[1] for r in c.execute('PRAGMA table_info(licenses)') if r[1] not in ('last_verified_at','verify_count')]
before=[tuple(r[k] for k in columns) for r in rows]
keyfile=Path('/www/server/ae-license/storage/keys/public.pem')
keyhash=hashlib.sha256(keyfile.read_bytes()).hexdigest()
results=[]
for row in rows:
    body=json.dumps(dict(identity=row['identity'],client_type=row['client_type']))
    # Loopback TLS is intentional: Host selects the production virtual host.
    connection=http.client.HTTPSConnection('127.0.0.1',context=ssl._create_unverified_context(),timeout=15)
    headers={'Host':'license.moneyfly.club','Content-Type':'application/json'}
    if settings.get('api_key_enabled')=='1':headers['X-API-Key']=settings.get('api_key','')
    connection.request('POST','/api/v1/license/verify',body=body,headers=headers)
    response=connection.getresponse();payload=json.loads(response.read());connection.close()
    expected=row['status']=='active' and row['expires_at']>=datetime.date.today().isoformat()
    results.append(dict(record_id=row['id'],expected=expected,authorized=payload.get('authorized'),http=response.status,
      match=payload.get('authorized') is expected,voucher_present=bool(payload.get('voucher') and payload.get('signature')) if expected else None))
after=[tuple(r) for r in c.execute('SELECT '+','.join(columns)+' FROM licenses ORDER BY id')];c.close()
shop=sqlite3.connect('file:/www/wwwroot/license.moneyfly.club/public/shop/data/license.db?mode=ro',uri=True)
result=dict(legacy_api_checked=len(results),all_results_match=all(r['match'] for r in results),
  original_license_fields_unchanged=before==after,original_public_key_unchanged=hashlib.sha256(keyfile.read_bytes()).hexdigest()==keyhash,
  legacy_integrity=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True).execute('PRAGMA quick_check').fetchone()[0],
  account_integrity=shop.execute('PRAGMA quick_check').fetchone()[0],results=results)
print(json.dumps(result))
if not (result['all_results_match'] and result['original_license_fields_unchanged']):raise SystemExit(1)
