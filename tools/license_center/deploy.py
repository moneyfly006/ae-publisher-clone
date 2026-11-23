"""Remote atomic file replacement with code preconditions and legacy-data invariants.

Run on the VPS after uploading the reviewed release archive. Does not restore,
replace, merge or delete either production database or the signature keys.
"""
from pathlib import Path
import datetime,hashlib,json,os,shutil,sqlite3,subprocess,sys,tarfile
release=Path(__file__).resolve().parent
manifest=json.loads((release/'manifest.json').read_text())
# Install newly introduced dependencies before replacing their callers.
manifest.sort(key=lambda item: item['before'] is not None)
backup=Path('/www/backups/license-rebuild')/('deploy-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True,mode=0o700)
legacy=Path('/www/server/ae-license/storage/license.sqlite')
shop=Path('/www/wwwroot/license.moneyfly.club/public/shop/data/license.db')
keys=Path('/www/server/ae-license/storage/keys')
def rows(path):
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
    columns=[r[1] for r in c.execute('PRAGMA table_info(licenses)')]
    ignored={'last_verified_at','verify_count'} if path==legacy else set()
    selected=[x for x in columns if x not in ignored]
    result=c.execute('SELECT '+','.join(selected)+' FROM licenses ORDER BY id').fetchall();c.close()
    return dict(columns=selected,rows=result)
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,default=str).encode()).hexdigest()
old=rows(legacy);old_shop=rows(shop)
key_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in keys.iterdir() if p.is_file()}
for path,name in [(legacy,'legacy.sqlite'),(shop,'shop.sqlite')]:
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);d=sqlite3.connect(str(backup/name));c.backup(d);d.close();c.close();(backup/name).chmod(0o600)
for item in manifest:
    target=Path(item['target'])
    if target.exists():
        actual=hashlib.sha256(target.read_bytes()).hexdigest()
        if item['before'] is not None and actual!=item['before']:raise RuntimeError('Online source changed: '+str(target))
        if item['before'] is None and actual!=item['after']:raise RuntimeError('Unexpected existing new file: '+str(target))
    elif item['before'] is not None:raise RuntimeError('Missing previous file: '+str(target))
    if item.get('operation')!='delete':
        source=release/item['source']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['after']:raise RuntimeError('Release file hash mismatch')
for p in release.rglob('*.php'):
    subprocess.run(['/www/server/php/74/bin/php','-l',str(p)],check=True,stdout=subprocess.DEVNULL)
nginx=Path('/www/server/panel/vhost/nginx/license.moneyfly.club.conf')
original_nginx=nginx.read_text()
rule='''    # AuroraList: private data and source backups are never web assets.
    location ^~ /shop/data/ { deny all; }
    location ~* \\.(db|sqlite|sqlite3)(-(wal|shm))?$ { deny all; }
    location ~* \\.(bak|backup)(\\.|/|$) { deny all; }
'''
new_nginx=original_nginx if 'AuroraList: private data' in original_nginx else original_nginx.replace('    location / {',rule+'\n    location / {',1)
(backup/'nginx.conf').write_text(original_nginx)
replaced=[]
try:
    for item in manifest:
        target=Path(item['target']);target.parent.mkdir(parents=True,exist_ok=True)
        before=backup/item['source'];before.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():shutil.copy2(target,before)
        if item.get('operation')=='delete':
            target.unlink();replaced.append(item);continue
        stage=target.with_name('.aurora-staging-'+target.name)
        shutil.copyfile(release/item['source'],stage);os.chown(stage,os.stat('/www/server/ae-license/app').st_uid,os.stat('/www/server/ae-license/app').st_gid);stage.chmod(0o644);stage.replace(target);replaced.append(item)
    nginx.write_text(new_nginx)
    subprocess.run(['/www/server/nginx/sbin/nginx','-t'],check=True,capture_output=True)
    # Initialize only new account-side indexes/tables. No original license row edits.
    subprocess.run(['/www/server/php/74/bin/php','-r',"require '/www/server/ae-license/shared/entitlements.php';lc_db();"],check=True,capture_output=True)
    # Diagnostic projections append new tables only; execute as the web user so
    # SQLite WAL/SHM ownership stays compatible with PHP-FPM.
    diagnostics=Path('/www/server/ae-license/bin/index_diagnostics.php')
    if diagnostics.exists():
        indexed=subprocess.run(['runuser','-u','www','--','/www/server/php/74/bin/php',str(diagnostics)],check=True,capture_output=True,text=True,timeout=180)
        (backup/'diagnostic-index.json').write_text(indexed.stdout)
    for suffix in ('','-wal','-shm'):
        p=Path(str(shop)+suffix)
        if p.exists():shutil.chown(p,user='www',group='www')
    if rows(legacy)!=old:raise RuntimeError('Original device license fields changed during deployment')
    if rows(shop)!=old_shop:raise RuntimeError('Existing account license rows changed during deployment')
    if key_hashes!={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in keys.iterdir() if p.is_file()}:raise RuntimeError('Signing keys changed')
    subprocess.run(['/www/server/nginx/sbin/nginx','-s','reload'],check=True,capture_output=True)
    result=dict(deployed=True,backup=str(backup),legacy_count=len(old['rows']),legacy_hash=digest(old),account_license_count=len(old_shop['rows']),account_hash=digest(old_shop),signing_keys_unchanged=True,files=len(replaced))
    (backup/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
except BaseException:
    for item in reversed(replaced):
        before=backup/item['source'];target=Path(item['target'])
        if before.exists():shutil.copy2(before,target)
        elif target.exists():target.unlink()
    nginx.write_text(original_nginx)
    subprocess.run(['/www/server/nginx/sbin/nginx','-t'],check=False,capture_output=True)
    subprocess.run(['/www/server/nginx/sbin/nginx','-s','reload'],check=False,capture_output=True)
    raise
