"""Visual editors for per-shop batch attributes, pricing, packaging and stock."""
from copy import deepcopy
import json
import threading

import flet as ft

from .attribute_fields import native_attribute_fields
from .structured_fields import structured_fields, text_field


def _extra_fields(data, managed, update):
    extra = {k: v for k, v in data.items() if k not in managed}
    fields, read = structured_fields(extra, None, update)

    def get():
        values = read()
        conflict = managed.intersection(values)
        if conflict:
            raise ValueError("请在上方修改此字段：" + "、".join(sorted(conflict)))
        return values

    return ft.ExpansionTile(title=ft.Text("其他已保存字段" if extra else "其他字段"), controls=[fields]), get


def _row_table(initial, columns, maximum, label, app, *, aliases=None):
    aliases = aliases or {}
    rows = ft.Column(spacing=8)
    readers = {}

    def append(source):
        controls = {}
        managed = {key for key, _, _ in columns} | set(aliases.values())
        extra_keys = set(source) - managed
        for key, title, numeric in columns:
            alias = aliases.get(key)
            stored_key = alias if key not in source and alias in source else key
            initial_value = source.get(stored_key, "")
            controls[stored_key] = text_field(title, initial_value, width=180, numeric=numeric)
        if extra_keys:
            extras, extra_values = _extra_fields(source, managed, app._safe_update)

        def remove(e):
            rows.controls.remove(row)
            readers.pop(row)
            app._safe_update()

        row = ft.Column([ft.Row([*controls.values(),
                                 ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除" + label, on_click=remove)],
                                wrap=True, spacing=8), *([extras] if extra_keys else [])], spacing=2)

        def read():
            result = extra_values() if extra_keys else {}
            result.update({alias: source[alias] for alias in aliases.values() if alias in source and alias not in controls})
            for key, control in controls.items():
                original = source.get(key, "")
                value = str(control.value or "").strip()
                result[key] = original if key in source and value == str(original) else value
            return result

        rows.controls.append(row)
        readers[row] = read

    for row in initial:
        append(row)

    def add(e):
        if len(readers) >= maximum:
            app.toast(f"{label}最多{maximum}项", False)
            return
        append({})
        app._safe_update()

    return ft.Column([rows, ft.TextButton("添加" + label, icon=ft.Icons.ADD, on_click=add)], spacing=6), \
        lambda: [read() for read in readers.values()]


def _trade_fields(initial, app):
    package = text_field("包装尺寸（长x宽x高 cm）", initial.get("package_size"), width=275)
    weight = text_field("包装重量（kg）", initial.get("weight"), width=190, numeric=True)
    delivery, delivery_values = _row_table(
        initial.get("deliver_periods", []), (("quantity", "数量上限", True), ("process_period", "发货天数", True)),
        3, "交期档位", app)
    inventory, inventory_values = _row_table(
        initial.get("inventory", []), (("warehouseCode", "仓库编码", False), ("value", "仓库库存", True)),
        30, "仓库库存", app)
    extras, extra_values = _extra_fields(initial, {"package_size", "weight", "deliver_periods", "inventory"}, app._safe_update)

    def read():
        result = extra_values()
        for key, control in (("package_size", package), ("weight", weight)):
            value = str(control.value or "").strip()
            if value:
                original = initial.get(key)
                result[key] = original if value == str(original) else value
        for key, values in (("deliver_periods", delivery_values()), ("inventory", inventory_values())):
            if values or initial.get(key) == []:
                result[key] = values
        return result

    return ft.Column([ft.Row([package, weight], wrap=True, spacing=8), delivery, inventory, extras], spacing=10), read


def editor(app, platform, key, title, empty):
    from . import app as ui
    from .collect_workflow import read_settings, write
    from ..config import AppConfig
    from ..services.collect_automation import settings
    from ..services.collect_store import scope

    original = deepcopy(read_settings(app, platform).get(key, empty))
    def _ctx():
        return (str(getattr(app.cfg, platform + "_shop_id", "") or ""),
                str(getattr(app.cfg, platform + "_site", "") or ""),
                str(getattr(app.cfg, platform + "_mode", "") or ""))
    original_scope = _ctx()
    rows_box = ft.Column(spacing=8)
    status = ft.Text("", color=ui.MUTED, selectable=True)
    native_attributes = key in ("attributes", "product_attributes") and isinstance(empty, list)
    category_key = "categoryId" if platform == "shopee" else "category_id"
    loaded_category = None
    render_request = 0

    def render(value, definitions=()):
        nonlocal read
        if key == "price_tiers":
            fields, read = _row_table(value, (("start_quantity", "起订数量", True), ("price", "单价", True)),
                                      4, "报价档位", app, aliases={"start_quantity": "qty"})
        elif key == "wholesale_trade":
            fields, read = _trade_fields(value, app)
        elif native_attributes:
            fields, read = native_attribute_fields(value, definitions, platform, app._safe_update)
        else:
            fields, read = structured_fields(value, None, app._safe_update,
                                              mapping_labels=("属性名称", "属性值", "添加属性"))
        rows_box.controls = [fields]

    def check_scope():
        nonlocal original_scope
        current = _ctx()
        if current != original_scope:
            if not original_scope[0] and not original_scope[1]:
                original_scope = current   # 页面初始化自动带出站点
                return
            raise ValueError("店铺或站点已切换，请重新打开当前功能页")

    def commit(e):
        nonlocal original
        try:
            check_scope()
            overrides = settings(app.cfg, platform)
            if overrides.get(key, empty) != original:
                raise ValueError("此预设已在其他位置修改，请重新打开当前功能页")
            category = str(overrides.get(category_key, "") or "")
            if loaded_category is not None and loaded_category != category:
                raise ValueError("统一类目已修改，请重新打开当前功能页并加载新类目属性")
            value = read()
            if value:
                overrides[key] = value
            else:
                overrides.pop(key, None)
            raw = json.dumps(overrides, ensure_ascii=False, allow_nan=False)
            draft = app.cfg.as_dict()
            draft[platform + "_collect_overrides"] = raw
            settings(AppConfig(draft), platform)
            write(app, platform + "_collect_overrides", raw)
            original = deepcopy(value)
            status.value = "已保存到当前店铺/站点预设"
            status.color = ui.SUCCESS_C
        except (ValueError, TypeError, OSError) as exc:
            status.value = "未保存：" + str(exc)
            status.color = ui.DANGER_C
        app._safe_update()

    def load(e):
        nonlocal render_request
        render_request += 1
        request = render_request
        status.value, status.color = "正在加载类目属性…", ui.MUTED
        app._safe_update()

        def work():
            nonlocal loaded_category
            try:
                from ..services.collect_automation import client_for
                from .shopee import rows
                check_scope()
                cid = str(settings(app.cfg, platform).get(category_key, "") or "")
                if not cid:
                    raise ValueError("请先填写统一类目 ID")
                client = client_for(platform, app.cfg)
                if platform == "tiktok":
                    response = client.category_attributes(app.cfg.tiktok_site, cid, app.cfg.tiktok_shop_id or "-1")
                else:
                    response = client.attributes(cid, app.cfg.shopee_shop_id)
                if request != render_request:
                    return
                check_scope()
                if str(settings(app.cfg, platform).get(category_key, "") or "") != cid:
                    raise ValueError("统一类目已修改，请重新加载属性")
                definitions = rows(response)
                if loaded_category is not None and loaded_category != cid:
                    if read() != original:
                        raise ValueError("类目已修改，旧类目的属性改动尚未保存。请重新打开当前功能页")
                    render(original, definitions)
                else:
                    render(read(), definitions)
                loaded_category = cid
                status.value = f"已加载 {len(definitions)} 项类目属性" if definitions else "此类目没有返回属性"
                status.color = ui.MUTED
            except Exception as exc:
                if request != render_request:
                    return
                status.value = str(exc)
                status.color = ui.DANGER_C
            app._safe_update()

        threading.Thread(target=work, daemon=True).start()

    read = None
    render(original)
    controls = [ui.h1(title, ui.FS_MD)]
    if native_attributes:
        controls.extend([ui.muted("填写统一类目 ID 后加载属性，选择本批商品共用的属性值。"),
                         ft.TextButton("加载平台属性候选", icon=ft.Icons.REFRESH, on_click=load)])
    controls.extend([rows_box, ft.Row([ft.OutlinedButton("保存" + title, on_click=commit), status], wrap=True)])
    return ui.card(ft.Column(controls, spacing=12))
