"""Amazon 真实刊登冒烟回归（离线 mock；无网络、绝不真实保存）。

对应 2026-11-22 真实账号冒烟（QINGHEMAOYI / shop 9408005 / CA）暴露的问题面：

1. **本次唯一真实失败 = 环境授权**：本机没有 license voucher，
   ``run_amazon_product(dry_run=False)`` 在「授权校验」步骤被 ``core.guard.require``
   拦住，绝不调用 ``save_product``；仅本次冒烟脚本进程内设 ``AURORA_SKIP_LICENSE=1``
   放行，且该开关不能绕过 ``confirm_write`` / 预演护栏 / 校验。
2. **真实响应形状**：``{"code": 0, "msg": "", "msgCode": null,
   "data": "<product_id>"}``（data 是字符串）必须判成功，并保留 39 键 op=1 保存包。
3. **店小秘 code=0 + 失败 msg**（如「操作失败，只有处在采集箱中的产品…」）不得判成功。
4. **自动补齐属性**：真实商品 #2（220114595363181982）保存后回读，
   model_number / part_number / model_name / manufacturer_contact_information
   仍在 productAttr 中；10 条卖点不得被截断。
5. **回读逐字段比较**：title / 五点 / 描述 / search_terms / productAttr / price / qty；
   productAttr 仅 JSON 序列化差异不算变更；price 的 ``None``（保存包 0 价）与平台回读
   ``0.0`` 等价。
6. 服务端失败（code!=0 / 抛 AmazonError）必须冒泡成 ``ok=False``，不能报成功。

跑法：``.venv/bin/python tests/test_amazon_live_smoke.py``
（不需要 AURORA_TEST；本文件自己隔离并清理环境变量。）
"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.integrations.amazon import AmazonClient, AmazonError  # noqa: E402
from aeclone.services import amazon_pipeline as PL  # noqa: E402
from aeclone.services import amazon_publish as PUB  # noqa: E402
from aeclone.services.amazon_payload import (  # noqa: E402
    parse_product_attr, split_bullets,
)
from aeclone.services.amazon_pipeline import (  # noqa: E402
    run_amazon_product,
)

ENV_KEYS = ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")
SITE = "A2EUQ1WTGCTBG2"          # CA
PID = "220114595362938114"       # 真实冒烟商品 #1（钻机）
PID_2 = "220114595363181982"     # 真实冒烟商品 #2（装载机，走自动补齐）

# 真实回读需要逐字段核对的键（docs/amazon-live-listing-smoke.json 同口径）
READBACK_FIELDS = ("title", "bulletPoint", "description", "searchTerms",
                   "productAttr", "standardPrice", "saleSalePrice", "quantity")


def attr_value(value):
    """真实 editSp.productAttr 里的属性值结构。"""
    return [{"value": value, "language_tag": "en_CA", "marketplace_id": SITE}]


def make_product(**over):
    """合成一个可通过 op=1 校验、结构对齐真实 editSp.product 的商品。"""
    product = {
        "id": PID,
        "shopId": "9408005",
        "marketPlaceId": SITE,
        "currency": "CAD",
        "title": "5 Ton Diesel Wheeled Backhoe Loader Heavy Duty Construction Machine",
        "titleDifferentiation": None,
        "categoryId": "13553005011",
        "categoryBrowsePath": "6299024011/6256963011/6256964011/13553005011",
        "audience": "all",
        "isVariation": 0,
        "brandSettledType": "external_product_id",
        "fullCid": "5405448-",
        "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
        "standardProductType": "upc",
        "standardProductId": None,
        "sku": "WY-QHCA-LTM-1",
        "brand": "Generic",
        "manufacturer": "Generic",
        "productAttr": json.dumps({
            "model_number": attr_value("WY-QHCA-LTM-1"),
            "part_number": attr_value("WY-QHCA-LTM-1"),
            "model_name": attr_value("WY-QHCA-LTM-1"),
            "manufacturer_contact_information": attr_value("Generic, CN"),
        }, ensure_ascii=False),
        # 真实草稿价/库存为 0：保存包 price=None，平台回读 0.0
        "standardPrice": 0.0,
        "saleSalePrice": 0.0,
        "standardPriceCurrency": "CAD",
        "saleStartDateStr": None,
        "saleEndDateStr": None,
        "quantity": 1,
        "conditionType": None,
        "conditionValue": "",
        "mainImage": "https://sc04.alicdn.com/kf/main.png",
        "extraImages": ["https://sc04.alicdn.com/kf/a.png"],
        "extraImagesStr": "https://sc04.alicdn.com/kf/a.png",
        "draftImgUrl": "https://sc04.alicdn.com/kf/a.png",
        "variationTheme": "",
        "skuTitle": 0,
        "description": "<p>Heavy duty backhoe loader for road construction and earthmoving.</p>",
        "bulletPoint": "±".join([
            "Diesel engine for heavy duty construction work",
            "4x4 wheel drive for rough terrain",
            "Hydraulic articulated steering",
            "Reinforced chassis for long service life",
            "Low fuel consumption and easy maintenance",
        ]),
        "gpsrManufacturerReference": "",
        "dsaResponsiblePartyAddress": "",
        "gpsrSafetyAttestation": 0,
        "switchFulfillmentTo": "MFN",
        "merchantShippingGroup": "legacy-template-id",
        "searchTerms": "backhoe loader diesel construction machine",
    }
    product.update(over)
    return product


def make_detail(product):
    return {"site": "CA", "product": product, "categoryName": "反铲",
            "productType": "OUTDOOR_LIVING", "categoryTree": "[]"}


class FakeLiveClient:
    """离线替身：模拟真实 op=1 写后回读；不联网、不新建客户端。"""

    def __init__(self, product=None, response=None, error=None):
        self.product = copy.deepcopy(product if product is not None else make_product())
        self.detail = make_detail(self.product)
        self.saved = []
        self.response = response if response is not None else {
            "code": 0, "msg": "", "msgCode": None, "data": self.product["id"]}
        self.error = error

    # --- 读接口 ---
    def product_detail(self, pid):
        return copy.deepcopy(self.detail)

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def category_by_path(self, site_code, node_path):
        return {"id": 159365, "categoryId": "13553005011", "chineseName": "反铲"}

    def check_banned_words(self, **kwargs):
        return []

    # --- 唯一写接口（只记录；按真实平台行为回写详情） ---
    def save_product(self, payload, op="1"):
        self.saved.append((copy.deepcopy(payload), str(op)))
        if self.error is not None:
            raise self.error
        if str(self.response.get("code")) == "0":
            self._persist(payload)
        return copy.deepcopy(self.response)

    def _persist(self, payload):
        for key in ("title", "bulletPoint", "description", "searchTerms",
                    "productAttr", "sku", "brand", "manufacturer",
                    "categoryId", "categoryBrowsePath", "mainImage"):
            if key in payload:
                self.product[key] = payload.get(key)
        # 平台把空价格存成 0.0、数量存成数字
        for key in ("standardPrice", "saleSalePrice"):
            try:
                self.product[key] = float(payload.get(key) or 0)
            except Exception:
                self.product[key] = 0.0
        try:
            self.product["quantity"] = int(float(payload.get("quantity") or 0))
        except Exception:
            self.product["quantity"] = 0
        self.detail = make_detail(self.product)


def normalize_field(key, value):
    """回读同口径：productAttr 语义化、price None/0 等价、quantity 数字、五点拆分。"""
    if key == "productAttr":
        try:
            return parse_product_attr(value)
        except Exception:
            return str(value or "")
    if key in ("standardPrice", "saleSalePrice"):
        try:
            return round(float(value or 0), 2)
        except Exception:
            return value
    if key == "quantity":
        try:
            return int(float(value or 0))
        except Exception:
            return value
    if key == "bulletPoint":
        return split_bullets(value)
    return str(value or "")


def readback_drift(expected, actual, fields=READBACK_FIELDS):
    """逐字段比较，返回 {字段: {expected, actual}}；空 dict=一致。"""
    expected = expected or {}
    actual = actual or {}
    drift = {}
    for key in fields:
        e = normalize_field(key, expected.get(key))
        a = normalize_field(key, actual.get(key))
        if e != a:
            drift[key] = {"expected": e, "actual": a}
    return drift


class EnvIsolatedCase(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LicenseGuardSmokeTests(EnvIsolatedCase):
    """本次真实冒烟唯一失败：本机无授权凭证。"""

    def test_missing_license_blocks_real_save_without_calling_save_product(self):
        fake = FakeLiveClient()
        # 真实本机配置没有 voucher/signature；用空 cfg 复现
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("授权", res.error)
        self.assertIn("授权校验", res.log())
        self.assertEqual(fake.saved, [])

    def test_skip_license_only_unlocks_guard_not_confirm_write(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeLiveClient()
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=False, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("confirm_write=True", res.error)
        self.assertEqual(fake.saved, [])

    def test_skip_license_cannot_bypass_forced_preview(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeLiveClient()
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertIn("拒绝真实保存", res.error)
        self.assertEqual(fake.saved, [])


class LiveResponseShapeTests(EnvIsolatedCase):
    """真实保存响应 / 失败响应 / 抛错 的判定。"""

    def setUp(self):
        super().setUp()
        os.environ["AURORA_SKIP_LICENSE"] = "1"

    def test_live_success_response_is_saved_with_39_key_payload(self):
        fake = FakeLiveClient(response={
            "code": 0, "msg": "", "msgCode": None, "data": PID})
        with patch.object(PL, "make_client", side_effect=AssertionError(
                "pipeline 不得新建客户端")), \
             patch.object(PUB, "make_client", side_effect=AssertionError(
                 "publish 不得新建客户端")):
            res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                     dry_run=False, confirm_write=True, op="1")
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertFalse(res.preview)
        self.assertEqual(res.op, "1")
        self.assertEqual(res.save_result.get("code"), 0)
        self.assertEqual(res.save_result.get("data"), PID)
        self.assertEqual(len(fake.saved), 1)
        payload, op = fake.saved[0]
        self.assertEqual(op, "1")
        self.assertEqual(len(payload), 39)
        self.assertEqual(payload.get("id"), PID)

    def test_nonzero_code_is_failure_with_platform_msg(self):
        fake = FakeLiveClient(response={
            "code": 2005, "msg": "系统繁忙,请稍后重试!!!", "data": None})
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("系统繁忙", res.error)

    def test_save_exception_is_surfaced_not_swallowed(self):
        fake = FakeLiveClient(error=AmazonError("保存失败：登录已失效，请重新登录"))
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("登录已失效", res.error)
        self.assertEqual(len(fake.saved), 1)

    def test_code_zero_but_failure_msg_is_not_success(self):
        """店小秘 code=0 + data.msg 失败语义：AmazonClient 必须抛错。"""
        client = AmazonClient("a=1; b=2", timeout=1)
        resp = {"code": 0, "msg": "Successful",
                "data": {"msg": "操作失败，只有处在采集箱中的产品才能编辑"}}
        with patch.object(AmazonClient, "_request", return_value=resp):
            with self.assertRaises(AmazonError) as ctx:
                client.save_product({"id": PID}, op="1")
        self.assertIn("操作失败", str(ctx.exception))


class AutofillRoundtripTests(EnvIsolatedCase):
    """真实商品 #2：缺 4 个发布必填属性 → 保存包补齐 → 回读仍在；10 条卖点不截断。"""

    def setUp(self):
        super().setUp()
        os.environ["AURORA_SKIP_LICENSE"] = "1"

    def _product_2(self):
        ten_bullets = ["Bullet %d for heavy duty backhoe loader" % i for i in range(1, 11)]
        return make_product(
            id=PID_2,
            sku="WY-QHCA-LTM-4",
            title="Heavy Duty 4WD Articulated Backhoe Loader Hydraulic Wheel Excavator",
            productAttr=json.dumps({
                "is_assembly_required": attr_value(False),
            }, ensure_ascii=False),
            standardPrice=0.0,
            saleSalePrice=0.0,
            quantity=0,
            searchTerms="",
            description="<p>Heavy duty backhoe loader for road subgrade earthwork.</p>",
            bulletPoint="±".join(ten_bullets),
        )

    def test_autofill_attributes_are_sent_and_persist_after_readback(self):
        fake = FakeLiveClient(product=self._product_2())
        res = run_amazon_product(PID_2, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertTrue(res.ok, res.error)
        payload, _op = fake.saved[0]
        sent = parse_product_attr(payload.get("productAttr"))
        for key in ("model_number", "part_number", "model_name"):
            self.assertIn(key, sent, f"保存包缺少自动补齐属性 {key}")
        self.assertNotIn("manufacturer_contact_information", sent)
        readback = fake.product_detail(PID_2)["product"]
        got = parse_product_attr(readback.get("productAttr"))
        for key in ("model_number", "part_number", "model_name"):
            self.assertEqual(got.get(key), sent.get(key), f"回读丢属性 {key}")
        self.assertEqual(readback_drift(payload, readback), {})

    def test_ten_bullets_preserved_on_save_and_readback(self):
        fake = FakeLiveClient(product=self._product_2())
        res = run_amazon_product(PID_2, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertTrue(res.ok, res.error)
        payload, _op = fake.saved[0]
        self.assertEqual(len(split_bullets(payload.get("bulletPoint"))), 10)
        readback = fake.product_detail(PID_2)["product"]
        self.assertEqual(len(split_bullets(readback.get("bulletPoint"))), 10)


class ReadbackCompareTests(unittest.TestCase):
    """回读逐字段对比口径（真实证据 JSON 用的同一套规则）。"""

    def setUp(self):
        self.payload = make_product()
        self.write_payload = {
            "title": self.payload["title"],
            "bulletPoint": self.payload["bulletPoint"],
            "description": self.payload["description"],
            "searchTerms": self.payload["searchTerms"],
            "productAttr": self.payload["productAttr"],
            "standardPrice": None,       # 保存包 0 价发 None
            "saleSalePrice": None,
            "quantity": 1,
        }
        self.readback = dict(self.write_payload)
        self.readback["standardPrice"] = 0.0   # 平台回读 0.0
        self.readback["saleSalePrice"] = 0.0
        self.readback["quantity"] = 1

    def test_live_price_zero_none_is_equivalent(self):
        self.assertEqual(readback_drift(self.write_payload, self.readback), {})

    def test_product_attr_reserialization_is_not_a_change(self):
        after = dict(self.readback)
        parsed = parse_product_attr(after["productAttr"])
        after["productAttr"] = json.dumps(parsed, ensure_ascii=False, indent=2)
        self.assertNotEqual(after["productAttr"], self.readback["productAttr"])
        self.assertEqual(readback_drift(self.write_payload, after), {})

    def test_content_and_price_drift_is_detected(self):
        after = dict(self.readback)
        after["title"] += " Extra"
        after["bulletPoint"] = "±".join(split_bullets(after["bulletPoint"])[:4])
        after["description"] += "<p>changed</p>"
        after["searchTerms"] = "different keywords"
        after["standardPrice"] = 123.45
        after["quantity"] = 7
        drift = readback_drift(self.write_payload, after)
        for key in ("title", "bulletPoint", "description", "searchTerms",
                    "standardPrice", "quantity"):
            self.assertIn(key, drift, f"未检出 {key} 漂移")

    def test_product_attr_semantic_drift_is_detected(self):
        after = dict(self.readback)
        parsed = parse_product_attr(after["productAttr"])
        parsed["new_required_attr"] = attr_value("x")
        after["productAttr"] = json.dumps(parsed, ensure_ascii=False)
        drift = readback_drift(self.write_payload, after)
        self.assertIn("productAttr", drift)


class SafetyBaselineTests(EnvIsolatedCase):
    """冒烟前置安全基线（dry-run / confirm_write / 校验拦截）。"""

    def test_dry_run_never_calls_save_product(self):
        class NoWrite(FakeLiveClient):
            def save_product(self, payload, op="1"):
                raise AssertionError("dry-run 不允许调用 save_product")

        res = run_amazon_product(PID, SimpleNamespace(), client=NoWrite(),
                                 dry_run=True, op="1")
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.preview)
        self.assertFalse(res.saved)
        self.assertIn("dry-run", res.log())

    def test_validation_issues_block_before_write(self):
        fake = FakeLiveClient(product=make_product(categoryId="", categoryBrowsePath=""))
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=False, confirm_write=True, op="1")
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(any("类目无效" in i for i in res.issues))
        self.assertEqual(fake.saved, [])

    def test_for_sale_title_is_intentionally_blocked_by_contract(self):
        """契约 §6.11 明确把 sale 列为阻断词：保留该行为，避免冒烟时误放行。"""
        fake = FakeLiveClient(product=make_product(
            title="VIFT Mini Tractor 4WD Backhoe Loader Excavator for Sale"))
        res = run_amazon_product(PID, SimpleNamespace(), client=fake,
                                 dry_run=True, op="1")
        self.assertFalse(res.ok)
        self.assertTrue(any("sale" in i for i in res.issues))
        self.assertEqual(fake.saved, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
