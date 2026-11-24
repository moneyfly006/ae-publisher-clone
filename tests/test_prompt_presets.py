"""自定义提示词预设：保存命名、选中生效、重启页面仍记住选择。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk

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
        app._switch_platform("aliexpress")
        app._switch(3)
        controls = list(walk(app.content.controls))
        # 页面里第一个带“添加自定义提示词”的预设下拉框（标题预设）
        dd0 = next(c for c in controls if isinstance(c, ft.Dropdown)
                   and any(getattr(o, "key", "") == "__add__" for o in (c.options or [])))
        dd0.value = "__add__"
        dd0.on_change(SimpleNamespace(control=dd0))
        dlg = app._dlg
        fields = [c for c in walk([dlg]) if isinstance(c, ft.TextField)]
        name, prompt = fields[0], fields[1]
        name.value = "我的机械标题"
        prompt.value = "我的自定义标题提示词：只写真实事实"
        dlg.actions[1].on_click(SimpleNamespace(control=dlg.actions[1]))
        data = json.loads(str(app.cfg.prompt_presets_data or "{}"))
        check("保存自定义提示词", data.get("custom", {}).get("smt:title", {}).get("我的机械标题")
              == "我的自定义标题提示词：只写真实事实", data)
        check("自动记住选择", data.get("choices", {}).get("smt:title") == "我的机械标题", data)
        # 重新打开页面，预设和选择仍在
        app._switch(0)
        app._switch(3)
        controls2 = list(walk(app.content.controls))
        dd = next(c for c in controls2 if isinstance(c, ft.Dropdown)
                  and any("我的机械标题" in str(getattr(o, "text", "")) for o in (c.options or [])))
        check("重新打开仍选中", str(dd.value or "").endswith("我的机械标题"), dd.value)
        check("预设仍在选项中", any("我的机械标题" in str(o.text) for o in dd.options),
              [o.text for o in dd.options])
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
