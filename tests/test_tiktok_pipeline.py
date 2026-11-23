"""TikTok 刊登流水线 / 保存收口离线单测（无网络、无真实写入）。

覆盖 Lead 指定的 6 类硬安全点：
1) 缺 confirm_write 不写；
2) forced_preview（AURORA_NO_SAVE / AURORA_TEST）不写；
3) 注入 client 必须复用（绝不新建真实客户端）；
4) op=1 / op=3 / op=2+AS_DRAFT 的 (payload, op, saveMode) 正确，未知 op 不写；
5) AI 失败不阻断（只进 warnings，保留手填内容）；
6) 校验阻断项（站点/类目/标题/图片/价格/库存/重量/五点）→ 不发起写。

跑法：``.venv/bin/python tests/test_tiktok_pipeline.py``
"""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.core.guard import LicenseError
from aeclone.integrations.tiktok import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, OP_SCHEDULE, SAVE_MODE_AS_DRAFT,
    TikTokError, TikTokNotReady,
)
from aeclone.services import tiktok_pipeline as PL
from aeclone.services import tiktok_publish as PUB
from aeclone.services.tiktok_payload import DEFAULT_LIMITS
from aeclone.services.tiktok_pipeline import (
    OP_LABELS, SUPPORTED_OPS, TikTokRunResult, TikTokStep,
    ai_generate_all, ai_generate_field, forced_preview, prepare_save,
    run_tiktok_product,
)

ENV_KEYS = ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")

IMAGE_1 = "https://img.example.com/1.jpg"
IMAGE_2 = "https://img.example.com/2.jpg"
IMAGE_3 = "https://img.example.com/3.jpg"

TITLE = "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W"
DESCRIPTION = ("<p>Great portable speaker for outdoor travel and daily use.</p>"
               "<ul><li>Battery: 20h</li><li>Waterproof: IPX7</li></ul>")

AI_TITLE = "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W Bass"
AI_DESC = ("<p>AI generated description for the outdoor speaker.</p>"
           "<ul><li>Battery: 20h</li><li>Waterproof: IPX7</li></ul>")
AI_FEATURES = ('["Fast Charging: 30 min full charge","Deep Bass: rich sound",'
               '"Portable: 300g light"]')
AI_TERMS = "portable speaker bluetooth waterproof outdoor"


def make_product(**over):
    """合成一个「能过校验」的 GB 商品（结构对齐 gettiktokProduct 的 data.product）。"""
    product = {
        "id": "1729000000000001",
        "shopId": "9408005",
        "site": "GB",
        "siteIds": ["site-shop-opaque-1"],
        "dxmState": "draft",
        "productName": TITLE,
        "description": DESCRIPTION,
        "categoryId": "601234",
        "categoryList": [{"categoryId": "601234", "categoryName": "Portable Speakers"}],
        "isNewCategory": 0,
        "productType": 1,
        "mainImage": "|".join([IMAGE_1, IMAGE_2]),
        "draftImgUrl": "|".join([IMAGE_1, IMAGE_2]),
        "packageWeight": 0.5,
        "packageLength": 10,
        "packageWidth": 10,
        "packageHeight": 10,
        "packageDimensionUnit": "CENTIMETER",
        "packageDimensionMode": "PRODUCT",
        "warehouseId": "WH-1",
        "variationList": [{
            "_combineId": "c1",
            "sellerSku": "SPK-001",
            "shopId": "9408005",
            "originalPrice": 19.99,
            "availableStock": 50,
            "skuProperties": [],
        }],
        "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
        "brandId": "700001",
        "brandInfo": "Generic",
        "productUnit": "100000015",
        "productAttributes": "[]",
        "productCertifications": "[]",
        "unknownCamelField": {"keep": True},
        "variationBos": {"should": "drop"},
    }
    product.update(over)
    return product


def make_detail(**over):
    """gettiktokProduct 的 data 形状：product + category（含 isLeaf）。"""
    detail = {
        "product": make_product(**over),
        "category": {"id": "601234", "isLeaf": 1},
        "shopId": "9408005",
        "isGlobalMode": False,
    }
    return detail


def make_us_detail(**over):
    base = {"site": "US", "siteIds": [], "keyProductFeatures": [], "searchTerms": []}
    base.update(over)
    return make_detail(**base)


def make_cfg(**over):
    cfg = {
        "api_base": "", "api_key": "", "timeout": 5,
        "tiktok_cookies": "", "manual_cookies": {},
        "tiktok_shop_id": "", "tiktok_shop_name": "", "tiktok_site": "",
        "tiktok_sku": "", "tiktok_price": "", "tiktok_stock": "",
        "tiktok_weight": "", "tiktok_length": "", "tiktok_width": "",
        "tiktok_height": "", "tiktok_dimension_unit": "",
        "tiktok_warehouse_id": "", "tiktok_product_unit": "",
        "tiktok_brand_id": "", "tiktok_manufacturer_id": "",
        "tiktok_responsible_person_id": "", "tiktok_product_attributes": "",
        "tiktok_key_feature_count": 5,
    }
    cfg.update(over)
    return cfg


