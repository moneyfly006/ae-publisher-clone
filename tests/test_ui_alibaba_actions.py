"""Alibaba 刊登页关键按钮的行为验证（隔离沙箱，不联网、不真实保存）。

覆盖：
- 「预演（不保存）」→ 调用 pipeline(dry_run=True) 并渲染分步结果；
- 「保存到 Alibaba」→ 二次确认弹窗 → 确认后调用 pipeline(dry_run=False)；
- Alibaba 平台「启动任务」守卫：不跑速卖通管线，跳 12 号页并提示。
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

BUTTONS = (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton)


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


def test_preview_button_runs_pipeline_and_renders_steps():
    with sandbox(threads=True):
        app = build_app()
        app._switch(12)
        app.cfg.set("alibaba_test_product_id", "220114598434958580")
        calls = []

        def fake(pid, cfg, **kw):
            calls.append(dict(pid=pid, **kw))
            return fake_result()

        with patch("aeclone.services.alibaba_pipeline.run_alibaba_product", side_effect=fake):
            btn = find_button(app, "AI 预演（不保存）")
            assert btn is not None, "找不到 AI 预演按钮"
            btn.on_click(None)
            assert wait_until(lambda: any("预演完成" in t for t in texts(app))), texts(app)
        assert calls and calls[0]["dry_run"] is True
        # AI 开关默认关闭；预演应显式传 use_ai=False（不产生付费调用）
        assert calls[0]["use_ai"] is False
    return True


def test_save_button_requires_confirmation_then_calls_real_pipeline():
    with sandbox(threads=True):
        app = build_app()
        app._switch(12)
        app.cfg.set("alibaba_test_product_id", "220114598434958580")
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
            btn = find_button(app, "保存到 Alibaba")
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
        app._start()
        assert app._page_idx == 12, app._page_idx
        msg = str(getattr(app.page.snack_bar.content, "value", "") or "")
        assert "Alibaba 商品请" in msg, msg
    return True


def test_ai_save_button_confirms_and_calls_pipeline_with_ai():
    with sandbox(threads=True):
        app = build_app()
        app._switch(12)
        app.cfg.set("alibaba_test_product_id", "220114598434958580")
        # 打开 AI 开关
        for c in walk(app.content.controls):
            if isinstance(c, ft.Switch) and "使用 AI 生成" in str(getattr(c, "label", "")):
                c.value = True
                break
        calls = []
        fake = fake_result(preview=False, ok=True, saved=True,
                           save_result={"code": 0, "data": {"msg": "您的产品编辑成功！"}})

        def run(pid, cfg, **kw):
            calls.append(dict(pid=pid, **kw))
            return fake

        with patch("aeclone.services.alibaba_pipeline.run_alibaba_product", side_effect=run):
            btn = find_button(app, "AI 生成并保存")
            assert btn is not None, "找不到 AI 生成并保存按钮"
            btn.on_click(None)
            assert wait_until(lambda: getattr(app, "_dlg", None) is not None)
            app._dlg.actions[1].on_click(NS(control=app._dlg.actions[1]))
            assert wait_until(lambda: any("已保存" in t for t in texts(app))), texts(app)
        assert calls and calls[0]["dry_run"] is False
        assert calls[0]["use_ai"] is True, calls
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
            assert wait_until(lambda: any("已打开商品" in t for t in texts(app))), texts(app)
    return True


def main():
    tests = [test_preview_button_runs_pipeline_and_renders_steps,
             test_save_button_requires_confirmation_then_calls_real_pipeline,
             test_start_guard_redirects_alibaba_platform,
             test_ai_save_button_confirms_and_calls_pipeline_with_ai,
             test_open_by_id_sets_current_product]
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
