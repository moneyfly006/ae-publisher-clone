"""Visual editor for the image rules applied to each collected product."""
import json

import flet as ft

from ..services.platform_media import GROUPS, batch_rules
from .media_actions import OPERATIONS, PROMPTS
from .structured_fields import text_field


def controls(app, platform):
    from . import app as ui
    from .collect_workflow import write
    from ..services.collect_store import scope

    groups = GROUPS[platform]
    prefix = platform + "_collect_"
    source_scope = scope(app.cfg, platform)
    box = ft.Column(spacing=8)
    notice = ft.Text("", size=12, color=ui.MUTED)
    try:
        rules = batch_rules(app.cfg, platform)
    except (ValueError, TypeError):
        rules = []
        notice.value = "旧图片规则无法读取，请重新设置后保存。"

    def render():
        box.controls = []
        for rule in rules:
            group = ft.Dropdown(label="图片分组", value=rule["group"], width=230,
                options=[ft.dropdown.Option(k, v.label) for k, v in groups.items()])
            position = ft.Dropdown(label="处理位置", width=170, value=str(rule["position"]))
            operation = ft.Dropdown(label="图片处理预设", width=195, value=rule["operation"],
                options=[ft.dropdown.Option(k, label) for k, label in OPERATIONS.items()])
            prompt = text_field("自动图片处理提示词", rule["prompt"], width=520)
            prompt.multiline = True
            prompt.min_lines, prompt.max_lines = 2, 6
            prompt_preset = ft.Dropdown(label="提示词预设", width=210,
                bgcolor=ui.CARD_2, border_color=ui.CARD_2, color=ui.TEXT, options=[])

            def set_positions(control, key):
                control.options = [ft.dropdown.Option("all", "该组全部图片")] + [
                    ft.dropdown.Option(str(i), f"第 {i} 张") for i in range(1, groups[key].limit + 1)]

            set_positions(position, rule["group"])

            def change_group(e, row=rule, target=position):
                row["group"], row["position"] = e.control.value, "1"
                set_positions(target, row["group"])
                target.value = "1"
                save(e)

            def change_operation(e, row=rule, field=prompt):
                row["operation"] = e.control.value
                row["prompt"] = field.value = PROMPTS[e.control.value]
                save(e)

            def remove(e, row=rule):
                rules.remove(row)
                render()
                save(e)

            group.on_change = change_group
            operation.on_change = change_operation
            def edit(e, row=rule, key="position"):
                row[key] = e.control.value
                save(e)

            position.on_change = edit
            prompt.on_change = lambda e, row=rule: edit(e, row, "prompt")
            try:
                from .prompt_presets import attach_dropdown
                # 同一个下拉框：内置图片提示词 + 我的预设 + 添加自定义；选择后立即写回规则。
                attach_dropdown(app, prompt_preset, key="image_rule:" + platform,
                                field=prompt,
                                builtins={label: PROMPTS.get(op, "") for op, label in OPERATIONS.items()},
                                labels={label: label for label in OPERATIONS.values()},
                                apply_prompt=lambda text, row=rule: (row.__setitem__("prompt", text), save(None)))
            except Exception:
                pass
            box.controls.append(ft.Container(padding=10, border_radius=8, bgcolor=ui.CARD_2,
                content=ft.Column([ft.Row([group, position, operation, prompt_preset,
                    ft.IconButton(ft.Icons.DELETE_OUTLINE,
                    tooltip="删除自动图片规则", on_click=remove)], wrap=True), prompt], spacing=8)))
        app._safe_update()

    def add(e):
        rules.append(dict(group="main", position="1", operation="white", prompt=PROMPTS["white"]))
        render()
        save(None)   # 新增规则也立即落盘，下次打开不丢

    def save(e):
        try:
            if scope(app.cfg, platform) != source_scope:
                raise ValueError("店铺或站点已切换，请重新打开图片页")
            if any(not str(row["prompt"] or "").strip() for row in rules):
                raise ValueError("请填写每条规则的提示词")
            for key in groups:
                targets = [row["position"] for row in rules if row["group"] == key]
                if len(set(targets)) < len(targets) or ("all" in targets and len(targets) > 1):
                    raise ValueError(f"「{groups[key].label}」有重叠规则，请为每张图片保留一个规则")
            write(app, prefix + "image_rules", json.dumps(rules, ensure_ascii=False))
            notice.value, notice.color = "已保存自动图片规则", ui.SUCCESS_C
        except (ValueError, OSError) as exc:
            notice.value, notice.color = str(exc), ui.DANGER_C
        app._safe_update()

    render()
    return ft.Column([box, ft.Row([
        ft.TextButton("添加自动图片规则", icon=ft.Icons.ADD, on_click=add),
        ft.OutlinedButton("保存图片规则", on_click=save), notice], wrap=True)], spacing=10)
