"""提示词自定义预设控件：下拉选择/保存命名/记住上次选择。"""
from __future__ import annotations

import flet as ft


def bar(app, *, key: str, prompt_key: str = "", field: ft.TextField,
        label: str = "我的提示词预设", builtins: dict | None = None, width: int = 300,
        apply_prompt=None):
    """返回一行「选择预设 + 保存当前提示词」控件。

    - 内置预设和自定义预设都立即写入 field / cfg；
    - 保存时会命名为一个新预设，并自动选中；
    - 选择会写入 prompt_presets_data.choices，重启后恢复。
    """
    from . import app as ui
    from ..services import prompt_presets as P
    from ..config import save_config
    builtins = dict(builtins or {})
    try:
        app._attach_clipboard_shortcuts(field)
    except Exception:
        pass

    def names():
        return [str(x) for x in builtins] + [str(x) for x in P.custom_presets(app.cfg, key)]

    dd = ft.Dropdown(label=label, width=width, dense=True, bgcolor=ui.CARD_2,
                     border_color=ui.CARD_2, color=ui.TEXT, options=[])
    status = ui.muted("", 10.5)

    def refresh(select: str = "") -> None:
        opts = [ft.dropdown.Option("", "（当前编辑）")]
        for name in builtins:
            opts.append(ft.dropdown.Option("builtin:" + name, "内置 · " + name))
        for name in P.custom_presets(app.cfg, key):
            opts.append(ft.dropdown.Option("custom:" + name, "★ " + name))
        opts.append(ft.dropdown.Option("__add__", "＋ 添加自定义提示词…"))
        dd.options = opts
        want = select or P.choice(app.cfg, key)
        match = next((o.key for o in opts if o.key in (want, "builtin:" + want, "custom:" + want)), "")
        dd.value = match
        status.value = ("下次打开仍会选这个预设" if match else "当前为手动编辑，选择预设后会被记住")

    def apply(e=None) -> None:
        raw = str(dd.value or "")
        if not raw:
            return
        if raw == "__add__":
            add_prompt()
            return
        kind, _, name = raw.partition(":")
        prompt = builtins.get(name, "") if kind == "builtin" else P.custom_presets(app.cfg, key).get(name, "")
        if not prompt:
            return
        field.value = prompt
        if callable(apply_prompt):
            apply_prompt(prompt)
        if prompt_key:
            app.cfg.set(prompt_key, prompt)
            save_config(app.cfg)
        P.set_choice(app.cfg, key, name, save_config)
        status.value = f"已选择：{name}（已记住，下次打开仍是它）"
        app.toast(f"已套用预设：{name}", True)
        try:
            app._safe_update()
        except Exception:
            pass

    dd.on_change = apply

    def add_prompt(e=None) -> None:
        name = ft.TextField(label="预设名称", width=320, bgcolor=ui.CARD_2,
                            border_color=ui.CARD_2, color=ui.TEXT,
                            hint_text="例如：我的机械标题风格")
        prompt_tf = ft.TextField(
            label="提示词内容", width=520, multiline=True, min_lines=5, max_lines=12,
            bgcolor=ui.CARD_2, border_color=ui.CARD_2, color=ui.TEXT,
            value=str(field.value or ""))
        hint = ui.muted("直接在这里输入/修改提示词，保存后可从同一个下拉框筛选调用。", 11)

        def do_save(ev=None) -> None:
            if not P.save_custom(app.cfg, key, name.value, prompt_tf.value, save_config):
                app.toast("请输入预设名称和提示词", False)
                return
            app._close_dlg(dlg)
            refresh("custom:" + str(name.value or "").strip())
            app.toast("提示词预设已保存，下次可直接选择", True)
            app._safe_update()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("添加自定义提示词"),
            content=ft.Column([hint, name, prompt_tf], spacing=8, width=520),
            actions=[ft.TextButton("取消", on_click=lambda ev: app._close_dlg(dlg)),
                     ft.ElevatedButton("保存", bgcolor=ui.ACCENT, color=ui.ON_ACCENT, on_click=do_save)])
        app._show_dlg(dlg)

    refresh()
    return ft.Column([dd, status], spacing=3)


