"""Alibaba 运行页（页面 33）四卡合并回归：标题唯一、原功能可达、不再放大选品卡。

只验证 UI 结构与控件可达性，不联网、不启动任务。
运行：.venv/bin/python tests/test_collect_workflow.py
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox, build_app, walk  # noqa: E402

import flet as ft  # noqa: E402


def controls(app):
    return list(walk(app.content.controls))


def button_texts(app):
    return [str(getattr(c, "text", "")) for c in controls(app)
            if isinstance(c, (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton))]


def labels(app):
    return [str(getattr(c, "label", "")) for c in controls(app)]


def text_values(app):
    return [str(c.value) for c in controls(app) if isinstance(c, ft.Text) and c.value]


def expansions(app):
    return [c for c in controls(app) if isinstance(c, ft.ExpansionTile)]


def expansion_title(control):
    title = getattr(control, "title", None)
    if isinstance(title, ft.Text):
        return str(title.value or "")
    if title is not None:
        texts = [c.value for c in walk([title]) if isinstance(c, ft.Text) and c.value]
        return " / ".join(str(t) for t in texts)
    return ""


class AlibabaRunPageCardsTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.cfg = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()
        self.app._switch_platform("alibaba")
        self.app._switch(33)

    def test_four_merged_card_titles(self):
        titles = {expansion_title(c) for c in expansions(self.app)}
        self.assertIn("高级运行参数 / AI 预算", titles)
        self.assertIn("任务队列（默认折叠）", titles)
        texts = text_values(self.app)
        for title in ("发品进度", "本次任务", "结果与修复"):
            self.assertIn(title, texts, f"缺少卡片标题：{title}")

    def test_old_separate_card_titles_are_gone(self):
        """合并后不再出现旧卡标题，避免运行页重复标题。"""
        texts = set(text_values(self.app))
        for old in ("处理范围与操作", "费用预估", "任务结果", "运行参数",
                    "AI预算与复用", "发布失败自动处理", "发品准备清单"):
            self.assertNotIn(old, texts, f"旧的重复卡片标题仍在：{old}")
        # 「选择商品」大卡已移到页面 29，运行页只留跳转入口。
        self.assertNotIn("选择商品", texts)
        self.assertNotIn("加载商品", button_texts(self.app))

    def test_task_card_keeps_scope_destination_and_action_buttons(self):
        labels_text = labels(self.app)
        self.assertIn("采集箱关键词（可选）", labels_text)
        self.assertIn("本次处理数量（0 = 全部）", labels_text)
        self.assertIn("完成后去向", labels_text)
        btns = button_texts(self.app)
        for text in ("检查本次范围", "开始自动处理", "停止", "查看全过程日志",
                     "查看任务结果", "检查配置冲突", "去采集箱商品"):
            self.assertIn(text, btns, f"本次任务缺少按钮/入口：{text}")

    def test_advanced_runtime_and_ai_budget_stay_collapsed(self):
        adv = next(c for c in expansions(self.app)
                   if expansion_title(c) == "高级运行参数 / AI 预算")
        self.assertFalse(bool(adv.initially_expanded))
        inner = [str(getattr(c, "label", "")) for c in walk([adv])]
        for label in ("运行参数预设", "平台请求超时（秒）", "只读失败重试次数",
                      "本次AI调用上限", "AI金额预算（元，0 = 不设）",
                      "复用相同商品与规则的AI结果"):
            self.assertIn(label, inner, f"高级运行参数缺少字段：{label}")
        self.assertIn("应用运行预设",
                      [getattr(c, "text", "") for c in walk([adv])
                       if isinstance(c, ft.OutlinedButton)])

    def test_results_and_recovery_are_merged_with_scroll_target(self):
        self.assertTrue(any(getattr(c, "key", None) == "collect-results"
                            for c in controls(self.app)))
        btns = button_texts(self.app)
        self.assertIn("分析发布失败", btns)
        self.assertIn("修复发布失败商品", btns)
        self.assertIn("发布失败最多处理次数", labels(self.app))
        # 结果列表的筛选/回读/分页/导出入口仍在。
        for text in ("导出筛选结果", "回读平台状态", "结果上一页", "结果下一页"):
            self.assertIn(text, btns)

    def test_queue_keeps_all_actions_inside_collapsed_tile(self):
        tile = next(c for c in expansions(self.app)
                    if expansion_title(c) == "任务队列（默认折叠）")
        self.assertFalse(bool(tile.initially_expanded))
        inner_btns = [str(getattr(c, "text", "")) for c in walk([tile])
                      if isinstance(c, ft.Control)
                      and isinstance(c, (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton))]
        for text in ("加入队列", "执行全部待执行", "清理已完成/失败"):
            self.assertIn(text, inner_btns)
        inner_labels = [str(getattr(c, "label", "")) for c in walk([tile])]
        self.assertIn("任务名称（如 挖掘机-美国站）", inner_labels)

    def test_progress_card_offers_fix_links(self):
        btns = button_texts(self.app)
        self.assertTrue("去处理" in btns or "去查看" in btns)
        # 跳转按钮按六步流程落到具体页面，而不是回到运行页自己。
        targets = []
        for c in controls(self.app):
            if isinstance(c, ft.TextButton) and getattr(c, "text", "") in ("去处理", "去查看"):
                self.assertIsNotNone(c.on_click)
                targets.append(c)
        self.assertTrue(targets)

    def test_other_platforms_keep_tabs_and_picker(self):
        for platform, page in (("amazon", 21), ("tiktok", 28), ("shopee", 43)):
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                tabs = [c for c in controls(self.app) if isinstance(c, ft.Tabs)]
                self.assertTrue(tabs, f"{platform}: 运行页应保留 Tabs")
                self.assertIn("开始自动处理", button_texts(self.app))


if __name__ == "__main__":
    unittest.main(verbosity=2)
