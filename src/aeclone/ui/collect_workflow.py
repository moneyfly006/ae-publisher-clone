"""Automatic collection workflow: settings belong to their functional pages."""
from copy import deepcopy
import json
import sqlite3
import tempfile
import threading

import flet as ft

from ..config import AppConfig, save_config
from ..services.collect_automation import settings, run_collect, validate_overrides

RUN_PAGE = {"alibaba": 33, "amazon": 21, "tiktok": 28, "shopee": 43}
CONNECTION_PAGE = {"alibaba": 1, "amazon": 15, "tiktok": 22, "shopee": 36}
ALIBABA_RULES_PAGE = 44
AMAZON_RULES_PAGE = 45
SECTIONS = {
    "alibaba": {30: "category", 31: "company", 32: "price", 3: "text", 5: "media"},
    "amazon": {17: "category", 18: "price", 34: "stock", 19: "text", 20: "media"},
    "tiktok": {24: "category", 25: "price", 35: "stock", 26: "text", 27: "media"},
    "shopee": {38: "category", 39: "price", 40: "stock", 41: "text", 42: "media"},
}
# Native override keys accepted by the existing platform services.
FIELDS = {
    "alibaba": {
        "category": [],
        "company": [],
        "price": [("price", "统一售价（可选）")],
        "stock": [],
    },
    "amazon": {
        "category": [],
        "price": [("standard_price", "统一售价（可选）"), ("fulfillment", "配送方式"),
                  ("shipping_template", "运费模板（可选）")],
        "stock": [("quantity", "统一库存（可选，0 表示清零）"), ("brand", "品牌（可选）"),
                  ("manufacturer", "制造商（可选）")],
    },
    "tiktok": {
        "category": [("category_id", "统一类目 ID（可选）")],
        "price": [("price", "统一售价（可选）"), ("weight", "包装重量（kg，可选）"),
                  ("length", "长（cm，可选）"), ("width", "宽（cm，可选）"),
                  ("height", "高（cm，可选）"), ("warehouse_id", "仓库 ID（可选）")],
        "stock": [("stock", "统一库存（可选，0 表示清零）")],
    },
    "shopee": {
        "category": [("categoryId", "统一类目 ID（可选）")],
        "price": [("originalPrice", "统一售价（可选）"), ("weight", "包装重量（kg，可选）"),
                  ("packageLength", "长（cm，可选）"), ("packageWidth", "宽（cm，可选）"),
                  ("packageHeight", "高（cm，可选）"), ("daysToShip", "备货天数（可选）")],
        "stock": [("normalStock", "统一库存（可选，0 表示清零）")],
    },
}


def _product_thumbnail(item, size: int = 52):
    """商品选择列表缩略图；没有可用图片时用占位图标。"""
    from . import app as _ui
    raw = ""
    for key in ("mainImage", "imgUrl", "draftImgUrl", "extraImagesStr"):
        value = item.get(key)
        if isinstance(value, (list, tuple)):
            value = next((str(x).strip() for x in value if str(x).strip()), "")
        text = str(value or "").strip()
        if text:
            raw = text.split("|")[0].split(";")[0].strip()
            if raw:
                break
    if raw.startswith(("http://", "https://")):
        try:
            return ft.Container(
                content=ft.Image(src=raw, width=size, height=size, fit=ft.ImageFit.COVER),
                width=size, height=size, border_radius=6)
        except Exception:
            pass
    return ft.Container(content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, size=22, color=_ui.MUTED),
                        width=size, height=size, alignment=ft.alignment.center)


def write(app, key, value):
    previous = app.cfg.get(key)
    app.cfg.set(key, value)
    try:
        save_config(app.cfg)
    except OSError:
        app.cfg.set(key, previous)
        raise
    if key.endswith("_collect_overrides"):
        record_error(app, key.removesuffix("_collect_overrides"), "_settings")


def record_error(app, platform, key, message=""):
    if not hasattr(app, "_collect_errors"):
        app._collect_errors = {}
    errors = app._collect_errors.setdefault(platform, {})
    if message:
        errors[key] = message
    else:
        errors.pop(key, None)


def read_settings(app, platform):
    try:
        return settings(app.cfg, platform)
    except (ValueError, TypeError) as exc:
        record_error(app, platform, "_settings", str(exc))
        return {}


def option_field(app, platform, key, label, *, multiline=False):
    from . import app as ui
    from ..services.collect_store import scope
    ov = read_settings(app, platform)
    def _ctx():
        return (str(getattr(app.cfg, platform + "_shop_id", "") or ""),
                str(getattr(app.cfg, platform + "_site", "") or ""),
                str(getattr(app.cfg, platform + "_mode", "") or ""))
    original_scope = _ctx()
    value = ov.get(key, "")
    if platform == "amazon" and key == "fulfillment":
        field = ft.Dropdown(label=label, value="AFN" if value == "FBA" else str(value or ""), width=320,
            color=ui.TEXT, bgcolor=ui.CARD_2, options=[ft.dropdown.Option("", "沿用商品配送方式"),
                ft.dropdown.Option("MFN", "卖家自发货"), ft.dropdown.Option("AFN", "亚马逊物流")])
    else:
        field = ft.TextField(label=label, value=str(value), width=None if multiline else 320,
                         multiline=multiline, min_lines=3 if multiline else 1,
                         color=ui.TEXT, bgcolor=ui.CARD_2, border_color=ui.BORDER)
    def changed(e):
        nonlocal original_scope
        # 只比较店铺/站点/模式；Cookie 轮换不应让正在编辑的设置失效。
        current = _ctx()
        if current != original_scope:
            # 首次进入页面自动带出店铺/站点时（原值为空）视为初始化，不拦截；
            # 已绑定店铺后再切换才提示，避免误伤正在编辑的设置。
            if not original_scope[0] and not original_scope[1]:
                original_scope = current
            else:
                field.error_text = "店铺或站点已切换，请重新打开当前功能页"
                app._safe_update()
                return
        try:
            current = settings(app.cfg, platform)
        except (ValueError, TypeError):
            field.error_text = "请先修复批量预设，再修改设置"
            app._safe_update()
            return
        value = str(e.control.value or "").strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)
        try:
            validate_overrides(current)
            write(app, platform + "_collect_overrides", json.dumps(current, ensure_ascii=False))
            field.error_text = None
            record_error(app, platform, key)
        except (ValueError, OSError) as exc:
            field.error_text = str(exc)
            record_error(app, platform, key, str(exc))
        app._safe_update()
    field.on_change = changed
    return field


def _repair_preset(app, platform, page):
    from . import app as ui
    from ..services.collect_store import scope

    def _ctx():
        return (str(getattr(app.cfg, platform + "_shop_id", "") or ""),
                str(getattr(app.cfg, platform + "_site", "") or ""),
                str(getattr(app.cfg, platform + "_mode", "") or ""))
    original_scope = _ctx()
    key = platform + "_collect_overrides"
    original = deepcopy(app.cfg.get(key))

    def repair(e):
        try:
            parsed = json.loads(original) if isinstance(original, str) else original
        except (ValueError, TypeError):
            parsed = None
        recovered = {}
        removed = []
        if isinstance(parsed, dict):
            draft = app.cfg.as_dict()
            # When an old and new Amazon price disagree, keep the current field.
            for field in sorted(parsed, key=lambda field: platform == "amazon" and field == "price"):
                candidate = {**recovered, field: parsed[field]}
                try:
                    draft[key] = json.dumps(candidate, ensure_ascii=False, allow_nan=False)
                    recovered = settings(AppConfig(draft), platform)
                except (ValueError, TypeError):
                    removed.append(field)

        def save():
            nonlocal original_scope
            if _ctx() != original_scope and not (original_scope[0] or original_scope[1]):
                original_scope = _ctx()   # 页面初始化自动带出站点，不算用户切换
            if _ctx() != original_scope or app.cfg.get(key) != original:
                raise ValueError("预设已改变，请重新打开此页")
            folder = AppConfig.path().parent / "preset-backups"
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                                             prefix=platform + "-", suffix=".bak", delete=False) as backup:
                backup.write(original if isinstance(original, str) else json.dumps(original, ensure_ascii=False))
            write(app, key, json.dumps(recovered, ensure_ascii=False))
            app._switch(page)
            app.toast("已修复批量预设。原始备份位于 " + str(backup.name))

        detail = ("保留可读取的设置，清除损坏的字段。请在对应页面重新填写被清除的设置。"
                  if isinstance(parsed, dict) else "此预设已无法读取。恢复后，批量任务将沿用各商品的原始资料。")
        if removed:
            detail += f" 将清除 {len(removed)} 项。"
        app._confirm("备份并修复批量预设", detail + " 原始预设将备份到本机。", save)

    return ft.Row([ft.Text("当前批量预设无法读取。修复后即可继续编辑。", color=ui.DANGER_C),
                   ft.OutlinedButton("备份并修复预设", icon=ft.Icons.RESTORE, on_click=repair)], wrap=True)


def prompt_controls(app, platform, section):
    from . import app as ui
    from ..services.platform_presets import prompt_specs, effective_prompt
    fields, controls, styles = [], [], {}
    for key, label, default in prompt_specs(platform, section):
        editor = ft.TextField(label=label + "提示词", multiline=True, min_lines=4, max_lines=12,
            color=ui.TEXT, bgcolor=ui.CARD_2, value=effective_prompt(app.cfg, key, default))
        editor.on_change = lambda e, k=key: write(app, k, str(e.control.value or ""))
        fields.append((key, default, editor))
        from ..services.platform_presets import TEXT_STYLES, styled_prompt
        style_key = key + "_style"
        style = ft.Dropdown(label=label + "预设", width=280, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(getattr(app.cfg, style_key, "default") or "default"),
            options=[ft.dropdown.Option(k, v[0]) for k, v in TEXT_STYLES.items()])
        def choose_style(name, k=key, base=default, target=editor, sk=style_key):
            # 在当前提示词基础上追加风格，不把用户自定义提示词重置为内置默认值。
            base = str(target.value or base or default)
            value = styled_prompt(base, str(name or "default"))
            target.value = value
            app.cfg.set(sk, str(name or "default"))
            write(app, k, value)
            return value
        try:
            from .prompt_presets import attach_dropdown
            from ..services import prompt_presets as _pp
            if not _pp.choice(app.cfg, platform + ":" + key):
                _pp.set_choice(app.cfg, platform + ":" + key, str(style.value or "default"), save_config)
            attach_dropdown(app, style, key=platform + ":" + key, prompt_key=key, field=editor,
                            builtins={k: v[0] for k, v in TEXT_STYLES.items()},
                            labels={k: v[0] for k, v in TEXT_STYLES.items()},
                            on_builtin=lambda name, _k=key, _b=default, _e=editor, _s=style_key:
                            choose_style(name, _k, _b, _e, _s))
        except Exception:
            style.on_change = lambda e, k=key, base=default, target=editor, sk=style_key: choose_style(
                str(e.control.value or "default"), k, base, target, sk)
        styles[key] = style
        controls.append(ft.ExpansionTile(title=ft.Text(label, color=ui.TEXT), controls=[style, editor]))
    if fields:
        def reset(e):
            for key, default, editor in fields:
                app.cfg.set(key, default)
                app.cfg.set(key + "_style", "default")
                editor.value = default
                styles[key].value = "default"
            save_config(app.cfg)
            app.toast("已恢复本页提示词预设")
            app._safe_update()
        controls.append(ft.TextButton("恢复本页提示词预设", icon=ft.Icons.RESTORE, on_click=reset))
    return controls


def image_prompt_editor(app, platform):
    """全平台图片提示词：每个提示词一个下拉框，可直接添加/保存/记忆。"""
    from . import app as ui
    from .prompt_presets import attach_dropdown
    keys = [
        ("main_image_generate_prompt", "AI 生成主图提示词"),
        ("designated_edit_prompt", "指定编辑提示词"),
        ("img_sku_edit_prompt", "SKU 图编辑提示词"),
        ("edit_prompt", "通用编辑提示词"),
        ("translate_prompt", "翻译提示词"),
        ("background_gen_sku_prompt", "SKU 背景生成提示词"),
    ]
    if platform == "amazon":
        keys.append(("amazon_image_prompt", "Amazon 图片提示词"))
    else:
        keys.append((platform + "_collect_image_prompt", "批量图片处理提示词"))
    rows = []
    for key, label in keys:
        tf = ft.TextField(label=label, multiline=True, min_lines=3, max_lines=7,
                          width=760, bgcolor=ui.CARD_2, border_color=ui.CARD_2,
                          color=ui.TEXT, value=str(getattr(app.cfg, key, "") or ""))
        tf.on_change = lambda e, _k=key: write(app, _k, str(e.control.value or ""))
        dd = ft.Dropdown(label=label + "预设", width=230, bgcolor=ui.CARD_2,
                         border_color=ui.CARD_2, color=ui.TEXT, options=[])
        builtins = (getattr(ui, "IMAGE_PROMPT_PRESETS", {}) or {}).get(
            "main" if key == "main_image_generate_prompt" else "", {})
        attach_dropdown(app, dd, key="image:" + platform + ":" + key, prompt_key=key,
                        field=tf, builtins=builtins,
                        labels={k: k for k in builtins})
        rows += [tf, dd]
    return ft.ExpansionTile(
        title=ft.Text("AI 图片提示词（可下拉添加/保存）", color=ui.TEXT),
        controls=[ft.Column(rows, spacing=8)], icon_color=ui.ACCENT)


