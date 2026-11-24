"""任务在新实例运行：独立配置目录、独立调试端口、自动启动标记。"""
import json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.core import instances
from aeclone.services import task_profiles as tp

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 old=os.environ.get("AURORA_CONFIG_DIR")
 with tempfile.TemporaryDirectory() as d:
  os.environ["AURORA_CONFIG_DIR"]=d
  task=tp.create("钻机任务", None, "aliexpress", keyword="钻机")
  captured={}
  def fake_launch(name, env_extra=None):
   captured["name"]=name; captured["env"]=dict(env_extra or {})
   class P:
    def wait(self): return 0
   return P()
  with patch.object(instances, "launch_instance", fake_launch):
   info=instances.launch_task_instance(task, "测试实例")
  cfg=json.loads((Path(info["path"])/"config.json").read_text(encoding="utf-8"))
  chk("任务实例独立配置目录", "AuroraListInstances" in info["path"], info["path"])
  chk("任务平台写入实例配置", cfg.get("active_platform")=="aliexpress", cfg.get("active_platform"))
  chk("任务关键词写入实例配置", cfg.get("aliexpress_collect_keyword")=="钻机")
  chk("实例带自动启动标记", captured.get("env",{}).get("AURORA_AUTOSTART")=="1")
  chk("实例带任务ID", captured.get("env",{}).get("AURORA_TASK_ID")==task["id"])
  chk("实例目录有任务快照", (Path(info["path"])/"task_profiles.json").exists())
 if old is None: os.environ.pop("AURORA_CONFIG_DIR",None)
 else: os.environ["AURORA_CONFIG_DIR"]=old
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
