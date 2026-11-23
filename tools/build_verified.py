"""每项修复构建到新目录，绝不覆盖正在运行的 PyInstaller 应用包。"""
from pathlib import Path
import argparse, datetime, hashlib, json, os, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--test',action='append',default=[])
    args=parser.parse_args()
    if not args.label.replace('-','').replace('_','').isalnum(): parser.error('label must contain letters, digits, - or _')
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output=ROOT/'dist'/f'verified-{args.label}-{stamp}'
    folder=ROOT/'build/verification'/f'{args.label}-{stamp}'
    folder.mkdir(parents=True)
    tests=[]
    for test in args.test:
        path=(ROOT/test).resolve()
        if not path.is_relative_to(ROOT/'tests') or path.suffix!='.py': parser.error('test must be a Python file under tests/')
        env=dict(os.environ,AURORA_CONFIG_DIR=str(folder/'config'),AURORA_SKIP_LICENSE='1',AURORA_TEST='1',PYTHONPATH=str(ROOT/'src'))
        with (folder/(path.stem+'.log')).open('w') as log:
            subprocess.run([str(ROOT/'.venv/bin/python'),str(path)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        tests.append(test)
    env=dict(os.environ,PYINSTALLER_CONFIG_DIR='/private/tmp/aurora-pyinstaller-cache')
    with (folder/'build.log').open('w') as log:
        subprocess.run([str(ROOT/'.venv/bin/pyinstaller'),'app.spec','--noconfirm','--log-level','WARN','--workpath',str(ROOT/'build/verified-work'),'--distpath',str(output)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    app=output/'AuroraList.app'
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
    exe=app/'Contents/MacOS/AuroraList'
    with (folder/'packaged-local-check.log').open('w') as log:
        subprocess.run([str(exe),'--verify-local'],cwd=ROOT,env=dict(os.environ,
            AURORA_CONFIG_DIR=str(folder/'localcheck-config')),stdout=log,stderr=subprocess.STDOUT,
            timeout=90,check=True)
    result=dict(issue=args.label,build=str(app.relative_to(ROOT)),tests=tests,sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),verified_at=datetime.datetime.now().isoformat(),signature='PASS',packaged_local_check='PASS')
    ledger=ROOT/'docs/fix-build-verification.json'
    rows=json.loads(ledger.read_text()) if ledger.exists() else []
    rows.append(result);ledger.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (ROOT/'build/verification/latest-build.json').write_text(json.dumps(result))
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
