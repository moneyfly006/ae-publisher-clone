"""每平台「今日刊登成功/失败」计数 + 心跳 publish 字段（task-12）。

不构建 UI：用 object.__new__(App) 只测计数与心跳 payload 构造。
"""
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

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
    app._publish_seen = {}
    app._publish_total = {"success": 0, "fail": 0}
    app._publish_day = today()
    app._publish_lock = threading.Lock()
    app._publish_save_timer = None
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




# ---------------------------------------------------------------- task-30 审计/持久化
class PublishOpFilterTests(unittest.TestCase):
    """哪些保存动作算「刊登」：草稿/移入待发布/发布为草稿 不计。"""

    def test_op_matrix(self):
        app = make_app()
        cases = [
            ("alibaba", "1", "", False), ("alibaba", "3", "", False),
            ("alibaba", "2", "", True),
            ("amazon", "1", "", False), ("amazon", "3", "", False),
            ("amazon", "2", "", True),
            ("tiktok", "1", "", False), ("tiktok", "3", "", False),
            ("tiktok", "2", "AS_DRAFT", False),
            ("tiktok", "2", "", True), ("tiktok", "2", "normal", True),
            ("shopee", "1", "", False), ("shopee", "3", "", False),
            ("shopee", "2", "", True),
            # 速卖通店小秘批量/单发：本身就是提交发布
            ("aliexpress", "2", "", True), ("aliexpress", "", "", True),
        ]
        for pid, op, mode, expected in cases:
            self.assertEqual(App._publish_op_counts(pid, op, mode), expected,
                             f"{pid} op={op} mode={mode}")

    def test_publish_counting_audit_table(self):
        """输出「平台 → 动作 → 是否计数 → 结论」审计表（测试日志可见）。"""
        rows = [
            ("Alibaba", "单品 保存草稿(op=1)/移入待发布(op=3)", "不计", "草稿/待发布非刊登"),
            ("Alibaba", "单品 提交审核/发布(op=2)", "计一次", "res.saved→success，异常→fail"),
            ("Alibaba", "采集箱批量 保存草稿(op=1)", "不计", "草稿"),
            ("Alibaba", "采集箱批量 发布(op=2)", "逐产品一次", "ok→success，fail→fail，skipped 不计"),
            ("Alibaba", "批量前评分检查（只读）", "不计", "只读"),
            ("AliExpress", "单发 _publish_one", "计一次", "batch_publish 结果；异常→fail"),
            ("AliExpress", "已选批量/待发布/重试失败", "逐产品一次", "success/failed ID 拆分"),
            ("AliExpress", "批量管线 _run_pipeline", "逐产品一次", "res.ok；审核中/preview 不计；异常→fail"),
            ("AliExpress", "只重试发布失败（自动修复）", "逐产品一次", "ok/fail；pending 待确认不计"),
            ("Amazon", "保存(op=1)/移入待发布(op=3)", "不计", "草稿"),
            ("Amazon", "保存并发布/重试发布(op=2)", "计一次", "saved+发布状态；异常→fail"),
            ("Amazon", "自动修复发布", "op=2 计一次", "op=1 保存不计；按最终 ok"),
            ("TikTok", "保存(op=1)/移入待发布(op=3)/发布为草稿(AS_DRAFT)", "不计", "非正式刊登"),
            ("TikTok", "发布(op=2)", "计一次", "saved；异常→fail"),
            ("Shopee", "保存草稿(op=1)/移入待发布(op=3)", "不计", "草稿"),
            ("Shopee", "保存并发布(op=2)", "计一次", "result.ok；异常→fail"),
            ("通用", "同一产品多阶段/重试", "仅最终结果一次", "_publish_seen 去重；fail→success 回滚"),
            ("通用", "预览/测试模式、用户取消", "不计", "_publish_counting_allowed / 无结果路径"),
            ("通用", "重启/跨天", "持久化+清零", "config 按天保存；服务端同日取 MAX"),
        ]
        print("\n===== 刊登计数路径审计表 =====")
        for row in rows:
            print(" | ".join(row))
        self.assertTrue(all(all(str(c) for c in row) for row in rows))

    def test_no_direct_increment_in_ui_publish_paths(self):
        """UI 发布路径必须走 _record_publish_result（带产品 ID 去重），不得裸加计数。"""
        src = (Path(__file__).resolve().parents[1]
               / "src/aeclone/ui/app.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("self._count_publish("), 0,
                         "UI 发布路径仍直接调用 _count_publish（可能重复计）")
        self.assertGreaterEqual(src.count("self._record_publish_result("), 12)


class PublishDedupeTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""})
        self._env.start()
        self.app = make_app()

    def tearDown(self):
        self._env.stop()
        self.app._preview_mode = False

    def test_same_result_counted_once(self):
        self.assertTrue(self.app._record_publish_result("alibaba", "P1", True))
        self.assertFalse(self.app._record_publish_result("alibaba", "P1", True))
        self.assertEqual(self.app._publish_counts["alibaba"],
                         {"success": 1, "fail": 0})

    def test_fail_then_success_keeps_final_result(self):
        self.assertTrue(self.app._record_publish_result("amazon", "P1", False))
        self.assertTrue(self.app._record_publish_result("amazon", "P1", True))
        self.assertEqual(self.app._publish_counts["amazon"],
                         {"success": 1, "fail": 0})
        self.assertEqual(self.app._publish_total, {"success": 1, "fail": 0})

    def test_success_then_fail_keeps_final_result(self):
        self.app._record_publish_result("tiktok", "P1", True)
        self.app._record_publish_result("tiktok", "P1", False)
        self.assertEqual(self.app._publish_counts["tiktok"],
                         {"success": 0, "fail": 1})

    def test_multiple_products_accumulate(self):
        for pid in ("P1", "P2", "P3"):
            self.app._record_publish_result("shopee", pid, True)
        self.app._record_publish_result("shopee", "P4", False)
        self.assertEqual(self.app._publish_counts["shopee"],
                         {"success": 3, "fail": 1})

    def test_no_product_id_falls_back_to_attempt_count(self):
        self.app._record_publish_result("aliexpress", "", True)
        self.app._record_publish_result("aliexpress", "", True)
        self.assertEqual(self.app._publish_counts["aliexpress"]["success"], 2)

    def test_preview_mode_never_counts(self):
        self.app._preview_mode = True
        self.assertFalse(self.app._record_publish_result("amazon", "P1", True))
        self.assertNotIn("amazon", getattr(self.app, "_publish_counts", {}))
        self.app._preview_mode = False
        self.assertTrue(self.app._record_publish_result("amazon", "P1", True))

    def test_seen_map_is_capped(self):
        self.app.PUBLISH_SEEN_LIMIT = 3
        for i in range(6):
            self.app._record_publish_result("alibaba", f"P{i}", True)
        self.assertEqual(self.app._publish_counts["alibaba"]["success"], 6)
        self.assertLessEqual(len(self.app._publish_seen["alibaba"]), 3)


