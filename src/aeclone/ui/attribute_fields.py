"""Visual editing for TikTok and Shopee native product attributes."""
from copy import deepcopy

import flet as ft

from .structured_fields import structured_fields, text_field


def _value(data, *keys):
    return next((data[key] for key in keys if data.get(key) not in (None, "")), "")


def _definitions(rows, platform):
    from ..services.tiktok_payload import ATTR_BRAND_ID, normalize_category_attribute
    from .shopee import rows as shopee_rows

    def flag(value):
        return str(value).lower() in ("true", "1")

    definitions = {}
    for raw in rows:
        if platform == "tiktok":
            source = dict(raw)
            source["attrId"] = _value(raw, "attrId", "attribute_id", "attributeId", "id")
            source["attrName"] = _value(raw, "attrName", "attributeName", "name")
            source.setdefault("required", raw.get("is_required"))
            source["values"] = _value(raw, "values", "attributeValueList", "attrValues", "options")
            item = normalize_category_attribute(source)
            if not item or item["attrId"] == ATTR_BRAND_ID:
                continue
            key, name, label = item["attrId"], item["attrName"], item["label"]
            native_id = key
            required = item["isMandatory"] and not item["isConditional"]
            multi, custom = item["isMultipleSelected"], item["isCustomized"]
            options = [(v["id"], v["name"], v["transName"] or v["name"]) for v in item["values"]]
        else:
            native_id = _value(raw, "attribute_id", "attributeId", "id")
            key = str(native_id)
            name = str(_value(raw, "attributeName", "nameEn", "name") or key)
            label = str(_value(raw, "display_attribute_name", "displayAttributeName") or name)
            required = flag(_value(raw, "isMandatory", "required", "mandatory", "is_mandatory"))
            input_type = str(_value(raw, "inputType", "input_type")).upper()
            multi = flag(_value(raw, "isMultipleSelected", "isMultiple", "multiple")) or "MULTIPLE" in input_type
            custom = flag(_value(raw, "isCustomized", "is_customized")) or "COMBO" in input_type
            options = []
            for row in shopee_rows(_value(raw, "attributeValueList", "attribute_value_list", "valueList", "values")):
                value_id = _value(row, "value_id", "valueId", "id")
                value_name = str(_value(row, "original_value_name", "originalValueName", "valueName", "name_en", "name") or value_id)
                options.append((value_id, value_name, str(_value(row, "display_value_name", "displayValueName") or value_name)))
        if key:
            definitions[key] = (name, label, required, multi, custom, [v for v in options if v[1]], native_id)
    return definitions


