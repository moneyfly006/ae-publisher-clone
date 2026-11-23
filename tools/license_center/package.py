"""Package changed website code only; exclude production config, databases and keys."""
from pathlib import Path
import hashlib
import json
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[2]
source=ROOT/'deploy/license-center-v2'
original=ROOT/'build/vps-review/source/www'
release=ROOT/'build/license-center-v2/release'
release.mkdir(parents=True,exist_ok=True)
manifest=[]
baseline_path=release.parent/'production-code-baseline.json'
baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
for path in source.rglob('*'):
    if not path.is_file():continue
    if path.name=='removed-files.json':continue
    rel=path.relative_to(source)
    if path.name=='config.php' or path.suffix in ('.db','.sqlite','.pem'):
        raise RuntimeError('Private production file in release: '+str(rel))
    if rel.parts[0] in ('app','bin','shared'):
        target=Path('/www/server/ae-license')/rel
        before=original/'server/ae-license'/rel
    else:
        target=Path('/www/wwwroot/license.moneyfly.club')/rel
        before=original/'wwwroot/license.moneyfly.club'/rel
    old=baseline.get(str(target),hashlib.sha256(before.read_bytes()).hexdigest() if before.exists() else None)
    new=hashlib.sha256(path.read_bytes()).hexdigest()
    if old==new:continue
    out=release/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,out)
    manifest.append(dict(source=str(rel),target=str(target),before=old,after=new))
for rel in json.loads((source/'removed-files.json').read_text()):
    if rel not in ('app/views/shop_refunds.php','app/views/shop_invoices.php'):
        raise RuntimeError('Unexpected removal target')
    target=Path('/www/server/ae-license')/rel
    before=original/'server/ae-license'/rel
    old=baseline.get(str(target),hashlib.sha256(before.read_bytes()).hexdigest() if before.exists() else None)
    if old:manifest.append(dict(source=rel,target=str(target),before=old,after=None,operation='delete'))
(release/'manifest.json').write_text(json.dumps(manifest,indent=2))
shutil.copy2(ROOT/'tools/license_center/deploy.py',release/'deploy.py')
archive=release.parent/'release.tar.gz'
with tarfile.open(archive,'w:gz') as package:
    for row in manifest:
        if row.get('operation')!='delete':package.add(release/row['source'],arcname=row['source'])
    for name in ('manifest.json','deploy.py'):package.add(release/name,arcname=name)
archive.chmod(0o600)
print(json.dumps(dict(files=len(manifest),sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
