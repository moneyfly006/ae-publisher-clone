"""Amazon 刊登 13 号页 UI 离线验收（契约 §4.3 / §4.4 / §4.5 / §7）。

只跑沙箱：不联网、不读真实 Cookie、不写任何平台数据。
后端模块（services.amazon_publish / amazon_payload / amazon_pipeline）尚未就绪时，
前端必须仍能渲染页面并给出「后端未就绪」提示；本文件用 mock 后端验证可用控件，
另外单独验证「后端缺失也不崩溃」。

运行：AURORA_TEST=1 .venv/bin/python tests/test_amazon_ui_page.py
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

from aeclone.platforms import AMAZON, get_platform  # noqa: E402
from aeclone.ui import app as ui  # noqa: E402
from aeclone.ui.navigation import DESTINATIONS  # noqa: E402

PRODUCT_ID = "220114595363182310"
MARKETPLACE = "A2EUQ1WTGCTBG2"
SITE_CODE = "CA"
CATEGORY_ID = "13553005011"
CATEGORY_PATH = "6299024011/6256963011/6256964011/13553005011"

# task-8/9：真实发布失败 errMsg 样例（缺必填属性）与自动补齐结果
PUBLISH_ERR = ("发布失败:code:90220,“型号” 为必填项，但缺失。,"
               "属性:model_number;属性:part_number;属性:model_name;"
               "属性:manufacturer_contact_information")
EXTRA_ERR = ("发布失败:code:90220,缺少必填项,属性:item_depth_width_height;"
             "属性:country_of_origin;属性:list_price")
PUBLISH_REQUIRED = ["model_number", "part_number", "model_name",
                    "manufacturer_contact_information"]
AUTOFILL = {"model_number": "HW20-28", "model_name": "Backhoe 2.5T",
            "part_number": "HW20-28-01",
            "manufacturer_contact_information": "Generic, CN"}


# ---------- mock 后端（契约 §3 签名） ----------
class FakeClient:
    def __init__(self):
        self.saved = []

    def check_login(self):
        return True

    def shops(self):
        return [{"id": "9408005", "name": "QingHe",
                 "merchant_id": "A26OV9RTCAYJMS", "default_marketplace": MARKETPLACE}]

    def sites(self, shop_id):
        return [{"marketplace_id": MARKETPLACE, "code": SITE_CODE, "country": "加拿大",
                 "currency": "CAD", "language": "en", "language_name": "英语"}]

    def list_products(self, state="draft", page=1, page_size=20, shop_id="-1", keyword=""):
        err = PUBLISH_ERR if state == "publishFail" else ""
        asin = "B0TEST12345" if state == "online" else ""
        return {
            "items": [{
                "id": PRODUCT_ID, "title": "Fake Backhoe Title", "sku": "WY-QHCA-LTM-1",
                "marketPlaceId": MARKETPLACE, "standardPrice": 12.5,
                "standardPriceCurrency": "CAD", "quantity": 0,
                "errMsg": "", "err_msg": err, "warningMsg": "需补图片",
                "asin": asin,
            }],
            "total": 1, "page_no": 1, "total_page": 1,
            "drafts": 16, "wait_publish": 5, "publish_fail": 21,
            "markets": {MARKETPLACE: "加拿大"},
        }

    def publish_state(self, product_id, shop_id="-1"):
        return {"state": "publishFail", "err_msg": PUBLISH_ERR, "asin": "",
                "title": "Fake Backhoe Title", "counts": {},
                "required_attrs": list(PUBLISH_REQUIRED),
                "attribute_notes": dict(AUTOFILL)}

    def parse_required_attrs(self, err_msg):
        from aeclone.integrations.amazon import parse_required_attrs
        return parse_required_attrs(err_msg)

    def product_detail(self, product_id):
        return {
            "site": SITE_CODE,
            "categoryName": "Backhoes",
            "categoryTree": [],
            "product": {
                "id": str(product_id), "title": "Fake Backhoe Title",
                "bulletPoint": "±".join([f"FEATURE {n}" for n in (
                    "one", "two", "three", "four", "five",
                    "six", "seven", "eight", "nine", "ten")]),
                "description": "<p>旧描述</p>",
                "searchTerms": "old keywords", "brand": "Generic", "manufacturer": "Generic",
                "sku": "WY-QHCA-LTM-1", "standardPrice": "12.5", "saleSalePrice": 0,
                "quantity": "0", "marketPlaceId": MARKETPLACE,
                "categoryId": CATEGORY_ID, "categoryBrowsePath": CATEGORY_PATH,
                "categoryName": "Backhoes",
                "mainImage": "https://example.invalid/main.jpg",
                "extraImagesStr": "https://example.invalid/a.jpg;https://example.invalid/b.jpg",
                "productAttr": json.dumps({"item_type": "backhoe"}),
                "switchFulfillmentTo": "MFN", "merchantShippingGroup": "tpl-1",
            },
        }

    def category_roots(self, site_code):
        return [
            {"id": "9001", "categoryId": "6299024011", "categoryName": "Backhoes",
             "chineseName": "挖掘机", "nodePath": "6299024011", "leafCategory": False,
             "childIds": ["9002"]},
            {"id": "9003", "categoryId": "6256963011", "categoryName": "Loaders",
             "chineseName": "装载机", "nodePath": "6256963011", "leafCategory": False,
             "childIds": []},
        ]

    def category_children(self, site_code, parent_id):
        return [{"id": "9002", "categoryId": CATEGORY_ID, "categoryName": "Backhoes",
                 "chineseName": "挖掘机", "nodePath": CATEGORY_PATH,
                 "leafCategory": True, "childIds": []}]

    def category_by_path(self, site_code, node_path):
        return {"id": "9002", "categoryId": CATEGORY_ID, "categoryName": "Backhoes",
                "chineseName": "挖掘机", "nodePath": node_path, "leafCategory": True}

    def category_history(self, shop_id, site_code):
        return [{"categoryId": CATEGORY_ID, "categoryName": "Backhoes",
                 "categoryBrowsePath": CATEGORY_PATH, "num": 3}]

    def product_types(self, site_code):
        return ["OUTDOOR_LIVING"]

    def predict_category(self, shop_id, site_code, item_name):
        return {"productType": "OUTDOOR_LIVING",
                "amaoznCategorySearchVos": [{"categoryName": "Backhoes"}]}

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def logistics(self, shop_id, marketplace_id):
        return [{"templateId": "tpl-1", "templateName": "CA 标准物流"}]

    def brands(self, shop_id, marketplace_id):
        return []

    def check_banned_words(self, **kwargs):
        return []

    def save_product(self, payload, op="1"):
        self.saved.append((payload, op))
        return {"code": 0, "data": {"msg": "ok"}}


class FakeResult:
    def __init__(self, *, saved=False, preview=False, ok=True, error="", issues=None,
                 publish_state="", publish_error="", publish_asin="",
                 publish_detail=None, required_attrs=None, attribute_notes=None):
        self.product_id = PRODUCT_ID
        self.ok = ok
        self.error = error
        self.steps = [SimpleNamespace(name="连接/读详情", ok=True, detail="ok"),
                      SimpleNamespace(name="站点+币种", ok=True, detail="CA/CAD"),
                      SimpleNamespace(name="必填校验", ok=ok, detail="" if ok else "缺字段")]
        self.preview = preview
        self.saved = saved
        self.issues = list(issues or [])
        self.warnings = ["提醒：库存 0", "提醒：品牌 Generic"]
        self.title = "Fake Title"
        self.bullets = ["b1", "b2", "b3", "b4", "b5"]
        self.description = "<p>AI desc</p>"
        self.search_terms = "fake keyword"
        self.site = SITE_CODE
        self.site_code = SITE_CODE
        self.currency = "CAD"
        self.category_id = CATEGORY_ID
        self.op = "1"
        self.banned_words = []
        self.save_result = {"code": 0}
        self.ai_used = False
        # task-8 新字段
        self.publish_state = publish_state
        self.publish_error = publish_error
        self.publish_asin = publish_asin
        self.publish_detail = publish_detail or {}
        self.required_attrs = list(required_attrs or [])
        self.attribute_notes = dict(attribute_notes or {})

    @property
    def completion_label(self):
        return "完成" if self.ok else "失败"

    def log(self):
        return ""


def make_backend():
    """返回 (make_client, payload, pipeline) 三个契约对象，并记录调用。"""
    client = FakeClient()
    calls = {"prepare": [], "run": [], "ai": [], "ai_all": [], "verify": [],
             "autofill": []}
    behavior = {
        "run_publish_state": "publishFail",
        "run_publish_error": PUBLISH_ERR,
        "run_publish_asin": "",
        "run_required_attrs": list(PUBLISH_REQUIRED),
        "run_attribute_notes": dict(AUTOFILL),
        "run_saved": True,
        "verify": {"state": "publishFail", "err_msg": PUBLISH_ERR, "asin": "",
                   "title": "Fake Backhoe Title", "counts": {},
                   "required_attrs": list(PUBLISH_REQUIRED),
                   "attribute_notes": dict(AUTOFILL)},
        "autofill": dict(AUTOFILL),
    }

    def prepare_save(pid, cfg, overrides=None, *, client=None, op="1"):
        calls["prepare"].append({"pid": pid, "overrides": overrides})
        return ({"site": SITE_CODE}, {"title": "Fake Title"}, [], ["提醒"])

    def run_amazon_product(pid, cfg, overrides=None, dry_run=True, use_ai=False,
                           op="1", confirm_write=False, client=None):
        calls["run"].append({"pid": pid, "overrides": overrides, "dry_run": dry_run,
                             "use_ai": use_ai, "op": op,
                             "confirm_write": confirm_write})
        pub = {}
        if not dry_run and str(op) == "2":
            pub = dict(publish_state=behavior["run_publish_state"],
                       publish_error=behavior["run_publish_error"],
                       publish_asin=behavior["run_publish_asin"],
                       required_attrs=list(behavior["run_required_attrs"]),
                       attribute_notes=dict(behavior["run_attribute_notes"]))
        return FakeResult(saved=bool(behavior["run_saved"]) if not dry_run else False,
                          preview=dry_run, **pub)

    def verify_publish(pid, cfg, *, client=None, wait_seconds=0, interval=10):
        calls["verify"].append({"pid": pid, "wait_seconds": wait_seconds})
        return dict(behavior["verify"])

    def autofill_attributes(product, cfg, marketplace_id="", overrides=None,
                            required=None, template=None):
        calls["autofill"].append({"product_id": (product or {}).get("id"),
                                  "marketplace_id": marketplace_id,
                                  "required": list(required or [])})
        return dict(behavior["autofill"])

    def ai_generate_field(product, cfg, field, marketplace_id="", count=5):
        calls["ai"].append({"field": field, "count": count})
        value = {"title": "AI Title Example",
                 "bullets": ["AI 卖点 1", "AI 卖点 2", "AI 卖点 3", "AI 卖点 4", "AI 卖点 5"],
                 "description": "<p>AI 描述</p>",
                 "search_terms": "ai keyword",
                 "sku": "Fake-SKU-001"}.get(field, "")
        return value, []

    def ai_generate_all(product, cfg, marketplace_id="", count=5):
        calls["ai_all"].append({"count": count})
        return ({"title": "AI Title Example",
                 "bullets": ["AI 卖点 1", "AI 卖点 2", "AI 卖点 3", "AI 卖点 4", "AI 卖点 5"],
                 "description": "<p>AI 描述</p>",
                 "search_terms": "ai keyword"}, [])

    payload = SimpleNamespace(
        split_bullets=lambda raw: [x.strip() for x in str(raw or "").split("±")
                                   if x.strip()],
        join_bullets=lambda bullets: "±".join(
            [str(x).strip() for x in (bullets or []) if str(x or "").strip()]),
    )
    pipeline = SimpleNamespace(
        prepare_save=prepare_save,
        run_amazon_product=run_amazon_product,
        verify_publish=verify_publish,
        autofill_attributes=autofill_attributes,
        ai_generate_field=ai_generate_field,
        ai_generate_all=ai_generate_all,
        forced_preview=lambda: bool(os.environ.get("AURORA_NO_SAVE")
                                    or os.environ.get("AURORA_TEST")),
    )
    return (lambda cfg: client), payload, pipeline, client, calls, behavior


@contextmanager
def fake_backend(behavior=None):
    make_client, payload, pipeline, client, calls, beh = make_backend()
    if behavior:
        beh.update(behavior)
    with patch.object(ui.App, "_amazon_backend",
                      lambda self: (make_client, payload, pipeline)):
        yield SimpleNamespace(client=client, calls=calls, payload=payload,
                              pipeline=pipeline, behavior=beh)


# ---------- mock 预设库（task-12 契约） ----------
PRESET_PROMPT_KEYS = {"title": "amazon_title_prompt",
                      "bullets": "amazon_bullet_prompt",
                      "description": "amazon_description_prompt",
                      "search_terms": "amazon_search_terms_prompt",
                      "sku": "amazon_sku_prompt",
                      "image": "amazon_image_prompt"}
PRESET_MARKER_KEYS = {"title": "amazon_title_preset",
                      "bullets": "amazon_bullet_preset",
                      "description": "amazon_description_preset",
                      "search_terms": "amazon_search_terms_preset",
                      "sku": "amazon_sku_preset",
                      "image": "amazon_image_preset"}


def make_presets():
    data = {
        "title": {"industry_b2b": ("工业机械 B2B 参数型", "型号+规格+场景"),
                  "seo_generic": ("通用 SEO 标题", "高转化关键词")},
        "bullets": {"benefit_first": ("Benefit-first", "利益点开头"),
                    "spec_first": ("参数规格优先", "B2B 采购")},
        "description": {"industrial_standard": ("标准工业结构", ""),
                        "param_table": ("参数表导向", "")},
        "search_terms": {"core_attr": ("核心词+属性", ""),
                         "longtail": ("长尾组合", "")},
        "sku": {"brand_model": ("品牌-型号-规格", ""),
                "short_code": ("纯字母数字短码", "")},
        "image": {"main_white": ("主图纯白底", "白底主图"),
                  "scene": ("工业场景图", "场景"),
                  "translate": ("图片文字翻译", "")},
    }
    presets = {}
    for kind, items in data.items():
        presets[kind] = {k: {"name": name, "desc": desc,
                             "prompt": f"{kind} {k} {{title}} {{language}}"}
                         for k, (name, desc) in items.items()}
    calls = {"apply": [], "reset": []}

    def preset_options(kind):
        return [(k, v["name"]) for k, v in presets.get(kind, {}).items()]

    def get_preset(kind, key):
        return dict(presets.get(kind, {}).get(key) or {})

    def list_presets(kind):
        return list(presets.get(kind, {}).values())

    def apply_to_cfg(cfg, kind, key):
        prompt = presets[kind][key]["prompt"]
        cfg.set(PRESET_PROMPT_KEYS[kind], prompt)
        cfg.set(PRESET_MARKER_KEYS[kind], key)
        calls["apply"].append((kind, key))
        return prompt

    def resolve_selected(cfg, kind):
        return str(cfg.get(PRESET_MARKER_KEYS[kind], "") or "")

    def reset_to_default(cfg, kind):
        cfg.set(PRESET_PROMPT_KEYS[kind], "")
        cfg.set(PRESET_MARKER_KEYS[kind], "")
        calls["reset"].append(kind)
        return ""

    def render(text, mapping):
        out = str(text or "")
        for k, v in (mapping or {}).items():
            if v:
                out = out.replace("{" + k + "}", str(v))
        return out

    return SimpleNamespace(PRESET_KINDS=tuple(PRESET_PROMPT_KEYS),
                           presets=presets, calls=calls,
                           preset_options=preset_options, get_preset=get_preset,
                           list_presets=list_presets, apply_to_cfg=apply_to_cfg,
                           resolve_selected=resolve_selected,
                           reset_to_default=reset_to_default, render=render)


@contextmanager
def fake_presets():
    mod = make_presets()
    with patch.object(ui.App, "_amazon_presets", lambda self: mod):
        yield mod


# ---------- mock 自动发布库（task-15 契约） ----------
class FakeAutoResult:
    """对齐后端 task-15 AutoPublishResult 的 dict 字段（rounds 为逐轮记录列表）。"""

    def __init__(self, *, ok=True, state="online", rounds=2, asin="B0AUTO123",
                 error="", fixes=None, required=None, steps=None, elapsed=12.5):
        self.product_id = PRODUCT_ID
        self.ok = ok
        self.saved = bool(ok)
        self.saved_round = rounds if ok else 0
        self.op = "2"
        self.final_state = state
        self.publish_state = state
        self.publish_status = state
        self.publish_error = error
        self.publish_asin = asin
        self.asin = asin
        self.attempts = int(rounds or 0)
        self.elapsed = elapsed
        self.elapsed_seconds = elapsed
        self.required_attrs = list(required or [])
        self.fix_log = list(fixes if fixes is not None
                            else ["补齐必填属性 model_number", "修正标题重复词"])
        self.blockers = []
        self.warnings = []
        if steps is not None:
            self.rounds = list(steps)
        else:
            records = []
            for _i in range(1, int(rounds or 0) + 1):
                last = _i == int(rounds or 0)
                records.append({
                    "round": _i, "op": "2",
                    "publish_state": state if last else "publishFail",
                    "status": "online" if last else "failed",
                    "err": "" if last else "code:90220 属性:model_number",
                    "fixes": [] if last else ["自动补齐 model_number"],
                })
            self.rounds = records
        self.detail = f"ASIN {asin}" if asin else ""
        self.error = error

    def __getattr__(self, name):
        # 模拟 AutoPublishResult 的 dict 属性访问
        raise AttributeError(name)


@contextmanager
def fake_auto(result=None, missing=False):
    calls = {"auto": []}

    def auto_publish(pid, cfg, overrides=None, op="2", max_rounds=3,
                     wait_seconds=90, auto_fix_attrs=True, auto_fix_title=True,
                     auto_fix_description=True, auto_ai_fix=False, client=None):
        calls["auto"].append(dict(product_id=pid, overrides=overrides, op=op,
                                  max_rounds=max_rounds,
                                  wait_seconds=wait_seconds,
                                  auto_fix_attrs=auto_fix_attrs,
                                  auto_fix_title=auto_fix_title,
                                  auto_fix_description=auto_fix_description,
                                  auto_ai_fix=auto_ai_fix))
        return result or FakeAutoResult()

    mod = SimpleNamespace(auto_publish=auto_publish, calls=calls)
    if missing:
        with patch.object(ui.App, "_amazon_auto",
                          side_effect=ImportError("no amazon_auto")):
            yield mod
    else:
        with patch.object(ui.App, "_amazon_auto", lambda self: mod):
            yield mod


@contextmanager
def sync_threads():
    """把后台线程改成同步执行，便于断言 UI 结果（沙箱本身已禁用网络）。"""
    with patch.object(threading.Thread, "start", lambda thread: thread.run()):
        yield


# ---------- 控件辅助 ----------
def controls(app):
    return list(walk(app.content.controls))


def texts(app):
    return {c.value for c in controls(app) if isinstance(c, ft.Text) and c.value}


def labels(app):
    return {getattr(c, "label", "") for c in controls(app)
            if getattr(c, "label", "")}


def buttons(app):
    kinds = (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton)
    return [c for c in controls(app) if isinstance(c, kinds)]


def find_button(app, text, exact=False):
    for ctrl in buttons(app):
        value = str(getattr(ctrl, "text", "") or "")
        if (value == text) if exact else (text in value):
            return ctrl
    raise AssertionError(f"按钮不存在：{text}；现有：{[getattr(b, 'text', '') for b in buttons(app)]}")


def find_control(app, cls, predicate=None):
    for ctrl in controls(app):
        if isinstance(ctrl, cls) and (predicate is None or predicate(ctrl)):
            return ctrl
    return None


def click(ctrl):
    event = SimpleNamespace(control=ctrl, data=str(getattr(ctrl, "value", "") or ""))
    ctrl.on_click(event)


def change(ctrl):
    event = SimpleNamespace(control=ctrl, data=str(getattr(ctrl, "value", "") or ""))
    ctrl.on_change(event)


def snack(app):
    try:
        return str(app.page.snack_bar.content.value or "")
    except Exception:
        return ""


class AmazonPageTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()
        self.app._switch_platform("amazon")

    # ---------- 导航 / 页面结构 ----------
    def test_single_editor_keeps_scope_and_clears_other_product_form(self):
        self.app.cfg.set("amazon_shop_id", "9408005")
        self.app.cfg.set("amazon_site", MARKETPLACE)
        with fake_backend({"autofill": {}}) as env, sync_threads():
            original = env.client.product_detail

            def field(label, cls=ft.TextField):
                return find_control(self.app, cls, lambda c: c.label == label)

            def detail(pid):
                result = original(pid)
                row = result["product"]
                row["shopId"] = "other-shop" if pid == "outside" else "9408005"
                if pid == "second":
                    row.update(categoryId="", merchantShippingGroup="", switchFulfillmentTo="AFN")
                return result

            env.client.product_detail = detail
            self.app._switch(16)
            picker = field("或输入商品 ID")
            self.assertFalse(self.app.content.controls[-1].visible)
            picker.value = PRODUCT_ID
            click(find_button(self.app, "按 ID 载入"))
            self.assertTrue(self.app.content.controls[-1].visible)
            model = field("型号（model_number）")
            model.value = "first-model"
            model.on_change(SimpleNamespace(control=model))
            click(find_button(self.app, "换一件商品"))
            picker.value = "outside"
            click(find_button(self.app, "按 ID 载入"))
            self.assertEqual(self.app._amz_form["id"], PRODUCT_ID)
            picker.value = "second"
            click(find_button(self.app, "按 ID 载入"))
            self.assertEqual(self.app._amz_form["id"], "second")
            self.assertEqual(model.value, "")
            self.assertEqual(self.app.cfg.amazon_category_id, "")
            self.assertEqual(field("配送方式", ft.Dropdown).value, "AFN")
            self.assertTrue(field("数量").disabled)

    def test_amazon_nav_has_three_steps_with_single_editor_in_overview(self):
        for i in (15, 45, 21, 16):
            self.assertIn(i, {d.index for d in DESTINATIONS})
        self.assertEqual(AMAZON.page(16).group, "__internal__")
        self.assertEqual(AMAZON.groups(),
                         ["工作空间", "自动处理", "发布与运行", "工具与设置"])
        nav = [b.content.controls[1].value for _, b in self.app.nav_buttons]
        self.assertEqual(nav, ["概览", "连接与店铺", "处理规则", "运行与结果", "任务与日志", "图片工作台", "设置"])
        self.app._sync_run_button()
        self.assertEqual(self.app.btn_run_text.value, "自动处理采集箱")
        # 导航搜索按功能命中
        for query, expect in (("五点", [45]), ("采集箱", [21]), ("发布失败", [21])):
            self.app._filter_navigation(query)
            self.assertEqual([i for i, b in self.app.nav_buttons if b.visible], expect,
                             f"搜索 {query} 命中异常")
        self.app._filter_navigation("")

    def test_amazon_function_pages_show_only_their_card(self):
        expected = {15: "检测连接", 17: "自动补齐必填属性",
                    18: "刷新运费模板", 34: "AI 生成 SKU", 19: "AI 生成五点",
                    20: "处理并回填图片", 21: "开始自动处理"}
        for index, action in expected.items():
            self.app._switch(index)
            self.assertEqual(self.app._current, index)
            find_button(self.app, action)
            if index != 21:
                self.assertFalse(any(b.text == "保存到 Amazon" for b in buttons(self.app)))
            if index != 19:
                self.assertFalse(any(b.text == "AI 生成五点" for b in buttons(self.app)))


    def test_hidden_route_13_renders_full_workbench(self):
        """13 号隐藏路由仍可 _switch(13) 渲染完整 7 卡（旧测试/深链兼容）。"""
        self.app._switch(13)
        self.assertEqual(self.app._page_idx, 13)
        self.assertIn("Amazon 刊登", self.app.breadcrumb.value)
        page_text = " ".join(texts(self.app))
        for title in ("① 连接与店铺", "② 采集箱商品", "③ 站点与类目",
                      "价格与物流", "⑤ 文案与 AI", "商品图片", "⑦ 发布与结果"):
            self.assertIn(title, page_text)

    def test_page_13_renders_in_dark_and_light_themes(self):
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            with self.subTest(theme=theme):
                self.app._switch(13)
                self.assertEqual(self.app._page_idx, 13)
                rendered = controls(self.app)
                self.assertGreater(len(rendered), 100, "13 号页控件树异常")
                # 主题切换后关键文案仍在（颜色走主题变量，不写死深色）
                self.assertIn("Amazon 刊登", self.app.breadcrumb.value)
                self.assertTrue(any(isinstance(c, ft.Text) and c.value
                                    and "⑦ 发布与结果" in c.value for c in rendered))
        ui.apply_theme("dark")

    def test_page_13_renders_with_all_seven_cards_and_key_controls(self):
        self.app._switch(13)
        self.assertEqual(self.app._page_idx, 13)
        self.assertEqual(self.app._current, 13)
        self.assertIn("Amazon 刊登", self.app.breadcrumb.value)
        all_text = texts(self.app)
        for title in ("① 连接与店铺", "② 采集箱商品", "③ 站点与类目",
                      "价格与物流",
                      "⑤ 文案与 AI",
                      "商品图片",
                      "⑦ 发布与结果"):
            self.assertIn(title, all_text)
        # 关键控件（契约 §4.3）
        self.assertIn("店铺", labels(self.app))
        self.assertIn("站点", labels(self.app))
        self.assertIn("五点描述（每行一条）", labels(self.app))
        # 默认显示 5 行卖点；载入多卖点商品会扩展到最多 10 行（见 10 条回归用例）
        self.assertEqual(len([l for l in labels(self.app) if l == "五点描述（每行一条）"]), 1)
        self.assertIn("标题", labels(self.app))
        self.assertIn("标准价", labels(self.app))
        self.assertIn("Search Terms（建议不超过 250 字节）", labels(self.app))
        # 发布必填属性输入框（task-9）
        for attr_label in ("型号（model_number）", "型号名称（model_name）",
                           "零件编号（part_number）",
                           "制造商联系信息（manufacturer_contact_information）"):
            self.assertIn(attr_label, labels(self.app))
        for btn in ("读取本机 Cookie", "检测连接", "刷新", "上一页", "下一页",
                    "加载类目", "校验路径", "AI/平台推荐产品类型",
                    "AI 生成标题", "AI 生成五点", "AI 生成描述",
                    "AI 生成关键词",
                    "AI 一键生成全部（付费）", "AI 生成 SKU", "刷新运费模板",
                    "查看 productAttr（只读）", "保存到 Amazon",
                    "查询发布结果", "自动补齐并重试发布", "自动发布（自动修复）",
                    "自动补齐必填属性", "恢复内置", "复制提示词"):
            find_button(self.app, btn)
        # 预设下拉 6 类（5 文案 + 图片）
        for label in ("标题预设", "五点描述预设", "产品描述预设",
                      "Search Terms预设", "SKU预设", "图片预设"):
            self.assertIn(label, labels(self.app), f"缺少预设下拉：{label}")
        # 页面不显示预演/检查模式字样；保存按钮直接真实写
        self.assertFalse(any("检查模式" in str(b.text) or "预演" in str(b.text)
                             for b in buttons(self.app)))
        # 状态按钮：采集箱/待发布/发布中/发布失败/在线
        for label in ("采集箱", "待发布", "发布中", "发布失败", "在线"):
            self.assertTrue(any(str(getattr(b, "text", "")).startswith(label)
                                for b in buttons(self.app)), label)

    def test_page_13_layout_groups_controls_by_function(self):
        self.app._switch(13)
        self.assertEqual(self.app._current, 13)
        for action in ("检测连接", "自动补齐必填属性", "刷新运费模板", "AI 生成 SKU",
                       "AI 生成五点", "保存到 Amazon", "自动发布（自动修复）"):
            find_button(self.app, action)
        self.app._switch(17)
        find_button(self.app, "自动补齐必填属性")
        self.assertNotIn("SKU", labels(self.app))
        self.app._switch(34)
        self.assertIn("SKU", labels(self.app))
        self.assertNotIn("标准价", labels(self.app))
        self.app._switch(19)
        self.assertEqual(sum(1 for b in buttons(self.app) if b.text == "AI 生成五点"), 1)
        self.assertFalse(any(b.text == "AI" for b in buttons(self.app)))


    def test_amazon_page_has_no_dev_wording(self):
        """正式版：Amazon 13 页可见文本不得出现测试/开发措辞。"""
        self.app._switch(13)
        page_texts = " ".join(str(t) for t in texts(self.app))
        page_texts += " " + " ".join(str(getattr(b, "text", "")) for b in buttons(self.app))
        for banned in ("AURORA_", "预演", "检查模式", "检查结果", "发布前检查",
                       "测试连接", "本地验收", "AURORA_TEST"):
            self.assertNotIn(banned, page_texts, f"Amazon 页仍含开发措辞：{banned}")
        for kept in ("检测连接", "发布与结果", "自动发布（自动修复）"):
            self.assertIn(kept, page_texts, f"正式版文案缺失：{kept}")

    # ---------- ① 连接 / ② 列表 / ③ 类目 / 载入详情 ----------
    def test_connect_list_load_detail_and_category(self):
        self.app._switch(13)
        with fake_backend(), sync_threads():
            click(find_button(self.app, "检测连接"))
        conn = find_control(self.app, ft.Text,
                            lambda c: c.value and c.value.startswith("检测连接正常"))
        self.assertIsNotNone(conn, f"连接状态未更新；文本：{texts(self.app)}")
        self.assertIn("采集箱 16", conn.value)
        self.assertIn("待发布 5", conn.value)
        self.assertIn("在线 1", conn.value)
        shop_dd = find_control(self.app, ft.Dropdown, lambda c: getattr(c, "label", "") == "店铺")
        site_dd = find_control(self.app, ft.Dropdown, lambda c: getattr(c, "label", "") == "站点")
        self.assertTrue(shop_dd.options, "店铺下拉未填充")
        self.assertTrue(site_dd.options, "站点下拉未填充")
        self.assertEqual(self.app.cfg.amazon_shop_id, "9408005")
        self.assertEqual(self.app.cfg.amazon_site, MARKETPLACE)
        self.assertEqual(self.app.cfg.amazon_site_code, SITE_CODE)

        # 列表：点「采集箱」刷新 → 有行 → 点「编辑」载入详情
        with fake_backend(), sync_threads():
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
        edit = [b for b in buttons(self.app) if str(getattr(b, "text", "")) == "编辑"]
        self.assertTrue(edit, "商品列表没有渲染「编辑」按钮")
        with fake_backend(), sync_threads():
            click(edit[0])
        self.assertEqual(self.app.cfg.amazon_test_product_id, PRODUCT_ID)
        title = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "标题")
        self.assertEqual(title.value, "Fake Backhoe Title")
        bullet1 = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "五点描述（每行一条）")
        self.assertEqual(bullet1.value.splitlines()[0], "FEATURE one")
        price = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "标准价")
        self.assertEqual(price.value, "12.5")
        self.assertEqual(self.app.cfg.amazon_category_id, CATEGORY_ID)
        self.assertEqual(self.app.cfg.amazon_category_path, CATEGORY_PATH)

        # 类目：加载顶级 → 展开子类 → 选末级
        with fake_backend(), sync_threads():
            click(find_button(self.app, "加载类目"))
        cat_root = find_control(self.app, ft.Dropdown, lambda c: getattr(c, "label", "") == "顶级类目")
        self.assertEqual(len(cat_root.options), 2)
        cat_root.value = str(cat_root.options[0].key)
        with fake_backend(), sync_threads():
            change(cat_root)
        choose = [b for b in buttons(self.app) if str(getattr(b, "text", "")) == "选择"]
        self.assertTrue(choose, "子类目没有「选择」按钮")
        with fake_backend(), sync_threads():
            click(choose[0])
        self.assertEqual(self.app.cfg.amazon_category_id, CATEGORY_ID)
        crumb = next((c.value for c in controls(self.app)
                      if isinstance(c, ft.Text) and c.value and "当前类目" in c.value), "")
        self.assertIn(CATEGORY_ID, crumb)

        # 手动路径校验
        path_tf = find_control(self.app, ft.TextField,
                               lambda c: "类目路径" in str(getattr(c, "label", "")))
        path_tf.value = CATEGORY_PATH
        with fake_backend(), sync_threads():
            click(find_button(self.app, "校验路径"))
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "路径校验通过" in c.value for c in controls(self.app)))

        # 推荐产品类型（只显示建议，不自动改类目）
        with fake_backend(), sync_threads():
            click(find_button(self.app, "AI/平台推荐产品类型"))
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "OUTDOOR_LIVING" in c.value for c in controls(self.app)))

    # ---------- ④b/⑦ AI 提示词预设 + SKU AI（task-12/13） ----------
    def _preset_dropdown(self, label):
        return find_control(self.app, ft.Dropdown,
                            lambda c: getattr(c, "label", "") == label)

    def _preset_buttons(self, text):
        return [b for b in buttons(self.app)
                if str(getattr(b, "text", "")) == text]

    def test_prompt_presets_apply_edit_reset_and_selection_name(self):
        with fake_presets() as mod:
            self.app._switch(0)
            self.app._switch(13)
            dd = self._preset_dropdown("标题预设")
            self.assertIsNotNone(dd, "缺少标题预设下拉")
            self.assertFalse(dd.disabled, "预设库就绪时下拉应可用")
            self.assertEqual(len(dd.options), 2)
            key = str(dd.options[0].key)
            dd.value = key
            change(dd)
            prompt = mod.presets["title"][key]["prompt"]
            self.assertEqual(self.app.cfg.amazon_title_prompt, prompt)
            self.assertEqual(self.app.cfg.amazon_title_preset, key)
            self.assertIn("已套用预设", snack(self.app))
            self.assertIn(mod.presets["title"][key]["name"], " ".join(texts(self.app)))
            tf = find_control(
                self.app, ft.TextField,
                lambda c: getattr(c, "label", "") == "标题提示词（可编辑，留空=内置默认）")
            self.assertEqual(tf.value, prompt)
            # 编辑即写回 cfg（防抖落盘）
            tf.value = "自定义标题提示词：{title} {language}"
            change(tf)
            self.assertEqual(self.app.cfg.amazon_title_prompt, tf.value)
            # 恢复内置
            click(self._preset_buttons("恢复内置")[0])
            self.assertEqual(self.app.cfg.amazon_title_prompt, "")
            self.assertEqual(self.app.cfg.amazon_title_preset, "")
            self.assertIn("内置默认", " ".join(texts(self.app)))
            # 预设下拉覆盖 5 类文案 + 图片
            for label, count in (("五点描述预设", 2), ("产品描述预设", 2),
                                 ("Search Terms预设", 2), ("SKU预设", 2),
                                 ("图片预设", 3)):
                dd = self._preset_dropdown(label)
                self.assertIsNotNone(dd, f"缺少预设下拉：{label}")
                self.assertEqual(len(dd.options), count, label)

    def test_ai_generate_sku_button_fills_input_only(self):
        self.app._switch(13)
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "AI 生成 SKU"))
            self.assertIsNotNone(self.app.page.dialog, "SKU AI 缺少付费确认")
            self.assertIn("付费", str(self.app.page.dialog.title.value))
            click(self.app.page.dialog.actions[1])
        sku_tf = find_control(self.app, ft.TextField,
                              lambda c: getattr(c, "label", "") == "SKU")
        self.assertEqual(sku_tf.value, "Fake-SKU-001")
        self.assertEqual(self.app.cfg.amazon_sku, "Fake-SKU-001")
        self.assertEqual(env.calls["ai"][-1]["field"], "sku")
        self.assertEqual(env.calls["run"], [], "SKU AI 只填输入框，不得真实保存/发布")

    def test_image_preset_apply_and_copy_renders_placeholders(self):
        with fake_presets() as mod:
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            self.app._switch(13)  # Load the product site's isolated prompt profile.
            dd = self._preset_dropdown("图片预设")
            self.assertIsNotNone(dd)
            key = "main_white"
            dd.value = key
            change(dd)
            self.assertEqual(self.app.cfg.amazon_image_prompt,
                             mod.presets["image"][key]["prompt"])
            self.assertIn(mod.presets["image"][key]["name"], " ".join(texts(self.app)))
            # 已载入商品/站点：复制时占位符渲染为实际值
            click(find_button(self.app, "复制提示词"))
            clip = str(self.app.page.clipboard or "")
            self.assertIn("Fake Backhoe Title", clip, f"占位符未渲染：{clip}")
            self.assertIn("英语", clip, "站点语言占位符未渲染")
            self.assertNotIn("{title}", clip)
            # 未载入商品：复制原文并提示占位符用法
            self.app._amz_form = {}
            self.app._amz_detail = {}
            with fake_presets():
                self.app._switch(0)
                self.app._switch(13)
            click(find_button(self.app, "复制提示词"))
            clip2 = str(self.app.page.clipboard or "")
            self.assertIn("{title}", clip2)
            self.assertIn("占位符可用", snack(self.app))

    def test_presets_backend_missing_is_friendly(self):
        with patch.object(ui.App, "_amazon_presets",
                          side_effect=ImportError("no amazon_presets")):
            self.app._switch(0)
            self.app._switch(13)
            dd = self._preset_dropdown("标题预设")
            self.assertTrue(dd.disabled, "预设库缺失时下拉应禁用")
            change(dd)
        self.assertIn("预设库未就绪", snack(self.app))
        self.assertIn("文案提示词",
                      " ".join(texts(self.app)))
        self.assertIn("商品图片", " ".join(texts(self.app)))

    # ---------- ④ AI ----------
    def test_ai_generation_needs_switch_then_fills_fields(self):
        self.app._switch(13)
        # AI 关闭时先拒绝（避免误触发付费调用）
        with fake_backend(), sync_threads():
            click(find_button(self.app, "AI 生成标题"))
        self.assertIn("开关", snack(self.app))
        # 载入商品
        with fake_backend(), sync_threads():
            click(find_button(self.app, "检测连接"))
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
        with fake_backend(), sync_threads():
            click([b for b in buttons(self.app)
                   if str(getattr(b, "text", "")) == "编辑"][0])
        # AI 开关关闭时仍拒绝
        with fake_backend(), sync_threads():
            click(find_button(self.app, "AI 生成标题"))
        self.assertIn("开关", snack(self.app))
        # 打开开关 → 弹出付费确认 → 确认后填入标题
        ai_switch = find_control(self.app, ft.Switch,
                                 lambda c: "AI" in str(getattr(c, "label", "")))
        ai_switch.value = True
        with fake_backend() as env, sync_threads():
            change(ai_switch)
            click(find_button(self.app, "AI 生成标题"))
            self.assertIsNotNone(self.app.page.dialog, "没有弹付费确认")
            self.assertIn("付费", str(self.app.page.dialog.title.value))
            click(self.app.page.dialog.actions[1])
        title = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "标题")
        self.assertEqual(title.value, "AI Title Example")
        self.assertTrue(env.calls["ai"], "ai_generate_field 未被调用")
        # 一键生成全部
        with fake_backend() as env2, sync_threads():
            click(find_button(self.app, "AI 一键生成全部"))
            self.assertIsNotNone(self.app.page.dialog)
            self.assertIn("付费", str(self.app.page.dialog.title.value))
            click(self.app.page.dialog.actions[1])
        self.assertTrue(env2.calls["ai_all"])
        bullet1 = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "五点描述（每行一条）")
        self.assertEqual(bullet1.value.splitlines()[0], "AI 卖点 1")
        desc = find_control(self.app, ft.TextField, lambda c: "产品描述" in str(getattr(c, "label", "")))
        self.assertIn("AI 描述", desc.value)

    def test_ten_bullets_are_preserved_on_save(self):
        self.app._switch(13)
        self._load_product()
        editor = find_control(self.app, ft.TextField,
                              lambda c: c.label == "五点描述（每行一条）")
        values = editor.value.splitlines()
        self.assertEqual(len(values), 10)
        self.assertEqual(values[-1], "FEATURE ten")
        editor.value = "\n".join(["x" * 701, *values[1:]])
        change(editor)
        self.assertTrue(any(isinstance(c, ft.Text) and "701/700" in str(c.value)
                            and c.color == ui.DANGER_C for c in controls(self.app)))
        editor.value = "\n".join(values)
        change(editor)
        self._real_mode()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "保存到 Amazon"))
            click(self.app.page.dialog.actions[1])
        overrides = env.calls["run"][-1]["overrides"]
        self.assertEqual(overrides["bullets"], values)


    # ---------- ⑥ 保存 / 发布 ----------
    def _load_product(self):
        with fake_backend(), sync_threads():
            click(find_button(self.app, "检测连接"))
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
            click([b for b in buttons(self.app)
                   if str(getattr(b, "text", "")) == "编辑"][0])

    def _real_mode(self):
        """切到「正式环境」页面（安全护栏关闭），保留已载入的商品表单。"""
        ctx = patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""})
        ctx.start()
        self.addCleanup(ctx.stop)
        self.app._preview_mode = False
        self.app._switch(0)
        self.app._switch(13)

    def _set_price_qty(self, price="12.5", qty="5"):
        ptf = find_control(self.app, ft.TextField,
                           lambda c: getattr(c, "label", "") == "标准价")
        qtf = find_control(self.app, ft.TextField,
                           lambda c: getattr(c, "label", "") == "数量")
        ptf.value = price
        change(ptf)
        qtf.value = qty
        change(qtf)

    def test_save_is_real_without_preview_ui_and_guard_blocks_write(self):
        """⑥ 只保留真实保存：无预演按钮/文案；安全护栏环境拒绝且不显示开发字样。"""
        self.app._switch(13)
        page_all = " ".join(texts(self.app)) + " " + " ".join(
            str(getattr(b, "text", "")) for b in buttons(self.app))
        for banned in ("预演", "检查模式", "发布前检查", "检查结果", "AURORA_"):
            self.assertNotIn(banned, page_all)
        find_button(self.app, "保存到 Amazon")
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "保存到 Amazon"))
        self.assertIsNone(self.app.page.dialog, "安全护栏环境不应弹真实保存确认")
        self.assertIn("禁止写入平台数据", snack(self.app))
        for banned in ("预演", "AURORA_", "检查模式", "测试"):
            self.assertNotIn(banned, snack(self.app))
        self.assertEqual(env.calls["run"], [], "安全护栏环境不能真实保存")

    def test_real_save_flow_uses_confirm_and_backend(self):
        # 模拟正式环境：清空 AURORA_TEST/AURORA_NO_SAVE 并重建页面
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            save_btn = find_button(self.app, "保存到 Amazon")
            with fake_backend() as env, sync_threads():
                click(save_btn)
                self.assertIsNotNone(self.app.page.dialog, "保存没有二次确认弹窗")
                dialog_text = " ".join(str(c.value) for c in walk([self.app.page.dialog.content])
                                       if isinstance(c, ft.Text))
                self.assertIn(PRODUCT_ID, dialog_text)
                self.assertIn("保存草稿", dialog_text)
                click(self.app.page.dialog.actions[1])
            self.assertTrue(env.calls["prepare"], "保存前未调用 prepare_save")
            self.assertTrue(env.calls["run"], "保存未调用 run_amazon_product")
            self.assertFalse(env.calls["run"][-1]["dry_run"], "保存必须 dry_run=False")
            self.assertTrue(env.calls["run"][-1].get("confirm_write"),
                            "真实保存必须显式 confirm_write=True")
            self.assertTrue(any(isinstance(c, ft.Text) and c.value
                                and "保存成功" in c.value for c in controls(self.app)))
            # 覆盖键只允许契约 §3.2.0 的 snake_case
            overrides = env.calls["run"][-1]["overrides"]
            allowed = {"title", "bullets", "description", "search_terms", "brand",
                       "manufacturer", "sku", "standard_price", "sale_price", "quantity",
                       "category_id", "category_path", "marketplace_id", "currency",
                       "fulfillment", "handling_time", "shipping_template",
                       "attributes"}
            self.assertTrue(set(overrides).issubset(allowed),
                            f"出现契约外 overrides 键：{set(overrides) - allowed}")
            # 后端已就绪时再对齐一次：UI 发的键必须被 amazon_payload 接受
            try:
                from aeclone.services.amazon_payload import OVERRIDE_KEYS
            except Exception:
                OVERRIDE_KEYS = None
            if OVERRIDE_KEYS:
                self.assertTrue(set(overrides).issubset(set(OVERRIDE_KEYS)),
                                f"后端不认这些 overrides 键："
                                f"{set(overrides) - set(OVERRIDE_KEYS)}")
            self.assertEqual(overrides["category_id"], CATEGORY_ID)
            self.assertEqual(overrides["marketplace_id"], MARKETPLACE)
            self.assertEqual(overrides["bullets"][0], "FEATURE one")

    # ---------- 发布结果 / 必填属性 (task-9) ----------
    def test_review_notice_does_not_hide_actionable_failure_in_editor(self):
        self.app._switch(13)
        self._load_product()
        error = "code:100521,正在审查;code:90220,属性:country_of_origin"
        with fake_backend({"verify": {"state": "publishFail", "err_msg": error}}), sync_threads():
            click(find_button(self.app, "查询发布结果"))
        self.assertTrue(any(isinstance(c, ft.Text) and c.value == "发布失败"
                            and c.color == ui.DANGER_C for c in controls(self.app)))

    def test_old_publish_lookup_cannot_restore_attributes_after_switching_product(self):
        self.app.cfg.set("amazon_shop_id", "9408005")
        self.app.cfg.set("amazon_site", MARKETPLACE)
        with fake_backend() as env, sync_threads():
            self.app._switch(16)
            picker = find_control(self.app, ft.TextField, lambda c: c.label == "或输入商品 ID")
            picker.value = PRODUCT_ID
            click(find_button(self.app, "按 ID 载入"))
            def lookup(*args, **kwargs):
                click(find_button(self.app, "换一件商品"))
                picker.value = "second"
                click(find_button(self.app, "按 ID 载入"))
                return {"state": "publishFail", "err_msg": "code:90220,属性:country_of_origin"}
            env.pipeline.verify_publish = lookup
            click(find_button(self.app, "查询发布结果"))
        self.assertEqual(self.app._amz_form["id"], "second")
        self.assertNotIn("country_of_origin", self.app._amz_missing_attrs)

    def _set_op2(self):
        op_dd = find_control(self.app, ft.Dropdown,
                             lambda c: getattr(c, "label", "") == "保存动作")
        self.assertIsNotNone(op_dd)
        op_dd.value = "2"
        change(op_dd)

    def test_publish_failure_shows_full_reason_and_missing_attrs(self):
        """op=2 发布失败：展示完整 errMsg + parse_required_attrs 的缺失属性。"""
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            self._set_op2()
            with fake_backend(), sync_threads():
                click(find_button(self.app, "保存到 Amazon"))
                self.assertIsNotNone(self.app.page.dialog, "保存没有二次确认")
                click(self.app.page.dialog.actions[1])
        joined = " ".join(texts(self.app)) + " " + " ".join(
            str(getattr(c, "value", "")) for c in controls(self.app)
            if isinstance(c, ft.TextField))
        self.assertIn("发布失败", joined, "缺少发布失败徽标")
        self.assertIn("90220", joined, "缺少完整失败原因 errMsg")
        for attr in PUBLISH_REQUIRED:
            self.assertIn(attr, joined, f"缺少解析出的必填属性：{attr}")
        # 徽标颜色：发布失败=红
        self.assertTrue(any(isinstance(c, ft.Text) and c.value == "发布失败"
                            and c.color == ui.DANGER_C for c in controls(self.app)))

    def test_publish_success_badge_shows_asin(self):
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            self._set_op2()
            behavior = {"run_publish_state": "online", "run_publish_error": "",
                        "run_publish_asin": "B0TEST12345", "run_required_attrs": []}
            with fake_backend(behavior), sync_threads():
                click(find_button(self.app, "保存到 Amazon"))
                click(self.app.page.dialog.actions[1])
        joined = " ".join(texts(self.app))
        self.assertIn("发布成功", joined)
        self.assertIn("B0TEST12345", joined)

    def test_review_pending_is_not_reported_as_failure(self):
        """publishFail + 100521/审核中 → 平台审核中（黄，非软件失败）。"""
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            self._set_op2()
            behavior = {"run_publish_state": "publishFail",
                        "run_publish_error": "发布失败:code:100521,商品正在审核中",
                        "run_required_attrs": []}
            with fake_backend(behavior), sync_threads():
                click(find_button(self.app, "保存到 Amazon"))
                click(self.app.page.dialog.actions[1])
        joined = " ".join(texts(self.app))
        self.assertIn("平台审核中", joined)
        self.assertIn("非软件失败", joined)

    def test_query_publish_result_and_dynamic_extra_attributes(self):
        """查询发布结果：展示状态/原因，并把 errMsg 里的其它必填属性动态追加输入框。"""
        self.app._switch(13)
        self._load_product()
        verify_state = {"state": "publishFail", "err_msg": EXTRA_ERR, "asin": "",
                        "title": "Fake Backhoe Title", "counts": {},
                        "required_attrs": ["item_depth_width_height",
                                           "country_of_origin", "list_price"],
                        "attribute_notes": {"item_depth_width_height": "30x20x15",
                                            "country_of_origin": "CN"}}
        with fake_backend({"verify": verify_state}), sync_threads():
            click(find_button(self.app, "查询发布结果"))
        joined = " ".join(texts(self.app))
        self.assertIn("发布失败", joined)
        self.assertIn("item_depth_width_height", joined)
        self.assertIn("country_of_origin", joined)
        # 动态属性输入框已追加，且预填后端 autofill/notes 值
        now_labels = labels(self.app)
        for attr in ("item_depth_width_height", "country_of_origin", "list_price"):
            self.assertIn(attr, now_labels, f"缺少动态属性输入框：{attr}")
        dyn = find_control(self.app, ft.TextField,
                           lambda c: getattr(c, "label", "") == "item_depth_width_height")
        self.assertEqual(dyn.value, "30x20x15")
        coo = find_control(self.app, ft.TextField,
                           lambda c: getattr(c, "label", "") == "country_of_origin")
        self.assertEqual(coo.value, "CN")
        # 手工修正后保存 → overrides["attributes"] 带动态属性
        dyn.value = "31x21x16"
        change(dyn)
        # 「自动补齐必填属性」按 errMsg 解析出的缺失属性向后端请求补齐
        with fake_backend() as afenv, sync_threads():
            click(find_button(self.app, "自动补齐必填属性"))
        self.assertEqual(sorted(afenv.calls["autofill"][-1]["required"]),
                         sorted(["item_depth_width_height", "country_of_origin",
                                 "list_price"]))
        self._real_mode()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "保存到 Amazon"))
            click(self.app.page.dialog.actions[1])
        attrs = env.calls["run"][-1]["overrides"]["attributes"]
        self.assertEqual(attrs.get("item_depth_width_height"), "31x21x16")
        self.assertEqual(attrs.get("country_of_origin"), "CN")

    def test_autofill_attributes_prefills_and_goes_into_overrides(self):
        self.app._switch(13)
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "检测连接"))
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
            click([b for b in buttons(self.app)
                   if str(getattr(b, "text", "")) == "编辑"][0])
        self.assertTrue(env.calls["autofill"], "载入商品应调用 autofill_attributes")
        for key, value in AUTOFILL.items():
            tf = find_control(self.app, ft.TextField,
                              lambda c, _k=key: _k in str(getattr(c, "label", "")))
            self.assertIsNotNone(tf, f"缺少属性输入框：{key}")
            self.assertEqual(tf.value, value, f"属性 {key} 未预填 autofill 值")
        self._real_mode()
        with fake_backend() as env2, sync_threads():
            click(find_button(self.app, "保存到 Amazon"))
            click(self.app.page.dialog.actions[1])
        attrs = env2.calls["run"][-1]["overrides"]["attributes"]
        for key, value in AUTOFILL.items():
            self.assertEqual(attrs.get(key), value)

    def test_list_publish_states_show_err_msg_summary(self):
        self.app._switch(13)
        # 新增状态按钮：发布中 / 发布失败
        for label in ("发布中", "发布失败"):
            self.assertTrue(any(str(getattr(b, "text", "")).startswith(label)
                                for b in buttons(self.app)), label)
        with fake_backend(), sync_threads():
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("发布失败")))
        joined = " ".join(texts(self.app))
        self.assertIn("90220", joined, "发布失败行未显示 err_msg 摘要")
        with fake_backend(), sync_threads():
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("发布中")))
        # 不崩溃且状态可切换
        self.assertTrue(any(str(getattr(b, "text", "")).startswith("发布中")
                            for b in buttons(self.app)))

    def test_auto_retry_publish_uses_op2_and_attributes(self):
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(13)
            self._load_product()
            with fake_backend() as env, sync_threads():
                click(find_button(self.app, "自动补齐并重试发布"))
                self.assertIsNotNone(self.app.page.dialog, "重试发布缺少二次确认")
                self.assertIn("重试发布", str(self.app.page.dialog.title.value))
                click(self.app.page.dialog.actions[1])
            self.assertTrue(env.calls["autofill"], "重试发布前应调用 autofill_attributes")
            self.assertTrue(env.calls["run"], "重试发布未调用 run_amazon_product")
            self.assertEqual(env.calls["run"][-1]["op"], "2")
            self.assertFalse(env.calls["run"][-1]["dry_run"])
            self.assertTrue(env.calls["run"][-1]["confirm_write"])
            self.assertIn("attributes", env.calls["run"][-1]["overrides"])
        joined = " ".join(texts(self.app))
        self.assertIn("重试发布结果", joined)

    def test_auto_retry_publish_rejected_in_guard_env(self):
        self.app._switch(13)
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "自动补齐并重试发布"))
        self.assertIsNone(self.app.page.dialog, "安全护栏环境不应弹真实发布确认")
        self.assertIn("禁止写入平台数据", snack(self.app))
        self.assertNotIn("预演", snack(self.app))
        self.assertEqual(env.calls["run"], [], "安全护栏环境不能真实发布")

    def _auto_publish_click(self, env_patch=True):
        click(find_button(self.app, "自动发布（自动修复）"))
        self.assertIsNotNone(self.app.page.dialog, "自动发布缺少二次确认")
        self.assertIn("自动发布", str(self.app.page.dialog.title.value))
        click(self.app.page.dialog.actions[1])

    def test_auto_publish_confirm_params_and_result(self):
        self.app._switch(13)
        self._load_product()
        self._real_mode()
        self._set_price_qty()
        with fake_auto() as autoenv, sync_threads():
            self._auto_publish_click()
        self.assertEqual(len(autoenv.calls["auto"]), 1)
        call = autoenv.calls["auto"][0]
        self.assertEqual(call["product_id"], PRODUCT_ID)
        self.assertEqual(call["op"], "2")
        self.assertEqual(call["max_rounds"], 3)
        # 等待秒数默认取 cfg.amazon_auto_wait / amazon_publish_wait / 90
        expected_wait = int(str(self.app.cfg.amazon_auto_wait
                                or self.app.cfg.amazon_publish_wait or 90))
        self.assertEqual(call["wait_seconds"], expected_wait)
        self.assertTrue(call["auto_fix_attrs"])
        self.assertTrue(call["auto_fix_title"])
        self.assertTrue(call["auto_fix_description"])
        self.assertFalse(call["auto_ai_fix"])
        self.assertIn("attributes", call["overrides"])
        joined = " ".join(texts(self.app))
        for expected in ("自动发布结果", "发布成功", "B0AUTO123", "轮数：2",
                         "自动修复记录", "第 1 轮", "第 2 轮"):
            self.assertIn(expected, joined, f"自动发布结果缺少：{expected}")

    def test_auto_publish_options_persist_and_are_passed(self):
        self.app._switch(13)
        self._load_product()
        self._real_mode()
        self._set_price_qty()
        op_dd = find_control(self.app, ft.Dropdown,
                             lambda c: getattr(c, "label", "") == "自动发布动作")
        rounds_dd = find_control(self.app, ft.Dropdown,
                                 lambda c: getattr(c, "label", "") == "最多自动修复轮数")
        wait_dd = find_control(self.app, ft.Dropdown,
                               lambda c: getattr(c, "label", "") == "发布后等待（秒）")
        op_dd.value = "3"
        change(op_dd)
        rounds_dd.value = "5"
        change(rounds_dd)
        wait_dd.value = "30"
        change(wait_dd)
        self.assertEqual(self.app.cfg.amazon_auto_op, "3")
        self.assertEqual(str(self.app.cfg.amazon_auto_rounds), "5")
        self.assertEqual(str(self.app.cfg.amazon_publish_wait), "30")
        switches = {str(getattr(c, "label", "")): c for c in controls(self.app)
                    if isinstance(c, ft.Switch)}
        ai_sw = switches["自动 AI 重写违规文案（付费）"]
        ai_sw.value = True
        change(ai_sw)
        self.assertTrue(self.app.cfg.amazon_auto_ai_fix)
        with fake_auto() as autoenv, sync_threads():
            self._auto_publish_click()
        call = autoenv.calls["auto"][0]
        self.assertEqual(call["op"], "3")
        self.assertEqual(call["max_rounds"], 5)
        self.assertEqual(call["wait_seconds"], 30)
        self.assertTrue(call["auto_ai_fix"])

    def test_auto_publish_failure_result_shows_reason_and_suggestion(self):
        self.app._switch(13)
        self._load_product()
        self._real_mode()
        self._set_price_qty()
        fail = FakeAutoResult(
            ok=False, state="publishFail", rounds=3, asin="",
            error="发布失败:code:90220,属性:model_number;属性:power_source_type",
            required=["model_number", "power_source_type"],
            fixes=["补齐 model_number（失败）"],
            steps=[{"round": 3, "op": "2", "publish_state": "publishFail",
                    "err": "code:90220 属性:model_number", "fixes": ["自动补齐失败"]}])
        with fake_auto(fail), sync_threads():
            self._auto_publish_click()
        joined = " ".join(texts(self.app)) + " " + " ".join(
            str(getattr(c, "value", "")) for c in controls(self.app)
            if isinstance(c, ft.TextField))
        for expected in ("自动发布结果", "发布失败", "90220", "model_number",
                         "建议：", "轮数：3"):
            self.assertIn(expected, joined, f"失败结果缺少：{expected}")

    def test_auto_publish_blocked_without_price_or_quantity(self):
        self.app._switch(13)
        self._load_product()
        self._real_mode()
        self._set_price_qty(qty="")
        with fake_auto() as autoenv, sync_threads():
            click(find_button(self.app, "自动发布（自动修复）"))
        self.assertIsNone(self.app.page.dialog, "缺少库存时不应弹发布确认")
        self.assertIn("库存", snack(self.app))
        self.assertEqual(autoenv.calls["auto"], [])
        self._set_price_qty(price="", qty="5")
        with fake_auto() as autoenv2, sync_threads():
            click(find_button(self.app, "自动发布（自动修复）"))
        self.assertIsNone(self.app.page.dialog, "缺少价格时不应弹发布确认")
        self.assertIn("标准价", snack(self.app))
        self.assertEqual(autoenv2.calls["auto"], [])

    def test_auto_publish_guard_env_rejects_and_has_no_preview_words(self):
        self.app._switch(13)
        self._load_product()
        self._set_price_qty()
        with fake_auto() as autoenv, sync_threads():
            click(find_button(self.app, "自动发布（自动修复）"))
        self.assertIsNone(self.app.page.dialog, "安全护栏环境不应弹自动发布确认")
        self.assertIn("禁止写入平台数据", snack(self.app))
        for banned in ("预演", "AURORA_", "检查模式", "测试"):
            self.assertNotIn(banned, snack(self.app))
        self.assertEqual(autoenv.calls["auto"], [], "安全护栏环境不能自动发布")

    def test_auto_publish_backend_missing_is_friendly(self):
        self.app._switch(13)
        self._load_product()
        self._real_mode()
        self._set_price_qty()
        with fake_auto(missing=True), sync_threads():
            self._auto_publish_click()
        joined = " ".join(texts(self.app))
        self.assertIn("自动发布失败", joined)
        self.assertIn("未就绪", joined)

    # ---------- 守卫 ----------
    def test_overview_opens_run_page(self):
        self.app._switch(0)
        labels = [str(getattr(c, "text", "")) for c in walk(self.app.content.controls)]
        self.assertIn("去运行与结果", labels)
        self.assertNotIn("选择商品", labels)
        button = next(c for c in walk(self.app.content.controls) if getattr(c, "text", "") == "去运行与结果")
        button.on_click(SimpleNamespace(control=button))
        self.assertEqual(self.app._page_idx, 21)
        self.assertTrue(any(getattr(c, "label", "") == "采集箱关键词（可选）" for c in walk(self.app.content.controls)))

    def test_amazon_platform_guards_block_smt_pipeline(self):
        self.app._switch(0)
        self.app._running = self.app._stop_requested = False
        with patch("aeclone.ui.collect_workflow.launch") as launch, patch.object(self.app, "_run_pipeline") as pipeline:
            self.app._toggle_run()
            self.app._start()
            self.app._on_key(SimpleNamespace(key="r", ctrl=True, meta=False))
            self.assertEqual(launch.call_count, 2)
            self.assertTrue(all(c.args == (self.app, "amazon") for c in launch.call_args_list))
            pipeline.assert_not_called()
        with patch.object(self.app, "_log") as log:
            self.app._run_pipeline()
            self.assertTrue(any(call.args[1] == "ERROR" for call in log.call_args_list))

    # ---------- 后端未就绪 / 空数据 ----------
    def test_page_and_callbacks_survive_missing_backend(self):
        """后端模块不存在时：页面能渲染，点按钮只提示「后端未就绪」，不抛异常。"""
        self.app._switch(13)
        with patch.object(ui.App, "_amazon_backend",
                          side_effect=ImportError("no amazon modules")):
            with sync_threads():
                click(find_button(self.app, "检测连接"))
            conn = [c.value for c in controls(self.app)
                    if isinstance(c, ft.Text) and c.value
                    and c.value.startswith("检测连接失败")]
            self.assertTrue(conn and "后端未就绪" in conn[0],
                            f"缺少后端未就绪提示：{conn}")
            # 所有按钮/输入回调逐个触发，不得抛出异常（回调内部必须 try/except）
            errors = []
            with sync_threads():
                for ctrl in controls(self.app):
                    for event_name in ("on_click", "on_change", "on_submit"):
                        callback = getattr(ctrl, event_name, None)
                        if not callable(callback):
                            continue
                        try:
                            if event_name == "on_click":
                                click(ctrl)
                            else:
                                change(ctrl)
                        except Exception as ex:  # noqa: BLE001
                            errors.append(f"{type(ctrl).__name__}.{event_name}: "
                                          f"{type(ex).__name__}: {ex}")
            self.assertEqual(errors, [], "控件回调抛出异常：\n" + "\n".join(errors[:8]))

    def test_empty_product_list_has_actionable_empty_state(self):
        """采集箱空列表必须有空状态提示（不是空白/无反馈）。"""
        self.app._switch(13)
        make_client, payload, pipeline, _client, _calls, _beh = make_backend()

        class EmptyClient(FakeClient):
            def list_products(self, state="draft", page=1, page_size=20,
                              shop_id="-1", keyword=""):
                return {"items": [], "total": 0, "page_no": 1, "total_page": 1,
                        "drafts": 0, "wait_publish": 0, "publish_fail": 0, "markets": {}}

        make_client = lambda cfg: EmptyClient()
        with patch.object(ui.App, "_amazon_backend",
                          lambda self: (make_client, payload, pipeline)):
            with sync_threads():
                click(next(b for b in buttons(self.app)
                           if str(getattr(b, "text", "")).startswith("采集箱")))
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "该状态没有商品" in c.value for c in controls(self.app)),
                        "空列表缺少空状态提示")


if __name__ == "__main__":
    unittest.main(verbosity=2)
