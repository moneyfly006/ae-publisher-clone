"""Alibaba 类目属性 value_id 解析 / 必填补齐 / AI 兜底 / 保存校验（纯离线，不联网）。

覆盖真实采集箱回归暴露的问题（2026-11-22，shop 9466975）：
- 商品属性有 value_name 但 value_id 全是 -1（有值名无 id）；
- 属性名可能是中文/英文别名（「重量（千克）」/「Origin」），旧逻辑只比英文名 → 误判缺失；
- 平台 values 是 JSON 字符串，中文名在 transValues[].trans_value；
- 多选属性把多个值塞在一个字段（"Children, Adult, All"）；
- 匹配不到时不能写空/负数 value_id，要给出可读中文 unresolved 原因。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import alibaba_ai as AI
from aeclone.services import alibaba_payload as P


def _vals(*rows):
    """构造店小秘 attributeList 的 values JSON 字符串。"""
    return json.dumps([{"id": str(i), "name_en": n} for i, n in rows], ensure_ascii=False)


def _def(name_en, name_zh="", required=1, values="", input_type="single_select",
         attr_name_id="1"):
    return {"attrNameId": attr_name_id, "nameEn": name_en, "nameZh": name_zh,
            "required": required, "values": values, "inputType": input_type}


def _cfg(**over):
    from aeclone.config import AppConfig
    data = {"alibaba_attr_presets": "", "api_base": "https://api.example.invalid",
            "api_key": "test-key", "timeout": 5}
    data.update(over)
    return AppConfig(data)


COLOR_DEF = _def(
    "Color", "颜色", values=_vals(("3331185", "White"), ("5412614", "Gray"),
                                  ("4", "Other"), ("123085", "Blue")),
    input_type="multi_select", attr_name_id="210190015")
ORIGIN_DEF = _def(
    "Place of Origin", "原产地",
    values=_vals(("100000458", "China"), ("100000639", "United States"),
                 ("-1", "other")),
    attr_name_id="1")
MATERIAL_DEF = _def(
    "Material", "材料",
    values=_vals(("3454400", "Steel"), ("383308193", "Aluminum Alloy"),
                 ("326756224", "Glass Fiber"), ("4", "Other")),
    input_type="multi_select", attr_name_id="238736366")
PEOPLE_DEF = _def(
    "Applicable People", "适用人群",
    values=_vals(("3561415", "Newborn"), ("4", "Other"), ("3485683", "Children"),
                 ("3972330", "Adult"), ("3254703", "All")),
    input_type="multi_select", attr_name_id="270605350")


class ResolveAttrValueTests(unittest.TestCase):
    def test_json_string_values_exact_and_case_insensitive(self):
        res = P.resolve_attr_value(COLOR_DEF, "white")
        self.assertTrue(res["matched"])
        self.assertEqual(res["value_id"], "3331185")
        self.assertEqual(res["value_name"], "White")

    def test_chinese_trans_value_matches(self):
        """平台中文名在 transValues[].trans_value（真实类目 202107201 结构）。"""
        d = dict(COLOR_DEF)
        d["transValues"] = json.dumps(
            [{"id": "3331185", "name_en": "White", "trans_value": "白色"},
             {"id": "4", "name_en": "Other", "trans_value": "其他"}], ensure_ascii=False)
        res = P.resolve_attr_value(d, "其他")
        self.assertTrue(res["matched"], res)
        self.assertEqual(res["value_id"], "4")

    def test_value_aliases(self):
        res = P.resolve_attr_value(ORIGIN_DEF, "Mainland China")
        self.assertTrue(res["matched"], res)
        self.assertEqual(res["value_id"], "100000458")
        mat = _def("Material", "材料", values=_vals(("326756224", "Glass Fiber"),
                                                    ("4", "Other")), input_type="multi_select")
        self.assertEqual(P.resolve_attr_value(mat, "Fiberglass")["value_id"], "326756224")
        self.assertEqual(P.resolve_attr_value(COLOR_DEF, "Customized Color")["value_id"], "4")

    def test_ambiguous_value_not_guessed(self):
        res = P.resolve_attr_value(
            _def("Type", "类型", values=_vals(("1", "Heated Pools"), ("2", "Residential Pools"),
                                              ("3", "Above Ground Pools"), ("4", "Lap Pools"))),
            "Pool")
        self.assertFalse(res["matched"])
        self.assertIn("歧义", res["reason"])

    def test_value_outside_enum_reports_reason(self):
        res = P.resolve_attr_value(COLOR_DEF, "Neon Pink")
        self.assertFalse(res["matched"])
        self.assertIn("不在平台候选值中", res["reason"])

    def test_candidate_without_id_never_returns_empty_id(self):
        d = _def("Material", "材料", values=json.dumps([{"name_en": "Steel"}]))
        res = P.resolve_attr_value(d, "Steel")
        self.assertTrue(res["matched"])
        self.assertIsNone(res["value_id"])

    def test_free_text_attr_has_no_enum(self):
        d = _def("Weight (kg)", "重量（千克）", values="", input_type="input")
        self.assertEqual(P._value_entries(d), [])
        self.assertTrue(P._is_free_text(d))

    def test_legacy_match_attribute_value(self):
        self.assertEqual(P.match_attribute_value(COLOR_DEF, "blue"), "Blue")
        self.assertIsNone(P.match_attribute_value(COLOR_DEF, "Neon Pink"))
        # 无候选值（自由填写）返回 None（旧行为保持）
        self.assertIsNone(P.match_attribute_value(
            _def("Model Number", "型号", values="", input_type="input"), "LS-600"))


class AutofillValueIdTests(unittest.TestCase):
    def test_existing_attr_value_name_gets_platform_value_id(self):
        """核心修复：有 value_name 但 value_id=-1 → 按平台 values 反查 id。"""
        product = {"attributes": [
            {"attribute_id": "-1", "attribute_name": "Color",
             "value_id": "-1", "value_name": "Other"},
            {"attribute_id": "-1", "attribute_name": "Place of Origin",
             "value_id": "-1", "value_name": "China"},
            {"attribute_id": "-1", "attribute_name": "Material",
             "value_id": "-1", "value_name": "Glass Fiber"},
            {"attribute_id": "-1", "attribute_name": "Applicable People",
             "value_id": "-1", "value_name": "All"},
        ]}
        attrs, filled, unresolved = P.autofill_required_attributes(
            product, [ORIGIN_DEF, COLOR_DEF, MATERIAL_DEF, PEOPLE_DEF], _cfg())
        got = {(a["attribute_name"], a["value_name"]): a["value_id"] for a in attrs}
        self.assertEqual(got[("Color", "Other")], "4")
        self.assertEqual(got[("Place of Origin", "China")], "100000458")
        self.assertEqual(got[("Material", "Glass Fiber")], "326756224")
        self.assertEqual(got[("Applicable People", "All")], "3254703")
        # 类目属性还要补 attribute_id=attrNameId（平台按 id 识别属性）
        by_name = {a["attribute_name"]: a for a in attrs}
        self.assertEqual(by_name["Color"]["attribute_id"], "210190015")
        self.assertEqual(unresolved, [])
        self.assertTrue(any("value_id 补全" in x for x in filled), filled)

    def test_chinese_attr_name_and_attr_id_match_def(self):
        """采集草稿属性名是中文/attribute_id 已存在时，不能误判必填缺失。"""
        product = {"attributes": [
            {"attribute_id": "1", "attribute_name": "原产地",
             "value_id": "100000458", "value_name": "China"},
            {"attribute_id": "225593355", "attribute_name": "重量（千克）",
             "value_id": "-2", "value_name": "3000"},
        ]}
        defs = [ORIGIN_DEF,
                _def("Weight (kg)", "重量（千克）", values="", input_type="input",
                     attr_name_id="225593355")]
        attrs, _filled, unresolved = P.autofill_required_attributes(product, defs, _cfg())
        self.assertEqual(unresolved, [])
        self.assertEqual(len(attrs), 2)

    def test_value_not_in_enum_is_unresolved_with_readable_reason(self):
        product = {"attributes": [
            {"attribute_id": "-1", "attribute_name": "Place of Origin",
             "value_id": "100000458", "value_name": "China"},
            {"attribute_id": "210190015", "attribute_name": "Color",
             "value_id": "-1", "value_name": "Neon Pink"},
        ]}
        reasons = []
        attrs, filled, unresolved = P.autofill_required_attributes(
            product, [ORIGIN_DEF, COLOR_DEF], _cfg(), reasons=reasons)
        # 枚举外的原值不编造 id，而是回退到平台 "Other" 候选并留下可复核说明。
        color = [a for a in attrs if a["attribute_name"] == "Color"][0]
        self.assertEqual(color["value_id"], "4")           # Color/Other
        self.assertEqual(color["value_name"], "Other")
        self.assertTrue(any("Color" in x and "Neon Pink" in x and "Other" in x
                            for x in filled), filled)
        self.assertEqual(unresolved, [])

    def test_missing_required_no_basis_is_unresolved_with_reason(self):
        product = {"attributes": [{"attribute_id": "1", "attribute_name": "原产地",
                                   "value_id": "100000458", "value_name": "China"}]}
        defs = [ORIGIN_DEF, _def("Model Number", "型号", values="", input_type="input",
                                 attr_name_id="2")]
        reasons = []
        _attrs, _filled, unresolved = P.autofill_required_attributes(
            product, defs, _cfg(), reasons=reasons)
        self.assertIn("Model Number", unresolved)
        self.assertTrue(any("Model Number" in r and "必填属性" in r for r in reasons), reasons)

    def test_generic_other_fallback_keeps_platform_id(self):
        product = {"attributes": [{"attribute_id": "1", "attribute_name": "原产地",
                                   "value_id": "100000458", "value_name": "China"}]}
        attrs, filled, unresolved = P.autofill_required_attributes(
            product, [ORIGIN_DEF, _def("Material", "材料",
                                       values=_vals(("326756224", "Glass Fiber"),
                                                    ("4", "Other")),
                                       input_type="multi_select")], _cfg())
        mat = [a for a in attrs if a["attribute_name"] == "Material"][0]
        self.assertEqual(mat["value_name"], "Other")
        self.assertEqual(mat["value_id"], "4")
        self.assertIn("Material=Other", filled)
        self.assertEqual(unresolved, [])

    def test_multi_select_combined_value_split_into_rows(self):
        product = {"attributes": [
            {"attribute_id": "1", "attribute_name": "原产地",
             "value_id": "100000458", "value_name": "China"},
            {"attribute_id": "270605350", "attribute_name": "适用人群",
             "value_id": "-1", "value_name": "Children, Adult, All"},
        ]}
        attrs, filled, unresolved = P.autofill_required_attributes(
            product, [ORIGIN_DEF, PEOPLE_DEF], _cfg())
        rows = [a for a in attrs if a["attribute_name"] == "适用人群"]
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual({r["value_name"] for r in rows}, {"Children", "Adult", "All"})
        self.assertEqual({r["value_id"] for r in rows}, {"3485683", "3972330", "3254703"})
        self.assertTrue(any("多选拆分" in f for f in filled), filled)
        self.assertEqual(unresolved, [])

    def test_preset_with_explicit_value_id_is_verified_against_enum(self):
        product = {"attributes": [{"attribute_id": "1", "attribute_name": "原产地",
                                   "value_id": "100000458", "value_name": "China"}]}
        cfg = _cfg(alibaba_attr_presets='{"Color": {"value_id": "123085"}}')
        attrs, filled, unresolved = P.autofill_required_attributes(
            product, [ORIGIN_DEF, COLOR_DEF], cfg)
        color = [a for a in attrs if a["attribute_name"] == "Color"]
        self.assertEqual(len(color), 1)
        self.assertEqual(color[0]["value_name"], "Blue")
        self.assertEqual(color[0]["value_id"], "123085")
        self.assertEqual(unresolved, [])

    def test_invalid_preset_does_not_fall_back_to_generic(self):
        cfg = _cfg(alibaba_attr_presets='{"Color": "Neon Pink"}')
        attrs, _filled, unresolved = P.autofill_required_attributes(
            {"attributes": []}, [COLOR_DEF], cfg)
        self.assertEqual(attrs, [])
        self.assertTrue(any("Color" in u and "预设值" in u for u in unresolved), unresolved)

    def test_preset_candidate_without_id_is_blocked(self):
        d = _def("Material", "材料", values=json.dumps([{"name_en": "Steel"}]))
        cfg = _cfg(alibaba_attr_presets='{"Material": "Steel"}')
        attrs, _filled, unresolved = P.autofill_required_attributes(
            {"attributes": []}, [d], cfg)
        self.assertEqual(attrs, [], "平台候选缺 id 时不能写空 value_id")
        self.assertTrue(any("Material" in u and "缺少 value_id" in u for u in unresolved),
                        unresolved)


class AiSuggestAttributesTests(unittest.TestCase):
    class FakeClient:
        def __init__(self, reply="{}", error=None, record=None):
            self.reply = reply
            self.error = error
            self.record = record if record is not None else {}

        def chat(self, model, messages, **kwargs):
            self.record["model"] = model
            self.record["messages"] = messages
            self.record["temperature"] = kwargs.get("temperature")
            if self.error:
                raise self.error
            return self.reply

    def _product(self, attrs=None, subject="Spider Crane with Outriggers",
                 desc="Hydraulic spider crane, max lifting load 3 ton."):
        return {"subject": subject, "keywords": "spider crane, hydraulic crane",
                "textDesc": desc, "attributes": json.dumps(attrs or [], ensure_ascii=False)}

    def test_suggestions_are_enum_mapped_and_not_written(self):
        defs = [ORIGIN_DEF, COLOR_DEF,
                _def("Max. Lifting Load", "最大起重量", values="", input_type="input",
                     attr_name_id="9")]
        reply = json.dumps({"Color": "Blue", "Max. Lifting Load": "3 ton"},
                           ensure_ascii=False)
        rec = {}
        res = AI.ai_suggest_attributes(
            self._product(), defs, _cfg(), missing=["Color", "Max. Lifting Load"],
            client=self.FakeClient(reply, record=rec))
        self.assertTrue(res["called"])
        self.assertEqual(res["model"], "qwen-plus")
        self.assertEqual(res["suggestions"]["Color"],
                         {"value_id": "123085", "value_name": "Blue",
                          "needs_confirm": False, "source": "ai"})
        self.assertEqual(res["suggestions"]["Max. Lifting Load"]["value_name"], "3 ton")
        self.assertEqual(res["suggestions"]["Max. Lifting Load"]["value_id"], "-1")
        # 只建议、不写平台：product 原属性未被修改
        self.assertEqual(json.loads(self._product()["attributes"]), [])

    def test_only_locally_unresolvable_fields_requested(self):
        defs = [ORIGIN_DEF, COLOR_DEF]
        rec = {}
        res = AI.ai_suggest_attributes(
            self._product(attrs=[{"attribute_id": "1", "attribute_name": "原产地",
                                  "value_id": "100000458", "value_name": "China"}]),
            defs, _cfg(), missing=["Place of Origin", "Color"],
            client=self.FakeClient(json.dumps({"Color": "Blue"}), record=rec))
        self.assertEqual(res["fields"], ["Color"], "本地已可解析的 Place of Origin 不应请求 AI")
        self.assertNotIn("Place of Origin", res["suggestions"])

    def test_model_number_is_flagged_needs_confirm(self):
        defs = [_def("Model Number", "型号", values="", input_type="input")]
        res = AI.ai_suggest_attributes(
            self._product(subject="LS-600 Fiber Laser Cutting Machine"),
            defs, _cfg(), missing=["Model Number"],
            client=self.FakeClient(json.dumps({"Model Number": "LS-600"})))
        sug = res["suggestions"]["Model Number"]
        self.assertTrue(sug["needs_confirm"], "禁止自动编造的属性必须人工确认")
        self.assertTrue(any("人工确认" in n for n in res["notes"]), res["notes"])

    def test_ai_value_outside_enum_is_rejected(self):
        defs = [COLOR_DEF]
        res = AI.ai_suggest_attributes(
            self._product(), defs, _cfg(), missing=["Color"],
            client=self.FakeClient(json.dumps({"Color": "Neon Pink"})))
        self.assertEqual(res["suggestions"], {})
        self.assertTrue(any("不在平台候选值中" in u for u in res["unresolved"]),
                        res["unresolved"])

    def test_ai_call_failure_does_not_raise(self):
        defs = [COLOR_DEF]
        res = AI.ai_suggest_attributes(
            self._product(), defs, _cfg(), missing=["Color"],
            client=self.FakeClient(error=RuntimeError("quota exceeded")))
        self.assertFalse(res["called"])
        self.assertIn("AI 调用失败", res["error"])
        self.assertEqual(res["suggestions"], {})

    def test_unparsable_reply_reports_error(self):
        defs = [COLOR_DEF]
        res = AI.ai_suggest_attributes(
            self._product(), defs, _cfg(), missing=["Color"],
            client=self.FakeClient("抱歉，我无法确定"))
        self.assertIn("未返回可解析的 JSON", res["error"])

    def test_no_targets_means_no_ai_call(self):
        rec = {}
        client = self.FakeClient("{}", record=rec)
        res = AI.ai_suggest_attributes(
            self._product(attrs=[{"attribute_id": "1", "attribute_name": "原产地",
                                  "value_id": "100000458", "value_name": "China"}]),
            [ORIGIN_DEF], _cfg(), missing=["Place of Origin"], client=client)
        self.assertFalse(res["called"])
        self.assertNotIn("model", rec)
        self.assertTrue(res["notes"])


class ValidateAttributeBlockersTests(unittest.TestCase):
    def _save(self, attrs):
        return {"subject": "Pool", "keywords": "pool, spa", "mainImage": "https://x/a.jpg",
                "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
                "priceType": "ladder_price",
                "bulkDiscountPrices": '[{"start_quantity":1,"price":10}]',
                "wholesaleTrade": '{"min_order_quantity":1,"package_size":"1X1X1","weight":"2"}',
                "attributes": json.dumps(attrs, ensure_ascii=False),
                "detailImage": "[]", "textDesc": "x"}

    def test_missing_required(self):
        issues = P.validate_alibaba_save({}, self._save([]), _cfg(), [COLOR_DEF], [])
        self.assertTrue(any("必填属性缺失：Color" in i for i in issues), issues)

    def test_required_value_empty(self):
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "210190015", "attribute_name": "Color",
                             "value_id": "4", "value_name": ""}]), _cfg(), [COLOR_DEF], [])
        self.assertTrue(any("必填属性「Color」值为空" in i for i in issues), issues)

    def test_required_value_id_missing_is_blocked_with_readable_hint(self):
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "210190015", "attribute_name": "Color",
                             "value_id": "-1", "value_name": "Other"}]), _cfg(), [COLOR_DEF], [])
        self.assertTrue(any("必填属性「Color」缺少 value_id" in i for i in issues), issues)
        self.assertTrue(any("id=4" in i for i in issues), issues)

    def test_required_value_not_in_enum_is_blocked(self):
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "210190015", "attribute_name": "Color",
                             "value_id": "-1", "value_name": "Neon Pink"}]),
            _cfg(), [COLOR_DEF], [])
        self.assertTrue(any("Color" in i and "不在平台候选值中" in i for i in issues), issues)

    def test_required_value_id_mismatch_is_blocked(self):
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "210190015", "attribute_name": "Color",
                             "value_id": "5412614", "value_name": "Blue"}]),
            _cfg(), [COLOR_DEF], [])
        self.assertTrue(any("value_id=5412614" in i and "不一致" in i for i in issues), issues)

    def test_valid_resolved_payload_passes_attribute_checks(self):
        issues = P.validate_alibaba_save(
            {}, self._save([
                {"attribute_id": "210190015", "attribute_name": "Color",
                 "value_id": "123085", "value_name": "Blue"},
                {"attribute_id": "1", "attribute_name": "Place of Origin",
                 "value_id": "100000458", "value_name": "China"},
            ]), _cfg(), [COLOR_DEF, ORIGIN_DEF], [])
        self.assertEqual([i for i in issues if "属性" in i], [])

    def test_free_text_required_accepts_any_nonempty_value(self):
        d = _def("Model Number", "型号", values="", input_type="input", attr_name_id="2")
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "2", "attribute_name": "Model Number",
                             "value_id": "-1", "value_name": "LS-600"}]), _cfg(), [d], [])
        self.assertEqual([i for i in issues if "属性" in i], [])

    def test_platform_other_candidate_with_id_minus_one_passes(self):
        """国家枚举里的「other」候选 id 就是 -1，不能当成 value_id 缺失误报。"""
        issues = P.validate_alibaba_save(
            {}, self._save([{"attribute_id": "1", "attribute_name": "Place of Origin",
                             "value_id": "-1", "value_name": "other"}]), _cfg(),
            [ORIGIN_DEF], [])
        self.assertEqual([i for i in issues if "属性" in i], [], issues)

    def test_null_values_string_has_no_candidates(self):
        d = _def("Production Capacity", "产能", values="null", input_type="input",
                 attr_name_id="3")
        self.assertEqual(P._value_entries(d), [])
        self.assertTrue(P._is_free_text(d))


class BuildSaveIntegrationTests(unittest.TestCase):
    def _product(self):
        return {
            "id": "220114598434958534", "shopId": "9466975",
            "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
            "subject": "Container Swimming Pool", "keywords": "pool, container pool",
            "attributes": json.dumps([
                {"attribute_id": "-1", "attribute_name": "Place of Origin",
                 "value_id": "-1", "value_name": "China"},
                {"attribute_id": "-1", "attribute_name": "Color",
                 "value_id": "-1", "value_name": "Other"},
                {"attribute_id": "-1", "attribute_name": "Material",
                 "value_id": "-1", "value_name": "Glass Fiber"},
                {"attribute_id": "-1", "attribute_name": "Applicable People",
                 "value_id": "-1", "value_name": "All"},
                {"attribute_id": "-1", "attribute_name": "Type",
                 "value_id": "-1", "value_name": "Above Ground Pools"},
            ], ensure_ascii=False),
            "mainImage": "https://img.example.com/a.jpg",
            "detailImage": [{"url": "https://img.example.com/d1.jpg"}],
            "textDesc": "<p>pool</p>",
            "bulkDiscountPrices": [{"start_quantity": 1, "price": 4000}],
            "wholesaleTrade": {"min_order_quantity": 1, "package_size": "300X200X150",
                               "weight": "300"},
            "priceType": "ladder_price",
            "productSku": {"attributes": [], "exclude_skus": [], "special_skus": []},
        }

    def test_build_payload_fills_value_ids_and_exposes_reasons(self):
        defs = [ORIGIN_DEF, COLOR_DEF, MATERIAL_DEF, PEOPLE_DEF,
                _def("Type", "类型", values=_vals(("1", "Heated Pools"),
                                                  ("3", "Above Ground Pools"),
                                                  ("4", "Other")),
                     input_type="multi_select", attr_name_id="191284229")]
        from aeclone.services.alibaba_payload import build_alibaba_save, validate_alibaba_save
        save = build_alibaba_save(self._product(), _cfg(), attr_defs=defs)
        attrs = json.loads(save["attributes"])
        ids = {(a["attribute_name"], a["value_name"]): a["value_id"] for a in attrs}
        self.assertEqual(ids[("Color", "Other")], "4")
        self.assertEqual(ids[("Type", "Above Ground Pools")], "3")
        self.assertIsInstance(save.get("_unresolved_reasons"), list)
        self.assertEqual(save["_unresolved"], [])
        issues = validate_alibaba_save(self._product(), save, _cfg(), defs, [])
        self.assertEqual([i for i in issues if "属性" in i], [], issues)


class MapSuggestionCompatTests(unittest.TestCase):
    def test_map_attribute_suggestion_uses_platform_enum(self):
        defs = [{"nameEn": "Color",
                 "values": '[{"id":"1","name_en":"White"},{"id":"2","name_en":"Blue"}]'},
                {"nameEn": "Material", "values": '[{"id":"9","name_en":"Metal"}]'}]
        got = AI.map_attribute_suggestion({"Color": "blue", "Material": "Plastic"}, defs)
        self.assertEqual(got["Color"], {"value_id": "2", "value_name": "Blue"})
        self.assertNotIn("Material", got)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
