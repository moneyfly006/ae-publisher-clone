"""AI → Alibaba 刊登流水线离线测试（无网络、无付费 AI、绝不真实保存）。

跑法：``.venv/bin/python tests/test_alibaba_pipeline.py``
失败时 sys.exit(1)，便于 tools/verify_regressions.py 收录。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import AppConfig
from aeclone.services import alibaba_publish as AP
from aeclone.services.alibaba_pipeline import (
    OP_LABELS, OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, OP_SCHEDULE,
    AlibabaRunResult, clip_title, forced_preview, run_alibaba_product,
)
from aeclone.services.alibaba_publish import make_client


# ---------------- 测试桩 ----------------
class FakeTextResult:
    def __init__(self, ok=True, value="", error="", notes=None):
        self.ok = ok
        self.value = value
        self.error = error
        self.notes = list(notes or [])


class FakeTextPipeline:
    """离线替身：不联网、不花钱，返回固定标题/详情。"""

    def __init__(self, title="AI Customized Fiberglass Swimming Pool Container",
                 detail="<div><h3>Product</h3><p>AI generated English detail HTML.</p></div>",
                 title_ok=True, detail_ok=True, title_error="模型超时", detail_error="模型超时"):
        self.title = title
        self.detail = detail
        self.title_ok = title_ok
        self.detail_ok = detail_ok
        self.title_error = title_error
        self.detail_error = detail_error
        self.title_calls = []
        self.detail_calls = []

    def make_title(self, title, brands=None):
        self.title_calls.append((title, brands))
        return FakeTextResult(self.title_ok, self.title, "" if self.title_ok else self.title_error,
                              ["已按 128 字符控制"] if self.title_ok else [])

    def make_detail(self, title, attributes=None, source_text="", images=None):
        self.detail_calls.append({"title": title, "attributes": attributes,
                                  "source_text": source_text, "images": images})
        return FakeTextResult(self.detail_ok, self.detail,
                              "" if self.detail_ok else self.detail_error)


def make_product(**over):
    product = {
        "id": "220114598434958580",
        "shopId": "9466975",
        "categoryId": "202107201",
        "fullCid": "18,100005657,100005663,202107201",
        "subject": "Customized Fiberglass Swimming Pool Container",
        "keywords": "fiberglass pool, container pool, above ground pool",
        "attributes": [
            {"attribute_id": "1", "attribute_name": "Place of Origin",
             "value_id": "100000458", "value_name": "China"},
        ],
        "mainImage": "https://img.example.com/a.jpg,https://img.example.com/b.jpg",
        "draftImgUrl": "https://img.example.com/a.jpg|https://img.example.com/b.jpg",
        "detailImage": [{"displayName": "default", "value": -2,
                         "url": "https://img.example.com/d1.jpg", "text": ""}],
        "textDesc": "<p>Original detail about the swimming pool.</p>",
        "bulkDiscountPrices": [{"start_quantity": 1, "price": 4001.4}],
        "wholesaleTrade": {"min_order_quantity": 1, "package_size": "300X200X150",
                           "weight": "300", "unit_type": "Piece", "sale_type": "normal"},
        "priceType": "ladder_price",
        "productSku": {"attributes": [], "exclude_skus": [], "special_skus": []},
        "publishType": 0,
        "sourceUrl": "",
        "groupId": "",
    }
    product.update(over)
    return product


REQUIRED_ATTRS = [
    {"attrNameId": "1", "nameEn": "Place of Origin", "required": 1, "values": []},
    {"attrNameId": "2", "nameEn": "Color", "required": 1,
     "values": [{"id": "1", "name_en": "White"}, {"id": "2", "name_en": "Other"}]},
]
SCHEMA_FIELDS = [{"fieldId": "textDesc"}, {"fieldId": "detailImage"}]
SAVE_OK = {"code": 0, "data": {"msg": "您的产品编辑成功！", "productId": "220114598434958580"}}
SAVE_FAIL_MSG = {"code": 0, "data": {"msg": "操作失败，只有处在采集箱中的产品才能发布"}}


class FakeClient:
    def __init__(self, product=None, required=None, schema=None, banned=None,
                 save_resp=None, product_exc=None):
        self.product = make_product() if product is None else product
        self.required = REQUIRED_ATTRS if required is None else required
        self.schema = SCHEMA_FIELDS if schema is None else schema
        self.banned = {"code": 0, "data": []} if banned is None else banned
        self.save_resp = SAVE_OK if save_resp is None else save_resp
        self.product_exc = product_exc
        self.detail_calls = []
        self.attr_calls = []
        self.schema_calls = []
        self.banned_texts = []
        self.saved = []          # [(product, save, op)]

    def product_detail(self, product_id):
        self.detail_calls.append(product_id)
        if self.product_exc:
            raise self.product_exc
        return dict(self.product)

    def required_attributes(self, category_id):
        self.attr_calls.append(category_id)
        return list(self.required)

    def category_schema(self, category_id):
        self.schema_calls.append(category_id)
        return {"fields": list(self.schema)}

    def check_banned_words(self, text):
        self.banned_texts.append(text)
        return self.banned

    def save_product(self, product, save_dict=None, op="1"):
        self.saved.append((product, save_dict, str(op)))
        if isinstance(self.save_resp, Exception):
            raise self.save_resp
        return self.save_resp


def make_cfg(**over):
    data = {
        "alibaba_cookies": "",
        "alibaba_category_id": "",
        "alibaba_full_cid": "",
        "alibaba_keywords": "",
        "alibaba_attr_presets": "",
        "alibaba_price_type": "ladder_price",
        "alibaba_min_order_quantity": 1,
        "alibaba_unit_type": "Piece",
        "alibaba_package_size": "",
        "alibaba_weight": "",
        "alibaba_stock": 0,
        "api_key": "",
    }
    data.update(over)
    return AppConfig(data)


def step_names(result):
    return [s.name for s in result.steps]


def step(result, name):
    for s in result.steps:
        if s.name == name:
            return s
    return None


def clean_env(**extra):
    """清掉会改变保存行为的验收环境变量，避免开发机环境污染测试。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


