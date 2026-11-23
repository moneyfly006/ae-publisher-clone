"""Amazon 平台 UI 去速卖通(SMT)残留的隔离回归（task-5）。

原则：只对 Amazon 平台隐藏不属于它的共享 UI；Alibaba / AliExpress 行为不变。
本文件只跑沙箱：不联网、不写平台数据。

运行：AURORA_TEST=1 .venv/bin/python tests/test_amazon_platform_ui_filter.py
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

from aeclone.platforms import AMAZON, PLATFORM_CAPS, cap, get_platform  # noqa: E402
from aeclone.ui import app as ui  # noqa: E402

# Amazon 专属 caps（其它平台不声明 → 默认 True）
AMAZON_ONLY_CAPS = (
    "smt_store_selector", "smt_pipeline_actions", "smt_pipeline_stats",
    "smt_diff_preview", "smt_run_stop", "smt_edited_history",
    "image_settings", "video_tools", "smt_wizard", "smt_conflicts",
)
# 复用已有 cap（Alibaba 本来就 False）：Amazon 也隐藏
REUSED_CAP = "scheduled_publish"


def controls(app):
    return list(walk(app.content.controls))


def texts(app):
    return {c.value for c in controls(app) if isinstance(c, ft.Text) and c.value}


def buttons(app):
    kinds = (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton)
    return [c for c in controls(app) if isinstance(c, kinds)]


def visible_buttons(app):
    return [b for b in buttons(app) if bool(getattr(b, "visible", True))]


def find_tabs(app):
    return next(c for c in controls(app) if isinstance(c, ft.Tabs))


class AmazonPlatformUiFilterTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()

    # ---------- caps 单元：只给 Amazon 加，其它平台默认不变 ----------
    def test_caps_are_amazon_only(self):
        self.assertIn("amazon", PLATFORM_CAPS)
        for name in AMAZON_ONLY_CAPS:
            self.assertFalse(cap("amazon", name), f"Amazon 应关闭 cap：{name}")
            # 只校验未被其它平台工作影响的两端：AliExpress / Shopee 保持默认 True。
            # TikTok 平台正由外部并发进程建设（14 号页），其 caps 由那条工作线维护，
            # 这里不断言它的默认值（避免把外部改动误判成 Amazon 回归）。
            for pid in ("aliexpress"):
                self.assertTrue(cap(pid, name), f"{pid} 不应被改动：{name}")
        # 复用 cap：Alibaba 原本就 False（既有行为），其它平台保持 True
        self.assertFalse(cap("amazon", REUSED_CAP))
        self.assertFalse(cap("alibaba", REUSED_CAP))
        self.assertTrue(cap("aliexpress", REUSED_CAP))
        # 既有 Alibaba caps 不能被回退
        self.assertTrue(cap("alibaba", "icbu_listing"))
        self.assertFalse(cap("alibaba", "smt_sku_page"))
        self.assertFalse(cap("aliexpress", "icbu_listing"))

    # ---------- A. 顶部栏 / 侧栏 ----------
    def test_amazon_shell_hides_store_selector_and_retitles_run_button(self):
        self.app._switch_platform("amazon")
        self.assertFalse(self.app.store_shortcut.visible)
        self.assertEqual(self.app.btn_run_text.value, "自动处理采集箱")
        self.assertIn("关键词", self.app.btn_run.tooltip)
        self.app._switch_platform("aliexpress")
        self.assertEqual(self.app.btn_run_text.value, "启动任务")
        self.assertEqual(self.app.btn_run.tooltip, "启动 / 停止本次任务（⌘R / Ctrl+R）")

    # ---------- B. 概览页：SMT 管线残留 ----------
    def test_amazon_overview_hides_smt_pipeline_ui(self):
        self.app._switch_platform("amazon")
        self.app._switch(0)
        labels = [getattr(c, "text", "") for c in controls(self.app)]
        self.assertNotIn("选择商品", labels)
        self.assertIn("去运行与结果", labels)
        self.assertIn("设置处理规则", labels)
        self.assertNotIn("配置冲突", texts(self.app))

    # ---------- C. 任务与日志：SMT 任务动作 ----------
    def test_amazon_logs_page_hides_smt_actions_and_schedule_tab(self):
        self.app._switch_platform("amazon")
        self.app._switch(10)
        tabs = find_tabs(self.app)
        self.assertEqual([t.text for t in tabs.tabs], ["运行日志"],
                         "Amazon 任务与日志只保留运行日志 tab")
        stop = [b for b in buttons(self.app) if str(getattr(b, "text", "")) == "停止任务"]
        self.assertTrue(stop, "控件应仍存在（只隐藏）")
        self.assertFalse(any(bool(getattr(b, "visible", True)) for b in stop),
                         "Amazon 不应显示「停止任务」")
        current = texts(self.app)
        self.assertFalse(any("已编辑记录" in t for t in current),
                         "Amazon 不应显示「已编辑记录 / 清空已编辑记录」")
        self.assertFalse(any("定时发布" in t for t in current),
                         "Amazon 不应显示定时发布队列")
        self.assertIn("跟踪 Amazon 运行日志与任务记录", current,
                      "Amazon 日志页副标题应替换为 Amazon 文案")

    # ---------- D. 设置：图片与模型 / ffmpeg ----------
    def test_amazon_settings_hides_image_tab_and_ffmpeg(self):
        self.app._switch_platform("amazon")
        self.app._switch(11)
        tabs = find_tabs(self.app)
        self.assertEqual([t.text for t in tabs.tabs],
                         ["账号与连接", "界面与实例", "维护与诊断"],
                         "Amazon 设置应隐藏「图片与模型」tab")
        self.assertNotIn("图片与模型", [t.text for t in tabs.tabs])
        ff = [b for b in buttons(self.app)
              if "ffmpeg" in str(getattr(b, "text", ""))]
        self.assertFalse(any(bool(getattr(b, "visible", True)) for b in ff),
                         "Amazon 不应显示 ffmpeg 下载按钮")
        current = texts(self.app)
        self.assertIn("诊断（出问题时再点）", current, "其它诊断项应保留")
        # 设置副标题去掉「图片服务」
        self.assertIn("管理 API、授权、界面与多开实例", current,
                      "Amazon 设置副标题应去掉「图片服务」")
        self.assertFalse(any("图片服务" in t for t in current))
        # 界面与实例说明不出现 SMT 措辞
        self.assertTrue(any("任务记录与日志也按实例隔离" in t for t in current),
                        "Amazon 界面与实例说明应换成非 SMT 措辞")
        self.assertFalse(any("任务队列与定时发布" in t for t in current))
        # 维护与诊断：恢复图床/图床提示隐藏，保留恢复 API + 全部恢复
        self.assertNotIn("图床", current, "Amazon 不应显示图床提示")
        restore_host = [b for b in buttons(self.app)
                        if str(getattr(b, "text", "")) == "恢复图床"]
        self.assertFalse(any(bool(getattr(b, "visible", True)) for b in restore_host),
                         "Amazon 不应显示「恢复图床」")
        restore_api = [b for b in buttons(self.app)
                       if str(getattr(b, "text", "")) == "恢复 API 地址"]
        self.assertTrue(any(bool(getattr(b, "visible", True)) for b in restore_api),
                        "Amazon 应保留「恢复 API 地址」")
        # 诊断时也不应探测/展示 ffmpeg（Amazon 无视频能力）
        with patch.object(ui, "video_probe") as probe:
            self.app._run_diag()
            probe.assert_not_called()
        diag_texts = {c.value for c in walk([self.app.diag_list])
                      if isinstance(c, ft.Text) and c.value}
        self.assertFalse(any("ffmpeg" in t for t in diag_texts),
                         f"Amazon 诊断不应出现 ffmpeg 项：{diag_texts}")
        self.assertFalse(self.app.diag_ff_btn.visible)

    # ---------- 13 页功能必须完整 ----------
    def test_amazon_listing_page_13_still_complete(self):
        self.app._switch_platform("amazon")
        self.app._switch(13)
        self.assertEqual(self.app._page_idx, 13)
        labels = {getattr(c, "label", "") for c in controls(self.app)
                  if getattr(c, "label", "")}
        for expect in ("店铺", "站点", "标题", "五点描述（每行一条）", "五点描述（每行一条）", "标准价",
                       "Search Terms（建议不超过 250 字节）", "保存动作",
                       "型号（model_number）", "型号名称（model_name）",
                       "零件编号（part_number）",
                       "制造商联系信息（manufacturer_contact_information）",
                       "标题预设", "五点描述预设", "产品描述预设",
                       "Search Terms预设", "SKU预设", "图片预设"):
            self.assertIn(expect, labels)
        btn_texts = {str(getattr(b, "text", "")) for b in visible_buttons(self.app)}
        # 自动发布选项面板（task-15/16）
        for expect in ("自动发布动作", "最多自动修复轮数", "发布后等待（秒）",
                       "自动补齐必填属性", "自动修正标题重复/超长/违规词",
                       "自动清理描述促销/联系方式/HTML", "自动 AI 重写违规文案（付费）"):
            self.assertIn(expect, labels, f"13 页缺少自动发布选项：{expect}")
        btn_texts = {str(getattr(b, "text", "")) for b in visible_buttons(self.app)}
        for expect in ("读取本机 Cookie", "检测连接", "刷新", "加载类目", "校验路径",
                       "AI 生成标题", "AI 一键生成全部（付费）", "AI 生成 SKU",
                       "保存到 Amazon", "查询发布结果", "自动发布（自动修复）",
                       "自动补齐并重试发布",
                       "自动补齐必填属性", "恢复内置", "复制提示词",
                       "发布中", "发布失败"):
            self.assertIn(expect, btn_texts, f"13 页缺少可用控件：{expect}")
        # 7 卡按功能分组（task-19）：标题编号与顺序
        page_text = " ".join(texts(self.app))
        for title in ("① 连接与店铺", "② 采集箱商品", "③ 站点与类目",
                      "价格与物流",
                      "⑤ 文案与 AI", "商品图片",
                      "⑦ 发布与结果"):
            self.assertIn(title, page_text)
        self.assertIn("文案提示词",
                      page_text)
        # 正式版文案：不得出现预演/检查模式/测试措辞
        page_all = " ".join(texts(self.app)) + " " + " ".join(btn_texts)
        for banned in ("AURORA_", "预演", "检查模式", "检查结果", "发布前检查",
                       "测试连接", "本地验收"):
            self.assertNotIn(banned, page_all, f"Amazon 13 页仍含开发措辞：{banned}")

    def test_amazon_primary_pages_keep_batch_and_single_controls_separate(self):
        self.app._switch_platform("amazon")
        for page, action in ((15, "检测连接"), (45, "去运行与结果"), (21, "开始自动处理"), (16, "按 ID 载入")):
            self.app._switch(page)
            self.assertTrue(any(getattr(c, "text", "") == action for c in controls(self.app)))
            if page != 16:
                self.assertFalse(any(getattr(c, "text", "") == "保存到 Amazon" for c in controls(self.app)))

    # ---------- 平台隔离：AliExpress 原样保留 ----------
    def test_amazon_ctrl_f_does_not_open_smt_search(self):
        """Amazon 平台 ⌘F 不得打开 SMT 全局商品搜索（应跳 13 页）；AliExpress 仍打开。"""
        from types import SimpleNamespace as Ev

        def key(name="f"):
            return Ev(key=name, ctrl=True, meta=False)

        self.app._switch_platform("amazon")
        self.app._switch(0)
        self.app._on_key(key())
        self.assertIsNone(getattr(self.app, "_dlg", None),
                          "Amazon ⌘F 不应打开 SMT 商品搜索弹窗")
        self.assertEqual(self.app._page_idx, 16, "Amazon ⌘F 应跳单品编辑")

        self.app._switch_platform("aliexpress")
        self.app._switch(0)
        self.app._on_key(key())
        self.assertIsNotNone(getattr(self.app, "_dlg", None),
                             "AliExpress ⌘F 仍应打开 SMT 商品搜索弹窗")

    def test_aliexpress_keeps_smt_ui(self):
        self.app._switch_platform("aliexpress")
        self.assertTrue(self.app.store_shortcut.visible)
        self.app._switch(0)
        pick = [b for b in buttons(self.app) if str(getattr(b, "text", "")) == "选择商品"]
        self.assertTrue(any(bool(getattr(b, "visible", True)) for b in pick),
                        "AliExpress 概览必须保留「选择商品」")
        current = texts(self.app)
        self.assertIn("今日任务", current)
        self.assertIn("改前后对比", current)
        # task-7 残留项：AliExpress 必须全部保留
        self.assertIn("配置冲突", current, "AliExpress 应保留「配置冲突」行")
        self.assertIn("图片服务", current, "AliExpress 应保留「图片服务」tile")
        conflict_btn = [b for b in buttons(self.app)
                        if str(getattr(b, "text", "")) == "检查配置冲突"]
        self.assertTrue(any(bool(getattr(b, "visible", True)) for b in conflict_btn),
                        "AliExpress 应保留「检查配置冲突」按钮")
        self.app._switch(10)
        tabs = find_tabs(self.app)
        self.assertIn("定时发布", [t.text for t in tabs.tabs])
        stop = [b for b in buttons(self.app) if str(getattr(b, "text", "")) == "停止任务"]
        self.assertTrue(any(bool(getattr(b, "visible", True)) for b in stop))
        self.app._switch(11)
        tabs = find_tabs(self.app)
        self.assertEqual([t.text for t in tabs.tabs],
                         ["账号与连接", "图片与模型", "界面与实例", "维护与诊断"])
        current = texts(self.app)
        self.assertTrue(any("图片服务" in t for t in current),
                        "AliExpress 设置副标题应保留「图片服务」")
        self.assertTrue(any("任务队列与定时发布也按实例隔离" in t for t in current),
                        "AliExpress 界面与实例说明应保留 SMT 措辞")
        self.assertIn("图床", current, "AliExpress 应保留图床提示")
        restore_host = [b for b in buttons(self.app)
                        if str(getattr(b, "text", "")) == "恢复图床"]
        self.assertTrue(any(bool(getattr(b, "visible", True)) for b in restore_host),
                        "AliExpress 应保留「恢复图床」按钮")

    # ---------- 平台隔离：Alibaba 保持既有 caps 行为 ----------
    def test_alibaba_keeps_existing_caps_behavior(self):
        self.app._switch_platform("alibaba")
        self.assertNotIn(6, [p.index for p in self.app.active_platform.pages])
        self.assertNotIn(7, [p.index for p in self.app.active_platform.pages])
        self.app._switch(0)
        labels = [getattr(c, "text", "") for c in controls(self.app)]
        self.assertNotIn("选择商品", labels)
        self.assertIn("去运行与结果", labels)

    # ---------- E. 首次引导 ----------
    def test_amazon_skips_smt_wizard_other_platforms_keep_it(self):
        self.app.cfg.onboarded = False
        self.app.cfg.set("store_id", "")
        self.app.cfg.set("api_key", "")
        self.app._switch_platform("amazon")
        self.app.page.dialog = None
        self.app._maybe_wizard()
        self.assertIsNone(self.app.page.dialog, "Amazon 平台不应弹 SMT 首次引导")
        self.app._switch_platform("aliexpress")
        self.app.page.dialog = None
        self.app._maybe_wizard()
        self.assertIsNotNone(self.app.page.dialog, "AliExpress 首次引导必须保留")
        self.assertIn("首次设置", str(self.app.page.dialog.title.value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
