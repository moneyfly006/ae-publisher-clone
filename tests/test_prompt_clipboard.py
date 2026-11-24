"""提示词输入框快捷键：页面级键盘处理器下仍能 Ctrl/Cmd+C/V。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    with sandbox():
        app = build_app()
        tf = ft.TextField(value="hello world")
        app._active_text_field = tf
        tf.selection = ft.TextSelection(start=6, end=11)
        app.page.clipboard = "PASTE"
        app._on_key(SimpleNamespace(key="c", ctrl=True, meta=False, shift=False, alt=False))
        check("复制选中文本", app.page.clipboard == "world", app.page.clipboard)
        tf.selection = ft.TextSelection(start=6, end=11)
        app.page.clipboard = "X"
        app._on_key(SimpleNamespace(key="v", ctrl=True, meta=False, shift=False, alt=False))
        check("粘贴覆盖选区", tf.value == "hello X", tf.value)
        app._on_key(SimpleNamespace(key="a", ctrl=True, meta=False, shift=False, alt=False))
        check("全选", tf.selection.start == 0 and tf.selection.end == len(tf.value),
              tf.selection)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
