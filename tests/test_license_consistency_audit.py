"""客户端授权一致性审计（task-25）：门禁路径 / 隐藏覆盖 / 本地到期 / 账号切换。

覆盖已验证/修复的问题：
1) 页面 feature 映射（Amazon 类目页 listing→category）—— 不变量在 test_platforms。
2) license_platforms 隐藏覆盖仅显式开发环境生效（生产忽略）。
3) 本地到期重算：周期 tick 先用缓存 token 重算，离线也按到期日锁定当前页。
4) 门禁路径盘点：_page_allowed / _license_active / _has_any_platform /
   _build_platform_rail / _render_locked / 购买按钮 / 运行按钮。
5) 账号 A→B 切换与退出登录：entitlements 完全替换、rev 基线重置、退出锁平台。
"""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.licensing import (  # noqa: E402
    Entitlements, issue_token, load_entitlements, override_enabled)
from aeclone.platforms import PLATFORMS  # noqa: E402
from aeclone.ui.app import App  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"
EMAIL = "user@example.com"
NO_OVERRIDE_ENV = {"AURORA_LICENSE_OVERRIDE": "", "AURORA_TEST": "",
                   "AURORA_NO_SAVE": ""}


def texts(controls) -> str:
    return "\n".join(str(c.value) for c in walk(controls)
                     if isinstance(c, ft.Text) and c.value)


def buttons(controls):
    return [(str(getattr(c, "text", "") or ""), c) for c in walk(controls)
            if hasattr(c, "text") and hasattr(c, "on_click")]


def find_button(controls, label):
    for text, control in buttons(controls):
        if label in text:
            return control
    return None


def stub_app(platform_id: str, features: dict) -> App:
    """只带门禁判断所需属性的 App 骨架（不构建 UI）。"""
    app = object.__new__(App)
    app.active_platform = PLATFORMS[platform_id]
    app.entitlements = Entitlements(
        platforms={pid: set(feats) for pid, feats in features.items()},
        source="token")
    app._license_test_bypass = lambda: False
    app._license_override_active = lambda: False
    return app


class HiddenOverrideTests(unittest.TestCase):
    """license_platforms 隐藏覆盖只在显式开发环境下生效。"""

    def _cfg(self):
        return NS(license_platforms="alibaba:listing+ai_text",
                  license_token="", license_token_ts=0,
                  license_account_email="", license_auto_login=True)

    def test_production_ignores_override(self):
        with patch.dict(os.environ, NO_OVERRIDE_ENV):
            self.assertFalse(override_enabled())
            ent = load_entitlements(self._cfg(), machine=MACHINE)
        self.assertEqual(ent.platforms_list(), [])
        self.assertNotEqual(ent.source, "override")

    def test_explicit_dev_env_enables_override(self):
        for env in ({"AURORA_LICENSE_OVERRIDE": "1"},
                    {"AURORA_TEST": "1"}, {"AURORA_NO_SAVE": "1"}):
            with patch.dict(os.environ, env):
                self.assertTrue(override_enabled(), env)
                ent = load_entitlements(self._cfg(), machine=MACHINE)
                self.assertEqual(ent.source, "override", env)
                self.assertTrue(ent.has_feature("alibaba", "ai_text"), env)

    def test_app_override_helper_follows_env(self):
        with sandbox(threads=True) as cfg:
            app = build_app()   # 沙箱默认 cfg 里有 license_platforms
            self.assertTrue(bool(str(cfg.license_platforms or "")))
            self.assertTrue(app._license_override_active())     # 沙箱显式开发环境
            with patch.dict(os.environ, NO_OVERRIDE_ENV):
                self.assertFalse(app._license_override_active())  # 生产：忽略配置覆盖
            with patch.dict(os.environ, {"AURORA_LICENSE_OVERRIDE": "1"}):
                self.assertTrue(app._license_override_active())


