"""Shopee 原生保存契约、变种身份、验证和跨平台 UI 回归。"""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import build_app, sandbox, walk
import flet as ft
from aeclone.config import AppConfig
from aeclone.integrations.shopee import ShopeeClient
from aeclone.services import shopee
from aeclone.ui.shopee import state_for
from aeclone.ui import app as ui


def product():
    return dict(idStr="90071992547409931234", id=90071992547409931234, shopId="88", categoryId="101",
        itemName="Portable Folding Storage Box", description="A reusable folding storage box for household use.",
        mainImage="https://example.invalid/a.jpg|https://example.invalid/b.jpg", weight="0.8",
        itemSku="PARENT", originalPrice="12.5", normalStock=0,
        attributes='[{"attribute_id":1,"attribute_value_list":[{"value_id":2}]}]',
        logisticsInfo='[{"shopId":"88","logisticsInfo":[{"logisticId":"1","enabled":true}]}]',
        variationList=[dict(idStr="10000000000000001", modelSku="RED", originalPrice=12.5,
                           normalStock=3, option1="Red", sellerStock='[{"location_id":"CNZ","stock":3}]'),
                       dict(idStr="10000000000000002", modelSku="BLUE", originalPrice=14.5,
                           normalStock=7, option1="Blue", sellerStock='[{"location_id":"CNZ","stock":7}]')])


class PayloadTests(unittest.TestCase):
    def test_ai_context_includes_extended_description_facts(self):
        api = Mock()
        api.chat.return_value = "Generated description"
        source = dict(itemName="Storage Box", descriptionType="extended",
                      descriptionInfoStr="<p>Original steel construction and dimensions</p>")
        shopee.generate_copy(source, AppConfig(), "description", client=api)
        context = json.loads(api.chat.call_args.args[1][1]["content"])
        self.assertIn("Original steel construction", str(context))

    def test_edit_exact_variant_preserves_other_variant_attributes_and_large_ids(self):
        source = product()
        before = deepcopy(source)
        out = shopee.build_payload(source, {"variant_edits": {
            "10000000000000002": {"originalPrice": "16.8", "normalStock": "0"}}})
        self.assertEqual(source, before)
        self.assertEqual(out["id"], "90071992547409931234")
        saved = json.loads(out["variationListStr"])
        self.assertEqual(saved[0]["originalPrice"], 12.5)
        self.assertEqual(saved[1]["normalStock"], "0")
        self.assertEqual(json.loads(saved[1]["sellerStock"])[0]["stock"], 0)
        self.assertEqual(saved[1]["id"], "10000000000000002")
        self.assertEqual(out["attributes"], source["attributes"])
        self.assertEqual(out["logisticsInfo"], source["logisticsInfo"])
        self.assertEqual(shopee.validate(out), [])

    def test_ambiguous_or_missing_variants_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "变更"):
            shopee.build_payload(product(), {"variant_edits": {"deleted": {"normalStock": 1}}})
        source = product()
        source["variationList"].append(deepcopy(source["variationList"][0]))
        with self.assertRaisesRegex(ValueError, "重复"):
            shopee.build_payload(source, {"variant_edits": {"10000000000000001": {"normalStock": 1}}})

    def test_multiwarehouse_total_does_not_overwrite_allocation(self):
        source = product()
        source["variationList"][0]["sellerStock"] = '[{"location_id":"A","stock":1},{"location_id":"B","stock":2}]'
        with self.assertRaisesRegex(ValueError, "多仓"):
            shopee.build_payload(source, {"variant_edits": {"10000000000000001": {"normalStock": 8}}})

    def test_validation_sees_edited_variant_and_remote_limits(self):
        out = shopee.build_payload(product(), {"variant_edits": {
            "10000000000000002": {"originalPrice": "nan", "normalStock": "-1"}}})
        issues = shopee.validate(out, limits={"nameMax": 10})
        self.assertTrue(any("标题" in x for x in issues))
        self.assertTrue(any("第 2 项价格" in x for x in issues))
        self.assertTrue(any("第 2 项库存" in x for x in issues))

    def test_image_delimiters_match_each_native_editor(self):
        urls = ["https://example.invalid/a.jpg", "https://example.invalid/b.jpg"]
        out = shopee.build_payload(product(), {"images": urls})
        self.assertEqual(out["mainImage"], "|".join(urls))
        out = shopee.build_payload(product(), {"images": urls}, mode="site")
        self.assertEqual(out["mainImage"], ";".join(urls))
        self.assertEqual(out["extraImages"], "|".join(urls))


