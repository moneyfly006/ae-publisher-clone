"""Confirmed single-field AI actions for the current product."""
from copy import deepcopy
import threading

import flet as ft


def button(app, platform, label, field, source, generate, apply):
    from ..config import AppConfig
    from ..services.collect_store import scope

    action = ft.TextButton("AI 生成" + label, icon=ft.Icons.AUTO_AWESOME)

    def context():
        return (scope(app.cfg, platform), str(getattr(app.cfg, platform + "_test_product_id", "") or ""),
                getattr(app, "_page_idx", None), getattr(getattr(app, "active_platform", None), "id", ""))

    def start(e):
        product = deepcopy(source())
        if not (product.get("idStr") or product.get("id")):
            app.toast("请先载入当前商品", False)
            return
        snapshot, old_value = context(), field.value

        def confirmed():
            if action.disabled or context() != snapshot or field.value != old_value:
                app.toast("商品或字段已变化，请重新确认", False)
                return
            cfg = AppConfig(deepcopy(app.cfg.as_dict()))
            action.disabled = True
            app._safe_update()

            def work():
                try:
                    value = generate(product, cfg)
                    if context() != snapshot or field.value != old_value:
                        app.toast("商品或字段已变化，AI 结果未回填", False)
                        return
                    if not str(value or "").strip():
                        raise ValueError("AI 返回空内容")
                    field.value = apply(value)
                    app.toast(label + "已生成，请复核后保存商品")
                except Exception as exc:
                    app.toast(f"{label}生成失败：{exc}", False)
                finally:
                    action.disabled = False
                    app._safe_update()

            try:
                threading.Thread(target=work, daemon=True).start()
            except RuntimeError as exc:
                action.disabled = False
                app.toast(f"AI 任务启动失败：{exc}", False)
                app._safe_update()

        app._confirm("确认使用 AI（付费）", f"将生成「{label}」，结果填入当前商品对应字段。", confirmed)

    action.on_click = start
    return action


def sku_button(app, platform, field, source, apply, *, index=None, label=" SKU"):
    from ..services.collect_ai import generate_sku_code
    return button(app, platform, label, field, source,
                  lambda product, cfg: generate_sku_code(platform, product, cfg, index=index),
                  apply)
