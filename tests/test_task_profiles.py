"""任务配置：保存多份快照、独立运行、完成后保留/删除。"""
import os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.config import AppConfig
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
  cfg=AppConfig(); cfg.set("template_product_id","TPL-1"); cfg.set("cookie","SECRET"); cfg.set("api_key","KEY")
  a=tp.create("任务A",cfg,"aliexpress",keyword="钻机",auto_release="keep")
  cfg.set("template_product_id","TPL-2")
  b=tp.create("任务B",cfg,"alibaba",keyword="挖掘机",auto_release="delete")
  items=tp.list_tasks()
  chk("可以保存多个任务",len(items)==2,items)
  chk("模板与关键词进入快照",a["config"].get("template_product_id")=="TPL-1"
      and b["config"].get("template_product_id")=="TPL-2")
  chk("敏感配置不写入任务",not any(k in a["config"] for k in ("cookie","api_key")))
  cfg2=AppConfig(); tp.apply_to_config(a,cfg2)
  chk("应用任务恢复模板",cfg2.template_product_id=="TPL-1")
  q=tp.enqueue("队列任务",cfg,"aliexpress",keyword="挖掘机",quantity=3,
               selected_products=["P-1","P-2"])
  chk("队列可加入待执行任务",q["status"]=="pending" and q["quantity"]==3
      and q["selected_products"]==["P-1","P-2"])
  chk("待执行任务可取出",[x["name"] for x in tp.pending_jobs()]==["队列任务"])
  tp.mark(a["id"],"done")
  chk("任务状态可更新",tp.get(a["id"])["status"]=="done")
  chk("auto_release=delete 执行释放",tp.release_if_done(b) and not tp.get(b["id"]))
  chk("auto_release=keep 不删除",(not tp.release_if_done(a)) and bool(tp.get(a["id"])))
  tp.append_log(q["id"],"第一行日志"); tp.append_log(q["id"],"第二行日志")
  chk("队列日志按任务持久化",len(tp.get(q["id"])["logs"])==2)
  tp.mark(q["id"],"running")
  chk("中断 running 可恢复为 pending",tp.recover_interrupted()==1
      and tp.get(q["id"])["status"]=="pending")
 if old is None: os.environ.pop("AURORA_CONFIG_DIR",None)
 else: os.environ["AURORA_CONFIG_DIR"]=old
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
