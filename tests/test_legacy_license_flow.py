"""旧版设备授权码升级：不强制登录、平台兼容、旧码仍被接受。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui_sandbox import build_app, sandbox  # noqa: E402
from aeclone.licensing import load_entitlements  # noqa: E402

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
        app.cfg.set("license_identity", "OLD-CODE-123")
        app.cfg.set("license_cache_ok", True)
        app.cfg.set("license_cache_legacy", True)
        app.cfg.set("license_cache_ts", 0)
        app.cfg.set("license_platforms", "")   # 模拟旧客户没有手动平台覆盖
        app._maybe_license_login()
        check("有旧授权 ID 时不弹登录", getattr(app, "_dlg", None) is None,
              getattr(app, "_dlg", None))
        ent = load_entitlements(app.cfg)
        check("旧授权迁移期开通 Alibaba", ent.has_platform("alibaba"), ent.platforms_list())
        check("旧授权迁移期开通速卖通", ent.has_platform("aliexpress"), ent.platforms_list())
        check("旧授权迁移期开通全部平台",
              all(ent.has_platform(p) for p in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")),
              ent.platforms_list())
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
