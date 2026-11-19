"""每个页面都必须能构建成功——否则点击菜单会「没反应」。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import get_config  # noqa: E402
from aeclone.ui.app import App  # noqa: E402


class Stub(App):
    def __getattr__(self, name):
        return MagicMock()


a = object.__new__(Stub)
a.cfg = get_config()
a.store_counts = {}
a._cookie_status_text = lambda refresh=False: (True, "手动配置 · 9 个")
a._safe_update = lambda: None
a.toast = lambda m, ok=True: None
a._cookies = lambda refresh=False: {}
a.products = []
a.log_rows = []
a.logs = []
a.field_refs = []

PAGES = [("_p_overview", "概览"), ("_p_store", "店铺设置"), ("_p_advanced", "商品设置"),
         ("_p_ai", "AI 文案"), ("_p_review", "内容审查"), ("_p_image", "AI 图片"),
         ("_p_price", "价格设置"), ("_p_variant", "变种设置"), ("_p_publish", "发布队列"), ("_p_settings", "设置"),
         ("_p_workbench", "图片工作台"), ("_p_logs", "任务与日志")]

ok = fail = 0
print("=" * 60)
print("界面页面构建测试")
for fn, label in PAGES:
    try:
        out = getattr(a, fn)()
        n = len(out) if isinstance(out, list) else 1
        ok += 1
        print(f"  ✓ {label:6} 控件 {n}")
    except Exception as ex:  # noqa: BLE001
        fail += 1
        print(f"  ✗ {label:6} {type(ex).__name__}: {str(ex)[:110]}")

print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)
