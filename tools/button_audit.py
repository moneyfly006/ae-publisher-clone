"""逐按钮遍历测试：实例化 App，遍历每个页面所有带回调的控件并触发，记录报错。"""
import sys, traceback
sys.path.insert(0, "src")

import flet as ft


class FakeControl:
    def __init__(self, value=""):
        self.value = value
        self.data = None
        self.control = self


class FakeEvent:
    def __init__(self, value=""):
        self.control = FakeControl(value)


class FakeSnackBar:
    def __init__(self):
        self.content = ft.Text("")
        self.bgcolor = None
        self.open = False


class FakePage:
    def __init__(self):
        self.controls = []
        self.dialog = None
        self.snack_bar = FakeSnackBar()
        self.overlay = []
        self.title = ""
        self.bgcolor = ""
        self.padding = 0
        self.theme_mode = ft.ThemeMode.DARK

    def update(self, *a, **k):
        pass

    def add(self, *a, **k):
        pass

    def launch_url(self, *a, **k):
        pass

    def run_thread(self, *a, **k):
        pass


from aeclone.ui.app import App

results = []


def walk(controls, depth=0):
    for c in controls:
        if c is None:
            continue
        yield c
        for attr in ("controls", "content"):
            try:
                sub = getattr(c, attr)
            except Exception:
                continue
            if isinstance(sub, (list, tuple)):
                yield from walk(sub, depth + 1)
            elif sub is not None:
                yield from walk([sub], depth + 1)


def run():
    app = App(FakePage())
    # 遍历每个页面
    for idx in range(12):
        try:
            app._switch(idx)
        except Exception as e:
            results.append((idx, "_switch", "ERR", str(e)))
            continue
        controls = list(app.content.controls) if getattr(app, "content", None) else []
        buttons = []
        for c in walk(controls):
            for cb in ("on_click", "on_change"):
                fn = getattr(c, cb, None)
                if callable(fn):
                    buttons.append((c, cb, fn))
        DESTRUCTIVE = ("恢复默认", "清空", "删除", "重置", "取消", "关闭", "终止")
        for c, cb, fn in buttons:
            label = getattr(c, "text", None) or getattr(c, "label", None) or type(c).__name__
            if any(k in str(label) for k in DESTRUCTIVE):
                results.append((idx, label, "SKIP", "破坏性操作，跳过"))
                continue
            try:
                if cb == "on_click":
                    fn(FakeEvent())
                else:
                    fn(FakeEvent(""))
                results.append((idx, label, "OK", ""))
            except Exception as e:
                results.append((idx, label, "ERR", f"{type(e).__name__}: {e}"))

    err = [r for r in results if r[2] == "ERR"]
    ok = [r for r in results if r[2] == "OK"]
    print(f"触发回调 {len(results)} 个 | OK {len(ok)} | ERR {len(err)}")
    for idx, label, st, msg in err:
        print(f"  [页{idx}] {label} -> {msg[:120]}")
    for idx, label, st, msg in ok:
        print(f"  [页{idx}] {label} OK")


if __name__ == "__main__":
    run()