def functional_settings(app, platform, page, *, run_link=True):
    from . import app as ui
    section = SECTIONS[platform][page]
    controls = [ui.muted("设置应用于自动处理的采集箱商品。留空沿用商品原值。表格修改后请点击对应的保存按钮。")]
    from ..services.collect_profiles import label
    controls.append(ui.muted("当前预设：" + label(app.cfg, platform) + "；店铺/站点分别保存。"))
    read_settings(app, platform)
    if getattr(app, "_collect_errors", {}).get(platform, {}).get("_settings"):
        controls.append(_repair_preset(app, platform, page))
    if section in ("category", "price", "stock", "company"):
        from .collect_forms import editor as visual_editor
        fields = FIELDS[platform].get(section, [])
        if platform == "shopee":
            fields = [("price" if k == "originalPrice" and getattr(app.cfg, "shopee_mode", "global") == "site" else
                       "stock" if k == "normalStock" and getattr(app.cfg, "shopee_mode", "global") == "site" else k, label)
                      for k, label in fields]
        controls.append(ft.Row([option_field(app, platform, k, label) for k, label in fields], wrap=True, spacing=12))
        if platform == "amazon" and section == "stock":
            _pfx = ft.TextField(label="SKU 前缀（可选）", width=220, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "amazon_sku_prefix", "") or ""),
                hint_text="例如 QH-")
            _sfx = ft.TextField(label="SKU 后缀（可选）", width=220, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "amazon_sku_suffix", "") or ""),
                hint_text="例如 -CA")
            _seq_sw = ft.Switch(label="前缀后自动加顺序号（1、2、3…）", active_color=ui.ACCENT,
                value=bool(getattr(app.cfg, "amazon_sku_sequence", True)))
            _preview = ft.Text("", size=12, color=ui.ACCENT_2, selectable=True)
            def _refresh_sku_preview(e=None):
                prefix = str(_pfx.value or "").strip()
                if prefix and bool(_seq_sw.value):
                    sep = "" if prefix.endswith(("-", "_")) else "-"
                    preview = f"{prefix}{sep}1、{prefix}{sep}2、{prefix}{sep}3 …"
                else:
                    preview = str(prefix) + "SKU-001" + str(_sfx.value or "")
                _preview.value = "编码示例：" + preview
                try: app._safe_update()
                except Exception: pass
            def _save_skus(e=None):
                app.cfg.set("amazon_sku_prefix", str(_pfx.value or ""))
                app.cfg.set("amazon_sku_suffix", str(_sfx.value or ""))
                app.cfg.set("amazon_sku_sequence", bool(_seq_sw.value))
                try:
                    save_config(app.cfg)
                    from ..core.platform_config import save as _save_platform
                    _save_platform(app.cfg, "amazon")
                except Exception:
                    pass
                _refresh_sku_preview()
            _pfx.on_change = _save_skus
            _sfx.on_change = _save_skus
            _seq_sw.on_change = _save_skus
            _refresh_sku_preview()
            controls += [ft.Row([_pfx, _sfx], wrap=True),
                         _seq_sw,
                         _preview,
                         ui.muted("填写即保存。开启顺序号后批量按前缀-1、前缀-2…统一编码；原始 SKU 不再拼接。", 11)]
            _contact = ft.TextField(label="制造商联系信息", width=300, color=ui.TEXT, bgcolor=ui.CARD_2,
                value=str(getattr(app.cfg, "amazon_manufacturer_contact", "Generic, CN") or ""),
                hint_text="例如 Generic, CN")
            _id_type = ft.TextField(label="产品 ID 类型（UPC/EAN/GTIN）", width=220, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "amazon_standard_product_type", "") or ""))
            _id_val = ft.TextField(label="产品 ID（留空不生成）", width=260, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "amazon_standard_product_id", "") or ""))
            _asin_tf = ft.TextField(label="Merchant Suggested ASIN（留空自动）", width=300, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "amazon_merchant_suggested_asin", "") or ""))
            _auto_asin = ft.Switch(label="自动生成缺失 Merchant ASIN", value=bool(getattr(app.cfg, "amazon_auto_merchant_asin", True)), active_color=ui.ACCENT)
            _gtin_sw = ft.Switch(label="已获批 GTIN/UPC 豁免（不填 UPC）", value=bool(getattr(app.cfg, "amazon_gtin_exemption", False)), active_color=ui.ACCENT)
            _auto_id = ft.Switch(label="自动生成缺失 UPC（仅在允许时开启）", value=bool(getattr(app.cfg, "amazon_auto_product_id", False)), active_color=ui.ACCENT)
            def _save_missing(e=None):
                vals = {
                    "amazon_manufacturer_contact": str(_contact.value or "").strip(),
                    "amazon_standard_product_type": str(_id_type.value or "").strip(),
                    "amazon_standard_product_id": str(_id_val.value or "").strip(),
                    "amazon_merchant_suggested_asin": str(_asin_tf.value or "").strip(),
                    "amazon_auto_merchant_asin": bool(_auto_asin.value),
                    "amazon_auto_product_id": bool(_auto_id.value),
                    "amazon_gtin_exemption": bool(_gtin_sw.value),
                }
                for k,v in vals.items(): app.cfg.set(k,v)
                try:
                    save_config(app.cfg)
                    from ..core.platform_config import save as _save_platform
                    _save_platform(app.cfg, "amazon")
                except Exception:
                    pass
            for _w in (_contact,_id_type,_id_val,_asin_tf,_auto_asin,_auto_id,_gtin_sw):
                try: _w.on_change = _save_missing
                except Exception: pass
            controls += [ft.Row([_contact, _id_type], wrap=True),
                         ft.Row([_id_val, _asin_tf], wrap=True),
                         ft.Row([_auto_asin, _auto_id, _gtin_sw], wrap=True),
                         ui.muted("填写即保存；Merchant ASIN 可自动生成，真实 UPC/EAN/GTIN 建议优先填写，不要虚构。", 11)]
            _defs = {}
            for _k,_label,_hint in (
                ("amazon_default_country_of_origin","默认原产国","CN"),
                ("amazon_default_included_components","默认包含组件","1"),
                ("amazon_default_unit_count","默认单位数","1"),
                ("amazon_default_power_source_type","默认动力来源","diesel"),
                ("amazon_default_voltage","默认电压","220"),
                ("amazon_default_dg_regulation","默认危险品规管","not_applicable"),
                ("amazon_default_dimensions","默认尺寸 cm（长x宽x高）","10x10x10"),
                ("amazon_upc_prefix","UPC 前缀（自动生成 UPC，如 012345）",""),
            ):
                _tf = ft.TextField(label=_label, width=220, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg,_k,_hint) or _hint))
                _defs[_k]=_tf
            def _save_defs(e=None):
                for _k,_tf in _defs.items(): app.cfg.set(_k,str(_tf.value or "").strip())
                if str(_defs.get("amazon_upc_prefix").value or "").strip():
                    app.cfg.set("amazon_auto_product_id", True)
                try:
                    save_config(app.cfg)
                    from ..core.platform_config import save as _sp
                    _sp(app.cfg, "amazon")
                except Exception: pass
            for _tf in _defs.values(): _tf.on_change=_save_defs
            controls += [ui.h1("缺失必填项默认值", 13),
                         ft.Row(list(_defs.values()), wrap=True, spacing=8),
                         ui.muted("填写即保存；开启智能属性后，发布前按类目 schema 使用。", 11)]
        if platform == "amazon" and section == "category":
            _kw = ft.TextField(label="采集箱关键词筛选（标题/SKU/ID）", width=420,
                color=ui.TEXT, bgcolor=ui.CARD_2,
                value=str(getattr(app.cfg, "amazon_collect_keyword", "")
                          or getattr(app.cfg, "amazon_batch_keyword", "") or ""),
                hint_text="运行自动处理时只处理包含该关键词的采集箱商品")
            _kw.on_change = lambda e: write(app, "amazon_collect_keyword", str(e.control.value or ""))
            _currency = ft.Dropdown(label="币种（留空按站点）", width=190, color=ui.TEXT, bgcolor=ui.CARD_2,
                value=str(getattr(app.cfg, "amazon_currency", "") or ""),
                options=[ft.dropdown.Option("", "按站点自动"), ft.dropdown.Option("USD", "USD 美元"),
                         ft.dropdown.Option("CAD", "CAD 加元"), ft.dropdown.Option("MXN", "MXN 墨西哥比索"),
                         ft.dropdown.Option("BRL", "BRL 巴西雷亚尔"), ft.dropdown.Option("GBP", "GBP 英镑"),
                         ft.dropdown.Option("EUR", "EUR 欧元"), ft.dropdown.Option("JPY", "JPY 日元"),
                         ft.dropdown.Option("AUD", "AUD 澳元"), ft.dropdown.Option("AED", "AED 迪拉姆"),
                         ft.dropdown.Option("SAR", "SAR 沙特里亚尔"), ft.dropdown.Option("INR", "INR 印度卢比"),
                         ft.dropdown.Option("SGD", "SGD 新加坡元")])
            _currency.on_change = lambda e: write(app, "amazon_currency", str(e.control.value or ""))
            _workers = ft.TextField(label="并发处理数（1-8）", width=170, color=ui.TEXT,
                bgcolor=ui.CARD_2, value=str(getattr(app.cfg, "concurrency", 4) or 4))
            def _save_workers(e=None):
                try:
                    n = max(1, min(8, int(float(_workers.value or 4))))
                except (TypeError, ValueError):
                    n = 4
                _workers.value = str(n)
                write(app, "concurrency", n)
            _workers.on_change = _save_workers
            _cat_text = ft.Text("", size=12, color=ui.ACCENT_2, selectable=True)
            def _cat_label():
                cid = str(getattr(app.cfg, "amazon_category_id", "") or "")
                cpath = str(getattr(app.cfg, "amazon_category_path", "") or "")
                cname = str(getattr(app.cfg, "amazon_category_name", "") or "")
                _cat_text.value = (f"已选类目：{cname or cid}" + (f" · {cpath}" if cpath else "")) if cid else "尚未选择类目"
                try: app._safe_update()
                except Exception: pass
            def _pick_cat(e=None):
                app._open_amazon_category_picker(str(_kw.value or ""), on_pick=lambda cid, path, name: (
                    app.cfg.set("amazon_category_id", cid),
                    app.cfg.set("amazon_category_path", path),
                    app.cfg.set("amazon_category_name", name),
                    save_config(app.cfg), _cat_label()))
            _cat_label()
            controls += [ft.Row([_kw, _currency, _workers], wrap=True, spacing=10), _cat_text,
                         ft.Row([ft.ElevatedButton("搜索/选择类目", icon=ft.Icons.SEARCH, on_click=_pick_cat)], wrap=True),
                         ui.muted("关键词只用于筛选采集箱商品；币种留空按站点，加拿大站点可手动选 CAD；并发数同时处理多个产品。", 11)]
        if platform == "amazon" and section == "category":
            controls.append(_inline_picker(app, platform, "amazon_collect_"))
            _tpl_tf = ft.TextField(label="模板产品 ID（可选，先读模板必填项）", width=420,
                color=ui.TEXT, bgcolor=ui.CARD_2,
                value=str(getattr(app.cfg, "template_product_id", "") or ""),
                hint_text="填写同店铺有完整属性的商品 ID，后续按模板补必填项")
            def _save_tpl(e=None):
                app.cfg.set("template_product_id", str(_tpl_tf.value or "").strip())
                try:
                    save_config(app.cfg)
                    from ..core.platform_config import save as _sp
                    _sp(app.cfg, "amazon")
                except Exception: pass
            _tpl_tf.on_change = _save_tpl
            controls += [_tpl_tf,
                         ui.muted("发布前会先检测模板商品的类目/必填属性，再按当前商品基础信息补全。", 11)]
        if section == "category":
            controls.append(ui.muted("默认逐商品沿用类目与属性。只有同类商品才填写统一类目；换类目后请先检查必填属性。"))
            if platform == "alibaba":
                _src = ft.Dropdown(label="模板来源", width=150, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg, "alibaba_template_source", "all") or "all"),
                    options=[ft.dropdown.Option("all", "全部"), ft.dropdown.Option("draft", "草稿/采集箱"),
                             ft.dropdown.Option("offline", "待发布"), ft.dropdown.Option("online", "在线"),
                             ft.dropdown.Option("waitPublish", "待发布"), ft.dropdown.Option("auditing", "审核中"),
                             ft.dropdown.Option("publishFail", "发布失败")])
                _src.on_change = lambda e: write(app, "alibaba_template_source", str(e.control.value or "all"))
                _cat = ft.Dropdown(label="类目来源", width=240, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg, "alibaba_category_source", "auto") or "auto"),
                    options=[ft.dropdown.Option("auto", "自动分类（关键词/商品类目/模板兜底）"),
                             ft.dropdown.Option("template", "使用模板产品类目")])
                _cat.on_change = lambda e: write(app, "alibaba_category_source", str(e.control.value or "auto"))
                _info = ft.Text("", size=12, color=ui.MUTED, selectable=True)

                def _refresh_tpl_info():
                    try:
                        from ..services.alibaba_template import load_snapshot
                        snap = load_snapshot(app.cfg)
                    except Exception:
                        snap = {}
                    if snap:
                        _info.value = (f"模板：{str(snap.get('title') or '')[:48]} · ID {snap.get('product_id') or '-'}"
                                       f" · 类目 {snap.get('category_id') or '-'} · 属性 {len(snap.get('attributes') or [])} 项"
                                       f" · 已保存 {str(snap.get('saved_at') or '')[:10]}")
                        _info.color = ui.ACCENT_2
                    else:
                        _info.value = "尚未保存模板参数：选择模板产品后点「保存模板到本地」"
                        _info.color = ui.MUTED
                _refresh_tpl_info()

                def _picked(pid, title):
                    app.cfg.set("alibaba_template_product_id", str(pid))
                    app.cfg.set("alibaba_template_product_name", str(title or ""))
                    save_config(app.cfg)
                    _info.value = f"已选择模板：{str(title or '')[:48]} · ID {pid}（下一步点「保存模板到本地」）"
                    _info.color = ui.ACCENT_2
                    try: app._safe_update()
                    except Exception: pass

                def _choose(e=None):
                    app._open_alibaba_template_picker(str(_src.value or "all"),
                                                      on_pick=_picked)
                def _save_tpl(e=None):
                    app._save_alibaba_template()
                    _refresh_tpl_info()
                def _auto_cat(e=None):
                    app._alibaba_auto_category_now()
                _auto_kw = ft.Switch(label="根据标题智能填写 3 个关键词",
                                     value=bool(getattr(app.cfg, "alibaba_auto_keywords", True)),
                                     active_color=ui.ACCENT)
                _auto_kw.on_change = lambda e: write(app, "alibaba_auto_keywords", bool(e.control.value))
                controls += [
                    ft.Row([_src, _cat,
                            ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH,
                                              on_click=_choose),
                            ft.OutlinedButton("保存模板到本地", icon=ft.Icons.SAVE,
                                              on_click=_save_tpl),
                            ft.OutlinedButton("自动选类目", icon=ft.Icons.AUTO_AWESOME,
                                              on_click=_auto_cat)], spacing=10, wrap=True),
                    _info, _auto_kw,
                    ft.Row([ft.OutlinedButton("设置必填属性", icon=ft.Icons.CHECKLIST,
                                              on_click=lambda e: app._open_alibaba_required_attrs()),
                            ui.muted("MOQ 在「价格与贸易 → 物流与包装」设置", 11)],
                           spacing=10, wrap=True)]
            if platform not in ("alibaba", "amazon"):
                controls.append(visual_editor(app, platform, "attributes" if platform != "tiktok" else "product_attributes",
                    "批量属性", [] if platform in ("shopee", "tiktok") else {}))
            controls += auxiliary_ai(app, platform, "attributes")
        if section == "stock" and platform != "alibaba":
            controls += auxiliary_ai(app, platform, "sku")
        if platform == "alibaba" and section == "price":
            controls += auxiliary_ai(app, platform, "sku")
            controls.append(visual_editor(app, platform, "price_tiers", "阶梯报价", []))

            def _alog_card():
                def _num(key, label, width=110):
                    c = ft.TextField(label=label, width=width, color=ui.TEXT, bgcolor=ui.CARD_2,
                                     value=str(getattr(app.cfg, key, "") or ""))
                    c.on_change = lambda e, _k=key: write(app, _k, str(e.control.value or ""))
                    return c
                _w = _num("alibaba_weight", "产品重量 KG")
                _l = _num("alibaba_package_length", "长 CM")
                _wd = _num("alibaba_package_width", "宽 CM")
                _h = _num("alibaba_package_height", "高 CM")
                def _size_changed(e=None):
                    vals = [str(getattr(x, "value", "") or "").strip() for x in (_l, _wd, _h)]
                    if all(vals):
                        app.cfg.set("alibaba_package_size", "X".join(vals))
                        save_config(app.cfg)
                for _x in (_l, _wd, _h):
                    _x.on_change = _size_changed
                _freight = ft.Dropdown(label="运费模板", width=250, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg, "alibaba_freight_template_id", "") or ""),
                    options=[ft.dropdown.Option(str(getattr(app.cfg, "alibaba_freight_template_id", "") or ""),
                                                str(getattr(app.cfg, "alibaba_default_freight_name", "买卖双方协商物流")))]
                    if getattr(app.cfg, "alibaba_freight_template_id", "") else [])
                _freight.on_change = lambda e: (write(app, "alibaba_freight_template_id", str(e.control.value or "")),
                                                write(app, "alibaba_default_freight_name",
                                                      next((o.text for o in _freight.options if o.key == e.control.value), "")))
                def _load_freight(e=None):
                    def work():
                        try:
                            from ..services.alibaba_publish import make_client
                            rows = make_client(app.cfg).freight_templates(str(getattr(app.cfg, "alibaba_shop_id", "") or ""))
                            opts = [ft.dropdown.Option(str(r.get("templateId")), str(r.get("templateName")))
                                    for r in rows if r.get("templateId")]
                            _freight.options = opts or _freight.options
                            cur = str(getattr(app.cfg, "alibaba_freight_template_id", "") or "")
                            if not cur and opts:
                                _freight.value = opts[0].key
                                write(app, "alibaba_freight_template_id", opts[0].key)
                            app._safe_update()
                        except Exception as ex:
                            app.toast(f"读取运费模板失败：{str(ex)[:80]}", False)
                    threading.Thread(target=work, daemon=True).start()
                _load_freight()
                _logi = ft.Dropdown(label="物流属性", width=190, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg, "alibaba_logistics_property", "general_cargo_0") or "general_cargo_0"),
                    options=[ft.dropdown.Option("general_cargo_0", "普货"),
                             ft.dropdown.Option("sensitive_goods", "敏感货")])
                _logi.on_change = lambda e: write(app, "alibaba_logistics_property", str(e.control.value or "general_cargo_0"))
                _sample = ft.Dropdown(label="特殊服务（样品服务）", width=200, color=ui.TEXT, bgcolor=ui.CARD_2,
                    value=str(getattr(app.cfg, "alibaba_default_sample_service", "0") or "0"),
                    options=[ft.dropdown.Option("0", "不支持"), ft.dropdown.Option("1", "支持")])
                _sample.on_change = lambda e: write(app, "alibaba_default_sample_service", str(e.control.value or "0"))
                # 三档发货期：可视化数量/天数，不再让用户写 JSON。
                try:
                    _tiers = json.loads(str(getattr(app.cfg, "alibaba_default_deliver_periods", "") or "[]"))
                except Exception:
                    _tiers = []
                _tiers = (_tiers + [{}, {}, {}])[:3]
                _tfields = []
                for i, t in enumerate(_tiers):
                    q = ft.TextField(label=f"≤数量{i+1}", width=110, color=ui.TEXT, bgcolor=ui.CARD_2,
                                     value=str(t.get("quantity") or ""))
                    d = ft.TextField(label=f"天数{i+1}", width=110, color=ui.TEXT, bgcolor=ui.CARD_2,
                                     value=str(t.get("process_period") or ""))
                    def _save_tiers(e=None):
                        rows = []
                        for qq, dd in _tfields:
                            try:
                                qv, dv = int(float(qq.value or 0)), int(float(dd.value or 0))
                            except Exception:
                                continue
                            if qv > 0 and dv > 0:
                                rows.append({"quantity": qv, "process_period": dv})
                        write(app, "alibaba_default_deliver_periods", json.dumps(rows, ensure_ascii=False))
                    q.on_change = _save_tiers; d.on_change = _save_tiers
                    _tfields.append((q, d))
                _moq = _num("alibaba_min_order_quantity", "最小起订量", 120)
                _stock = _num("alibaba_stock", "库存", 90)
                _hs = _num("alibaba_hs_code", "HS 编码", 130)
                return ui.card(ft.Column([
                    ui.h1("物流与包装", ui.FS_MD),
                    ui.muted("这里配置的是批量默认值；商品已有真实物流/包装数据时不会被覆盖。"),
                    ft.Row([_w, _l, _wd, _h], spacing=10, wrap=True),
                    ft.Row([_freight, ft.OutlinedButton("同步运费模板", icon=ft.Icons.SYNC,
                                                       on_click=_load_freight), _logi, _sample], spacing=10, wrap=True),
                    ui.muted("发货期（按数量分档）：", 11),
                    ft.Row([x for pair in _tfields for x in pair], spacing=8, wrap=True),
                    ft.Row([_moq, _stock, _hs], spacing=10, wrap=True),
                ], spacing=10))

            controls.append(_alog_card())
        if platform == "alibaba" and section == "company":
            from ..alibaba_company import FACTS, apply_profile
            controls += [ui.muted("公司介绍及 FAQ 沿用商品真实资料。此处填写后应用于本次批量；不会自动添加年限、认证或工厂声明。")]
            _local_fields = {}
            for key, label, width in (("alibaba_company_name", "公司名称（本地）", 300),
                                      ("alibaba_company_website", "公司网站（本地）", 300),
                                      ("alibaba_company_address", "公司地址（本地）", 300),
                                      ("alibaba_company_license", "营业执照号（本地）", 260)):
                field = ft.TextField(label=label, width=width, color=ui.TEXT, bgcolor=ui.CARD_2,
                                     value=str(getattr(app.cfg, key, "") or ""))
                field.on_change = lambda e, k=key: write(app, k, str(e.control.value or ""))
                _local_fields[key] = field
            controls.append(ft.Row(list(_local_fields.values()), spacing=10, wrap=True))
            _company_fields = {}
            for suffix, label in (("company_desc", "公司介绍（英文）"), ("company_faq", "公司 FAQ（英文）")):
                key = platform + "_collect_" + suffix
                field = ft.TextField(label=label, multiline=True, min_lines=4, color=ui.TEXT,
                    bgcolor=ui.CARD_2, value=str(getattr(app.cfg, key, "") or ""))
                field.on_change = lambda e, k=key: write(app, k, str(e.control.value or ""))
                _company_fields[suffix] = field
                controls.append(field)

            def _use_company(e=None):
                values = apply_profile(app.cfg, save_config)
                for suffix, source in (("company_desc", "alibaba_company_desc"),
                                       ("company_faq", "alibaba_company_faq")):
                    # 批量预设键也同步，批量任务才能真正带上公司资料。
                    app.cfg.set("alibaba_collect_" + suffix, values[source])
                    _company_fields[suffix].value = values[source]
                for key, field in _local_fields.items():
                    field.value = values.get(key, str(getattr(app.cfg, key, "") or ""))
                save_config(app.cfg)
                app.toast("已填入参考店铺公司资料", True)
                app._log("Alibaba 公司资料预设已填入（来源：参考店铺公开资料）", "INFO")
                app._safe_update()

            controls += [ft.Row([ft.ElevatedButton("填入参考店铺资料", icon=ft.Icons.BUSINESS,
                                                   bgcolor=ui.ACCENT_2, color=ui.ON_ACCENT,
                                                   on_click=_use_company)], spacing=8, wrap=True),
                         ui.muted("已核实事实：" + FACTS, 11)]
    elif section == "text":
        enabled = ft.Switch(label="启用自动AI文案（付费）", active_color=ui.ACCENT,
            value=bool(getattr(app.cfg, platform + "_collect_ai", False)))
        enabled.on_change = lambda e: write(app, platform + "_collect_ai", bool(e.control.value))
        controls += [enabled, ui.muted("基于每个商品的真实资料生成。默认「补齐缺失」：已有标题/详情/关键词不会调用 AI；"
                                       "要让 AI 重写已有文案，请把下方处理方式改为「重写所选字段」。")]
        mode = ft.Dropdown(label="文案处理方式", width=300, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(getattr(app.cfg, platform + "_collect_ai_mode", "missing")),
            options=[ft.dropdown.Option("missing", "补齐缺失字段（默认）"), ft.dropdown.Option("rewrite", "重写所选字段")])
        mode.on_change = lambda e: write(app, platform + "_collect_ai_mode", str(e.control.value))
        controls.append(mode)
        from ..services.platform_presets import ai_fields
        try:
            selected = json.loads(getattr(app.cfg, platform + "_collect_ai_fields", "[]") or "[]")
            if not isinstance(selected, list):
                raise ValueError()
        except (ValueError, TypeError):
            selected = []
            controls.append(ft.Text("AI字段选择格式无效，请重新勾选需要的字段。", color=ui.DANGER_C))
        names = {"title": "标题", "highlight": "商品亮点", "description": "描述", "bullets": "完整五点", "search_terms": "搜索词", "keywords": "关键词", "key_features": "五点（US）"}
        def selection(e, kind):
            try:
                current = json.loads(getattr(app.cfg, platform + "_collect_ai_fields", "[]"))
                current = current if isinstance(current, list) else []
            except (ValueError, TypeError):
                current = []
            current = [k for k in current if k != kind]
            if e.control.value:
                current.append(kind)
            write(app, platform + "_collect_ai_fields", json.dumps(current))
        checks = []
        for kind in ai_fields(platform):
            control = ft.Checkbox(label="AI " + names[kind], value=kind in selected)
            control.on_change = lambda e, k=kind: selection(e, k)
            checks.append(control)
        controls.append(ft.Row(checks, wrap=True))
        if platform == "alibaba":
            # Alibaba 行业预设直接写 alibaba_title/detail/sku/keyword 提示词。
            preset = app._alibaba_preset_control()
            if preset is not None:
                controls.append(preset)
        elif platform in ("tiktok", "shopee"):
            preset = app._industry_prompt_control()
            if preset is not None:
                controls.append(preset)
        controls += prompt_controls(app, platform, "text")
    elif section == "media":
        enabled = ft.Switch(label="自动处理商品图片（付费 AI + 图床）", active_color=ui.ACCENT,
            value=bool(getattr(app.cfg, platform + "_collect_images", False)))
        enabled.on_change = lambda e: write(app, platform + "_collect_images", bool(e.control.value))
        from .batch_media import controls as batch_media
        _video_row = []
        if platform == "alibaba":
            _vsrc = ft.Dropdown(label="商品视频来源", width=260, color=ui.TEXT, bgcolor=ui.CARD_2,
                value=str(getattr(app.cfg, "alibaba_video_source", "auto") or "auto"),
                options=[ft.dropdown.Option("auto", "采集视频优先，缺失用图片轮播生成"),
                         ft.dropdown.Option("collected", "只用采集到的视频"),
                         ft.dropdown.Option("generated", "用主图生成轮播视频"),
                         ft.dropdown.Option("none", "不要视频")])
            _vsrc.on_change = lambda e: write(app, "alibaba_video_source", str(e.control.value or "auto"))
            _video_row = [_vsrc, ui.muted("轮播视频本地 ffmpeg 合成，不消耗 AI 图片额度；视频不足 5 分项。")]
        controls += [enabled,
            *_video_row,
            ui.muted("为每个商品指定要处理的图片分组和位置。商品缺少对应图片时跳过该规则。"),
            batch_media(app, platform),
            ft.OutlinedButton("打开图片工作台", icon=ft.Icons.GRID_VIEW, on_click=lambda e: app._switch(9))]
        controls.append(image_prompt_editor(app, platform))
    # SKU and attribute rules are already provided by auxiliary_ai above.
    # The legacy Amazon scalar prompts use a different response format and
    # aren't read by collection processing; exposing both caused conflicts.
    if run_link:
        controls.append(ft.TextButton("前往自动处理", icon=ft.Icons.PLAY_ARROW,
                                      on_click=lambda e: app._switch(RUN_PAGE[platform])))
    return ui.card(ft.Column(controls, spacing=16))


