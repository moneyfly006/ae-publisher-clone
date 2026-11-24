"""Alibaba 当前商品编辑；草稿按商品 ID 隔离，不把商品编辑写成全局默认值。"""
from copy import deepcopy
import json
import threading

import flet as ft


def copy_page(app):
    from . import app as ui
    if getattr(app, "_collect_editor_only", False):
        pid = str(getattr(app.cfg, "alibaba_test_product_id", "") or "未载入")
        return app._workflow_frame("当前商品文案", "文案生成共用本页提示词。", [panel(app, "copy")],
            3, (1, 30, 31, 32, 3, 5, 4, 33), "当前商品：" + pid)
    from ..config import save_config
    from ..services.alibaba_prompts import resolve_preset
    from .prompt_presets import bar
    preset = resolve_preset(str(getattr(app.cfg, "alibaba_ai_preset", "") or ""))
    prompts = []
    for key, label in (("title_prompt", "标题"), ("detail_prompt", "详情"), ("keyword_prompt", "关键词")):
        editor = ft.TextField(label=label + "提示词", multiline=True, min_lines=4, max_lines=12,
            color=ui.TEXT, bgcolor=ui.CARD_2, border_color=ui.BORDER,
            value=str(getattr(app.cfg, "alibaba_" + key, "") or preset.get(key) or ""))
        def change(e, k="alibaba_" + key):
            app.cfg.set(k, str(e.control.value or ""))
            save_config(app.cfg)
        editor.on_change = change
        _cbar = bar(app, key="alibaba:" + key, prompt_key="alibaba_" + key,
                    field=editor, label=label + "我的预设")
        prompts.append(ft.ExpansionTile(title=ft.Text(label + "生成规则", color=ui.TEXT),
            controls=[editor, _cbar], initially_expanded=(key == "title_prompt"), icon_color=ui.ACCENT))
    rules = ui.card(ft.Column([
        ui.h1("Alibaba 文案规则", ui.FS_MD),
        ui.muted("提示词已预设。只作用于 Alibaba；修改自动保存。保存时勾选使用 AI 即可按这些规则生成。"),
        app._alibaba_preset_control(), *prompts,
    ], spacing=12))
    return [ui.page_header("AI 文案", "编辑当前商品的标题、关键词和描述；按需调整 Alibaba 专用提示词。"),
            ui.workspace_tabs([("当前商品文案", [panel(app, "copy")]), ("生成规则与预设", [rules])])]


def state_for(app):
    pid = str(getattr(app.cfg, "alibaba_test_product_id", "") or "").strip()
    states = getattr(app, "_alibaba_editor_states", None)
    if not isinstance(states, dict):
        states = {}
        app._alibaba_editor_states = states
    return pid, states.setdefault(pid, {"product": {}, "overrides": {}})


def current_overrides(app):
    _, state = state_for(app)
    return deepcopy(state["overrides"])


def remember_product(app, product):
    pid = str(product.get("idStr") or product.get("id") or "")
    if not pid:
        return
    states = getattr(app, "_alibaba_editor_states", None)
    if not isinstance(states, dict):
        states = {}
        app._alibaba_editor_states = states
    state = states.setdefault(pid, {"product": {}, "overrides": {}})
    state["product"] = deepcopy(product)


def _object(value, default):
    if isinstance(value, type(default)):
        return deepcopy(value)
    try:
        decoded = json.loads(value or "null")
        return decoded if isinstance(decoded, type(default)) else deepcopy(default)
    except (ValueError, TypeError):
        return deepcopy(default)