class ClientTests(unittest.TestCase):
    def test_zip_member_and_endpoint_are_platform_specific(self):
        for mode, name, endpoint in (("global", "shopeeGlobalSave.txt", "/shopeeGlobalProduct/add.json"),
                                    ("site", "shopeeSave.txt", "/shopeeProduct/add.json")):
            client = ShopeeClient({}, mode=mode)
            client.http._post = Mock(return_value={"code": 0, "data": {}})
            client.save_product({"id": "123"}, "2")
            args, kwargs = client.http._post.call_args
            self.assertEqual(args[0], endpoint)
            self.assertEqual(kwargs["data"], {"op": "2"})
            with zipfile.ZipFile(io.BytesIO(kwargs["files"]["file"][1])) as archive:
                self.assertEqual(archive.namelist(), [name])
                self.assertEqual(json.loads(archive.read(name))["op"], "2")

    def test_list_failed_state_has_correct_native_filter(self):
        client = ShopeeClient({})
        client.http._post = Mock(return_value={"code": 0, "data": {"page": {"list": [], "totalSize": 0}}})
        result = client.list_products("publishFail", page=3, shop_id="88", keyword="box")
        args, kwargs = client.http._post.call_args
        self.assertEqual(args[0], "/shopeeGlobalProduct/pageList.json")
        self.assertEqual(kwargs["data"]["pageNo"], 3)
        self.assertEqual(kwargs["data"]["dxmOfflineState"], "publishFail")
        self.assertEqual(kwargs["data"]["dxmState"], "offline")
        self.assertEqual(result["items"], [])

    def test_category_keyword_search_uses_search_endpoint(self):
        for mode, endpoint in (("global", "/shopeeCategory/searchGlobalCategory.json"),
                               ("site", "/shopeeCategory/searchCategoryNew.json")):
            client = ShopeeClient({}, mode=mode)
            client.http._post = Mock(return_value={"code": 0, "data": []})
            client.categories("0", shop_id="88", keyword="phone")
            args, kwargs = client.http._post.call_args
            self.assertEqual(args[0], endpoint)
            self.assertEqual(kwargs["data"], {"categoryParentId": "0", "shopId": "88",
                                              "category": "phone"})

    def test_category_drilldown_uses_native_parent_parameter(self):
        client = ShopeeClient({})
        client.http._post = Mock(return_value={"code": 0, "data": []})
        client.categories("100535", shop_id="88")
        args, kwargs = client.http._post.call_args
        self.assertEqual(args[0], "/shopeeCategory/globalList.json")
        self.assertEqual(kwargs["data"], {"categoryParentId": "100535", "shopId": "88", "category": ""})

    def test_nested_business_error_is_not_success(self):
        client = ShopeeClient({})
        client.http._post = Mock(return_value={"code": 0, "data": {"code": -1, "msg": "类目缺失"}})
        with self.assertRaisesRegex(ValueError, "类目缺失"):
            client.save_product({"id": "1"})

    def test_guard_env_and_invalid_input_never_write(self):
        client = Mock()
        cfg = AppConfig({"shopee_mode": "global"})
        with patch.dict(os.environ, {"AURORA_TEST": "1"}):
            result = shopee.save("123", cfg, client=client)
        self.assertFalse(result.saved)
        client.save_product.assert_not_called()
        client.product_detail.assert_not_called()

    def test_save_rechecks_and_reports_platform_failure_without_claiming_online(self):
        client = Mock()
        client.product_detail.side_effect = [{"product": product()},
            {"product": dict(product(), dxmOfflineState="publishFail", errMsg="物流未启用")}]
        client.limits.return_value = dict(shopee.DEFAULT_LIMITS)
        client.attributes.return_value = []
        client.save_product.return_value = {"code": 0}
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}), patch("aeclone.core.guard.require"):
            result = shopee.save("123", AppConfig({"shopee_mode": "global"}), client=client, op="2")
        self.assertTrue(result.saved)
        self.assertFalse(result.ok)
        self.assertIn("物流未启用", result.message)
        self.assertEqual(client.product_detail.call_count, 2)

    def test_submit_callback_runs_after_validation_and_before_write(self):
        client = Mock()
        client.product_detail.return_value = {"product": product()}
        client.limits.return_value = dict(shopee.DEFAULT_LIMITS)
        client.attributes.return_value = []
        before_submit = Mock(side_effect=RuntimeError("journal stopped"))
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}), patch("aeclone.core.guard.require"):
            result = shopee.save("123", AppConfig(), client=client, before_submit=before_submit)
        before_submit.assert_called_once_with()
        client.save_product.assert_not_called()
        self.assertFalse(result.saved)
        self.assertEqual(result.message, "journal stopped")


class PageTests(unittest.TestCase):
    def setUp(self):
        context = sandbox()
        self.cfg = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app = build_app()
        self.app._switch_platform("shopee")

    def controls(self):
        return list(walk(self.app.content.controls))

    def test_all_shopee_pages_render_in_both_themes(self):
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            for item in self.app.active_platform.pages:
                self.app._switch(item.index)
                self.assertEqual(self.app._current, item.index)
                self.assertTrue(self.controls())

    def test_copy_and_variant_edit_survive_navigation(self):
        state = state_for(self.app)
        state["product"] = product()
        self.app._switch(41)
        control = next(c for c in self.controls() if isinstance(c, ft.TextField) and c.label == "商品标题")
        control.value = "A new title for portable folding storage boxes"
        control.on_change(SimpleNamespace(control=control))
        self.app._switch(40)
        stock = [c for c in self.controls() if isinstance(c, ft.TextField) and c.label == "库存"][1]
        stock.value = "0"
        stock.on_change(SimpleNamespace(control=stock))
        self.app._switch(41)
        rebuilt = next(c for c in self.controls() if isinstance(c, ft.TextField) and c.label == "商品标题")
        self.assertEqual(rebuilt.value, control.value)
        self.assertEqual(state["overrides"]["variant_edits"]["10000000000000002"]["normalStock"], "0")

    def test_shopee_does_not_start_aliexpress_pipeline(self):
        with patch.object(self.app, "_save_api_fields") as smt, patch("aeclone.ui.collect_workflow.launch") as launch:
            self.app._start()
        smt.assert_not_called()
        launch.assert_called_once_with(self.app, "shopee")
        with patch("aeclone.services.publisher.Publisher") as publisher:
            self.app._run_pipeline()
        publisher.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