def alibaba_rule_tabs(app, initial=3):
    indices = (3, 5, 30, 32, 31)
    labels = ("AI 文案", "AI 图片", "类目与属性", "价格与贸易", "公司资料")
    return _rule_tabs(app, "alibaba", indices, labels, initial)


def _rule_tabs(app, platform, indices, labels, initial):
    from . import app as ui
    tabs = ui.workspace_tabs([(label, []) for label in labels])
    tabs.selected_index = indices.index(initial)

    def show(e=None):
        pos = tabs.selected_index
        content = tabs.tabs[pos].content.content.controls
        if not content:
            item = app.active_platform.page(indices[pos])
            if app._page_allowed(item):
                content.append(functional_settings(app, platform, item.index, run_link=False))
            else:
                content.append(ui.card(ui.muted("此项功能尚未开通。")))
        if e is not None:
            app._safe_update()

    tabs.on_change = show
    show()
    return tabs


def alibaba_rules_page(app):
    from . import app as ui

    app._current = ALIBABA_RULES_PAGE
    return [ui.page_header("处理规则", "默认沿用每件商品的资料。只配置你想批量修改的字段。"),
            ft.Row([ft.ElevatedButton("去运行与结果", icon=ft.Icons.ARROW_FORWARD,
                                     on_click=lambda e: app._switch(RUN_PAGE["alibaba"]))], wrap=True),
            alibaba_rule_tabs(app)]