def panel(app, area, *, show_context=True):
    from . import app as ui
    from ..services.alibaba_publish import make_client
    pid, state = state_for(app)
    product, overrides = state["product"], state["overrides"]
    controls = []
    status = ft.Text("修改保留在当前商品编辑区。完成后请打开「保存单品」。",
                     size=12, color=ui.MUTED, selectable=True)

    def read(e=None):
        if not pid:
            app.toast("请先在「采集箱商品」选择商品", False)
            return
        page_index = app._page_idx
        status.value = "正在读取商品详情…"
        app._log(f"[Alibaba][{pid}] 读取编辑详情：开始")
        app._safe_update()
        def work():
            try:
                data = make_client(app.cfg).product_detail(pid)
                if not data:
                    raise ValueError("商品详情为空，请检查账号及商品 ID")
                remember_product(app, data)
                app._log(f"[Alibaba][{pid}] 读取编辑详情：完成，保留已编辑字段", "OK")
                if app.active_platform.id == "alibaba" and app._page_idx == page_index:
                    app._switch(page_index)
            except Exception as error:
                status.value = f"读取失败：{error}。请到「连接与店铺」检查登录。"
                status.color = ui.DANGER_C
                app._log(f"[Alibaba][{pid}] 读取编辑详情失败：{error}", "ERROR")
                app._safe_update()
        threading.Thread(target=work, daemon=True).start()

    def field(label, key, *, group=None, original=None, multiline=False, width=220):
        target = overrides if group is None else overrides.setdefault(group, {})
        value = target.get(key, original if original is not None else product.get(key, ""))
        control = ft.TextField(label=label, value=str(value if value is not None else ""),
            multiline=multiline, min_lines=4 if multiline else 1,
            max_lines=10 if multiline else 1, width=None if multiline else width,
            bgcolor=ui.CARD_2, border_color=ui.BORDER, color=ui.TEXT)
        def change(e):
            target[key] = str(e.control.value or "")
        control.on_change = change
        return control

    heading = {"copy": "当前商品文案", "media": "当前商品图片", "attributes": "当前商品属性",
               "profile": "当前商品资料", "trade": "当前商品交易与物流"}[area]
    controls.append(ui.h1(heading, ui.FS_MD))
    if show_context:
        controls.append(ft.Row([ui.muted(f"商品：{pid or '未选择'}"),
                ft.OutlinedButton("读取当前商品资料", icon=ft.Icons.REFRESH, on_click=read)],
               spacing=12, wrap=True))
    if not product:
        controls.append(ui.muted("选择商品后读取详情，原有内容会自动填入；各商品的本次修改独立保留。"))
    if area == "attributes":
        attributes = deepcopy(overrides.get("native_attributes", _object(product.get("attributes"), [])))
        box = ft.Column(spacing=8)
        def render():
            box.controls = []
            for index, row in enumerate(attributes):
                name = ft.TextField(label="属性名", width=260,
                    value=str(row.get("attribute_name") or ""),
                    read_only=str(row.get("attribute_id")) not in ("", "-1", "None"),
                    color=ui.TEXT, bgcolor=ui.CARD_2)
                value = ft.TextField(label="属性值", width=350,
                    value=str(row.get("value_name") or ""), color=ui.TEXT, bgcolor=ui.CARD_2)
                def edit(e, r=row, n=name, v=value):
                    r.update(attribute_name=str(n.value or ""), value_name=str(v.value or ""), value_id="-1")
                    overrides["native_attributes"] = deepcopy(attributes)
                name.on_change = value.on_change = edit
                controls = [name, value]
                if str(row.get("attribute_id")) in ("", "-1", "None"):
                    def remove(e, i=index):
                        attributes.pop(i)
                        overrides["native_attributes"] = deepcopy(attributes)
                        render()
                        app._safe_update()
                    controls.append(ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="移除此自定义属性", on_click=remove))
                box.controls.append(ft.Row(controls, wrap=True, spacing=10))
        def add(e):
            attributes.append(dict(attribute_id="-1", attribute_name="", value_id="-1", value_name=""))
            render()
            app._safe_update()
        render()
        controls += [ui.muted("系统属性保留身份，必填值将再次按平台候选校验；自定义属性可增删。"),
                     box, ft.TextButton("添加自定义属性", icon=ft.Icons.ADD, on_click=add)]
    elif area == "copy":
        from .copy_actions import button
        from ..services.collect_ai import _generate_field
        from ..services.alibaba_payload import text_desc_plain

        title = field("商品标题（最多 128 字符）", "subject", multiline=True)
        keywords = field("商品关键词（逗号分隔）", "keywords", multiline=True)
        description = field("商品描述（纯文本，最多 2000 字符）", "text_desc", original=product.get("textDesc") or "",
                            multiline=True)

        def facts():
            return dict(product, subject=title.value, textDesc=description.value, keywords=keywords.value)

        for kind, native, label, control in (("title", "subject", "标题", title),
                ("description", "text_desc", "描述", description), ("keywords", "keywords", "关键词", keywords)):
            def generate(source, cfg, key=kind):
                from ..config import AppConfig
                from ..services.platform_presets import resolve_config_prompts
                cfg = AppConfig(resolve_config_prompts(cfg.as_dict(), "alibaba"))
                value, _ = _generate_field("alibaba", source, cfg, key)
                if key == "keywords" and isinstance(value, (tuple, list)):
                    return ", ".join(value)
                return text_desc_plain(str(value)) if key == "description" else str(value)

            def apply(value, key=native):
                overrides[key] = value
                return value

            controls.extend([control, button(app, "alibaba", label, control, facts, generate, apply)])
    elif area == "profile":
        controls += [ft.Row([
            field("来源链接", "sourceUrl", group="editor_fields", width=480),
            field("产品分组 ID（选填）", "groupId", group="editor_fields"),
        ], wrap=True, spacing=10),
            ui.muted("分组与来源仅作用于当前商品；通用公司介绍与 FAQ 在「自动处理设置」维护。")]
        controls.append(ft.ExpansionTile(title=ft.Text("海外现货与发货地", color=ui.TEXT), controls=[
            ui.muted("已有商品的设置会自动读取。以下为平台原始编码，修改前请核对店铺的发货地与海外仓信息。"),
            ft.Row([field("销售模式（平台编码）", "publishType", group="editor_fields"),
                    field("发货地编码", "shipmentPlace", group="editor_fields"),
                    field("仓库类型编码", "warehouseType", group="editor_fields"),
                    field("海外国家/地区编码", "overseaCountryRegion", group="editor_fields")], wrap=True, spacing=10),
        ], icon_color=ui.ACCENT))
    elif area == "media":
        from .media_actions import controls as image_actions
        from ..services.platform_media import GROUPS, read_groups, write_group

        def apply_images(changes):
            for group, urls in changes.items():
                write_group("alibaba", product, overrides, group, urls)

        controls += [ui.muted("主图最多 6 张，详情图最多 30 张。处理结果保存在当前商品编辑区。"),
                     image_actions(app, None, None, platform="alibaba", groups=GROUPS["alibaba"],
                                   get_groups=lambda: read_groups("alibaba", product, overrides),
                                   apply_groups=apply_images)]
    else:
        trade = _object(product.get("wholesaleTrade"), {})
        price_type = ft.Dropdown(label="本商品定价方式", width=260, color=ui.TEXT,
            bgcolor=ui.CARD_2, value=str(overrides.get("price_type", product.get("priceType") or "")),
            options=[ft.dropdown.Option("", "沿用商品定价方式"),
                ft.dropdown.Option("ladder_price", "按数量阶梯定价"),
                ft.dropdown.Option("fob_price", "FOB 区间报价"),
                ft.dropdown.Option("sku_price", "按 SKU 规格定价")])
        price_type.on_change = lambda e: overrides.update(price_type=str(e.control.value or ""))
        controls.append(price_type)
        tiers = deepcopy(overrides.get("price_tiers", _object(product.get("bulkDiscountPrices"), [])))
        tier_box = ft.Column(spacing=8)
        def render_tiers():
            tier_box.controls = []
            for row in tiers:
                quantity = ft.TextField(label="最小起订量", value=str(row.get("start_quantity") or ""),
                    width=160, color=ui.TEXT, bgcolor=ui.CARD_2)
                amount = ft.TextField(label="单价", value=str(row.get("price") or ""),
                    width=160, color=ui.TEXT, bgcolor=ui.CARD_2)
                def changed(e, r=row, q=quantity, a=amount):
                    r.update(start_quantity=str(q.value or ""), price=str(a.value or ""))
                    overrides.update(price_type="ladder_price", price_tiers=deepcopy(tiers))
                quantity.on_change = amount.on_change = changed
                tier_box.controls.append(ft.Row([quantity, amount], wrap=True, spacing=10))
        def add_tier(e):
            if len(tiers) >= 4:
                app.toast("平台最多支持 4 档阶梯价", False)
                return
            tiers.append(dict(start_quantity="", price=""))
            render_tiers()
            app._safe_update()
        render_tiers()
        controls += [ui.h1("商品阶梯价", 14), tier_box,
                     ft.TextButton("添加价格档位", icon=ft.Icons.ADD, on_click=add_tier)]
        sourcing = _object(product.get("sourcingTrade"), {})
        controls.append(ft.ExpansionTile(title=ft.Text("FOB 区间报价", color=ui.TEXT), controls=[
            ft.Row([field("最低价", "fob_min_price", group="sourcing_trade", original=sourcing.get("fob_min_price")),
                    field("最高价", "fob_max_price", group="sourcing_trade", original=sourcing.get("fob_max_price")),
                    field("报价币种", "fob_currency", group="sourcing_trade", original=sourcing.get("fob_currency", "USD"))], wrap=True, spacing=10),
        ], icon_color=ui.ACCENT))
        controls += [ft.Row([
            field("包装尺寸（长 X 宽 X 高，cm）", "package_size", group="wholesale_trade",
                  original=trade.get("package_size")),
            field("包装重量（kg）", "weight", group="wholesale_trade", original=trade.get("weight")),
            field("包装说明", "salePkgDesc", group="editor_fields", width=350),
        ], spacing=10, wrap=True)]
        rows = overrides.get("wholesale_trade", {}).get("deliver_periods", trade.get("deliver_periods") or [])
        delivery_box = ft.Column(spacing=8)
        values = deepcopy(rows)
        def delivery_rows():
            delivery_box.controls = []
            for index, row in enumerate(values):
                quantity = ft.TextField(label="数量上限", width=150, value=str(row.get("quantity", "")),
                                       color=ui.TEXT, bgcolor=ui.CARD_2)
                days = ft.TextField(label="预计发货天数", width=150, value=str(row.get("process_period", "")),
                                   color=ui.TEXT, bgcolor=ui.CARD_2)
                def changed(e, r=row, q=quantity, d=days):
                    r.update(quantity=q.value, process_period=d.value)
                    overrides.setdefault("wholesale_trade", {})["deliver_periods"] = deepcopy(values)
                quantity.on_change = days.on_change = changed
                def remove(e, i=index):
                    values.pop(i)
                    overrides.setdefault("wholesale_trade", {})["deliver_periods"] = deepcopy(values)
                    delivery_rows()
                    app._safe_update()
                delivery_box.controls.append(ft.Row([quantity, days,
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除此档交期", on_click=remove)], wrap=True))
        def add(e):
            if len(values) >= 3:
                app.toast("店小秘最多支持 3 档发货期", False)
                return
            values.append({"quantity": "", "process_period": ""})
            delivery_rows()
            app._safe_update()
        delivery_rows()
        controls += [ui.h1("分档发货期", 14), delivery_box,
                     ft.TextButton("添加发货期档位", icon=ft.Icons.ADD, on_click=add)]
        native = overrides.setdefault("editor_fields", {})
        sample = ft.Dropdown(label="样品服务", width=220, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(native.get("marketSample", product.get("marketSample") or "0")),
            options=[ft.dropdown.Option("0", "沿用商品设置"), ft.dropdown.Option("1", "支持样品"),
                     ft.dropdown.Option("2", "不支持样品")])
        sample.on_change = lambda e: native.update(marketSample=str(e.control.value or "0"))
        controls += [ft.Row([sample,
            field("样品数量", "marketSamplingQuantity", group="editor_fields"),
            field("样品价格", "marketSamplingPrice", group="editor_fields")], spacing=10, wrap=True)]
        inventory = deepcopy(overrides.get("wholesale_trade", {}).get("inventory", trade.get("inventory") or []))
        if inventory:
            controls.append(ui.h1("仓库库存", 14))
            for row in inventory:
                stock = ft.TextField(label="库存 · " + str(row.get("warehouseCode") or "默认仓"),
                    value=str(row.get("value") if row.get("value") is not None else ""),
                    width=260, color=ui.TEXT, bgcolor=ui.CARD_2)
                def stock_change(e, r=row):
                    r["value"] = str(e.control.value or "")
                    overrides.setdefault("wholesale_trade", {})["inventory"] = deepcopy(inventory)
                stock.on_change = stock_change
                controls.append(stock)
        sku = overrides.get("product_sku", _object(product.get("productSku"), {}))
        sku_rows = sku.get("special_skus") or []
        if sku_rows:
            controls.append(ui.h1("商品 SKU 与规格价格", 14))
            controls.append(ui.muted("仅修改已有 SKU；规格维度、图片和仓库结构随原商品保留。"))
            from .copy_actions import sku_button
            for index, row in enumerate(sku_rows, 1):
                if not isinstance(row, dict):
                    continue
                sku_code = ft.TextField(label="SKU 编码", value=str(row.get("sku_code") or ""),
                    width=240, color=ui.TEXT, bgcolor=ui.CARD_2)
                price = ft.TextField(label="规格单价", value=str(row.get("price") or ""),
                    width=160, color=ui.TEXT, bgcolor=ui.CARD_2)
                def sku_change(e, r=row, code=sku_code, amount=price):
                    r["sku_code"] = str(code.value or "")
                    r["price"] = str(amount.value or "")
                    overrides["product_sku"] = deepcopy(sku)
                sku_code.on_change = price.on_change = sku_change
                def sku_facts(r=row):
                    return dict(product, subject=overrides.get("subject", product.get("subject")),
                                selected_variant=r.get("attributes") or r.get("sku_code"))

                def apply_sku(value, r=row):
                    r["sku_code"] = value
                    overrides["product_sku"] = deepcopy(sku)
                    return value

                controls.append(ft.Row([sku_code, price, sku_button(app, "alibaba", sku_code, sku_facts,
                    apply_sku, index=index)], spacing=10, wrap=True))
    controls.append(status)
    if area == "trade":
        controls.append(ui.muted("视频及特殊类目高级资料可通过上方「店小秘完整编辑页」维护，已存在的数据随商品保留。"))
    return ui.card(ft.Column(controls, spacing=12))
