"""Actual control events under synchronous and out-of-order service responses."""
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import contextmanager
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import flet as ft
from ui_sandbox import sandbox, build_app, walk
import test_amazon_ui_page as amazon
import test_tiktok_ui_page as tiktok


class NavigationOperations(unittest.TestCase):
    def setUp(self):
        context = sandbox()
        self.cfg = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app = build_app()

    def field(self, label):
        return next(c for c in walk(self.app.content.controls) if isinstance(c, ft.TextField) and (c.label == label or (label == "搜索标题 / SKU / 商品 ID" and c.label == "搜索标题 / 商品 ID")))

    def button(self, label):
        return next(c for c in walk(self.app.content.controls) if getattr(c, "text", "") == label)

    def setup_platform(self, platform):
        self.app._switch_platform(platform)
        self.app._switch(13 if platform == "amazon" else 14)
        return getattr(self.app, "_amz_list_state" if platform == "amazon" else "_tt_list_state")

    def test_search_enter_and_pagination_boundaries(self):
        for platform, helper in (("amazon", amazon), ("tiktok", tiktok)):
            with helper.fake_backend() as backend:
                state = self.setup_platform(platform)
                calls = []
                def listing(*args):
                    calls.append(args)
                    return dict(items=[], page_no=args[1], total_page=3, total=50)
                backend.client.list_products = listing
                search = self.field("搜索标题 / SKU / 商品 ID")
                state.update(page=3, pages=3, keyword="old")
                search.value = "new keyword"
                search.on_change(NS(control=search))
                self.assertEqual(state["page"], 1)
                with helper.sync_threads():
                    search.on_submit(NS(control=search))
                self.assertEqual(calls[-1][1], 1)
                self.assertEqual(calls[-1][4], "new keyword")
                state.update(page=1, pages=3)
                count = len(calls)
                with helper.sync_threads():
                    button = self.button("上一页")
                    button.on_click(NS(control=button))
                self.assertEqual(len(calls), count)
                with helper.sync_threads():
                    button = self.button("下一页")
                    button.on_click(NS(control=button))
                self.assertEqual(calls[-1][1], 2)
                state.update(page=3, pages=3)
                count = len(calls)
                with helper.sync_threads():
                    self.button("下一页").on_click(NS())
                self.assertEqual(len(calls), count)

    def test_slow_old_search_cannot_replace_new_results(self):
        for platform, helper in (("amazon", amazon), ("tiktok", tiktok)):
            with helper.fake_backend() as backend:
                state = self.setup_platform(platform)
                def listing(*args):
                    return dict(items=[dict(id=args[4], title=args[4], productName=args[4])], page_no=1, total_page=1, total=1)
                backend.client.list_products = listing
                search = self.field("搜索标题 / SKU / 商品 ID")
                pending = []
                with patch("threading.Thread.start", lambda thread: pending.append(thread)):
                    for keyword in ("first", "second"):
                        search.value = keyword
                        search.on_submit(NS(control=search))
                pending[1].run()
                pending[0].run()
                self.assertEqual(state["items"][0]["id"], "second")
                self.assertEqual(state["keyword"], "second")

    def test_slow_old_product_load_cannot_overwrite_new_selection(self):
        for platform, helper in (("amazon", amazon), ("tiktok", tiktok)):
            with helper.fake_backend() as backend:
                state = self.setup_platform(platform)
                backend.client.list_products = lambda *a: dict(items=[dict(id="first", title="First", productName="First"),
                    dict(id="second", title="Second", productName="Second")], total=2, total_page=1, page_no=1)
                with helper.sync_threads():
                    search = self.field("搜索标题 / SKU / 商品 ID")
                    search.on_submit(NS(control=search))
                edits = [c for c in walk(self.app.content.controls) if getattr(c, "text", "") == "编辑"]
                original = backend.client.product_detail
                def detail(pid):
                    result = original(pid)
                    result["product"]["id"] = pid
                    result["product"]["title"] = pid
                    result["product"]["productName"] = pid
                    return result
                backend.client.product_detail = detail
                pending = []
                with patch("threading.Thread.start", lambda thread: pending.append(thread)):
                    for edit in edits:
                        edit.on_click(NS(control=edit))
                pending[1].run()
                pending[0].run()
                self.assertEqual(state["selected"], "second")
                self.assertEqual(getattr(self.cfg, platform + "_test_product_id"), "second")

    def test_failed_search_reports_error_without_losing_last_results(self):
        for platform, helper in (("amazon", amazon), ("tiktok", tiktok)):
            with helper.fake_backend() as backend:
                state = self.setup_platform(platform)
                state["items"] = [dict(id="existing", title="Keep this")]
                backend.client.list_products = lambda *a: (_ for _ in ()).throw(RuntimeError("network unavailable"))
                with helper.sync_threads():
                    search = self.field("搜索标题 / SKU / 商品 ID")
                    search.on_submit(NS(control=search))
                self.assertEqual(state["items"][0]["id"], "existing")
                texts = [str(c.value) for c in walk(self.app.content.controls) if isinstance(c, ft.Text)]
                self.assertTrue(any("读取商品失败" in text for text in texts))

    def test_corrupt_batch_settings_render_repair_form(self):
        # Alibaba 六步：价格/规则合并后，可修复预设的功能页是 30「类目与属性」。
        for platform, page in (("amazon", 18), ("tiktok", 25), ("alibaba", 30), ("shopee", 39)):
            self.app._switch_platform(platform)
            self.cfg.set(platform + "_collect_overrides", "{bad json")
            self.app._switch(page)
            self.button("备份并修复预设").on_click(NS())
            self.app.page.dialog.actions[1].on_click(NS())
            self.assertEqual(getattr(self.cfg, platform + "_collect_overrides"), "{}")

    def test_duplicate_confirmation_does_not_stack_dialogs(self):
        self.app._confirm("Run", "same confirmed settings", lambda: None)
        first = self.app._dlg
        count = len(self.app._dlg_stack)
        self.app._confirm("Run", "same confirmed settings", lambda: None)
        self.assertIs(self.app._dlg, first)
        self.assertEqual(len(self.app._dlg_stack), count)

    def test_progress_burst_keeps_every_log_and_coalesces_ui_updates(self):
        from aeclone.services.progress import Event
        self.app._collect_running = "amazon"
        self.app._switch(10)
        with patch.object(self.app, "_log") as log, patch.object(self.app, "_safe_update") as update:
            for index in range(100):
                self.app._on_progress(Event(ts=0, stage="test", action="进行中", detail=str(index)))
            self.app._on_progress(Event(ts=0, stage="test", action="结束"))
        self.assertEqual(log.call_count, 101)
        self.assertLessEqual(update.call_count, 3)

    def test_new_ai_preset_selectors_persist_correct_prompt_keys(self):
        for platform, page, label in (("alibaba", 3, "标题"), ("amazon", 19, "五点描述"),
                                      ("tiktok", 26, "标题"), ("shopee", 41, "标题")):
            self.app._switch_platform(platform)
            self.app._switch(page)
            tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
            controls = list(walk([tabs.tabs[0].content]))
            choice = next(c for c in controls if isinstance(c, ft.Dropdown) and c.label == label + "预设")
            editor = next(c for c in controls if isinstance(c, ft.TextField) and c.label == label + "提示词")
            old = editor.value
            choice.value = "builtin:technical"
            choice.on_change(NS(control=choice))
            self.assertTrue(editor.value.startswith(old))
            self.assertTrue(any("专业" in str(o.text) for o in choice.options),
                            [o.text for o in choice.options])
            self.assertIn("补充表达要求", editor.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
