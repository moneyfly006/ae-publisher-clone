"""Shopee 功能页。遵循速卖通的准备、内容、发布分组，使用独立服务和商品状态。"""
from copy import deepcopy
import json
import threading
from urllib.parse import urlparse

import flet as ft

from ..config import save_config
from ..integrations.shopee import make_client
from ..services import shopee as service

SECTIONS = (
    ("连接与店铺", "连接店小秘，选择全球账号或站点店铺。"),
    ("采集箱商品", "浏览、搜索商品；载入后可分步骤编辑，切页会保留修改。"),
    ("类目与属性", "选择末级类目，补齐商品属性。切换类目后请重新核对属性。"),
    ("价格与物流", "设置售价、包装、备货时间和运输方式，按站点限额校验。"),
    ("SKU 与库存", "编辑 Parent SKU 与各变种价格、库存，保留未修改的规格和仓库信息。"),
    ("AI 文案", "编辑商品标题和描述，或使用站点语言及预设提示词生成。"),
    ("商品图片与视频", "维护主图顺序、尺码图和视频信息；图片处理可使用图片工作台。"),
    ("保存与发布", "检查缺项、确认提交并回读结果；过程记录在任务与日志。"),
)


def state_for(app):
    mode = str(getattr(app.cfg, "shopee_mode", "global") or "global")
    state = getattr(app, "_shopee_state", None)
    if not isinstance(state, dict) or state.get("mode") != mode:
        state = dict(mode=mode, product={}, overrides={}, shops=[], categories=[], attrs=[],
                     items=[], page=1, pages=1, total=0, limits=dict(service.DEFAULT_LIMITS),
                     state="draft", keyword="", issues=[], result="", category_path=[])
        app._shopee_state = state
    return state


