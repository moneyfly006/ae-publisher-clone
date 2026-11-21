"""Amazon 13 号「Amazon 刊登」页 · 沙箱按钮遍历 + 截图（AURORA_TEST=1，绝不真实写）。

做什么
------
1. 用 ``tools/ui_sandbox.py`` 的 ``build_app/sandbox`` 在隔离配置里切到 Amazon 平台、
   打开 13 号页；若 ``platforms.AMAZON.pages`` 还没有 index 13（UI 尚未实现），
   打印 ``NOT_READY`` 并以 0 退出（供后续重跑，不阻塞其它任务）。
2. 遍历页面内所有 ``ft.ElevatedButton / OutlinedButton / TextButton / IconButton``，
   逐个调用 ``on_click``；网络被 mock（返回空 data，不发真实请求），线程/文件选择器
   被禁用，任何回调异常都记为失败。
3. 通过 Flet Web 预览（``ft.app(..., WEB_BROWSER)``）+ Playwright/Chrome 截图，
   输出 ``docs/design/amazon-listing-page.png``。若截图链路不可用，降级把控件清单
   写成 ``docs/design/amazon-listing-page.json``（PNG 保留上一次成功产物）。

跑法
----
    AURORA_TEST=1 .venv/bin/python tools/amazon_ui_shots.py
    .venv/bin/python tools/amazon_ui_shots.py --page 12 --platform alibaba \
        --output /tmp/smoke.png        # 只用于冒烟验证截图管线（非 Amazon 13 页）

说明：本脚本不写业务代码、不调用保存接口；保存类按钮在沙箱里最多触发「预演/拒绝」
路径，网络 mock 保证不可能产生真实写操作。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AURORA_TEST"] = "1"   # 必须在 import 应用模块之前：禁止任何真实写

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _path in (str(SRC), str(TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

DEFAULT_OUTPUT = ROOT / "docs" / "design" / "amazon-listing-page.png"
FALLBACK_JSON = ROOT / "docs" / "design" / "amazon-listing-page.json"
DEFAULT_JSON = FALLBACK_JSON
VIEWPORT = {"width": 1560, "height": 2200}
BTN_TYPES = ("ElevatedButton", "OutlinedButton", "TextButton", "IconButton")


# ---------------------------------------------------------------------------
# 页面就绪检测
# ---------------------------------------------------------------------------
def page13_item(platform_id: str = "amazon"):
    from aeclone.platforms import get_platform
    platform = get_platform(platform_id)
    return platform.page(13) if platform else None


def is_ready(platform_id: str = "amazon") -> tuple[bool, str]:
    item = page13_item(platform_id)
    if item is None:
        return False, f"platforms.{platform_id.upper()}.pages 无 index 13（Amazon 刊登页尚未实现）"
    # 页面实现可能正在并行开发：分发表已引用但方法未定义时同样按 NOT_READY 处理。
    try:
        from aeclone.ui.app import App
    except Exception as exc:  # noqa: BLE001
        return False, f"UI 应用模块不可导入：{type(exc).__name__}: {exc}"
    if not hasattr(App, "_p_amazon_listing"):
        return False, "platforms 有 index 13，但 App._p_amazon_listing 尚未实现（UI 开发中）"
    return True, f"index=13 label={item.label} group={item.group}"


# ---------------------------------------------------------------------------
# 网络 mock：只返回空数据，绝不触达真实接口（更不可能发生写）
# ---------------------------------------------------------------------------
class MockResponse:
    status_code = 200

    def __init__(self, payload: dict | None = None):
        self._payload = payload or {"code": 0, "msg": "", "data": {}}

    def json(self) -> dict:
        return self._payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload, ensure_ascii=False)


def mock_request(self, method, url, *args, **kwargs):
    return MockResponse()


@contextlib.contextmanager
def mocked_network():
    """在沙箱已禁用真实网络的基础上，再把它替换为返回空数据的 mock。"""
    import requests
    with patch.object(requests.sessions.Session, "request", mock_request), \
         patch.object(requests.sessions.Session, "get", mock_request), \
         patch.object(requests.sessions.Session, "post", mock_request):
        yield


# ---------------------------------------------------------------------------
# 按钮遍历
# ---------------------------------------------------------------------------
def _button_specs(app):
    import flet as ft
    from ui_sandbox import label, walk
    specs = []
    for position, control in enumerate(walk(app.content.controls)):
        if not isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                    ft.TextButton, ft.IconButton)):
            continue
        if control.disabled or not callable(getattr(control, "on_click", None)):
            continue
        specs.append({"position": position, "label": label(control),
                      "type": type(control).__name__})
    return specs


def collect_and_traverse(platform_id: str, page_index: int) -> dict:
    from ui_sandbox import build_app, sandbox
    from ui_sandbox import label, walk  # noqa: F401 - 保持与既有工具一致的导入面
    import flet as ft

    # 1) 先在同构页面里收集按钮位置与标签（隔离配置，不点任何按钮）
    with sandbox(), mocked_network():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        specs = _button_specs(app)

    results = []
    for spec in specs:
        status, detail = "OK", ""
        with sandbox(), mocked_network():
            import threading
            app = build_app()
            if platform_id != "alibaba":
                app._switch_platform(platform_id)
            app._switch(page_index)
            control = list(walk(app.content.controls))[spec["position"]]
            try:
                with patch.object(threading.Thread, "start",
                                  lambda thread: None), \
                     patch.object(ft.FilePicker, "pick_files", lambda *a, **kw: None), \
                     patch.object(ft.FilePicker, "save_file", lambda *a, **kw: None), \
                     patch.object(ft.FilePicker, "get_directory_path",
                                  lambda *a, **kw: None):
                    control.on_click(SimpleNamespace(
                        control=control,
                        data=str(getattr(control, "value", "") or ""),
                        page=app.page))
            except Exception as exc:  # noqa: BLE001 - 记录后继续遍历
                status = "ERROR"
                detail = f"{type(exc).__name__}: {exc}"[:240]
        results.append({"label": spec["label"], "type": spec["type"],
                        "status": status, "detail": detail})
    errors = [row for row in results if row["status"] == "ERROR"]
    return {
        "platform": platform_id,
        "page": page_index,
        "buttons": len(results),
        "ok": len(results) - len(errors),
        "errors": len(errors),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 截图：Flet Web 预览 + Playwright/Chrome
# ---------------------------------------------------------------------------
class _PreviewServer:
    def __init__(self, platform_id: str, page_index: int, port: int):
        self.platform_id = platform_id
        self.page_index = page_index
        self.port = port
        self.process: subprocess.Popen | None = None
        self.log_path = ROOT / "build" / "verification" / "amazon-ui-preview.log"

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "AURORA_TEST": "1",
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "build" / "ui-preview-deps"), str(SRC), str(TOOLS)]),
            "AURORA_WINDOW": "1514x945",
        })
        log = open(self.log_path, "w")
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--serve", "--port", str(self.port),
             "--platform", self.platform_id, "--page", str(self.page_index)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
        self._log = log

    def wait(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.4)
                if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                    return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=6)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                process.kill()
        with contextlib.suppress(Exception):
            self._log.close()
        self.process = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def capture_screenshot(platform_id: str, page_index: int,
                       output: Path) -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return False, f"Playwright 不可用：{type(exc).__name__}: {exc}"
    port = _free_port()
    server = _PreviewServer(platform_id, page_index, port)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start()
        if not server.wait():
            return False, f"Flet 预览服务端口 {port} 未就绪（见 {server.log_path}）"
        with sync_playwright() as playwright:
            try:
                # 新版 headless 才能正确加载 mac 系统中文字体（旧 headless 会渲染成方框）。
                browser = playwright.chromium.launch(
                    channel="chrome", headless=True,
                    args=["--headless=new", "--force-device-scale-factor=1"])
            except Exception:  # noqa: BLE001 - 没有 Chrome channel 时用内置 Chromium
                with contextlib.suppress(Exception):
                    browser = playwright.chromium.launch(
                        channel="chrome", headless=True,
                        args=["--force-device-scale-factor=1"])
                if "browser" not in locals():
                    browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}", wait_until="load", timeout=45000)
                with contextlib.suppress(Exception):
                    page.wait_for_selector("canvas", timeout=25000)
                page.wait_for_timeout(3500)  # CanvasKit 字体/布局稳定
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output))
            finally:
                browser.close()
        if not output.exists() or output.stat().st_size <= 0:
            return False, f"截图文件为空：{output}"
        return True, f"{output} ({output.stat().st_size} bytes)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# --serve：Flet Web 预览进程（供截图子进程拉起）
# ---------------------------------------------------------------------------
def serve(platform_id: str, page_index: int, port: int, theme: str) -> None:
    os.environ["AURORA_THEME"] = theme
    import flet as ft
    from ui_sandbox import ROOT as ASSET_ROOT
    from ui_sandbox import build_app, sandbox

    def target(page):
        app = build_app(page)
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        with contextlib.suppress(Exception):
            page.update()

    with sandbox(threads=True):
        ft.app(target=target, view=ft.AppView.WEB_BROWSER, host="127.0.0.1",
               port=port, assets_dir=str(ASSET_ROOT / "assets"),
               web_renderer=ft.WebRenderer.CANVAS_KIT)


def write_control_inventory(platform_id: str, page_index: int, path: Path,
                            traversal: dict) -> None:
    """截图不可用时的降级产物：页面控件树文本 dump。"""
    from ui_sandbox import build_app, label, sandbox, walk

    inventory = []
    with sandbox(), mocked_network():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        for position, control in enumerate(walk(app.content.controls)):
            inventory.append({
                "position": position,
                "type": type(control).__name__,
                "label": label(control)[:160],
                "has_on_click": callable(getattr(control, "on_click", None)),
                "disabled": bool(getattr(control, "disabled", False)),
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "platform": platform_id,
        "page": page_index,
        "note": "PNG 截图链路不可用，降级为控件清单（控件树文本 dump）。",
        "controls": inventory,
        "traversal": traversal,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amazon 13 页沙箱按钮遍历 + 截图")
    parser.add_argument("--page", type=int, default=13, help="页面号（默认 13）")
    parser.add_argument("--platform", default="amazon", help="平台 id（默认 amazon）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="PNG 输出路径")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="降级控件清单 JSON 路径")
    parser.add_argument("--theme", default="dark", help="dark / light")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.serve:
        serve(args.platform, args.page, args.port or 8891, args.theme)
        return 0

    target_is_amazon13 = args.platform == "amazon" and args.page == 13
    if target_is_amazon13:
        ready, detail = is_ready("amazon")
        if not ready:
            print(f"NOT_READY: {detail}")
            print("提示：UI 完成后重跑本脚本即可生成 docs/design/amazon-listing-page.png。")
            return 0
        print(f"READY: {detail}")

    # 渲染 + 打开目标页，确认分发表真的落到目标页（防止 index 存在但未接入 _switch）。
    from ui_sandbox import build_app, sandbox
    try:
        with sandbox(), mocked_network():
            app = build_app()
            if args.platform != "alibaba":
                app._switch_platform(args.platform)
            app._switch(args.page)
            landed = getattr(app, "_page_idx", None)
            controls = len(app.content.controls)
    except Exception as exc:  # noqa: BLE001 - UI 并行开发期间可能半成品
        if target_is_amazon13:
            print(f"NOT_READY: 13 页实现不完整（{type(exc).__name__}: {exc}）")
            return 0
        print(f"ERROR: 页面渲染失败（{type(exc).__name__}: {exc}）")
        return 1
    print(f"页面渲染：platform={args.platform} page={args.page} "
          f"落点 _page_idx={landed} 顶层控件={controls}")
    if target_is_amazon13 and landed != 13:
        print(f"NOT_READY: platforms 有 index 13，但 _switch 未落到 13 页（实际 {landed}）")
        return 0

    print("遍历按钮（网络 mock；线程/文件选择器禁用）...")
    traversal = collect_and_traverse(args.platform, args.page)
    print(f"按钮：{traversal['buttons']} 个 · OK {traversal['ok']} · ERROR {traversal['errors']}")
    for row in traversal["results"]:
        if row["status"] != "OK":
            print(f"  [ERROR] {row['label']}: {row['detail']}")

    print("截图（Flet Web 预览 + Playwright/Chrome）...")
    shot_ok, shot_detail = capture_screenshot(args.platform, args.page,
                                              Path(args.output))
    if shot_ok:
        print(f"截图成功：{shot_detail}")
    else:
        print(f"截图失败：{shot_detail}")
        fallback = Path(args.json)
        try:
            write_control_inventory(args.platform, args.page, fallback, traversal)
            print(f"已降级输出控件清单：{fallback}")
        except Exception as exc:  # noqa: BLE001
            print(f"控件清单降级也失败：{type(exc).__name__}: {exc}")

    # 只要页面打开成功：按钮异常才算失败；截图链路失败按「降级」处理（退出 0）。
    if traversal["errors"]:
        print("结论：FAIL（存在按钮回调异常）")
        return 1
    print("结论：PASS（页面可打开、按钮无异常"
          + ("、截图成功" if shot_ok else "、截图已降级") + "）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