# ---------------- 用例 ----------------
class DryRunTests(unittest.TestCase):
    def test_dry_run_never_saves_and_returns_preview(self):
        client = FakeClient()
        tp = FakeTextPipeline()
        with clean_env():
            r = run_alibaba_product("220114598434958580", make_cfg(),
                                    client=client, text_pipeline=tp)
        self.assertIsInstance(r, AlibabaRunResult)
        self.assertTrue(r.ok, r.error)
        self.assertTrue(r.preview)
        self.assertFalse(r.saved)
        self.assertEqual(client.saved, [], "dry_run 绝不允许调用 save_product")
        # 步骤覆盖整条链路
        names = step_names(r)
        for expected in ("连接 Alibaba", "读取商品详情", "读取类目规则", "AI 标题",
                         "AI 详情", "关键词", "保存前校验", "违禁词检查", "预演", "完成"):
            self.assertIn(expected, names)
        # 结果字段来自真实构建后的载荷
        self.assertEqual(r.product_id, "220114598434958580")
        self.assertEqual(r.category_id, "202107201")
        self.assertTrue(r.subject and len(r.subject) <= 128)
        self.assertGreaterEqual(len(r.keywords), 3)
        self.assertLessEqual(len(r.keywords), 5)
        self.assertGreater(r.text_chars, 0)
        self.assertTrue(r.ai_used)
        self.assertEqual(tp.title_calls[0][0], "Customized Fiberglass Swimming Pool Container")
        self.assertTrue(tp.detail_calls[0]["images"], "详情图应传给 AI 排版")
        self.assertIn("纯文本", step(r, "AI 详情").detail)

    def test_use_ai_false_keeps_product_text_and_skips_ai(self):
        client = FakeClient()
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), use_ai=False, client=client)
        self.assertTrue(r.ok, r.error)
        self.assertTrue(r.preview)
        self.assertFalse(r.ai_used)
        self.assertEqual(r.subject, "Customized Fiberglass Swimming Pool Container")
        self.assertEqual(client.saved, [])

    def test_product_keywords_preserved_when_not_configured(self):
        """无 cfg.alibaba_keywords 时，AI 流水线不得用标题词覆盖商品原短语关键词。"""
        client = FakeClient()
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.keywords, ["fiberglass pool", "container pool",
                                      "above ground pool"])
        self.assertEqual(client.saved, [], "dry-run 不保存")

    def test_short_product_keywords_not_expanded_from_title(self):
        """商品只有 1 个关键词时也不能用标题词扩写（保留原语义）。"""
        client = FakeClient(product=make_product(keywords="fiberglass pool"))
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.keywords, ["fiberglass pool"])
        self.assertTrue(any("关键词仅 1 个" in w for w in r.warnings))

    def test_cfg_keywords_override_product_keywords(self):
        client = FakeClient()
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(
                alibaba_keywords="custom pool, fiberglass pool, spa pool"),
                client=client, text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.keywords[:3], ["custom pool", "fiberglass pool", "spa pool"])

    def test_clip_title_limits_128_without_ai_call(self):
        long_title = " ".join(["word"] * 60)
        clipped = clip_title(long_title)
        self.assertLessEqual(len(clipped), 128)
        self.assertFalse(clipped.endswith(" "))
        self.assertEqual(clip_title("Short Pool"), "Short Pool")