def attach_dropdown(app, dd, *, key: str, prompt_key: str = "", field=None,
                    builtins: dict | None = None, labels: dict | None = None,
                    on_builtin=None, apply_prompt=None):
    """把已有的预设下拉框扩展为：内置 + 我的预设 + 添加自定义，并记住选择。

    不额外增加独立输入框/第二排按钮；用户直接在这个下拉框里添加、筛选、调用。
    """
    from . import app as ui
    from ..services import prompt_presets as P
    from ..config import save_config
    builtins = dict(builtins or {})
    labels = dict(labels or {})
    try:
        app._attach_clipboard_shortcuts(field)
    except Exception:
        pass

    def current_prompt() -> str:
        if field is not None:
            return str(getattr(field, "value", "") or "")
        return str(getattr(app.cfg, prompt_key, "") or "")

    def refresh(select: str = "") -> None:
        opts = [ft.dropdown.Option("", "（当前编辑）")]
        for name in builtins:
            opts.append(ft.dropdown.Option("builtin:" + name,
                                           "内置 · " + str(labels.get(name, name))))
        for name in P.custom_presets(app.cfg, key):
            opts.append(ft.dropdown.Option("custom:" + name, "★ " + name))
        opts.append(ft.dropdown.Option("__add__", "＋ 添加自定义提示词…"))
        dd.options = opts
        want = select or P.choice(app.cfg, key)
        dd.value = next((o.key for o in opts if o.key in (want, "builtin:" + want, "custom:" + want)), "")
        try:
            app._safe_update()
        except Exception:
            pass

    def add_prompt() -> None:
        name = ft.TextField(label="预设名称", width=320, bgcolor=ui.CARD_2,
                            border_color=ui.CARD_2, color=ui.TEXT,
                            hint_text="例如：我的机械标题风格")
        prompt_tf = ft.TextField(label="提示词内容", width=520, multiline=True,
                                 min_lines=5, max_lines=12, bgcolor=ui.CARD_2,
                                 border_color=ui.CARD_2, color=ui.TEXT,
                                 value=current_prompt())
        hint = ui.muted("直接输入提示词，保存后从同一个下拉框筛选调用。", 11)

        def do_save(ev=None) -> None:
            if not P.save_custom(app.cfg, key, name.value, prompt_tf.value, save_config):
                app.toast("请输入预设名称和提示词", False)
                return
            app._close_dlg(dlg)
            refresh("custom:" + str(name.value or "").strip())
            app.toast("提示词预设已保存，下次可直接选择", True)
            app._safe_update()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("添加自定义提示词"),
            content=ft.Column([hint, name, prompt_tf], spacing=8, width=520),
            actions=[ft.TextButton("取消", on_click=lambda ev: app._close_dlg(dlg)),
                     ft.ElevatedButton("保存", bgcolor=ui.ACCENT, color=ui.ON_ACCENT, on_click=do_save)])
        app._show_dlg(dlg)

    def change(e=None) -> None:
        if getattr(dd, "disabled", False):
            app.toast("预设库未就绪（等待后端 amazon_presets）", False)
            return
        raw = str(dd.value or "")
        if raw == "__add__":
            add_prompt()
            return
        if not raw:
            return
        kind, _, name = raw.partition(":")
        if kind == "builtin":
            if callable(on_builtin):
                text = on_builtin(name)
            else:
                text = builtins.get(name, "")
        else:
            text = P.custom_presets(app.cfg, key).get(name, "")
        if not text:
            return
        if field is not None:
            field.value = text
        if callable(apply_prompt):
            apply_prompt(text)
        if prompt_key:
            app.cfg.set(prompt_key, text)
            save_config(app.cfg)
        P.set_choice(app.cfg, key, name, save_config)
        app.toast(f"已套用预设：{name}", True)
        try:
            app._safe_update()
        except Exception:
            pass

    dd.on_change = change
    refresh()
    return dd