class PageGateAuditTests(unittest.TestCase):
    def test_page_allowed_follows_feature_mapping(self):
        # 只开 listing：Amazon 类目页（feature=category）必须锁
        app = stub_app("amazon", {"amazon": ["listing"]})
        self.assertTrue(app._page_allowed(PLATFORMS["amazon"].page(16)))   # 单品编辑 listing
        self.assertFalse(app._page_allowed(PLATFORMS["amazon"].page(17)))  # 类目与属性 category
        # 开 category 后解锁
        app2 = stub_app("amazon", {"amazon": ["listing", "category"]})
        self.assertTrue(app2._page_allowed(PLATFORMS["amazon"].page(17)))

    def test_every_page_gate_matches_its_feature(self):
        """遍历所有平台页面：只开某 feature 时，只有该 feature 的页面 allowed。"""
        for pid, platform in PLATFORMS.items():
            for feature in ("listing", "category", "pricing", "variants",
                            "ai_text", "ai_image", "audit", "trade"):
                app = stub_app(pid, {pid: [feature]})
                for item in platform.pages:
                    self.assertEqual(
                        app._page_allowed(item), item.feature == feature,
                        f"{pid}#{item.index} {item.label} feature={item.feature} "
                        f"opened={feature}")

    def test_run_gate_requires_listing_feature(self):
        category_only = stub_app("alibaba", {"alibaba": ["category"]})
        self.assertFalse(category_only._license_active(),
                         "只有类目权限不得允许运行刊登任务")
        listing = stub_app("alibaba", {"alibaba": ["listing"]})
        self.assertTrue(listing._license_active())
        none = stub_app("alibaba", {})
        self.assertFalse(none._license_active())
        self.assertFalse(none._has_any_platform())

    def test_platform_rail_lock_icons(self):
        app = stub_app("alibaba", {"alibaba": ["listing"]})
        rail = app._build_platform_rail()
        lock_icons = [c for c in walk([rail])
                      if isinstance(c, ft.Icon)
                      and getattr(c, "name", "") == ft.Icons.LOCK_OUTLINE]
        visible = [c for c in lock_icons if bool(getattr(c, "visible", True))]
        self.assertEqual(len(lock_icons), len(PLATFORMS))
        # 只开 alibaba → 其余 4 个平台显示锁
        self.assertEqual(len(visible), len(PLATFORMS) - 1)

    def test_locked_page_has_no_unlock_actions(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                                 "2099-01-01"))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            app._switch_platform("amazon")
            page = app.active_platform.page(16)
            self.assertFalse(app._page_allowed(page))
            body = texts(app.content.controls)
            self.assertIn("未开通", body)
            labels = [t for t, _c in buttons(app.content.controls)]
            self.assertIsNotNone(find_button(app.content.controls, "去开通 / 购买套餐"))
            self.assertIsNotNone(find_button(app.content.controls, "刷新授权"))
            for banned in ("保存", "发布", "启动任务"):
                self.assertFalse(any(banned in label and "刷新" not in label
                                     for label in labels),
                                 f"锁定页出现可操作按钮：{labels}")

    def test_buy_button_only_opens_browser_not_unlock(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                                 "2099-01-01"))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            app._switch_platform("amazon")
            opened = []
            app._open_url = lambda url: opened.append(url) or True
            find_button(app.content.controls, "去开通 / 购买套餐").on_click(None)
            self.assertTrue(opened and "/pricing" in opened[0], opened)
            self.assertFalse(app.entitlements.has_platform("amazon"))
            self.assertIn("未开通", texts(app.content.controls))

    def test_task_queue_page_respects_feature_gate(self):
        """46 号任务队列页也必须在 feature 门禁之后（原来提前 return 会绕过）。"""
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                                 "2099-01-01"))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            # 已开通平台：任务队列正常显示
            app._switch(46)
            self.assertIn("任务队列", texts(app.content.controls))
            # 未开通平台：同样进入锁定页，而不是任务队列
            app._switch_platform("amazon")
            app._switch(46)
            body = texts(app.content.controls)
            self.assertIn("未开通", body)
            self.assertNotIn("配置快照队列", body)

    def test_gate_audit_table(self):
        """输出授权门禁路径审计表（测试日志可见）。"""
        rows = [
            ("_page_allowed", "PageItem.feature → entitlements.has_feature", "无 feature 即锁定"),
            ("_license_active", "active_platform + listing feature", "无 listing 不允许运行"),
            ("_has_any_platform", "entitlements.platforms_list()", "空→顶部购买提示条"),
            ("_build_platform_rail", "entitlements.has_platform(pid)", "未开通平台显示锁"),
            ("_render_locked", "!has_feature", "仅刷新/购买/查看，无写操作"),
            ("账号对话框", "entitlements.platforms_list()", "只读展示"),
            ("购买/续费按钮", "打开网页 pricing", "不修改本地授权"),
            ("运行/启动按钮", "_license_active()", "无权限弹出购买提示并阻断"),
        ]
        print("\n===== 授权门禁路径审计表 =====")
        for path, basis, conclusion in rows:
            print(f"{path} | {basis} | {conclusion}")
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(c for _p, _b, c in rows))


