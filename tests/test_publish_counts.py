"""每平台「今日刊登成功/失败」计数 + 心跳 publish 字段（task-12）。

不构建 UI：用 object.__new__(App) 只测计数与心跳 payload 构造。
"""
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.ui.app import App  # noqa: E402


def make_app(platforms=("alibaba", "amazon"), active="alibaba"):
    """无 UI 的 App 骨架：只带计数/心跳需要的最小属性。"""
    app = object.__new__(App)
    app.cfg = NS(license_server="https://lic.test", store_name="")
    app.entitlements = NS(platforms_list=lambda: list(platforms))
    app.active_platform = NS(id=active)
    app.products = []
    app._running = False
    app.machine_code = lambda: "MACHINE-TEST"
    # 与 App.__init__ 一致
    app._publish_counts = {}
    app._publish_total = {"success": 0, "fail": 0}
    app._publish_day = today()
    app._publish_lock = threading.Lock()
    return app


def today():
    return time.strftime("%Y-%m-%d")


class PublishCounterTests(unittest.TestCase):
    def test_count_accumulates_per_platform(self):
        app = make_app()
        app._count_publish("alibaba", True)
        app._count_publish("alibaba", True)
        app._count_publish("alibaba", False)
        app._count_publish("amazon", False)
        self.assertEqual(app._publish_counts["alibaba"], {"success": 2, "fail": 1})
        self.assertEqual(app._publish_counts["amazon"], {"success": 0, "fail": 1})
        self.assertEqual(app._publish_total, {"success": 2, "fail": 2})

    def test_count_falls_back_to_active_platform(self):
        app = make_app(active="amazon")
        app._count_publish("", True)
        self.assertEqual(app._publish_counts["amazon"], {"success": 1, "fail": 0})

    def test_add_publish_bulk_and_zero_noop(self):
        app = make_app()
        app._add_publish("alibaba", success=7, fail=2)
        app._add_publish("amazon", 0, 0)           # 用户取消/未选：不写任何记录
        self.assertEqual(app._publish_counts["alibaba"], {"success": 7, "fail": 2})
        self.assertNotIn("amazon", app._publish_counts)
        self.assertEqual(app._publish_total, {"success": 7, "fail": 2})

    def test_negative_counts_are_clamped(self):
        app = make_app()
        app._add_publish("alibaba", success=-5, fail=-3)
        self.assertNotIn("alibaba", getattr(app, "_publish_counts", {}))

    def test_day_initialized_and_reset_flag(self):
        app = make_app()
        self.assertFalse(app._ensure_publish_day())
        app._count_publish("alibaba", True)
        app._publish_day = "2020-01-01"
        self.assertTrue(app._ensure_publish_day())
        self.assertEqual(app._publish_counts, {})
        app._count_publish("alibaba", True)
        self.assertEqual(app._publish_counts["alibaba"], {"success": 1, "fail": 0})

    def test_thread_safe_increment(self):
        app = make_app()
        app._count_publish("alibaba", True)        # 先创建锁/结构

        def work():
            for _ in range(100):
                app._count_publish("alibaba", True)

        threads = [threading.Thread(target=work) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(app._publish_counts["alibaba"]["success"], 1001)


class HeartbeatPublishPayloadTests(unittest.TestCase):
    def test_payload_contains_publish_and_keeps_old_fields(self):
        app = make_app()
        app._count_publish("alibaba", True)
        app._count_publish("alibaba", False)
        app._count_publish("alibaba", False)
        app._count_publish("amazon", True)
        server, machine, payload = app._heartbeat_payload()
        self.assertEqual(server, "https://lic.test")
        self.assertEqual(machine, "MACHINE-TEST")
        # 原有字段保留
        self.assertIn("platforms", payload)
        self.assertIn("products", payload)
        self.assertEqual(payload["platforms"]["alibaba"]["status"], "空闲")
        self.assertEqual(payload["platforms"]["amazon"]["today"], 0)
        # 新字段
        self.assertEqual(payload["publish"]["alibaba"], {"success": 1, "fail": 2})
        self.assertEqual(payload["publish"]["amazon"], {"success": 1, "fail": 0})
        self.assertEqual(payload["publish_total"], {"success": 2, "fail": 2})
        self.assertEqual(payload["publish_day"], today())

    def test_payload_publish_empty_without_counts(self):
        app = make_app()
        _s, _m, payload = app._heartbeat_payload()
        self.assertEqual(payload["publish"], {})
        self.assertEqual(payload["publish_total"], {"success": 0, "fail": 0})

    def test_only_platforms_with_data_in_publish(self):
        app = make_app()
        app._count_publish("tiktok", True)
        _s, _m, payload = app._heartbeat_payload()
        self.assertEqual(set(payload["publish"]), {"tiktok"})
        self.assertEqual(payload["publish"]["tiktok"], {"success": 1, "fail": 0})

    def test_cross_day_heartbeat_resets_counts(self):
        app = make_app()
        app._count_publish("alibaba", True)
        app._publish_counts["alibaba"] = {"success": 50, "fail": 10}   # 昨天累计
        app._publish_day = "2020-01-01"
        _s, _m, payload = app._heartbeat_payload()
        self.assertEqual(payload["publish"], {})
        self.assertEqual(getattr(app, "_publish_counts"), {})
        self.assertEqual(payload["publish_day"], today())
        # 当日继续计数从 0 开始
        app._count_publish("alibaba", False)
        _s2, _m2, payload2 = app._heartbeat_payload()
        self.assertEqual(payload2["publish"]["alibaba"], {"success": 0, "fail": 1})

    def test_heartbeat_never_raises_without_init_attrs(self):
        """极端情况：stub/老对象没有计数属性时也要能发心跳。"""
        app = object.__new__(App)
        app.cfg = NS(license_server="", store_name="")
        app.entitlements = NS(platforms_list=lambda: [])
        app.active_platform = NS(id="alibaba")
        app.products = []
        app._running = False
        app.machine_code = lambda: "M"
        _s, _m, payload = app._heartbeat_payload()
        self.assertIn("publish", payload)
        self.assertEqual(payload["publish"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
