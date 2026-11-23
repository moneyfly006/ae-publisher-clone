"""Alibaba AI 提示词预设离线单测（无网络、无付费调用、不读写真实配置）。

跑法：``.venv/bin/python tests/test_alibaba_prompts.py``
失败时 sys.exit(1)，便于 tools/verify_regressions.py 收录。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import AppConfig
from aeclone.services import alibaba_prompts as AP
from aeclone.services.alibaba_payload import (
    MAX_KEYWORDS, MAX_SUBJECT, MIN_KEYWORDS,
)

PROMPT_FIELDS = (
    "title_prompt", "detail_prompt", "sku_prompt", "keyword_prompt", "review_prompt",
)
META_FIELDS = (
    "id", "name", "kind", "industry", "applicable_categories", "aliases",
    "keyword_strategy", "compliance_notes",
)
KINDS = {"industry", "function"}


class PresetShapeTests(unittest.TestCase):
    def test_presets_present_and_complete(self):
        ids = AP.PRESET_IDS
        self.assertGreaterEqual(len(ids), 7, f"预设数量不足：{ids}")
        for pid in ids:
            p = AP.PRESETS[pid]
            self.assertEqual(p["id"], pid)
            self.assertIn(p["kind"], KINDS, pid)
            for f in META_FIELDS + PROMPT_FIELDS:
                self.assertIn(f, p, f"{pid} 缺字段 {f}")
            self.assertIsInstance(p["applicable_categories"], list, pid)
            self.assertTrue(p["applicable_categories"], pid)
            self.assertIsInstance(p["aliases"], list, pid)
            self.assertTrue(p["aliases"], pid)
            for f in PROMPT_FIELDS:
                self.assertIsInstance(p[f], str, f"{pid}.{f}")
                self.assertGreater(len(p[f].strip()), 30, f"{pid}.{f} 太短")

    def test_industry_and_function_presets_both_exist(self):
        self.assertGreaterEqual(len(AP.INDUSTRY_PRESET_IDS), 4)
        self.assertGreaterEqual(len(AP.FUNCTION_PRESET_IDS), 3)
        # 任务点名的 7 类预设必须覆盖
        for pid in ("container_pool", "machinery_industrial", "electric_atv",
                    "general_industrial", "detail_html", "sku_naming", "content_review"):
            self.assertIn(pid, AP.PRESETS)

    def test_title_prompt_constraints(self):
        """Alibaba subject ≤128 字符、英文、只输出一行。"""
        self.assertEqual(MAX_SUBJECT, 128, "alibaba_payload.MAX_SUBJECT 变了，请同步预设")
        for pid, p in AP.PRESETS.items():
            t = p["title_prompt"]
            self.assertIn("128", t, f"{pid} 标题提示词未约束 128 字符")
            self.assertTrue(("英文" in t) or ("English" in t), f"{pid} 标题提示词未要求英文")
            self.assertIn("只输出", t, f"{pid} 标题提示词未约束“只输出”")
            self.assertNotIn("125", t, f"{pid} 标题提示词仍在用旧的 125 上限")

    def test_detail_prompt_constraints(self):
        """平台 textDesc 只收纯文本：提示词必须要求纯文本、≤2000 字符、禁联系方式。

        2026-11-22 实测：Alibaba add.json 对 HTML 详情报
        textDesc:不能输入HTML脚本 / 不能超过1000个汉字或2000个字符。
        """
        for pid, p in AP.PRESETS.items():
            d = p["detail_prompt"]
            self.assertIn("纯文本", d, f"{pid} 详情提示词未要求纯文本")
            self.assertIn("2000", d, f"{pid} 详情提示词未约束 2000 字符上限")
            self.assertIn("800~1500", d, f"{pid} 详情提示词未给安全长度区间")
            self.assertNotIn("data-detail-img", d, f"{pid} 详情提示词不应再要求图片占位符")
            self.assertIn("HTML", d, f"{pid} 详情提示词未说明不要 HTML")

    def test_sku_prompt_constraints(self):
        for pid, p in AP.PRESETS.items():
            s = p["sku_prompt"]
            self.assertIn("20", s, f"{pid} SKU 提示词未约束 20 字符")
            self.assertTrue(("英文" in s) or ("English" in s), f"{pid} SKU 提示词未要求英文")
            self.assertIn("每行一个", s, f"{pid} SKU 提示词缺少逐行输出约束")

    def test_keyword_strategy(self):
        self.assertEqual(MIN_KEYWORDS, 3)
        self.assertEqual(MAX_KEYWORDS, 5)
        for pid, p in AP.PRESETS.items():
            kw = p["keyword_strategy"]
            self.assertIn("count", kw, pid)
            self.assertIn("3", str(kw["count"]), pid)
            self.assertIn("5", str(kw["count"]), pid)
            self.assertTrue(kw.get("examples"), f"{pid} 关键词策略缺 examples")
            self.assertTrue(kw.get("rules"), f"{pid} 关键词策略缺 rules")
            self.assertTrue(kw.get("composition"), f"{pid} 关键词策略缺 composition")
            self.assertIn("3~5", p["keyword_prompt"], f"{pid} keyword_prompt 未写 3~5")

    def test_review_prompt(self):
        for pid, p in AP.PRESETS.items():
            r = p["review_prompt"]
            self.assertIn("PASS", r, pid)
            self.assertIn("FAIL", r, pid)
            self.assertIn("只输出一行", r, pid)
            self.assertTrue(("联系方式" in r) or ("邮箱" in r), pid)

    def test_compliance_notes(self):
        for pid, p in AP.PRESETS.items():
            c = p["compliance_notes"]
            for f in ("banned_words", "notes", "images"):
                self.assertIn(f, c, f"{pid} compliance_notes 缺 {f}")
            self.assertTrue(c["banned_words"], pid)
            self.assertTrue(c["notes"], pid)
            self.assertIsInstance(c["images"], str)
            self.assertTrue(c["images"].strip(), pid)
            for w in c["notes"]:
                self.assertIsInstance(w, str)
                self.assertTrue(w.strip(), pid)

    def test_common_banned_words_kept(self):
        for w in ("cheap", "replica", "fake", "copy", "knockoff",
                  "best price", "free shipping"):
            self.assertIn(w, AP.BANNED_WORDS)
        # 联系方式类词也应在违规词表里（本地体检用）
        for w in ("whatsapp", "wechat"):
            self.assertIn(w, AP.BANNED_WORDS)


class ResolveTests(unittest.TestCase):
    def test_resolve_by_id(self):
        for pid in AP.PRESET_IDS:
            self.assertIs(AP.resolve_preset(pid), AP.PRESETS[pid])

    def test_resolve_by_name_and_alias(self):
        cases = {
            "集装箱泳池 / 泳池设备（主推）": "container_pool",
            "泳池": "container_pool",
            "集装箱泳池": "container_pool",
            "POOL": "container_pool",
            "container pool": "container_pool",
            "swimming pool": "container_pool",
            "机械": "machinery_industrial",
            "工业设备": "machinery_industrial",
            "atv": "electric_atv",
            "电动atv": "electric_atv",
            "electric atv": "electric_atv",
            "通用工业品": "general_industrial",
            "通用": "general_industrial",
            "详情": "detail_html",
            "详情html": "detail_html",
            "sku": "sku_naming",
            "审查": "content_review",
            "合规": "content_review",
        }
        for key, want in cases.items():
            got = AP.resolve_preset(key)["id"]
            self.assertEqual(got, want, f"resolve_preset({key!r}) → {got}，期望 {want}")

    def test_resolve_fragment_tolerant(self):
        self.assertEqual(AP.resolve_preset("集装箱泳池定制款")["id"], "container_pool")
        self.assertEqual(AP.resolve_preset("electric atv for kids")["id"], "electric_atv")
        self.assertEqual(AP.resolve_preset("  泳池  ")["id"], "container_pool")
        self.assertEqual(AP.resolve_preset("POOL LADDER")["id"], "container_pool")

    def test_resolve_fallback(self):
        self.assertEqual(AP.resolve_preset(None)["id"], AP.DEFAULT_PRESET_ID)
        self.assertEqual(AP.resolve_preset("")["id"], AP.DEFAULT_PRESET_ID)
        self.assertEqual(AP.resolve_preset("   ")["id"], AP.DEFAULT_PRESET_ID)
        self.assertEqual(AP.resolve_preset("不存在的行业xyz")["id"], AP.DEFAULT_PRESET_ID)
        self.assertEqual(AP.resolve_preset(123)["id"], AP.DEFAULT_PRESET_ID)

    def test_resolve_dict_passthrough(self):
        d = {"id": "custom", "title_prompt": "x"}
        self.assertIs(AP.resolve_preset(d), d)

    def test_list_and_options(self):
        rows = AP.list_presets()
        self.assertEqual(len(rows), len(AP.PRESET_IDS))
        # 返回浅拷贝：改副本不能污染 PRESETS
        rows[0]["name"] = "MUTATED"
        self.assertNotEqual(AP.PRESETS[rows[0]["id"]]["name"], "MUTATED")
        opts = AP.preset_options()
        self.assertEqual([o[0] for o in opts], AP.PRESET_IDS)
        self.assertTrue(all(o[1] for o in opts))
        self.assertEqual(len(AP.list_presets("industry")), len(AP.INDUSTRY_PRESET_IDS))
        self.assertEqual(len(AP.list_presets("function")), len(AP.FUNCTION_PRESET_IDS))

    def test_get_prompt_and_describe(self):
        self.assertIn("128", AP.get_prompt("title_prompt", "泳池"))
        self.assertEqual(AP.get_prompt("nope", "泳池"), "")
        desc = AP.describe_preset("pool")
        self.assertIn("container_pool", desc)
        self.assertIn("集装箱泳池", desc)


class ApplyToCfgTests(unittest.TestCase):
    def test_apply_writes_only_prompt_keys(self):
        cfg = AppConfig()
        before = cfg.as_dict()
        with patch.object(AppConfig, "save", side_effect=AssertionError("apply_to_cfg 不允许保存配置")):
            written = AP.apply_to_cfg(cfg, "container_pool")
        self.assertEqual(written, list(AP.PROMPT_KEYS))
        p = AP.PRESETS["container_pool"]
        self.assertEqual(cfg.title_prompt, p["title_prompt"])
        self.assertEqual(cfg.detail_prompt, p["detail_prompt"])
        self.assertEqual(cfg.sku_prompt, p["sku_prompt"])
        self.assertEqual(cfg.review_text_prompt, p["review_prompt"])
        after = cfg.as_dict()
        changed = {k for k in after if after.get(k) != before.get(k)}
        self.assertEqual(changed, set(AP.PROMPT_KEYS))
        # 其它关键值确实没被改
        for k in ("api_key", "api_base", "title_prefix", "weight_prompt",
                  "review_main_img_prompt", "alibaba_keywords"):
            self.assertEqual(after.get(k), before.get(k), k)

    def test_apply_dict_cfg(self):
        d = {"title_prompt": "old", "detail_prompt": "old", "other": 1}
        written = AP.apply_to_cfg(d, AP.PRESETS["electric_atv"])
        self.assertEqual(written, list(AP.PROMPT_KEYS))
        self.assertEqual(d["title_prompt"], AP.PRESETS["electric_atv"]["title_prompt"])
        self.assertIn("review_text_prompt", d)
        self.assertEqual(d["other"], 1)

    def test_apply_all_presets_no_leftover(self):
        cfg = AppConfig()
        for pid in AP.PRESET_IDS:
            written = AP.apply_to_cfg(cfg, pid)
            self.assertEqual(written, list(AP.PROMPT_KEYS), pid)
            self.assertEqual(cfg.title_prompt, AP.PRESETS[pid]["title_prompt"], pid)
            self.assertEqual(cfg.review_text_prompt, AP.PRESETS[pid]["review_prompt"], pid)

    def test_apply_unknown_uses_default(self):
        cfg = AppConfig()
        AP.apply_to_cfg(cfg, "不存在的行业xyz")
        self.assertEqual(cfg.title_prompt, AP.PRESETS[AP.DEFAULT_PRESET_ID]["title_prompt"])

    def test_apply_empty_preset_writes_nothing(self):
        cfg = AppConfig()
        before = cfg.as_dict()
        written = AP.apply_to_cfg(cfg, {})
        self.assertEqual(written, [])
        self.assertEqual(cfg.as_dict(), before)


class FabricationWarningTests(unittest.TestCase):
    """真实实测发现「示例数字会被模型抄进标题」，这里离线锁住后置体检能力。"""

    def test_flags_fabricated_spec_material_absolute(self):
        source = "Customized Container Swimming Pool Acrylic Glass Window Outdoor Above Ground Pool"
        text = "Customized 40ft Fiberglass Container Pool 100% Waterproof"
        w = AP.fabrication_warnings(text, source)
        joined = " | ".join(w)
        self.assertIn("40ft", joined)
        self.assertIn("fiberglass", joined.lower())
        self.assertIn("100%", joined)

    def test_no_warning_when_grounded(self):
        source = "Fiberglass Container Pool 40ft Customized outdoor pool"
        text = "Customized 40ft Fiberglass Container Pool"
        self.assertEqual(AP.fabrication_warnings(text, source), [])

    def test_hyphen_unit_flagged(self):
        w = AP.fabrication_warnings("Prefabricated 40-foot container pool", "container pool")
        self.assertTrue(any("40-foot" in x for x in w), w)

    def test_source_with_contact_only_gets_spec_warnings(self):
        # 真实环境里 220114598434958576 的原详情只有联系方式、没有任何规格
        source = "Contact: Whatsapp +8615890156038 Email: a@b.com Please leave message for quote"
        w = AP.fabrication_warnings("40ft Fiberglass Pool with 30 years warranty", source)
        joined = " | ".join(w)
        self.assertIn("40ft", joined)
        self.assertIn("fiberglass", joined.lower())
        self.assertIn("30", joined)

    def test_empty_and_non_ascii_inputs(self):
        self.assertEqual(AP.fabrication_warnings("", ""), [])
        self.assertEqual(AP.fabrication_warnings(None, None), [])
        self.assertEqual(AP.fabrication_warnings("纯中文没有规格", "原文"), [])

    def test_measured_real_regression_case(self):
        """v1 预设实测回归：576 原文只有标题、无 fiberglass/尺寸，新标题却写了。"""
        source_576 = ("Customized Large Size Prefabricated Container Swimming Pool "
                      "Acrylic Glass Window Outdoor Above Ground Pool for Sale")
        old_title = "Large Prefab Container Swimming Pool with Acrylic Glass Window – Outdoor Above-Ground Pool"
        new_title_v1 = ("Customized 40ft Prefabricated Container Pool with Acrylic Window – "
                        "Outdoor Above Ground Fiberglass Pool")
        self.assertEqual(AP.fabrication_warnings(old_title, source_576), [])
        joined = " | ".join(AP.fabrication_warnings(new_title_v1, source_576))
        self.assertIn("40ft", joined)
        self.assertIn("fiberglass", joined.lower())

    def test_pool_prompt_hardened_against_examples(self):
        """v2 加固：泳池标题/详情提示词明确禁止搬用示例尺寸与材质。"""
        p = AP.PRESETS["container_pool"]
        self.assertIn("禁止出现任何尺寸数字", p["title_prompt"])
        self.assertIn("原文没有具体尺寸时禁止写任何 ft/m 数字", p["detail_prompt"])
        self.assertEqual(AP.PRESET_VERSION, "2026-11-22.2")


class TextPipelineCompatibilityTests(unittest.TestCase):
    """预设要能直接作为 TextPipeline 的 system prompt 使用（离线，不联网）。"""

    def test_prompt_usable_as_system_prompt(self):
        from aeclone.services.text_pipeline import TextPipeline, enforce_title_length  # noqa: F401
        cfg = AppConfig()
        AP.apply_to_cfg(cfg, "container_pool")
        tp = TextPipeline(cfg, client=object())  # 只验证构造，不调用模型
        self.assertEqual(tp.cfg.title_prompt, cfg.title_prompt)
        self.assertTrue(cfg.detail_prompt.startswith("你是 Alibaba.com"))
        # 旧默认与新手预设必须不同（否则接线后看不出效果）
        self.assertNotEqual(cfg.title_prompt, AppConfig.DEFAULTS["title_prompt"])
        self.assertNotEqual(cfg.detail_prompt, AppConfig.DEFAULTS["detail_prompt"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n{'PASS' if result.wasSuccessful() else 'FAIL'}: "
          f"{result.testsRun} tests, {len(result.failures)} failures, "
          f"{len(result.errors)} errors")
    sys.exit(0 if result.wasSuccessful() else 1)
