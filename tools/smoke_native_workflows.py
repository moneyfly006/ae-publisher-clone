"""启动最终本地包并截图验收。只使用独立无凭据配置，禁止商品写入。"""
import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="关闭本次构建创建的 Flet 验收窗口")
    parser.add_argument("--inspect-open", action="store_true", help="只读确认最终包及其Flet窗口进程")
    parser.add_argument("--platform", choices=("alibaba", "amazon", "tiktok", "shopee"), default="shopee")
    parser.add_argument("--page", type=int, default=36)
    parser.add_argument("--skip-screenshot", action="store_true", help="只验证原生启动与错误日志，截图另用 Flet 实际渲染验收")
    args = parser.parse_args()
    from aeclone.config import AppConfig
    from aeclone.licensing import PLATFORM_FEATURES
    latest = json.loads((ROOT / "build/verification/latest-build.json").read_text())
    app = ROOT / latest["build"]
    if args.inspect_open:
        processes = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True)
        rows = []
        for line in processes.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            command = parts[1]
            if command.startswith(str(app / "Contents/MacOS/AuroraList")):
                rows.append(dict(pid=int(parts[0]), component="AuroraList"))
            elif "/Contents/MacOS/Flet " in command and str(app) in command:
                rows.append(dict(pid=int(parts[0]), component="Flet window"))
        result = dict(build=latest["build"], processes=rows)
        (ROOT / "build/ui-redesign/final-open-processes.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.cleanup:
        import signal
        process_list = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True)
        marker = str(app / "Contents/Frameworks/assets")
        closed = []
        for line in process_list.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and "/Contents/MacOS/Flet " in parts[1] and marker in parts[1]:
                pid = int(parts[0])
                try:
                    os.kill(pid, signal.SIGTERM)
                    closed.append(pid)
                except ProcessLookupError:
                    pass
        print(json.dumps({"closed_verification_windows": closed}))
        return
    exe = app / "Contents/MacOS/AuroraList"
    folder = ROOT / "build/ui-redesign/native-final" / args.platform
    config = folder / "config"
    config.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(dict(active_platform=args.platform, start_page=args.page, onboarded=True,
        api_key="", api_base="http://127.0.0.1:9/v1", license_server="http://127.0.0.1:9",
        manual_cookies={"native-ui-smoke": "1"}, alibaba_cookies="", amazon_cookies="",
        tiktok_cookies="", shopee_cookies="native-ui-smoke=1", license_cache_ok=False,
        license_platforms=",".join(f"{pid}:{'+'.join(features)}" for pid, features in PLATFORM_FEATURES.items()),
        ui_theme="light", window_size="1280x900"))
    (config / "config.json").write_text(json.dumps(cfg.as_dict(), ensure_ascii=False))
    (config / "config.json").chmod(0o600)
    env = dict(os.environ, AURORA_CONFIG_DIR=str(config), AURORA_INSTANCE="原生界面验收",
               AURORA_TEST="1", AURORA_NO_SAVE="1", AURORA_SKIP_LICENSE="1", AURORA_PAGE=str(args.page))
    script_root = Path("/Users/apple/.codex/skills/screenshot/scripts")
    result = dict(build=latest["build"], native_launch=False, screenshot=False)
    with (folder / "console.log").open("w") as log:
        process = subprocess.Popen([str(exe), "--page", str(args.page)], cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            time.sleep(8)
            if process.poll() is not None:
                raise RuntimeError("原生程序提前退出，请查看 console.log")
            result["native_launch"] = True
            permission = None if args.skip_screenshot else subprocess.run(
                ["bash", str(script_root / "ensure_macos_permissions.sh")], capture_output=True, text=True, timeout=35)
            if args.skip_screenshot:
                result["capture_output"] = "本次仅检查原生启动，截图使用独立 Flet 渲染验收。"
            elif permission.returncode == 0:
                try:
                    capture = subprocess.run([sys.executable, str(script_root / "take_screenshot.py"),
                        "--app", "AuroraList", "--mode", "temp"], capture_output=True, text=True, timeout=55)
                    result["screenshot"] = capture.returncode == 0
                    result["capture_output"] = capture.stdout.strip() or capture.stderr.strip()
                except subprocess.TimeoutExpired:
                    result["capture_output"] = "系统截图工具超时；原生进程已正常启动，页面另有 Flet 实际渲染截图。"
            else:
                result["capture_output"] = "系统未授予截图权限；原生进程已正常启动"
            error_log = config / "error.log"
            result["runtime_errors"] = error_log.read_text() if error_log.exists() else ""
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    if result.get("runtime_errors") or not result["native_launch"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
