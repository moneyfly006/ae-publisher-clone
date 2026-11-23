"""各平台 Cookie 独立：设置某平台专属 Cookie 后，不得串用其他平台。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import AppConfig  # noqa: E402
from aeclone.services.alibaba_publish import make_client as alibaba_client  # noqa: E402
from aeclone.services.amazon_publish import make_client as amazon_client  # noqa: E402
from aeclone.services.tiktok_publish import make_client as tiktok_client  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def jar(client):
    try:
        return dict(client.s.cookies)
    except Exception:
        return {}


def main():
    print("=" * 60)
    cfg = AppConfig()
    cfg.set("alibaba_cookies", "alb=1; shared=alibaba")
    cfg.set("amazon_cookies", "amz=2; shared=amazon")
    cfg.set("tiktok_cookies", "tt=3; shared=tiktok")
    cfg.set("manual_cookies", {"shared": "manual", "dxm": "1"})
    a, m, t = jar(alibaba_client(cfg)), jar(amazon_client(cfg)), jar(tiktok_client(cfg))
    check("Alibaba 用 alibaba_cookies", a.get("alb") == "1" and a.get("shared") == "alibaba", a)
    check("Amazon 用 amazon_cookies", m.get("amz") == "2" and m.get("shared") == "amazon", m)
    check("TikTok 用 tiktok_cookies", t.get("tt") == "3" and t.get("shared") == "tiktok", t)
    check("Alibaba 不串 Amazon Cookie", "amz" not in a, a)
    check("Amazon 不串 TikTok Cookie", "tt" not in m, m)
    check("TikTok 不串 Alibaba Cookie", "alb" not in t, t)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