def native_attribute_fields(initial, rows, platform, update, *, show_required=False):
    """Return category-guided controls and a reader for their platform payload."""
    from . import app as ui

    tiktok = platform == "tiktok"
    id_key, values_key = ("attributeId", "values") if tiktok else ("attribute_id", "attribute_value_list")
    value_id_key, value_name_key = ("valueId", "valueName") if tiktok else ("value_id", "original_value_name")
    custom_id = "" if tiktok else 0
    definitions = _definitions(rows, platform)
    fields = ft.Column(spacing=10)
    readers = {}
    chooser = ft.Dropdown(label="添加类目属性", width=300, color=ui.TEXT, bgcolor=ui.CARD_2)
    add_button = ft.TextButton("添加属性", icon=ft.Icons.ADD)
    chooser_row = ft.Row([chooser, add_button], wrap=True)

    def sync_options():
        active = {key for key, _ in readers.values()}
        chooser.options = [ft.dropdown.Option(key, row[1]) for key, row in definitions.items() if key not in active]
        chooser.value = None
        chooser_row.visible = bool(chooser.options)

    def value_field(label, original, options, custom, required):
        vid = _value(original, value_id_key, "valueId", "value_id", "id")
        name = _value(original, value_name_key, "valueName", "originalValueName", "name", "value")
        text = text_field(label if not options else "自定义属性值", name, width=240)
        loaded = bool(original)
        if required and not loaded and not options:
            text.border_color = ui.WARN_C
        select = None
        initial_choice = None
        if options:
            choices = [ft.dropdown.Option(str(i), display) for i, (_, _, display) in enumerate(options)]
            initial_choice = next((str(i) for i, (key, value, _) in enumerate(options)
                                   if (vid != "" and str(vid) == str(key)) or
                                   (vid == "" and name and name == value)), None)
            if loaded and initial_choice is None:
                initial_choice = "current"
                choices.append(ft.dropdown.Option("current", str(name if name != "" else vid) or "已保存的值"))
            if custom:
                choices.append(ft.dropdown.Option("custom", "自定义值"))
            select = ft.Dropdown(label=label, width=265, options=choices, value=initial_choice,
                                 color=ui.TEXT, bgcolor=ui.CARD_2,
                                 border_color=ui.WARN_C if required and not loaded else ui.BORDER)

            def choice_changed(e):
                text.visible = select.value == "custom"
                update()

            select.on_change = choice_changed
            text.visible = False

        def read():
            if select is not None and select.value == initial_choice:
                return deepcopy(original) if loaded else None
            if select is not None and select.value and select.value != "custom":
                pick_id, pick_value, _ = options[int(select.value)]
            else:
                if select is None and loaded and str(text.value or "") == str(name):
                    return deepcopy(original)
                pick_id, pick_value = custom_id, str(text.value or "").strip()
                if not pick_value and select is not None and select.value == "custom":
                    raise ValueError(f"请填写「{label}」的自定义属性值")
                if not pick_value:
                    return None
            result = deepcopy(original)
            native_id = next((key for key in (value_id_key, "valueId", "value_id", "id") if key in result), value_id_key)
            native_name = next((key for key in (value_name_key, "valueName", "originalValueName", "name", "value")
                                if key in result), value_name_key)
            result.update({native_id: pick_id if pick_id != "" else custom_id, native_name: pick_value})
            return result

        return [*([select] if select is not None else []), text], read

    def append(key, original):
        definition = definitions.get(key)
        values = original.get(values_key, []) if isinstance(original, dict) else None
        if not definition or not isinstance(values, list) or any(not isinstance(v, dict) for v in values) or (
                values_key not in original and any(k in original for k in ("value", "valueName", "attribute_value_list", "values"))):
            label = str(_value(original, "attributeName", "attrName", "name") or key) if isinstance(original, dict) else "已保存属性"
            group, read = structured_fields(original, label or "已保存属性", update)
            content = ft.ExpansionTile(title=ft.Text(label or "已保存属性"), controls=[group])
        else:
            name, label, required, multi, custom, options, native_id = definition
            value_rows = ft.Column(spacing=6)
            value_readers = {}
            field_label = label + ("（必填）" if show_required and required else "")

            def append_value(item):
                controls, get = value_field(field_label, item, options, custom, show_required and required)

                def remove_value(e):
                    value_rows.controls.remove(row)
                    value_readers.pop(row)
                    if not value_readers:
                        append_value({})
                    update()

                row = ft.Row([*controls, *([ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除此值",
                               on_click=remove_value)] if multi or len(values) > 1 else [])], wrap=True, spacing=6)
                value_rows.controls.append(row)
                value_readers[row] = get

            for value in values or [{}]:
                append_value(value)

            def add_value(e):
                append_value({})
                update()

            content = ft.Column([value_rows, *([ft.TextButton("添加值", icon=ft.Icons.ADD, on_click=add_value)] if multi else [])], spacing=2)

            def read():
                current = [value for get in value_readers.values() if (value := get()) is not None]
                if not original and not current:
                    return None
                if original and current == values:
                    return deepcopy(original)
                entry = deepcopy(original) or {id_key: native_id}
                if tiktok and not original:
                    entry["attributeName"] = name
                entry[values_key] = current
                return entry

        def remove(e):
            fields.controls.remove(block)
            readers.pop(block)
            sync_options()
            update()

        block = ft.Row([ft.Container(content=content, expand=True),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="移除此属性", on_click=remove)],
                       vertical_alignment=ft.CrossAxisAlignment.START, spacing=4)
        fields.controls.append(block)
        readers[block] = (key, read)

    for item in initial:
        key = str(_value(item, id_key, "attributeId", "attribute_id", "attrId", "id")) if isinstance(item, dict) else ""
        append(key, item)
    if show_required:
        existing = {key for key, _ in readers.values()}
        for key, definition in definitions.items():
            if definition[2] and key not in existing:
                append(key, {})

    def add(e):
        if chooser.value in definitions:
            append(chooser.value, {})
            sync_options()
            update()

    def read_all():
        return [row for _, read in readers.values() if (row := read()) is not None]

    add_button.on_click = add
    sync_options()
    return ft.Column([chooser_row, fields], spacing=10), read_all
