"""TikTok Shop 刊登 14 号页 UI 离线验收（契约 §4 / §6.3）。

只跑沙箱：不联网、不读真实 Cookie、不写任何平台数据。
后端模块（services.tiktok_publish / tiktok_payload / tiktok_pipeline）尚未就绪时，
前端必须仍能渲染页面并给出「后端未就绪」提示；本文件用 mock 后端验证可用控件，
另外单独验证「后端缺失也不崩溃」。

运行：AURORA_TEST=1 .venv/bin/python tests/test_tiktok_ui_page.py
（脚本也会自动 setdefault，单独直接跑同样可复现；显式清空 env 的用例自行 patch。）
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import threading
import unittest
from unittest.mock import patch

# 离线验收默认测试保护模式：必须在 import aeclone.ui.app / 构建 App 之前生效，
# 避免测试误触真实写路径。
os.environ.setdefault("AURORA_TEST", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

from aeclone.platforms import PLATFORM_CAPS, TIKTOK, cap, get_platform  # noqa: E402
from aeclone.ui import app as ui  # noqa: E402
from aeclone.ui.navigation import DESTINATIONS  # noqa: E402

PRODUCT_ID = "5764600000000012345"
SITE = "GB"
CATEGORY_ID = "605196"
CATEGORY_PATH = "601450/601460/605196"
MAIN_IMAGE = "https://example.invalid/main.jpg"


# ---------- mock 后端（契约 §3 签名） ----------
class FakeClient:
    def __init__(self, shops=None, sites=None):
        self._shops = shops if shops is not None else [
            {"id": "9001", "name": "Aurora TikTok Shop", "platform": "tiktok",
             "marketplace_id": "GB", "is_tiktok": True},
        ]
        self._sites = sites
        self.saved = []

    def check_login(self):
        return True

    def shops(self):
        return list(self._shops)

    def sites(self, shop_id=""):
        if self._sites is not None:
            return [dict(x) for x in self._sites]
        return [dict(x) for x in ui.TIKTOK_SITE_META.values()]

    def site_limits(self, site=""):
        return dict(ui.TIKTOK_DEFAULT_LIMITS)

    def list_products(self, state="draft", page=1, page_size=20, shop_id="-1",
                      keyword="", site=""):
        return {
            "items": [{
                "id": PRODUCT_ID, "idStr": PRODUCT_ID,
                "productName": "Fake TikTok Product Title With Enough Length",
                "imgUrl": MAIN_IMAGE, "site": SITE,
                "categoryId": CATEGORY_ID, "categoryName": "Phone Cases",
                "dxmState": state, "updateTime": "2026-11-22 10:00:00",
                "price": "12.50", "quantity": "8",
            }],
            "total": 1, "page_no": 1, "total_page": 1,
            "drafts": 1, "wait_publish": 2, "publish_fail": 3,
        }

    def counts(self, shop_id="-1"):
        return {"draftNum": 1, "publishingTiming": 0, "waitPublish": 2,
                "publishFail": 3, "publishing": 0}

    def product_detail(self, product_id):
        return {
            "product": {
                "id": str(product_id), "idStr": str(product_id),
                "productName": "Fake TikTok Product Title With Enough Length",
                "description": "<p>Old description</p>",
                "keyProductFeatures": ["Feature one", "Feature two"],
                "searchTerms": ["phone case", "shockproof"],
                "mainImage": f"{MAIN_IMAGE}|https://example.invalid/second.jpg",
                "videoId": "",
                "sourceUrl": "https://example.invalid/source",
                "productType": 1, "price": "12.50", "quantity": 8,
                "packageWeight": "0.5", "packageLength": "10",
                "packageWidth": "10", "packageHeight": "10",
                "packageDimensionUnit": "CENTIMETER",
                "warehouseId": "", "brandId": "", "manufacturerId": "",
                "responsiblePersonId": "", "productAttributes": '{"color":"black"}',
                "categoryId": CATEGORY_ID, "categoryName": "Phone Cases",
                "categoryList": [{"categoryId": CATEGORY_ID,
                                  "categoryName": "Phone Cases"}],
                "site": SITE,
                "variationList": [{"sellerSku": "SKU-1", "sourcePrice": "12.50",
                                   "availableStock": 8}],
            },
            "site": SITE,
            "category": {"categoryId": CATEGORY_ID, "categoryName": "Phone Cases"},
        }

    def product_variants(self, product_id):
        return [{"sellerSku": "SKU-1"}]

    def categories(self, site, parent_id="0", shop_id="-1", cross_border=True):
        if str(parent_id) == "0":
            return [{"id": "n1", "categoryId": "601450", "categoryName": "Phones",
                     "isLeaf": "0", "nodePathId": "601450", "certifications": ""}]
        if str(parent_id) == "601450":
            return [{"id": "n2", "categoryId": "601460",
                     "categoryName": "Phone Accessories", "isLeaf": "0",
                     "nodePathId": "601450/601460", "certifications": ""}]
        return [{"id": "n3", "categoryId": CATEGORY_ID,
                 "categoryName": "Phone Cases", "isLeaf": "1",
                 "nodePathId": CATEGORY_PATH,
                 "certifications": json.dumps([
                     {"id": "c1", "name": "GPSR", "is_required": True}])}]

    def search_categories(self, keyword, site, shop_id="-1", cross_border=True):
        return [{"id": "n3", "categoryId": "700001", "categoryName": "Phone Holders",
                 "isLeaf": "1", "nodePathId": "601450/700001"}]

    def category_attributes(self, site, category_id, shop_id="-1",
                            cross_border=True, attr_type=3):
        return [{"attrId": "7", "attrName": "Color", "required": True,
                 "isMultipleSelected": True, "isCustomized": True,
                 "values": [{"id": "21", "name": "Black"}, {"id": "22", "name": "White"}]},
                {"attrId": "8", "attrName": "Material", "required": False, "values": []}]

    def category_history(self, site, shop_id="-1"):
        return [{"categoryId": CATEGORY_ID, "categoryName": "Phone Cases",
                 "nodePathId": CATEGORY_PATH, "num": 2}]

    def brands(self, site, category_id="", shop_id="-1"):
        return []

    def warehouses(self, shop_id=""):
        return [{"id": "wh1", "name": "GB 主仓"}]

    def check_banned_words(self, title="", description=""):
        return []

    def save_product(self, payload, op="1", save_mode=""):
        self.saved.append((payload, op, save_mode))
        return {"code": 0}


class FakeResult:
    def __init__(self, *, saved=False, preview=False, ok=True, error="", issues=None):
        self.product_id = PRODUCT_ID
        self.ok = ok
        self.error = error
        self.steps = [SimpleNamespace(name="连接/读详情", ok=True, detail="ok"),
                      SimpleNamespace(name="店铺+站点", ok=True, detail="GB/GBP"),
                      SimpleNamespace(name="必填/限额校验", ok=ok,
                                      detail="" if ok else "缺字段")]
        self.preview = preview
        self.saved = saved
        self.issues = list(issues or [])
        self.warnings = ["提醒：库存 0", "提醒：附加图 <3"]
        self.title = "Fake TikTok Product Title With Enough Length"
        self.description = "<p>AI desc</p>"
        self.key_features = ["f1", "f2", "f3", "f4", "f5"]
        self.search_terms = ["phone", "case"]
        self.site = SITE
        self.site_name = "英国"
        self.currency = "GBP"
        self.category_id = CATEGORY_ID
        self.category_name = "Phone Cases"
        self.op = "1"
        self.banned_words = []
        self.save_result = {"code": 0}
        self.ai_used = False
        self.limits = dict(ui.TIKTOK_DEFAULT_LIMITS)

    @property
    def completion_label(self):
        return "完成" if self.ok else "失败"

    def log(self):
        return ""


def make_backend(site_rows=None, shops=None):
    """返回契约三件套，并记录调用。"""
    client = FakeClient(shops=shops, sites=site_rows)
    calls = {"prepare": [], "run": [], "ai": [], "ai_all": []}

    def prepare_save(pid, cfg, overrides=None):
        calls["prepare"].append({"pid": pid, "overrides": overrides})
        return ({"product": {"id": pid}}, {"productName": "Fake Title"},
                [], ["提醒"])

    def run_tiktok_product(pid, cfg, overrides=None, dry_run=True, use_ai=False,
                           op="1", save_mode="", client=None, confirm_write=False):
        calls["run"].append({"pid": pid, "overrides": overrides, "dry_run": dry_run,
                             "use_ai": use_ai, "op": op, "save_mode": save_mode,
                             "confirm_write": confirm_write})
        return FakeResult(saved=not dry_run, preview=dry_run)

    def ai_generate_field(product, cfg, field, site="", count=5):
        calls["ai"].append({"field": field, "site": site, "count": count})
        value = {"title": "AI Generated TikTok Title That Is Long Enough",
                 "description": "<p>AI 描述</p>",
                 "key_features": ["AI 卖点 1", "AI 卖点 2", "AI 卖点 3",
                                  "AI 卖点 4", "AI 卖点 5"],
                 "search_terms": ["ai", "keyword"]}.get(field, "")
        return value, []

    def ai_generate_all(product, cfg, site="", count=5):
        calls["ai_all"].append({"site": site, "count": count})
        return ({"title": "AI Generated TikTok Title That Is Long Enough",
                 "description": "<p>AI 描述</p>",
                 "key_features": ["AI 卖点 1", "AI 卖点 2", "AI 卖点 3",
                                  "AI 卖点 4", "AI 卖点 5"],
                 "search_terms": ["ai", "keyword"]}, [])

    payload = SimpleNamespace(
        split_pipe=lambda raw: [x.strip() for x in str(raw or "").split("|")
                                if x.strip()],
        join_pipe=lambda items: "|".join([str(x) for x in (items or []) if str(x or "")]),
    )
    pipeline = SimpleNamespace(
        prepare_save=prepare_save,
        run_tiktok_product=run_tiktok_product,
        ai_generate_field=ai_generate_field,
        ai_generate_all=ai_generate_all,
        forced_preview=lambda: bool(os.environ.get("AURORA_NO_SAVE")
                                    or os.environ.get("AURORA_TEST")),
    )
    return (lambda cfg: client), payload, pipeline, client, calls


@contextmanager
def fake_backend(**kwargs):
    make_client, payload, pipeline, client, calls = make_backend(**kwargs)
    with patch.object(ui.App, "_tiktok_backend",
                      lambda self: (make_client, payload, pipeline)):
        yield SimpleNamespace(client=client, calls=calls, payload=payload,
                              pipeline=pipeline)


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


def find_dropdown(app, prefix):
    return find_control(app, ft.Dropdown,
                        lambda c: str(getattr(c, "label", "")).startswith(prefix))


def find_textfield(app, prefix):
    return find_control(app, ft.TextField,
                        lambda c: str(getattr(c, "label", "")).startswith(prefix))


def click(ctrl):
    ctrl.on_click(SimpleNamespace(control=ctrl,
                                  data=str(getattr(ctrl, "value", "") or "")))


def change(ctrl):
    ctrl.on_change(SimpleNamespace(control=ctrl,
                                   data=str(getattr(ctrl, "value", "") or "")))


def snack(app):
    try:
        return str(app.page.snack_bar.content.value or "")
    except Exception:
        return ""


class TikTokPageTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()
        self.app._switch_platform("tiktok")

    def _load_product(self):
        """测试连接 → 采集箱列表 → 编辑载入商品（全部 mock 后端）。"""
        with fake_backend(), sync_threads():
            click(find_button(self.app, "测试连接"))
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
            click([b for b in buttons(self.app)
                   if str(getattr(b, "text", "")) == "编辑"][0])

    # ---------- 平台注册 / 导航 ----------
    def test_tiktok_platform_is_implemented_with_split_pages(self):
        self.assertTrue(TIKTOK.implemented, "TikTok 必须 implemented=True")
        visible = [p.index for p in TIKTOK.pages if not p.group.startswith("_")]
        self.assertEqual(visible, [0, 22, 24, 25, 35, 26, 27, 28, 46, 10, 9, 11])
        self.assertEqual({p.index for p in get_platform("tiktok").pages},
                         set(visible) | {14})
        self.assertEqual(TIKTOK.groups(),
                         ["工作空间", "商品准备", "内容处理", "发布与运行", "工具与设置"])
        # 每个功能页一个独立导航条目（像速卖通那样），14 号完整页只做隐藏路由
        labels = {p.index: p.label for p in TIKTOK.pages}
        self.assertEqual(labels[22], "连接与店铺")
        self.assertNotIn(23, labels)
        self.assertEqual(labels[24], "类目与属性")
        self.assertEqual(labels[25], "价格与物流")
        self.assertEqual(labels[26], "AI 文案")
        self.assertEqual(labels[27], "图片与视频")
        self.assertEqual(labels[28], "保存与发布")
        self.assertTrue(TIKTOK.page(14).group.startswith("_"))
        self.assertIn(14, {d.index for d in DESTINATIONS})
        dest = next(d for d in DESTINATIONS if d.index == 14)
        self.assertEqual(dest.label, "TikTok 刊登")
        self.assertEqual(dest.icon, "PUBLISH")
        # 搜索别名：契约 §4.1（tiktok/采集箱/站点/类目/价格/库存/保存/发布/ai）
        page = get_platform("tiktok").page(14)
        for word in ("tiktok", "采集箱", "站点", "类目", "价格", "库存",
                     "保存", "发布", "ai"):
            self.assertTrue(page.matches(word), f"14 号页搜索不到：{word}")
        # 侧栏按功能拆成 10 项；启动按钮文案变为「去 TikTok 刊登」
        nav = [b.content.controls[1].value for _, b in self.app.nav_buttons]
        self.assertEqual(nav, ["概览", "连接与店铺", "类目与属性",
                               "价格与物流", "SKU 与库存", "AI 文案", "图片与视频",
                               "保存与发布", "任务队列", "任务与日志", "图片工作台", "设置"])
        self.app._sync_run_button()
        self.assertEqual(self.app.btn_run_text.value, "自动处理采集箱")
        # 导航搜索按功能命中对应页（不再只有一个 14 号页）
        expect = {"采集箱": [28], "类目": [24], "站点": [22, 24],
                  "价格": [25], "保存": [28]}
        for query, want in expect.items():
            self.app._filter_navigation(query)
            got = [i for i, b in self.app.nav_buttons if b.visible]
            self.assertEqual(got, want, f"搜索 {query} 命中 {got}，期望 {want}")
        self.app._filter_navigation("")
        # 契约 §4.1：TikTok 关闭 SMT 专属能力，icbu_listing 关闭
        self.assertIn("tiktok", PLATFORM_CAPS)
        for name in ("smt_wizard", "smt_store_selector", "smt_pipeline_actions",
                     "smt_pipeline_stats", "smt_run_stop",
                     "smt_extra_ai", "smt_prompt_library", "smt_detail_assembly",
                     "marketing_images"):
            self.assertFalse(cap("tiktok", name), f"TikTok 应关闭 cap：{name}")
        self.assertFalse(cap("tiktok", "icbu_listing"))

    def test_page_14_renders_in_dark_and_light_themes(self):
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            with self.subTest(theme=theme):
                self.app._switch(14)
                self.assertEqual(self.app._page_idx, 14)
                rendered = controls(self.app)
                self.assertGreater(len(rendered), 150, "14 号页控件树异常")
                self.assertIn("TikTok 刊登", self.app.breadcrumb.value)
                self.assertTrue(any(isinstance(c, ft.Text) and c.value
                                    and "诊断" == c.value for c in rendered))
        ui.apply_theme("dark")

    def test_page_14_has_tabbed_cards_and_key_controls(self):
        self.app._switch(14)
        self.assertEqual(self.app._current, 14)
        self.assertIn("TikTok 刊登", self.app.breadcrumb.value)
        all_text = texts(self.app)
        for title in ("连接与店铺 / 站点", "采集箱商品", "类目与属性",
                      "文案与 AI", "图片与视频", "价格与物流",
                      "保存与发布", "诊断"):
            self.assertIn(title, all_text)
        # 与速卖通页面一致的 workspace_tabs 分栏（5 栏）
        _tabs = next(c for c in controls(self.app) if isinstance(c, ft.Tabs))
        self.assertEqual([str(t.text) for t in _tabs.tabs],
                         ["连接与商品", "类目与属性", "文案与图片",
                          "价格与物流", "保存与诊断"])
        # 店铺与站点分开的两个下拉（契约 §4.3）
        self.assertTrue(find_dropdown(self.app, "店铺"))
        self.assertTrue(find_dropdown(self.app, "站点"))
        for label_prefix in ("标题", "描述", "主图 URL", "价格", "库存", "重量",
                             "搜索类目关键词", "保存动作"):
            self.assertIsNotNone(find_textfield(self.app, label_prefix)
                                 or find_dropdown(self.app, label_prefix),
                                 f"缺少控件：{label_prefix}")
        for btn in ("读取本机 Cookie", "测试连接", "刷新", "上一页", "下一页",
                    "加载类目", "搜索类目", "编辑类目属性",
                    "AI 生成标题", "AI 生成描述", "AI 生成五点", "AI 生成关键词",
                    "AI 一键生成全部（付费）", "AI 一键翻译（站点语言）",
                    "应用图片", "从商品详情恢复图片", "打开视频地址", "刷新仓库",
                    "保存到 TikTok", "查看保存包（只读）", "运行诊断"):
            find_button(self.app, btn)
        # 页面不再显示预演入口
        for banned in ("预演（不保存）", "查看保存包（预演）", "保存（预演模式）"):
            self.assertFalse(any(str(b.text) == banned for b in buttons(self.app)), banned)
        # 状态按钮
        for label in ("采集箱", "待发布", "在线"):
            self.assertTrue(any(str(getattr(b, "text", "")).startswith(label)
                                for b in buttons(self.app)), label)

    def test_all_tiktok_pages_build_and_route(self):
        """左侧导航全部功能页 + 14 号隐藏完整页都必须能在沙箱构建并正确落点。"""
        for idx in [p.index for p in TIKTOK.pages]:
            self.app._switch(idx)
            self.assertEqual(self.app._page_idx, idx)
            item = get_platform("tiktok").page(idx)
            self.assertIn(item.label, self.app.breadcrumb.value)
            self.assertTrue(list(walk(self.app.content.controls)),
                            f"页面 {idx} 控件树为空")
        # 导航按钮逐一点击都落到自己的页面
        for index, button in self.app.nav_buttons:
            button.on_click(SimpleNamespace(control=button))
            self.assertEqual(self.app._page_idx, index)
        # 任务与日志：副标题换成 TikTok 文案（不出现 Amazon/SMT 措辞）
        self.app._switch(10)
        self.assertIn("跟踪 TikTok 运行日志与任务记录", texts(self.app))

    def test_tiktok_nav_split_by_function(self):
        """像速卖通一样：每个功能一个左侧导航页，每页只放对应卡片。"""
        self.app._switch_platform("tiktok")
        self.app._switch(15)
        nav = [idx for idx, _b in self.app.nav_buttons]
        self.assertEqual(nav, [0, 22, 24, 25, 35, 26, 27, 28, 46, 10, 9, 11])
        for idx, btn in self.app.nav_buttons:
            btn.on_click(SimpleNamespace(control=btn))
            self.assertEqual(self.app._page_idx, idx, f"导航 {idx} 落点错误")

        def btns():
            return [str(getattr(b, "text", "")) for b in buttons(self.app)]

        self.app._switch(22)
        self.assertIn("测试连接", btns())
        self.assertNotIn("保存到 TikTok", btns())
        self.app._switch(23)
        self.assertEqual(self.app._page_idx, 28)
        self.assertIn("开始自动处理", btns())
        self.app._switch(24)
        self.assertTrue(any("补全属性" in x for x in btns()))
        self.assertNotIn("保存到 TikTok", btns())
        self.app._switch(26)
        self.assertTrue(any("AI 生成标题" in x for x in btns()))
        self.assertNotIn("保存到 TikTok", btns())
        self.app._switch(27)
        self.assertTrue(any("应用图片" in x for x in btns()))
        self.assertNotIn("保存到 TikTok", btns())
        self.app._switch(25)
        self.assertNotIn("保存到 TikTok", btns())
        self.app._switch(28)
        self.assertIn("开始自动处理", btns())
        self.assertIn("检查本次范围", btns())
        # 拆分页不再使用 5 栏 Tabs（Tabs 只在 14 号隐藏完整工作台）
        self.assertFalse(any(isinstance(c, ft.Tabs) for c in controls(self.app)))

    # ---------- ① 无店铺指引 / 23 站点 ----------
    def test_no_shop_guidance_and_23_l7_sites(self):
        self.app._switch(14)
        self.assertEqual(len((find_dropdown(self.app, "站点").options or [])), 23,
                         "无店铺时站点下拉必须有 23 个 L7 站点")
        with fake_backend(shops=[]), sync_threads():
            click(find_button(self.app, "测试连接"))
        shop_dd = find_dropdown(self.app, "店铺")
        site_dd = find_dropdown(self.app, "站点")
        self.assertEqual(list(shop_dd.options or []), [],
                         "无 TikTok 店铺时店铺下拉必须为空")
        self.assertEqual(len(site_dd.options or []), 23)
        self.assertIn("未检测到 TikTok 店铺", " ".join(texts(self.app)))
        status = [c.value for c in controls(self.app)
                  if isinstance(c, ft.Text) and c.value
                  and c.value.startswith("连接成功")]
        self.assertTrue(status and "未检测到 TikTok 店铺" in status[0], status)
        # 站点仍可用：写入配置
        self.assertTrue(self.app.cfg.tiktok_site)
        # 切到一个站点即可用类目/AI（不要求店铺）
        with fake_backend(shops=[]), sync_threads():
            click(find_button(self.app, "加载类目"))
        root = find_dropdown(self.app, "顶级类目")
        self.assertTrue(root.options, "无店铺时仍应能加载类目")

    # ---------- ③ 类目：下钻 / 切站点清空 ----------
    def test_category_drilldown_and_site_switch_clears_category(self):
        self.app._switch(14)
        with fake_backend(), sync_threads():
            click(find_button(self.app, "加载类目"))
        root = find_dropdown(self.app, "顶级类目")
        self.assertEqual(len(root.options), 1)
        root.value = str(root.options[0].key)
        with fake_backend(), sync_threads():
            change(root)
        choose = [b for b in buttons(self.app)
                  if str(getattr(b, "text", "")) == "下级"]
        self.assertTrue(choose, "非末级类目应显示「下级」")
        with fake_backend(), sync_threads():
            click(choose[0])
        choose_leaf = [b for b in buttons(self.app)
                       if str(getattr(b, "text", "")) == "选择"]
        self.assertTrue(choose_leaf, "末级类目应显示「选择」")
        with fake_backend(), sync_threads():
            click(choose_leaf[0])
        self.assertEqual(self.app.cfg.tiktok_category_id, CATEGORY_ID)
        self.assertEqual(self.app.cfg.tiktok_category_path, CATEGORY_PATH)
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "当前类目" in c.value and CATEGORY_ID in c.value
                            for c in controls(self.app)))
        # 属性表加载（必填高亮）
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "属性：" in c.value and "2 项" in c.value
                            for c in controls(self.app)))
        # 切站点 → 清空类目 + 更新币种/语言/限额
        site_dd = find_dropdown(self.app, "站点")
        old = str(site_dd.value)
        new = next(str(o.key) for o in site_dd.options if str(o.key) != old)
        site_dd.value = new
        with fake_backend(), sync_threads():
            change(site_dd)
        self.assertEqual(self.app.cfg.tiktok_category_id, "",
                         "切站点必须清空类目（需重选）")
        self.assertEqual(self.app.cfg.tiktok_site, new)
        meta = ui.TIKTOK_SITE_META.get(new) or {}
        if meta:
            self.assertTrue(any(isinstance(c, ft.Text) and c.value
                                and meta["currency"] in c.value
                                for c in controls(self.app)),
                            "切站点后币种未更新")
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "站点限额" in c.value for c in controls(self.app)))

    def test_attribute_dialog_edits_native_values_and_keeps_legacy_metadata(self):
        cfg = self.app.cfg
        cfg.set("tiktok_site", SITE)
        cfg.set("tiktok_category_id", CATEGORY_ID)
        previous = {"productAttributes": [{"attributeId": "7", "attributeName": "Color", "readback": True,
                     "values": [{"valueId": "21", "valueName": "Black", "tag": "old"}]}], "platformFlag": 9}
        cfg.set("tiktok_product_attributes", json.dumps(previous))
        self.app._switch(14)
        with fake_backend(), sync_threads():
            click(find_button(self.app, "加载类目属性", exact=True))

        click(find_button(self.app, "编辑类目属性", exact=True))
        dialog = self.app.page.dialog
        get = lambda name: next(c for c in walk([dialog.content]) if getattr(c, "label", "") == name)
        color = get("Color（必填）")
        color.value = "1"
        click(dialog.actions[0])
        self.assertEqual(json.loads(cfg.tiktok_product_attributes), previous)

        click(find_button(self.app, "编辑类目属性", exact=True))
        dialog = self.app.page.dialog
        get("Color（必填）").value = "1"
        click(next(c for c in walk([dialog.content]) if getattr(c, "text", "") == "添加值"))
        extra = [c for c in walk([dialog.content]) if getattr(c, "label", "") == "Color（必填）"][-1]
        extra.value = "custom"
        change(extra)
        next(c for c in walk([dialog.content]) if getattr(c, "label", "") == "自定义属性值" and c.visible).value = "Beige"
        get("添加类目属性").value = "8"
        click(next(c for c in walk([dialog.content]) if getattr(c, "text", "") == "添加属性"))
        get("Material").value = "TPU"
        click(dialog.actions[1])
        current = json.loads(cfg.tiktok_product_attributes)
        self.assertEqual(current["platformFlag"], 9)
        self.assertEqual(current["productAttributes"][0]["readback"], True)
        self.assertEqual(current["productAttributes"][0]["values"],
                         [{"valueId": "22", "valueName": "White", "tag": "old"}, {"valueId": "", "valueName": "Beige"}])
        self.assertEqual(current["productAttributes"][1]["values"], [{"valueId": "", "valueName": "TPU"}])
        self.assertIn("已保存 2 项属性", texts(self.app))

    # ---------- ② 列表 / 载入详情 ----------
    def test_connect_list_and_load_detail(self):
        self.app._switch(14)
        with fake_backend(), sync_threads():
            click(find_button(self.app, "测试连接"))
        conn = find_control(self.app, ft.Text,
                            lambda c: c.value and c.value.startswith("连接成功"))
        self.assertIsNotNone(conn, f"连接状态未更新；文本：{texts(self.app)}")
        self.assertIn("采集箱 1", conn.value)
        self.assertEqual(self.app.cfg.tiktok_shop_id, "9001")
        self.assertEqual(self.app.cfg.tiktok_shop_name, "Aurora TikTok Shop")
        # 列表 → 编辑 → 详情回填
        with fake_backend(), sync_threads():
            click(next(b for b in buttons(self.app)
                       if str(getattr(b, "text", "")).startswith("采集箱")))
        edit = [b for b in buttons(self.app)
                if str(getattr(b, "text", "")) == "编辑"]
        self.assertTrue(edit, "商品列表没有渲染「编辑」按钮")
        with fake_backend(), sync_threads():
            click(edit[0])
        self.assertEqual(self.app.cfg.tiktok_test_product_id, PRODUCT_ID)
        title = find_textfield(self.app, "标题")
        self.assertIn("Fake TikTok", title.value)
        desc = find_textfield(self.app, "描述")
        self.assertIn("Old description", desc.value)
        images = find_textfield(self.app, "主图 URL")
        self.assertIn(MAIN_IMAGE, images.value)
        self.assertEqual(self.app.cfg.tiktok_category_id, CATEGORY_ID)
        self.assertEqual(float(self.app.cfg.tiktok_price), 12.5)
        self.assertEqual(self.app.cfg.tiktok_sku, "SKU-1")
        # 空列表给空状态
        class EmptyClient(FakeClient):
            def list_products(self, state="draft", page=1, page_size=20,
                              shop_id="-1", keyword="", site=""):
                return {"items": [], "total": 0, "page_no": 1, "total_page": 1,
                        "drafts": 0, "wait_publish": 0, "publish_fail": 0}

        empty_make = (lambda cfg: EmptyClient())
        with patch.object(ui.App, "_tiktok_backend",
                          lambda self: (empty_make, SimpleNamespace(), SimpleNamespace())):
            with sync_threads():
                click(next(b for b in buttons(self.app)
                           if str(getattr(b, "text", "")).startswith("采集箱")))
        self.assertTrue(any(isinstance(c, ft.Text) and c.value
                            and "该状态没有商品" in c.value for c in controls(self.app)),
                        "空列表缺少空状态提示")

    # ---------- ④ AI ----------
    def test_ai_needs_switch_then_fills_fields(self):
        self.app._switch(14)
        # 未载入商品且未开开关：拒绝
        with fake_backend(), sync_threads():
            click(find_button(self.app, "AI 生成标题"))
        self.assertIn("开关", snack(self.app))
        self._load_product()
        with fake_backend(), sync_threads():
            click(find_button(self.app, "AI 生成标题"))
        self.assertIn("开关", snack(self.app))
        # 打开开关 → 付费确认 → 确认后填标题
        ai_switch = find_control(self.app, ft.Switch,
                                 lambda c: "AI" in str(getattr(c, "label", "")))
        ai_switch.value = True
        with fake_backend() as env, sync_threads():
            change(ai_switch)
            click(find_button(self.app, "AI 生成标题"))
            self.assertIsNotNone(self.app.page.dialog, "没有弹付费确认")
            self.assertIn("付费", str(self.app.page.dialog.title.value))
            click(self.app.page.dialog.actions[1])
        title = find_textfield(self.app, "标题")
        self.assertEqual(title.value, "AI Generated TikTok Title That Is Long Enough")
        self.assertTrue(env.calls["ai"], "ai_generate_field 未被调用")
        self.assertEqual(env.calls["ai"][-1]["field"], "title")
        # 一键生成全部
        with fake_backend() as env2, sync_threads():
            click(find_button(self.app, "AI 一键生成全部"))
            self.assertIsNotNone(self.app.page.dialog)
            click(self.app.page.dialog.actions[1])
        self.assertTrue(env2.calls["ai_all"])
        # 一键翻译（站点语言）→ 两次字段调用
        with fake_backend() as env3, sync_threads():
            click(find_button(self.app, "AI 一键翻译"))
            self.assertIsNotNone(self.app.page.dialog)
            click(self.app.page.dialog.actions[1])
        fields = [x["field"] for x in env3.calls["ai"]]
        self.assertIn("title", fields)
        self.assertIn("description", fields)

    # ---------- ⑦ 保存 ----------
    def test_no_preview_ui_and_save_rejected_in_test_guard(self):
        self.app._switch(14)
        self._load_product()
        # UI 不再有预演入口
        for banned in ("预演（不保存）", "查看保存包（预演）", "保存（预演模式）"):
            self.assertFalse(any(str(b.text) == banned for b in buttons(self.app)), banned)
        # AURORA_TEST/AURORA_NO_SAVE 下点保存：测试保护拒绝，且不产生任何真实保存调用
        with fake_backend() as env2, sync_threads():
            click(find_button(self.app, "保存到 TikTok"))
        self.assertEqual(snack(self.app), "测试保护模式：已拒绝真实保存")
        self.assertEqual(env2.calls["run"], [], "测试保护下不能有任何真实保存调用")
        self.assertEqual(env2.calls["prepare"], [])

    def test_save_precheck_blocks_missing_shop_site_category_images(self):
        self.app._switch(14)
        self.app.cfg.set("tiktok_shop_id", "")
        self.app.cfg.set("tiktok_category_id", "")
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(14)
            self.app.cfg.set("tiktok_test_product_id", PRODUCT_ID)
            with fake_backend() as env, sync_threads():
                click(find_button(self.app, "保存到 TikTok"))
        # 前置缺失：不发起写请求（不调用 prepare/run）
        self.assertEqual(env.calls["prepare"], [])
        self.assertEqual(env.calls["run"], [])
        self.assertTrue(any("保存前置检查未通过" in t for t in texts(self.app)))

    def test_real_save_flow_uses_confirm_and_backend(self):
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.app._preview_mode = False
            self.app._switch(0)
            self.app._switch(14)
            self._load_product()
            save_btn = find_button(self.app, "保存到 TikTok")
            with fake_backend() as env, sync_threads():
                click(save_btn)
                self.assertIsNotNone(self.app.page.dialog, "保存没有二次确认弹窗")
                dialog_text = " ".join(
                    str(c.value) for c in walk([self.app.page.dialog.content])
                    if isinstance(c, ft.Text))
                self.assertIn(PRODUCT_ID, dialog_text)
                self.assertIn("确认保存到 TikTok", str(self.app.page.dialog.title.value))
                click(self.app.page.dialog.actions[1])
            self.assertTrue(env.calls["prepare"], "保存前未调用 prepare_save")
            self.assertTrue(env.calls["run"], "保存未调用 run_tiktok_product")
            self.assertFalse(env.calls["run"][-1]["dry_run"], "保存必须 dry_run=False")
            self.assertTrue(env.calls["run"][-1].get("confirm_write"),
                            "真实保存必须显式 confirm_write=True")
            self.assertTrue(any(isinstance(c, ft.Text) and c.value
                                and "保存成功" in c.value for c in controls(self.app)))
            # overrides 只允许契约 §3.4 的 snake_case 键
            overrides = env.calls["run"][-1]["overrides"]
            allowed = {
                "title", "description", "key_features", "search_terms",
                "category_id", "category_name", "category_list", "images",
                "video_id", "source_url", "product_type", "price", "stock",
                "seller_sku", "weight", "length", "width", "height",
                "dimension_unit", "warehouse_id", "product_unit", "brand_id",
                "manufacturer_id", "responsible_person_id",
                "product_attributes", "certifications",
            }
            self.assertTrue(set(overrides).issubset(allowed),
                            f"出现契约外 overrides 键：{set(overrides) - allowed}")
            # 后端已就绪时再对齐一次：UI 发的键必须被 tiktok_payload 接受
            try:
                from aeclone.services.tiktok_payload import OVERRIDE_KEYS
            except Exception:
                OVERRIDE_KEYS = None
            if OVERRIDE_KEYS:
                self.assertTrue(set(overrides).issubset(set(OVERRIDE_KEYS)),
                                f"后端不认这些 overrides 键："
                                f"{set(overrides) - set(OVERRIDE_KEYS)}")
            self.assertEqual(overrides["category_id"], CATEGORY_ID)
            self.assertEqual(overrides["images"][0], MAIN_IMAGE)
            self.assertEqual(overrides["seller_sku"], "SKU-1")

    def test_save_payload_preview_is_readonly(self):
        self.app._switch(14)
        self._load_product()
        with fake_backend() as env, sync_threads():
            click(find_button(self.app, "查看保存包"))
        self.assertTrue(env.calls["prepare"], "查看保存包未调用 prepare_save")
        self.assertIsNotNone(self.app.page.dialog)
        self.assertIn("保存包预览", str(self.app.page.dialog.title.value))
        # 预览不触发任何真实保存
        self.assertEqual(env.calls["run"], [])

    # ---------- ⑧ 诊断 ----------
    def test_diagnose_reports_missing_shop(self):
        self.app._switch(14)
        with fake_backend(shops=[]), sync_threads():
            click(find_button(self.app, "运行诊断"))
        all_text = " ".join(texts(self.app))
        self.assertIn("后端模块", all_text)
        self.assertIn("未检测到 TikTok 店铺", all_text)
        self.assertIn("保存前置", all_text)

    # ---------- 概览 / 守卫 ----------
    def test_overview_uses_tiktok_counts_and_jumps_to_first_function_page(self):
        self.app._switch(0)
        labels = [str(getattr(c, "text", "")) for c in walk(self.app.content.controls)]
        self.assertIn("开始自动处理", labels)
        self.assertNotIn("选择商品", labels)
        button = next(c for c in walk(self.app.content.controls) if getattr(c, "text", "") == "处理范围与发布去向")
        button.on_click(SimpleNamespace(control=button))
        self.assertEqual(self.app._page_idx, 28)
        self.assertTrue(any(getattr(c, "label", "") == "采集箱关键词（可选）" for c in walk(self.app.content.controls)))

    def test_tiktok_platform_guards_block_smt_pipeline(self):
        self.app._switch(0)
        self.app._running = self.app._stop_requested = False
        with patch("aeclone.ui.collect_workflow.launch") as launch, patch.object(self.app, "_run_pipeline") as pipeline:
            self.app._toggle_run()
            self.app._start()
            self.app._on_key(SimpleNamespace(key="r", ctrl=True, meta=False))
            self.assertEqual(launch.call_count, 3)
            self.assertTrue(all(c.args == (self.app, "tiktok") for c in launch.call_args_list))
            pipeline.assert_not_called()
        with patch.object(self.app, "_log") as log:
            self.app._run_pipeline()
            self.assertTrue(any(call.args[1] == "ERROR" for call in log.call_args_list))

    # ---------- 平台锁定：implemented=False → 暂不支持 ----------
    def test_unimplemented_platform_shows_unsupported_and_tiktok_is_enterable(self):
        self.app._switch_platform("shopee")
        self.app._switch(0)
        self.assertIn("Shopee 工作空间", " ".join(texts(self.app)))
        # 不出现购买/开通 CTA（不能卖还没做的能力）
        self.assertFalse(any("去授权网站开通" == str(getattr(b, "text", ""))
                             for b in buttons(self.app)))
        # TikTok 已实现：正常进入，不显示暂不支持
        self.app._switch_platform("tiktok")
        self.app._switch(14)
        self.assertEqual(self.app._page_idx, 14)
        self.assertNotIn("暂不支持", " ".join(texts(self.app)))

    # ---------- 后端未就绪 / 回调 0 异常 ----------
    def test_page_and_callbacks_survive_missing_backend(self):
        """后端模块不存在时：页面能渲染，点按钮只提示「后端未就绪」，不抛异常。"""
        self.app._switch(14)
        with patch.object(ui.App, "_tiktok_backend",
                          side_effect=ImportError("no tiktok modules")):
            with sync_threads():
                click(find_button(self.app, "测试连接"))
            conn = [c.value for c in controls(self.app)
                    if isinstance(c, ft.Text) and c.value
                    and "连接失败" in c.value]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
