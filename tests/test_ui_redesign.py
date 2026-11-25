"""验证重新分组后的功能入口、设置保存、主题和控件生命周期。"""
from pathlib import Path
from types import SimpleNamespace as Event
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import build_app, sandbox, walk
import flet as ft
from aeclone.config import AppConfig
from aeclone.ui import app as ui
from aeclone.ui.navigation import DESTINATIONS, destination


class RedesignTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()

    def test_all_platform_pages_build_in_both_themes(self):
        """按平台实际页面逐个构建：Alibaba 已移除速卖通专属页，速卖通保留全部旧页。"""
        _idx = {d.index for d in DESTINATIONS}
        self.assertTrue(({46} | set(range(12))).issubset(_idx), _idx)
        self.assertTrue({12, 13, 14}.issubset(_idx), _idx)
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            for pf_id, expect_all in (("alibaba", False), ("aliexpress", True)):
                self.app._switch_platform(pf_id)
                pf = self.app.active_platform
                items = list(pf.pages)
                if expect_all:
                    self.assertEqual({p.index for p in items}, set(range(12)) | {46},
                                     "速卖通平台必须保留 0-11 全部页面")
                for item in items:
                    with self.subTest(theme=theme, platform=pf_id, page=item.label):
                        self.app._switch(item.index)
                        self.assertEqual(self.app._current, item.index)
                        self.assertIn(item.label, self.app.breadcrumb.value)
                        self.assertTrue(list(walk(self.app.content.controls)))
                        self.assertTrue(all(isinstance(b, ft.TextButton)
                                            for _, b in self.app.nav_buttons))
        self.app._switch_platform("alibaba")
        self.app._switch(25)
        # 平台化后页面号不再连续，越界页面统一回到当前平台首页。
        self.assertEqual(self.app._page_idx, self.app.active_platform.pages[0].index)
        self.assertIn(self.app.active_platform.pages[0].label, self.app.breadcrumb.value)

    def test_alibaba_hides_smt_only_pages_and_controls(self):
        """Alibaba 平台移除速卖通专属页面，且共享页面里的 SMT 卡片不出现。"""
        self.app._switch_platform("alibaba")
        pf = self.app.active_platform
        indices = {p.index for p in pf.pages}
        self.assertNotIn(6, indices, "Alibaba 平台不应有 SMT 价格页")
        self.assertNotIn(7, indices, "Alibaba 平台不应有 SMT 变种/库存页")
        self.assertNotIn(2, indices, "Alibaba 的类目信息已合并到刊登页，不应再有重复页")
        self.assertIn(12, indices)
        nav_labels = [b.content.controls[1].value for _, b in self.app.nav_buttons]
        self.assertNotIn("价格 / 阶梯 / 批发", nav_labels)
        self.assertNotIn("SKU · 库存 · 套装", nav_labels)
        # 图片页：营销图 Tab 移除；日志页：定时发布 Tab 移除
        self.app._switch(5)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        self.assertNotIn("营销图", [t.text for t in tabs.tabs])
        self.app._switch(10)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        self.assertNotIn("定时发布", [t.text for t in tabs.tabs])
        # 切换回速卖通：这些页面/卡片必须全部回来
        self.app._switch_platform("aliexpress")
        exp = {p.index for p in self.app.active_platform.pages}
        self.assertTrue({6, 7}.issubset(exp))
        self.app._switch(2)
        all_texts = {c.value for c in walk(self.app.content.controls)
                     if isinstance(c, ft.Text) and c.value}
        self.assertTrue(any("欧盟资质" in t for t in all_texts))
        self.app._switch(5)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        self.assertIn("营销图", [t.text for t in tabs.tabs])

    def test_alibaba_home_offers_one_manual_workspace_outside_the_batch_steps(self):
        self.app._switch(0)
        visible = {index for index, _ in self.app.nav_buttons}
        self.assertTrue({1, 44, 33}.issubset(visible))
        self.assertTrue({12, 3, 5, 30, 31, 32}.isdisjoint(visible))
        actions = {getattr(c, "text", ""): c for c in walk(self.app.content.controls)
                   if isinstance(c, (ft.OutlinedButton, ft.TextButton))}
        self.assertTrue({"去连接店铺", "设置处理规则", "去运行与结果", "单品处理"}.issubset(actions))
        actions["单品处理"].on_click(None)
        self.assertEqual(self.app._page_idx, 12)
        self.assertIn("单品处理", self.app.breadcrumb.value)
        controls = list(walk(self.app.content.controls))
        self.assertIn("按商品 ID 打开（可空）", {getattr(c, "label", "") for c in controls})
        self.assertIn("按 ID 打开", {getattr(c, "text", "") for c in controls})
        self.assertNotIn("保存草稿", {getattr(c, "text", "") for c in controls})

    def test_navigation_search_alias_empty_state_and_enter(self):
        self.app._filter_navigation("水印")
        visible = {i for i, button in self.app.nav_buttons if button.visible}
        self.assertEqual(visible, {44})
        self.app._filter_navigation("no-such-destination")
        self.assertTrue(self.app.nav_empty.visible)
        self.app.nav_search.value = "库存"
        self.app._open_navigation_match()
        self.assertEqual(self.app._page_idx, 44)
        self.assertEqual(self.app.nav_search.value, "")
        self.assertTrue(all(b.visible for _, b in self.app.nav_buttons))
        self.app._switch_platform("aliexpress")
        self.app.nav_search.value = "库存"
        self.app._open_navigation_match()
        self.assertEqual(self.app._page_idx, 7, "速卖通平台的库存页必须仍可被搜索到")

    def test_each_navigation_button_routes_to_its_own_page(self):
        for index, button in self.app.nav_buttons:
            button.on_click(Event(control=button))
            self.assertEqual(self.app._page_idx, index)
            item = self.app.active_platform.page(index)
            self.assertIn(item.label, self.app.breadcrumb.value)

    def test_navigation_resets_content_scroll_to_show_page_header(self):
        with patch.object(self.app.content, "scroll_to") as scroll:
            self.app._switch(3)
            scroll.assert_called_once_with(offset=0, duration=0)

    def test_keyword_takes_effect_without_enter_and_survives_theme(self):
        self.app.search_kw.value = "drilling"
        self.app.search_kw.on_change(Event(control=self.app.search_kw))
        self.assertEqual(self.app.run_keyword, "drilling")
        self.app._toggle_theme()
        self.assertEqual(self.app.search_kw.value, "drilling")
        self.assertEqual(self.app.run_keyword, "drilling")
        self.app._clear_search()
        self.assertEqual(self.app.run_keyword, "")
        self.assertEqual(self.app.search_kw.value, "")

    def test_repeated_theme_changes_keep_pickers_and_one_scheduler(self):
        self.app._switch_platform("aliexpress")
        self.app._switch(3)
        self.app._switch(5)
        scheduler = self.app.scheduler
        for _ in range(4):
            self.app._toggle_theme()
            self.assertIs(self.app.scheduler, scheduler)
            for name in ("file_picker", "wm_picker", "_dl_picker", "_ref_picker"):
                self.assertIn(getattr(self.app, name), self.app.page.overlay)
            ids = [id(c) for c in self.app.page.overlay]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(self.app._page_idx, 5)

    def test_ai_and_review_fields_are_complete_and_save_across_navigation(self):
        # 速卖通原有能力完整保留，Alibaba 的专用提示词不应写入通用配置。
        self.app._switch_platform("aliexpress")
        for index, schema in ((3, ui.schema_ai), (4, ui.schema_review)):
            self.app._switch(index)
            expected = {f.key for _, fields in schema() for f in fields}
            fields = {f.key: f for f in self.app.field_refs}
            self.assertEqual(set(fields), expected)
            for key, field in fields.items():
                value = not field.ctrl.value if field.kind == "bool" else "验证提示词"
                field.ctrl.value = value
                field.ctrl.on_change(Event(control=field.ctrl))
                self.assertEqual(self.app.cfg.get(key), value)
            self.app._switch(0)
            self.app._switch(index)
            for field in self.app.field_refs:
                self.assertEqual(field.ctrl.value, self.app.cfg.get(field.key))
        self.app._switch_platform("alibaba")
        before = self.app.cfg.title_prompt
        self.app._switch(3)
        editor = next(c for c in walk(self.app.content.controls)
                      if isinstance(c, ft.TextField) and c.label == "标题提示词")
        editor.value = "Alibaba 专用测试提示词"
        editor.on_change(Event(control=editor))
        self.assertEqual(self.app.cfg.alibaba_title_prompt, editor.value)
        self.assertEqual(self.app.cfg.title_prompt, before)

    def test_feature_groups_keep_every_settings_card_and_variant_tool(self):
        # Alibaba：只保留 Alibaba 能力；SMT 专属 Tab/卡片被裁剪
        self.app._switch_platform("alibaba")
        self.app._switch(12)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        self.assertEqual([t.text for t in tabs.tabs],
                         ["选择商品", "文案", "图片", "类目与属性", "商品资料", "价格与贸易", "保存单品"])
        alibaba_expected = {
            44: ["AI 文案", "AI 图片", "类目与属性", "价格与贸易", "公司资料"],
            9: ["商品与取图", "图片处理与输出"],
            10: ["运行日志"],
            11: ["账号与连接", "图片与模型", "界面", "维护与诊断"],
        }
        for index, titles in alibaba_expected.items():
            self.app._switch(index)
            tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
            self.assertEqual([t.text for t in tabs.tabs], titles, f"page {index}")
            # 控件树必须包含非活动 Tab 的功能，不能仅渲染一页后丢掉其余能力。
            self.assertTrue(all(list(walk([tab.content])) for tab in tabs.tabs))
        # 设置页关键卡仍在
        self.app._switch(11)
        all_text = {c.value for c in walk(self.app.content.controls) if isinstance(c, ft.Text)}
        self.assertTrue({"授权", "图床", "AI 模型", "模型价格", "版本更新"} <= all_text)
        self.assertFalse(any("推荐配置" in t for t in all_text))
        self.assertFalse(any("平台与功能包授权" in t or "多开 / 多任务" in t for t in all_text))

        # 速卖通平台：原有页面/卡片必须全部保留（多平台融合，不能误删）
        self.app._switch_platform("aliexpress")
        expected = {
            2: ["类目与规格", "属性模板与清理", "欧盟资质"],
            3: ["AI 能力与提示词", "详情内容增强"],
            5: ["简单编辑", "精细化编辑", "AI 图片提示词", "营销图", "图片输出与水印", "商品视频"],
            6: ["定价与试算", "阶梯与批发", "区域调价", "价格模板库"],
            7: ["编码与库存", "变种属性与图片", "套装 SKU"],
            8: ["运行与发布设置", "发布概览（数量与批量操作）"],
            9: ["商品与取图", "图片处理与输出"],
            10: ["运行日志"],
            11: ["账号与连接", "图片与模型", "界面", "维护与诊断"],
        }
        for index, titles in expected.items():
            self.app._switch(index)
            tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
            self.assertEqual([t.text for t in tabs.tabs], titles, f"aliexpress page {index}")
            self.assertTrue(all(list(walk([tab.content])) for tab in tabs.tabs))
        self.app._switch(7)
        self.assertTrue(any(getattr(c, "text", "") == "打开替换表"
                            for c in walk(self.app.content.controls)))

    def test_run_button_keeps_start_stop_and_stopping_states(self):
        # Alibaba 平台空闲时按钮语义是「处理采集箱」（打开 12 号页并确认批量），
        # SMT 平台才是「启动任务」；先切到速卖通验证原按钮状态。
        self.app._running = self.app._stop_requested = False
        self.app._sync_run_button()
        self.assertEqual(self.app.btn_run_text.value, "自动处理采集箱")
        self.app._switch_platform("aliexpress")
        for running, stopping, title in ((False, False, "启动任务"), (True, False, "停止"),
                                         (True, True, "停止中…")):
            self.app._running, self.app._stop_requested = running, stopping
            self.app._sync_run_button()
            self.assertEqual(self.app.btn_run_text.value, title)
        with patch.object(self.app, "_start") as start, patch.object(self.app, "_request_stop") as stop:
            self.app._running = self.app._stop_requested = False
            self.app.btn_run.on_click(Event(control=self.app.btn_run))
            start.assert_called_once()
            self.app._running = True
            self.app.btn_run.on_click(Event(control=self.app.btn_run))
            stop.assert_called_once()

    def test_publish_starts_with_quantity_and_destination_and_video_lives_with_media(self):
        # 速卖通队列保留；Alibaba 旧队列统一转到自动处理发布页。
        self.app._switch_platform("aliexpress")
        self.app._switch(8)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        self.assertEqual(tabs.selected_index, 0)
        controls = list(walk([tabs.tabs[0].content]))
        labels = {getattr(c, "label", "") for c in controls}
        self.assertTrue({"处理产品数量", "编辑完成后去向"} <= labels)
        self.assertNotIn("视频来源", labels)
        self.assertTrue(any(getattr(c, "text", "").startswith("只重试发布失败") for c in controls))
        self.app._switch(5)
        tabs = next(c for c in walk(self.app.content.controls) if isinstance(c, ft.Tabs))
        video = next(t for t in tabs.tabs if t.text == "商品视频")
        labels = {getattr(c, "label", "") for c in walk([video.content])}
        self.assertTrue({"视频来源", "每张停留秒数", "视频尺寸", "首尾淡入淡出"} <= labels)
        self.app._switch_platform("alibaba")
        self.app._switch(8)
        self.assertEqual(self.app._page_idx, 33)
        labels = {getattr(c, "label", "") for c in walk(self.app.content.controls)}
        self.assertTrue({"本次处理数量（0 = 全部）", "完成后去向"} <= labels)

    def test_documented_unlimited_count_and_local_processing_validation(self):
        from aeclone.services.preflight import ai_active
        for key in self.app.cfg.as_dict():
            if isinstance(self.app.cfg.get(key), bool):
                self.app.cfg.set(key, False)
        self.app.cfg.image_edit_mode = "fine"
        self.app.cfg.image_ai_jobs = "[]"
        self.app.cfg.image_edit_plans = "[]"
        self.app.cfg.background_gen_rules = "[]"
        self.app.cfg.api_key = ""
        self.assertFalse(ai_active(self.app.cfg))
        for count in ("0", "", "1"):
            self.app.cfg.edit_sum = count
            errors = self.app._validate()
            self.assertFalse(any("产品数量" in error for error in errors), errors)
            self.assertFalse(any("API Key" in error for error in errors), errors)
        self.app.cfg.edit_sum = "-1"
        self.assertTrue(any("产品数量" in error for error in self.app._validate()))

    def test_price_modes_and_inventory_save_to_isolated_config(self):
        self.app._switch_platform("aliexpress")
        self.app._switch(6)
        for mode in ("fixed", "multiplier", "free_shipping", "commission"):
            self.app.price_mode_dd.value = mode
            self.app.price_mode_dd.on_change(Event(control=self.app.price_mode_dd))
            self.assertEqual(self.app.cfg.price_mode, mode)
            self.assertEqual(self.app.p_fixed.disabled, mode != "fixed")
        self.app._switch(7)
        self.app.stock_tf.value = "42"
        self.app.stock_tf.on_change(Event(control=self.app.stock_tf))
        self.assertEqual(json.loads(AppConfig.path().read_text())["stock_quantity"], "42")
        self.app._switch(6)
        self.app._switch(7)
        self.assertEqual(self.app.stock_tf.value, "42")

    def test_reset_opens_confirmation_without_resetting_on_first_click(self):
        self.app.cfg.api_key = "test-only-key"
        self.app._reset()
        self.assertEqual(self.app.cfg.api_key, "test-only-key")
        self.assertTrue(self.app.page.dialog.open)
        cancel = self.app.page.dialog.actions[0]
        cancel.on_click(Event(control=cancel))
        self.assertFalse(self.app.page.dialog.open)
        self.assertEqual(self.app.cfg.api_key, "test-only-key")

    def test_clear_edited_keeps_confirmation_and_current_page(self):
        from aeclone.services import edited
        self.app._switch(10)
        self.app._clear_edited()
        self.assertIsNone(self.app.page.dialog)
        edited.mark("test-product", "测试商品")
        self.app._clear_edited()
        self.assertTrue(self.app.page.dialog.open)
        self.assertTrue(edited.is_edited("test-product"))
        confirm = self.app.page.dialog.actions[1]
        confirm.on_click(Event(control=confirm))
        self.assertEqual(edited.stats()["count"], 0)
        self.assertEqual(self.app._page_idx, 10)
        self.assertFalse(self.app.page.dialog.open)

    def test_combo_controls_are_editable_and_invalid_multiplier_is_reported(self):
        # 套装 SKU 是速卖通能力；Alibaba 平台不展示该页。
        self.app._switch_platform("aliexpress")
        self.app._switch(7)
        controls = list(walk(self.app.content.controls))
        enable = next(c for c in controls if getattr(c, "label", "") == "生成套装 SKU")
        factor = next(c for c in controls if getattr(c, "label", "") == "套装价格倍率")
        enable.value = True
        enable.on_change(Event(control=enable))
        factor.value = "3"
        factor.on_change(Event(control=factor))
        self.assertTrue(self.app.cfg.combo_enable)
        self.assertEqual(self.app.cfg.combo_multiplier, "3")
        self.assertIn("× 3", self.app.combo_preview.value)
        for bad in ("invalid", "-2", "nan"):
            factor.value = bad
            factor.on_change(Event(control=factor))
            self.assertIn("有效套装价格倍率", self.app.combo_preview.value)
            self.assertTrue(any("套装价格倍率" in item for item in self.app._validate()))

    def test_image_prompt_survives_navigation_before_debounce_flush(self):
        from button_audit import populate
        populate(self.app, 5)
        self.app._switch(5)
        prompt = next(c for c in walk(self.app.content.controls)
                      if getattr(c, "label", "") == "自动图片处理提示词")
        prompt.value = "保留商品结构，使用浅灰背景"
        prompt.on_change(Event(control=prompt))
        self.app._switch(0)
        self.app._switch(5)
        rebuilt = next(c for c in walk(self.app.content.controls)
                       if getattr(c, "label", "") == "自动图片处理提示词")
        self.assertEqual(rebuilt.value, "保留商品结构，使用浅灰背景")

    def test_workbench_download_moves_to_processing_without_network(self):
        self.app._switch(9)
        self.app.wb_rows = [{"id": "test-product", "images": ["https://example.invalid/image"]}]
        self.app.wb_selected = {"test-product"}
        self.app._wb_pick_images()
        self.assertEqual(self.app.wb_tabs.selected_index, 1)
        self.assertIn("下载 1 张图", self.app.wb_status.value)

    def test_toast_contrast_in_both_themes(self):
        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                      for v in channels]
            return sum(v * w for v, w in zip(values, (.2126, .7152, .0722)))
        for theme in ("dark", "light"):
            ui.apply_theme(theme)
            for ok in (True, False):
                self.app.toast("保存完成" if ok else "保存失败", ok)
                snackbar = self.app.page.snack_bar
                fg, bg = luminance(snackbar.content.color), luminance(snackbar.bgcolor)
                self.assertGreaterEqual((max(fg, bg) + .05) / (min(fg, bg) + .05), 4.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
