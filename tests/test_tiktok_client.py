"""TikTok 客户端离线单测（假 requests session；无真实网络、无任何写调用）。

跑法：``.venv/bin/python tests/test_tiktok_client.py``
覆盖：站点解析、类目参数构造、ZIP/multipart 形状、save_product 成功/失败/
NotReady、无店铺行为、_request 错误码/登录失效/繁忙重试边界。
"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.tiktok import (  # noqa: E402
    BASE, DEFAULT_LIMITS, L7_SITES, OP_MOVE_PENDING, OP_SAVE, OP_PUBLISH,
    SAVE_MODE_AS_DRAFT, SAVE_NAME, SITE_META, L7_SHOP_ID, TikTokClient,
    TikTokError, TikTokNotReady, extract_product, normalize_product,
    parse_cookie, zip_payload,
)


# ---------------------------------------------------------------------------
# 假 session / 假响应
# ---------------------------------------------------------------------------
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


class FakeSession:
    """按顺序返回桩响应；记录每次调用的 method/url/kwargs。"""

    def __init__(self, responses):
        self.queue = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.queue:
            raise AssertionError(f"没有更多桩响应，却被请求：{method} {url}")
        item = self.queue.pop(0)
        if callable(item):
            return item()
        if isinstance(item, BaseException):
            raise item
        return item


def ok(data=None):
    return FakeResponse({"code": 0, "msg": "Successful", "data": data})


def make_client(responses):
    client = TikTokClient("a=1; b=2; broken")
    session = FakeSession(responses)
    client.s = session
    sleeps = []
    client.sleep = sleeps.append
    return client, session, sleeps


def user_in_payload(shop_map):
    return {"code": 0, "msg": "Successful",
            "data": {"account": "QINGHEMAOYI", "puid": 2728813, "shopMap": shop_map}}


def call_data(session, index=0):
    return session.calls[index].get("data") or {}


# ---------------------------------------------------------------------------
# 基础：Cookie / 常量 / 工具函数
# ---------------------------------------------------------------------------
class CookieAndUtilTests(unittest.TestCase):
    def test_parse_cookie(self):
        self.assertEqual(parse_cookie("a=1; b=2; broken; c=x=y"),
                         {"a": "1", "b": "2", "c": "x=y"})
        self.assertEqual(parse_cookie(""), {})
        self.assertEqual(parse_cookie(None), {})

    def test_exception_hierarchy(self):
        self.assertTrue(issubclass(TikTokError, RuntimeError))
        self.assertTrue(issubclass(TikTokNotReady, TikTokError))

    def test_constants(self):
        self.assertEqual(SAVE_NAME, "tiktokSave.txt")
        self.assertEqual((OP_SAVE, OP_PUBLISH, OP_MOVE_PENDING), ("1", "2", "3"))
        self.assertEqual(SAVE_MODE_AS_DRAFT, "AS_DRAFT")
        self.assertEqual(L7_SHOP_ID, "ALL")
        self.assertEqual(len(L7_SITES), 23)
        self.assertEqual(len(set(L7_SITES)), 23, "L7 站点不能重复")
        for site in L7_SITES:
            self.assertIn(site, SITE_META, f"L7 站点 {site} 缺少 SITE_META")
        self.assertIn("US", SITE_META, "店铺站点里可能出现 US（L7 不含）")
        self.assertEqual(SITE_META["GB"]["currency"], "GBP")
        self.assertEqual(SITE_META["DE"]["language_name"], "德语")
        self.assertEqual(len(DEFAULT_LIMITS), 15)
        self.assertEqual(DEFAULT_LIMITS["nameMin"], 25)
        self.assertEqual(DEFAULT_LIMITS["nameMax"], 255)
        self.assertEqual(DEFAULT_LIMITS["stockMax"], 99999)

    def test_zip_payload_entry_and_utf8(self):
        blob = zip_payload({"productName": "测试标题", "op": 1})
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(zf.namelist(), [SAVE_NAME])
            raw = zf.read(SAVE_NAME).decode("utf-8")
        self.assertEqual(json.loads(raw), {"productName": "测试标题", "op": 1})
        self.assertNotIn(" ", raw, "紧凑 JSON 不应含多余空格")
        self.assertIn("测试标题", raw, "中文必须 UTF-8 原样写入，不能转义")

    def test_class_build_save_zip_alias(self):
        self.assertEqual(TikTokClient.build_save_zip({"a": 1}), zip_payload({"a": 1}))

    def test_normalize_product_defensive(self):
        empty = normalize_product({})
        for key in ("id", "title", "image", "shop_id", "site", "category_id",
                    "state", "price", "stock", "sku"):
            self.assertIn(key, empty)
        self.assertEqual(normalize_product(None), {})
        self.assertEqual(normalize_product("x"), {})

    def test_normalize_product_alternate_keys(self):
        row = {
            "idStr": "9001",
            "productName": "Phone Case",
            "imgUrl": "https://x/main.jpg",
            "images": [{"url": "https://x/a.jpg"}, "https://x/b.jpg"],
            "shopId": 9408005,
            "site": "GB",
            "categoryId": "605196",
            "categoryName": "Automotive",
            "quantity": 7,
            "price": "9.99",
            "dxmState": "draft",
            "updateTime": 1700000000000,
        }
        product = normalize_product(row)
        self.assertEqual(product["id"], "9001")
        self.assertEqual(product["title"], "Phone Case")
        self.assertEqual(product["image"], "https://x/main.jpg")
        self.assertEqual(product["images"], ["https://x/a.jpg", "https://x/b.jpg"])
        self.assertEqual(product["shop_id"], "9408005")
        self.assertEqual(product["stock"], 7)
        self.assertEqual(product["price"], "9.99")
        self.assertEqual(product["state"], "draft")

    def test_extract_product_priority_and_deepcopy(self):
        product = {"id": "1", "productName": "A"}
        detail = {"product": product, "category": {"code": "x"}}
        extracted = extract_product(detail)
        self.assertEqual(extracted, product)
        extracted["productName"] = "B"
        self.assertEqual(product["productName"], "A", "必须是深拷贝")
        self.assertEqual(extract_product({"data": {"product": {"id": "2"}}}), {"id": "2"})
        self.assertEqual(extract_product({"data": {"id": "3"}}), {"id": "3"})
        self.assertEqual(extract_product({"id": "4"}), {"id": "4"})
        self.assertEqual(extract_product(None), {})


# ---------------------------------------------------------------------------
# 登录 / 店铺 / 站点
# ---------------------------------------------------------------------------
class ShopTests(unittest.TestCase):
    def test_no_shop_map_returns_empty(self):
        client, session, _ = make_client([ok({"account": "QINGHEMAOYI"})])
        self.assertEqual(client.shops(), [])
        self.assertEqual(len(session.calls), 1)

    def test_amazon_only_account_has_no_tiktok_shop(self):
        # 真实账号事实：只有 Amazon + 手工订单，没有 TikTok 店铺
        shop_map = {
            "9408005": {"id": "9408005", "name": "QingHe", "platform": "amazon"},
            "0": {"id": "0", "name": "手工订单", "platform": "our"},
        }
        client, session, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        self.assertEqual(client.shops(), [])
        self.assertEqual(session.calls[0]["url"], BASE + "/api/userIn.json")

    def test_parses_tiktok_shop_with_site_list(self):
        shop_map = {
            "3001": {
                "id": "3001", "name": "TikTok UK", "platform": "tiktok",
                "merchantId": "M-1", "marketPlaceId": "GB",
                "marketplaceIds": "GB,US",
                "shopSiteList": [
                    {"site": "GB", "idStr": "site-gb", "name": "UK Store"},
                    {"marketPlaceId": "US", "id": "site-us"},
                ],
            }
        }
        client, _, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        shops = client.shops()
        self.assertEqual(len(shops), 1)
        shop = shops[0]
        self.assertEqual(shop["id"], "3001")
        self.assertEqual(shop["name"], "TikTok UK")
        self.assertTrue(shop["is_tiktok"])
        self.assertEqual(shop["merchant_id"], "M-1")
        self.assertEqual(shop["marketplace_ids"], ["GB", "US"])
        sites = {row["site"]: row for row in shop["sites"]}
        self.assertIn("GB", sites)
        self.assertEqual(sites["GB"]["site_shop_id"], "site-gb")
        self.assertEqual(sites["GB"]["name"], "UK Store")
        self.assertIn("US", sites)

    def test_expands_child_shop_list(self):
        shop_map = {
            "4001": {
                "id": "4001", "name": "Global Shop", "platform": "tiktok",
                "childShopList": [
                    {"idStr": "4002", "name": "US Sub", "marketPlaceId": "4Seller"},
                    {"idStr": "4003", "name": "GB Sub", "marketPlaceId": "GB"},
                ],
            }
        }
        client, _, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        shops = client.shops()
        ids = [shop["id"] for shop in shops]
        self.assertIn("4001", ids)
        self.assertIn("4002", ids)
        self.assertIn("4003", ids)
        by_id = {shop["id"]: shop for shop in shops}
        self.assertEqual([row["site"] for row in by_id["4002"]["sites"]],
                         ["US"], "4Seller 应映射为 US")
        self.assertEqual([row["site"] for row in by_id["4003"]["sites"]], ["GB"])
        # 子店站点并入父店，避免 UI 站点下拉缺站点
        parent_sites = {row["site"] for row in by_id["4001"]["sites"]}
        self.assertEqual(parent_sites, {"US", "GB"})

    def test_shop_site_list_json_string(self):
        shop_map = {
            "5001": {"id": "5001", "name": "S", "platform": "tiktok",
                     "shopSiteList": json.dumps([{"site": "DE"}, {"site": "FR"}])}
        }
        client, _, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        sites = {row["site"] for row in client.shops()[0]["sites"]}
        self.assertEqual(sites, {"DE", "FR"})

    def test_shop_map_list_variant(self):
        shop_map = [{"idStr": "6001", "name": "S", "platform": "tiktok",
                     "marketPlaceId": "JP"}]
        client, _, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        shops = client.shops()
        self.assertEqual([shop["id"] for shop in shops], ["6001"])
        self.assertEqual([row["site"] for row in shops[0]["sites"]], ["JP"])

    def test_shop_without_id_is_skipped(self):
        shop_map = {"x": {"name": "no id", "platform": "tiktok"}}
        client, _, _ = make_client([FakeResponse(user_in_payload(shop_map))])
        self.assertEqual(client.shops(), [])


class SiteTests(unittest.TestCase):
    def test_sites_without_shop_returns_23_l7(self):
        client, session, _ = make_client([
            ok({"tiktokL7Site": L7_SITES, "tiktokL7ShopId": "ALL"})])
        sites = client.sites()
        self.assertEqual(len(sites), 23)
        self.assertEqual([row["site"] for row in sites], L7_SITES)
        for row in sites:
            self.assertTrue(row["cross_border"])
            self.assertEqual(row["site_shop_id"], "")
        gb = next(row for row in sites if row["site"] == "GB")
        self.assertEqual(gb["code"], "GB")
        self.assertEqual(gb["country"], "英国")
        self.assertEqual(gb["currency"], "GBP")
        self.assertEqual(gb["language"], "en")
        self.assertEqual(gb["language_name"], "英语")
        self.assertEqual(session.calls[0]["method"], "GET")
        self.assertTrue(session.calls[0]["url"].endswith(
            "/api/tiktokProduct/getL7ShopIdAndSite.json"))

    def test_sites_l7_failure_falls_back_to_constant(self):
        client, _, _ = make_client([FakeResponse({"code": 500, "msg": "err"})])
        sites = client.sites()
        self.assertEqual([row["site"] for row in sites], L7_SITES)
        self.assertTrue(all(row["cross_border"] for row in sites))

    def test_sites_with_shop_unions_shop_and_l7(self):
        shop_map = {"7001": {"id": "7001", "name": "T", "platform": "tiktok",
                             "shopSiteList": [{"site": "US", "idStr": "shop-us"},
                                              {"site": "GB", "idStr": "shop-gb"}]}}
        client, session, _ = make_client([
            FakeResponse(user_in_payload(shop_map)),
            ok({"tiktokL7Site": L7_SITES, "tiktokL7ShopId": "ALL"}),
        ])
        sites = client.sites("7001")
        codes = [row["site"] for row in sites]
        self.assertEqual(len(codes), 24, "店铺站点 US/GB ∪ 23 L7 = 24（GB 去重）")
        self.assertEqual(codes[0], "US", "店铺站点优先")
        self.assertEqual(codes[1], "GB")
        us = sites[0]
        self.assertEqual(us["site_shop_id"], "shop-us")
        self.assertEqual(us["country"], "美国")
        # GB 站点应保留店铺的 site_shop_id
        gb = next(row for row in sites if row["site"] == "GB")
        self.assertEqual(gb["site_shop_id"], "shop-gb")
        # L7 里的站点全部存在
        for code in L7_SITES:
            self.assertIn(code, codes)

    def test_sites_unknown_shop_id_falls_back_l7(self):
        client, _, _ = make_client([
            FakeResponse(user_in_payload({})),
            ok({"tiktokL7Site": L7_SITES}),
        ])
        sites = client.sites("does-not-exist")
        self.assertEqual(len(sites), 23)


class LimitAndWarehouseTests(unittest.TestCase):
    def test_site_limits_success_merges_remote(self):
        remote = dict(DEFAULT_LIMITS)
        remote.update({"nameMin": 26, "priceMax": 12345.0, "stockMax": 777})
        client, session, _ = make_client([ok(remote)])
        limits = client.site_limits("GB")
        self.assertEqual(limits["nameMin"], 26)
        self.assertEqual(limits["stockMax"], 777)
        self.assertEqual(limits["priceMax"], 12345.0)
        self.assertEqual(limits["descMax"], 10000, "缺失字段回退默认")
        self.assertEqual(call_data(session), {"site": "GB"})

    def test_site_limits_failure_returns_defaults(self):
        client, _, _ = make_client([FakeResponse({"code": -1, "msg": "err"})])
        self.assertEqual(client.site_limits("GB"), DEFAULT_LIMITS)

    def test_site_limits_empty_site_no_request(self):
        client, session, _ = make_client([])
        self.assertEqual(client.site_limits(""), DEFAULT_LIMITS)
        self.assertEqual(session.calls, [])

    def test_warehouses_success_and_failure(self):
        client, session, _ = make_client([
            ok([{"id": "9", "name": "WH"}]),          # success
            FakeResponse({"code": -1, "msg": "err"}),  # failure
        ])
        self.assertEqual(client.warehouses("1"), [{"id": "9", "name": "WH"}])
        self.assertEqual(call_data(session), {"shopId": "1"})
        self.assertEqual(client.warehouses(), [])
        self.assertEqual(call_data(session, 1), {"shopId": "-1"})


# ---------------------------------------------------------------------------
# 商品列表 / 计数 / 详情 / 变种
# ---------------------------------------------------------------------------
class ProductListTests(unittest.TestCase):
    def test_list_products_params_extraction_and_counts_merge(self):
        page = {"page": {"list": [{"id": "1", "productName": "A",
                                   "imgUrl": "https://x/a.jpg", "dxmState": "draft",
                                   "quantity": 3, "price": "1.5", "site": "GB"}],
                         "totalSize": 1, "pageNo": 1, "totalPage": 1}}
        counts = {"draftNum": 2, "publishingTiming": 1, "waitPublish": 4,
                  "publishFail": 5, "publishing": 6}
        client, session, _ = make_client([ok(page), ok(counts)])
        result = client.list_products(state="draft", page=1, page_size=20,
                                      shop_id="-1", keyword="case", site="GB")
        first = call_data(session, 0)
        self.assertEqual(first["pageNo"], 1)
        self.assertEqual(first["pageSize"], 20)
        self.assertEqual(first["shopId"], "-1")
        self.assertEqual(first["dxmState"], "draft")
        self.assertEqual(first["searchValue"], "case")
        self.assertEqual(first["site"], "GB")
        self.assertTrue(session.calls[0]["url"].endswith("/api/tiktokProduct/pageList.json"))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "A")
        self.assertEqual(result["drafts"], 2)
        self.assertEqual(result["wait_publish"], 4)
        self.assertEqual(result["publish_fail"], 5)
        self.assertEqual(result["publishing"], 6)

    def test_list_products_uses_inline_counts_without_extra_request(self):
        page = {"draftNum": 9, "page": {"list": [], "totalSize": 0}}
        client, session, _ = make_client([ok(page)])
        result = client.list_products()
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(result["drafts"], 9)

    def test_list_products_invalid_state_raises_without_request(self):
        client, session, _ = make_client([])
        with self.assertRaises(TikTokError):
            client.list_products(state="bogus")
        self.assertEqual(session.calls, [])

    def test_list_products_malformed_data_is_defensive(self):
        client, _, _ = make_client([ok(None), ok(None)])
        result = client.list_products()
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["page_no"], 1)

    def test_counts_normalized_and_raw(self):
        client, session, _ = make_client([
            ok({"draftNum": 3, "publishingTiming": 1, "waitPublish": 2,
                "publishFail": 4, "publishing": 5})])
        counts = client.counts("-1")
        self.assertEqual(counts["drafts"], 3)
        self.assertEqual(counts["wait_publish"], 2)
        self.assertEqual(counts["publish_fail"], 4)
        self.assertEqual(counts["publishing"], 5)
        self.assertEqual(counts["publishing_timing"], 1)
        self.assertEqual(counts["draftNum"], 3, "保留原始键，兼容不同消费方")
        self.assertEqual(call_data(session), {"shopId": "-1"})


class ProductDetailTests(unittest.TestCase):
    def test_primary_success(self):
        detail = {"product": {"id": "1", "productName": "A"}, "category": {}}
        client, session, _ = make_client([ok(detail)])
        self.assertEqual(client.product_detail("1"), detail)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(call_data(session), {"idStr": "1"})

    def test_fallback_after_primary_business_error(self):
        client, session, _ = make_client([
            FakeResponse({"code": -1, "msg": "商品不存在", "data": None}),
            ok({"product": {"id": "2"}}),
        ])
        self.assertEqual(client.product_detail("2"), {"product": {"id": "2"}})
        self.assertEqual(call_data(session, 1), {"productId": "2"})

    def test_fallback_after_primary_http_404(self):
        client, session, _ = make_client([
            FakeResponse({}, status_code=404, text="<html>not found</html>"),
            ok({"product": {"id": "3"}}),
        ])
        self.assertEqual(client.product_detail("3"), {"product": {"id": "3"}})
        self.assertEqual(len(session.calls), 2)

    def test_both_empty_raises_chinese_error(self):
        client, session, _ = make_client([ok(None), ok(None)])
        with self.assertRaises(TikTokError) as ctx:
            client.product_detail("404")
        self.assertIn("读不到商品", str(ctx.exception))
        self.assertIn("TikTok", str(ctx.exception))
        self.assertEqual(len(session.calls), 2)

    def test_blank_id_raises_without_request(self):
        client, session, _ = make_client([])
        with self.assertRaises(TikTokError):
            client.product_detail("  ")
        self.assertEqual(session.calls, [])

    def test_variants_success_and_failure(self):
        client, session, _ = make_client([
            ok([{"id": "sku1"}, "bad"]),
            FakeResponse({"code": -1, "msg": "产品已被删除"}),
        ])
        self.assertEqual(client.product_variants("1"), [{"id": "sku1"}])
        self.assertEqual(call_data(session), {"id": "1"})
        self.assertEqual(client.product_variants("2"), [])


# ---------------------------------------------------------------------------
# 类目 / 属性 / 品牌 / 违禁词
# ---------------------------------------------------------------------------
class CategoryTests(unittest.TestCase):
    def test_categories_top_params(self):
        client, session, _ = make_client([ok([{"categoryId": "605196"}])])
        rows = client.categories("GB")
        self.assertEqual(rows, [{"categoryId": "605196"}])
        self.assertEqual(call_data(session), {
            "shopId": "-1", "site": "GB", "crossBorder": 1,
            "categoryParentId": "0",
        })
        self.assertTrue(session.calls[0]["url"].endswith("/api/tiktokCategory/list.json"))

    def test_categories_child_uses_parent_category_id(self):
        client, session, _ = make_client([ok([]), ok([])])
        client.categories("gb", parent_id="605196")
        self.assertEqual(call_data(session, 0)["categoryParentId"], "605196")
        self.assertEqual(call_data(session, 0)["site"], "GB")
        client.categories("GB", parent_id="605196", shop_id="3001", cross_border=False)
        self.assertEqual(call_data(session, 1)["shopId"], "3001")
        self.assertEqual(call_data(session, 1)["crossBorder"], 0)

    def test_categories_dict_wrapped_data(self):
        client, _, _ = make_client([ok({"categoryList": [{"categoryId": "1"}]})])
        self.assertEqual(client.categories("GB"), [{"categoryId": "1"}])

    def test_categories_requires_site(self):
        client, session, _ = make_client([])
        with self.assertRaises(TikTokError):
            client.categories("")
        self.assertEqual(session.calls, [])

    def test_search_categories_uses_category_field(self):
        client, session, _ = make_client([ok([{"categoryId": "911112"}])])
        rows = client.search_categories("phone", "GB")
        self.assertEqual(rows, [{"categoryId": "911112"}])
        data = call_data(session)
        self.assertEqual(data["category"], "phone")
        self.assertNotIn("keyword", data, "实测关键词字段名是 category，不是 keyword")
        self.assertEqual(data["crossBorder"], 1)
        self.assertTrue(session.calls[0]["url"].endswith(
            "/api/tiktokCategory/searchCategory.json"))

    def test_search_categories_blank_keyword_no_request(self):
        client, session, _ = make_client([])
        self.assertEqual(client.search_categories("   ", "GB"), [])
        self.assertEqual(session.calls, [])

    def test_category_attributes_requires_type_3(self):
        client, session, _ = make_client([ok([{"attrId": "100107"}])])
        rows = client.category_attributes("GB", "911112")
        self.assertEqual(rows, [{"attrId": "100107"}])
        data = call_data(session)
        self.assertEqual(data["type"], 3)
        self.assertEqual(data["categoryId"], "911112")
        self.assertEqual(data["crossBorder"], 1)
        # attr_type 可覆盖
        client, session, _ = make_client([ok([])])
        client.category_attributes("GB", "911112", attr_type=2)
        self.assertEqual(call_data(session)["type"], 2)

    def test_category_attributes_blank_category_no_request(self):
        client, session, _ = make_client([])
        self.assertEqual(client.category_attributes("GB", ""), [])
        self.assertEqual(session.calls, [])

    def test_category_history_primary_then_fallback(self):
        client, session, _ = make_client([
            ok([{"categoryId": "1"}]),
            FakeResponse({"code": -1, "msg": "err"}),
            ok([{"categoryId": "2"}]),
            FakeResponse({}, status_code=200, text="", json_error=True),
        ])
        self.assertEqual(client.category_history("GB"), [{"categoryId": "1"}])
        self.assertEqual(client.category_history("GB"), [{"categoryId": "2"}])
        self.assertEqual(client.category_history("GB"), [])

    def test_brands_returns_empty_on_any_failure(self):
        client, session, _ = make_client([
            FakeResponse({}, status_code=500, text="<html>err</html>"),
            FakeResponse({"code": -1, "msg": "err"}),
        ])
        self.assertEqual(client.brands("GB", "911112"), [])
        self.assertEqual(len(session.calls), 2, "先 POST 再 GET 兜底")
        self.assertTrue(session.calls[0]["url"].endswith(
            "/api/tiktokCategory/listCategoryBrands.json"))
        self.assertTrue(session.calls[1]["url"].endswith(
            "/api/tiktokCategory/listCategoryBrand.json"))

    def test_brands_success(self):
        client, _, _ = make_client([ok([{"brandId": "1"}])])
        self.assertEqual(client.brands("GB", "911112"), [{"brandId": "1"}])

    def test_check_banned_words_success_and_failure(self):
        client, session, _ = make_client([
            ok(["free shipping"]),
            FakeResponse({"code": -1, "msg": "err"}),
        ])
        self.assertEqual(client.check_banned_words("title", "desc"), ["free shipping"])
        data = call_data(session)
        self.assertEqual(data["platform"], "tiktok")
        self.assertEqual(data["productTitle"], "title")
        self.assertEqual(client.check_banned_words("t"), [])
        # 接口 data=null（实测「没有有效的违禁词！」）也返回 []
        client, _, _ = make_client([ok(None)])
        self.assertEqual(client.check_banned_words("t"), [])


# ---------------------------------------------------------------------------
# 保存：ZIP / multipart / NotReady / 写不重试
# ---------------------------------------------------------------------------
class SaveProductTests(unittest.TestCase):
    def test_save_builds_zip_and_multipart_field_order(self):
        payload = {"id": "123", "productName": "Phone Case", "site": "GB"}
        client, session, _ = make_client([ok({"id": "123"})])
        result = client.save_product(payload, op=OP_SAVE)
        self.assertEqual(result["code"], 0)
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/api/tiktokProduct/add.json"))
        self.assertIsNone(call["data"], "op/saveMode 也走 files 列表以固定 multipart 顺序")
        fields = call["files"]
        self.assertEqual([name for name, _ in fields], ["file", "op"])
        filename, blob, content_type = fields[0][1]
        self.assertEqual(filename, "blob")
        self.assertEqual(content_type, "application/zip")
        self.assertEqual(fields[1][1], (None, "1"))
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(zf.namelist(), [SAVE_NAME])
            self.assertEqual(json.loads(zf.read(SAVE_NAME).decode("utf-8")), payload)
        self.assertIn("tiktokProduct/edit", call["headers"]["Referer"])
        # 真实 multipart 体中 file 字段必须在 op 之前
        prepared = requests.Request(call["method"], call["url"],
                                    files=call["files"]).prepare()
        body = prepared.body
        self.assertIsInstance(body, bytes)
        self.assertLess(body.index(b'name="file"'), body.index(b'name="op"'))
        self.assertNotIn(b'name="saveMode"', body)

    def test_save_with_save_mode_appends_after_op(self):
        client, session, _ = make_client([ok({})])
        client.save_product({"id": "1"}, op=OP_PUBLISH, save_mode=SAVE_MODE_AS_DRAFT)
        call = session.calls[0]
        fields = call["files"]
        self.assertEqual([name for name, _ in fields], ["file", "op", "saveMode"])
        self.assertEqual(fields[1][1], (None, "2"))
        self.assertEqual(fields[2][1], (None, "AS_DRAFT"))
        prepared = requests.Request(call["method"], call["url"],
                                    files=call["files"]).prepare()
        body = prepared.body
        self.assertLess(body.index(b'name="file"'), body.index(b'name="op"'))
        self.assertLess(body.index(b'name="op"'), body.index(b'name="saveMode"'))

    def test_save_empty_payload_raises_without_request(self):
        client, session, _ = make_client([])
        with self.assertRaises(TikTokError):
            client.save_product({})
        with self.assertRaises(TikTokError):
            client.save_product(None)
        self.assertEqual(session.calls, [])

    def test_save_invalid_op_raises_without_request(self):
        client, session, _ = make_client([])
        with self.assertRaises(TikTokError):
            client.save_product({"id": "1"}, op="9")
        self.assertEqual(session.calls, [])

    def test_save_shop_error_becomes_not_ready(self):
        client, session, _ = make_client([
            FakeResponse({"code": -1, "msg": "产品未选择店铺或店铺无效！",
                          "data": None})])
        with self.assertRaises(TikTokNotReady) as ctx:
            client.save_product({"id": "1"})
        message = str(ctx.exception)
        self.assertIn("店铺", message)
        self.assertIn("绑定", message, "提示必须可操作")
        self.assertIn("TikTok", message)
        self.assertEqual(len(session.calls), 1, "写接口不得自动重试")

    def test_save_generic_error_raises_tiktok_error(self):
        client, _, _ = make_client([
            FakeResponse({"code": -1, "msg": "类目不可用", "data": None})])
        with self.assertRaises(TikTokError) as ctx:
            client.save_product({"id": "1"})
        self.assertNotIsInstance(ctx.exception, TikTokNotReady)
        self.assertIn("类目不可用", str(ctx.exception))

    def test_save_code0_failure_msg_is_error(self):
        client, _, _ = make_client([
            FakeResponse({"code": 0, "msg": "Successful",
                          "data": {"msg": "保存失败：类目不可用"}})])
        with self.assertRaises(TikTokError) as ctx:
            client.save_product({"id": "1"})
        self.assertIn("保存失败", str(ctx.exception))

    def test_save_success_returns_raw_payload(self):
        raw = {"code": 0, "msg": "Successful", "data": {"id": "9"}}
        client, _, _ = make_client([FakeResponse(raw), FakeResponse(raw)])
        self.assertEqual(client.save_product({"id": "9"}), raw)
        self.assertEqual(client.save({"id": "9"}, op=OP_MOVE_PENDING), raw)

    def test_save_busy_does_not_retry(self):
        client, session, sleeps = make_client([
            FakeResponse({"code": 2005, "msg": "系统繁忙,请稍后重试!!!", "data": None}),
            ok({"id": "1"}),
        ])
        with self.assertRaises(TikTokError):
            client.save_product({"id": "1"})
        self.assertEqual(len(session.calls), 1, "写接口繁忙也不重试（避免重复写入）")
        self.assertEqual(sleeps, [])

    def test_save_connection_error_does_not_retry(self):
        def boom():
            raise requests.ConnectionError("network down")

        client, session, sleeps = make_client([boom, ok({})])
        with self.assertRaises(TikTokError):
            client.save_product({"id": "1"})
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(sleeps, [])

    def test_publish_products_payload_and_not_ready(self):
        client, session, _ = make_client([
            ok({"ok": True}),
            ok({"ok": True}),
            FakeResponse({"code": -1, "msg": "产品未选择店铺或店铺无效！"}),
        ])
        client.publish_products(["1", "2"], shop_ids="3001")
        self.assertEqual(call_data(session, 0),
                         {"ids": "1,2", "shopIds": "3001"})
        self.assertTrue(session.calls[0]["url"].endswith(
            "/api/tiktokProduct/batchPublish.json"))
        client.publish_products("3", save_mode="AS_DRAFT")
        self.assertEqual(call_data(session, 1),
                         {"ids": "3", "saveMode": "AS_DRAFT"})
        with self.assertRaises(TikTokNotReady):
            client.publish_products(["4"])
        self.assertEqual(len(session.calls), 3, "写接口不自动重试")

    def test_delete_products_payload(self):
        client, session, _ = make_client([
            ok({"deleted": 2}),
            FakeResponse({"code": -1, "msg": "店铺无效"}),
        ])
        client.delete_products(["7", "8"])
        self.assertEqual(call_data(session, 0), {"ids": "7,8"})
        self.assertTrue(session.calls[0]["url"].endswith(
            "/api/tiktokProduct/batchProductDelete.json"))
        with self.assertRaises(TikTokNotReady):
            client.delete_products(["9"])


# ---------------------------------------------------------------------------
# _request：错误码 / 登录失效 / 繁忙重试边界
# ---------------------------------------------------------------------------
class RequestTests(unittest.TestCase):
    def test_login_code_2001(self):
        client, _, _ = make_client([FakeResponse({"code": 2001, "msg": "未登录"})])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("登录", str(ctx.exception))

    def test_http_401_login_error(self):
        client, _, _ = make_client([
            FakeResponse({}, status_code=401, text="unauthorized")])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("登录已失效", str(ctx.exception))

    def test_html_login_error(self):
        client, _, _ = make_client([
            FakeResponse(None, status_code=200, json_error=True,
                         text="<!DOCTYPE html><html>登录</html>")])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("登录", str(ctx.exception))

    def test_non_json_plain_text_error(self):
        client, _, _ = make_client([
            FakeResponse(None, status_code=200, json_error=True, text="oops")])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("非 JSON", str(ctx.exception))

    def test_non_dict_json_error(self):
        client, _, _ = make_client([FakeResponse([1, 2, 3])])
        with self.assertRaises(TikTokError):
            client.user_in()

    def test_unknown_business_code(self):
        client, _, _ = make_client([FakeResponse({"code": 500, "msg": "服务器错误"})])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("服务器错误", str(ctx.exception))

    def test_read_busy_retries_then_succeeds(self):
        busy = lambda: FakeResponse({"code": 2005, "msg": "系统繁忙,请稍后重试!!!"})
        client, session, sleeps = make_client([
            busy, busy, FakeResponse(user_in_payload({}))])
        self.assertEqual(client.user_in()["account"], "QINGHEMAOYI")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1.5, 3.0], "有界退避 1.5s/3.0s")

    def test_read_busy_exhausted_raises(self):
        busy = lambda: FakeResponse({"code": 2005, "msg": "系统繁忙,请稍后重试!!!"})
        client, session, sleeps = make_client([busy, busy, busy])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("繁忙", str(ctx.exception))
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1.5, 3.0])

    def test_read_timeout_is_retryable(self):
        def timeout():
            raise requests.Timeout("slow")

        client, session, _ = make_client([
            timeout, FakeResponse(user_in_payload({}))])
        self.assertTrue(client.check_login())
        self.assertEqual(len(session.calls), 2)

    def test_read_connection_error_exhausted(self):
        def boom():
            raise requests.ConnectionError("down")

        client, session, _ = make_client([boom, boom, boom])
        with self.assertRaises(TikTokError) as ctx:
            client.user_in()
        self.assertIn("网络连接失败", str(ctx.exception))

    def test_check_login_true_and_false(self):
        client, _, _ = make_client([FakeResponse(user_in_payload({}))])
        self.assertTrue(client.check_login())
        client, _, _ = make_client([FakeResponse({"code": 2001, "msg": "未登录"})])
        self.assertFalse(client.check_login())
        client, _, _ = make_client([FakeResponse({}, status_code=500, text="err")])
        self.assertFalse(client.check_login())

    def test_client_headers_and_base(self):
        client = TikTokClient("a=1", base="https://www.dianxiaomi.com/")
        self.assertEqual(client.base, "https://www.dianxiaomi.com")
        self.assertIn("tiktokProduct", client.s.headers["Referer"])
        self.assertEqual(client._url("/api/userIn.json"),
                         "https://www.dianxiaomi.com/api/userIn.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
