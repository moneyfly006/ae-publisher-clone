"""跨平台功能归属与切页状态回归；沙箱阻断网络及真实发布。"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk
from aeclone.ui import app as ui
from aeclone.platforms import ALIEXPRESS


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        context = sandbox()
        self.cfg = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app = build_app()

    def controls(self):
        return list(walk(self.app.content.controls))

    def test_alibaba_cookie_is_compact_and_connection_only_reads_shops(self):
        self.cfg.set("alibaba_shop_id", "old-store")
        self.cfg.set("alibaba_test_product_id", "old-item")
        self.cfg.set("manual_cookies", {"shared": "original"})
        self.app._switch(1)
        field = next(c for c in self.controls() if isinstance(c, ft.TextField) and "Cookie" in c.label)
        self.assertFalse(field.multiline)
        self.assertTrue(field.dense)
        self.assertTrue(field.password)
        field.value = "ali=test"
        field.on_change(SimpleNamespace(control=field))
        self.assertEqual(self.cfg.alibaba_cookies, "ali=test")
        self.assertEqual(self.cfg.manual_cookies, {"shared": "original"})
        client = Mock()
        client.shops.return_value = [{"id": "new-store", "name": "Example Store"}]
        with patch("aeclone.services.alibaba_publish.make_client", return_value=client), \
             patch("threading.Thread.start", lambda thread: thread.run()):
            next(c for c in self.controls() if getattr(c, "text", "") == "检测连接").on_click(None)
        client.shops.assert_called_once()
        client.product_detail.assert_not_called()
        self.assertEqual(self.cfg.alibaba_shop_id, "new-store")
        self.assertEqual(self.cfg.alibaba_test_product_id, "")

    def test_alibaba_manual_workspace_changes_product_and_preserves_drafts(self):
        self.cfg.set("alibaba_cookies", "ali=test")
        client = Mock()
        client.product_detail.side_effect = lambda pid: {"idStr": pid, "subject": f"Original {pid}"}
        with patch("aeclone.integrations.alibaba.AlibabaClient", return_value=client), \
             patch("threading.Thread.start", lambda thread: thread.run()):
            self.app._switch(12)
            field = next(c for c in self.controls() if getattr(c, "label", "") == "按商品 ID 打开（可空）")
            open_item = next(c for c in self.controls() if getattr(c, "text", "") == "按 ID 打开")
            self.assertFalse(any(getattr(c, "text", "") == "保存草稿" for c in self.controls()))
            for pid, expected in (("A", "Original A"), ("B", "Original B"), ("A", "Edited A")):
                field.value = pid
                open_item.on_click(None)
                title = next(c for c in self.controls() if getattr(c, "label", "") == "商品标题（最多 128 字符）")
                self.assertEqual(title.value, expected)
                if pid == "A" and expected == "Original A":
                    title.value = "Edited A"
                    title.on_change(SimpleNamespace(control=title))
        self.assertEqual([call.args[0] for call in client.product_detail.call_args_list], ["A", "B", "A"])

    def test_alibaba_pages_have_only_their_own_actions(self):
        expected = {30: "保存批量属性", 32: "保存阶梯报价", 33: "开始自动处理"}
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            for index, action in expected.items():
                with self.subTest(theme=theme, page=index):
                    self.app._switch(index)
                    self.assertEqual(self.app._current, index)
                    labels = [getattr(c, "text", "") for c in self.controls()]
                    self.assertIn(action, labels)
                    for other_index, other_action in expected.items():
                        if other_index != index:
                            self.assertNotIn(other_action, labels)
        self.app._switch(30)
        self.assertIn("保存批量属性", [getattr(c, "text", "") for c in self.controls()])
        self.app._switch(31)
        self.assertNotIn("保存批量属性", [getattr(c, "text", "") for c in self.controls()])

    def test_alibaba_price_survives_navigation_and_next_step_works(self):
        self.app._switch(44)
        tabs = next(c for c in self.controls() if isinstance(c, ft.Tabs))
        tabs.selected_index = 3
        tabs.on_change(SimpleNamespace(control=tabs))
        field = next(c for c in self.controls()
                     if isinstance(c, ft.TextField) and c.label == "统一售价（可选）")
        field.value = "126.75"
        field.on_change(SimpleNamespace(control=field))
        next_step = next(c for c in self.controls() if getattr(c, "text", "") == "去运行与结果")
        next_step.on_click(None)
        self.assertEqual(self.app._current, 33)
        self.app._switch(32)
        field = next(c for c in self.controls()
                     if isinstance(c, ft.TextField) and c.label == "统一售价（可选）")
        self.assertEqual(field.value, "126.75")

    def test_aliexpress_navigation_and_all_existing_pages_preserved(self):
        self.assertEqual([p.index for p in ALIEXPRESS.pages],
                         [0, 1, 2, 6, 7, 3, 5, 4, 8, 10, 9, 11])
        self.app._switch_platform("aliexpress")
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            for item in ALIEXPRESS.pages:
                self.app._switch(item.index)
                self.assertEqual(self.app._current, item.index)
                self.assertTrue(self.controls())

    def test_amazon_bullets_one_editor_one_call_and_navigation_preserves_content(self):
        from test_amazon_ui_page import fake_backend, sync_threads, find_button, click
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_bullet_count", "3")  # 旧配置不改变新入口生成五条的约定。
        self.cfg.set("amazon_site", "A2EUQ1WTGCTBG2")
        self.app._switch(19)  # Establish the new site's isolated preset.
        self.cfg.set("amazon_collect_ai", True)
        self.app._amz_detail = {"product": {"id": "123", "title": "Original product"}}
        self.app._amz_form = {"id": "123", "title": "Original product", "bullets": []}
        self.app._switch(19)
        fields = [c for c in self.controls() if isinstance(c, ft.TextField)
                  and c.label == "五点描述（每行一条）"]
        self.assertEqual(len(fields), 1)
        self.assertFalse(any(str(getattr(c, "label", "")).startswith("卖点 ") for c in self.controls()))
        with fake_backend() as backend, sync_threads():
            click(find_button(self.app, "AI 生成五点", exact=True))
            click(self.app.page.dialog.actions[1])
        self.assertEqual(backend.calls["ai"], [{"field": "bullets", "count": 5}])
        self.assertEqual(len(fields[0].value.splitlines()), 5)
        self.app._switch(18)
        self.app._switch(19)
        rebuilt = next(c for c in self.controls() if isinstance(c, ft.TextField)
                       and c.label == "五点描述（每行一条）")
        self.assertEqual(rebuilt.value, fields[0].value)
        prompt = next(c for c in self.controls() if isinstance(c, ft.TextField)
                      and "五点描述提示词" in str(c.label))
        self.assertIn("恰好输出", prompt.value)

    def test_amazon_category_price_and_inventory_are_separate(self):
        self.app._switch_platform("amazon")
        for index, own, absent in ((17, "自动补齐必填属性", "AI 生成 SKU"),
                                   (34, "AI 生成 SKU", "刷新运费模板"),
                                   (18, "刷新运费模板", "自动补齐必填属性")):
            self.app._switch(index)
            labels = [getattr(c, "text", "") for c in self.controls()]
            self.assertIn(own, labels)
            self.assertNotIn(absent, labels)

    def test_amazon_media_and_native_fields_reach_payload_without_changing_key_contract(self):
        from aeclone.services.amazon_payload import build_amazon_payload, SAVE_KEYS
        from test_amazon_pipeline import make_product
        source = make_product()
        out = build_amazon_payload(source, self.cfg, {
            "main_image": "https://example.invalid/new-main.jpg",
            "extra_images": ["https://example.invalid/extra.jpg"],
            "native_fields": {"saleStartDateStr": "2027-01-01", "saleEndDateStr": "2027-01-31",
                              "sourceUrl": "https://example.invalid/source", "standardProductId": "123456789012"},
        })
        self.assertEqual(list(out), SAVE_KEYS)
        self.assertEqual(out["mainImage"], "https://example.invalid/new-main.jpg")
        self.assertEqual(out["extraImagesStr"], "https://example.invalid/extra.jpg")
        self.assertEqual(out["saleStartDateStr"], "2027-01-01")
        self.assertEqual(out["standardProductId"], "123456789012")
        self.assertNotEqual(source["mainImage"], out["mainImage"])

    def test_tiktok_precise_variant_edit_and_navigation(self):
        from aeclone.services.tiktok_payload import build_tiktok_payload
        self.app._switch_platform("tiktok")
        source = {"id": "1", "productName": "A valid product with enough title length",
            "site": "GB", "variationList": [
                {"idStr": "90071992547409931", "sellerSku": "RED", "originalPrice": 10, "availableStock": 5},
                {"idStr": "90071992547409932", "sellerSku": "BLUE", "originalPrice": 12, "availableStock": 7}]}
        self.app._tt_product = source
        self.app._switch(35)
        stocks = [c for c in self.controls() if isinstance(c, ft.TextField) and c.label == "变种库存"]
        stocks[1].value = "0"
        stocks[1].on_change(SimpleNamespace(control=stocks[1]))
        self.app._switch(25)
        self.app._switch(35)
        rebuilt = [c for c in self.controls() if isinstance(c, ft.TextField) and c.label == "变种库存"]
        self.assertEqual(rebuilt[1].value, "0")
        edits = self.app._tt_form["variant_edits"]
        out = build_tiktok_payload({"product": source}, self.cfg, {"variant_edits": {k: v for k, v in edits.items() if v}})
        self.assertEqual(out["variationList"][0]["availableStock"], 5)
        self.assertEqual(out["variationList"][1]["availableStock"], 0)
        self.assertEqual(source["variationList"][1]["availableStock"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