class FakeClient:
    """离线替身：读接口返回固定详情；save/publish 只记录不联网。"""

    def __init__(self, detail=None, shops=None, sites=None, limits=None,
                 banned=None, save_error=None, search_leaf=None):
        self.detail = detail if detail is not None else make_detail()
        self.detail_calls = 0
        self.shops_calls = 0
        self.sites_calls = 0
        self.limits_calls = []
        self.search_calls = []
        self.banned_calls = []
        self.saved = []
        self.published = []
        self.save_attempts = 0
        if shops is None:
            shops = [{"id": "9408005", "name": "QingHe TikTok", "platform": "tiktok"}]
        self.shops_data = shops
        if sites is None:
            sites = [
                {"site": "GB", "code": "GB", "site_shop_id": ""},
                {"site": "US", "code": "US", "site_shop_id": ""},
            ]
        self.sites_data = sites
        self.limits_data = limits
        self.banned = list(banned or [])
        self.save_error = save_error
        self.search_leaf = search_leaf

    # ---- 只读接口 ----
    def product_detail(self, product_id):
        self.detail_calls += 1
        if isinstance(self.detail, Exception):
            raise self.detail
        return copy.deepcopy(self.detail)

    def shops(self):
        self.shops_calls += 1
        if isinstance(self.shops_data, Exception):
            raise self.shops_data
        return copy.deepcopy(self.shops_data)

    def sites(self, shop_id=""):
        self.sites_calls += 1
        if isinstance(self.sites_data, Exception):
            raise self.sites_data
        return copy.deepcopy(self.sites_data)

    def site_limits(self, site=""):
        self.limits_calls.append(site)
        if isinstance(self.limits_data, Exception):
            raise self.limits_data
        if isinstance(self.limits_data, dict):
            merged = dict(DEFAULT_LIMITS)
            merged.update(self.limits_data)
            return merged
        return dict(DEFAULT_LIMITS)

    def search_categories(self, keyword, site, shop_id="-1", cross_border=True):
        self.search_calls.append((keyword, site))
        if isinstance(self.search_leaf, Exception):
            raise self.search_leaf
        if self.search_leaf is None:
            return []
        return [{"categoryId": str(keyword), "isLeaf": self.search_leaf}]

    def check_banned_words(self, title="", description=""):
        self.banned_calls.append((title, description))
        return list(self.banned)

    # ---- 唯一写接口（记录参数，不联网）----
    def save_product(self, payload, op=OP_SAVE, save_mode=""):
        self.save_attempts += 1
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((copy.deepcopy(payload), str(op), str(save_mode)))
        return {"code": 0, "msg": "操作成功", "data": {"id": payload.get("id")}}

    def publish_products(self, ids, shop_ids="", save_mode=""):
        self.save_attempts += 1
        if self.save_error is not None:
            raise self.save_error
        self.published.append((list(ids) if isinstance(ids, (list, tuple)) else ids,
                               shop_ids, save_mode))
        return {"code": 0, "msg": "操作成功"}


class FakeAI:
    """离线 AI 替身：按提示词标记返回固定文本。"""

    def __init__(self, replies=None):
        self.replies = replies or {
            "请直接输出最终标题": AI_TITLE,
            "请直接输出 HTML 片段": AI_DESC,
            "JSON 字符串数组": AI_FEATURES,
            "小写关键词": AI_TERMS,
        }
        self.prompts = []

    def chat(self, model, messages, max_tokens=2048, temperature=0.6):
        content = str((messages or [{}])[0].get("content") or "")
        self.prompts.append(content)
        for marker, reply in self.replies.items():
            if marker in content:
                return reply
        return ""


class EnvIsolatedCase(unittest.TestCase):
    """清空 AURORA_* 环境变量，测试结束恢复（避免污染其它测试）。"""

    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def allow_write(self):
        """授权校验放行（AURORA_SKIP_LICENSE=1 由 core.guard 内部识别）。"""
        os.environ["AURORA_SKIP_LICENSE"] = "1"


