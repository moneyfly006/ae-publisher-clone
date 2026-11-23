"""Alibaba 刊登页关键按钮的行为验证（隔离沙箱，不联网、不真实保存）。

覆盖：
- Alibaba 页已删除「预演/AI 预演」入口（直接校验并保存）；
- 「保存到 Alibaba」→ 二次确认弹窗 → 确认后调用 pipeline(dry_run=False)；
- 属性预设/阶梯价改为可视化编辑，不再让用户手写 JSON；
- Alibaba 平台「启动任务」守卫：不跑速卖通管线，打开批量运行与结果。
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox, build_app, walk  # noqa: E402

import flet as ft  # noqa: E402
from aeclone.services.alibaba_pipeline import AlibabaRunResult, AlibabaStep  # noqa: E402

from aeclone.ui.alibaba_editor import remember_product


def open_cached_item(app):
    pid = "220114598434958580"
    app.cfg.set("alibaba_test_product_id", pid)
    remember_product(app, {"id": pid, "subject": "Example product"})
    app._switch(12)


BUTTONS = (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton)


SAVE_TEXTS = ("保存到 Alibaba", "保存草稿", "移入待发布", "提交审核 / 发布", "定时发布")


def find_save_button(app):
    for text in SAVE_TEXTS:
        btn = find_button(app, text)
        if btn is not None:
            return btn
    return None


def find_button(app, text):
    for c in walk(app.content.controls):
        if isinstance(c, BUTTONS) and str(getattr(c, "text", "") or "") == text:
            return c
    return None


def texts(app):
    return {c.value for c in walk(app.content.controls)
            if isinstance(c, ft.Text) and c.value}


def wait_until(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def fake_result(**over):
    res = AlibabaRunResult(
        product_id="220114598434958580", ok=True, preview=True,
        subject="Customized Fiberglass Container Pool",
        keywords=["fiberglass pool", "container pool", "villa pool"],
        issues=[], steps=[AlibabaStep("连接 Alibaba", True, "Cookie 可用"),
                          AlibabaStep("保存前校验", True, "必填项全部满足")])
    res.__dict__.update(over)
    return res


def test_no_preview_entries_and_visual_editors():
    """Alibaba 页不再有预演入口；JSON 输入改为可视化编辑。"""
    with sandbox():
        app = build_app()
        open_cached_item(app)
        contents = list(walk(app.content.controls))
        buttons = [str(getattr(c, "text", "") or "") for c in contents]
        labels = [str(getattr(c, "label", "") or "") for c in contents]
        tabs = [str(getattr(t, "text", "") or "") for c in contents
                if isinstance(c, ft.Tabs) for t in c.tabs]
        values = " ".join(str(getattr(c, "value", "") or "") for c in contents)

        assert "预演（不保存）" not in buttons, buttons
        assert "AI 预演（不保存）" not in buttons, buttons
        assert "保存单品" in tabs, tabs
        assert "添加自定义属性" in buttons
        assert "添加价格档位" in buttons
        # 不再出现要求手写 JSON 的输入框
        assert not any("属性预设 JSON" in x for x in labels), labels
        assert not any("阶梯价 JSON" in x for x in labels), labels
        assert "预演（不保存）" not in values
    return True


def test_save_button_requires_confirmation_then_calls_real_pipeline():
    with sandbox(threads=True):
        app = build_app()
        open_cached_item(app)
        calls = []

        def fake(pid, cfg, **kw):
            calls.append(dict(pid=pid, **kw))
            if kw.get("dry_run"):
                return fake_result()
            return fake_result(preview=False, ok=True, saved=True,
                               save_result={"code": 0, "data": {"msg": "您的产品编辑成功！"}},
                               steps=[AlibabaStep("保存到 Alibaba", True,
                                                  "保存草稿成功：您的产品编辑成功！")])

        with patch("aeclone.services.alibaba_publish.prepare_save",
                   return_value=({"id": "220114598434958580"}, {}, [], [])), \
             patch("aeclone.services.alibaba_pipeline.run_alibaba_product", side_effect=fake):
            btn = find_save_button(app)
            assert btn is not None, "找不到保存按钮"
            btn.on_click(None)
            # 先出现二次确认弹窗
            assert wait_until(lambda: getattr(app, "_dlg", None) is not None)
            dlg = app._dlg
            confirm = next(c for c in dlg.actions
                           if isinstance(c, ft.ElevatedButton) and c.text == "确认保存")
            # 取消不应保存
            dlg.actions[0].on_click(NS(control=dlg.actions[0]))
            assert not any(c["dry_run"] is False for c in calls), "点取消不应真实保存"
            # 再点一次保存 + 确认
            btn.on_click(None)
            assert wait_until(lambda: getattr(app, "_dlg", None) is not None)
            app._dlg.actions[1].on_click(NS(control=app._dlg.actions[1]))
            assert wait_until(lambda: any("已保存" in t for t in texts(app))), texts(app)
        assert any(c["dry_run"] is False and c["op"] == "1" for c in calls), calls
    return True


def test_start_guard_redirects_alibaba_platform():
    with sandbox():
        app = build_app()
        app._switch(0)
        app.platform_loaded = True
        app._toggle_run()
        assert app._page_idx == 33, app._page_idx
    return True


def test_manual_save_keeps_copy_actions_separate():
    with sandbox(threads=True):
        app = build_app()
        open_cached_item(app)
        assert not any(isinstance(c, ft.Switch) and "本次保存使用 AI" in str(c.label)
                       for c in walk(app.content.controls))
        calls = []
        fake = fake_result(preview=False, ok=True, saved=True,
                           save_result={"code": 0, "data": {"msg": "您的产品编辑成功！"}})

        def run(pid, cfg, **kw):
            calls.append(dict(pid=pid, **kw))
            return fake

        with patch("aeclone.services.alibaba_publish.prepare_save",
                   return_value=({"id": "220114598434958580"}, {}, [], [])), \
             patch("aeclone.services.alibaba_pipeline.run_alibaba_product", side_effect=run):
            btn = find_save_button(app)
            assert btn is not None, "找不到保存按钮"
            btn.on_click(None)
            assert wait_until(lambda: getattr(app, "_dlg", None) is not None)
            app._dlg.actions[1].on_click(NS(control=app._dlg.actions[1]))
            assert wait_until(lambda: any("已保存" in t for t in texts(app))), texts(app)
        assert calls and calls[0]["dry_run"] is False
        assert calls[0]["use_ai"] is False, calls
    return True


def test_open_by_id_sets_current_product():
    with sandbox(threads=True):
        class FakeClient:
            def __init__(self, *a, **k):
                pass
            def product_detail(self, pid):
                return {"id": pid, "subject": "Fake Pool", "categoryId": "202107201"}
            def list_products(self, *a, **k):
                return {"data": {"page": {"list": [], "totalSize": 0, "totalPage": 1}}}
        # 必须在页面构建前替换，因为 _alibaba_cards 在构建时 import AlibabaClient
        with patch("aeclone.integrations.alibaba.AlibabaClient", FakeClient):
            app = build_app()
            app.cfg.set("alibaba_cookies", "dxm_i=test; JSESSIONID=test")
            app._switch(12)
            tf = next((c for c in walk(app.content.controls)
                       if isinstance(c, ft.TextField)
                       and "按商品 ID 打开" in str(getattr(c, "label", ""))), None)
            assert tf is not None, "找不到按 ID 打开输入框"
            tf.value = "220114598434958580"
            btn = find_button(app, "按 ID 打开")
            assert btn is not None
            btn.on_click(None)
            assert wait_until(lambda: str(app.cfg.get("alibaba_test_product_id")) == "220114598434958580")
            assert wait_until(lambda: any("商品已载入" in t for t in texts(app))), texts(app)
    return True


def test_page12_has_score_gate_and_category_scope():
    """Alibaba 刊登页必须有评分门禁（≥4.5）与经营类目经营范围预检入口。"""
    with sandbox():
        app = build_app()
        app._switch_platform("alibaba")
        open_cached_item(app)
        contents = list(walk(app.content.controls))
        values = [str(getattr(c, "value", "") or "") for c in contents]
        labels = [str(getattr(c, "label", "") or "") for c in contents]
        buttons = [str(getattr(c, "text", "") or "") for c in contents]
        joined = " ".join(values)
        assert "刊登评分与发布门禁" in joined
        assert "评分并诊断" in buttons
        assert "检查类目经营范围" in buttons
        assert "提交审核 / 发布" in buttons
        assert "移入待发布" in buttons
        assert "定时发布" in buttons
        assert "定时发布时间（如 2030-01-01 09:00）" in labels


def test_publish_queue_has_failure_review():
    """Alibaba 发布队列必须能查看「发布失败」及平台原因（用户要求按真实列表核对）。"""
    with sandbox():
        app = build_app()
        app._switch_platform("alibaba")
        app._switch(8)
        contents = list(walk(app.content.controls))
        buttons = [str(getattr(c, "text", "") or "") for c in contents]
        joined = " ".join(str(getattr(c, "value", "") or "") for c in contents)
        assert "分析发布失败" in buttons
        assert "发布失败" in joined
        assert any("发布失败" in t for t in buttons)


def main():
    tests = [test_no_preview_entries_and_visual_editors,
             test_save_button_requires_confirmation_then_calls_real_pipeline,
             test_start_guard_redirects_alibaba_platform,
             test_manual_save_keeps_copy_actions_separate,
             test_open_by_id_sets_current_product,
             test_page12_has_score_gate_and_category_scope,
             test_publish_queue_has_failure_review]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except Exception as ex:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {fn.__name__}: {type(ex).__name__}: {ex}")
    print(f"结果: {len(tests) - failures} 通过, {failures} 失败")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
