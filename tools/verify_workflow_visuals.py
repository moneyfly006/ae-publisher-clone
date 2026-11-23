"""隔离 Flet 实际渲染验收，输出页面截图和浏览器错误；不读取真实配置或提交商品。"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default="shopee")
    parser.add_argument("--page", type=int, action="append")
    parser.add_argument("--theme", default="dark")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scenario", choices=("default", "results", "forms"), default="default")
    args = parser.parse_args()
    if args.serve:
        import flet as ft
        import shutil
        from ui_sandbox import build_app, sandbox
        from aeclone.ui import app as ui
        assets = ROOT / "build/ui-preview-assets"
        shutil.copytree(ROOT / "assets", assets, dirs_exist_ok=True)
        font = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
        if font.exists() and not (assets / "preview-cjk.ttf").exists():
            shutil.copyfile(font, assets / "preview-cjk.ttf")
        with sandbox(threads=True):
            def target(page):
                ui.apply_theme(args.theme)
                app = build_app(page, eager_editors=False)
                if args.theme == "light":
                    app._toggle_theme()
                app._switch_platform(args.platform)
                if args.scenario == "results":
                    from aeclone.services import collect_store as store
                    cfg = app.cfg
                    run = store.create(cfg, args.platform, [{"id": str(1000 + i)} for i in range(5)])
                    for i, outcome in enumerate(("draft_saved", "reviewing", "uncertain", "needs_info")):
                        store.item_update(run, str(1000 + i), "done", result=dict(id=str(1000 + i), ok=i < 2,
                            saved=i < 2, outcome=outcome, error=("类目属性缺少真实材质，请补齐后再处理" if i == 3 else "已记录当前阶段")))
                elif args.scenario == "forms":
                    import json as _json
                    values = (dict(price_tiers=[dict(start_quantity=1, price=12), dict(start_quantity=10, price=10)],
                        wholesale_trade=dict(package_size="20x15x10", weight="1.5", deliver_periods=[dict(quantity=10, process_period=5)]))
                        if args.platform == "alibaba" and args.page[0] == 32 else
                        dict(attributes={"material": "Steel"}) if args.platform in ("alibaba", "amazon") else
                        dict(product_attributes=[dict(attributeId="7", attributeName="Material", values=[dict(valueId="9", valueName="Steel")])]) if args.platform == "tiktok" else
                        dict(attributes=[dict(attribute_id=7, attribute_value_list=[dict(value_id=9, original_value_name="Steel")])]))
                    app.cfg.set(args.platform + "_collect_overrides", _json.dumps(values))
                app._switch(args.page[0])
                # CanvasKit 无法读取系统 CJK 字体时，预览显式从本机提供字体。
                # 字体仅存放在忽略的 build/ 下，不进入安装包。
                if font.exists():
                    page.fonts = {"PingFang SC": "/preview-cjk.ttf"}
                    # CanvasKit's partial TextTheme can fall back to Roboto for
                    # plain Text while form labels inherit the configured font.
                    from ui_sandbox import walk
                    for control in walk(page.controls):
                        if isinstance(control, ft.Text):
                            control.font_family = "PingFang SC"
                        for attribute in ("text_style", "label_style", "hint_style", "helper_style", "error_style"):
                            if hasattr(control, attribute):
                                style = getattr(control, attribute, None) or ft.TextStyle()
                                style.font_family = "PingFang SC"
                                setattr(control, attribute, style)
                page.update()
            ft.app(target=target, view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=args.port,
                   assets_dir=str(assets), web_renderer=ft.WebRenderer.CANVAS_KIT)
        return
    from aeclone.platforms import get_platform
    from playwright.sync_api import sync_playwright
    # Chrome can leave the remote CanvasKit WASM fetch pending indefinitely.
    # Cache the exact engine's official assets for this local preview only.
    import flet_web
    import re
    from urllib.request import urlopen
    bootstrap = (Path(flet_web.__file__).parent / "web/flutter_bootstrap.js").read_text()
    revision = re.search(r'"engineRevision":"([a-f0-9]+)"', bootstrap).group(1)
    canvas_origin = f"https://www.gstatic.com/flutter-canvaskit/{revision}/"
    canvas_cache = ROOT / "build/ui-preview-canvaskit" / revision
    canvas_cache.mkdir(parents=True, exist_ok=True)
    for name in ("canvaskit.js", "canvaskit.wasm"):
        asset = canvas_cache / name
        if not asset.exists():
            with urlopen(canvas_origin + "chromium/" + name, timeout=30) as response:
                asset.write_bytes(response.read())
    platform = get_platform(args.platform)
    pages = args.page or [p.index for p in platform.pages if not p.group.startswith("_")]
    folder = ROOT / "build/ui-redesign/visuals" / platform.id / args.theme
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True,
            args=["--headless=new", "--force-device-scale-factor=1"])
        try:
            for index in pages:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                env = dict(os.environ, AURORA_TEST="1", PYTHONPATH=os.pathsep.join([
                    str(ROOT / "build/ui-preview-deps"), str(ROOT / "src"), str(ROOT / "tools")]))
                with (folder / f"{index}.log").open("w") as log:
                    process = subprocess.Popen([sys.executable, __file__, "--serve", "--platform", platform.id,
                        "--page", str(index), "--port", str(port), "--theme", args.theme, "--scenario", args.scenario],
                        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                    errors, requests_failed, pending, completed_fonts = [], [], set(), set()
                    try:
                        deadline = time.monotonic() + 25
                        while time.monotonic() < deadline:
                            with socket.socket() as probe:
                                if probe.connect_ex(("127.0.0.1", port)) == 0:
                                    break
                            if process.poll() is not None:
                                raise RuntimeError("预览服务提前退出")
                            time.sleep(.2)
                        page = browser.new_page(viewport={"width": 1280, "height": 900})
                        page.route(canvas_origin + "chromium/*", lambda route:
                            route.fulfill(path=str(canvas_cache / route.request.url.rsplit("/", 1)[-1]),
                                          headers={"access-control-allow-origin": "*"},
                                          content_type="application/wasm" if route.request.url.endswith(".wasm")
                                          else "application/javascript"))
                        page.on("pageerror", lambda e: errors.append(str(getattr(e, "stack", "") or e)))
                        page.on("requestfailed", lambda r: requests_failed.append(dict(url=r.url, failure=r.failure)))
                        page.on("request", lambda r: pending.add(r.url))
                        def finished(request):
                            pending.discard(request.url)
                            if request.url.endswith("preview-cjk.ttf"):
                                completed_fonts.add(request.url)
                        page.on("requestfinished", finished)
                        page.on("console", lambda m: errors.append(m.text) if
                                "overflowed" in m.text.lower() or "EXCEPTION CAUGHT" in m.text else None)
                        page.goto(f"http://127.0.0.1:{port}", wait_until="load", timeout=30000)
                        page.wait_for_selector("flt-glass-pane", state="attached", timeout=20000)
                        font_deadline = time.monotonic() + 25
                        while not completed_fonts and time.monotonic() < font_deadline:
                            page.wait_for_timeout(250)
                        if not completed_fonts:
                            raise RuntimeError("本地CJK预览字体尚未载入，不能把缺字截图计为通过")
                        page.wait_for_timeout(1800)
                        page.screenshot(path=str(folder / f"{index}-1280.png"))
                        if args.scenario != "default":
                            page.mouse.move(900, 600)
                            page.mouse.wheel(0, 900 if args.scenario == "forms" else 1600)
                            page.wait_for_timeout(500)
                            page.screenshot(path=str(folder / f"{index}-{args.scenario}-scrolled.png"))
                            page.mouse.wheel(0, -4000)
                            page.wait_for_timeout(300)
                        page.set_viewport_size({"width": 1024, "height": 768})
                        page.wait_for_timeout(500)
                        page.screenshot(path=str(folder / f"{index}-1024.png"))
                        page.close()
                        results.append(dict(page=index, status="PASS" if not errors else "FAIL", errors=errors,
                                            requests_failed=requests_failed))
                        print(f"{platform.id} {args.theme} page={index} {results[-1]['status']}", flush=True)
                    except Exception as error:
                        diagnostics = folder / f"{index}-failure.html"
                        try:
                            diagnostics.write_text(page.content())
                            page.screenshot(path=str(folder / f"{index}-failure.png"))
                        except Exception:
                            pass
                        results.append(dict(page=index, status="FAIL", error=str(error), errors=errors,
                                            requests_failed=requests_failed, pending_requests=sorted(pending)))
                        print(f"{platform.id} page={index} FAIL: {error}", flush=True)
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=6)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
        finally:
            browser.close()
    record = folder / "results.json"
    previous = {r["page"]: r for r in json.loads(record.read_text())} if record.exists() else {}
    previous.update({r["page"]: r for r in results})
    record.write_text(json.dumps(list(previous.values()), ensure_ascii=False, indent=2))
    if any(r["status"] != "PASS" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