# ============================================================================
# A. 常量 / 结果对象
# ============================================================================
class ConstantsTests(EnvIsolatedCase):
    def test_op_labels_and_supported_ops(self):
        self.assertEqual(OP_SAVE, "1")
        self.assertEqual(OP_MOVE_PENDING, "3")
        self.assertEqual(OP_PUBLISH, "2")
        self.assertEqual(OP_SCHEDULE, "5")
        self.assertEqual(OP_LABELS[OP_SAVE], "保存草稿")
        self.assertEqual(OP_LABELS[OP_MOVE_PENDING], "保存并移入待发布")
        self.assertEqual(OP_LABELS[OP_PUBLISH], "保存并发布")
        self.assertEqual(SUPPORTED_OPS, (OP_SAVE, OP_MOVE_PENDING, OP_PUBLISH))

    def test_completion_labels(self):
        res = TikTokRunResult(product_id="1", preview=True, ok=True)
        self.assertIn("预演完成", res.completion_label)
        res = TikTokRunResult(product_id="1", preview=True, ok=False)
        self.assertIn("预演失败", res.completion_label)
        res = TikTokRunResult(product_id="1", ok=True, saved=True)
        self.assertEqual(res.completion_label, "保存成功")
        res = TikTokRunResult(product_id="1", ok=False)
        self.assertEqual(res.completion_label, "保存失败")

    def test_log_and_incomplete_steps(self):
        res = TikTokRunResult(product_id="1", steps=[
            TikTokStep("a", True, "ok"), TikTokStep("b", False, "bad")])
        log = res.log()
        self.assertIn("✓ a: ok", log)
        self.assertIn("✗ b: bad", log)
        self.assertEqual(len(res.incomplete_steps), 1)

    def test_forced_preview(self):
        self.assertFalse(forced_preview())
        os.environ["AURORA_NO_SAVE"] = "1"
        self.assertTrue(forced_preview())
        os.environ.pop("AURORA_NO_SAVE")
        os.environ["AURORA_TEST"] = "1"
        self.assertTrue(forced_preview())


# ============================================================================
# B. prepare_save（只读）
# ============================================================================
class PrepareSaveTests(EnvIsolatedCase):
    def test_prepare_save_read_only(self):
        fake = FakeClient()
        detail, payload, issues, warnings = prepare_save("p1", make_cfg(), client=fake)
        self.assertEqual(detail["product"]["id"], "1729000000000001")
        self.assertEqual(issues, [])
        self.assertIsInstance(warnings, list)
        self.assertEqual(fake.saved, [])
        self.assertEqual(fake.save_attempts, 0)
        # 保存包：op 写 int、variationBos 已删、siteIds 原样保留（不伪造）
        self.assertEqual(payload["op"], 1)
        self.assertNotIn("variationBos", payload)
        self.assertEqual(payload["site"], "GB")
        self.assertEqual(payload["shopId"], "9408005")
        self.assertEqual(payload["siteIds"], ["site-shop-opaque-1"])
        self.assertEqual(payload["productName"], TITLE)
        self.assertEqual(payload["mainImage"], "|".join([IMAGE_1, IMAGE_2]))

    def test_prepare_save_applies_overrides(self):
        fake = FakeClient()
        _, payload, issues, _ = prepare_save(
            "p1", make_cfg(), overrides={
                "title": "Manual TikTok Title For Bluetooth Speaker Outdoor Portable",
                "description": "<p>Manual description</p>",
                "images": [IMAGE_3],
                "price": "25.5", "stock": "8", "seller_sku": "MANUAL-1",
                "weight": "1.2", "dimension_unit": "INCH",
            }, client=fake)
        self.assertEqual(issues, [])
        self.assertEqual(payload["productName"],
                         "Manual TikTok Title For Bluetooth Speaker Outdoor Portable")
        self.assertEqual(payload["mainImage"], IMAGE_3)
        row = payload["variationList"][0]
        self.assertEqual(row["originalPrice"], 25.5)
        self.assertEqual(row["availableStock"], 8)
        self.assertEqual(row["sellerSku"], "MANUAL-1")
        self.assertEqual(payload["packageWeight"], 1.2)
        self.assertEqual(payload["packageDimensionUnit"], "INCH")

    def test_prepare_save_missing_id(self):
        with self.assertRaises(ValueError):
            prepare_save("", make_cfg(), client=FakeClient())

    def test_prepare_save_detail_error_is_wrapped(self):
        fake = FakeClient(detail=TikTokError("读不到商品（接口返回空）"))
        with self.assertRaises(TikTokError) as ctx:
            prepare_save("p1", make_cfg(), client=fake)
        self.assertIn("读取商品", str(ctx.exception))

    def test_prepare_save_not_ready_detail(self):
        fake = FakeClient(detail=TikTokNotReady("未绑定 TikTok 店铺"))
        with self.assertRaises(TikTokNotReady) as ctx:
            prepare_save("p1", make_cfg(), client=fake)
        self.assertIn("绑定", str(ctx.exception))

    def test_prepare_save_maps_site_ids_when_client_knows(self):
        fake = FakeClient(sites=[{"site": "GB", "site_shop_id": "site-abc",
                                  "code": "GB"}])
        _, payload, _, _ = prepare_save("p1", make_cfg(), client=fake)
        self.assertEqual(payload["siteIds"], ["site-abc"])

    def test_prepare_save_publish_strictness(self):
        detail = make_detail(keyProductFeatures=["a", "b", "c"])
        fake = FakeClient(detail=detail)
        # 草稿保存：非 US 带五点只 warning，不阻断
        _, _, issues_draft, warnings_draft = prepare_save(
            "p1", make_cfg(), client=fake)
        self.assertEqual(issues_draft, [])
        self.assertTrue(any("五点" in w for w in warnings_draft))
        # 发布动作：for_publish=True → 阻断
        _, _, issues_pub, _ = prepare_save(
            "p1", make_cfg(), client=FakeClient(detail=detail), op=OP_PUBLISH)
        self.assertTrue(any("不支持五点描述" in i for i in issues_pub))


