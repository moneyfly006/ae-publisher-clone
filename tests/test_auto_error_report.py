"""出现 ERROR 日志时自动上报授权后台（离线桩，不联网）。"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui_sandbox import build_app, sandbox  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    calls = []
    with sandbox(threads=True), patch("aeclone.core.report.upload",
                          side_effect=lambda *a, **k: calls.append((a, k)) or {"ok": True}):
        app = build_app()
        app.cfg.set("license_server", "https://license.example")
        app.cfg.set("auto_report_errors", True)
        app._report_identity = "TEST-MACHINE"
        app._log("这是一个测试错误", "ERROR")
        for _ in range(30):
            if calls:
                break
            time.sleep(0.1)
        check("ERROR 会自动上报", bool(calls), calls)
        if calls:
            args, kwargs = calls[0]
            check("上报内容含错误日志", "这是一个测试错误" in str(args[2]), str(args[2])[:100])
            check("上报事件为 error", any(e.get("event") == "error" for e in kwargs.get("events", [])),
                  kwargs.get("events"))
        # 同一错误短时间内不重复上报
        app._log("这是一个测试错误", "ERROR")
        time.sleep(0.4)
        check("同一错误去重", len(calls) == 1, len(calls))
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
