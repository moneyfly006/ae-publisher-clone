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

    def prepare_save(pid, cfg, overrides=None):
        calls["prepare"].append({"pid": pid, "overrides": overrides})
        return ({"site": SITE_CODE}, {"title": "Fake Title"}, [], ["提醒"])

    def run_amazon_product(pid, cfg, overrides=None, dry_run=True, use_ai=False,
                           op="1", confirm_write=False):
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
                 "search_terms": "ai keyword"}.get(field, "")
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
    def test_amazon_navigation_is_slim_and_has_page_13(self):
        self.assertEqual({p.index for p in AMAZON.pages}, {0, 13, 10, 11})
        self.assertEqual([p.index for p in AMAZON.pages], [0, 13, 10, 11])
        self.assertIn(13, {d.index for d in DESTINATIONS})
        dest = next(d for d in DESTINATIONS if d.index == 13)
        self.assertEqual(dest.label, "Amazon 刊登")
        # 搜索别名：契约 §4.1
        page = get_platform("amazon").page(13)
        for word in ("采集箱", "站点", "五点", "卖点", "bullet", "类目", "价格", "保存",
                     "发布", "ai", "翻译"):
            self.assertTrue(page.matches(word), f"13 号页搜索不到：{word}")
        # 侧栏只有 4 项
        nav = [b.content.controls[1].value for _, b in self.app.nav_buttons]
        self.assertEqual(nav, ["概览", "Amazon 刊登", "任务与日志", "设置"])
        # 侧栏「启动任务」在 Amazon 平台文案改为「去 Amazon 刊登」
        self.app._sync_run_button()
        self.assertEqual(self.app.btn_run_text.value, "去 Amazon 刊登")
        # 导航搜索别名：五点 / 采集箱 都命中 13
        for query in ("五点", "采集箱", "bullet"):
            self.app._filter_navigation(query)
            self.assertEqual([i for i, b in self.app.nav_buttons if b.visible], [13],
                             f"搜索 {query} 未命中 Amazon 刊登页")
        self.app._filter_navigation("")

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
                                    and "⑥ 发布前检查与保存" in c.value for c in rendered))
        ui.apply_theme("dark")

    def test_page_13_renders_with_all_six_cards_and_key_controls(self):
        self.app._switch(13)
        self.assertEqual(self.app._page_idx, 13)
        self.assertEqual(self.app._current, 13)
        self.assertIn("Amazon 刊登", self.app.breadcrumb.value)
        all_text = texts(self.app)
        for title in ("① 连接与店铺", "② 采集箱商品", "③ 站点与类目",
                      "④ 标题与卖点（AI）", "⑤ 价格 / 库存 / 物流 / 品牌 / SKU",
                      "⑥ 发布前检查与保存"):
            self.assertIn(title, all_text)
        # 关键控件（契约 §4.3）
        self.assertIn("店铺", labels(self.app))
        self.assertIn("站点", labels(self.app))
        for i in range(1, 6):
            self.assertIn(f"卖点 {i}", labels(self.app))
        # 默认显示 5 行卖点；载入多卖点商品会扩展到最多 10 行（见 10 条回归用例）
        self.assertEqual(len([l for l in labels(self.app) if l.startswith("卖点 ")]), 5)
        self.assertIn("标题", labels(self.app))
        self.assertIn("标准价", labels(self.app))
        self.assertIn("Search Terms（≤250 字节）", labels(self.app))
        # 发布必填属性输入框（task-9）
        for attr_label in ("型号（model_number）", "型号名称（model_name）",
                           "零件编号（part_number）",
                           "制造商联系信息（manufacturer_contact_information）"):
            self.assertIn(attr_label, labels(self.app))
        for btn in ("读取本机 Cookie", "检测连接", "刷新", "上一页", "下一页",
                    "加载类目", "校验路径", "AI/平台推荐产品类型",
                    "AI 生成标题", "AI 生成五点", "＋添加卖点", "AI 生成描述",
                    "AI 生成关键词",
                    "AI 一键生成全部（付费）", "刷新运费模板", "查看 productAttr（只读）",
                    "发布前检查（不保存）", "查询发布结果", "自动补齐并重试发布",
                    "自动补齐必填属性"):
            find_button(self.app, btn)
        # 检查模式（AURORA_TEST=1）下保存按钮明示
        self.assertTrue(any("检查模式" in str(b.text) for b in buttons(self.app)))
        # 状态按钮：采集箱/待发布/发布中/发布失败/在线
        for label in ("采集箱", "待发布", "发布中", "发布失败", "在线"):
            self.assertTrue(any(str(getattr(b, "text", "")).startswith(label)
                                for b in buttons(self.app)), label)

    def test_amazon_page_has_no_dev_wording(self):
        """正式版：Amazon 13 页可见文本不得出现测试/开发措辞。"""
        self.app._switch(13)
        page_texts = " ".join(str(t) for t in texts(self.app))
        page_texts += " " + " ".join(str(getattr(b, "text", "")) for b in buttons(self.app))
        for banned in ("AURORA_", "预演", "测试连接", "本地验收", "AURORA_TEST",
                       "AURORA_NO_SAVE"):
            self.assertNotIn(banned, page_texts, f"Amazon 页仍含开发措辞：{banned}")
        for kept in ("检测连接", "发布前检查", "检查模式", "发布前检查与保存"):
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
        bullet1 = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "卖点 1")
        self.assertEqual(bullet1.value, "FEATURE one")
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
        bullet1 = find_control(self.app, ft.TextField, lambda c: getattr(c, "label", "") == "卖点 1")
        self.assertEqual(bullet1.value, "AI 卖点 1")
        desc = find_control(self.app, ft.TextField, lambda c: "产品描述" in str(getattr(c, "label", "")))
        self.assertIn("AI 描述", desc.value)

    def test_ten_bullets_are_preserved_on_save(self):
        """P0 回归：商品有 10 条卖点时，UI 显示 10 行且保存 overrides 保留 10 条。

        真实采集箱商品多为 10 条卖点（schema maxItems=10）；只显示 5 行会在保存时
        把第 6~10 条写丢，落到平台。
        """
        self.app._switch(13)
        self._load_product()
        bullet_fields = [c for c in controls(self.app)
                         if isinstance(c, ft.TextField)
                         and str(getattr(c, "label", "")).startswith("卖点 ")]
        self.assertEqual(len(bullet_fields), 10,
                         "载入 10 条卖点必须显示 10 行（不能截断成 5）")
        self.assertEqual(bullet_fields[9].value, "FEATURE ten")
        # 计数：n/700，>500 黄字，>700 红字
        counters = [c for c in controls(self.app)
                    if isinstance(c, ft.Text) and c.value and "/700" in str(c.value)]
        self.assertEqual(len(counters), 10, "每条卖点应有 n/700 计数")
        bullet_fields[0].value = "x" * 501
        change(bullet_fields[0])
        self.assertTrue(any(isinstance(c, ft.Text) and c.value == "501/700"
                            and c.color == ui.WARN_C for c in controls(self.app)))
        bullet_fields[0].value = "x" * 701
        change(bullet_fields[0])
        self.assertTrue(any(isinstance(c, ft.Text) and c.value == "701/700"
                            and c.color == ui.DANGER_C for c in controls(self.app)))
        bullet_fields[0].value = "FEATURE one"
        change(bullet_fields[0])
        # 发布前检查 overrides 必须带全部 10 条（保存使用同一份 overrides 组装逻辑）
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "发布前检查（不保存）"))
        overrides = env.calls["run"][-1]["overrides"]
        self.assertEqual(len(overrides["bullets"]), 10, overrides["bullets"])
        self.assertEqual(overrides["bullets"][0], "FEATURE one")
        self.assertEqual(overrides["bullets"][-1], "FEATURE ten")
        # 到 10 行上限后「＋添加卖点」给出明确提示
        click(find_button(self.app, "＋添加卖点"))
        self.assertIn("最多 10 行", snack(self.app))

    # ---------- ⑥ 预演 / 保存 ----------
    def _load_product(self):
        with fake_backend(), sync_threads():
            click(find_button(self.app, "检测连接"))
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
            click([b for b in buttons(self.app)
                   if str(getattr(b, "text", "")) == "编辑"][0])

    def test_preview_never_writes_and_save_is_rejected_in_preview_mode(self):
        self.app._switch(13)
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "发布前检查（不保存）"))
        self.assertTrue(env.calls["run"], "发布前检查未调用 run_amazon_product")
        self.assertTrue(env.calls["run"][-1]["dry_run"], "发布前检查必须 dry_run=True")
        self.assertEqual(env.calls["run"][-1]["op"], "1")
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "发布前检查" in c.value for c in controls(self.app)))
        # AURORA_TEST/AURORA_NO_SAVE 下保存按钮拒绝，且不产生 dry_run=False 调用
        with fake_backend() as env2, sync_threads():
            # 检查模式（AURORA_TEST）下按钮文案是「保存（检查模式）」；正式环境是「保存到 Amazon」
            save_btn = find_button(self.app, "保存（检查模式）")
            click(save_btn)
        self.assertEqual(snack(self.app), "检查模式：已拒绝真实保存，请用「发布前检查」校验")
        self.assertEqual(env2.calls["run"], [], "检查模式下不能有任何真实保存调用")
        self.assertEqual(env2.calls["prepare"], [])

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
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "发布前检查（不保存）"))
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
        with fake_backend() as env2, sync_threads():
            click(find_button(self.app, "发布前检查（不保存）"))
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

    def test_auto_retry_publish_rejected_in_check_mode(self):
        self.app._switch(13)
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "自动补齐并重试发布"))
        self.assertIsNone(self.app.page.dialog, "检查模式不应弹真实发布确认")
        self.assertIn("检查模式", snack(self.app))
        self.assertEqual(env.calls["run"], [], "检查模式不能真实发布")

    # ---------- 守卫 ----------
    def test_overview_uses_amazon_counts_and_jumps_to_13(self):
        self.app._switch(0)
        self.assertEqual({k for k in self.app.ov_tiles}, {"draft", "offline", "online",
                                                          "publishFail"})
        # 计数刷新后数字块更新（只读缓存）
        self.app.amazon_counts = {"draft": 16, "offline": 5, "online": 142,
                                  "publishFail": 21}
        self.app._update_amazon_tiles()
        self.assertEqual({k: v.value for k, v in self.app.ov_tiles.items()},
                         {"draft": "16", "offline": "5", "online": "142",
                          "publishFail": "21"})
        # 点「去 Amazon 刊登」→ 13
        click(find_button(self.app, "去 Amazon 刊登"))
        self.assertEqual(self.app._page_idx, 13)
        # 工作流入口都指向 13 / 10（不再出现 1、3 号 SMT 页）
        self.app._switch(0)
        workflow_targets = []
        for ctrl in buttons(self.app):
            if str(getattr(ctrl, "text", "")).startswith(("01  ", "02  ", "03  ", "04  ")):
                workflow_targets.append(str(getattr(ctrl, "text", "")))
        self.assertTrue(any("Amazon 刊登" in t for t in workflow_targets))
        # 8 号发布队列在 Amazon 平台不可达 → 回概览
        self.app._switch(8)
        self.assertEqual(self.app._page_idx, 0)

    def test_amazon_platform_guards_block_smt_pipeline(self):
        self.app._switch(0)
        self.app._running = self.app._stop_requested = False
        with patch.object(self.app, "_run_pipeline") as pipeline, \
                patch.object(self.app, "_log") as log:
            self.app._toggle_run()
            self.assertEqual(self.app._page_idx, 13)
            self.assertTrue(any(call.args and call.args[1] == "WARN"
                                for call in log.call_args_list), "守卫必须写 WARN 日志")
            pipeline.assert_not_called()
            self.app._switch(0)
            self.app._start()
            self.assertEqual(self.app._page_idx, 13)
            pipeline.assert_not_called()
            self.app._switch(0)
            self.app._on_key(SimpleNamespace(key="r", ctrl=True, meta=False))
            self.assertEqual(self.app._page_idx, 13)
            pipeline.assert_not_called()
        # 服务层兜底：即使被直接调用也不跑
        with patch.object(self.app, "_log") as log:
            self.app._run_pipeline()
            self.assertTrue(any(call.args and call.args[1] == "ERROR"
                                for call in log.call_args_list))
        # SMT 写操作守卫（发布队列 / 重试发布 / 定时发布）
        with patch.object(self.app, "_log") as log:
            done = MagicMock()
            self.app._publish_one("123", on_done=done)
            done.assert_called_once()
            self.app._retry_publish_failed()
            self.app._run_scheduled(SimpleNamespace(product_id="1", title="t"))
            self.assertEqual(self.app._page_idx, 13, "定时器在 Amazon 平台应跳 13 号页")
            self.assertTrue(log.called, "SMT 写操作守卫必须写日志")

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
