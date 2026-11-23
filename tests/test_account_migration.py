"""旧授权客户 → 账号绑定迁移：全平台、记住密码、下次登录。"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Crypto.Cipher import AES  # noqa: E402,F401  (preload; sandbox blocks subprocess)
import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.account_store import load_password, save_password  # noqa: E402
from aeclone.license import machine_code  # noqa: E402
from aeclone.licensing import issue_token, load_entitlements  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    with sandbox() as cfg:
        check("密码加密后不是明文", save_password(cfg, "secret-123456")
              and "secret-123456" not in cfg.license_account_password)
        check("密码可解密", load_password(cfg) == "secret-123456", load_password(cfg))
        cfg.set("license_account_save_password", False)

    with sandbox(threads=True):
        app = build_app()
        app.cfg.set("license_identity", "OLD-LEGACY-ID")
        app.cfg.set("license_account_bound", False)
        app.entitlements = load_entitlements(app.cfg)
        check("迁移期全部平台可见",
              all(app.entitlements.has_platform(p)
                  for p in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")),
              app.entitlements.platforms_list())
        app._show_migration_prompt()
        dlg = app._dlg
        check("运行后弹绑定账号窗口", dlg is not None)
        if dlg:
            title = str(getattr(dlg.title, "value", "") or "")
            check("窗口标题为绑定授权账号", title == "绑定授权账号", title)
            labels = [getattr(c, "text", "") for c in walk([dlg])]
            check("窗口有保存密码选项",
                  any("保存密码" in str(getattr(c, "label", "")) for c in walk([dlg])),
                  labels[:5])

    # 模拟绑定成功后：账号绑定标记、保存密码、全部平台
    with sandbox(threads=True):
        app = build_app()
        app._account_password_for_save = "pass-123456"
        tok = issue_token(machine_code(), {
            "alibaba": ["listing"], "aliexpress": ["listing"], "amazon": ["listing"],
            "tiktok": ["listing"], "shopee": ["listing"]}, "2030-01-01")
        with patch("aeclone.account_store.save_password", return_value=True):
            app._apply_account_token(tok, "user@example.com", True)
        check("绑定后标记账号已绑定", bool(app.cfg.license_account_bound))
        check("绑定后保存邮箱", app.cfg.license_account_email == "user@example.com")
        check("绑定后全部平台", all(app.entitlements.has_platform(p)
                                  for p in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")),
              app.entitlements.platforms_list())
        app._maybe_license_login()
        check("已绑定启动要求登录", app._dlg is not None)


    # 模拟旧客户在弹窗里绑定账号
    with sandbox(threads=True):
        app = build_app()
        app.cfg.set("license_server", "https://license.example.invalid")
        app.cfg.set("license_identity", "OLD-LEGACY-ID")
        app.entitlements = load_entitlements(app.cfg)
        app._show_migration_prompt()
        dlg = app._dlg
        fields = [c for c in walk([dlg]) if isinstance(c, ft.TextField)]
        for field in fields:
            if getattr(field, "label", "") == "邮箱":
                field.value = "migrate@example.com"
            elif getattr(field, "label", "") == "密码":
                field.value = "pass-123456"
        for cb in walk([dlg]):
            if isinstance(cb, ft.Checkbox) and "保存密码" in str(getattr(cb, "label", "")):
                cb.value = True
        token = issue_token(machine_code(), {
            "alibaba": ["listing"], "aliexpress": ["listing"], "amazon": ["listing"],
            "tiktok": ["listing"], "shopee": ["listing"]}, "2030-01-01")
        with patch("aeclone.license_server.software_bind", return_value=(token, "", {})):
            dlg.actions[0].on_click(None)
            time.sleep(1)
        check("弹窗绑定后账号已绑定", bool(app.cfg.license_account_bound))
        check("弹窗绑定后邮箱已保存", app.cfg.license_account_email == "migrate@example.com")
        check("弹窗绑定后全部平台", all(app.entitlements.has_platform(p)
                                      for p in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")),
              app.entitlements.platforms_list())


    # 下次启动：保存密码 → 自动登录，无需手动输入
    with sandbox(threads=True):
        app = build_app()
        app.cfg.set("license_server", "https://license.example.invalid")
        app.cfg.set("license_account_bound", True)
        app.cfg.set("license_account_email", "auto@example.com")
        save_password(app.cfg, "pass-123456")
        app.cfg.set("license_account_save_password", True)
        token = issue_token(machine_code(), {
            "alibaba": ["listing"], "aliexpress": ["listing"], "amazon": ["listing"],
            "tiktok": ["listing"], "shopee": ["listing"]}, "2030-01-01")
        with patch("aeclone.license_server.software_login", return_value=(token, "", {})):
            app._maybe_license_login()
            time.sleep(1.2)
        check("保存密码后自动登录", bool(app.cfg.license_token), app.cfg.license_token[:20])
        check("自动登录后全部平台", all(app.entitlements.has_platform(p)
                                      for p in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")),
              app.entitlements.platforms_list())


    # 启动后计时器触发迁移提示（用 0 秒模拟“运行一段时间”）
    with sandbox(threads=True):
        app = build_app()
        app.cfg.set("license_identity", "OLD-LEGACY-ID")
        app.cfg.set("license_server", "https://license.example.invalid")
        app.entitlements = load_entitlements(app.cfg)
        with patch.dict("os.environ", {"AURORA_MIGRATION_DELAY": "0"}):
            app._maybe_license_login()
            time.sleep(0.6)
        check("计时器到点弹迁移绑定", app._dlg is not None)

    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