def rows(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("categoryList", "attributeList", "attributes", "list", "rows", "categories", "logisticsList"):
            if isinstance(value.get(key), list):
                return rows(value[key])
    return []


def build(app, section):
    from . import app as ui
    state = state_for(app)
    mode = state["mode"]
    index = 36 + section
    app._current = index
    product, ov = state["product"], state["overrides"]
    title_key, price_key, stock_key = (("name", "price", "stock") if mode == "site"
                                     else ("itemName", "originalPrice", "normalStock"))
    pid = str(product.get("idStr") or product.get("id") or "")
    status = ft.Text(state.setdefault("notices", {}).get(section, ""), color=ui.MUTED, size=12, selectable=True)
    content = []

    def set_config(key, value):
        app.cfg.set(key, value)
        save_config(app.cfg)

    def set_cookie(value):
        """Shopee 专属 Cookie：交给 App 统一解析/保存（同时同步平台镜像文件）。"""
        helper = getattr(app, "_set_platform_cookie", None)
        if callable(helper):
            helper("shopee", value)
        else:
            set_config("shopee_cookies", str(value or "").strip())

    def refresh():
        if app.active_platform.id == "shopee" and app._page_idx == index:
            app._switch(index)

    def run(label, action, *, rebuild=False):
        status.value = label + "…"
        status.color = ui.MUTED
        app._log("[Shopee] " + label + "：开始")
        app._safe_update()
        def work():
            try:
                message = action()
                status.value = str(message or label + "完成")
                status.color = ui.SUCCESS_C
                app._log("[Shopee] " + status.value, "OK")
            except Exception as error:
                status.value = f"{label}失败：{error}"
                status.color = ui.DANGER_C
                app._log("[Shopee] " + status.value, "ERROR")
            state["notices"][section] = status.value
            if rebuild:
                refresh()
            app._safe_update()
        threading.Thread(target=work, daemon=True).start()

    def field(label, key, *, multiline=False, width=220, default=""):
        value = ov.get(key, product.get(key, default))
        control = ft.TextField(label=label, value=str(value if value is not None else ""),
            width=None if multiline else width, multiline=multiline,
            min_lines=4 if multiline else 1, max_lines=12 if multiline else 1,
            bgcolor=ui.CARD_2, border_color=ui.BORDER, color=ui.TEXT)
        control.on_change = lambda e: ov.update({key: str(e.control.value or "")})
        return control

    def category_children(parent="0"):
        data = make_client(app.cfg).categories(parent, getattr(app.cfg, "shopee_shop_id", ""))
        state["categories"] = rows(data)
        return f"已读取 {len(state['categories'])} 个类目"

    def load_product(product_id):
        if not str(product_id or "").strip():
            app.toast("请填写有效商品ID", False)
            return
        if ov:
            app._confirm("切换商品", "切换后将载入另一商品。当前编辑尚未提交，请先保存需要保留的修改。",
                         lambda: _load(product_id))
        else:
            _load(product_id)

    def _load(product_id):
        def action():
            client = make_client(app.cfg)
            detail = client.product_detail(product_id)
            if not isinstance(detail.get("product"), dict) or not (detail["product"].get("idStr") or detail["product"].get("id")):
                raise ValueError("平台返回空商品详情，请检查商品ID和账号")
            state.update(product=detail["product"], overrides={}, attrs=[], issues=[], result="")
            set_config("shopee_test_product_id", str(product_id))
            if detail["product"].get("shopId"):
                set_config("shopee_shop_id", str(detail["product"]["shopId"]))
            return "商品已载入，下一步可编辑类目与属性"
        run("读取商品详情", action, rebuild=True)

    if section == 0:
        from ..integrations.cookie_parse import cookie_header
        cookie = ft.TextField(label="Shopee 店小秘 Cookie", password=True, can_reveal_password=True,
            value=cookie_header(getattr(app.cfg, "shopee_cookies", "")), multiline=True,
            min_lines=2, max_lines=4, color=ui.TEXT, bgcolor=ui.CARD_2, border_color=ui.BORDER)
        cookie.on_change = lambda e: set_cookie(e.control.value)
        mode_select = ft.Dropdown(label="商品类型", width=240, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=mode, options=[ft.dropdown.Option("global", "Shopee 全球商品"),
                               ft.dropdown.Option("site", "Shopee 站点商品")])
        def change_mode(e):
            if str(e.control.value) == mode:
                return
            def apply():
                set_config("shopee_mode", str(e.control.value))
                set_config("shopee_shop_id", "")
                set_config("shopee_test_product_id", "")
                app._shopee_state = None
                refresh()
            if product:
                app._confirm("切换商品类型", "切换后会清空当前编辑区，请先保存需要保留的商品修改。", apply)
            else:
                apply()
        mode_select.on_change = change_mode
        shop = ft.Dropdown(label="Shopee 店铺", width=340, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(getattr(app.cfg, "shopee_shop_id", "") or ""),
            options=[ft.dropdown.Option(str(s.get("id")), str(s.get("name") or s.get("id")))
                     for s in state["shops"]])
        def change_shop(e):
            selected = str(e.control.value or "")
            original = str(product.get("shopId") or "")
            def apply():
                set_config("shopee_shop_id", selected)
                if product and original != selected:
                    state.update(product={}, overrides={}, attrs=[], category_path=[], issues=[], result="")
                    set_config("shopee_test_product_id", "")
                    refresh()
            if product and original != selected:
                app._confirm("切换店铺", "切换后会清空当前编辑区，避免把原店铺商品提交到另一店铺。请先保存需要保留的修改。", apply)
            else:
                apply()
        shop.on_change = change_shop
        site = ft.Dropdown(label="站点 / 文案语言", width=240, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(getattr(app.cfg, "shopee_site", "global") or "global"),
            options=[ft.dropdown.Option(k, f"{k} · {v}") for k, v in service.LANGUAGES.items()])
        def site_change(e):
            set_config("shopee_site", str(e.control.value or "global"))
            state["limits"] = dict(service.DEFAULT_LIMITS)
        site.on_change = site_change
        def connect():
            client = make_client(app.cfg)
            state["shops"] = client.shops()
            state["limits"] = client.limits(str(site.value or "global"))
            shop.options = [ft.dropdown.Option(str(s["id"]), str(s.get("name") or s["id"])) for s in state["shops"]]
            if not state["shops"]:
                return "连接成功，账号暂无此类型的 Shopee 店铺。请在店小秘授权店铺后刷新。"
            return f"连接成功，读取到 {len(state['shops'])} 个店铺"
        def read_cookie():
            from ..integrations.browser_cookies import resolve
            source = resolve("dianxiaomi.com")
            if not source.ok:
                raise ValueError("未读取到登录态，请先登录店小秘或粘贴 Cookie")
            value = "; ".join(f"{k}={v}" for k, v in source.cookies.items())
            set_cookie(value)
            cookie.value = value
            return "本机 Cookie 已读取，请检测连接"
        content += [ui.card(ft.Column([
            ui.h1("店小秘连接", ui.FS_MD), cookie,
            ft.Row([ft.OutlinedButton("读取本机 Cookie", icon=ft.Icons.COOKIE_OUTLINED,
                                     on_click=lambda e: run("读取登录态", read_cookie)),
                    ft.ElevatedButton("检测连接并刷新店铺", icon=ft.Icons.WIFI_TETHERING,
                                      on_click=lambda e: run("检测连接", connect))], wrap=True, spacing=10),
            ft.Row([mode_select, shop, site], wrap=True, spacing=10),
            ui.muted("先选择正确的商品类型，再载入商品；Cookie 独立保存，留空时尝试已有店小秘登录态。"),
            status,
        ], spacing=12))]
    elif section == 1:
        search = ft.TextField(label="标题搜索", value=state["keyword"], width=300,
                              color=ui.TEXT, bgcolor=ui.CARD_2)
        search.on_change = lambda e: state.update(keyword=str(e.control.value or ""))
        state_select = ft.Dropdown(label="商品状态", width=180, value=state["state"],
            color=ui.TEXT, bgcolor=ui.CARD_2, options=[ft.dropdown.Option(k, label) for k, label in
                (("draft", "采集箱"), ("waitPublish", "待发布"), ("publishFail", "发布失败"),
                 ("publishing", "发布中"), ("online", "在线商品"))])
        state_select.on_change = lambda e: state.update(state=str(e.control.value), page=1)
        def load(page=None):
            data = make_client(app.cfg).list_products(state["state"], page or state["page"],
                shop_id=str(getattr(app.cfg, "shopee_shop_id", "") or "-1"), keyword=state["keyword"])
            state.update(items=data["items"], page=data["page"], pages=data["pages"], total=data["total"])
        def change_page(delta):
            page = max(1, min(int(state["pages"] or 1), int(state["page"]) + delta))
            run("读取商品列表", lambda: load(page), rebuild=True)
        box = ft.Column(spacing=8)
        for row in state["items"]:
            identity = str(row.get("idStr") or row.get("id") or "")
            text = str(row.get("itemName") or row.get("name") or row.get("title") or identity)
            box.controls.append(ft.Row([
                ft.Column([ft.Text(text, color=ui.TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ui.muted(identity + (" · " + str(row.get("errMsg")) if row.get("errMsg") else ""))], expand=True),
                ft.OutlinedButton("编辑", on_click=lambda e, p=identity: load_product(p)),
            ], spacing=10))
        if not state["items"]:
            box.controls.append(ui.muted("暂无商品。可刷新列表，或先在店小秘采集/创建商品后按 ID 打开。"))
        identity = ft.TextField(label="商品 ID", width=280, color=ui.TEXT, bgcolor=ui.CARD_2)
        content += [ui.card(ft.Column([
            ft.Row([state_select, search,
                ft.ElevatedButton("刷新商品", icon=ft.Icons.REFRESH,
                    on_click=lambda e: run("读取商品列表", lambda: load(1), rebuild=True))], spacing=10, wrap=True),
            ft.Row([identity, ft.OutlinedButton("按 ID 打开", on_click=lambda e: load_product(str(identity.value or "").strip()))], wrap=True),
            box, ft.Row([ft.TextButton("上一页", on_click=lambda e: change_page(-1)),
                ui.muted(f"第 {state['page']}/{state['pages']} 页 · 共 {state['total']} 个商品"),
                ft.TextButton("下一页", on_click=lambda e: change_page(1))], wrap=True), status,
        ], spacing=12))]
    elif section == 2:
        tree = []
        def pick(row):
            cid = str(row.get("categoryId") or row.get("id") or "")
            leaf = row.get("leafCategory", row.get("isLeaf", row.get("isleaf", False)))
            if leaf not in (True, 1, "1", "true"):
                run("读取下级类目", lambda: category_children(cid), rebuild=True)
                return
            ov.update(categoryId=cid, attributes=[])
            state["category_name"] = str(row.get("chineseName") or row.get("categoryName") or row.get("name") or cid)
            state["attrs"] = rows(make_client(app.cfg).attributes(cid, getattr(app.cfg, "shopee_shop_id", "")))
            refresh()
        for row in state["categories"]:
            name = str(row.get("chineseName") or row.get("categoryName") or row.get("name") or row.get("categoryId"))
            tree.append(ft.TextButton(name, on_click=lambda e, r=deepcopy(row): run("选择类目", lambda: pick(r))))
        def attrs():
            cid = ov.get("categoryId", product.get("categoryId"))
            if not cid:
                raise ValueError("请先选择末级类目")
            state["attrs"] = rows(make_client(app.cfg).attributes(cid, getattr(app.cfg, "shopee_shop_id", "")))
        fields = []
        current = service.json_value(ov.get("attributes", product.get("attributes")), [])
        for definition in state["attrs"]:
            key = str(definition.get("attributeId") or definition.get("attribute_id") or definition.get("id") or "")
            label = str(definition.get("attributeName") or definition.get("name") or key)
            required = definition.get("isMandatory") or definition.get("required") or definition.get("mandatory")
            if required in (True, 1, "1", "true"):
                label += " *"
            existing = next((a for a in current if str(a.get("attribute_id") or a.get("attributeId")) == key), {})
            values = existing.get("attribute_value_list") or []
            first = values[0] if values else {}
            choices = rows(definition.get("attributeValueList") or definition.get("valueList") or definition.get("values") or [])
            def update(e, k=key, options=choices):
                selected = str(e.control.value or "")
                source = next((x for x in options if str(x.get("valueId") or x.get("value_id") or x.get("id")) == selected), {})
                entry = {"attribute_id": k, "attribute_value_list": [{
                    "value_id": source.get("valueId", source.get("value_id", source.get("id", 0))),
                    "original_value_name": source.get("originalValueName") or source.get("valueName") or source.get("name") or selected}]}
                remaining = [a for a in current if str(a.get("attribute_id") or a.get("attributeId")) != k]
                current[:] = remaining + [entry]
                ov["attributes"] = deepcopy(current)
            if choices:
                control = ft.Dropdown(label=label, width=360, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(first.get("value_id") or ""), options=[ft.dropdown.Option(
                        str(v.get("valueId") or v.get("value_id") or v.get("id")),
                        str(v.get("originalValueName") or v.get("valueName") or v.get("name") or "")) for v in choices])
            else:
                control = ft.TextField(label=label, width=360, color=ui.TEXT, bgcolor=ui.CARD_2,
                                       value=str(first.get("original_value_name") or ""))
            control.on_change = update
            fields.append(control)
        content += [ui.card(ft.Column([
            ui.h1("类目选择", ui.FS_MD), ui.muted("当前类目：" + str(state.get("category_name") or ov.get("categoryId") or product.get("categoryId") or "未选择")),
            ft.Row([ft.OutlinedButton("读取根类目", on_click=lambda e: run("读取类目", category_children, rebuild=True)),
                ft.OutlinedButton("读取类目属性", on_click=lambda e: run("读取属性", attrs, rebuild=True))], wrap=True, spacing=10),
            *tree, ft.ResponsiveRow([ft.Container(c, col={"xs": 12, "lg": 6}) for c in fields]), status,
        ], spacing=10))]
    elif section == 3:
        logistics_box = ft.Column(spacing=8)
        native_logistics = "logistics" if mode == "site" else "logisticsInfo"
        existing = service.json_value(ov.get(native_logistics, product.get(native_logistics)), [])
        def render_logistics():
            logistics_box.controls = []
            for group in existing:
                entries = [group] if mode == "site" else (group.get("logisticsInfo") or [])
                for entry in entries:
                    label = str(entry.get("logisticName") or entry.get("name") or entry.get("logisticId") or entry.get("logistic_id") or "物流渠道")
                    switch = ft.Switch(label=label, value=bool(entry.get("enabled")), active_color=ui.ACCENT)
                    def changed(e, row=entry):
                        row["enabled"] = bool(e.control.value)
                        ov[native_logistics] = deepcopy(existing)
                    switch.on_change = changed
                    logistics_box.controls.append(switch)
            if not logistics_box.controls:
                logistics_box.controls.append(ui.muted("未读取物流渠道；选择店铺后刷新，或沿用原商品物流设置。"))
        def load_logistics():
            shop_id = str(getattr(app.cfg, "shopee_shop_id", "") or product.get("shopId") or "")
            if not shop_id:
                raise ValueError("请先在「连接与店铺」选择店铺")
            data = make_client(app.cfg).logistics(shop_id)
            entries = rows(data)
            if mode == "site":
                existing[:] = entries
            else:
                existing[:] = [{"shopId": shop_id, "logisticsInfo": entries}]
            # 读取不写入 overrides；只有用户切换渠道才应用修改。
            render_logistics()
            return f"已读取 {len(entries)} 个物流渠道，请勾选需要启用的渠道"
        render_logistics()
        content += [ui.card(ft.Column([
            ui.h1("售价与包装", ui.FS_MD),
            ft.Row([field("售价（以店铺币种为准）", price_key), field("包装重量（kg）", "weight"),
                field("长（cm）", "packageLength"), field("宽（cm）", "packageWidth"),
                field("高（cm）", "packageHeight")], wrap=True, spacing=10),
            ft.Row([field("商品保存状况（NEW / USED）", "condition", default="NEW"),
                field("备货天数", "daysToShip", default="1")], wrap=True, spacing=10),
            ui.muted("多变种价格请在「SKU 与库存」逐项编辑；仓库、运费及物流配置沿用商品原设置。"),
            field("来源链接", "sourceUrl", width=520), status,
            ft.ExpansionTile(title=ft.Text("运输渠道", color=ui.TEXT), controls=[
                ft.OutlinedButton("读取物流渠道", icon=ft.Icons.REFRESH,
                    on_click=lambda e: run("读取物流渠道", load_logistics)), logistics_box]),
        ], spacing=12))]
    elif section == 4:
        from .copy_actions import sku_button
        variants = service.variation_rows(product, mode)
        parent = field("Parent SKU", "itemSku", width=320)

        def set_parent(value):
            ov["itemSku"] = value
            return value

        controls = [ft.Row([parent, sku_button(app, "shopee", parent, lambda: product, set_parent,
                                               label="父 SKU")], wrap=True)]
        if not variants:
            controls.append(field("可售库存", stock_key))
        for index, row in enumerate(variants, 1):
            key = str(row.get("idStr") or row.get("id") or row.get("modelIdStr") or row.get("modelId") or "")
            controls.append(ui.muted("变种：" + (str(row.get("option1") or "") + " / " + str(row.get("option2") or "")).strip(" /")))
            if not key:
                controls.append(ui.muted("该变种缺少稳定 ID，保留原值；请在店小秘修复后重新读取。"))
                continue
            code_key = "variationSku" if mode == "site" else "modelSku"
            edits = ov.setdefault("variant_edits", {}).setdefault(key, {})
            fields = []
            for native, label in ((code_key, "SKU"), (price_key, "单价"), (stock_key, "库存")):
                control = ft.TextField(label=label, width=220, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(edits.get(native, row.get(native)) if edits.get(native, row.get(native)) is not None else ""))
                control.on_change = lambda e, k=native, target=edits: target.update({k: str(e.control.value or "")})
                fields.append(control)
            def source(r=row):
                return dict(product, selected_variant=[r.get(k) for k in ("option1", "option2")])

            def update_sku(value, target=edits, key=code_key):
                target[key] = value
                return value

            controls.append(ft.Row([*fields, sku_button(app, "shopee", fields[0], source, update_sku,
                index=index, label="变种 SKU")], wrap=True, spacing=10))
        content += [ui.card(ft.Column([ui.h1("SKU 与库存", ui.FS_MD), *controls, status], spacing=12))]
    elif section == 5:
        title = field("商品标题", title_key, multiline=True)
        description = field("产品描述（纯文本）", "description", multiline=True)
        if product.get("descriptionType") == "extended":
            description = field("新版产品描述（HTML，保留原结构）", "descriptionInfoStr", multiline=True)
        from .copy_actions import button
        from ..services.platform_media import keep_description_images

        def text_facts():
            key = "descriptionInfoStr" if product.get("descriptionType") == "extended" else "description"
            return {**product, title_key: title.value, key: description.value}

        def generate(kind, control):
            native = title_key if kind == "title" else (
                "descriptionInfoStr" if product.get("descriptionType") == "extended" else "description")

            def create(source, cfg):
                return service.generate_copy(source, cfg, kind, site=cfg.shopee_site or "global")

            def apply(value):
                if native == "descriptionInfoStr":
                    import html
                    value = keep_description_images("<p>" + html.escape(value).replace("\n", "<br>") + "</p>",
                                                    control.value)
                ov[native] = value
                return value

            return button(app, "shopee", "标题" if kind == "title" else "描述", control, text_facts, create, apply)

        title_action = generate("title", title)
        description_action = generate("description", description)
        prompts = []
        for kind, label in (("title", "标题"), ("description", "描述")):
            editor = ft.TextField(label=label + "提示词", multiline=True, min_lines=4, max_lines=10,
                color=ui.TEXT, bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "shopee_" + kind + "_prompt", "") or service.DEFAULT_PROMPTS[kind]))
            editor.on_change = lambda e, k="shopee_" + kind + "_prompt": set_config(k, str(e.control.value or ""))
            try:
                from .prompt_presets import bar as _prompt_bar
                _cbar = _prompt_bar(app, key="shopee:" + kind,
                                    prompt_key="shopee_" + kind + "_prompt", field=editor,
                                    label=label + "我的预设")
            except Exception:
                _cbar = None
            prompts.append(ft.ExpansionTile(title=ft.Text(label + "生成规则", color=ui.TEXT),
                                            controls=([editor, _cbar] if _cbar is not None else [editor])))
        if getattr(app, "_collect_editor_only", False):
            content += [ui.card(ft.Column([
                title, title_action,
                description, description_action, status,
            ], spacing=12))]
        else:
            content += [ui.workspace_tabs([
            ("商品文案", [ui.card(ft.Column([
                title, title_action,
                description, description_action, status,
            ], spacing=12))]),
            ("提示词预设", [ui.card(ft.Column(prompts, spacing=10))]),
            ])]
    elif section == 6:
        from dataclasses import replace
        from .media_actions import controls as image_actions
        from ..services.platform_media import GROUPS, read_groups, write_group

        groups = dict(GROUPS["shopee"])
        if ov.get("descriptionType", product.get("descriptionType")) != "extended":
            groups.pop("detail")
        else:
            limit = service.number(state["limits"].get("descImageNumMax"))
            if limit and limit >= 1:
                groups["detail"] = replace(groups["detail"], limit=int(limit))

        def apply_images(changes):
            for group, urls in changes.items():
                write_group("shopee", product, ov, group, urls, mode)

        content += [ui.card(ft.Column([
            ui.h1("图片与视频", ui.FS_MD),
            ui.muted("商品图最多 9 张，至少 400×400。尺码图和视频封面分别编辑。"),
            image_actions(app, None, None, platform="shopee", groups=groups,
                get_groups=lambda: read_groups("shopee", product, ov, mode), apply_groups=apply_images,
                language=lambda: service.LANGUAGES.get(str(getattr(app.cfg, "shopee_site", "global")), "英语")),
            *([] if getattr(app, "_collect_editor_only", False) else [
                ft.TextButton("打开图片工作台", icon=ft.Icons.GRID_VIEW, on_click=lambda e: app._switch(9))]),
            field("视频 URL（MP4，10–60 秒）", "videoUrl", width=520), status,
        ], spacing=12))]
    else:
        def overrides():
            result = deepcopy(ov)
            shop = str(getattr(app.cfg, "shopee_shop_id", "") or "")
            if shop:
                result["shopId"] = shop
            return result
        def check():
            if not pid:
                raise ValueError("请先在「采集箱商品」载入商品")
            _, issues = service.prepare(pid, app.cfg, overrides())
            state["issues"] = issues
            state["result"] = "检查通过，可以保存或提交" if not issues else f"需要修正 {len(issues)} 项"
        def submit(op):
            if not pid:
                app.toast("请先载入商品", False)
                return
            snapshot = overrides()
            def action():
                # 只有「提交发布(op=2)」算刊登；保存草稿/移入待发布不计数
                is_publish = bool(app._publish_op_counts("shopee", op))
                try:
                    result = service.save(pid, app.cfg, snapshot, op=op)
                except Exception:
                    # 真实提交抛错也算一次失败（用户取消不会走到这里）
                    if is_publish:
                        try:
                            app._record_publish_result("shopee", str(pid), False)
                        except Exception:
                            pass
                    raise
                state["issues"], state["result"] = result.issues, result.message
                if is_publish:
                    try:
                        app._record_publish_result("shopee", str(pid), bool(result.ok))
                    except Exception:
                        pass
                if not result.ok:
                    raise ValueError(result.message)
                return result.message
            app._confirm("确认" + ("保存草稿" if op == "1" else "提交发布"),
                f"商品：{pid}\n标题：{ov.get(title_key, product.get(title_key, ''))}\n"
                "将重新读取并校验商品后提交；发布结果以平台回读状态为准。",
                lambda: run("保存商品", action, rebuild=True))
        def report():
            # 服务保存已做回读；手动刷新仍只读取。
            latest = make_client(app.cfg).product_detail(pid)["product"]
            state["result"] = str(latest.get("errMsg") or latest.get("dxmOfflineState") or latest.get("dxmState") or "平台尚未返回状态")
        links = {"连接与店铺": 36, "类目与属性": 38, "AI 文案": 41, "商品图片": 42,
                 "价格与物流": 39, "SKU 与库存": 40}
        issue_controls = []
        for issue in state["issues"]:
            destination = next((i for label, i in links.items() if issue.startswith(label)), 37)
            issue_controls.append(ft.Row([ft.Text(issue, color=ui.DANGER_C, expand=True, selectable=True),
                ft.TextButton("去修正", on_click=lambda e, i=destination: app._switch(i))]))
        content += [ui.card(ft.Column([
            ui.h1("检查与提交", ui.FS_MD),
            ft.Row([ft.OutlinedButton("发布前检查", icon=ft.Icons.FACT_CHECK,
                        on_click=lambda e: run("发布前检查", check, rebuild=True)),
                ft.ElevatedButton("保存草稿", icon=ft.Icons.SAVE, on_click=lambda e: submit("1")),
                ft.OutlinedButton("移入待发布", on_click=lambda e: submit("3")),
                ft.ElevatedButton("保存并发布", icon=ft.Icons.CLOUD_UPLOAD, on_click=lambda e: submit("2"))], wrap=True, spacing=10),
            ft.Row([ft.TextButton("刷新平台结果", icon=ft.Icons.REFRESH,
                        on_click=lambda e: run("回读平台状态", report, rebuild=True)),
                ft.TextButton("查看全过程日志", on_click=lambda e: app._switch(10))], wrap=True),
            ft.Text(state["result"] or "尚未提交；先检查缺项，再确认保存或发布。", color=ui.TEXT, selectable=True),
            *issue_controls, status,
        ], spacing=12))]
    context = f"当前商品：{pid or '未选择'} · 类型：{'站点商品' if mode == 'site' else '全球商品'} · 店铺：{getattr(app.cfg, 'shopee_shop_id', '') or '未选择'}"
    return app._workflow_frame(*SECTIONS[section], content, index, tuple(range(36, 44)), context)


def overview(app):
    from . import app as ui
    app._current = 0
    return [ui.page_header("Shopee 工作空间", "连接店铺 → 选商品 → 类目、价格与库存 → 内容 → 检查发布。"),
        ui.card(ft.Column([
            ui.h1("开始本次刊登", ui.FS_MD),
            ui.muted("全球商品与站点商品使用独立接口，选择正确类型后再载入商品。"),
            ft.Row([ft.ElevatedButton("连接与店铺", icon=ft.Icons.STORE_OUTLINED,
                                     on_click=lambda e: app._switch(36)),
                    ft.OutlinedButton("打开采集箱商品", icon=ft.Icons.INVENTORY_2_OUTLINED,
                                      on_click=lambda e: app._switch(37))], spacing=12, wrap=True),
        ], spacing=12))]
