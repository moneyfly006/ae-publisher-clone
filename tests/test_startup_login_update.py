"""启动流程：登录弹窗未结束时不能弹更新窗。"""
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
 app=object.__new__(App)
 app._startup_login_open=True
 app._startup_update_started=False
 calls=[]
 app._startup_update_gate=lambda: calls.append(1)
 app._finish_startup_login()
 chk("登录窗打开时不检查更新", not calls,calls)
 app._startup_login_open=False
 app._finish_startup_login()
 chk("登录窗关闭后才检查更新", len(calls)==1,calls)
 app._finish_startup_login()
 chk("更新检查不会重复启动", len(calls)==1,calls)
 src=(ROOT/"src/aeclone/ui/app.py").read_text(encoding="utf-8")
 chk("启动代码不再直接连调更新检查",
     "self._startup_update_gate()\n        except Exception:\n            pass\n        # 设置里用到 ffmpeg" not in src)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
