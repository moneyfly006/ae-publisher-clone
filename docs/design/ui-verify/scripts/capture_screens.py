"""task-5 截图：逐页起只读预览服务 + playwright(chrome headless) 截图。

用法: .venv/bin/python /tmp/capture_screens.py [--only name[,name]]
输出: docs/design/ui-verify/*.png (+ manifest json)
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/apple/Projects/alibaba-lister")
OUT = ROOT / "docs" / "design" / "ui-verify"
PY = str(ROOT / ".venv" / "bin" / "python")
SHOT = str(Path(__file__).resolve().parent / "shot_ui.py")
VIEWPORT = {"width": 1560, "height": 2200}

SHOTS = [
    # name, page, tab, theme, platform, lock
    ("00-overview-alibaba-dark", 0, 0, "dark", "alibaba", 0),
    ("01-store-templates-dark", 1, 0, "dark", "alibaba", 0),
    ("02-category-attributes-dark", 2, 0, "dark", "alibaba", 0),
    ("03-price-tier-wholesale-dark", 6, 0, "dark", "alibaba", 0),
    ("04-sku-inventory-dark", 7, 0, "dark", "alibaba", 0),
    ("05-publish-queue-alibaba-dark", 8, 0, "dark", "alibaba", 0),
    ("06-ai-text-dark", 3, 0, "dark", "alibaba", 0),
    ("07-ai-image-dark", 5, 0, "dark", "alibaba", 0),
    ("08-content-review-dark", 4, 0, "dark", "alibaba", 0),
    ("09-tasks-logs-dark", 10, 0, "dark", "alibaba", 0),
    ("10-image-workbench-dark", 9, 0, "dark", "alibaba", 0),
    ("11-settings-dark", 11, 0, "dark", "alibaba", 0),
    # 12 号页 5 个 Tab
    ("12-alibaba-listing-tab1-connect-products", 12, 0, "dark", "alibaba", 0),
    ("12-alibaba-listing-tab2-category", 12, 1, "dark", "alibaba", 0),
    ("12-alibaba-listing-tab3-presets", 12, 2, "dark", "alibaba", 0),
    ("12-alibaba-listing-tab4-price-trade", 12, 3, "dark", "alibaba", 0),
    ("12-alibaba-listing-tab5-preview-save", 12, 4, "dark", "alibaba", 0),
    # 主题 / 平台 / 锁定态证据
    ("13-overview-alibaba-light", 0, 0, "light", "alibaba", 0),
    ("13-alibaba-listing-tab1-light", 12, 0, "light", "alibaba", 0),
    ("14-overview-aliexpress-dark", 0, 0, "dark", "aliexpress", 0),
    ("15-category-locked-dark", 2, 0, "dark", "alibaba", 1),
]


def port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_port(port, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def capture_one(pw, name, page, tab, theme, platform, lock, port):
    env = dict(os.environ)
    env.update(SHOT_PAGE=str(page), SHOT_TAB=str(tab), SHOT_THEME=theme,
               SHOT_PLATFORM=platform, SHOT_PORT=str(port), AURORA_LOCK=str(lock),
               PYTHONPATH=str(ROOT / "build" / "ui-preview-deps"),
               AURORA_WINDOW="1514x945")
    log = open(f"/tmp/shot-{name}.log", "w")
    proc = subprocess.Popen([PY, SHOT], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_port(port):
            raise RuntimeError(f"预览服务端口 {port} 未就绪（见 /tmp/shot-{name}.log）")
        browser = pw.chromium.launch(channel="chrome", headless=True,
                                     args=["--force-device-scale-factor=1"])
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{port}", wait_until="load", timeout=45000)
        try:
            pg.wait_for_selector("canvas", timeout=25000)
        except Exception:
            pass
        pg.wait_for_timeout(3500)      # CanvasKit 字体/布局稳定
        pg.screenshot(path=str(OUT / f"{name}.png"))
        browser.close()
        return dict(name=name, file=f"{name}.png", page=page, tab=tab, theme=theme,
                    platform=platform, locked=bool(lock), ok=True,
                    bytes=(OUT / f"{name}.png").stat().st_size)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    shots = [s for s in SHOTS if not only or s[0] in only]
    OUT.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    manifest = []
    with sync_playwright() as pw:
        port = 8891
        for shot in shots:
            name, page, tab, theme, platform, lock = shot
            while not port_free(port):
                port += 1
            t0 = time.time()
            try:
                row = capture_one(pw, name, page, tab, theme, platform, lock, port)
            except Exception as ex:  # noqa: BLE001
                row = dict(name=name, ok=False, error=f"{type(ex).__name__}: {ex}")
            row["elapsed_s"] = round(time.time() - t0, 1)
            manifest.append(row)
            print(("OK   " if row.get("ok") else "FAIL ") + name +
                  f" ({row['elapsed_s']}s)" + ("" if row.get("ok") else f" :: {row.get('error')}"),
                  flush=True)
            port += 1
    (OUT / "manifest.json").write_text(
        json.dumps(dict(viewport=VIEWPORT, shots=manifest), ensure_ascii=False, indent=1),
        encoding="utf-8")
    ok = sum(1 for r in manifest if r.get("ok"))
    print(f"\n{ok}/{len(manifest)} screenshots -> {OUT}")
    return 0 if ok == len(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
