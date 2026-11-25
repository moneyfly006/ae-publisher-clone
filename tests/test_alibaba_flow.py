"""Alibaba 六步流程通用组件：纯逻辑状态 + 无 Page 构造回归。

可独立运行：``.venv/bin/python tests/test_alibaba_flow.py -v``
（组件只读 cfg，不启动 Flet 服务、不发起网络请求。）
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import flet as ft

import aeclone.ui.alibaba_flow as flow
from aeclone.config import AppConfig
from aeclone.ui.alibaba_flow import (
    ALIBABA_STEPS,
    alibaba_step_statuses,
    alibaba_stepper,
    batch_override,
    configured_count,
    save_next_bar,
    status_badge,
)


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------
def walk(controls):
    """遍历 Flet 真实子控件树（与 tools/ui_sandbox.walk 相同思路）。"""
    seen: set[int] = set()

    def visit(control):
        if not isinstance(control, ft.Control) or id(control) in seen:
            return
        seen.add(id(control))
        yield control
        for child in control._get_children():
            yield from visit(child)

    for control in controls:
        yield from visit(control)


def texts(control) -> list[str]:
    return [
        item.value
        for item in walk([control])
        if isinstance(item, ft.Text) and item.value is not None
    ]


def find_control(control, predicate):
    return next((item for item in walk([control]) if predicate(item)), None)


def find_button(control, label: str):
    return find_control(
        control,
        lambda item: isinstance(item, (ft.ElevatedButton, ft.TextButton))
        and getattr(item, "text", None) == label,
    )


def click(control) -> None:
    assert control is not None, "按钮不存在"
    handler = getattr(control, "on_click", None)
    assert callable(handler), "按钮没有 on_click"
    handler(SimpleNamespace(control=control))


class FakePage:
    def __init__(self):
        self.updates = 0

    def update(self):
        self.updates += 1


class StubCfg:
    """最小配置桩：只实现 get，模拟缺 key 的旧配置。"""

    def __init__(self, data=None):
        object.__setattr__(self, "_data", dict(data or {}))

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getattr__(self, name):
        raise AttributeError(name)


class WriteTrackingCfg(StubCfg):
    def __init__(self, data=None):
        super().__init__(data)
        object.__setattr__(self, "writes", [])

    def set(self, key, value):
        self.writes.append((key, value))


class BrokenCfg:
    def get(self, key, default=None):
        raise RuntimeError("配置读取失败")

    def __getattr__(self, name):
        raise RuntimeError("配置属性读取失败")


class BrokenApp:
    """连 app.cfg / 路由访问都会抛异常的宿主，用于验证防御性。"""

    @property
    def cfg(self):
        raise RuntimeError("cfg 不可用")

    @property
    def _page_idx(self):
        raise RuntimeError("路由不可用")

    def _switch(self, index):
        raise RuntimeError("跳转失败")

    def toast(self, message, ok=True):
        raise RuntimeError("toast 失败")


def make_app(data=None, **attrs):
    app = SimpleNamespace(cfg=StubCfg(data), **attrs)
    return app


def full_config(**overrides):
    data = {
        "alibaba_shop_id": "9466975",
        "alibaba_shop_name": "zzyugong",
        "alibaba_test_product_id": "220114598434958580",
        "alibaba_category_id": "127726092",
        "alibaba_attr_presets": json.dumps({"Warranty": "1 Year"}),
        "alibaba_company_name": "ACME Machinery",
        "alibaba_company_desc": "Factory profile",
        "alibaba_keywords": "laser screed, concrete, machinery",
        "publish_seen": "{}",
    }
    data.update(overrides)
    return data


PRODUCT_ID = "220114598434958580"


def done_product_state():
    return {
        "product": {
            "subject": "Laser Screed",
            "textDesc": "Description",
            "mainImage": "https://example.invalid/a.jpg,https://example.invalid/b.jpg",
            "detailImage": "[]",
        },
        "overrides": {},
    }


# ---------------------------------------------------------------------------
# 1. 步骤定义
# ---------------------------------------------------------------------------
class StepContractTests(unittest.TestCase):
    def test_alibaba_steps_exact_contract(self):
        self.assertEqual(
            [tuple(step) for step in ALIBABA_STEPS],
            [
                (1, "连接店铺"),
                (29, "采集箱商品"),
                (30, "类目与属性"),
                (31, "商品资料与价格"),
                (3, "文案与图片"),
                (33, "检查与发布"),
            ],
        )
        self.assertEqual(len({page for page, _ in ALIBABA_STEPS}), 6)


# ---------------------------------------------------------------------------
# 2. 状态纯逻辑
# ---------------------------------------------------------------------------
class StepStatusTests(unittest.TestCase):
    def test_empty_config_is_all_todo(self):
        statuses = alibaba_step_statuses(make_app(), current_page=None)
        self.assertEqual([item["page"] for item in statuses], [1, 29, 30, 31, 3, 33])
        self.assertTrue(all(item["status"] == "todo" for item in statuses))
        self.assertTrue(all(item["done"] is False for item in statuses))
        self.assertTrue(all(item["label"] == "未开始" for item in statuses))
        self.assertTrue(all(item["detail"] for item in statuses))

    def test_missing_or_broken_config_never_raises(self):
        cases = [
            None,
            SimpleNamespace(),
            SimpleNamespace(cfg=None),
            SimpleNamespace(cfg=BrokenCfg()),
            BrokenApp(),
        ]
        for app in cases:
            with self.subTest(app=type(app).__name__):
                statuses = alibaba_step_statuses(app, 30)
                self.assertEqual(len(statuses), 6)
                self.assertEqual(statuses[2]["status"], "current")
                self.assertFalse(statuses[2]["done"])
                # 公共组件同样不能在坏宿主上抛异常
                stepper = alibaba_stepper(app, 29)
                self.assertIsInstance(stepper, ft.Control)
                tile = batch_override(app, None, [("alibaba_weight", "重量")])
                self.assertIsInstance(tile, ft.ExpansionTile)
                bar = save_next_bar(app, 1, 29)
                self.assertIsInstance(bar, ft.Control)

    def test_full_config_all_steps_done_and_current_highlight(self):
        app = make_app(
            full_config(),
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
            _publish_seen={"alibaba": {PRODUCT_ID: "success"}},
        )
        statuses = alibaba_step_statuses(app, current_page=3)
        self.assertEqual([item["done"] for item in statuses], [True] * 6)
        self.assertEqual(statuses[4]["status"], "current")
        self.assertEqual(statuses[4]["label"], "当前")
        self.assertTrue(all(item["status"] == "done" for i, item in enumerate(statuses) if i != 4))
        self.assertTrue(any("关键词 3 个" in item["detail"] for item in statuses))
        self.assertTrue(any("主图 2 张" in item["detail"] for item in statuses))
        self.assertTrue(any("发布成功" in item["detail"] for item in statuses))

    def test_real_appconfig_statuses(self):
        cfg = AppConfig(
            {
                "alibaba_shop_id": "123",
                "alibaba_test_product_id": PRODUCT_ID,
                "alibaba_category_id": "456",
                "alibaba_company_name": "ACME",
                "alibaba_company_desc": "profile",
                "alibaba_keywords": "a, b, c",
            }
        )
        app = SimpleNamespace(
            cfg=cfg,
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
        )
        statuses = alibaba_step_statuses(app, 33)
        self.assertTrue(statuses[0]["done"])
        self.assertTrue(statuses[1]["done"])
        self.assertTrue(statuses[2]["done"])
        self.assertTrue(statuses[3]["done"])
        self.assertTrue(statuses[4]["done"])
        self.assertFalse(statuses[5]["done"])  # 未发布
        self.assertEqual(statuses[5]["status"], "current")

    def test_attr_only_config_object_is_supported(self):
        class AttrCfg:
            alibaba_test_product_id = "123456789012345678"

        statuses = alibaba_step_statuses(SimpleNamespace(cfg=AttrCfg()), None)
        self.assertTrue(statuses[1]["done"])
        self.assertIn("123456789012345678"[-8:], statuses[1]["detail"])

    def test_partial_progress_is_marked_待处理(self):
        app = make_app(
            {
                "alibaba_test_product_id": PRODUCT_ID,
                "alibaba_company_name": "ACME",
                "alibaba_company_desc": "profile",
            },
            _alibaba_editor_states={
                PRODUCT_ID: {
                    # 有标题/详情但无主图 → 内容步骤应为「待处理」
                    "product": {"subject": "Title", "textDesc": "Description"},
                    "overrides": {},
                }
            },
        )
        statuses = alibaba_step_statuses(app, None)
        self.assertEqual(statuses[3]["status"], "partial")  # 公司已填，关键词不足
        self.assertTrue(statuses[3]["partial"])
        self.assertIn("0/3", statuses[3]["detail"])
        self.assertEqual(statuses[4]["status"], "partial")  # 文案完整，主图缺失
        self.assertTrue(statuses[4]["partial"])
        self.assertIn("主图待补", statuses[4]["detail"])

    def test_explicit_override_clear_is_respected(self):
        state = done_product_state()
        state["overrides"] = {"subject": "", "text_desc": "", "main_images": []}
        app = make_app(
            {"alibaba_test_product_id": PRODUCT_ID},
            _alibaba_editor_states={PRODUCT_ID: state},
        )
        statuses = alibaba_step_statuses(app, None)
        self.assertEqual(statuses[4]["status"], "todo")

    def test_publish_result_from_cfg_json_and_failure(self):
        cfg = full_config(publish_seen=json.dumps({"alibaba": {PRODUCT_ID: "success"}}))
        app = make_app(cfg, _alibaba_editor_states={PRODUCT_ID: done_product_state()})
        statuses = alibaba_step_statuses(app, None)
        self.assertTrue(statuses[5]["done"])
        self.assertIn("发布成功", statuses[5]["detail"])

        fail_app = make_app(
            full_config(publish_seen=json.dumps({"alibaba": {PRODUCT_ID: "fail"}})),
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
        )
        statuses = alibaba_step_statuses(fail_app, None)
        self.assertFalse(statuses[5]["done"])
        self.assertEqual(statuses[5]["status"], "partial")
        self.assertIn("发布失败", statuses[5]["detail"])

    def test_publish_failure_count_is_warned(self):
        app = make_app(
            {"alibaba_test_product_id": PRODUCT_ID, "publish_counts": '{"alibaba": {"fail": 2}}'},
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
        )
        statuses = alibaba_step_statuses(app, None)
        self.assertEqual(statuses[5]["status"], "partial")
        self.assertIn("2 条发布失败", statuses[5]["detail"])

    def test_current_page_inference_and_legacy_alias(self):
        app = make_app({"alibaba_test_product_id": PRODUCT_ID})
        app._page_idx = 30
        inferred = alibaba_step_statuses(app)
        self.assertEqual(
            next(item["page"] for item in inferred if item["status"] == "current"), 30
        )
        # 旧页面号 32 / 5 / 12 / 44 映射到新步骤
        for legacy, expected in ((32, 31), (5, 3), (12, 29), (44, 33)):
            with self.subTest(legacy=legacy):
                statuses = alibaba_step_statuses(app, current_page=legacy)
                self.assertEqual(
                    next(item["page"] for item in statuses if item["status"] == "current"),
                    expected,
                )
        # 非法 current_page 不抛异常，也不产生 current
        for weird in ("bad", None, [], -1, 999):
            statuses = alibaba_step_statuses(app, current_page=weird)
            self.assertEqual(len(statuses), 6)
            if weird is None:
                continue
            self.assertFalse(any(item["status"] == "current" for item in statuses))

    def test_components_only_read_config(self):
        cfg = WriteTrackingCfg(full_config())
        app = SimpleNamespace(
            cfg=cfg,
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
            _switch=lambda index: None,
        )
        alibaba_stepper(app, 30)
        batch_override(
            app,
            None,
            [("alibaba_default_weight", "重量", lambda value: ft.TextField(value=str(value)))],
        )
        save_next_bar(app, 1, 29, on_save=lambda: None)
        self.assertEqual(cfg.writes, [])


# ---------------------------------------------------------------------------
# 3. status_badge
# ---------------------------------------------------------------------------
class StatusBadgeTests(unittest.TestCase):
    def test_levels_map_to_theme_colors(self):
        palette = flow._palette()
        expected = {
            "ok": palette["success"],
            "warn": palette["warn"],
            "current": palette["accent"],
            "muted": palette["muted"],
        }
        for level, color in expected.items():
            with self.subTest(level=level):
                badge = status_badge(level, level)
                self.assertIsInstance(badge, ft.Container)
                self.assertIsInstance(badge.content, ft.Text)
                self.assertEqual(badge.content.value, level)
                self.assertEqual(badge.content.color, color)

    def test_aliases_and_unknown_level_are_safe(self):
        palette = flow._palette()
        self.assertEqual(status_badge("x", "success").content.color, palette["success"])
        self.assertEqual(status_badge("x", "warning").content.color, palette["warn"])
        self.assertEqual(status_badge("x", "info").content.color, palette["accent"])
        self.assertEqual(status_badge("x", "???").content.color, palette["muted"])
        self.assertEqual(status_badge(None).content.value, "—")


# ---------------------------------------------------------------------------
# 4. 步骤条
# ---------------------------------------------------------------------------
class StepperTests(unittest.TestCase):
    def test_stepper_renders_six_steps_and_navigates(self):
        switched = []
        app = make_app({"alibaba_test_product_id": PRODUCT_ID})
        app._switch = lambda index: switched.append(index)
        stepper = alibaba_stepper(app, current_page=29)
        self.assertIsInstance(stepper, ft.Control)

        labels = texts(stepper)
        for _, title in ALIBABA_STEPS:
            self.assertIn(title, labels)
        self.assertTrue(any("已完成 1/6" in value for value in labels))

        chip = find_control(
            stepper,
            lambda item: isinstance(item, ft.Container)
            and str(getattr(item, "tooltip", "")).startswith("第 1 步"),
        )
        self.assertIsNotNone(chip)
        click(chip)
        self.assertEqual(switched, [1])

        refresh = find_button(stepper, "刷新状态")
        click(refresh)
        self.assertIn(29, switched)

    def test_stepper_without_switch_has_no_navigation(self):
        app = make_app()
        stepper = alibaba_stepper(app, current_page=29)
        chip = find_control(
            stepper,
            lambda item: isinstance(item, ft.Container)
            and str(getattr(item, "tooltip", "")).startswith("第 2 步"),
        )
        self.assertIsNotNone(chip)
        self.assertIsNone(chip.on_click)
        self.assertIsNone(find_button(stepper, "刷新状态"))

    def test_stepper_all_done_shows_complete_badge(self):
        app = make_app(
            full_config(),
            _alibaba_editor_states={PRODUCT_ID: done_product_state()},
            _publish_seen={"alibaba": {PRODUCT_ID: "success"}},
        )
        labels = texts(alibaba_stepper(app, current_page=33))
        self.assertTrue(any("已完成 6/6" in value for value in labels))


# ---------------------------------------------------------------------------
# 5. 批量覆盖
# ---------------------------------------------------------------------------
class BatchOverrideTests(unittest.TestCase):
    def test_count_and_control_factory_contract(self):
        values = []
        no_args = []
        cfg = StubCfg(
            {
                "alibaba_default_weight": "100",
                "alibaba_default_package_size": "",
                "alibaba_hs_code": "84791000",
                "alibaba_default_freight_name": "freight",
            }
        )
        app = SimpleNamespace(cfg=cfg)
        page = FakePage()
        fields = [
            ("alibaba_default_weight", "重量"),
            (
                "alibaba_default_package_size",
                "包装",
                lambda value: values.append(value)
                or ft.TextField(label="包装", value=str(value or "")),
            ),
            {"config_key": "alibaba_hs_code", "label": "HS 编码"},
            {
                "config_key": "alibaba_default_freight_name",
                "label": "运费模板",
                "control_factory": lambda: no_args.append(True) or ft.Text("freight"),
            },
        ]
        self.assertEqual(configured_count(app, fields), 3)

        tile = batch_override(app, page, fields)
        self.assertIsInstance(tile, ft.ExpansionTile)
        self.assertEqual(values, [""])  # 工厂收到当前配置值
        self.assertEqual(no_args, [True])  # 无参工厂按无参调用
        labels = texts(tile)
        self.assertTrue(any("已设置 3/4 项" in value for value in labels))
        self.assertIn("100", labels)
        self.assertIn("84791000", labels)
        self.assertTrue(any("未设置" in value for value in labels))

        # 改变配置后刷新计数（组件只读，不写 cfg）
        cfg._data["alibaba_hs_code"] = ""
        tile.on_change(SimpleNamespace(control=tile))
        refreshed = texts(tile)
        self.assertTrue(any("已设置 2/4 项" in value for value in refreshed))
        self.assertGreaterEqual(page.updates, 1)

        refresh_button = find_button(tile, "刷新已设置数量")
        click(refresh_button)
        self.assertGreaterEqual(page.updates, 2)

    def test_malformed_fields_and_broken_factories_are_tolerated(self):
        app = make_app({"alibaba_weight": "5"})
        fields = [
            None,
            ("", "空 key"),
            ("alibaba_weight",),
            {"label": "无 key"},
            ("alibaba_weight", "重量", lambda value: 1 / 0),
            ("alibaba_hs_code", "HS", ft.Text("静态控件")),
            {"key": "alibaba_weight", "label": "重量2", "control": ft.TextField()},
        ]
        tile = batch_override(app, None, fields)
        self.assertIsInstance(tile, ft.ExpansionTile)
        self.assertTrue(any("已设置" in value for value in texts(tile)))
        # fields=None / 非可迭代对象同样安全
        for bad in (None, 42, "not-a-field-list"):
            with self.subTest(bad=bad):
                self.assertIsInstance(batch_override(app, None, bad), ft.ExpansionTile)

    def test_configured_count_never_builds_controls(self):
        def explode(value=None):
            raise AssertionError("configured_count 不应调用 control_factory")

        fields = [("alibaba_weight", "重量", explode)]
        self.assertEqual(configured_count(make_app({"alibaba_weight": "5"}), fields), 1)


# ---------------------------------------------------------------------------
# 6. 保存并下一步
# ---------------------------------------------------------------------------
class SaveNextBarTests(unittest.TestCase):
    def _app(self):
        calls = []
        app = SimpleNamespace(
            cfg=StubCfg(),
            calls=calls,
            _switch=lambda index: calls.append(("switch", index)),
            toast=lambda message, ok=True: calls.append(("toast", message, ok)),
        )
        return app, calls

    def test_save_then_navigate(self):
        app, calls = self._app()
        bar = save_next_bar(app, 29, 30, on_save=lambda: calls.append(("save",)))
        click(find_button(bar, "保存并下一步"))
        self.assertEqual(calls, [("save",), ("switch", 30)])

    def test_event_argument_is_forwarded(self):
        app, calls = self._app()
        received = []
        bar = save_next_bar(app, 29, 30, on_save=lambda event: received.append(event))
        button = find_button(bar, "保存并下一步")
        click(button)
        self.assertEqual(len(received), 1)
        self.assertIs(received[0].control, button)
        self.assertIn(("switch", 30), calls)

    def test_false_result_blocks_navigation(self):
        app, calls = self._app()
        bar = save_next_bar(app, 29, 30, on_save=lambda: False)
        click(find_button(bar, "保存并下一步"))
        self.assertEqual(calls, [])

    def test_callback_error_toasts_and_stays(self):
        app, calls = self._app()

        def boom():
            raise ValueError("保存接口失败")

        bar = save_next_bar(app, 29, 30, on_save=boom)
        click(find_button(bar, "保存并下一步"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "toast")
        self.assertFalse(calls[0][2])
        self.assertIn("保存接口失败", calls[0][1])

    def test_prev_navigation_and_missing_pages(self):
        app, calls = self._app()
        bar = save_next_bar(app, 29, 30, on_save=None)
        click(find_button(bar, "上一步"))
        self.assertEqual(calls, [("switch", 29)])

        # 上一步 / 下一步为空时只保留可用按钮，点击保存不跳转
        calls.clear()
        only_save = save_next_bar(app, None, None, on_save=lambda: calls.append(("save",)))
        self.assertIsNone(find_button(only_save, "上一步"))
        click(find_button(only_save, "保存并下一步"))
        self.assertEqual(calls, [("save",)])

        # 没有 app._switch / toast 时：保存回调照常执行，跳转安静降级
        calls.clear()
        no_switch = SimpleNamespace(cfg=StubCfg())
        bar2 = save_next_bar(no_switch, 1, 29, on_save=lambda: calls.append("save"))
        click(find_button(bar2, "保存并下一步"))
        self.assertEqual(calls, ["save"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