def amazon_rules_page(app):
    from . import app as ui

    app._current = AMAZON_RULES_PAGE
    indices = (19, 20, 17, 18, 34)
    tabs = _rule_tabs(app, "amazon", indices, tuple(app.active_platform.page(i).label for i in indices), 19)
    return [ui.page_header("处理规则", "只调整需要批量修改的项目。未设置的字段沿用各商品资料。"),
            ft.TextButton("去运行与结果", icon=ft.Icons.ARROW_FORWARD, on_click=lambda e: app._switch(21)), tabs]


def auxiliary_ai(app, platform, kind):
    from . import app as ui
    from ..services.collect_ai import SKU_PROMPT, ATTRIBUTE_PROMPT
    suffix = "ai_sku" if kind == "sku" else "ai_attributes"
    prompt_key = platform + "_collect_" + ("sku_prompt" if kind == "sku" else "attribute_prompt")
    default = SKU_PROMPT if kind == "sku" else ATTRIBUTE_PROMPT
    title = ("自动补齐已有变种的缺失SKU（付费AI）" if platform == "alibaba" else "自动生成缺失父SKU（付费AI）") if kind == "sku" else "AI建议有原文依据的缺失属性（付费）"
    switch = ft.Switch(label=title, active_color=ui.ACCENT, value=bool(getattr(app.cfg, platform + "_collect_" + suffix, False)))
    switch.on_change = lambda e: write(app, platform + "_collect_" + suffix, bool(e.control.value))
    _mode_dd = None
    if platform == "amazon" and kind == "sku":
        _mode_dd = ft.Dropdown(label="SKU 生成方式", width=300, color=ui.TEXT, bgcolor=ui.CARD_2,
            value=str(getattr(app.cfg, "amazon_sku_generate_mode", "local") or "local"),
            options=[ft.dropdown.Option("local", "规则生成（默认，不使用 AI）"),
                     ft.dropdown.Option("ai", "AI 提示词生成（付费）")])
        _mode_dd.on_change = lambda e: write(app, "amazon_sku_generate_mode", str(e.control.value or "local"))
        switch.label = "自动生成缺失 SKU"
    field = ft.TextField(label="自动SKU提示词" if kind == "sku" else "自动属性建议提示词", multiline=True,
        min_lines=4, max_lines=10, value=str(getattr(app.cfg, prompt_key, "") or default), color=ui.TEXT, bgcolor=ui.CARD_2)
    field.on_change = lambda e: write(app, prompt_key, str(e.control.value or ""))
    options = {"default": "通用保真", "concise": "简短易读", "strict": "严格引用原文"}
    chooser = ft.Dropdown(label="SKU规则预设" if kind == "sku" else "属性建议预设", width=300,
        color=ui.TEXT, bgcolor=ui.CARD_2, value=str(getattr(app.cfg, prompt_key + "_style", "default") or "default"),
        options=[ft.dropdown.Option(k, v) for k, v in options.items()])
    def choose(e):
        suffix = {"default": "", "concise": "\n保持输出简短；编码只保留可识别的核心词，属性值不要附加修饰。",
                  "strict": "\n再次核对每个输出值都有当前商品原文直接依据；不确定的属性一律留空。"}[str(e.control.value)]
        field.value = default + suffix
        app.cfg.set(prompt_key + "_style", str(e.control.value))
        write(app, prompt_key, field.value)
        app._safe_update()
    chooser.on_change = choose
    _lead = []
    if _mode_dd is not None:
        _lead = [_mode_dd, ui.muted("默认规则生成：原 SKU/型号/标题 + 商品 ID，不调用 AI；"
                                    "只有选择 AI 才读取下方提示词。前缀/后缀在 SKU 页填写，生成后自动叠加。", 11)]
    return [*_lead, switch, ui.muted("现有父SKU与变种编码保留，缺失编码加商品ID避免重复。" if kind == "sku" else
        "只采用原文证据与平台候选一致的建议；认证、产地、重量尺寸、识别码、价格库存等不会由AI猜测。"),
        ft.ExpansionTile(title=ft.Text("AI编码规则" if kind == "sku" else "AI属性建议规则", color=ui.TEXT), controls=[chooser, field])]


def wrap(app, platform, page, editor):
    """The ordinary page is batch settings; existing detailed editor is optional."""
    from . import app as ui
    tabs = ui.workspace_tabs([("自动处理设置", [functional_settings(app, platform, page)]),
        ("单品高级编辑", [ui.muted("仅调整当前商品的字段；文案与图片共用「自动处理设置」中的规则。自动处理无需载入单品。")])])
    loaded = [False]
    def load(e=None):
        if tabs.selected_index == 1 and not loaded[0] and app.active_platform.id == platform and app._page_idx == page:
            controls = editor() if callable(editor) else editor
            tabs.tabs[1].content.content.controls.extend(controls)
            loaded[0] = True
            app._safe_update()
    tabs.on_change = load
    load()
    return tabs


def runtime_controls(app, platform, *, embedded=False):
    """运行参数控件；embedded=True 时不带卡片标题，供「高级运行参数」折叠区合并。"""
    from . import app as ui
    from ..services.collect_runtime import PARAMETERS, PRESETS, parameter_value, apply_preset
    prefix = platform + "_collect_"
    editors = {}
    def current_label():
        try:
            actual = {key: parameter_value(key, getattr(app.cfg, prefix + key, spec[1])) for key, spec in PARAMETERS.items()}
            name = next((label for label, values in PRESETS.values() if values == actual), "自定义")
            return f"下次运行：{name} · 请求{actual['request_timeout']}秒 · 只读重试{actual['read_retries']}次 · 间隔{actual['interval']:g}秒 · 连续失败{actual['failure_limit'] or '不限'}件停止"
        except ValueError:
            return "运行参数无效，请修正下方标红字段"
    current = ft.Text(current_label(), color=ui.TEXT, selectable=True)
    for suffix, spec in PARAMETERS.items():
        label, default, *_ = spec
        field = ft.TextField(label=label, width=310, value=str(getattr(app.cfg, prefix + suffix, default)),
                             color=ui.TEXT, bgcolor=ui.CARD_2)
        def change(e, key=suffix, target=field):
            try:
                value = parameter_value(key, e.control.value)
                write(app, prefix + key, value)
                target.error_text = None
                record_error(app, platform, key)
            except ValueError as exc:
                target.error_text = str(exc)
                record_error(app, platform, key, str(exc))
            current.value = current_label()
            app._safe_update()
        field.on_change = change
        editors[suffix] = field
    workers = ft.TextField(label="并发处理数（1-8）", width=180,
        value=str(getattr(app.cfg, "concurrency", 4) or 4), color=ui.TEXT, bgcolor=ui.CARD_2)
    def change_workers(e):
        try:
            n = max(1, min(8, int(float(workers.value or 4))))
        except (TypeError, ValueError):
            n = 4
        workers.value = str(n)
        write(app, "concurrency", n)
        app._safe_update()
    workers.on_change = change_workers
    actual_preset = next((key for key, (_, values) in PRESETS.items() if all(getattr(app.cfg, prefix + k, None) == v for k, v in values.items())), None)
    chooser = ft.Dropdown(label="运行参数预设", width=250, value=actual_preset, color=ui.TEXT, bgcolor=ui.CARD_2,
        options=[ft.dropdown.Option(key, value[0]) for key, value in PRESETS.items()])
    preview = ft.Text("选择预设后点击应用；当前参数在下方查看。", color=ui.MUTED, size=12)
    def preview_preset(e):
        choice = PRESETS.get(e.control.value)
        if choice is None:
            preview.value = "请选择有效的运行预设"
        else:
            value = choice[1]
            preview.value = (f"{choice[0]}：请求{value['request_timeout']}秒，只读重试{value['read_retries']}次，"
                             f"间隔{value['interval']:g}秒，连续失败{value['failure_limit']}件停止；点击应用后生效。")
        app._safe_update()
    chooser.on_change = preview_preset
    def apply(e):
        try:
            apply_preset(app.cfg, platform, chooser.value)
        except ValueError as exc:
            app.toast(str(exc), False)
            return
        save_config(app.cfg)
        for key, field in editors.items():
            field.value = str(getattr(app.cfg, prefix + key))
            field.error_text = None
            record_error(app, platform, key)
        current.value = current_label()
        app.toast("已应用" + PRESETS[chooser.value][0] + "参数；下次任务生效")
        app._safe_update()
    if embedded:
        # 合并进「高级运行参数 / AI 预算」折叠区：直接展示字段，不再套一层折叠。
        return ft.Column([
            current,
            ft.Row([chooser, ft.OutlinedButton("应用运行预设", icon=ft.Icons.TUNE, on_click=apply)], wrap=True, spacing=12),
            preview,
            ft.Row([*editors.values(), workers], wrap=True, spacing=12),
            ui.muted("只调整当前平台的运行速度与保护阈值，保留商品设置和AI开关。并发数同时处理多个产品；网络异常不重发保存/发布。"),
        ], spacing=10)
    return ui.card(ft.Column([
        ui.h1("运行参数", ui.FS_MD),
        current,
        ft.Row([chooser, ft.OutlinedButton("应用运行预设", icon=ft.Icons.TUNE, on_click=apply)], wrap=True, spacing=12),
        preview,
        ft.ExpansionTile(title=ft.Text("调整运行参数", color=ui.TEXT), controls=[
            ft.Row([*editors.values(), workers], wrap=True, spacing=12)]),
        ui.muted("只调整当前平台的运行速度与保护阈值，保留商品设置和AI开关。并发数同时处理多个产品；网络异常不重发保存/发布。"),
    ], spacing=12))


