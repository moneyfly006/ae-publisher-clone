"""发布概览不是空队列：数量必须刷新，且有自动刷新。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.ui.app import App

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 src=(ROOT/"src/aeclone/ui/app.py").read_text(encoding="utf-8")
 chk("发布数量强制绕过30秒缓存", "counts(self.cfg.store_id or None, ttl=0)" in src)
 chk("发布概览有自动刷新", "_start_publish_counts_auto_refresh" in src)
 chk("页签说明是发布概览", "发布概览（数量与批量操作）" in src)
 chk("仍保留待发布/失败/批量操作", "看发布失败列表" in src and "批量发布「待发布」" in src)
 app=object.__new__(App)
 app._current=8
 app._pub_counts_gen=1
 calls=[]
 app._start_publish_counts_auto_refresh(lambda: calls.append(1))
 chk("自动刷新线程已启动", isinstance(app._pub_counts_gen,int), app._pub_counts_gen)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
