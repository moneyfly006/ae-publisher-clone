"""Amazon 客户端离线单测（mock requests，无真实网络、无真实保存）。

跑法：``.venv/bin/python tests/test_amazon_client.py``
"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.amazon import (
    AmazonClient, AmazonError, AmazonNotReady, SAVE_NAME, extract_list,
    normalize_product, parse_cookie,
)

BASE = "https://www.dianxiaomi.com"


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None, json_error=False):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


def install_responses(testcase, responses):
    """把 requests.Session.request 换成按顺序返回 responses 的桩，返回调用记录。"""
    calls = []
    queue = list(responses)

    def fake_request(session, method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if not queue:
            raise AssertionError(f"没有更多桩响应，却被请求：{method} {url}")
        item = queue.pop(0)
        if callable(item):
            return item()
        return item

    patcher = patch.object(requests.Session, "request", fake_request)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return calls


def ok(payload):
    return FakeResponse({"code": 0, "msg": "Successful", "data": payload})


class ParseAndCompatTests(unittest.TestCase):
    def test_parse_cookie(self):
        ck = parse_cookie("a=1; b=2; broken; c=x=y")
        self.assertEqual(ck, {"a": "1", "b": "2", "c": "x=y"})

    def test_legacy_exception_alias(self):
        self.assertTrue(issubclass(AmazonError, RuntimeError))
        self.assertTrue(issubclass(AmazonNotReady, AmazonError))

    def test_legacy_normalize_extract(self):
        p = normalize_product({"id": 1, "title": "Machine", "asin": "B01", "sku": "S1",
                               "price": "9.99", "quantity": 5, "dxmState": "draft",
                               "marketPlace": "US", "updateTime": 123})
        self.assertEqual(p["id"], "1")
        self.assertEqual(p["asin"], "B01")
        self.assertEqual(p["stock"], 5)
        self.assertEqual(p["status"], "draft")
        rows = extract_list({"page": {"list": [{"id": 2, "title": "T"}]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "T")

    def test_normalize_amazon_fields(self):
        row = {"id": "9", "marketPlaceId": "A2EUQ1WTGCTBG2",
               "standardPrice": 0.0, "saleSalePrice": 12.5,
               "standardPriceCurrency": "CAD", "quantity": 3,
               "mainImage": "https://x/a.png", "extraImages": ["https://x/b.png"]}
        p = normalize_product(row)
        self.assertEqual(p["price"], "12.5")
        self.assertEqual(p["currency"], "CAD")
        self.assertEqual(p["marketplace_id"], "A2EUQ1WTGCTBG2")
        self.assertEqual(p["extra_images"], ["https://x/b.png"])


class LoginShopSiteTests(unittest.TestCase):
    def test_check_login_true_false(self):
        c = AmazonClient("a=1")
        install_responses(self, [ok({"id": 1, "account": "u", "shopMap": {}})])
        self.assertTrue(c.check_login())

        c2 = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": 2001, "msg": "未登录"},
                                              text='{"code":2001}')])
        self.assertFalse(c2.check_login())

    def test_shops(self):
        c = AmazonClient("a=1")
        install_responses(self, [ok({
            "id": 2728813, "account": "QINGHEMAOYI",
            "shopMap": {"9408005": {"id": 9408005, "name": "QingHe",
                                    "merchantId": "A26OV9RTCAYJMS",
                                    "marketPlaceId": "ATVPDKIKX0DER"}},
        })])
        shops = c.shops()
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0]["id"], "9408005")
        self.assertEqual(shops[0]["name"], "QingHe")
        self.assertEqual(shops[0]["merchant_id"], "A26OV9RTCAYJMS")
        self.assertEqual(shops[0]["default_marketplace"], "ATVPDKIKX0DER")

    def test_sites_parse_str_country(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [ok({
            "strCountry": ("美国,na,USD,US,ATVPDKIKX0DER#"
                           "加拿大,na,CAD,CA,A2EUQ1WTGCTBG2#"
                           "墨西哥,na,MXN,MX,A1AM78C64UM0Y8"),
            "markeyPlaceLang": {"ATVPDKIKX0DER": "en_US",
                                "A2EUQ1WTGCTBG2": "en_CA",
                                "A1AM78C64UM0Y8": "es_MX"},
        })])
        sites = c.sites("9408005")
        self.assertEqual([s["code"] for s in sites], ["US", "CA", "MX"])
        self.assertEqual(sites[1]["marketplace_id"], "A2EUQ1WTGCTBG2")
        self.assertEqual(sites[1]["currency"], "CAD")
        self.assertEqual(sites[1]["language"], "en")
        self.assertEqual(sites[2]["language"], "es")
        self.assertEqual(calls[0]["data"], {"shopId": "9408005"})
        self.assertTrue(calls[0]["url"].endswith("/api/amazonProduct/getCountryByShopId.json"))


class ProductListTests(unittest.TestCase):
    def test_list_products_shape_and_params(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [ok({
            "page": {"list": [{"id": "220114595363182310", "title": "Backhoe",
                               "sku": "S1", "marketPlaceId": "A2EUQ1WTGCTBG2",
                               "quantity": 0, "standardPrice": 0.0,
                               "saleSalePrice": 0.0, "standardPriceCurrency": "CAD",
                               "dxmState": "draft"}],
                     "pageNo": 1, "pageSize": 20, "totalSize": 16, "totalPage": 1},
            "draftNum": 16, "leftStat": {"waitPublish": "5", "publishFail": "21"},
            "marketPids": {"A2EUQ1WTGCTBG2": "加拿大"},
        })])
        data = c.list_products("draft", page=2, page_size=5, shop_id="9408005",
                               keyword="backhoe")
        self.assertEqual(data["total"], 16)
        self.assertEqual(data["page_no"], 1)
        self.assertEqual(data["total_page"], 1)
        self.assertEqual(data["drafts"], 16)
        self.assertEqual(data["wait_publish"], 5)
        self.assertEqual(data["publish_fail"], 21)
        self.assertEqual(data["markets"], {"A2EUQ1WTGCTBG2": "加拿大"})
        self.assertEqual(data["items"][0]["title"], "Backhoe")
        sent = calls[0]["data"]
        self.assertEqual(sent["pageNo"], 2)
        self.assertEqual(sent["pageSize"], 5)
        self.assertEqual(sent["shopId"], "9408005")
        self.assertEqual(sent["dxmState"], "draft")
        self.assertEqual(sent["searchValue"], "backhoe")

    def test_list_products_rejects_unknown_state(self):
        c = AmazonClient("a=1")
        with self.assertRaises(AmazonError):
            c.list_products("bogus")

    def test_product_detail(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [ok({"site": "CA", "product": {"id": "1"}})])
        detail = c.product_detail("1")
        self.assertEqual(detail["site"], "CA")
        self.assertEqual(calls[0]["params"], {"id": "1"})
        self.assertTrue(calls[0]["url"].endswith("/api/amazonProductSp/editSp.json"))

    def test_product_detail_error_is_amazon_error(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": -1, "msg": "商品不存在"},
                                              text='{"code":-1}')])
        with self.assertRaises(AmazonError):
            c.product_detail("404")

    def test_product_attributes_parses_json_string(self):
        c = AmazonClient("a=1")
        raw = json.dumps({"is_assembly_required": [{"value": False}]})
        calls = install_responses(self, [ok(raw)])
        attrs = c.product_attributes("1", "A2EUQ1WTGCTBG2")
        self.assertEqual(attrs["is_assembly_required"][0]["value"], False)
        self.assertEqual(calls[0]["data"], {"id": "1", "site": "A2EUQ1WTGCTBG2"})

    def test_product_attributes_failure_returns_empty(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": -1, "msg": "err"},
                                              text='{"code":-1}')])
        self.assertEqual(c.product_attributes("1", "A2EUQ1WTGCTBG2"), {})


class CategoryAndAuxTests(unittest.TestCase):
    def test_category_tree_uses_site_code_and_internal_parent_id(self):
        c = AmazonClient("a=1")
        node = {"id": 166, "categoryId": "6948390011", "categoryName": "Automotive",
                "childIds": [], "leafCategory": False}
        calls = install_responses(self, [ok([node]), ok([node])])
        roots = c.category_roots("CA")
        children = c.category_children("CA", 188)
        self.assertEqual(roots[0]["id"], 166)
        self.assertEqual(calls[0]["data"], {"site": "CA", "parentId": "0"})
        self.assertEqual(calls[1]["data"], {"site": "CA", "parentId": "188"})

    def test_category_by_path(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [ok({"id": 159365, "categoryId": "13553005011",
                                             "leafCategory": True})])
        node = c.category_by_path("CA", "a/b/c")
        self.assertEqual(node["categoryId"], "13553005011")
        sent = calls[0]["data"]
        self.assertEqual(sent["site"], "CA")
        self.assertEqual(sent["nodePath"], "a/b/c")

    def test_category_by_path_error(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": -1, "msg": "分类数据加载失败"},
                                              text='{"code":-1}')])
        with self.assertRaises(AmazonError):
            c.category_by_path("CA", "bad/path")

    def test_history_types_predict(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [
            ok([{"categoryId": "9709494011", "categoryName": "Bricks"}]),
            ok(["OUTDOOR_LIVING", "ABIS_BOOK"]),
            ok({"productType": "OUTDOOR_LIVING", "amaoznCategorySearchVos": None}),
        ])
        self.assertEqual(c.category_history("9408005", "CA")[0]["categoryId"], "9709494011")
        self.assertEqual(c.product_types("CA"), ["OUTDOOR_LIVING", "ABIS_BOOK"])
        pred = c.predict_category("9408005", "CA", "Backhoe Loader")
        self.assertEqual(pred["productType"], "OUTDOOR_LIVING")
        self.assertEqual(calls[0]["data"], {"shopId": "9408005", "site": "CA"})
        self.assertEqual(calls[1]["data"], {"site": "CA"})
        self.assertEqual(calls[2]["data"]["site"], "CA")

    def test_predict_failure_returns_empty(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": -1, "msg": "预测失败"},
                                              text='{"code":-1}')])
        self.assertEqual(c.predict_category("9408005", "CA", "x"), {})

    def test_currency_logistics_brands_use_marketplace_id(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [
            ok({"currency": "CAD"}),
            ok([{"templateId": "legacy-template-id", "templateName": "Migrated"}]),
            ok([]),
        ])
        self.assertEqual(c.currency("9408005", "A2EUQ1WTGCTBG2"), "CAD")
        self.assertEqual(c.logistics("9408005", "A2EUQ1WTGCTBG2")[0]["templateId"],
                         "legacy-template-id")
        self.assertEqual(c.brands("9408005", "A2EUQ1WTGCTBG2"), [])
        for call in calls:
            self.assertEqual(call["data"],
                             {"shopId": "9408005", "marketPlaceId": "A2EUQ1WTGCTBG2"})

    def test_check_banned_words(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [ok({"words": ["best", "100%"]})])
        hits = c.check_banned_words(title="Best product", description="100%",
                                    bullet_point="a±b", search_terms="x")
        self.assertEqual(hits, ["best", "100%"])
        self.assertEqual(calls[0]["data"]["bulletPoint"], "a±b")

    def test_check_banned_words_failure_returns_empty(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": 500, "msg": "服务器错误"},
                                              text='{"code":500}')])
        self.assertEqual(c.check_banned_words(title="x"), [])


class SaveProductTests(unittest.TestCase):
    def _payload(self):
        from aeclone.services.amazon_payload import SAVE_KEYS
        payload = {k: None for k in SAVE_KEYS}
        payload.update({"id": "1", "shopId": "9", "marketPlaceId": "A2EUQ1WTGCTBG2",
                        "title": "T", "categoryId": "123", "sku": "S",
                        "brand": "B", "manufacturer": "M", "productAttr": "{}",
                        "mainImage": "https://x/a.png", "bulletPoint": "a±b",
                        "description": "<p>d</p>", "searchTerms": "",
                        "quantity": 0, "isVariation": 0, "skuTitle": 0,
                        "gpsrSafetyAttestation": "0"})
        return payload

    def test_save_builds_zip_and_multipart(self):
        c = AmazonClient("a=1")
        calls = install_responses(self, [FakeResponse(
            {"code": 0, "msg": "Successful", "data": {"id": "1"}})])
        payload = self._payload()
        resp = c.save_product(payload, op="3")
        self.assertEqual(resp["code"], 0)
        call = calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/api/amazonProductSp/add.json"))
        self.assertEqual(call["data"], {"op": "3"})
        files = call["files"]
        self.assertIn("file", files)
        name, blob, mime = files["file"]
        self.assertEqual(name, "blob")
        self.assertEqual(mime, "application/zip")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(zf.namelist(), [SAVE_NAME])
            data = json.loads(zf.read(SAVE_NAME).decode("utf-8"))
        self.assertEqual(data, payload)

    def test_save_rejects_empty_payload_and_bad_op(self):
        c = AmazonClient("a=1")
        with self.assertRaises(AmazonError):
            c.save_product({})
        with self.assertRaises(AmazonError):
            c.save_product(self._payload(), op="5")

    def test_save_failure_raises_with_msg(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({"code": -1, "msg": "保存失败：类目不可用"},
                                              text='{"code":-1}')])
        with self.assertRaises(AmazonError) as ctx:
            c.save_product(self._payload())
        self.assertIn("类目不可用", str(ctx.exception))

    def test_save_code_zero_but_failure_msg_raises(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse(
            {"code": 0, "msg": "Successful",
             "data": {"msg": "操作失败，只有处在采集箱中的产品才能编辑"}})])
        with self.assertRaises(AmazonError) as ctx:
            c.save_product(self._payload())
        self.assertIn("操作失败", str(ctx.exception))

    def test_login_expired_401(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse({}, status_code=401, text="unauthorized")])
        with self.assertRaises(AmazonError) as ctx:
            c.list_products()
        self.assertIn("登录已失效", str(ctx.exception))

    def test_non_json_html_means_login_expired(self):
        c = AmazonClient("a=1")
        install_responses(self, [FakeResponse(None, status_code=200,
                                              text="<html>login</html>", json_error=True)])
        with self.assertRaises(AmazonError) as ctx:
            c.list_products()
        self.assertIn("登录", str(ctx.exception))

    def test_zip_payload_alias(self):
        blob = AmazonClient.zip_payload({"id": "1"})
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(json.loads(zf.read(SAVE_NAME).decode()),
                             {"id": "1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