def product_picker(app, platform, on_done=None):
    """通用商品选择器：搜索标题/ID、分页、全选本页/筛选结果。"""
    from . import app as ui
    from ..services.collect_automation import client_for, snapshot
    prefix = platform + "_collect_"
    try:
        old = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
        picked = set(str(x) for x in old if str(x).strip()) if isinstance(old, list) else set()
    except (ValueError, TypeError):
        picked = set()
    state = {"rows": [], "filtered": [], "page": 1, "kw": ""}
    PAGE = 50
    search = ft.TextField(hint_text="搜索标题或商品 ID", width=340, dense=True,
                          bgcolor=ui.CARD_2, border_color=ui.CARD_2, color=ui.TEXT)
    st = ft.Dropdown(width=150, dense=True, bgcolor=ui.CARD_2, border_color=ui.CARD_2,
                     color=ui.TEXT, value="draft",
                     options=[ft.dropdown.Option("draft", "采集箱"),
                              ft.dropdown.Option("editingRequired", "待发布"),
                              ft.dropdown.Option("publishFail", "发布失败")])
    status = ft.Text("输入条件后点「加载商品」", size=12.5, color=ui.MUTED)
    page_info = ft.Text("", size=12.5, color=ui.MUTED)
    sel_text = ft.Text(f"已选 {len(picked)} 个", size=12.5, color=ui.ACCENT_2)
    col = ft.Column(spacing=2, height=400, scroll=ft.ScrollMode.AUTO)
    dlg_holder = {}
    prev_btn = ft.OutlinedButton("上一页", on_click=lambda e: move(-1))
    next_btn = ft.OutlinedButton("下一页", on_click=lambda e: move(1))

    def upd():
        try:
            app.page.update()
        except Exception:
            pass

    def render():
        items = state["filtered"]
        total_pages = max(1, (len(items) + PAGE - 1) // PAGE)
        state["page"] = min(max(1, state["page"]), total_pages)
        start = (state["page"] - 1) * PAGE
        rows = []
        for it in items[start:start + PAGE]:
            pid = str(it.get("id") or it.get("idStr") or "")
            cb = ft.Checkbox(value=pid in picked, on_change=lambda e, _p=pid: toggle(_p, e.control.value))
            rows.append(ft.ListTile(
                leading=ft.Row([cb, _product_thumbnail(it)], spacing=6, tight=True),
                title=ft.Text((it.get("title") or it.get("subject") or it.get("productName")
                               or it.get("itemName") or pid)[:80], size=13.5,
                              color=ui.TEXT, max_lines=2),
                subtitle=ft.Text(pid, size=11.5, color=ui.MUTED), dense=True))
        col.controls = rows or [ui.muted("没有匹配的商品")]
        page_info.value = f"第 {state['page']}/{total_pages} 页 · 共 {len(items)} 个"
        prev_btn.disabled = state["page"] <= 1
        next_btn.disabled = state["page"] >= total_pages
        sel_text.value = f"已选 {len(picked)} 个"
        upd()

    def toggle(pid, on):
        (picked.add(pid) if on else picked.discard(pid))
        sel_text.value = f"已选 {len(picked)} 个"
        upd()

    def move(delta):
        state["page"] += delta
        render()

    def apply_filter():
        k = str(search.value or "").strip().lower()
        state["kw"] = k
        if k:
            state["filtered"] = [x for x in state["rows"]
                                 if k in str(x.get("title") or x.get("subject") or x.get("productName")
                                            or x.get("itemName") or "").lower()
                                 or k == str(x.get("id") or x.get("idStr") or "")]
        else:
            state["filtered"] = list(state["rows"])
        state["page"] = 1
        render()

    def load(e=None):
        status.value = "正在读取商品…"
        status.color = ui.MUTED
        upd()
        def work():
            try:
                cfg = app.cfg
                client = client_for(platform, cfg)
                sid = str(getattr(cfg, platform + "_shop_id", "") or "")
                site = str(getattr(cfg, platform + "_site", "") or "")
                rows = snapshot(client, platform, limit=0, state=str(st.value or "draft"),
                                shop_id=sid, site_id=site)
                state["rows"] = rows or []
                status.value = f"读取完成：{len(state['rows'])} 个"
                status.color = ui.ACCENT_2
                apply_filter()
            except Exception as exc:  # noqa: BLE001
                status.value = f"读取失败：{str(exc)[:120]}"
                status.color = ui.DANGER_C
                upd()
        threading.Thread(target=work, daemon=True).start()

    def select_page(on=True):
        start = (state["page"] - 1) * PAGE
        for it in state["filtered"][start:start + PAGE]:
            pid = str(it.get("id") or it.get("idStr") or "")
            (picked.add(pid) if on else picked.discard(pid))
        render()

    def select_all(on=True):
        if on:
            for it in state["filtered"]:
                picked.add(str(it.get("id") or it.get("idStr") or ""))
        else:
            picked.clear()
        render()

    def save(e=None):
        write(app, prefix + "selected_ids", json.dumps(sorted(picked), ensure_ascii=False))
        app._close_dlg(dlg_holder.get("dlg"))
        if on_done:
            on_done()
        app.toast(f"已选择 {len(picked)} 个商品；运行只处理这些商品" if picked
                  else "已清空商品选择；运行将处理全部符合条件商品", True)

    search.on_submit = lambda e: apply_filter()
    dlg = ft.AlertDialog(
        title=ft.Text("选择要处理的商品"),
        content=ft.Column([
            ft.Row([search, st,
                    ft.ElevatedButton("加载商品", icon=ft.Icons.REFRESH, on_click=load),
                    ft.TextButton("查询", icon=ft.Icons.SEARCH, on_click=lambda e: apply_filter())],
                   spacing=8, wrap=True),
            status, col,
            ft.Row([prev_btn, next_btn, page_info,
                    ft.Container(expand=True), sel_text], spacing=8),
            ft.Row([ft.TextButton("全选本页", on_click=lambda e: select_page(True)),
                    ft.TextButton("全选筛选结果", on_click=lambda e: select_all(True)),
                    ft.TextButton("清空选择", on_click=lambda e: select_all(False))], spacing=8),
        ], spacing=8, width=760),
        actions=[ft.TextButton("取消", on_click=lambda e: app._close_dlg(dlg_holder.get("dlg"))),
                 ft.ElevatedButton("只处理选中商品", bgcolor=ui.ACCENT, color=ui.ON_ACCENT,
                                   on_click=save)],
    )
    dlg_holder["dlg"] = dlg
    app._show_dlg(dlg)
    load()

def _inline_picker(app, platform, prefix):
    """内联商品选择器：状态筛选 + 关键词 + 勾选，直接嵌入页面（不弹窗）。

    复用 collect_automation.snapshot 读取商品；勾选写入 {platform}_collect_selected_ids。
    generation counter：每次 load() 时递增，旧线程在写 UI 前检查，若已过期则退出，
    避免切换页面后旧线程触发 page.update() 导致视觉干扰。
    """
    from . import app as ui
    from ..services.collect_automation import client_for, snapshot
    try:
        old = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
        picked = set(str(x) for x in old if str(x).strip()) if isinstance(old, list) else set()
    except (ValueError, TypeError):
        picked = set()
    state = {"rows": [], "filtered": [], "page": 1, "kw": "", "loaded": False, "gen": 0}
    PAGE = 50

    search = ft.TextField(label="标题或商品 ID 筛选", hint_text="留空显示全部；加载后在此进一步筛选",
                          width=320, dense=True, bgcolor=ui.CARD_2, color=ui.TEXT)
    st = ft.Dropdown(label="商品状态", width=170, dense=True, bgcolor=ui.CARD_2, color=ui.TEXT,
                    value="draft", options=[ft.dropdown.Option("draft", "采集箱（草稿）"),
                     ft.dropdown.Option("editingRequired", "待发布"),
                     ft.dropdown.Option("publishFail", "发布失败")])
    status = ft.Text("点「加载商品」读取采集箱", size=12.5, color=ui.MUTED)
    page_info = ft.Text("", size=12.5, color=ui.MUTED)
    sel_text = ft.Text(f"已选 {len(picked)} 个", size=12.5, color=ui.ACCENT_2)
    col = ft.Column(spacing=2, height=320, scroll=ft.ScrollMode.AUTO)
    prev_btn = ft.OutlinedButton("上一页", on_click=lambda e: move(-1))
    next_btn = ft.OutlinedButton("下一页", on_click=lambda e: move(1))

    def upd():
        try:
            app.page.update()
        except Exception:
            pass

    def render():
        items = state["filtered"]
        total_pages = max(1, (len(items) + PAGE - 1) // PAGE)
        state["page"] = min(max(1, state["page"]), total_pages)
        start = (state["page"] - 1) * PAGE
        rows = []
        for it in items[start:start + PAGE]:
            pid = str(it.get("id") or it.get("idStr") or "")
            cb = ft.Checkbox(value=pid in picked, on_change=lambda e, _p=pid: toggle(_p, e.control.value))
            title = (it.get("title") or it.get("subject") or it.get("productName")
                     or it.get("itemName") or pid)
            rows.append(ft.ListTile(
                leading=ft.Row([cb, _product_thumbnail(it)], spacing=6, tight=True),
                title=ft.Text(str(title)[:80], size=13.5, color=ui.TEXT, max_lines=2),
                subtitle=ft.Text(pid, size=11.5, color=ui.MUTED), dense=True))
        col.controls = rows or [ui.muted("没有匹配的商品；点「加载商品」读取采集箱")]
        page_info.value = f"第 {state['page']}/{total_pages} 页 · 共 {len(items)} 个"
        prev_btn.disabled = state["page"] <= 1
        next_btn.disabled = state["page"] >= total_pages
        sel_text.value = f"已选 {len(picked)} 个"
        upd()

    def toggle(pid, on):
        (picked.add(pid) if on else picked.discard(pid))
        sel_text.value = f"已选 {len(picked)} 个"
        write(app, prefix + "selected_ids", json.dumps(sorted(picked), ensure_ascii=False))
        upd()

    def move(delta):
        state["page"] += delta
        render()

    def apply_filter():
        k = str(search.value or "").strip().lower()
        state["kw"] = k
        if k:
            state["filtered"] = [x for x in state["rows"]
                                 if k in str(x.get("title") or x.get("subject") or x.get("productName")
                                            or x.get("itemName") or "").lower()
                                 or k == str(x.get("id") or x.get("idStr") or "")]
        else:
            state["filtered"] = list(state["rows"])
        state["page"] = 1
        render()

    def load(e=None):
        # 递增 generation，使旧线程发现自己已过期
        state["gen"] += 1
        gen = state["gen"]
        status.value = "正在读取商品…"
        status.color = ui.MUTED
        upd()
        def work():
            try:
                client = client_for(platform, app.cfg)
                sid = str(getattr(app.cfg, platform + "_shop_id", "") or "")
                site = str(getattr(app.cfg, platform + "_site", "") or "")
                rows = snapshot(client, platform, limit=0, state=str(st.value or "draft"),
                                shop_id=sid, site_id=site)
                if state["gen"] != gen:   # 页面已切走，丢弃结果
                    return
                state["rows"] = rows or []
                state["loaded"] = True
                status.value = f"读取完成：{len(state['rows'])} 个"
                status.color = ui.ACCENT_2
                apply_filter()
            except Exception as exc:  # noqa: BLE001
                if state["gen"] != gen:
                    return
                status.value = f"读取失败：{str(exc)[:120]}"
                status.color = ui.DANGER_C
                upd()
        threading.Thread(target=work, daemon=True).start()

    def select_page(on=True):
        start = (state["page"] - 1) * PAGE
        for it in state["filtered"][start:start + PAGE]:
            pid = str(it.get("id") or it.get("idStr") or "")
            (picked.add(pid) if on else picked.discard(pid))
        write(app, prefix + "selected_ids", json.dumps(sorted(picked), ensure_ascii=False))
        render()

    def select_all(on=True):
        if on:
            for it in state["filtered"]:
                picked.add(str(it.get("id") or it.get("idStr") or ""))
        else:
            picked.clear()
        write(app, prefix + "selected_ids", json.dumps(sorted(picked), ensure_ascii=False))
        render()

    def clear_all(e=None):
        picked.clear()
        write(app, prefix + "selected_ids", "[]")
        render()

    search.on_submit = lambda e: apply_filter()

    return ui.card(ft.Column([
        ui.h1i(ft.Icons.CHECKLIST, "选择商品", ui.FS_MD),
        ui.muted("选择要处理的商品：勾选后只处理选中商品；不勾选则处理全部符合条件商品。", 11),
        ft.Row([st, search,
                ft.ElevatedButton("加载商品", icon=ft.Icons.REFRESH, on_click=load),
                ft.TextButton("筛选", icon=ft.Icons.SEARCH, on_click=lambda e: apply_filter())],
               spacing=8, wrap=True),
        status, col,
        ft.Row([prev_btn, next_btn, page_info, ft.Container(expand=True), sel_text], spacing=8),
        ft.Row([ft.TextButton("全选本页", on_click=lambda e: select_page(True)),
                ft.TextButton("全选筛选结果", on_click=lambda e: select_all(True)),
                ft.TextButton("清空选择", on_click=clear_all)], spacing=8),
    ], spacing=10))


def _cost_estimate(app, platform, prefix, *, embedded=False):
    """费用预估：根据选中数量和配置实时估算。embedded=True 只返回内容，供合并卡片使用。"""
    from . import app as ui
    cost_text = ft.Text("加载中…", size=13, color=ui.TEXT, selectable=True)

    def refresh():
        try:
            count = int(getattr(app.cfg, prefix + "limit", 10) or 0)
            if not count:
                # 用已选商品数，否则提示「全部」
                try:
                    _sel = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
                    count = len(_sel) if isinstance(_sel, list) and _sel else 10
                except (ValueError, TypeError):
                    count = 10
            from ..services.collect_budget import estimate
            exp = estimate(app.cfg, platform, count)
            if exp is None:
                cost_text.value = "需填写预算单价才能估算金额"
                return
            text_n, img_n, money = exp["text"], exp["images"], exp["estimate"]
            parts = []
            if text_n:
                parts.append(f"文案 {text_n} 次")
            if img_n:
                parts.append(f"图片 {img_n} 次")
            if money is not None:
                parts.append(f"约 {money:g} 元")
            if not parts:
                cost_text.value = "本次未启用 AI；保留商品原文/原图，无额外费用"
            else:
                time_est = max(1, count * 3)
                cost_text.value = f"处理 {count} 件 · {' · '.join(parts)} · 预计 {time_est} 分钟"
        except Exception as exc:  # noqa: BLE001
            cost_text.value = f"预估失败：{str(exc)[:60]}"

    refresh()
    # 暴露刷新方法，供 limit/选择变化时调用
    app._cost_refresh = refresh
    note = ui.muted("实际费用以服务商账单为准；缓存命中和缺项商品会减少调用。", 11)
    if embedded:
        return ft.Column([cost_text, note], spacing=6)
    return ui.card(ft.Column([
        ui.h1i(ft.Icons.ACCOUNT_BALANCE_WALLET, "费用预估", ui.FS_MD),
        cost_text,
        note,
    ], spacing=8))


def _queue_tab(app, platform, prefix, *, embedded=False):
    """任务队列：多任务排队、顺序执行。embedded=True 只返回内容，供折叠卡片使用。"""
    from . import app as ui
    from ..services import task_profiles as _tp

    q_name = ft.TextField(label="任务名称（如 挖掘机-美国站）", width=260,
                          bgcolor=ui.CARD_2, color=ui.TEXT)
    q_keyword = ft.TextField(label="关键词（可选）", width=240,
                              value=str(getattr(app.cfg, prefix + "keyword", "") or ""),
                              bgcolor=ui.CARD_2, color=ui.TEXT)
    q_qty = ft.TextField(label="数量（0=全部）", width=140,
                         value=str(getattr(app.cfg, prefix + "limit", 10)),
                         bgcolor=ui.CARD_2, color=ui.TEXT)
    q_list = ft.Column(spacing=6)

    def upd():
        try:
            app.page.update()
        except Exception:
            pass

    def refresh_list():
        try:
            jobs = [j for j in _tp.list_tasks()
                    if str(j.get("status") or "idle") != "idle"]
        except Exception:
            jobs = []
        rows = []
        for j in jobs:
            st = str(j.get("status") or "idle")
            label = {"pending": "等待执行", "running": "执行中", "done": "已完成",
                     "failed": "失败", "idle": "未加入"}.get(st, st)
            color = {"pending": ui.ACCENT_2, "running": ui.ACCENT, "done": ui.SUCCESS_C,
                     "failed": ui.DANGER_C}.get(st, ui.MUTED)
            rows.append(ft.Row([
                ft.Column([
                    ft.Text(str(j.get("name") or "-"), size=13, color=ui.TEXT,
                            weight=ft.FontWeight.BOLD),
                    ft.Text(f"{j.get('platform') or '-'} · 关键词 {j.get('keyword') or '全部'} · "
                            f"数量 {j.get('quantity') or '全部'} · "
                            f"商品 {len(j.get('selected_products') or []) or '全部'}",
                            size=11.5, color=ui.MUTED),
                ], spacing=1, expand=True),
                ft.Text(label, size=11.5, color=color, width=70),
                ft.TextButton("执行", icon=ft.Icons.PLAY_ARROW,
                              on_click=lambda e, _j=j: app._start_config_queue([_j])),
                ft.TextButton("日志", icon=ft.Icons.RECEIPT_LONG,
                              on_click=lambda e, _j=j: app._show_config_job_logs(_j)),
                ft.TextButton("删除", icon=ft.Icons.DELETE_OUTLINE,
                              on_click=lambda e, _j=j: app._delete_config_job(_j)),
            ], spacing=6, wrap=True))
        q_list.controls = rows or [ui.muted("队列为空：配置好软件后，填写名称并加入队列", 12)]
        upd()

    def enqueue(e=None):
        name = str(q_name.value or "").strip()
        if not name:
            app.toast("请填写任务名称", False)
            return
        try:
            qty = max(0, int(q_qty.value or 0))
        except (ValueError, TypeError):
            qty = 0
        try:
            _sel = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
            selected = [str(x) for x in _sel if str(x).strip()] if isinstance(_sel, list) else []
        except (ValueError, TypeError):
            selected = []
        try:
            app._save_api_fields()
        except Exception:
            pass
        kw = str(q_keyword.value or "").strip()
        try:
            app.cfg.set(platform + "_collect_keyword", kw)
        except Exception:
            pass
        try:
            _tp.enqueue(name, app.cfg, platform, keyword=kw, quantity=qty,
                        selected_products=selected)
            q_name.value = ""
            app.toast(f"已加入队列：{name}")
            app._log(f"已加入队列：{name}（{platform}，关键词 {kw or '全部'}）")
            refresh_list()
        except Exception as ex:
            app.toast(f"加入队列失败：{str(ex)[:80]}", False)

    def run_all(e=None):
        try:
            jobs = _tp.pending_jobs()
        except Exception:
            jobs = []
        if not jobs:
            app.toast("没有等待执行的队列任务", False)
            return
        app._start_config_queue(jobs)

    def clear_finished(e=None):
        try:
            n = _tp.clear_finished()
            refresh_list()
            app.toast(f"已清理 {n} 条队列任务" if n else "没有可清理的任务")
        except Exception as ex:
            app.toast(str(ex), False)

    app._refresh_inline_queue = refresh_list
    refresh_list()

    body = [
        ui.muted("把当前配置加入队列，可排队多条任务，点「执行全部」顺序处理。"
                 "每条任务是独立配置快照，互不影响。要并行请用设置里的「新实例运行」。", 11),
        ft.Row([q_name, q_keyword, q_qty,
                ft.ElevatedButton("加入队列", icon=ft.Icons.ADD_TASK,
                                  bgcolor=ui.ACCENT, color=ui.ON_ACCENT, on_click=enqueue)],
               spacing=8, wrap=True),
        ft.Row([ft.ElevatedButton("执行全部待执行", icon=ft.Icons.PLAY_CIRCLE_FILL,
                                  bgcolor=ui.ACCENT, color=ui.ON_ACCENT, on_click=run_all),
                ft.OutlinedButton("清理已完成/失败", icon=ft.Icons.CLEANING_SERVICES,
                                  on_click=clear_finished)], spacing=8),
        q_list,
    ]
    if embedded:
        return ft.Column(body, spacing=10)
    return ui.card(ft.Column([
        ui.h1i(ft.Icons.QUEUE, "任务队列（多任务批量处理）", ui.FS_MD),
        *body,
    ], spacing=10))


def _card_body(panel, drop_texts=()):
    """拆出 ui.card(...) 的内部控件，用于把多张卡片合并成一张。"""
    content = getattr(panel, "content", None)
    controls = list(getattr(content, "controls", []) or []) if isinstance(content, ft.Column) else [panel]
    if drop_texts:
        controls = [c for c in controls
                    if not (isinstance(c, ft.Text) and str(getattr(c, "value", "") or "") in drop_texts)]
    return controls


def _alibaba_task_card(app, platform, prefix, keyword, limit, selected_text, op, repair, summary, halted):
    """页面 33「本次任务」：合并原处理范围与操作 + 费用预估；运行参数/AI 预算收进折叠区。"""
    from . import app as ui
    shop_id = str(getattr(app.cfg, platform + "_shop_id", "") or "")
    shop = str(getattr(app.cfg, platform + "_shop_name", "") or shop_id) if shop_id else "未选店铺"
    advanced = ft.ExpansionTile(
        title=ft.Text("高级运行参数 / AI 预算", color=ui.TEXT),
        controls=[
            ui.muted("运行速度与保护：只影响本次任务的请求节奏，不改商品设置和 AI 开关。", 11),
            runtime_controls(app, platform, embedded=True),
            ui.muted("AI 预算与结果复用：按本次任务预约调用；实际费用以服务商账单为准。", 11),
            budget_controls(app, platform, embedded=True),
        ],
        initially_expanded=False)
    return ui.card(ft.Column([
        ui.h1i(ft.Icons.TUNE, "本次任务", ui.FS_MD),
        ft.Row([
            ui.muted("当前店铺：" + shop),
            ft.TextButton("连接与店铺", on_click=lambda e: app._switch(CONNECTION_PAGE[platform])),
            ft.TextButton("去采集箱商品", icon=ft.Icons.INVENTORY_2_OUTLINED,
                          on_click=lambda e: app._switch(29)),
        ], wrap=True, spacing=10),
        ft.Row([keyword, limit], wrap=True, spacing=12),
        selected_text,
        ui.muted("默认前 10 件、保存草稿、保留原商品资料。关键词匹配后再限制数量；每件失败原因写入日志。"),
        ft.Row([op, repair], wrap=True, spacing=12),
        ui.muted("低于 4.5 分时用商品已有资料补齐并复评。只有已启用的 AI 文案字段会追加调用。"),
        ft.Row([
            ft.OutlinedButton("检查本次范围", icon=ft.Icons.FACT_CHECK,
                on_click=lambda e: launch(app, platform, dry_run=True)),
            ft.ElevatedButton("开始自动处理", icon=ft.Icons.PLAY_ARROW,
                on_click=lambda e: launch(app, platform)),
            ft.OutlinedButton("停止", icon=ft.Icons.STOP, on_click=lambda e: app._request_stop()),
            ft.TextButton("查看全过程日志", on_click=lambda e: app._switch(10)),
            ft.TextButton("检查配置冲突", on_click=lambda e: app._check_conflicts()),
            ft.TextButton("查看任务结果", on_click=lambda e: app.content.scroll_to(key="collect-results", duration=200)),
        ], spacing=10, wrap=True),
        ft.Text(summary, color=ui.TEXT, selectable=True),
        *([ft.Text("运行已停止：" + halted, color=ui.DANGER_C, selectable=True)] if halted else []),
        _cost_estimate(app, platform, prefix, embedded=True),
        advanced,
    ], spacing=14))


def _alibaba_results_card(app, platform, attempts):
    """页面 33「结果与修复」：合并任务结果面板与发布失败自动处理。"""
    from . import app as ui
    panel = results_panel(app, platform)
    body = _card_body(panel, drop_texts=("任务结果",))
    card = ui.card(ft.Column([
        ui.h1i(ft.Icons.FACT_CHECK, "结果与修复", ui.FS_MD),
        ui.muted("先回读当前错误。审核中自动跳过；确定的格式与字段问题自动修复；经营权限、真实识别码和缺失资料会列出处理办法。"),
        attempts,
        ft.Row([
            ft.OutlinedButton("分析发布失败", icon=ft.Icons.FACT_CHECK,
                on_click=lambda e: launch(app, platform, dry_run=True, failures=True)),
            ft.ElevatedButton("修复发布失败商品", icon=ft.Icons.AUTO_FIX_HIGH,
                on_click=lambda e: launch(app, platform, failures=True)),
        ], spacing=10, wrap=True),
        ui.muted("修复使用「本次任务」的数量、关键词与完成后去向。默认保存修复结果；选择提交发布时，仍有阻断项的商品不会重发。"),
        *body,
    ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
    # 「查看任务结果」按钮滚动到这里；collect_results 的卡片标题不再单独出现。
    card.key = "collect-results"
    return card


def _alibaba_queue_card(app, platform, prefix):
    """页面 33「任务队列（默认折叠）」：保留原队列全部功能，默认收起。"""
    from . import app as ui
    body = _queue_tab(app, platform, prefix, embedded=True)
    return ui.card(ft.Column([
        ft.ExpansionTile(
            leading=ft.Icon(ft.Icons.QUEUE, color=ui.ACCENT),
            title=ft.Text("任务队列（默认折叠）", color=ui.TEXT, weight=ft.FontWeight.BOLD, size=ui.FS_MD),
            controls=[body],
            initially_expanded=False,
        ),
    ], spacing=4))


def run_page(app, platform):
    from . import app as ui
    app._current = RUN_PAGE[platform]
    prefix = platform + "_collect_"
    alibaba = platform == "alibaba"
    amazon = platform == "amazon"
    simple = alibaba or amazon

    keyword = ft.TextField(label="采集箱关键词（可选）", hint_text="留空自动处理全部；可按标题、SKU 或 ID 筛选",
        width=430, value=str(getattr(app.cfg, prefix + "keyword", "") or ""), color=ui.TEXT, bgcolor=ui.CARD_2)
    keyword.on_change = lambda e: write(app, prefix + "keyword", str(e.control.value or ""))
    limit = ft.TextField(label="本次处理数量（0 = 全部）", width=220,
        value=str(getattr(app.cfg, prefix + "limit", 10)), color=ui.TEXT, bgcolor=ui.CARD_2)
    try:
        _sel0 = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
        _sel_n = len(_sel0) if isinstance(_sel0, list) else 0
    except (ValueError, TypeError):
        _sel_n = 0
    selected_text = ft.Text(f"已选商品：{_sel_n} 个（未选择=全部符合条件商品）" if _sel_n
                            else "未选择商品：处理全部符合条件商品", size=12.5,
                            color=ui.ACCENT_2 if _sel_n else ui.MUTED)

    def _refresh_selected(e=None):
        try:
            _picked = json.loads(str(getattr(app.cfg, prefix + "selected_ids", "") or "[]"))
            _n = len(_picked) if isinstance(_picked, list) else 0
        except (ValueError, TypeError):
            _n = 0
        selected_text.value = (f"已选商品：{_n} 个（未选择=全部符合条件商品）" if _n
                               else "未选择商品：处理全部符合条件商品")
        selected_text.color = ui.ACCENT_2 if _n else ui.MUTED
        try: app._safe_update()
        except Exception: pass

    select_btn = ft.OutlinedButton(
        "手动选择商品", icon=ft.Icons.CHECKLIST,
        on_click=lambda e: product_picker(app, platform, on_done=_refresh_selected))

    def change_limit(e):
        try:
            value = int(e.control.value)
            if value < 0:
                raise ValueError()
            write(app, prefix + "limit", value)
            limit.error_text = None
            record_error(app, platform, "limit")
        except (TypeError, ValueError):
            limit.error_text = "请输入大于等于 0 的整数"
            record_error(app, platform, "limit", limit.error_text)
        if hasattr(app, "_cost_refresh"):
            app._cost_refresh()
        app._safe_update()
    limit.on_change = change_limit
    op = ft.Dropdown(label="完成后去向", width=230, value=str(getattr(app.cfg, prefix + "op", "1") or "1"),
        color=ui.TEXT, bgcolor=ui.CARD_2, options=[ft.dropdown.Option("1", "保存草稿（默认）"),
            ft.dropdown.Option("3", "移入待发布"), ft.dropdown.Option("2", "提交发布")])
    op.on_change = lambda e: write(app, prefix + "op", str(e.control.value))
    results = [] if platform == "amazon" else getattr(app, "_collect_results", {}).get(platform, [])
    try:
        from ..services import collect_store as store
        recent = store.latest(app.cfg, platform)
        if recent:
            results = [item["result"] for item in recent["items"] if item["result"]]
    except (OSError, ValueError, sqlite3.Error):
        pass
    repair = ft.Switch(label="低分商品自动补齐并复评" if platform == "alibaba" else "自动修复可确定的问题",
        active_color=ui.ACCENT, value=bool(getattr(app.cfg, prefix + "auto_repair", True)))
    repair.on_change = lambda e: write(app, prefix + "auto_repair", bool(e.control.value))
    attempts = ft.Dropdown(label="发布失败最多处理次数", width=230,
        color=ui.TEXT, bgcolor=ui.CARD_2, value=str(getattr(app.cfg, prefix + "max_attempts", 2)),
        options=[ft.dropdown.Option(str(i), str(i) + " 次") for i in (1, 2, 3)])
    attempts.on_change = lambda e: write(app, prefix + "max_attempts", int(e.control.value))
    recovery = []
    if platform == "amazon":
        recovery = [ui.card(ft.Column([ui.h1("发布失败自动处理", ui.FS_MD),
            ui.muted("先回读当前错误。审核中自动跳过；确定的格式与字段问题自动修复；经营权限、真实识别码和缺失资料会列出处理办法。"),
            attempts, ft.Row([
                ft.OutlinedButton("分析发布失败", icon=ft.Icons.FACT_CHECK,
                    on_click=lambda e: launch(app, platform, dry_run=True, failures=True)),
                ft.ElevatedButton("修复发布失败商品", icon=ft.Icons.AUTO_FIX_HIGH,
                    on_click=lambda e: launch(app, platform, failures=True))], spacing=10, wrap=True),
            ui.muted("修复使用上方数量、关键词与完成后去向。默认保存修复结果；选择提交发布时，仍有阻断项的商品不会重发。"),
        ], spacing=12))]
    summary = "尚未运行" if not results else f"已处理 {len(results)} 件 · 成功 {sum(r['ok'] for r in results)} · 失败 {sum(not r['ok'] for r in results)}"
    halted = results[-1].get("halt_reason", "") if results else ""
    if alibaba:
        # 页面 33 固定 4 张卡：发品进度 → 本次任务 → 结果与修复 → 任务队列（默认折叠）。
        return [
            ui.page_header("检查与发布",
                           "按六步流程：补齐发品资料 → 设置本次任务 → 开始处理 → 结果与修复；选品统一在「采集箱商品」。"),
            alibaba_readiness(app),
            _alibaba_task_card(app, platform, prefix, keyword, limit, selected_text, op, repair, summary, halted),
            _alibaba_results_card(app, platform, attempts),
            _alibaba_queue_card(app, platform, prefix),
        ]
    extra = [runtime_controls(app, platform), budget_controls(app, platform), *recovery]
    shop_info = []
    if simple:
        if amazon:
            extra.insert(0, repair)
        extra = [ui.card(ft.ExpansionTile(title=ft.Text("高级选项与发布失败处理", color=ui.TEXT), controls=extra))]
        shop_id = str(getattr(app.cfg, platform + "_shop_id", "") or "")
        shop = str(getattr(app.cfg, platform + "_shop_name", "") or shop_id) if shop_id else "未选店铺"
        site = str(getattr(app.cfg, "amazon_site_code", "") or getattr(app.cfg, "amazon_site", "") or "未选站点")
        shop_info = [ft.Row([ui.muted("当前店铺：" + shop + (" · " + site if amazon else "")),
            ft.TextButton("连接与店铺", on_click=lambda e: app._switch(CONNECTION_PAGE[platform]))], wrap=True)]

    # 立即处理 Tab：Amazon 选品卡已移到「处理规则 → 类目与属性」。
    picker_card = None if amazon else _inline_picker(app, platform, prefix)
    cost_card = _cost_estimate(app, platform, prefix)
    _range_head = (ft.Row([op, limit], wrap=True, spacing=12) if amazon
                   else ft.Row([keyword, limit], wrap=True, spacing=12))
    range_card = ui.card(ft.Column([ui.h1("处理范围与操作", ui.FS_MD), *shop_info,
        _range_head,
        ft.Row([selected_text, select_btn], wrap=True, spacing=10),
        ui.muted("默认前 10 件、保存草稿、保留原商品资料。关键词在「处理规则 → 类目与属性」填写；每件失败原因写入日志。"),
        *([] if amazon else [op]), *([repair, ui.muted("低于 4.5 分时用商品已有资料补齐并重评。只有已启用的 AI 文案字段会追加调用。")]
              if alibaba else []), ft.Row([
            ft.OutlinedButton("检查本次范围", icon=ft.Icons.FACT_CHECK,
                on_click=lambda e: launch(app, platform, dry_run=True)),
            ft.ElevatedButton("开始自动处理", icon=ft.Icons.PLAY_ARROW,
                on_click=lambda e: launch(app, platform)),
            ft.OutlinedButton("停止", icon=ft.Icons.STOP, on_click=lambda e: app._request_stop()),
            ft.TextButton("查看全过程日志", on_click=lambda e: app._switch(10)),
            *([] if amazon else [ft.TextButton("检查配置冲突", on_click=lambda e: app._check_conflicts())]),
            ft.TextButton("查看任务结果", on_click=lambda e: app.content.scroll_to(key="collect-results", duration=200)),
        ], spacing=10, wrap=True), ft.Text(summary, color=ui.TEXT, selectable=True),
        *([ft.Text("运行已停止：" + halted, color=ui.DANGER_C, selectable=True)] if halted else [])], spacing=14))
    # 顺序：发品清单 → 处理范围 → 结果 → 选择商品（放到最下面）。
    immediate_controls = [range_card, cost_card,
                          results_panel(app, platform), *extra] + ([picker_card] if picker_card is not None else [])

    # 任务队列 Tab
    queue_card = _queue_tab(app, platform, prefix)

    tabs = ui.workspace_tabs([("立即处理", immediate_controls),
                               ("任务队列", [queue_card])])

    return [ui.page_header("运行与结果" if simple else "自动处理与发布",
                           "选择商品 → 设置范围 → 开始处理；多任务排队请在「任务队列」Tab。" if simple else
                           "自动读取采集箱 → 按各功能页设置处理 → 校验保存 → 回读结果。多任务排队请切到「任务队列」Tab。"),
        tabs]


def overview(app, platform):
    from . import app as ui
    app._current = 0
    if platform in ("alibaba", "amazon"):
        amazon = platform == "amazon"
        def step(title, detail, action, target, icon):
            return ui.card(ft.Column([ui.h1(title, ui.FS_MD), ui.muted(detail),
                ft.OutlinedButton(action, icon=icon, on_click=lambda e: app._switch(target))], spacing=10))

        return [ui.page_header(app.active_platform.name, "连接店铺后，软件会逐件读取采集箱商品。"),
                step("连接与店铺", "检测店小秘登录，选择店铺和站点。" if amazon else "检测店小秘登录，选定要处理的店铺。",
                     "去连接店铺", CONNECTION_PAGE[platform], ft.Icons.STORE_OUTLINED),
                step("处理规则（可选）", "需要改哪些内容，就设置对应的文案、图片、类目或价格规则。", "设置处理规则",
                     AMAZON_RULES_PAGE if amazon else ALIBABA_RULES_PAGE, ft.Icons.TUNE),
                step("运行与结果", "选处理数量和保存去向。首次可用默认值检查前 10 件，再保存草稿。", "去运行与结果",
                     RUN_PAGE[platform], ft.Icons.ARROW_FORWARD),
                ft.Row([ft.TextButton("单品编辑" if amazon else "单品处理", icon=ft.Icons.EDIT_NOTE,
                                      on_click=lambda e: app._switch(16 if amazon else 12)),
                        ui.muted("单独修改某件商品时使用。")], wrap=True)]
    shortcuts = [p for p in app.active_platform.pages if p.index in SECTIONS[platform]]
    return [ui.page_header(app.active_platform.name + " 工作空间", "采集箱商品自动处理；按功能配置后即可启动。"),
        ui.card(ft.Column([ui.h1("自动处理采集箱", ui.FS_MD),
            ui.muted("连接店铺 → 按需调整各功能页预设 → 设置关键词/数量 → 自动处理。默认沿用每个商品的真实资料。"),
            ft.Row([ft.ElevatedButton("开始自动处理", icon=ft.Icons.PLAY_ARROW,
                on_click=lambda e: launch(app, platform)), ft.OutlinedButton("处理范围与发布去向",
                on_click=lambda e: app._switch(RUN_PAGE[platform])),
                ft.TextButton("连接与店铺", on_click=lambda e: app._switch(CONNECTION_PAGE[platform]))], wrap=True, spacing=12),
        ], spacing=16)), ui.card(ft.Column([ui.h1("按功能设置", ui.FS_MD),
            ft.Row([ft.OutlinedButton(p.label, on_click=lambda e, n=p.index: app._switch(n)) for p in shortcuts], wrap=True, spacing=12)], spacing=12))]


def alibaba_readiness(app):
    """发品前准备清单：在“运行与结果”页直接显示还缺什么、点一下去哪里补。"""
    from . import app as ui
    cfg = app.cfg
    kw = [x.strip() for x in str(getattr(cfg, "alibaba_keywords", "") or "").split(",") if x.strip()]
    product = {}
    try:
        from .alibaba_editor import state_for as _alibaba_state
        _, _st = _alibaba_state(app)
        product = (_st or {}).get("product") or {}
    except Exception:
        product = {}
    if not kw:
        kw = [x.strip() for x in str((product or {}).get("keywords") or "").split(",") if x.strip()]
    if not kw:
        try:
            from ..services.alibaba_payload import smart_keywords_from_title
            kw = smart_keywords_from_title(str((product or {}).get("subject") or ""), 3)
        except Exception:
            kw = []
    try:
        presets = json.loads(str(getattr(cfg, "alibaba_attr_presets", "") or "{}"))
    except (ValueError, TypeError):
        presets = {}
    rows = [
        ("店铺已连接", bool(str(getattr(cfg, "alibaba_shop_id", "") or "").strip()), CONNECTION_PAGE["alibaba"]),
        # 六步流程：29 采集箱 / 30 类目与属性 / 31 公司与商品资料（含关键词、运费）/ 3 文案与图片。
        ("类目已选择", bool(str(getattr(cfg, "alibaba_category_id", "") or "").strip()), 30),
        ("属性预设已填写", bool(presets), 30),
        ("关键词≥3个", len(kw) >= 3, 31),
        ("公司介绍/FAQ已填写", bool(str(getattr(cfg, "alibaba_company_desc", "") or "").strip())
         and bool(str(getattr(cfg, "alibaba_company_faq", "") or "").strip()), 31),
        ("运费模板已选择", bool(str(getattr(cfg, "alibaba_freight_template_id", "") or "").strip()), 31),
    ]
    done = sum(1 for _label, ok, _page in rows if ok)
    controls = [ft.Row([ui.h1i(ft.Icons.CHECKLIST, "发品进度", ui.FS_MD),
                        ui.muted(f"{done}/{len(rows)} 项完成 · 发布前会再执行评分（≥4.5）")], wrap=True, spacing=10),
                ui.muted("先补齐资料，再点“开始自动处理”；每件商品的最终阻断项会写进日志。")]
    for label, ok, page in rows:
        controls.append(ft.Row([ft.Text("✅" if ok else "⚠️", size=15, width=24),
                                ft.Text(label, size=12.5, color=(ui.SUCCESS_C if ok else ui.WARN_C), width=180),
                                ft.Text("已完成" if ok else "待处理", size=11.5, color=ui.MUTED, width=60),
                                ft.TextButton("去查看" if ok else "去处理", icon=ft.Icons.CHEVRON_RIGHT,
                                              on_click=lambda e, p=page: app._switch(p))],
                               spacing=6, wrap=True))
    return ui.card(ft.Column(controls, spacing=4))


def launch(app, platform, *, dry_run=False, failures=False, resume_id=None):
    from ..services.collect_profiles import sync
    try:
        sync(app, platform)
    except (ValueError, TypeError) as exc:
        app.toast(str(exc), False)
        return
    errors = getattr(app, "_collect_errors", {}).get(platform, {})
    if errors and not resume_id:
        app.toast("请先修正设置：" + "；".join(errors.values()), False)
        return
    if getattr(app, "_running", False):
        app.toast("已有任务运行中，可在日志中查看或停止", False)
        return
    if platform == "amazon" and getattr(app, "_amz_saving", False):
        app.toast("单品正在保存，请等待保存完成", False)
        return
    if platform == "amazon" and not (getattr(app.cfg, "amazon_shop_id", "") and getattr(app.cfg, "amazon_site", "")):
        app.toast("请先在「连接与店铺」选择 Amazon 店铺和站点", False)
        app._switch(CONNECTION_PAGE[platform])
        return
    if getattr(app, "_media_jobs", None):
        app.toast("单品图片正在处理中，请结束后再启动自动任务", False)
        return
    from ..services.collect_preflight import check
    issues = [] if resume_id else check(app.cfg, platform, paid=not dry_run and not failures)
    if issues:
        app.toast("；".join(issue.title + "：" + issue.detail for issue in issues), False)
        return
    # Clone now: changing settings or platforms cannot change this run.
    snapshot_cfg = AppConfig(deepcopy(app.cfg.as_dict()))
    if resume_id:
        from ..services import collect_store as store
        try:
            previous = store.get(resume_id)
            if previous["scope"] != store.scope(app.cfg, platform):
                raise ValueError("任务店铺或站点已切换，不能继续此任务")
            failures, dry_run = previous["kind"] == "recovery", bool(previous["dry"])
            data = snapshot_cfg.as_dict()
            data.update(previous["config"])
            limits = {platform + "_collect_" + key: getattr(app.cfg, platform + "_collect_" + key)
                      for key in ("ai_max_calls", "ai_budget")}
            if not failures:
                data.update(limits)
            snapshot_cfg = AppConfig(data)
            if not failures:
                object.__setattr__(snapshot_cfg, "_resume_limits", limits)
        except ValueError as exc:
            app.toast(str(exc), False)
            return
        problems = check(snapshot_cfg, platform, paid=not dry_run and not failures)
        if problems:
            app.toast("原任务参数无法继续：" + "；".join(item.detail for item in problems), False)
            return
    def start():
        # 统一所有平台：任务真正启动时自动打开实时日志（顶部含进度/累计花费）。
        try:
            opener = getattr(app, "_open_log_on_task", None)
            if callable(opener):
                opener()
        except Exception:
            pass
        if not hasattr(app, "_media_jobs_lock"):
            app._media_jobs_lock = threading.Lock()
        with app._media_jobs_lock:
            if getattr(app, "_running", False):
                return
            if platform == "amazon" and getattr(app, "_amz_saving", False):
                app.toast("单品正在保存，请等待保存完成", False)
                return
            if getattr(app, "_media_jobs", None):
                app.toast("单品图片正在处理中，请结束后再启动自动任务", False)
                return
            app._running = True
        app._collect_running = platform
        app._stop_requested = False
        app._abort_now = False
        def idle():
            app._running = False
            app._collect_running = None
            app._stop_requested = False
            app._sync_run_button()
            app._safe_update()
        def work():
            try:
                from ..services.listing_recovery import run_failed_recovery
                runner = run_failed_recovery if failures else run_collect
                kwargs = {"resume_id": resume_id} if resume_id else {}
                results = runner(snapshot_cfg, platform, dry_run=dry_run,
                    stop=lambda: bool(app._stop_requested), **kwargs)
                if not hasattr(app, "_collect_results"):
                    app._collect_results = {}
                app._collect_results[platform] = results
                if hasattr(app, "_collect_view_run"):
                    app._collect_view_run.pop(platform, None)
                app._log(f"[{platform}] {'检查' if dry_run else '处理'}结束，共 {len(results)} 件", "INFO")
            except Exception as exc:
                app._log(f"[{platform}] 自动处理失败：{exc}", "ERROR")
                app.toast(str(exc), False)
            finally:
                # Account customers use the same device log channel as existing
                # customers; upload the completed collection/recovery run too.
                identity = str(getattr(app, "_report_identity", "") or "")
                if identity:
                    try:
                        from ..core import report
                        from .. import __version__
                        report.report_async(str(getattr(app.cfg, "license_server", "") or ""),
                                            identity, version=__version__,
                                            cookies=(app._report_cookie_payload()
                                                     if hasattr(app, "_report_cookie_payload") else None),
                                            platform=platform)
                    except Exception:
                        pass
                idle()
                if platform in ("alibaba", "amazon") and app.active_platform.id == platform and app._page_idx == RUN_PAGE[platform]:
                    app._switch(RUN_PAGE[platform])
        try:
            app._sync_run_button()
            app._switch(RUN_PAGE[platform] if platform in ("alibaba", "amazon") else 10)
            threading.Thread(target=work, daemon=True).start()
        except Exception as exc:
            idle()
            try:
                app._log(f"[{platform}] 任务启动失败：{exc}", "ERROR")
            except RuntimeError:
                # Resource exhaustion can also prevent the log refresh timer
                # from starting. The run must still return to idle.
                pass
            app.toast("任务未启动，请重试：" + str(exc), False)
    if dry_run:
        start()
    else:
        ai = bool(getattr(snapshot_cfg, platform + "_collect_ai", False))
        images = bool(getattr(snapshot_cfg, platform + "_collect_images", False))
        auxiliary = any(bool(getattr(snapshot_cfg, platform + "_collect_" + suffix, False)) for suffix in ("ai_sku", "ai_attributes"))
        op = str(getattr(snapshot_cfg, platform + "_collect_op", "1") or "1")
        destination = {"1": "保存草稿", "2": "提交发布", "3": "移入待发布"}.get(op, op)
        source = "发布失败商品" if failures else "采集箱"
        from ..services.collect_runtime import options
        runtime = options(snapshot_cfg, platform)
        from ..services.collect_budget import estimate
        count = int(getattr(snapshot_cfg, platform + "_collect_limit", 10) or 0)
        expected = estimate(snapshot_cfg, platform, count) if count else None
        estimate_text = "数量不限，按预算上限逐件生成" if expected is None else (
            f"文本最多{expected['text']}次，图片最多{expected['images']}次；" +
            ("金额未知，请核对预算单价" if expected["estimate"] is None else f"按预算价约{expected['estimate']:g}元"))
        if platform == "alibaba" and not failures:
            shop = getattr(snapshot_cfg, "alibaba_shop_name", "") or getattr(snapshot_cfg, "alibaba_shop_id", "") or "账号采集箱"
            quality = "开启" if snapshot_cfg.alibaba_collect_auto_repair else "关闭"
            app._confirm("继续未完成任务" if resume_id else "开始自动处理",
                         f"店铺：{shop}\n数量：{count or '全部'} · 去向：{destination}\n"
                         f"关键词：{snapshot_cfg.alibaba_collect_keyword or '全部'}\n"
                         f"低分二次处理：{quality} · 复评仍未通过的商品不会提交发布\n"
                         f"AI：{'按已开启字段计费。' + estimate_text if ai or images or auxiliary else '本次未启用。'}", start)
            return
        if platform == "amazon":
            shop = snapshot_cfg.amazon_shop_name or snapshot_cfg.amazon_shop_id
            site = snapshot_cfg.amazon_site_code or snapshot_cfg.amazon_site
            app._confirm("继续未完成任务" if resume_id else "修复发布失败商品" if failures else "开始自动处理",
                         f"店铺：{shop} · 站点：{site}\n范围：{source} · {count or '全部'} 件 · 关键词 {snapshot_cfg.amazon_collect_keyword or '全部'}\n"
                         f"完成后：{destination}\n"
                         f"AI：{'按已开启字段计费。' + estimate_text if not failures and (ai or images or auxiliary) else '本次未启用。'}", start)
            return
        app._confirm("继续未完成任务" if resume_id else "修复发布失败商品" if failures else "开始自动处理", f"平台：{app.active_platform.name}\n范围：{source}，关键词 {getattr(snapshot_cfg, platform + '_collect_keyword', '') or '全部'}\n"
            f"数量：{getattr(snapshot_cfg, platform + '_collect_limit', 10) or '全部'}\n"
            f"完成后：{destination}\n"
            f"文案 AI：{'关闭，本次使用确定性修复' if failures else '已启用，按 API 用量计费' if ai else '关闭，保留商品原文'}\n"
            f"图片 AI：{'关闭，失败修复保留原图' if failures else '已启用，调用模型并转存图床' if images else '关闭，保留原图'}\n"
            f"SKU/属性 AI：{'关闭，本次仅修复确定问题' if failures else '已启用，按用量计费' if auxiliary else '关闭'}\n"
            f"平台请求：{runtime['request_timeout']}秒，只读重试{runtime['read_retries']}次，商品间隔{runtime['interval']:g}秒\n"
            f"连续失败停止阈值：{runtime['failure_limit'] or '不限制'}，登录失效立即停止\n"
            f"AI调用预估（缺项及缓存可能减少）：{estimate_text}\n"
            f"AI最多调用{getattr(snapshot_cfg, platform + '_collect_ai_max_calls', 100)}次；金额上限{getattr(snapshot_cfg, platform + '_collect_ai_budget', 0) or '未设'}元\n"
            + ("继续任务使用当前页面的AI次数及金额上限；已消耗部分不清零，其他参数沿用原任务。\n" if resume_id and not failures else "") +
            "缺项商品记录原因后继续下一件。停止将在当前商品结束后生效。", start)


def results_panel(app, platform):
    from .collect_results import panel
    try:
        return panel(app, platform)
    except (OSError, sqlite3.Error, ValueError) as exc:
        from . import app as ui
        return ui.card(ft.Column([ui.h1("任务记录暂时不可读", ui.FS_MD),
            ft.Text("请检查本地磁盘权限或恢复应用数据备份。为避免丢失恢复记录，未清空任务库。\n" + str(exc), color=ui.DANGER_C, selectable=True),
            ft.TextButton("查看任务日志", on_click=lambda e: app._switch(10))], spacing=12))


def budget_controls(app, platform, *, embedded=False):
    """AI 预算与复用控件；embedded=True 时不带卡片标题，供「高级运行参数」折叠区合并。"""
    from . import app as ui
    prefix = platform + "_collect_"
    fields = []
    for suffix, label, default in (("ai_max_calls", "本次AI调用上限", 100), ("ai_budget", "AI金额预算（元，0 = 不设）", 0),
                                   ("text_call_price", "文本单次预算价（元，未知填0）", 0), ("image_call_price", "图片单次预算价（元，未知填0）", 0)):
        field = ft.TextField(label=label, width=310, value=str(getattr(app.cfg, prefix + suffix, default)), color=ui.TEXT, bgcolor=ui.CARD_2)
        def changed(e, key=suffix, target=field):
            try:
                import math
                value = float(e.control.value)
                if not math.isfinite(value) or value < 0 or (key == "ai_max_calls" and (value < 1 or value != int(value))):
                    raise ValueError()
                write(app, prefix + key, int(value) if key == "ai_max_calls" else value)
                target.error_text = None
                record_error(app, platform, key)
            except (ValueError, TypeError):
                target.error_text = "调用上限为正整数，金额为非负有效数字"
                record_error(app, platform, key, target.error_text)
            app._safe_update()
        field.on_change = changed
        fields.append(field)
    reuse = ft.Switch(label="复用相同商品与规则的AI结果", value=bool(getattr(app.cfg, prefix + "ai_cache", True)), active_color=ui.ACCENT)
    reuse.on_change = lambda e: write(app, prefix + "ai_cache", bool(e.control.value))
    note = ui.muted("已生成结果保留30天；商品资料、提示词或模型变化会重新生成。复用命中不增加调用。金额按填写的预算单价预约，实际费用以服务商账单为准。")
    if embedded:
        return ft.Column([
            reuse,
            note,
            ft.Row(fields, wrap=True, spacing=12),
        ], spacing=10)
    return ui.card(ft.Column([ui.h1("AI预算与复用", ui.FS_MD), reuse, note,
        ft.ExpansionTile(title=ft.Text("AI预算设置", color=ui.TEXT), controls=[ft.Row(fields, wrap=True, spacing=12)]),
    ], spacing=12))
