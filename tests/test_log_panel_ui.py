"""实时日志：自动打开、非模态、按平台过滤，左下角不再重复。"""
import os,sys
from pathlib import Path
from types import SimpleNamespace
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
 app.log_rows=[("10:00:00","INFO","速卖通 · 保存成功"),
               ("10:00:01","INFO","Alibaba · 保存成功"),
               ("10:00:02","WARN","TikTok · 图片超时")]
 app.log_panel_level=SimpleNamespace(value="全部")
 app.log_panel_platform=SimpleNamespace(value="aliexpress")
 rows=app._log_panel_items()
 chk("速卖通过滤只保留速卖通", len(rows)==1 and "速卖通" in rows[0][2], rows)
 app.log_panel_platform.value="alibaba"
 chk("Alibaba 过滤只保留 Alibaba", len(app._log_panel_items())==1)
 app.log_panel_platform.value="全部"
 chk("全部平台显示全部", len(app._log_panel_items())==3)
 # 自动打开在测试环境必须跳过
 os.environ["AURORA_TEST"]="1"
 called=[]
 app._open_log_panel=lambda: called.append(1)
 app._auto_open_log_panel()
 chk("测试/预览环境不自动打开面板", called==[],called)
 os.environ.pop("AURORA_TEST",None)
 src=(ROOT/"src/aeclone/ui/app.py").read_text(encoding="utf-8")
 chk("左下角已移除日志监控入口", 'TextButton("日志监控"' not in src)
 chk("实时日志是非模态右上角", "modal=False, alignment=ft.alignment.top_right" in src)
 chk("设置页版本可点击检查更新", "点击检查更新" in src)
 _gate=src[src.find("def _startup_update_gate"):src.find("def _show_update_gate")]
 chk("登录/更新完成后不自动打开日志", "_open_log_on_task" not in _gate and "_auto_open_log_panel" not in _gate)
 chk("任务启动路径会打开日志", src.count("_open_log_on_task") >= 3)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