# ============================================================================
# C. 预演（dry_run）默认路径
# ============================================================================
class DryRunTests(EnvIsolatedCase):
    def test_default_dry_run_never_saves(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.preview)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])
        self.assertEqual(fake.save_attempts, 0)
        self.assertEqual(res.op, OP_SAVE)
        self.assertEqual(res.site, "GB")
        self.assertEqual(res.site_name, "英国")
        self.assertEqual(res.currency, "GBP")
        self.assertEqual(res.title, TITLE)
        self.assertIn("IPX7", res.description)
        self.assertEqual(res.category_id, "601234")
        self.assertEqual(res.limits.get("nameMin"), 25)
        self.assertEqual(res.limits.get("nameMax"), 255)
        self.assertIn("预演", res.log())
        self.assertIn("未保存商品", res.completion_label)

    def test_dry_run_confirm_write_still_no_save(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake, dry_run=True,
                                 confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_dry_run_op_move_pending(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake, op=OP_MOVE_PENDING)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.op, OP_MOVE_PENDING)
        self.assertIn("保存并移入待发布", res.log())
        self.assertEqual(fake.save_attempts, 0)

    def test_limits_injected_into_validation(self):
        fake = FakeClient(limits={"nameMin": 100, "nameMax": 300})
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertEqual(fake.limits_calls, ["GB"])
        self.assertTrue(any("标题长度" in i for i in res.issues))

    def test_banned_words_is_hint_only(self):
        fake = FakeClient(banned=["best", "cure"])
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.banned_words, ["best", "cure"])
        self.assertTrue(any("违禁词命中" in w for w in res.warnings))

    def test_chinese_title_is_warning_not_blocking(self):
        title = "无线蓝牙音箱防水户外便携20W长续航重低音双声道大音量"
        fake = FakeClient(detail=make_detail(productName=title))
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(any("中文" in w for w in res.warnings))
        self.assertFalse(any("中文" in i for i in res.issues))


