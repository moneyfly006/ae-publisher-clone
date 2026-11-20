"""打开构建好的原生验收实例，使用独立配置，不改动日常实例设置。"""
from pathlib import Path
import os
import subprocess
import sys
import json
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--page',type=int,default=8)
parser.add_argument('--live',action='store_true',help='打开真实运行实例，由用户自行点击开始')
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
latest=ROOT/'build/verification/latest-build.json'
app=(ROOT/json.loads(latest.read_text())['build'] if latest.exists() else
     ROOT/'dist/verified-01-queue-order/AuroraList.app')
path=app/'Contents/MacOS/AuroraList'
if not path.exists(): raise SystemExit('请先构建 dist/native-verification/AuroraList.app')
env=dict(os.environ)
env.update(AURORA_CONFIG_DIR=str(ROOT/'build/verification/native-config'),
           AURORA_INSTANCE='本地验收', AURORA_NO_SAVE='1')
if args.live:
    for key in ('AURORA_NO_SAVE','AURORA_TEST','AURORA_SKIP_LICENSE'):
        env.pop(key,None)
    env['AURORA_INSTANCE']='本地真实运行'
log=ROOT/'build/verification/native-console.log'
with log.open('a') as output:
    process=subprocess.Popen([str(path),'--page',str(args.page)],env=env,stdout=output,stderr=output,start_new_session=True)
print(f'{"真实运行" if args.live else "验收预演"}进程已启动，PID {process.pid}，应用包：{app}')
