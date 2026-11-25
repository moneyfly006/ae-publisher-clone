"""Pagination, immutable run scope, real-data defaults, stop and platform isolation."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services import collect_automation as service


class Pages:
    def __init__(self, rows, size=2, *, platform="amazon"):
        self.rows = deepcopy(rows)
        self.calls = []
        self.size = size
        self.platform = platform

    def list_products(self, *args):
        page = args[0] if self.platform == "alibaba" else args[1]
        self.calls.append(page)
        data = self.rows[(page - 1) * self.size:page * self.size]
        if self.platform == "alibaba":
            return {"data": {"page": {"list": data, "totalSize": len(self.rows),
                                      "totalPage": (len(self.rows) + self.size - 1) // self.size}}}
        return {"items": data, "total": len(self.rows),
                "total_page": (len(self.rows) + self.size - 1) // self.size}

    def product_detail(self, pid):
        return {"product": {"id": pid, "dxmState": "draft"}}


class CollectionTests(unittest.TestCase):
    def cfg(self, platform="amazon", **data):
        return AppConfig({platform + "_collect_limit": 0, **data})

    def test_all_platforms_paginate_and_match_before_limit(self):
        for platform in service.PLATFORMS:
            client = Pages([{"id": "1", "title": "chair"}, {"id": "2", "title": "chair"},
                            {"id": "3", "title": "POOL"}, {"id": "4", "title": "pool"}], platform=platform)
            selected = service.snapshot(client, platform, keyword="pool", limit=1, page_size=2)
            self.assertEqual([r["id"] for r in selected], ["3"])
            self.assertEqual(client.calls, [1, 2])

    def test_big_integer_string_id_is_preferred(self):
        self.assertEqual(service.identity({"id": 10000000000000000, "idStr": "10000000000000001"}), "10000000000000001")

    def test_alibaba_collection_filters_by_selected_shop(self):
        captured = []

        def listing(*args):
            captured.append(args)
            return {"data": {"page": {"list": [
                {"id": "1", "shopId": "shop-one"}, {"id": "2", "shopId": "shop-two"}], "totalPage": 1}}}

        rows = service.snapshot(SimpleNamespace(list_products=listing), "alibaba", shop_id="shop-two")
        self.assertEqual(captured, [(1, 100, "draft", "shop-two")])
        self.assertEqual([item["id"] for item in rows], ["2"])

    def test_overlap_deduplicated(self):
        class Overlap:
            def list_products(self, state, page, *args):
                return {"items": [{"id": "1"}, {"id": "2"}] if page == 1 else [{"id": "2"}, {"id": "3"}], "total_page": 2}
        self.assertEqual([r["id"] for r in service.snapshot(Overlap(), "amazon", limit=0)], ["1", "2", "3"])

    def test_missing_pagination_counts_do_not_end_a_full_page(self):
        pages = Mock(side_effect=[
            {"items": [{"id": "1"}], "total": 0, "total_page": 0},
            {"items": [{"id": "2"}], "total": "0", "total_page": "0"},
            {"items": [], "total": 0, "total_page": 0},
        ])
        selected = service.snapshot(SimpleNamespace(list_products=pages), "tiktok", limit=0, page_size=1)
        self.assertEqual([row["id"] for row in selected], ["1", "2"])
        self.assertEqual(pages.call_count, 3)

    def test_incomplete_reported_page_count_never_writes_partial_scope(self):
        cases = (({"total_page": 3}, {"items": [], "total_page": 0}),
                 ({"total_page": 2, "total": 3}, {"items": [{"id": "2"}], "total_page": 2}))
        for first_page_counts, response in cases:
            pages = Mock(side_effect=[{"items": [{"id": "1"}], **first_page_counts}, response])
            with self.subTest(response=response), patch.object(service, "process_one") as process:
                with self.assertRaisesRegex(ValueError, "未执行写入"):
                    service.run_collect(self.cfg(), "amazon", client=SimpleNamespace(list_products=pages), dry_run=False)
                process.assert_not_called()

    def test_amazon_scope_filters_other_shop_and_site_before_limit(self):
        calls = []
        def listing(*args):
            calls.append(args)
            return {"items": [{"id": "1", "shop_id": "other", "marketplace_id": "CA"},
                              {"id": "2", "shop_id": "selected", "marketplace_id": "US"},
                              {"id": "3", "shop_id": "selected", "marketplace_id": "CA"}], "total_page": 1}
        rows = service.snapshot(SimpleNamespace(list_products=listing), "amazon", shop_id="selected", site_id="CA", limit=1)
        self.assertEqual(calls, [("draft", 1, 100, "selected", "")])
        self.assertEqual([row["id"] for row in rows], ["3"])

    def test_tiktok_and_shopee_scope_is_applied_before_collection_limit(self):
        for platform in ("tiktok", "shopee"):
            with self.subTest(platform=platform):
                cfg = self.cfg(platform, **{platform + "_shop_id": "selected"})
                cfg.set(platform + "_collect_limit", 1)
                if platform == "tiktok":
                    cfg.set("tiktok_site", "GB")
                rows = [{"id": "other-shop", "shop_id": "other", "site": "GB"}]
                if platform == "tiktok":
                    rows.append({"id": "other-site", "shop_id": "selected", "site": "US"})
                rows.append({"id": "selected-item", "shopIdStr": "selected", "site": "gb"})
                listing = Mock(return_value={"items": rows, "total_page": 1})
                with patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=False)) as process:
                    result, = service.run_collect(cfg, platform, client=SimpleNamespace(list_products=listing))
                self.assertEqual(result["id"], "selected-item")
                self.assertEqual(process.call_args.args[1], "selected-item")
                filters = {"site": "GB"} if platform == "tiktok" else {}
                listing.assert_called_once_with("draft", 1, 100, "selected", "", **filters)

    def test_repeated_page_never_writes_partial_scope(self):
        client = SimpleNamespace(list_products=lambda *args: {"items": [{"id": "1"}], "total": 4, "total_page": 4})
        with patch.object(service, "process_one") as process:
            with self.assertRaisesRegex(ValueError, "重复"):
                service.run_collect(self.cfg(), "amazon", client=client, dry_run=False)
            process.assert_not_called()

    def test_failed_scan_never_writes_partial_scope(self):
        def listing(*args):
            if args[1] == 2:
                raise RuntimeError("network interrupted")
            return {"items": [{"id": "1"}], "total_page": 2}
        with patch.object(service, "process_one") as process:
            with self.assertRaises(RuntimeError):
                service.run_collect(self.cfg(), "amazon", client=SimpleNamespace(list_products=listing), dry_run=False)
            process.assert_not_called()

    def test_scope_fixed_before_saves_remove_source_rows(self):
        client = Pages([{"id": str(i)} for i in range(5)])
        processed = []
        def process(platform, pid, *args, **kwargs):
            processed.append(pid)
            client.rows[:] = [r for r in client.rows if r["id"] != pid]
            return SimpleNamespace(ok=True, saved=True, error="")
        with patch.object(service, "process_one", side_effect=process):
            result = service.run_collect(self.cfg(), "amazon", client=client, dry_run=False)
        self.assertEqual(processed, ["0", "1", "2", "3", "4"])
        self.assertEqual(len(result), 5)

    def test_stop_during_scan_writes_nothing(self):
        calls = iter([False, True, True])
        client = Pages([{"id": str(i)} for i in range(6)])
        with patch.object(service, "process_one") as process:
            service.run_collect(self.cfg(), "amazon", client=client, dry_run=False, stop=lambda: next(calls, True))
            process.assert_not_called()

    def test_stop_after_current_item(self):
        state = {"stop": False}
        def process(*args, **kwargs):
            state["stop"] = True
            return SimpleNamespace(ok=True, saved=True, error="")
        with patch.object(service, "process_one", side_effect=process) as run:
            results = service.run_collect(self.cfg(), "amazon", client=Pages([]), rows=[{"id": "1"}, {"id": "2"}],
                                         stop=lambda: state["stop"], dry_run=False)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(len(results), 1)

    def test_failure_continues_and_progress_records_every_item(self):
        progress = []
        responses = [RuntimeError("missing category"), SimpleNamespace(ok=True, saved=True, error="")]
        with patch.object(service, "process_one", side_effect=responses):
            results = service.run_collect(self.cfg(), "amazon", client=Pages([]), rows=[{"id": "1"}, {"id": "2"}],
                progress=lambda i, total, r: progress.append((i, total, r["ok"])), dry_run=False)
        self.assertEqual([r["ok"] for r in results], [False, True])
        self.assertEqual(progress, [(1, 2, False), (2, 2, True)])

    def test_single_item_settings_do_not_leak(self):
        cfg = AppConfig({"tiktok_title": "previous product", "tiktok_stock": 123, "tiktok_weight": "500",
                         "amazon_price": "10", "amazon_sku": "OLD", "alibaba_attr_presets": '{"Weight":"600"}'})
        original = deepcopy(cfg.as_dict())
        for platform, keys in (("tiktok", ("title", "stock", "weight")),
                               ("amazon", ("price", "sku"))):
            isolated = service.isolated_config(cfg, platform)
            for key in keys:
                self.assertEqual(getattr(isolated, platform + "_" + key), "")
        # 批量必须沿用用户已选类目/模板/属性预设，否则不会按类目 schema 补必填项。
        alibaba = service.isolated_config(cfg, "alibaba")
        self.assertEqual(alibaba.alibaba_category_id, cfg.alibaba_category_id)
        self.assertEqual(alibaba.alibaba_attr_presets, cfg.alibaba_attr_presets)
        self.assertEqual(cfg.as_dict(), original)

    def test_default_settings_are_non_destructive(self):
        cfg = AppConfig()
        for platform in service.PLATFORMS:
            self.assertEqual(getattr(cfg, platform + "_collect_op"), "1")
            self.assertFalse(getattr(cfg, platform + "_collect_ai"))
            self.assertEqual(service.settings(cfg, platform), {})

    def test_dry_run_never_enables_paid_ai(self):
        with patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=False, error="")) as run:
            service.run_collect(self.cfg(amazon_collect_ai=True), "amazon", client=Pages([]), rows=[{"id": "1"}])
            self.assertFalse(run.call_args.kwargs["use_ai"])
            self.assertTrue(run.call_args.kwargs["dry_run"])

    def test_platform_routes_to_its_own_pipeline(self):
        for platform in ("amazon", "tiktok", "alibaba"):
            with patch(f"aeclone.services.{platform}_pipeline.run_{platform}_product") as run:
                service.process_one(platform, "1", AppConfig({"amazon_collect_auto_repair": False}), object(), {}, dry_run=True, op="1", use_ai=False)
                run.assert_called_once()
                self.assertTrue(run.call_args.kwargs["dry_run"])

    def test_alibaba_trade_originals_survive_machinery_install_defaults(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        product = {"id": "1", "categoryId": "123", "fullCid": "1,123", "subject": "Portable tool box for home storage",
                   "wholesaleTrade": json.dumps({"unit_type": "Set", "sale_type": "batch", "batch_number": 4,
                       "inventory": [{"warehouseCode": "W", "value": 91}], "weight": "0.65", "package_size": "10X20X30",
                       "deliver_periods": [{"quantity": 2, "process_period": 7}]})}
        cfg = service.isolated_config(AppConfig({"alibaba_category_id": ""}), "alibaba")
        saved = build_alibaba_save(product, cfg)
        trade = json.loads(saved["wholesaleTrade"])
        expected = json.loads(product["wholesaleTrade"])
        expected.setdefault("min_order_quantity", 1)
        expected.setdefault("tariffsHsCode", [{"toCountry": "US", "hsCode": "84791000"}])
        self.assertEqual(trade, expected)
        self.assertEqual(saved["categoryId"], "123")
        self.assertNotIn("professional machinery", saved.get("companyDesc", ""))

    def test_invalid_platform_and_quantity_fail_before_requests(self):
        with self.assertRaises(ValueError):
            service.snapshot(object(), "aliexpress")
        with self.assertRaises(ValueError):
            service.snapshot(object(), "amazon", limit=-1)

    def test_alibaba_fob_trade_survives_batch_defaults(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        fob = {"fob_min_price": 12, "fob_max_price": 16, "fob_currency": "EUR", "sale_type": "batch", "min_order_quantity": 50}
        product = {"id": "1", "categoryId": "123", "priceType": "fob_price", "sourcingTrade": json.dumps(fob)}
        saved = build_alibaba_save(product, service.isolated_config(AppConfig(), "alibaba"))
        self.assertEqual(json.loads(saved["sourcingTrade"]), fob)

    def test_guarded_no_write_cannot_be_reported_as_saved_success(self):
        with patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=False, error="")):
            result = service.run_collect(self.cfg(), "amazon", client=Pages([]), rows=[{"id": "1"}], dry_run=False)
        self.assertFalse(result[0]["ok"])
        self.assertIn("未执行保存", result[0]["error"])

    def test_image_failure_prevents_save_and_dry_check_never_calls_images(self):
        cfg = self.cfg(amazon_collect_images=True, api_key="test-only-key")
        with patch.object(service, "process_images", side_effect=RuntimeError("hosting failed")) as image, \
             patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=False, error="")) as process:
            checked = service.run_collect(cfg, "amazon", client=Pages([]), rows=[{"id": "1"}])
            self.assertTrue(checked[0]["ok"])
            image.assert_not_called()
            process.reset_mock()
            failed = service.run_collect(cfg, "amazon", client=Pages([]), rows=[{"id": "1"}], dry_run=False)
            process.assert_not_called()
            self.assertFalse(failed[0]["ok"])

    def test_images_map_to_each_platform_and_keep_unselected_urls(self):
        from unittest.mock import MagicMock
        products = {
            "alibaba": {"mainImage": "https://old/1,https://old/2"},
            "amazon": {"mainImage": "https://old/1", "extraImagesStr": "https://old/2"},
            "tiktok": {"mainImage": "https://old/1|https://old/2"},
            "shopee": {"mainImage": "https://old/1|https://old/2"},
        }
        for platform, product in products.items():
            product["id"] = "1"
            pipeline = MagicMock()
            pipeline.run.return_value = SimpleNamespace(ok=True, last_url=lambda: "https://temporary/new")
            detail = product if platform == "alibaba" else {"product": product}
            cfg = AppConfig({platform + "_collect_image_indices": "1" if platform == "amazon" else "2",
                             platform + "_collect_image_rules": ""})
            overrides = {}
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload", return_value="https://hosted/new") as upload:
                service.process_images(platform, "1", cfg, SimpleNamespace(product_detail=lambda _: detail), overrides)
                self.assertEqual(pipeline.run.call_args.args[0],
                                 "https://old/1" if platform == "amazon" else "https://old/2")
                upload.assert_called_once()
            expected = ({"main_image": "https://hosted/new", "extra_images": ["https://old/2"]}
                        if platform == "amazon" else
                        {"main_images" if platform == "alibaba" else "images": ["https://old/1", "https://hosted/new"]})
            self.assertEqual(overrides, expected)

    def test_bad_batch_numbers_are_rejected_before_any_processing(self):
        for value in ("bad", "-1", "1.5", "NaN", "Infinity"):
            cfg = self.cfg(amazon_collect_overrides=json.dumps({"quantity": value}))
            with patch.object(service, "process_one") as run:
                with self.assertRaises(ValueError):
                    service.run_collect(cfg, "amazon", client=Pages([]), rows=[{"id": "1"}], dry_run=False)
                run.assert_not_called()

    def test_image_budget_stop_is_resumable_and_does_not_submit(self):
        from aeclone.services import collect_store
        client = SimpleNamespace(product_detail=lambda pid: {
            "product": {"id": pid, "mainImage": "https://example.invalid/image.jpg"}})
        cfg = self.cfg(amazon_collect_images=True, api_key="test-only", amazon_collect_image_indices="1")
        with patch("aeclone.services.image_pipeline.ImagePipeline"), \
             patch("aeclone.services.collect_budget.paid", side_effect=collect_store.BudgetExceeded("图片预算已用完")), \
             patch.object(service, "process_one") as process:
            result = service.run_collect(cfg, "amazon", client=client, rows=[{"id": "1"}, {"id": "2"}], dry_run=False)
        process.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["resume_pending"])
        self.assertEqual(collect_store.get(result[0]["run_id"])["items"][0]["phase"], "queued")

    def test_readback_failure_is_saved_but_not_verified_success(self):
        client = SimpleNamespace(product_detail=lambda _: {"product": {"id": "wrong"}})
        with patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")):
            result = service.run_collect(self.cfg(), "amazon", client=client, rows=[{"id": "1"}], dry_run=False)
        self.assertTrue(result[0]["saved"])
        self.assertFalse(result[0]["ok"])
        self.assertIn("回读失败", result[0]["error"])

    def test_amazon_move_to_pending_uses_readback_state(self):
        cfg = self.cfg(amazon_collect_op="3")
        for state, expected in (("draft", "uncertain"), ("waitPublish", "pending")):
            client = SimpleNamespace(product_detail=lambda pid: {"product": {"id": pid, "dxmState": state}})
            with patch.object(service, "process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")):
                result, = service.run_collect(cfg, "amazon", client=client, rows=[{"id": "1"}], dry_run=False)
            self.assertEqual(result["outcome"], expected)
            self.assertEqual(result["ok"], state == "waitPublish")


if __name__ == "__main__":
    unittest.main(verbosity=2)