class ValidationTests(unittest.TestCase):
    def test_validation_failure_blocks_save(self):
        # 脏草稿：无类目、无主图（真实店铺 27/28 个草稿就是这种形状）
        product = make_product(categoryId=None, fullCid="", mainImage="", draftImgUrl="")
        client = FakeClient(product=product)
        with clean_env():
            r = run_alibaba_product("dirty-1", make_cfg(), client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertFalse(r.ok)
        self.assertIn("保存前校验未通过", r.error)
        self.assertTrue(r.issues)
        text = "；".join(r.issues)
        self.assertIn("类目", text)
        self.assertIn("主图为空", text)
        self.assertEqual(client.saved, [], "校验失败时不允许调用保存接口")
        self.assertFalse(step(r, "保存前校验").ok)

    def test_ai_failure_returns_failed_preview_without_save(self):
        client = FakeClient()
        with clean_env(AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline(title_ok=False))
        self.assertFalse(r.ok)
        self.assertIn("AI 标题", r.error)
        self.assertTrue(r.preview, "AI 失败只能给预演结果")
        self.assertFalse(r.saved)
        self.assertEqual(client.saved, [], "AI 失败时不允许真实保存")
        self.assertFalse(step(r, "AI 标题").ok)

    def test_read_failure_is_reported_not_swallowed(self):
        client = FakeClient(product_exc=RuntimeError("接口 502"))
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), client=client)
        self.assertFalse(r.ok)
        self.assertIn("502", r.error)
        self.assertEqual(client.saved, [])

    def test_unknown_op_is_rejected_before_network(self):
        client = FakeClient()
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), op="9", client=client)
        self.assertFalse(r.ok)
        self.assertIn("op", r.error)
        self.assertEqual(client.detail_calls, [])