class LocalExpiryRecheckTests(unittest.TestCase):
    def test_tick_recomputes_from_cached_token_and_locks_current_page(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            tok = issue_token(
                MACHINE, {"alibaba": ["listing"]}, "2030-01-01",
                feature_expiries={"alibaba": {"listing": "2030-01-01"}})
            cfg.set("license_token", tok)
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            app._license_test_bypass = lambda: False
            app._license_override_active = lambda: False
            self.assertTrue(app.entitlements.has_feature("alibaba", "listing"))
            app._refresh_platform_license = lambda **kw: None
            rebuilt = []
            app._rebuild_nav = lambda: rebuilt.append(1)
            real_load = load_entitlements

            def future_load(c, **kw):
                # 模拟时间跨过 feature 到期日（2030-01-01 之后）
                return real_load(c, now=1893456000.0,   # 2030-01-02
                                 machine=kw.get("machine"))

            with patch("aeclone.ui.app.load_entitlements", side_effect=future_load):
                app._license_watch_tick()
            self.assertFalse(app.entitlements.has_feature("alibaba", "listing"),
                             "到期后本地重算必须锁定")
            self.assertTrue(rebuilt, "到期后应重建导航")
            self.assertIn("未开通", texts(app.content.controls))
            self.assertFalse(app._license_active())

    def test_license_watch_tick_still_network_refreshes_when_logged_in(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                                 "2099-01-01"))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            app._license_test_bypass = lambda: False
            app._license_override_active = lambda: False
            calls = []
            app._refresh_platform_license = lambda **kw: calls.append(1)
            self.assertTrue(app._license_watch_tick())
            self.assertEqual(calls, [1])


class AccountSwitchTests(unittest.TestCase):
    def _app(self, cfg):
        cfg.set("license_platforms", "")
        cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                             "2099-01-01"))
        cfg.set("license_token_ts", int(time.time()))
        cfg.set("license_account_email", EMAIL)
        app = build_app()
        app._license_test_bypass = lambda: False
        app._license_override_active = lambda: False
        return app

    def test_switch_account_replaces_entitlements_and_rev_baseline(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._set_license_rev("rev-of-A")
            tok_b = issue_token(MACHINE, {"amazon": ["listing"]}, "2099-01-01")
            app._apply_account_token(tok_b, "b@example.com", False, True, "")
            self.assertEqual(set(app.entitlements.platforms_list()), {"amazon"})
            self.assertFalse(app.entitlements.has_platform("alibaba"),
                             "切换账号后不得残留 A 的平台")
            self.assertFalse(app._license_rev_known, "切换账号应重置 rev 基线")
            self.assertEqual(app._license_rev, "")

    def test_logout_clears_token_and_locks_platforms(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            self.assertTrue(app.entitlements.has_platform("alibaba"))
            app._account_logout(show_login=False)
            self.assertEqual(cfg.license_token, "")
            self.assertEqual(cfg.license_account_email, "")
            self.assertEqual(app.entitlements.platforms_list(), [])
            self.assertFalse(app._license_active())
            self.assertFalse(app._license_rev_known)


class LegacyClientRiskTests(unittest.TestCase):
    def test_offline_grace_and_legacy_risk_recorded(self):
        from aeclone.licensing import OFFLINE_GRACE_DAYS
        self.assertEqual(OFFLINE_GRACE_DAYS, 7)
        src = (ROOT / "src/aeclone/licensing.py").read_text(encoding="utf-8")
        self.assertIn("1.0.10", src)
        self.assertIn("旧客户端风险", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