# ============================================================================
# D. 安全护栏：缺 confirm_write / forced_preview / guard
# ============================================================================
class SafetyGateTests(EnvIsolatedCase):
    def test_missing_confirm_write_never_saves(self):
        fake = FakeClient()
        with patch.object(PL, "make_client", side_effect=AssertionError("不应新建客户端")):
            res = run_tiktok_product("p1", make_cfg(), client=fake,
                                     dry_run=False, confirm_write=False)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("confirm_write", res.error)
        self.assertEqual(fake.save_attempts, 0)
        self.assertEqual(fake.detail_calls, 0)  # 连详情都不读

    def test_env_no_save_blocks_real_write(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertIn("拒绝真实保存", res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_env_test_blocks_real_write(self):
        os.environ["AURORA_TEST"] = "1"
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("拒绝真实保存", res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_license_failure_blocks_write(self):
        # 没有 AURORA_SKIP_LICENSE，cfg 也没有凭证 → guard.require 抛 LicenseError
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertIn("授权", res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_invalid_payload_blocks_write(self):
        # 无类目 → 校验阻断，即使 confirm_write=True 也不写
        self.allow_write()
        fake = FakeClient(detail=make_detail(categoryId="", categoryList=[]))
        res = run_tiktok_product("p1", make_cfg(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertIn("保存前校验未通过", res.error)
        self.assertEqual(fake.save_attempts, 0)
        self.assertTrue(any("类目" in i for i in res.issues))

    def test_unknown_op_never_saves(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake, op="9",
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertIn("未知操作", res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_schedule_op_rejected(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake, op=OP_SCHEDULE,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertIn("定时发布", res.error)
        self.assertEqual(fake.save_attempts, 0)

    def test_as_draft_only_for_publish(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake, op=OP_SAVE,
                                 save_mode=SAVE_MODE_AS_DRAFT)
        self.assertFalse(res.ok)
        self.assertIn("AS_DRAFT", res.error)
        self.assertEqual(fake.save_attempts, 0)


# ============================================================================
# E. 注入 client 必须复用
# ============================================================================
class ClientReuseTests(EnvIsolatedCase):
    def test_injected_client_reused_for_preview(self):
        fake = FakeClient()
        with patch.object(PL, "make_client",
                          side_effect=AssertionError("不应新建真实客户端")):
            res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertGreaterEqual(fake.detail_calls, 1)
        self.assertGreaterEqual(fake.shops_calls, 1)

    def test_injected_client_reused_for_real_save(self):
        self.allow_write()
        fake = FakeClient()
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建真实客户端")), \
                patch.object(PL, "make_client",
                             side_effect=AssertionError("不应新建真实客户端")):
            res = run_tiktok_product("p1", make_cfg(), client=fake,
                                     dry_run=False, confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(fake.save_attempts, 1)


# ============================================================================
# F. 真实写：op / saveMode / payload 收尾正确
# ============================================================================
class RealWriteTests(EnvIsolatedCase):
    def setUp(self):
        super().setUp()
        self.allow_write()

    def _run(self, fake, **kwargs):
        return run_tiktok_product("p1", make_cfg(), client=fake,
                                  dry_run=False, confirm_write=True, **kwargs)

    def test_op_save_write(self):
        fake = FakeClient()
        res = self._run(fake, op=OP_SAVE)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(fake.save_attempts, 1)
        payload, op, save_mode = fake.saved[0]
        self.assertEqual(op, OP_SAVE)
        self.assertEqual(save_mode, "")
        self.assertEqual(payload["op"], 1)
        self.assertNotIn("variationBos", payload)
        self.assertEqual(payload["siteIds"], ["site-shop-opaque-1"])
        self.assertEqual(res.save_result.get("code"), 0)
        self.assertIn("保存成功", res.completion_label)

    def test_submit_callback_runs_after_validation_and_before_write(self):
        fake = FakeClient()

        def before_submit():
            self.assertEqual(fake.save_attempts, 0)
            raise RuntimeError("journal stopped")

        result = self._run(fake, before_submit=before_submit)
        self.assertIn("journal stopped", result.error)
        self.assertFalse(result.saved)
        self.assertEqual(fake.save_attempts, 0)

    def test_op_move_pending_write(self):
        fake = FakeClient()
        res = self._run(fake, op=OP_MOVE_PENDING)
        self.assertTrue(res.ok, res.error)
        payload, op, save_mode = fake.saved[0]
        self.assertEqual(op, OP_MOVE_PENDING)
        self.assertEqual(save_mode, "")
        self.assertEqual(payload["op"], 3)

    def test_op_publish_as_draft_write(self):
        fake = FakeClient()
        res = self._run(fake, op=OP_PUBLISH, save_mode=SAVE_MODE_AS_DRAFT)
        self.assertTrue(res.ok, res.error)
        payload, op, save_mode = fake.saved[0]
        self.assertEqual(op, OP_PUBLISH)
        self.assertEqual(save_mode, SAVE_MODE_AS_DRAFT)
        self.assertEqual(payload["op"], 2)

    def test_save_error_not_retried(self):
        fake = FakeClient(save_error=TikTokError("系统繁忙，请稍后重试"))
        res = self._run(fake)
        self.assertFalse(res.ok)
        self.assertIn("保存失败", res.error)
        self.assertEqual(fake.save_attempts, 1)  # 写接口绝不自动重试

    def test_save_result_business_code_failure(self):
        class CodeFailClient(FakeClient):
            def save_product(self, payload, op=OP_SAVE, save_mode=""):
                self.save_attempts += 1
                return {"code": -1, "msg": "产品未选择店铺或店铺无效！"}

        fake = CodeFailClient()
        res = self._run(fake)
        self.assertFalse(res.ok)
        self.assertIn("店铺无效", res.error)
        self.assertFalse(res.saved)


# ============================================================================
# G. save_to_tiktok（唯一写收口）直测
# ============================================================================
class SaveToTikTokGateTests(EnvIsolatedCase):
    def test_missing_confirm_write_raises_before_client(self):
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建客户端")):
            with self.assertRaises(RuntimeError) as ctx:
                PUB.save_to_tiktok("p1", make_cfg(), client=FakeClient())
        self.assertIn("confirm_write", str(ctx.exception))

    def test_forced_preview_raises_before_client(self):
        os.environ["AURORA_TEST"] = "1"
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建客户端")):
            with self.assertRaises(RuntimeError) as ctx:
                PUB.save_to_tiktok("p1", make_cfg(), client=FakeClient(),
                                   confirm_write=True)
        self.assertIn("拒绝真实保存", str(ctx.exception))

    def test_env_no_save_raises(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        with self.assertRaises(RuntimeError):
            PUB.save_to_tiktok("p1", make_cfg(), client=FakeClient(),
                               confirm_write=True)

    def test_unknown_op_raises(self):
        self.allow_write()
        with self.assertRaises(ValueError):
            PUB.save_to_tiktok("p1", make_cfg(), client=FakeClient(),
                               confirm_write=True, op="9")

    def test_schedule_op_raises(self):
        self.allow_write()
        with self.assertRaises(ValueError) as ctx:
            PUB.save_to_tiktok("p1", make_cfg(), client=FakeClient(),
                               confirm_write=True, op=OP_SCHEDULE)
        self.assertIn("定时发布", str(ctx.exception))

    def test_missing_product_id_raises(self):
        self.allow_write()
        with self.assertRaises(ValueError):
            PUB.save_to_tiktok("", make_cfg(), client=FakeClient(),
                               confirm_write=True)

    def test_license_failure_raises_before_client(self):
        fake = FakeClient()
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建客户端")):
            with self.assertRaises(LicenseError):
                PUB.save_to_tiktok("p1", make_cfg(), client=fake,
                                   confirm_write=True)
        self.assertEqual(fake.save_attempts, 0)


class SaveToTikTokWriteTests(EnvIsolatedCase):
    def setUp(self):
        super().setUp()
        self.allow_write()

    def test_save_returns_result_and_finalizes_payload(self):
        fake = FakeClient()
        out = PUB.save_to_tiktok("p1", make_cfg(), client=fake, confirm_write=True)
        self.assertEqual(out["op"], OP_SAVE)
        self.assertEqual(out["product_id"], "1729000000000001")
        self.assertEqual(out["issues"], [])
        self.assertNotIn("variationBos", out["payload"])
        self.assertEqual(out["payload"]["op"], 1)
        self.assertEqual(fake.save_attempts, 1)
        self.assertIn("result", out)

    def test_save_publish_as_draft(self):
        fake = FakeClient()
        out = PUB.save_to_tiktok("p1", make_cfg(), client=fake,
                                 confirm_write=True, op=OP_PUBLISH,
                                 save_mode=SAVE_MODE_AS_DRAFT)
        payload, op, save_mode = fake.saved[0]
        self.assertEqual(op, OP_PUBLISH)
        self.assertEqual(save_mode, SAVE_MODE_AS_DRAFT)
        self.assertEqual(payload["op"], 2)
        self.assertEqual(out["save_mode"], SAVE_MODE_AS_DRAFT)

    def test_invalid_payload_rejected(self):
        fake = FakeClient(detail=make_detail(mainImage="", draftImgUrl=""))
        with self.assertRaises(ValueError) as ctx:
            PUB.save_to_tiktok("p1", make_cfg(), client=fake, confirm_write=True)
        self.assertIn("保存前校验未通过", str(ctx.exception))
        self.assertEqual(fake.save_attempts, 0)

    def test_allow_invalid_writes(self):
        fake = FakeClient(detail=make_detail(mainImage="", draftImgUrl=""))
        out = PUB.save_to_tiktok("p1", make_cfg(), client=fake, confirm_write=True,
                                 allow_invalid=True)
        self.assertTrue(out["issues"])
        self.assertEqual(fake.save_attempts, 1)

    def test_injected_client_reused(self):
        fake = FakeClient()
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建真实客户端")):
            PUB.save_to_tiktok("p1", make_cfg(), client=fake, confirm_write=True)
        self.assertEqual(fake.save_attempts, 1)

    def test_write_error_not_retried(self):
        fake = FakeClient(save_error=TikTokError("系统繁忙"))
        with self.assertRaises(TikTokError):
            PUB.save_to_tiktok("p1", make_cfg(), client=fake, confirm_write=True)
        self.assertEqual(fake.save_attempts, 1)


class PublishTikTokTests(EnvIsolatedCase):
    def test_missing_confirm(self):
        with self.assertRaises(RuntimeError):
            PUB.publish_tiktok(["1"], make_cfg(), client=FakeClient())

    def test_forced_env(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        with self.assertRaises(RuntimeError):
            PUB.publish_tiktok(["1"], make_cfg(), client=FakeClient(),
                               confirm_write=True)

    def test_empty_ids(self):
        self.allow_write()
        with self.assertRaises(ValueError):
            PUB.publish_tiktok([], make_cfg(), client=FakeClient(),
                               confirm_write=True)

    def test_publish_uses_injected_client(self):
        self.allow_write()
        fake = FakeClient()
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("不应新建真实客户端")):
            out = PUB.publish_tiktok(["1", "2"], make_cfg(), shop_ids="9408005",
                                     client=fake, confirm_write=True)
        self.assertEqual(fake.published[0][0], ["1", "2"])
        self.assertEqual(fake.published[0][1], "9408005")
        self.assertEqual(out["ids"], ["1", "2"])


# ============================================================================
# H. 类目叶子校验（pipeline 负责）
# ============================================================================
class LeafCategoryTests(EnvIsolatedCase):
    def test_leaf_from_detail(self):
        fake = FakeClient()
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake.search_calls, [])
        self.assertIn("叶子类目", res.log())

    def test_non_leaf_blocks(self):
        detail = make_detail()
        detail["category"] = {"id": "601234", "isLeaf": 0}
        fake = FakeClient(detail=detail)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertTrue(any("不是叶子类目" in i for i in res.issues))
        self.assertEqual(fake.save_attempts, 0)

    def test_unknown_leaf_is_warning_only(self):
        detail = make_detail()
        detail.pop("category")
        fake = FakeClient(detail=detail)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(fake.search_calls), 1)  # best-effort 类目搜索
        self.assertTrue(any("无法确认类目" in w for w in res.warnings))

    def test_remote_leaf_state_used(self):
        detail = make_detail()
        detail.pop("category")
        fake = FakeClient(detail=detail, search_leaf=0)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertTrue(any("不是叶子类目" in i for i in res.issues))


# ============================================================================
# I. 店铺校验
# ============================================================================
class ShopCheckTests(EnvIsolatedCase):
    def test_no_tiktok_shop_is_preview_warning(self):
        fake = FakeClient(shops=[])
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(any("未检测到 TikTok 店铺" in w for w in res.warnings))

    def test_shop_mismatch_blocks(self):
        fake = FakeClient(shops=[{"id": "111", "name": "other"}])
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertTrue(any("不在已授权" in i for i in res.issues))
        self.assertEqual(fake.save_attempts, 0)

    def test_shop_list_failure_is_not_blocking(self):
        fake = FakeClient(shops=RuntimeError("接口 500"))
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(any("跳过店铺校验" in w for w in res.warnings))


# ============================================================================
# J. 校验阻断项
# ============================================================================
class ValidationBlockTests(EnvIsolatedCase):
    def _assert_blocked(self, detail, needle):
        fake = FakeClient(detail=detail)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertTrue(any(needle in i for i in res.issues),
                        f"issues={res.issues}")
        self.assertTrue(res.preview)  # 预演仍展示
        self.assertEqual(fake.save_attempts, 0)

    def test_missing_site(self):
        detail = make_detail(site="")
        fake = FakeClient(detail=detail)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertTrue(any("站点" in i for i in res.issues))

    def test_missing_category(self):
        self._assert_blocked(make_detail(categoryId="", categoryList=[]), "类目")

    def test_short_title(self):
        self._assert_blocked(make_detail(productName="Short"), "标题长度")

    def test_missing_description(self):
        self._assert_blocked(make_detail(description=""), "产品描述为空")

    def test_missing_image(self):
        self._assert_blocked(make_detail(mainImage="", draftImgUrl=""),
                             "主图数量")

    def test_missing_price(self):
        self._assert_blocked(
            make_detail(variationList=[{"sellerSku": "S1", "availableStock": 5}],
                        price=None),
            "价格必填")

    def test_missing_weight(self):
        self._assert_blocked(make_detail(packageWeight=0), "重量")

    def test_missing_sku_auto_generated(self):
        # §5.11：sellerSku 缺失时保存包会自动生成（不阻断草稿保存）
        fake = FakeClient(
            detail=make_detail(variationList=[{"originalPrice": 9.9,
                                               "availableStock": 5}]))
        _, payload, issues, _ = prepare_save("p1", make_cfg(), client=fake)
        self.assertEqual(issues, [])
        row = payload["variationList"][0]
        self.assertTrue(row.get("sellerSku"))
        self.assertTrue(str(row["sellerSku"]).startswith("AURORA-"))

    def test_us_key_features_limits(self):
        detail = make_us_detail(keyProductFeatures=["x" * 1400, "y" * 200],
                                searchTerms=["t%d" % i for i in range(20)])
        fake = FakeClient(detail=detail)
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        text = "；".join(res.issues)
        self.assertIn("五点描述总长度", text)
        self.assertIn("搜索关键词最多", text)


# ============================================================================
# K. AI 生成
# ============================================================================
class AIGenerationTests(EnvIsolatedCase):
    def test_ai_generate_field_unknown(self):
        value, notes = ai_generate_field(make_product(), make_cfg(), "unknown")
        self.assertIsNone(value)
        self.assertTrue(any("未知字段" in n for n in notes))

    def test_ai_generate_all_site_aware(self):
        fake_ai = FakeAI()
        with patch.object(PL, "_ai_client", lambda cfg: fake_ai):
            out, notes = ai_generate_all(make_product(), make_cfg(), "GB")
        self.assertEqual(len(fake_ai.prompts), 2)  # 非 US：标题 + 描述
        self.assertEqual(out["title"], AI_TITLE)
        self.assertEqual(out["description"], AI_DESC)
        self.assertEqual(out["key_features"], [])
        self.assertTrue(notes)

        fake_us_ai = FakeAI()
        with patch.object(PL, "_ai_client", lambda cfg: fake_us_ai):
            out_us, _ = ai_generate_all(make_product(site="US"), make_cfg(), "US")
        self.assertEqual(len(fake_us_ai.prompts), 4)
        self.assertEqual(len(out_us["key_features"]), 3)
        self.assertTrue(out_us["search_terms"])

    def test_ai_failure_does_not_block(self):
        # US 缺五点/关键词 → 会尝试 AI；无 API Key → 每字段失败，但预演继续
        fake = FakeClient(detail=make_us_detail())
        res = run_tiktok_product("p1", make_cfg(), client=fake, use_ai=True)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.preview)
        self.assertTrue(res.ai_used)
        self.assertTrue(any("生成失败" in w for w in res.warnings))
        ai_step = [s for s in res.steps if s.name == "AI 文案"]
        self.assertTrue(ai_step)
        self.assertFalse(ai_step[0].ok)  # 失败但只记录，不阻断
        self.assertEqual(fake.save_attempts, 0)

    def test_ai_fills_missing_title(self):
        detail = make_detail(productName="")
        fake_ai = FakeAI()
        with patch.object(PL, "_ai_client", lambda cfg: fake_ai):
            res = run_tiktok_product("p1", make_cfg(), client=FakeClient(detail=detail),
                                     use_ai=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.ai_used, True)
        self.assertEqual(res.title, AI_TITLE)
        self.assertTrue(any("请直接输出最终标题" in p for p in fake_ai.prompts))
        # 描述已有值 → 不生成、不覆盖
        self.assertFalse(any("请直接输出 HTML 片段" in p for p in fake_ai.prompts))

    def test_ai_does_not_overwrite_existing(self):
        fake_ai = FakeAI()
        with patch.object(PL, "_ai_client", lambda cfg: fake_ai):
            res = run_tiktok_product("p1", make_cfg(), client=FakeClient(),
                                     use_ai=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.title, TITLE)
        self.assertEqual(fake_ai.prompts, [])
        ai_step = [s for s in res.steps if s.name == "AI 文案"]
        self.assertIn("已有文案", ai_step[0].detail)

    def test_ai_applies_to_save_package(self):
        # prepare_save 只读且不跑 AI：标题为空时校验会阻断（AI 只在 run 里跑）
        detail = make_detail(productName="")
        _, _, issues, _ = prepare_save("p1", make_cfg(), client=FakeClient(detail=detail))
        self.assertTrue(any("标题为空" in i for i in issues))
        # run + use_ai：AI 标题写进保存包，真实写时 payload.productName 一致
        self.allow_write()
        fake_ai = FakeAI()
        fake = FakeClient(detail=detail)
        with patch.object(PL, "_ai_client", lambda cfg: fake_ai):
            res = run_tiktok_product("p1", make_cfg(), client=fake, use_ai=True,
                                     dry_run=False, confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake.saved[0][0]["productName"], AI_TITLE)


# ============================================================================
# L. 发布动作严格校验（for_publish）
# ============================================================================
class PublishStrictnessTests(EnvIsolatedCase):
    def test_non_us_features_block_publish_not_draft(self):
        detail = make_detail(keyProductFeatures=["One: a", "Two: b", "Three: c"])
        # 草稿预演：仅 warning
        res_draft = run_tiktok_product("p1", make_cfg(),
                                       client=FakeClient(detail=detail))
        self.assertTrue(res_draft.ok, res_draft.error)
        self.assertTrue(any("不支持五点" in w for w in res_draft.warnings))
        # 发布：for_publish=True → 阻断且不写
        self.allow_write()
        fake = FakeClient(detail=detail)
        res_pub = run_tiktok_product("p1", make_cfg(), client=fake,
                                     dry_run=False, confirm_write=True,
                                     op=OP_PUBLISH)
        self.assertFalse(res_pub.ok)
        self.assertTrue(any("不支持五点描述" in i for i in res_pub.issues))
        self.assertEqual(fake.save_attempts, 0)


# ============================================================================
# M. 异常路径
# ============================================================================
class ErrorPathTests(EnvIsolatedCase):
    def test_missing_product_id(self):
        res = run_tiktok_product("", make_cfg(), client=FakeClient())
        self.assertFalse(res.ok)
        self.assertIn("product_id", res.error)

    def test_client_creation_failure(self):
        with patch.object(PL, "make_client",
                          side_effect=RuntimeError("没有 Cookie")):
            res = run_tiktok_product("p1", make_cfg())
        self.assertFalse(res.ok)
        self.assertIn("创建客户端失败", res.error)

    def test_detail_read_failure(self):
        fake = FakeClient(detail=TikTokError("接口错误 code=500"))
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertIn("读取商品", res.error)

    def test_empty_detail_failure(self):
        fake = FakeClient()
        fake.detail = None
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        self.assertFalse(res.ok)
        self.assertIn("读不到商品", res.error)

    def test_site_limits_failure_falls_back(self):
        fake = FakeClient(limits=RuntimeError("limit api down"))
        res = run_tiktok_product("p1", make_cfg(), client=fake)
        # 限额失败回默认值，不阻断
        self.assertEqual(res.limits.get("nameMin"), 25)
        self.assertTrue(res.ok, res.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
