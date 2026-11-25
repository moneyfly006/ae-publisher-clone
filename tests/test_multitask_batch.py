"""多任务批量处理页面测试：立即处理 Tab、任务队列 Tab、各平台一致性。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox, build_app, walk

import flet as ft
from aeclone.ui.collect_workflow import RUN_PAGE


def controls(app):
    return list(walk(app.content.controls))


def texts(app):
    return {c.value for c in controls(app) if isinstance(c, ft.Text) and c.value}


def buttons(app):
    return [c for c in controls(app) if isinstance(c, ft.ElevatedButton)
            or isinstance(c, ft.OutlinedButton) or isinstance(c, ft.TextButton)]


def btn_texts(app):
    return [str(getattr(b, "text", "")) for b in buttons(app)]


class MultiTaskRunPageTests(unittest.TestCase):
    """验证新的双 Tab 运行页面在每个平台均正确渲染。"""

    def setUp(self):
        self.ctx = sandbox()
        self.cfg = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.app = build_app()

    # ------------------------------------------------------------------ structure

    def test_all_platforms_run_page_has_two_tabs(self):
        """每个平台的运行页都有「立即处理」和「任务队列」两个 Tab。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                tabs = [c for c in controls(self.app) if isinstance(c, ft.Tabs)]
                self.assertTrue(tabs, f"{platform}: 运行页应有 Tabs")
                titles = [t.text for tab in tabs for t in (tab.tabs or [])]
                self.assertIn("立即处理", titles, f"{platform}: 应有「立即处理」Tab")
                self.assertIn("任务队列", titles, f"{platform}: 应有「任务队列」Tab")
                # 只有 2 个 Tab（不是旧的 5 栏工作台）
                for tab in tabs:
                    self.assertLessEqual(len(tab.tabs or []), 2,
                                         f"{platform}: 运行页 Tabs 不超过 2 个")

    def test_all_platforms_have_start_and_check_buttons(self):
        """每个平台运行页都有「开始自动处理」和「检查本次范围」按钮。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                btns = btn_texts(self.app)
                self.assertIn("开始自动处理", btns, f"{platform}: 缺少「开始自动处理」")
                self.assertIn("检查本次范围", btns, f"{platform}: 缺少「检查本次范围」")

    def test_all_platforms_have_keyword_and_limit_fields(self):
        """每个平台运行页都有关键词和数量字段。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                labels = {getattr(c, "label", "") for c in controls(self.app)}
                self.assertIn("采集箱关键词（可选）", labels,
                              f"{platform}: 缺少关键词字段")
                self.assertIn("本次处理数量（0 = 全部）", labels,
                              f"{platform}: 缺少数量字段")
                self.assertIn("完成后去向", labels, f"{platform}: 缺少完成后去向")

    # ------------------------------------------------------------------ inline picker

    def test_inline_picker_has_load_button_and_status_filter(self):
        """内联选品卡片含「加载商品」按钮和状态下拉框。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                btns = btn_texts(self.app)
                self.assertIn("加载商品", btns, f"{platform}: 内联选品缺少「加载商品」")
                # 状态下拉框（采集箱/待发布/发布失败）
                dropdowns = [c for c in controls(self.app)
                             if isinstance(c, ft.Dropdown)]
                dd_opts = [str(o.text) for d in dropdowns
                           for o in (d.options or [])]
                self.assertIn("采集箱（草稿）", dd_opts,
                              f"{platform}: 内联选品缺少「采集箱（草稿）」选项")

    def test_inline_picker_has_select_all_and_clear_buttons(self):
        """内联选品卡片含「全选筛选结果」和「清空选择」按钮。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                btns = btn_texts(self.app)
                self.assertIn("全选本页", btns, f"{platform}: 缺「全选本页」")
                self.assertIn("全选筛选结果", btns, f"{platform}: 缺「全选筛选结果」")
                self.assertIn("清空选择", btns, f"{platform}: 缺「清空选择」")

    def test_inline_picker_selection_persists_to_config(self):
        """勾选商品后配置里的 selected_ids 更新。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                prefix = platform + "_collect_"
                # 初始应为空列表
                raw = str(getattr(self.cfg, prefix + "selected_ids", "[]") or "[]")
                self.assertEqual(json.loads(raw), [])
                # 手动写入模拟勾选
                self.cfg.set(prefix + "selected_ids",
                             json.dumps(["pid1", "pid2"], ensure_ascii=False))
                # 再次切换页面，应显示"已选 2 个"
                self.app._switch(page)
                found = any("已选" in t and "2" in t for t in texts(self.app))
                self.assertTrue(found, f"{platform}: 重新进入页面应显示已选商品数")

    # ------------------------------------------------------------------ cost estimate

    def test_cost_estimate_card_renders(self):
        """费用预估卡片出现在每个平台的运行页。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                # 费用预估卡片里有 ACCOUNT_BALANCE_WALLET 图标说明文字或"件"关键词
                found = any("件" in t or "预估" in t or "未启用" in t or "填写" in t
                            for t in texts(self.app))
                self.assertTrue(found, f"{platform}: 未渲染费用预估卡片")

    def test_limit_change_triggers_cost_refresh(self):
        """修改数量字段后，_cost_refresh 属性存在（已绑定）。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                self.assertTrue(hasattr(self.app, "_cost_refresh"),
                                f"{platform}: run_page 未绑定 _cost_refresh")
                # 调用不抛异常
                try:
                    self.app._cost_refresh()
                except Exception as e:  # noqa: BLE001
                    self.fail(f"{platform}: _cost_refresh 抛出异常: {e}")

    # ------------------------------------------------------------------ task queue

    def test_queue_tab_has_enqueue_and_run_all_buttons(self):
        """任务队列 Tab 含「加入队列」和「执行全部待执行」按钮。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                btns = btn_texts(self.app)
                self.assertIn("加入队列", btns, f"{platform}: 缺「加入队列」")
                self.assertIn("执行全部待执行", btns, f"{platform}: 缺「执行全部待执行」")
                self.assertIn("清理已完成/失败", btns, f"{platform}: 缺「清理已完成/失败」")

    def test_queue_tab_has_name_and_keyword_fields(self):
        """任务队列 Tab 含任务名称和关键词输入框。"""
        for platform, page in RUN_PAGE.items():
            with self.subTest(platform=platform):
                self.app._switch_platform(platform)
                self.app._switch(page)
                labels = {getattr(c, "label", "") for c in controls(self.app)}
                self.assertIn("任务名称（如 挖掘机-美国站）", labels,
                              f"{platform}: 缺任务名称字段")
                self.assertIn("关键词（可选）", labels,
                              f"{platform}: 缺关键词字段")
                self.assertIn("数量（0=全部）", labels,
                              f"{platform}: 缺数量字段")

    def test_enqueue_requires_name(self):
        """加入队列时不填名称会 toast 提示，不写入任务。"""
        import os
        import tempfile
        old_dir = os.environ.get("AURORA_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AURORA_CONFIG_DIR"] = tmpdir
            # 用 alibaba 平台（在 RUN_PAGE 里）
            self.app._switch_platform("alibaba")
            self.app._switch(RUN_PAGE["alibaba"])
            enqueue_btn = next(
                (b for b in buttons(self.app) if getattr(b, "text", "") == "加入队列"),
                None)
            self.assertIsNotNone(enqueue_btn, "找不到「加入队列」按钮")
            with patch.object(self.app, "toast") as toast:
                enqueue_btn.on_click(SimpleNamespace(control=enqueue_btn))
            toast.assert_called_once()
            call_args = toast.call_args
            self.assertFalse(call_args.args[1] if len(call_args.args) > 1
                             else call_args.kwargs.get("ok", True))
            if old_dir:
                os.environ["AURORA_CONFIG_DIR"] = old_dir
            else:
                os.environ.pop("AURORA_CONFIG_DIR", None)

    def test_run_all_queues_no_pending_shows_toast(self):
        """没有待执行任务时点「执行全部待执行」应提示，不崩溃。"""
        import os
        import tempfile
        old_dir = os.environ.get("AURORA_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AURORA_CONFIG_DIR"] = tmpdir
            self.app._switch_platform("alibaba")
            self.app._switch(RUN_PAGE["alibaba"])
            run_all_btn = next(
                (b for b in buttons(self.app)
                 if getattr(b, "text", "") == "执行全部待执行"),
                None)
            self.assertIsNotNone(run_all_btn, "找不到「执行全部待执行」按钮")
            with patch.object(self.app, "toast") as toast:
                run_all_btn.on_click(SimpleNamespace(control=run_all_btn))
            toast.assert_called_once()
            if old_dir:
                os.environ["AURORA_CONFIG_DIR"] = old_dir
            else:
                os.environ.pop("AURORA_CONFIG_DIR", None)

    # ------------------------------------------------------------------ alibaba specific

    def test_alibaba_run_page_has_readiness_checklist(self):
        """Alibaba 运行页在 Tabs 外还有准备清单（readiness check）。"""
        self.app._switch_platform("alibaba")
        self.app._switch(33)
        check_items = [t for t in texts(self.app) if "店铺已连接" in t or "类目已选择" in t]
        self.assertTrue(check_items, "Alibaba: 运行页缺少准备清单")

    def test_alibaba_run_page_has_repair_switch(self):
        """Alibaba 运行页包含「低分商品自动补齐并复评」开关。"""
        self.app._switch_platform("alibaba")
        self.app._switch(33)
        labels = {getattr(c, "label", "") for c in controls(self.app)}
        self.assertIn("低分商品自动补齐并复评", labels)

    # ------------------------------------------------------------------ amazon specific

    def test_amazon_run_page_has_shop_and_site_info(self):
        """Amazon 运行页显示当前店铺和站点信息。"""
        self.cfg.set("amazon_shop_id", "SHOP-TEST")
        self.cfg.set("amazon_shop_name", "测试店铺")
        self.cfg.set("amazon_site", "A1F83G8C2ARO7P")
        self.app._switch_platform("amazon")
        self.app._switch(21)
        t = texts(self.app)
        self.assertTrue(any("测试店铺" in s for s in t), "Amazon: 运行页未显示店铺名")

    # ------------------------------------------------------------------ 旧路由兼容

    def test_old_routes_still_redirect_to_run_page(self):
        """旧路由（23/29/37）仍然重定向到对应平台的运行页，不抛异常。"""
        cases = [("tiktok", 23), ("alibaba", 29), ("shopee", 37)]
        for platform, old_idx in cases:
            with self.subTest(platform=platform, old_idx=old_idx):
                self.app._switch_platform(platform)
                self.app._switch(old_idx)
                self.assertEqual(self.app._page_idx, RUN_PAGE[platform],
                                 f"{platform}: 旧路由 {old_idx} 未重定向到运行页")


if __name__ == "__main__":
    unittest.main(verbosity=2)
