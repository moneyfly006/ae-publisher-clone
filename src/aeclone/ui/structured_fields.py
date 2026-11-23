"""Form controls for custom attribute values and legacy platform fields."""
import math

import flet as ft


def text_field(label, value="", *, width=220, numeric=False):
    from . import app as ui
    return ft.TextField(label=label, value="" if value is None else str(value), width=width,
                        keyboard_type=ft.KeyboardType.NUMBER if numeric else ft.KeyboardType.TEXT,
                        color=ui.TEXT, bgcolor=ui.CARD_2, border_color=ui.BORDER)


def structured_fields(initial, label, update, *, mapping_labels=("字段名称", "字段值", "添加字段")):
    """Return a field and a reader, preserving value types until explicitly edited."""
    if not isinstance(initial, (dict, list)):
        if isinstance(initial, bool):
            switch = ft.Switch(label=label, value=initial)
            return switch, lambda: bool(switch.value)
        if initial is None:
            field = text_field(label, width=220)
            field.hint_text = "未填写"
            return field, lambda: field.value or None
        numeric = isinstance(initial, (int, float))
        field = text_field(label, initial, numeric=numeric)

        def read():
            if not numeric:
                return field.value or ""
            try:
                value = type(initial)(field.value)
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError
                return value
            except (ValueError, TypeError):
                raise ValueError(f"{label}请填写{'整数' if isinstance(initial, int) else '数字'}") from None

        return field, read

    mapping = isinstance(initial, dict)
    entries = ft.Column(spacing=8)
    readers = {}

    def append(key, value):
        name = text_field(mapping_labels[0], key, width=185) if mapping else None
        content, read = structured_fields(value, mapping_labels[1] if mapping else "项目值", update)

        def remove(e):
            entries.controls.remove(row)
            readers.pop(row)
            update()

        row = ft.Row([*([name] if name else []), content,
                      ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除此项", on_click=remove)],
                     wrap=True, vertical_alignment=ft.CrossAxisAlignment.START, spacing=6)
        entries.controls.append(row)
        readers[row] = (name, key, read)

    for key, value in (initial.items() if mapping else enumerate(initial)):
        append(key, value)

    types = {"text": ("文本", ""), "integer": ("整数", 0), "number": ("小数", 0.0), "bool": ("开关", False),
             "list": ("列表", []), "group": ("字段组", {})}
    kind = ft.Dropdown(label="新项目类型", value="text", width=140,
                       options=[ft.dropdown.Option(k, title) for k, (title, _) in types.items()])

    def add(e):
        append(None, types[kind.value or "text"][1])
        update()

    def read_all():
        if not mapping:
            return [read() for _, _, read in readers.values()]
        result = {}
        for name, old_key, read in readers.values():
            key = name.value if name.value == old_key else str(name.value or "").strip()
            if not key and old_key != "":
                raise ValueError("请填写字段名称")
            if key in result:
                raise ValueError(f"字段名称重复：{key}")
            result[key] = read()
        return result

    return ft.Column([*([ft.Text(label, weight=ft.FontWeight.W_500)] if label else []), entries,
                      ft.Row([kind, ft.TextButton(mapping_labels[2] if mapping else "添加项目", icon=ft.Icons.ADD,
                                                 on_click=add)], wrap=True)], spacing=8), read_all