class SafetyGuardTests(unittest.TestCase):
    def test_aurora_no_save_forces_preview_even_when_dry_run_false(self):
        client = FakeClient()
        with clean_env(AURORA_NO_SAVE="1"):
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertTrue(r.preview)
        self.assertFalse(r.saved)
        self.assertEqual(client.saved, [], "AURORA_NO_SAVE 下必须强制预演")
        self.assertIsNotNone(step(r, "安全护栏"))
        self.assertTrue(any("AURORA_NO_SAVE" in w for w in r.warnings))

    def test_aurora_test_forces_preview(self):
        client = FakeClient()
        with clean_env(AURORA_TEST="1"):
            self.assertTrue(forced_preview(), "测试环境内应处于强制预演模式")
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.preview)
        self.assertFalse(r.saved)
        self.assertEqual(client.saved, [])

    def test_license_required_for_real_save(self):
        client = FakeClient()
        with clean_env():  # 无 AURORA_SKIP_LICENSE，cfg 也无授权凭证
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertFalse(r.ok)
        self.assertIn("授权", r.error)
        self.assertEqual(client.saved, [], "授权未通过不允许保存")

    def test_real_save_op_passthrough(self):
        for op in (OP_SAVE, OP_PUBLISH, OP_MOVE_PENDING, OP_SCHEDULE):
            with self.subTest(op=op):
                client = FakeClient()
                with clean_env(AURORA_SKIP_LICENSE="1"):
                    r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, op=op,
                                            client=client, text_pipeline=FakeTextPipeline())
                self.assertTrue(r.ok, r.error)
                self.assertFalse(r.preview)
                self.assertTrue(r.saved)
                self.assertEqual(len(client.saved), 1)
                self.assertEqual(client.saved[0][2], op)
                self.assertIn(OP_LABELS[op], step(r, "保存到 Alibaba").detail)

    def test_code_zero_but_failure_message_is_failed(self):
        client = FakeClient(save_resp=SAVE_FAIL_MSG)
        with clean_env(AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertFalse(r.ok, "code=0 但 msg 是失败语义时必须判失败")
        self.assertFalse(r.saved)
        self.assertIn("失败", r.error)
        self.assertIn("采集箱", r.error)
        self.assertEqual(len(client.saved), 1, "应已尝试一次保存，但结果判失败")

    def test_save_exception_is_failed(self):
        client = FakeClient(save_resp=RuntimeError("连接被重置"))
        with clean_env(AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product("pid-1", make_cfg(), dry_run=False, client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertFalse(r.ok)
        self.assertIn("连接被重置", r.error)
        self.assertFalse(r.saved)


class BannedWordTests(unittest.TestCase):
    def test_banned_words_are_warning_not_blocker(self):
        client = FakeClient(banned={"code": 0, "data": {"bannedWords": ["cheap", "free shipping"]}})
        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), client=client,
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertTrue(r.preview)
        self.assertEqual(r.banned_words, ["cheap", "free shipping"])
        self.assertTrue(any("违禁词" in w for w in r.warnings))
        # 违禁词只提示、不阻断：步骤记成功，避免完成标签误报「N 项未完成」
        self.assertTrue(step(r, "违禁词检查").ok)
        self.assertIn("命中", step(r, "违禁词检查").detail)

    def test_banned_word_api_error_does_not_crash(self):
        class BoomClient(FakeClient):
            def check_banned_words(self, text):
                raise RuntimeError("接口超时")

        with clean_env():
            r = run_alibaba_product("pid-1", make_cfg(), client=BoomClient(),
                                    text_pipeline=FakeTextPipeline())
        self.assertTrue(r.ok, r.error)
        self.assertTrue(any("违禁词检查失败" in w for w in r.warnings))
        self.assertTrue(step(r, "违禁词检查").ok, "接口失败也只是提示，不应记失败步骤")
        self.assertIn("跳过", step(r, "违禁词检查").detail)


class CookieOrderTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(AP, "_browser_cookies", return_value={})
        self.browser = patcher.start()
        self.addCleanup(patcher.stop)

    def test_alibaba_cookies_wins(self):
        cfg = make_cfg(alibaba_cookies="dxm_i=from_alibaba; dxm_t=token_a",
                       manual_cookies={"dxm_i": "from_manual"})
        client = make_client(cfg)
        self.assertEqual(client.s.cookies.get("dxm_i"), "from_alibaba")
        self.assertEqual(client.s.cookies.get("dxm_t"), "token_a")
        self.browser.assert_not_called()

    def test_manual_cookies_fallback(self):
        cfg = make_cfg(alibaba_cookies="", manual_cookies={"dxm_i": "from_manual",
                                                          "JSESSIONID": "js"})
        client = make_client(cfg)
        self.assertEqual(client.s.cookies.get("dxm_i"), "from_manual")
        self.assertEqual(client.s.cookies.get("JSESSIONID"), "js")
        self.browser.assert_not_called()

    def test_no_cookie_anywhere_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            make_client(make_cfg(alibaba_cookies="", manual_cookies={}))
        self.assertIn("Cookie", str(ctx.exception))
        self.browser.assert_called_once()

    def test_browser_fallback_still_works(self):
        self.browser.return_value = {"dxm_i": "from_chrome"}
        client = make_client(make_cfg(alibaba_cookies="", manual_cookies={}))
        self.assertEqual(client.s.cookies.get("dxm_i"), "from_chrome")

    def test_dict_config_supported(self):
        client = make_client({"alibaba_cookies": "", "manual_cookies": {"dxm_i": "dict_cfg"}})
        self.assertEqual(client.s.cookies.get("dxm_i"), "dict_cfg")


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