class FConfig(dict):
    """轻量 config（支持属性读 + set；不落盘）。"""

    def __getattr__(self, k):
        return self.get(k, "")

    def set(self, k, v):
        self[k] = v


class PublishPersistenceTests(unittest.TestCase):
    def setUp(self):
        # 离线回归环境会设置 AURORA_TEST=1（计数会被安全禁用），这里恢复正式判定
        self._env = patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _app(self, cfg):
        app = make_app()
        app.cfg = cfg
        return app

    def _cancel_timer(self, app):
        timer = getattr(app, "_publish_save_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def test_restart_keeps_today_counts_and_dedupe(self):
        cfg = FConfig(
            publish_counts_day=today(),
            publish_counts=json.dumps({"amazon": {"success": 3, "fail": 1}}),
            publish_seen=json.dumps({"amazon": {"P1": "success"}}),
            publish_total=json.dumps({"success": 7, "fail": 2}),
        )
        app = self._app(cfg)
        app._load_publish_state()
        self.assertEqual(app._publish_counts["amazon"], {"success": 3, "fail": 1})
        self.assertEqual(app._publish_total, {"success": 7, "fail": 2})
        # 重启后同一产品再上报相同结果：不重复计
        self.assertFalse(app._record_publish_result("amazon", "P1", True))
        self.assertTrue(app._record_publish_result("amazon", "P2", True))
        with patch("aeclone.config.save_config"):
            app._flush_publish_state()
        self._cancel_timer(app)
        # 模拟再次重启：从 config 恢复
        app2 = self._app(cfg)
        app2._load_publish_state()
        self.assertEqual(app2._publish_counts["amazon"], {"success": 4, "fail": 1})
        self.assertEqual(app2._publish_total, {"success": 8, "fail": 2})
        self.assertEqual(app2._publish_day, today())

    def test_cross_day_resets_today_keeps_total(self):
        cfg = FConfig(
            publish_counts_day="2000-01-01",
            publish_counts=json.dumps({"alibaba": {"success": 9, "fail": 2}}),
            publish_seen=json.dumps({"alibaba": {"P1": "success"}}),
            publish_total=json.dumps({"success": 20, "fail": 5}),
        )
        app = self._app(cfg)
        app._load_publish_state()
        self.assertEqual(app._publish_counts, {}, "跨天必须清零今日计数")
        self.assertEqual(app._publish_seen, {})
        self.assertEqual(app._publish_total, {"success": 20, "fail": 5},
                         "累计 total 跨天保留")
        self.assertEqual(app._publish_day, today())
        _s, _m, payload = app._heartbeat_payload()
        self.assertEqual(payload["publish"], {})
        self.assertEqual(payload["publish_day"], today())
        self.assertEqual(payload["publish_total"], {"success": 20, "fail": 5})
        self._cancel_timer(app)

    def test_heartbeat_uses_persisted_counts(self):
        cfg = FConfig(
            publish_counts_day=today(),
            publish_counts=json.dumps({"tiktok": {"success": 2, "fail": 1}}),
            publish_seen="{}",
            publish_total=json.dumps({"success": 5, "fail": 1}),
        )
        app = self._app(cfg)
        app._load_publish_state()
        _s, _m, payload = app._heartbeat_payload()
        self.assertEqual(payload["publish"]["tiktok"], {"success": 2, "fail": 1})
        self.assertEqual(payload["publish_day"], today())
        self.assertEqual(payload["publish_total"], {"success": 5, "fail": 1})
        self.assertIn("platforms", payload)
        self.assertIn("products", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
