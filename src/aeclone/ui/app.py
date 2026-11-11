"""AuroraList · 现代化界面（schema 驱动，覆盖老软件全部设置项）。"""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient
from ..config import AppConfig, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService
from ..services.pricing import calc_multiplier, calc_free_shipping
from ..services.variants import auto_map, parse_mapping_json
from ..integrations.cdp import launch_edge, launch_browser, cookies_via_cdp, open_url_via_cdp, BrowserError
from ..integrations.bitbrowser import BitBrowser
from ..integrations.zhanfu import ZhanfuBrowser
from ..services.publisher import Publisher, demo_product
from ..integrations.dianxiaomi import DianxiaomiClient, ENDPOINTS
from ..services.video import build_slideshow, probe as video_probe
from ..core import updater
from ..core.instances import list_instances, create_instance, launch_instance
from ..services import history
from ..services.queue import TaskQueue
from ..services.scheduler import Scheduler
from ..services.text_pipeline import TextPipeline
from ..services.image_pipeline import ImagePipeline

ACCENT, ACCENT_2 = "#7C5CFF", "#00D2A8"
BG, CARD, CARD_2 = "#0B0D12", "#141824", "#1B2030"
TEXT, MUTED = "#E8EAF2", "#8B92A8"

BROWSERS = ["本机Chrome", "本机Edge", "比特浏览器", "站斧浏览器"]
CURRENCIES = ["USD", "EUR", "GBP", "CNY", "RUB", "BRL"]


def card(content, padding=20, expand=False):
    return ft.Container(content=content, bgcolor=CARD, border_radius=16, padding=padding,
                        expand=expand, border=ft.border.all(1, CARD_2))


def h1(t, s=20):
    return ft.Text(t, size=s, weight=ft.FontWeight.BOLD, color=TEXT)


def muted(t, s=12):
    return ft.Text(t, size=s, color=MUTED)


class Field:
    """把配置项渲染成控件。"""

    def __init__(self, key, label, kind="text", options=None, hint=""):
        self.key, self.label, self.kind, self.options, self.hint = key, label, kind, options or [], hint
        self.ctrl = None

    def build(self, cfg):
        val = cfg.get(self.key, "")
        if self.kind == "bool":
            self.ctrl = ft.Switch(label=self.label, value=bool(val), active_color=ACCENT)
        elif self.kind == "select":
            self.ctrl = ft.Dropdown(label=self.label, value=str(val) if val not in ("", None) else None,
                                    options=[ft.dropdown.Option(o) for o in self.options], width=240,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        elif self.kind == "multiline":
            self.ctrl = ft.TextField(label=self.label, value=str(val), multiline=True, min_lines=2, max_lines=6,
                                     bgcolor=CARD_2, border_color=CARD_2, color=TEXT, helper_text=self.hint or None)
        else:
            self.ctrl = ft.TextField(label=self.label, value=str(val), bgcolor=CARD_2, border_color=CARD_2,
                                     color=TEXT, helper_text=self.hint or None)
        return self.ctrl

    def read(self):
        v = self.ctrl.value
        if self.kind == "bool":
            cfg_val = bool(v)
        else:
            cfg_val = v
        return self.key, cfg_val


def section(title, fields, cfg, cols=2):
    rows, controls = [], []
    for f in fields:
        c = f.build(cfg)
        controls.append(f)
        if f.kind in ("multiline",):
            rows.append(c)
        else:
            rows.append(c)
    return card(ft.Column([h1(title, 15), *rows], spacing=12)), controls


# ---------------- 页面 schema ----------------
def schema_store():
    return [
        ("店铺", [Field("browser_type", "浏览器窗口", "select", BROWSERS),
                  Field("store_name", "店铺名称"), Field("store_id", "店铺 ID")]),
        ("模板", [Field("product_template_name", "模板名称"), Field("product_template_id", "模板 ID")]),
    ]


def schema_advanced():
    return [
        ("基础", [Field("edit_sum", "编辑产品数量"), Field("watermark", "水印图片路径")]),
        ("定时发布", [Field("schedule_publish", "启用定时发布", "bool"),
                      Field("schedule_time", "发布时间（如 2026-11-12 09:00）")]),
        ("套装", [Field("combo_enable", "生成套装 SKU", "bool"),
                  Field("combo_prefix", "套装前缀"), Field("combo_multiplier", "套装倍率")]),
        ("水印叠加", [Field("overlay_watermark_enable", "叠加水印到主图", "bool"),
                      Field("overlay_position", "位置（bottom-right/center…）"),
                      Field("overlay_opacity", "透明度 0-1")]),
        ("SKU 图替换", [Field("sku_image_replace", "替换表 JSON（SKU→图片URL）", "multiline")]),
        ("SKU", [Field("max_sku_count", "SKU 数量上限"),
                 Field("custom_sku_prefix", "SKU 前缀"),
                 Field("sku_split_char", "SKU 分隔符"),
                 Field("auto_generate_sku_code", "自动生成 SKU 编码", "bool"),
                 Field("modify_stock_999", "库存改为 999", "bool"),
                 Field("stock_quantity", "库存数量"),
                 Field("only_sku_prefix_checkbox", "只检查前缀不重排", "bool"),
                 Field("copy_sku_to_detail", "SKU 写入详情", "bool"),
                 Field("pack_weight", "包装重量")]),
        ("规格", [Field("spec_weight", "重量"), Field("spec_length", "长"),
                  Field("spec_width", "宽"), Field("spec_height", "高"),
                  Field("product_type", "商品类型"), Field("declared_value", "申报价值")]),
        ("详情文字", [Field("detail_text_info", "详情文字（头部）", "multiline"),
                      Field("detail_text_info_tail", "详情文字（尾部）", "multiline"),
                      Field("detail_text_position", "插入位置（head/tail/both）"),
                      Field("move_detail_text", "详情文字并入头部", "bool")]),
        ("运行", [Field("auto_save_products", "自动保存到店小秘（关闭=只试跑）", "bool")]),
    ]


def schema_ai():
    return [
        ("标题", [Field("enable_ai_title", "启用 AI 标题", "bool"),
                  Field("title_prompt", "标题提示词", "multiline"),
                  Field("title_prefix", "标题前缀"), Field("title_suffix", "标题后缀")]),
        ("详情", [Field("enable_ai_detail", "启用 AI 详情", "bool"),
                  Field("detail_prompt", "详情提示词", "multiline")]),
        ("SKU", [Field("enable_ai_sku", "启用 AI 优化 SKU", "bool"),
                 Field("sku_prompt", "SKU 提示词", "multiline")]),
        ("读图估重", [Field("enable_ai_weight", "启用 AI 估重", "bool"),
                      Field("weight_prompt", "估重提示词", "multiline")]),
    ]


def schema_review():
    return [
        ("文本审查", [Field("review_text_enable", "启用文本审查", "bool"),
                      Field("review_text_prompt", "文本审查提示词", "multiline"),
                      Field("check_banned_word", "违禁词检查", "bool")]),
        ("图片审查", [Field("review_main_img_enable", "主图审查", "bool"),
                      Field("review_main_img_prompt", "主图审查提示词", "multiline"),
                      Field("review_detail_img_enable", "详情图审查", "bool"),
                      Field("review_detail_img_prompt", "详情图审查提示词", "multiline"),
                      Field("detail_review_first_count", "详情图审查前 N 张"),
                      Field("detail_review_last_count", "详情图审查后 N 张"),
                      Field("review_block_on_fail", "不合规时拦截发布", "bool")]),
    ]


def schema_image():
    return [
        ("AI主图生成", [Field("main_image_generate_enable", "启用文生图主图", "bool"),
                        Field("image_gen_model", "生图模型"),
                        Field("main_image_generate_prompt", "生成提示词", "multiline"),
                        Field("main_image_generate_count", "生成数量")]),
        ("指定图片编辑", [Field("designated_edit_enable", "启用指定编辑", "bool"),
                          Field("designated_edit_indices", "编辑序号（如 1,3）"),
                          Field("designated_edit_prompt", "编辑提示词", "multiline")]),
        ("主图", [Field("white_bg_enable", "白底图", "bool"),
                  Field("watermark_enable", "去水印", "bool"),
                  Field("white_background_pic_1", "主图起始序号"),
                  Field("white_background_pic_2", "主图结束序号")]),
        ("描述图", [Field("white_background_detail_pic_indices", "详情图序号（如 1-5,8）")]),
        ("去水印范围", [Field("remove_watermark_pic_1", "去水印起始序号"),
                        Field("remove_watermark_pic_2", "去水印结束序号")]),
        ("精细索引", [Field("single_edit_main_pic_indices", "主图编辑序号（如 1,3）"),
                      Field("single_edit_detail_pic_indices", "详情图编辑序号")]),
        ("主图/SKU图翻译", [Field("translate_main_pic_enable", "主图文字翻译", "bool"),
                            Field("translate_main_pic_indices", "主图索引（如 1-2）"),
                            Field("translate_sku_pic", "SKU 图文字翻译", "bool")]),
        ("素材补齐", [Field("pad_main_images_from_extra", "主图不足时用详情图补", "bool"),
                      Field("main_pic_count", "主图目标数量")]),
        ("详情图翻译", [Field("translate_detail_pic_enable", "启用详情图文字翻译", "bool"),
                        Field("translate_detail_pic_indices", "翻译图序号（如 1-3）"),
                        Field("translate_prompt", "翻译提示词", "multiline")]),
        ("背景生成", [Field("background_gen_rules", "规则 JSON（position/prompt）", "multiline"),
                      Field("background_gen_sku_prompt", "SKU 背景提示词", "multiline")]),
        ("SKU 图", [Field("white_background_sku_pic", "处理 SKU 图", "bool")]),
        ("精细处理", [Field("edit_enable", "启用通用编辑", "bool"),
                      Field("edit_prompt", "编辑提示词", "multiline"),
                      Field("four_in_one", "四合一拼图", "bool")]),
    ]


def schema_price():
    return [
        ("模式", [Field("price_mode", "价格模式（multiplier/free_shipping/commission）"),
                  Field("price_multiplier", "价格倍率"),
                  Field("price_fixed_add", "固定加价"),
                  Field("regional_pricing", "区域调价", "bool")]),
        ("成本", [Field("goods_cost", "商品成本"),
                  Field("platform_commission", "平台佣金%"),
                  Field("sales_profit", "期望利润%"),
                  Field("default_express_price", "默认运费")]),
        ("区域模板", [Field("adjust_price_template_detail", "区域倍率 JSON（如 {\"USD\":1.0,\"EUR\":0.92}）", "multiline")]),
        ("阶梯", [Field("tuoguan_price_tiered_enabled", "启用阶梯倍率", "bool"),
                  Field("tuoguan_price_tiers", "阶梯规则 JSON", "multiline")]),
    ]


def schema_variant():
    return [("映射", [Field("variant_mappings", "属性映射 JSON", "multiline")])]


def section(title, fields, cfg, cols=2):
    rows, controls = [], []
    for f in fields:
        c = f.build(cfg)
        controls.append(f)
        if f.kind in ("multiline",):
            rows.append(c)
        else:
            rows.append(c)
    return card(ft.Column([h1(title, 15), *rows], spacing=12)), controls


# ---------------- 页面 schema ----------------
def schema_settings(cfg):
    return [
        ("上传", [Field("upload_full_cid", "相册分类 ID"), Field("upload_category_name", "相册分类名")]),
        ("视频", [Field("generate_main_video", "生成主图视频", "bool"),
                  Field("use_template_video", "使用模板视频", "bool"),
                  Field("overwrite_existing_video", "覆盖已有视频", "bool")]),
        ("API 配置", [Field("api_key", "API Key"),
                      Field("concurrency", "并发数"), Field("timeout", "超时(秒)")]),
        ("通用", [Field("image_upload_category", "图片上传分类"),
                  Field("market_white_bg_pic_no", "营销白底图取第几张"),
                  Field("publish_destination", "发布目标"),
                  Field("folder_path", "本地素材目录")]),
        ("视频与发布", [Field("generate_main_video", "生成主图视频", "bool"),
                        Field("use_template_video", "使用模板视频", "bool"),
                        Field("overwrite_existing_video", "覆盖已有视频", "bool")]),
    ]


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg: AppConfig = get_config()
        self.picked: list[Path] = []
        self.logs: list[str] = []
        self.field_refs: list[Field] = []
        self.file_picker = ft.FilePicker(on_result=self._on_files)

        page.title = "AuroraList · 极光上架"
        page.bgcolor = BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        page.overlay.append(self.file_picker)
        page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)
        self._build_shell()
        import os
        try:
            start = int(os.environ.get("AURORA_PAGE", "0"))
        except ValueError:
            start = 0
        self._switch([0,1,2,3,4,5,6,7,8,9,10,11][start % 12])
        page.update()

    # ---------- 壳 ----------
    def _build_shell(self):
        self.nav_buttons = []
        groups = [
            ("", [("概览", ft.Icons.DASHBOARD_OUTLINED, 0)]),
            ("店铺与上架", [("店铺设置", ft.Icons.STORE_OUTLINED, 1), ("高级设置", ft.Icons.TUNE, 2)]),
            ("AI 设置", [("AI 智能填写", ft.Icons.AUTO_FIX_HIGH, 3), ("内容审查", ft.Icons.FACT_CHECK_OUTLINED, 4)]),
            ("AI图片处理", [("AI图片处理", ft.Icons.IMAGE_OUTLINED, 5), ("图片工作台", ft.Icons.GRID_VIEW, 9)]),
            ("价格与变种", [("价格设置", ft.Icons.SELL_OUTLINED, 6), ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED, 7)]),
            ("系统", [("设置", ft.Icons.SETTINGS_OUTLINED, 8), ("任务与日志", ft.Icons.RECEIPT_LONG_OUTLINED, 10)]),
        ]
        col = ft.Column(spacing=3)
        for group, items in groups:
            if group:
                col.controls.append(ft.Container(
                    content=ft.Text(group, size=10, color="#5D6478", weight=ft.FontWeight.BOLD),
                    padding=ft.padding.only(left=12, top=10, bottom=2)))
            for label, icon, page_idx in items:
                b = ft.Container(
                    content=ft.Row([ft.Icon(icon, size=16, color=MUTED), ft.Text(label, color=MUTED, size=13)], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=8), border_radius=10,
                    on_click=lambda e, idx=page_idx: self._switch(idx), ink=True)
                self.nav_buttons.append((page_idx, b))
                col.controls.append(b)
        self.license_text = ft.Text("检测中…", color=MUTED, size=12, weight=ft.FontWeight.BOLD)
        sidebar = ft.Container(width=216, bgcolor=CARD, padding=16, content=ft.Column([
            ft.Row([ft.Container(width=32, height=32, border_radius=10, bgcolor=ACCENT,
                                 content=ft.Text("A", size=15, weight=ft.FontWeight.BOLD, color="#0B0D12")),
                    ft.Column([ft.Text("AuroraList", weight=ft.FontWeight.BOLD, color=TEXT, size=14),
                               muted("极光上架 v0.2")], spacing=0)], spacing=10),
            ft.Container(height=14), col, ft.Container(expand=True),
            card(ft.Column([muted("授权状态"), self.license_text], spacing=4), padding=10),
        ]))
        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=14)
        self.page.add(ft.Row([sidebar, ft.Container(expand=True, content=self.content,
                                                    padding=ft.padding.only(left=24, right=24, top=18, bottom=18))],
                             expand=True, spacing=0))
        threading.Thread(target=self._check_license, daemon=True).start()
        try:
            self.scheduler = Scheduler()
            self.scheduler.start(self._run_scheduled)
        except Exception as ex:
            self._log(f"调度器启动失败：{ex}", "WARN")
        try:
            from ..integrations.browser_cookies import read_best
            src = read_best("dianxiaomi.com")
            self._log(f"Cookie 源：{src.name} · {len(src.cookies)} 个" + ("" if src.ok else f"（{src.error[:60]}）"),
                      "INFO" if src.ok else "WARN")
        except Exception as ex:
            self._log(f"Cookie 检测异常：{ex}", "WARN")

        try:
            self._maybe_wizard()
        except Exception as ex:
            self._log(f"引导弹窗异常：{ex}")

    def _switch(self, idx):
        for page_idx, b in self.nav_buttons:
            on = page_idx == idx
            b.bgcolor = CARD_2 if on else None
            b.content.controls[0].color = ACCENT if on else MUTED
            b.content.controls[1].color = TEXT if on else MUTED
        self.field_refs = []
        pages = [self._p_overview, self._p_store, self._p_advanced, self._p_ai, self._p_review,
                 self._p_image, self._p_price, self._p_variant, self._p_settings, self._p_workbench, self._p_logs]
        self.content.controls = pages[idx]()
        self.page.update()

    def _implemented_keys(self):
        if getattr(self, "_impl_keys", None):
            return self._impl_keys
        import re as _re, pathlib as _pl
        root = _pl.Path(__file__).resolve().parents[1]
        code = ""
        for py in root.rglob("*.py"):
            try:
                code += py.read_text()
            except Exception:
                continue
        from ..config import DEFAULTS
        keys = set()
        for k in DEFAULTS:
            if _re.search(rf"\.{_re.escape(k)}\b", code):
                keys.add(k)
        self._impl_keys = keys
        return keys

    def _render(self, title, schemas, extra=None):
        out = [h1(title), muted("所有配置自动保存到本地 config.json")]
        for name, fields in schemas:
            c, refs = section(name, fields, self.cfg)
            self.field_refs.extend(refs)
            out.append(c)
        if extra:
            out.append(extra)
        out.append(self._save_row())
        return out

    def _save_row(self):
        def save(e):
            for f in self.field_refs:
                k, v = f.read()
                self.cfg.set(k, v)
            save_config(self.cfg)
            self.toast("配置已保存")

        return ft.Row([ft.ElevatedButton("保存配置", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white", on_click=save),
                       ft.OutlinedButton("恢复默认", icon=ft.Icons.RESTART_ALT,
                                         on_click=lambda e: self._reset())], spacing=12)

    def _reset(self):
        self.cfg = AppConfig()
        save_config(self.cfg)
        self._switch(self._current)
        self.toast("已恢复默认")

    # ---------- 页面 ----------
    def _maybe_wizard(self):
        if getattr(self.cfg, "onboarded", False):
            return
        self._wizard_step = 1
        self.wz_browser = ft.Dropdown(label="浏览器", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                      value=self.cfg.browser_type or "本机Edge",
                                      options=[ft.dropdown.Option(x) for x in BROWSERS])
        self.wz_store = ft.Dropdown(label="店铺", width=320, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_source = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     value=self.cfg.template_source or "draft",
                                     options=[ft.dropdown.Option("draft", "草稿"), ft.dropdown.Option("online", "在线")],
                                     on_change=lambda e: self._wz_source_changed())
        self.wz_tpl = ft.Dropdown(label="模板产品", width=380, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_status = ft.Text("① 选择浏览器后点击「打开浏览器」", size=12, color=MUTED)
        self.wz_btn = ft.ElevatedButton("① 读取已登录 Chrome（推荐）", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=self._wz_sync)
        self.wz_btn2 = ft.ElevatedButton("或打开浏览器重新登录", icon=ft.Icons.OPEN_IN_BROWSER, bgcolor=CARD_2, color=TEXT, on_click=self._wz_open)

        self.wz_dlg = ft.AlertDialog(
            title=ft.Text("欢迎使用 AuroraList · 首次设置"),
            content=ft.Column([
                ft.Text("第 1 步：直接读取你 Chrome 里已登录的店小秘（推荐），或打开浏览器重新登录", size=12, color=TEXT),
                ft.Row([self.wz_browser, self.wz_btn, self.wz_btn2], spacing=10),
                ft.ElevatedButton("读取店铺信息（我已登录）", icon=ft.Icons.SYNC, on_click=self._wz_sync),
                ft.Divider(color=CARD_2),
                ft.Row([self.wz_store, self.wz_source], spacing=10),
                ft.Row([ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT,
                                          on_click=lambda e: self._open_template_picker(self.wz_source.value,
                                                                                        on_pick=lambda pid, t, imgs: self._set_tpl_preview(imgs))),
                        self.tpl_preview_box], spacing=10),
                self.wz_status,
            ], spacing=12, width=520),
            actions=[ft.TextButton("稍后设置", on_click=lambda e: self._wz_close(False)),
                     ft.ElevatedButton("完成", bgcolor=ACCENT_2, color="#04231C", on_click=self._wz_finish)],
        )
        self.page.dialog = self.wz_dlg
        self.wz_dlg.open = True
        self.page.update()

    def _wz_open(self, e):
        self.wz_status.value = "正在启动 Chrome…"
        self.cfg.browser_type = self.wz_browser.value
        save_config(self.cfg)
        self.page.update()

        def go():
            try:
                self._connect_browser()
                if not self.ws_endpoint:
                    self.wz_status.value = "浏览器已启动，但未拿到调试端点"
                    self.page.update(); return
                try:
                    open_url_via_cdp(self.ws_endpoint, "https://www.dianxiaomi.com/web/smt/smtProductList/offline")
                except Exception:
                    pass
                self.wz_status.value = "请在弹出的 Chrome 窗口登录店小秘（一次即可，之后记住登录），登录后自动同步…"
                self.page.update()
                # 自动等待登录（最多 5 分钟）
                for _ in range(100):
                    time.sleep(3)
                    try:
                        ck = self._cookies()
                    except Exception:
                        continue
                    if not ck:
                        continue
                    try:
                        c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                        shops = c.shops()
                        if not shops:
                            continue
                        prods = c.drafts(limit=50) if self.wz_source.value == "draft" else c.online(limit=50)
                        self.wz_store.options = [ft.dropdown.Option(str(x["id"]), x["name"]) for x in shops]
                        self.wz_store.value = str(shops[0]["id"])
                        self.cfg.store_id, self.cfg.store_name = str(shops[0]["id"]), shops[0]["name"]
                        self.wz_tpl.options = [ft.dropdown.Option(str(x["id"]), (x.get("title") or x["id"])[:44]) for x in prods]
                        if prods:
                            self.wz_tpl.value = str(prods[0]["id"])
                        self.wz_status.value = f"已检测到登录：{len(shops)} 个店铺 / {len(prods)} 个模板候选，点「完成」即可"
                        self.page.update()
                        return
                    except Exception:
                        continue
                self.wz_status.value = "等待登录超时，请点「读取店铺信息」重试"
            except Exception as ex:
                self.wz_status.value = f"启动失败：{ex}"
            self.page.update()
        threading.Thread(target=go, daemon=True).start()

    def _wz_source_changed(self):
        self.cfg.template_source = self.wz_source.value
        save_config(self.cfg)
        self.toast(f"模板来源已切换为{'草稿' if self.wz_source.value == 'draft' else '在线'}")

    def _wz_sync(self, e):
        self.wz_status.value = "正在读取 Cookie 与店铺…"
        self.page.update()

        def go():
            try:
                ck = self._cookies()
                if not ck:
                    self.wz_status.value = "未读取到 Cookie，请确认已在浏览器登录店小秘"
                    self.page.update(); return
                c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                shops = c.shops()
                self.wz_store.options = [ft.dropdown.Option(str(s["id"]), s["name"]) for s in shops]
                if shops:
                    self.wz_store.value = str(shops[0]["id"])
                    self.cfg.store_id, self.cfg.store_name = str(shops[0]["id"]), shops[0]["name"]
                prods = c.drafts(limit=50) if self.wz_source.value == "draft" else c.online(limit=50)
                self.wz_tpl.options = [ft.dropdown.Option(str(p["id"]), (p.get("title") or p["id"])[:44]) for p in prods]
                if prods:
                    self.wz_tpl.value = str(prods[0]["id"])
                self.cfg.template_source = self.wz_source.value
                save_config(self.cfg)
                self.wz_status.value = f"已同步：{len(shops)} 个店铺 / {len(prods)} 个模板候选"
            except Exception as ex:
                self.wz_status.value = f"读取失败：{ex}"
            self.page.update()
        threading.Thread(target=go, daemon=True).start()

    def _wz_finish(self, e):
        if self.wz_store.value:
            self.cfg.store_id = str(self.wz_store.value)
            name = next((o.text for o in self.wz_store.options if o.key == self.wz_store.value), "")
            self.cfg.store_name = name or self.cfg.store_name
        if self.wz_tpl.value:
            self.cfg.template_product_id = str(self.wz_tpl.value)
            title = next((o.text for o in self.wz_tpl.options if o.key == self.wz_tpl.value), "")
            self.cfg.template_product_title = title or ""
        self.cfg.template_source = self.wz_source.value
        self.cfg.browser_type = self.wz_browser.value
        self.cfg.onboarded = True
        save_config(self.cfg)
        self.wz_dlg.open = False
        self._log(f"首次设置完成：店铺 {self.cfg.store_name} / 模板 {self.cfg.template_product_title[:30]}")
        self.page.update()
        self._switch(0)

    def _wz_close(self, mark):
        self.wz_dlg.open = False
        self.page.update()

    def _p_overview(self):
        self.stat_api = ft.Text("未检测", color=MUTED, size=12)
        self._current = 0

        def stat(label, ctrl, icon, color):
            return card(ft.Column([ft.Row([ft.Icon(icon, size=17, color=color), muted(label)], spacing=8), ctrl],
                                  spacing=8), padding=16, expand=True)

        st = {"runs": 0, "ok": 0, "failed": 0}
        try:
            st = history.stats(days=1)
        except Exception:
            pass
        return [h1("概览"), muted("AI 能力接入自建站点，全部调用按站点价格计费"),
                card(ft.Row([ft.Text(f"今日任务 {st['runs']}", color=TEXT, size=13),
                             ft.Text(f"成功 {st['ok']}", color=ACCENT_2, size=13),
                             ft.Text(f"失败 {st['failed']}", color="#FF8A8A", size=13)], spacing=24)),
                ft.Row([stat("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT),
                        stat("授权状态", ft.Text("见左下角", color=MUTED, size=12), ft.Icons.VERIFIED_OUTLINED, ACCENT_2),
                        stat("图片服务", ft.Text("~15 秒/张", color=ACCENT_2, size=12), ft.Icons.BOLT_OUTLINED, "#FFB020")],
                       spacing=12),
                card(ft.Column([h1("配置概览", 15),
                                muted(f"共 {len(self.cfg.as_dict())} 项配置 · 浏览器：{self.cfg.browser_type} · 店铺：{self.cfg.store_name or '未绑定'}"),
                                ft.Row([ft.ElevatedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                                          on_click=lambda e: threading.Thread(target=self._test, daemon=True).start()),
                                        ft.ElevatedButton("启动", icon=ft.Icons.ROCKET_LAUNCH, bgcolor=ACCENT, color="white",
                                                          on_click=lambda e: self._start()),
                                        ft.ElevatedButton("一键自检", icon=ft.Icons.PLAY_CIRCLE_FILL,
                                                          bgcolor=ACCENT_2, color="#04231C",
                                                          on_click=lambda e: threading.Thread(target=self._self_test, daemon=True).start()),
                                        ft.OutlinedButton("去设置", icon=ft.Icons.SETTINGS,
                                                          on_click=lambda e: self._switch(8))], spacing=10)], spacing=10))]

    def _validate(self) -> list[str]:
        cfg = self.cfg
        errs = []
        if not (cfg.api_key or "").strip():
            errs.append("未填写 API Key")
        if not (cfg.store_id or cfg.store_name):
            errs.append("未选择店铺（点「同步店铺/模板」）")
        if not (cfg.product_template_id or cfg.product_template_name):
            errs.append("未选择产品模板")
        try:
            if int(cfg.edit_sum) <= 0:
                errs.append("编辑产品数量必须大于 0")
        except Exception:
            errs.append("编辑产品数量不是有效数字")
        try:
            if int(cfg.max_sku_count) <= 0:
                errs.append("SKU 上限必须大于 0")
        except Exception:
            errs.append("SKU 上限不是有效数字")
        try:
            if not (1 <= int(cfg.concurrency) <= 10):
                errs.append("并发数应在 1~10")
        except Exception:
            errs.append("并发数不是有效数字")
        try:
            if float(cfg.price_multiplier) <= 0:
                errs.append("价格倍率必须大于 0")
        except Exception:
            errs.append("价格倍率不是有效数字")
        if cfg.watermark:
            from pathlib import Path as _P
            if not _P(str(cfg.watermark)).exists():
                errs.append(f"水印文件不存在：{cfg.watermark}")
        return errs

    def _start(self):
        errs = self._validate()
        if errs:
            dlg = ft.AlertDialog(title=ft.Text("启动前检查未通过"),
                                 content=ft.Column([ft.Text("• " + e, color="#FF8A8A", size=12) for e in errs],
                                                   spacing=6, height=200, width=460),
                                 actions=[ft.TextButton("去修改", on_click=lambda e: (setattr(dlg, "open", False), self._switch(1), self.page.update())),
                                          ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
            self.page.dialog = dlg; dlg.open = True; self.page.update()
            return
        self._log("启动：配置检查通过")
        self._switch(10)
        self._log("开始执行…")
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    def _run_scheduled(self, job):
        self._log(f"定时任务触发：{job.product_id}", "INFO")
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            prods = dxm.drafts(limit=200, shop_id=self.cfg.store_id or None)
            prod = next((p for p in prods if p["id"] == job.product_id), None)
            if not prod:
                raise RuntimeError("产品不在待发布列表")
            res = Publisher(self.cfg, dxm=dxm, ws_endpoint=self.ws_endpoint).run(
                prod, dry_run=not self.cfg.auto_save_products)
            for line in res.log().splitlines():
                self._log("   " + line)
            self._log(f"定时任务完成：{job.product_id}", "INFO")
        except Exception as ex:
            self._log(f"定时任务失败：{ex}", "ERROR")
            raise

    def _run_pipeline(self):
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            all_prods, _meta = dxm.product_page(1, 100, self.cfg.store_id or None, None)
            blocked = [p for p in all_prods if (p.get("status_type") or "") in ("auditing", "onSelling")]
            self._log(f"共 {len(all_prods)} 个产品，跳过审核中/已发布 {len(blocked)} 个", "INFO")
            limit = int(self.cfg.edit_sum or 0) or 0
            prods = dxm.pending_products(limit=limit, shop_id=self.cfg.store_id or None)
            self.products = prods
            if not prods:
                self._log("没有可编辑（待发布）的产品", "WARN"); return
            self._log(f"本次待处理 {len(prods)} 个产品", "INFO")
            pub = Publisher(self.cfg, dxm=dxm, ws_endpoint=self.ws_endpoint)
            ok_n = fail_n = 0
            for i, prod in enumerate(prods, 1):
                self._log(f"[{i}/{len(prods)}] {prod['id']} {(prod.get('title') or '')[:40]}", "INFO")
                try:
                    res = pub.run(prod, dry_run=not self.cfg.auto_save_products)
                    for line in res.log().splitlines():
                        self._log("   " + line, "INFO" if "✓" in line else "WARN")
                    ok_n += 1 if res.ok else 0
                    fail_n += 0 if res.ok else 1
                    if self.cfg.auto_save_products and res.ok:
                        history.record("publish", 1, 0, prod["id"])
                except Exception as ex:  # noqa: BLE001
                    fail_n += 1
                    self._log(f"   失败：{ex}", "ERROR")
            self._log(f"完成：成功 {ok_n} / 失败 {fail_n}", "INFO")
        except Exception as ex:
            self._log(f"运行异常：{ex}", "ERROR")

    def _self_test(self):
        from ..services.text_pipeline import TextPipeline
        from ..services.image_pipeline import ImagePipeline
        from ..services import ImageService
        import requests as _rq, tempfile as _tf
        from pathlib import Path as _P

        rows = []
        def step(name, fn):
            t = time.time()
            try:
                v = fn(); ok = True; detail = str(v)[:60]
            except Exception as ex:
                ok = False; detail = str(ex)[:80]
            rows.append((name, ok, round(time.time() - t, 1), detail))
            self._log(f"自检 {name}: {'OK' if ok else 'FAIL'} ({detail})")
            self._refresh_self_test(rows)

        cfg = self.cfg
        c = ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
        tp = TextPipeline(cfg, c)
        step("模型列表", lambda: f"{len(c.models())} models")
        step("标题生成", lambda: tp.make_title("3 吨迷你蜘蛛吊车 20 米臂长").value[:50])
        step("详情生成", lambda: tp.make_detail("3-Ton Mini Spider Crane").value[:40])
        step("SKU 优化", lambda: tp.make_skus(["手机壳适用于iPhone 15 Pro Max"]).value)
        step("文本审查", lambda: tp.review_text("厂家直销 电话13800000000 限时特惠").value[:30])
        img = "https://sc04.alicdn.com/kf/H36d2c1e2529d4ab18cab919a38f33e25D.jpg"
        step("读图估重", lambda: tp.estimate_weight(img, "吊车").value[:40])
        step("图片审查", lambda: ImagePipeline(cfg, c).review(img, "main")[:40])
        d = _P(_tf.mkdtemp()) / "a.jpg"
        d.write_bytes(_rq.get(img, timeout=60).content)
        svc = ImageService(c)
        step("白底图", lambda: svc.white_background(d))
        step("去水印", lambda: svc.remove_watermark(d))
        ok_n = sum(1 for r in rows if r[1])
        self._show_test_dialog(rows, ok_n)

    def _refresh_self_test(self, rows):
        try:
            self.page.update()
        except Exception:
            pass

    def _show_test_dialog(self, rows, ok_n):
        items = [ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                                 size=16, color=ACCENT_2 if ok else "#FF6B6B"),
                         ft.Text(f"{name}", size=12, color=TEXT, width=90),
                         ft.Text(f"{sec}s", size=11, color=MUTED, width=50),
                         ft.Text(detail, size=11, color=MUTED, expand=True)], spacing=8)
                 for name, ok, sec, detail in rows]
        dlg = ft.AlertDialog(title=ft.Text(f"自检结果 {ok_n}/{len(rows)} 通过"),
                             content=ft.Column(items, scroll=ft.ScrollMode.AUTO, height=380, width=620),
                             actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
        self.page.dialog = dlg; dlg.open = True; self.page.update()

    def _p_store(self):
        self._current = 1
        from ..integrations.browser_cookies import read_best
        try:
            src = read_best("dianxiaomi.com")
            ck_ok, ck_text = src.ok, f"{src.name} · {len(src.cookies)} 个"
        except Exception:
            ck_ok, ck_text = False, "读取异常"

        cnt = getattr(self, "store_counts", {}) or {}
        status_chip = lambda label, value, color=MUTED: ft.Column([
            ft.Text(label, size=10, color=MUTED), ft.Text(str(value), size=16, color=color, weight=ft.FontWeight.BOLD)
        ], spacing=0)

        self.shop_dd = ft.Dropdown(label="当前店铺", width=340, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                   options=[ft.dropdown.Option(key=self.cfg.store_id, text=self.cfg.store_name)] if self.cfg.store_id else [],
                                   value=self.cfg.store_id or None,
                                   on_change=lambda e: self._on_shop_change())
        self.tpl_preview_box = ft.Container(content=self._template_preview_row())
        self.tpl_snapshot_text = ft.Text("模板参数：未读取", size=12, color=MUTED)
        self.tpl_source_dd = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                         value=self.cfg.template_source or "draft",
                                         options=[ft.dropdown.Option("draft", "待发布"), ft.dropdown.Option("online", "在线")],
                                         on_change=lambda e: self._on_tpl_source())
        self.cookie_status = ft.Text(f"Cookie：{ck_text}" if ck_ok else f"Cookie：{ck_text}（读取失败，可粘贴）",
                                     size=11, color=ACCENT_2 if ck_ok else "#FFB020")
        self.store_status = ft.Text("点击「读取并同步」获取店铺与模板", size=12, color=MUTED)

        def sync(e):
            self.store_status.value = "正在读取…"; self.page.update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        connect_card = card(ft.Column([
            h1("1 · 连接店小秘", 15),
            muted("直接读取本机 Chrome/Edge 里已登录的 Cookie，无需启动浏览器"),
            self.cookie_status,
            ft.Row([ft.ElevatedButton("读取并同步", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=sync),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
                    ft.OutlinedButton("启动浏览器（兜底）", icon=ft.Icons.OPEN_IN_BROWSER,
                                      on_click=lambda e: threading.Thread(target=self._connect_browser, daemon=True).start()),
                    ft.OutlinedButton("从链接采集", icon=ft.Icons.LINK, on_click=lambda e: self._collect_dialog())],
                   spacing=10),
            self.store_status,
        ], spacing=10))

        shop_card = card(ft.Column([
            h1("2 · 选择店铺", 15),
            self.shop_dd,
            ft.Row([status_chip("在线", cnt.get("onSelling", "—"), ACCENT_2),
                    status_chip("待发布", cnt.get("editingRequired", "—"), "#6EC6FF"),
                    status_chip("审核中", cnt.get("auditing", "—"), "#FFB020")], spacing=28),
        ], spacing=12))

        def pick(e):
            self._open_template_picker(self.tpl_source_dd.value,
                                       on_pick=lambda pid, t, imgs: self._set_tpl_preview(imgs))

        tpl_card = card(ft.Column([
            h1("3 · 选择模板", 15),
            muted("后续产品沿用模板的类目、运费模板、属性、重量尺寸；标题/SKU/图片仍按新生成"),
            ft.Row([self.tpl_source_dd,
                    ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT, on_click=pick)], spacing=10),
            self.tpl_preview_box,
            ft.Row([ft.ElevatedButton("读取模板参数", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=CARD_2, color=TEXT,
                                      on_click=lambda e: threading.Thread(target=self._read_template, daemon=True).start()),
                    self.tpl_snapshot_text], spacing=10),
        ], spacing=10))

        if not getattr(self, "_store_autosynced", False):
            self._store_autosynced = True
            threading.Thread(target=self._sync_shops, daemon=True).start()
        return [h1("店铺设置"), muted("三步：读 Cookie → 选店铺 → 选模板"), connect_card, shop_card, tpl_card]

    def _on_shop_change(self):
        self.cfg.template_product_id = ""
        self.cfg.template_product_title = ""
        self.cfg.template_snapshot = ""
        save_config(self.cfg)
        self.tpl_preview_box.content = self._template_preview_row()
        self.tpl_snapshot_text.value = "模板参数：未读取（已切换店铺，请重新选择模板）"
        self.toast("已切换店铺，请重新选择模板")
        sid = self.shop_dd.value
        name = next((o.text for o in self.shop_dd.options if o.key == sid), "")
        self.cfg.store_id = str(sid or "")
        self.cfg.store_name = name or self.cfg.store_name
        save_config(self.cfg)
        threading.Thread(target=self._sync_shops, daemon=True).start()

    def _on_tpl_source(self):
        self.cfg.template_source = self.tpl_source_dd.value
        save_config(self.cfg)
        self._open_template_picker(self.tpl_source_dd.value,
                                   on_pick=lambda pid, t, imgs: self._set_tpl_preview(imgs))

    # ---------- 对接动作 ----------
    def _collect_dialog(self):
        url_field = ft.TextField(label="商品链接（1688 / 阿里国际 / 其它）", bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        out = ft.TextField(label="采集结果", multiline=True, min_lines=6, read_only=True,
                           bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        state = {"collected": None}

        def to_queue(e):
            c = state.get("collected")
            if not c or not c.ok:
                self.toast("请先采集成功", False); return
            try:
                from ..services.collector import to_product
                prod = to_product(c)
                self.products = [prod] + [x for x in getattr(self, "products", []) if x.get("id") != ""]
                self.toast("已加入产品列表（未入库，可先试跑）")
                self._log(f"采集并入列：{prod['title'][:30]}")
                dlg.open = False; self.page.update()
            except Exception as ex:
                self.toast(f"失败：{ex}", False)

        def run(e):
            out.value = "采集中…"; self.page.update()
            def go():
                try:
                    from ..services.collector import collect
                    r = collect((url_field.value or "").strip())
                    if r.ok:
                        state["collected"] = r
                        out.value = f"标题：{r.title}\n图片：{len(r.images)} 张\n价格：{r.price or '-'}\n\n" + "\n".join(r.images[:8])
                        self._log(f"采集成功：{r.title[:30]} / {len(r.images)} 张图")
                    else:
                        out.value = f"采集失败（可能需要登录或页面需 JS）：{r.error}"
                except Exception as ex:
                    out.value = f"异常：{ex}"
                self.page.update()
            threading.Thread(target=go, daemon=True).start()

        dlg = ft.AlertDialog(title=ft.Text("从链接采集商品"),
                             content=ft.Column([url_field, out], spacing=10, width=560),
                             actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                                      ft.TextButton("加入队列", on_click=to_queue),
                                      ft.ElevatedButton("开始采集", bgcolor=ACCENT, color="white", on_click=run)])
        self.page.dialog = dlg; dlg.open = True; self.page.update()

    def _paste_cookie(self):
        field = ft.TextField(label="Cookie", multiline=True, min_lines=4, bgcolor=CARD_2,
                             border_color=CARD_2, color=TEXT,
                             hint_text="从 Chrome 开发者工具 Network → 任意请求 → Request Headers → Cookie 复制整行")

        def save(e):
            raw = (field.value or "").strip()
            ck = {}
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    ck[k.strip()] = v.strip()
            if not ck:
                self.toast("Cookie 解析为空", False); return
            self.cfg.manual_cookies = ck
            save_config(self.cfg)
            self.toast(f"已保存 {len(ck)} 个 Cookie")
            dlg.open = False
            self.page.update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        dlg = ft.AlertDialog(title=ft.Text("手动粘贴 Cookie"),
                             content=ft.Column([field], width=560, height=200),
                             actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                                      ft.ElevatedButton("保存并同步", bgcolor=ACCENT, color="white", on_click=save)])
        self.page.dialog = dlg; dlg.open = True; self.page.update()

    def _set_tpl_preview(self, images=None):
        self.tpl_preview_box.content = self._template_preview_row(images)
        try:
            self.page.update()
        except Exception:
            pass

    def _read_template(self):
        pid = self.tpl_dd.value or self.cfg.template_product_id
        if not pid:
            self.toast("请先选择模板产品", False); return
        self.tpl_snapshot_text.value = "正在读取模板参数…"
        self.page.update()
        try:
            from ..integrations.template_reader import read_snapshot
            snap = read_snapshot(self.ws_endpoint, str(pid))
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = snap.title
            self.cfg.template_snapshot = snap.to_json()
            save_config(self.cfg)
            f = snap.fields
            self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                            f" · 属性 {len(snap.attributes)} 项 · 重量 {f.get('weight','-')}")
            self._log(f"模板参数已读取：{pid}")
        except Exception as ex:
            self.tpl_snapshot_text.value = f"读取失败：{ex}"
        self.page.update()

    def _sync_shops(self):
        try:
            ck = self._cookies()
            c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            shops = c.shops()
            self.shop_dd.options = [ft.dropdown.Option(key=str(s["id"]), text=s["name"]) for s in shops]
            if shops and not self.cfg.store_id:
                self.cfg.store_id = shops[0]["id"]; self.cfg.store_name = shops[0]["name"]
                save_config(self.cfg)
            if shops:
                self.shop_dd.value = str(self.cfg.store_id or shops[0]["id"])
            sid = self.cfg.store_id or (shops[0]["id"] if shops else None)
            cnt = c.counts(sid) if sid else {}
            self.store_counts = cnt
            self.store_status.value = (f"店铺 {self.cfg.store_name}：在线 {cnt.get('onSelling',0)} · "
                                       f"待发布 {cnt.get('editingRequired',0)} · 审核中 {cnt.get('auditing',0)}")
            self.store_status.color = ACCENT_2
        except Exception as ex:
            self.store_status.value = f"同步失败：{ex}"
            self.store_status.color = "#FF6B6B"
        self.page.update()

    def _connect_browser(self):
        try:
            kind = self.cfg.browser_type
            if kind == "比特浏览器":
                bb = BitBrowser()
                if not bb.health():
                    raise BrowserError("比特浏览器本地服务未启动（127.0.0.1:54345）")
                bid = self.cfg.bit_browser_id or ""
                if not bid and self.cfg.bit_browser_name:
                    found = bb.find_by_name(self.cfg.bit_browser_name)
                    bid = (found or {}).get("id", "")
                if not bid:
                    raise BrowserError("未配置比特浏览器 ID / 名称")
                self.ws_endpoint = bb.ws_endpoint(bid)
                self.store_status.value = f"比特浏览器已连接 · {bid}"
            elif kind == "站斧浏览器":
                zf = ZhanfuBrowser()
                port = int(self.cfg.zhanfu_port or 9222)
                self.ws_endpoint = zf.ws_endpoint(port)
                self.store_status.value = f"站斧浏览器已连接 · 端口 {port}"
            elif kind == "本机Edge":
                info = launch_edge()
                self.ws_endpoint = info["ws"]
            else:
                info = launch_browser("chrome", url="https://www.dianxiaomi.com/web/smt/smtProductList/offline")
                self.ws_endpoint = info["ws"]
                self.store_status.value = f"Edge 已启动 · 端口 {info['port']} · {info['browser']}"
            self.store_status.color = ACCENT_2
        except BrowserError as e:
            self.store_status.value = f"启动失败：{e}"; self.store_status.color = "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"启动异常：{e}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _cookies(self):
        # 0) 手动粘贴的 Cookie（最高优先级）
        manual = getattr(self.cfg, "manual_cookies", None)
        if isinstance(manual, dict) and manual:
            return dict(manual)
        # 1) 优先直接读本机浏览器（已登录的 Chrome/Edge），无需 CDP
        try:
            from ..integrations.browser_cookies import read_best, is_logged_in
            src = read_best("dianxiaomi.com")
            if src.ok and is_logged_in(src.cookies):
                return src.cookies
        except Exception:
            pass
        # 2) 回退到 CDP
        if not self.ws_endpoint:
            raise RuntimeError("未读取到浏览器 Cookie，请先登录 Chrome 或点「打开浏览器」")
        return cookies_via_cdp(self.ws_endpoint, domains=["dianxiaomi"])

    def _check_dxm_login(self):
        try:
            ck = self._cookies()
            if not ck:
                self.store_status.value = "未读取到店小秘 Cookie，请先在浏览器登录店小秘"
                self.store_status.color = "#FFB020"
            else:
                client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                ok = client.check_login()
                detail = ""
                if ok:
                    try:
                        info = client.user_info()
                        detail = f" · {info.get('account', '')}"
                    except Exception:
                        pass
                self.store_status.value = (f"店小秘登录有效{detail}" if ok else "登录已失效，请在浏览器重新登录")
                self.store_status.color = ACCENT_2 if ok else "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"检测失败：{e}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _fetch_products(self):
        try:
            client = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            self.products = client.product_list(page=1, size=50)
            self.store_status.value = f"已拉取 {len(self.products)} 个产品"
            self.store_status.color = ACCENT_2
        except Exception as e:
            self.store_status.value = f"拉取失败：{e}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _dry_run(self):
        try:
            prod = self.products[0] if self.products else demo_product()
            pub = Publisher(self.cfg, dxm=None,
                            text_service=TextService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg))
            res = pub.run(prod, dry_run=not self.cfg.auto_save_products)
            for line in res.log().splitlines():
                self._log(line)
            self.store_status.value = ("试跑完成" if res.ok else f"试跑失败：{res.error}")
            self.store_status.color = ACCENT_2 if res.ok else "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"试跑异常：{e}"; self.store_status.color = "#FF6B6B"
            self._log(f"试跑异常：{e}")
        self.page.update()

    def _confirm_publish(self):
        if not self.products:
            self.toast("请先拉取产品", False); return
        pid = self.products[0]["id"]
        title = self.products[0].get("title", "")[:40]

        def do_publish(e):
            dlg.open = False
            self.page.update()
            self.store_status.value = f"正在发布 {pid} …"; self.page.update()
            threading.Thread(target=self._publish, args=(pid,), daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("确认发布到速卖通？"),
            content=ft.Text(f"{pid}\n{title}\n\n发布会真实刊登商品，请确认。", color=MUTED),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("确认发布", bgcolor="#D9534F", color="white", on_click=do_publish)],
        )
        self.page.dialog = dlg; dlg.open = True; self.page.update()

    def _publish(self, pid):
        from ..integrations.editor_automation import publish_product
        try:
            r = publish_product(self.ws_endpoint, pid, confirm=True)
            self.store_status.value = ("发布成功：" if r.ok else "发布失败：") + r.message
            self.store_status.color = ACCENT_2 if r.ok else "#FF6B6B"
            self._log(f"发布 {pid}: {r.message}")
        except Exception as ex:
            self.store_status.value = f"发布异常：{ex}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _real_save(self):
        if not self.ws_endpoint:
            self.toast("请先启动浏览器", False); return
        if not self.products:
            self.toast("请先拉取产品", False); return
        pid = self.products[0]["id"]
        self.store_status.value = f"正在保存 {pid} …"; self.page.update()
        try:
            pub = Publisher(self.cfg, ws_endpoint=self.ws_endpoint)
            res = pub.run({"id": pid, "title": self.products[0].get("title", ""),
                           "skus": [], "images": []}, dry_run=False)
            for line in res.log().splitlines():
                self._log(line)
            self.store_status.value = "保存成功" if res.ok else f"保存失败：{res.error}"
            self.store_status.color = ACCENT_2 if res.ok else "#FF6B6B"
        except Exception as ex:
            self.store_status.value = f"保存异常：{ex}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _show_albums(self):
        try:
            client = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            data = client.album_index()
            items = (data.get("data") or {})
            cats = items.get("list") if isinstance(items, dict) else items
            cats = cats or []
            rows = []
            for c in cats[:40]:
                name = c.get("name") or c.get("albumName") or str(c.get("id", ""))
                cid = c.get("id") or c.get("albumId") or ""
                num = c.get("commodityProductNum")
                pic = c.get("picNum") or c.get("imageNum")
                sub = f"{cid}" + (f" · 商品 {num}" if num is not None else "") + (f" · 图片 {pic}" if pic is not None else "")
                rows.append(ft.ListTile(title=ft.Text(name, size=13, color=TEXT),
                                        subtitle=ft.Text(sub, size=10, color=MUTED)))
            if not rows:
                rows = [ft.ListTile(title=ft.Text("未取到相册（接口路径可能需校正）", color=MUTED, size=12))]
            def open_album(e):
                try:
                    open_url_via_cdp(self.ws_endpoint, "https://www.dianxiaomi.com/web/album/index")
                except Exception:
                    pass
            dlg = ft.AlertDialog(title=ft.Text(f"图片空间 / 相册（{len(cats)} 个分类）"),
                                 content=ft.Column(rows, scroll=ft.ScrollMode.AUTO, height=400),
                                 actions=[ft.TextButton("打开相册页面", on_click=open_album),
                                          ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
            self.page.dialog = dlg; dlg.open = True; self.page.update()
        except Exception as ex:
            self.toast(f"读取相册失败：{ex}", False)

    def _close_dlg(self, dlg):
        dlg.open = False
        self.page.update()

    def _open_template_picker(self, source: str, on_pick=None):
        try:
            ck = self._cookies()
            c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            sid = self.cfg.store_id or None
            data = {"draft": c.drafts(limit=300, shop_id=sid), "online": c.online(limit=300, shop_id=sid)}
            totals = c.counts(sid) if sid else {}
        except Exception as ex:
            self.toast(f"读取失败：{ex}", False); return

        state = {"source": source if source in data else "draft", "kw": ""}
        list_col = ft.Column(spacing=2, height=420, scroll=ft.ScrollMode.AUTO)
        search = ft.TextField(hint_text="搜索标题或产品ID", prefix_icon=ft.Icons.SEARCH, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT, on_change=lambda e: render())
        tab_row = ft.Row(spacing=8)

        def pick(pid, title, images):
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = title
            self.cfg.template_source = state["source"]
            save_config(self.cfg)
            self.toast(f"已选模板：{title[:24]}")
            dlg.open = False
            self.page.update()
            if on_pick:
                on_pick(pid, title, images)
            threading.Thread(target=self._read_template, daemon=True).start()

        def render():
            items = data[state["source"]]
            kw = (state["kw"] or "").strip().lower()
            if kw:
                items = [x for x in items if kw in (x.get("title") or "").lower() or kw in str(x.get("id"))]
            rows = []
            for it in items[:120]:
                imgs = it.get("images") or []
                thumb = imgs[0] if imgs else None
                rows.append(ft.ListTile(
                    leading=ft.Container(width=44, height=44, border_radius=6, bgcolor=CARD_2,
                                         clip_behavior=ft.ClipBehavior.HARD_EDGE,
                                         content=ft.Image(src=thumb, fit=ft.ImageFit.COVER) if thumb else
                                         ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED, size=18, color=MUTED)),
                    title=ft.Text((it.get("title") or it["id"])[:60], size=12, color=TEXT, max_lines=2),
                    subtitle=ft.Text(f"{it['id']} · {it.get('status_type')}", size=10, color=MUTED),
                    dense=True, on_click=lambda e, x=it: pick(x["id"], x.get("title", ""), x.get("images") or []),
                ))
            list_col.controls = rows or [ft.Text("无匹配结果", size=12, color=MUTED)]
            tab_row.controls = [
                ft.ElevatedButton(f"待发布 {totals.get('editingRequired', len(data['draft']))}", bgcolor=ACCENT_2 if state["source"] == "draft" else CARD_2,
                                  color="#04231C" if state["source"] == "draft" else TEXT,
                                  on_click=lambda e: switch("draft")),
                ft.ElevatedButton(f"在线 {totals.get('onSelling', len(data['online']))}", bgcolor=ACCENT_2 if state["source"] == "online" else CARD_2,
                                  color="#04231C" if state["source"] == "online" else TEXT,
                                  on_click=lambda e: switch("online")),
            ]
            self.page.update()

        def switch(src):
            state["source"] = src
            self.cfg.template_source = src
            save_config(self.cfg)
            render()

        search.on_change = lambda e: (state.update({"kw": e.control.value or ""}), render())

        dlg = ft.AlertDialog(
            title=ft.Text("选择模板产品"),
            content=ft.Column([tab_row, search, list_col], spacing=10, width=620),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg; dlg.open = True
        render()

    def _template_preview_row(self, images=None):
        thumb = (images or [None])[0] if images else None
        title = self.cfg.template_product_title or "未选择模板"
        return ft.Row([
            ft.Container(width=40, height=40, border_radius=6, bgcolor=CARD_2, clip_behavior=ft.ClipBehavior.HARD_EDGE,
                         content=ft.Image(src=thumb, fit=ft.ImageFit.COVER) if thumb else
                         ft.Icon(ft.Icons.IMAGE_OUTLINED, size=16, color=MUTED)),
            ft.Text(title[:44], size=12, color=TEXT),
        ], spacing=10)

    def _open_template_dialog(self):
        try:
            ck = self._cookies()
            c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            drafts = c.drafts(limit=60)
            online = c.online(limit=60)
        except Exception as ex:
            self.toast(f"读取失败：{ex}", False); return

        state = {"source": self.cfg.template_source or "draft"}

        list_col = ft.Column(spacing=4, height=380, scroll=ft.ScrollMode.AUTO)

        def render():
            items = drafts if state["source"] == "draft" else online
            rows = []
            for it in items:
                def choose(e, pid=it["id"], title=it.get("title", "")):
                    self.cfg.template_product_id = str(pid)
                    self.cfg.template_product_title = title
                    self.cfg.template_source = state["source"]
                    save_config(self.cfg)
                    self.toast(f"已选模板：{title[:28]}")
                    threading.Thread(target=self._read_template, daemon=True).start()
                    dlg.open = False
                    self.page.update()
                rows.append(ft.ListTile(
                    title=ft.Text(it.get("title") or it["id"], size=12, color=TEXT),
                    subtitle=ft.Text(f"{it['id']} · {it.get('status_type')}", size=10, color=MUTED),
                    on_click=choose, dense=True))
            list_col.controls = rows or [ft.Text("无数据", size=12, color=MUTED)]
            self.page.update()

        def switch(src):
            state["source"] = src
            render()

        tabs = ft.Row([
            ft.OutlinedButton(f"草稿 {len(drafts)}", on_click=lambda e: switch("draft")),
            ft.OutlinedButton(f"在线 {len(online)}", on_click=lambda e: switch("online")),
        ], spacing=8)

        dlg = ft.AlertDialog(
            title=ft.Text("选择模板产品（草稿 / 在线）"),
            content=ft.Column([tabs,
                               ft.Text("选中后会自动读取该模板的类目/运费/属性", size=11, color=MUTED),
                               list_col], spacing=8, width=560),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg; dlg.open = True
        render()
        self.page.update()

    def _show_albums(self):
        try:
            client = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            data = client.album_index()
            items = (data.get("data") or {})
            cats = items.get("list") if isinstance(items, dict) else items
            cats = cats or []
            rows = []
            for c in cats[:40]:
                name = c.get("name") or c.get("albumName") or str(c.get("id", ""))
                cid = c.get("id") or c.get("albumId") or ""
                num = c.get("commodityProductNum")
                pic = c.get("picNum") or c.get("imageNum")
                sub = f"{cid}" + (f" · 商品 {num}" if num is not None else "") + (f" · 图片 {pic}" if pic is not None else "")
                rows.append(ft.ListTile(title=ft.Text(name, size=13, color=TEXT),
                                        subtitle=ft.Text(sub, size=10, color=MUTED)))
            if not rows:
                rows = [ft.ListTile(title=ft.Text("未取到相册（接口路径可能需校正）", color=MUTED, size=12))]
            def open_album(e):
                try:
                    open_url_via_cdp(self.ws_endpoint, "https://www.dianxiaomi.com/web/album/index")
                except Exception:
                    pass
            dlg = ft.AlertDialog(title=ft.Text(f"图片空间 / 相册（{len(cats)} 个分类）"),
                                 content=ft.Column(rows, scroll=ft.ScrollMode.AUTO, height=400),
                                 actions=[ft.TextButton("打开相册页面", on_click=open_album),
                                          ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
            self.page.dialog = dlg; dlg.open = True; self.page.update()
        except Exception as ex:
            self.toast(f"读取相册失败：{ex}", False)

    def _close_dlg(self, dlg):
        dlg.open = False
        self.page.update()

    def _open_template_picker(self, source: str, on_pick=None):
        try:
            ck = self._cookies()
            c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            sid = self.cfg.store_id or None
            data = {"draft": c.drafts(limit=300, shop_id=sid), "online": c.online(limit=300, shop_id=sid)}
            totals = c.counts(sid) if sid else {}
        except Exception as ex:
            self.toast(f"读取失败：{ex}", False); return

        state = {"source": source if source in data else "draft", "kw": ""}
        list_col = ft.Column(spacing=2, height=420, scroll=ft.ScrollMode.AUTO)
        search = ft.TextField(hint_text="搜索标题或产品ID", prefix_icon=ft.Icons.SEARCH, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT, on_change=lambda e: render())
        tab_row = ft.Row(spacing=8)

        def pick(pid, title, images):
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = title
            self.cfg.template_source = state["source"]
            save_config(self.cfg)
            self.toast(f"已选模板：{title[:24]}")
            dlg.open = False
            self.page.update()
            if on_pick:
                on_pick(pid, title, images)
            threading.Thread(target=self._read_template, daemon=True).start()

        def render():
            items = data[state["source"]]
            kw = (state["kw"] or "").strip().lower()
            if kw:
                items = [x for x in items if kw in (x.get("title") or "").lower() or kw in str(x.get("id"))]
            rows = []
            for it in items[:120]:
                imgs = it.get("images") or []
                thumb = imgs[0] if imgs else None
                rows.append(ft.ListTile(
                    leading=ft.Container(width=44, height=44, border_radius=6, bgcolor=CARD_2,
                                         clip_behavior=ft.ClipBehavior.HARD_EDGE,
                                         content=ft.Image(src=thumb, fit=ft.ImageFit.COVER) if thumb else
                                         ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED, size=18, color=MUTED)),
                    title=ft.Text((it.get("title") or it["id"])[:60], size=12, color=TEXT, max_lines=2),
                    subtitle=ft.Text(f"{it['id']} · {it.get('status_type')}", size=10, color=MUTED),
                    dense=True, on_click=lambda e, x=it: pick(x["id"], x.get("title", ""), x.get("images") or []),
                ))
            list_col.controls = rows or [ft.Text("无匹配结果", size=12, color=MUTED)]
            tab_row.controls = [
                ft.ElevatedButton(f"待发布 {totals.get('editingRequired', len(data['draft']))}", bgcolor=ACCENT_2 if state["source"] == "draft" else CARD_2,
                                  color="#04231C" if state["source"] == "draft" else TEXT,
                                  on_click=lambda e: switch("draft")),
                ft.ElevatedButton(f"在线 {totals.get('onSelling', len(data['online']))}", bgcolor=ACCENT_2 if state["source"] == "online" else CARD_2,
                                  color="#04231C" if state["source"] == "online" else TEXT,
                                  on_click=lambda e: switch("online")),
            ]
            self.page.update()

        def switch(src):
            state["source"] = src
            self.cfg.template_source = src
            save_config(self.cfg)
            render()

        search.on_change = lambda e: (state.update({"kw": e.control.value or ""}), render())

        dlg = ft.AlertDialog(
            title=ft.Text("选择模板产品"),
            content=ft.Column([tab_row, search, list_col], spacing=10, width=620),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg; dlg.open = True
        render()

    def _template_preview_row(self, images=None):
        thumb = (images or [None])[0] if images else None
        title = self.cfg.template_product_title or "未选择模板"
        return ft.Row([
            ft.Container(width=40, height=40, border_radius=6, bgcolor=CARD_2, clip_behavior=ft.ClipBehavior.HARD_EDGE,
                         content=ft.Image(src=thumb, fit=ft.ImageFit.COVER) if thumb else
                         ft.Icon(ft.Icons.IMAGE_OUTLINED, size=16, color=MUTED)),
            ft.Text(title[:44], size=12, color=TEXT),
        ], spacing=10)

    def _open_template_dialog(self):
        rows = []
        for p in self.products[:50]:
            title = p.get("title") or p.get("name") or p.get("productTitle") or str(p.get("id", ""))

            def choose(e, prod=p):
                self.cfg.product_template_id = str(prod.get("id", ""))
                self.cfg.product_template_name = title
                self.cfg.product_id_title = title
                save_config(self.cfg)
                self.dlg.open = False
                self.page.update()
                self._switch(1)
                self.toast("模板已设置")

            rows.append(ft.ListTile(title=ft.Text(title[:60], color=TEXT, size=13), on_click=choose))
        if not rows:
            rows = [ft.ListTile(title=ft.Text("暂无产品，请先点击「拉取产品」", color=MUTED, size=13))]
        self.dlg = ft.AlertDialog(title=ft.Text("选择模板产品"), content=ft.Column(rows, scroll=ft.ScrollMode.AUTO, height=420),
                                  actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dialog())])
        self.page.dialog = self.dlg
        self.dlg.open = True
        self.page.update()

    def _close_dialog(self):
        self.dlg.open = False
        self.page.update()

    def _p_advanced(self):
        self._current = 2
        # 去重：其他页面已有的字段不在高级设置重复出现
        others = set()
        for fn in (schema_store, schema_ai, schema_review, schema_image, schema_price, schema_variant):
            for _sec, fields in fn():
                others.update(f.key for f in fields)
        deduped = []
        for sec_name, fields in schema_advanced():
            kept = [f for f in fields if f.key not in others]
            if kept:
                deduped.append((sec_name, kept))
        return self._render("高级设置", deduped)

    def _p_ai(self):
        self._current = 3
        return self._render("AI 智能填写", schema_ai())

    def _p_review(self):
        self._current = 4
        return self._render("内容审查", schema_review())

    def _p_image(self):
        self._current = 5
        view = self._render("AI图片处理", schema_image())
        self.img_status = ft.Text("未选择图片", color=MUTED, size=12)
        self.img_results = ft.Column(spacing=6)
        self.img_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)

        def pick(e):
            self.file_picker.pick_files(allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"])

        def run(kind):
            if not self.picked:
                self.toast("请先选择图片", False); return
            if not self.cfg.api_key:
                self.toast("请先在设置里填写 API Key", False); return
            self.img_busy.visible = True; self.page.update()
            threading.Thread(target=self._do_image, args=(kind,), daemon=True).start()

        tool = card(ft.Column([
            h1("图片工具", 15),
            ft.Row([ft.OutlinedButton("选择图片", icon=ft.Icons.UPLOAD_FILE, on_click=pick),
                    ft.ElevatedButton("白底图", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                    ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                    ft.ElevatedButton("通用编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                    self.img_busy], spacing=10),
            self.img_status, self.img_results], spacing=10))
        return [view[0], tool] + view[1:]

    def _p_price(self):
        self._current = 6
        self.base_input = ft.TextField(label="原始价格（试算）", value="100", width=180,
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.price_out = ft.Text("—", color=ACCENT_2, size=16, weight=ft.FontWeight.BOLD)

        def calc(e):
            try:
                base = float(self.base_input.value or 0)
                mult = float(self.cfg.price_multiplier or 1.2)
                add = float(self.cfg.price_fixed_add or 0)
                if self.cfg.price_mode == "free_shipping":
                    r = calc_free_shipping(base, float(self.cfg.default_express_price or 0),
                                           float(self.cfg.sales_profit or 0) / 100)
                else:
                    r = calc_multiplier(base, mult, add)
                self.price_out.value = f"¥{r.final}（{r.note}）"
            except Exception as ex:
                self.price_out.value = f"计算失败：{ex}"
            self.page.update()

        self.tpl_kw = ft.TextField(label="模板关键词", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.tpl_list = ft.Column(spacing=4)
        self.tpl_status = ft.Text("", size=12, color=MUTED)

        def search(e):
            self.tpl_status.value = "搜索中…"; self.page.update()
            threading.Thread(target=self._search_templates, daemon=True).start()

        tpl_card = card(ft.Column([h1("区域调价模板", 15),
                                   ft.Row([self.tpl_kw, ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, on_click=search)], spacing=10),
                                   self.tpl_status, self.tpl_list], spacing=10))

        calc_card = card(ft.Column([h1("价格试算", 15), ft.Row([self.base_input,
                                                                 ft.ElevatedButton("计算", on_click=calc),
                                                                 self.price_out], spacing=14)], spacing=10))
        view = self._render("价格设置", schema_price())
        return [view[0], calc_card, tpl_card] + view[1:]

    def _search_templates(self):
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            data = client.search_price_templates(self.tpl_kw.value or "")
            items = (data.get("data") or {})
            items = items.get("list") if isinstance(items, dict) else items
            items = items or []
            rows = []
            for t in items[:30]:
                name = t.get("name") or t.get("templateName") or str(t.get("id", ""))
                tid = str(t.get("id", ""))

                def apply(e, _tid=tid, _name=name):
                    self.cfg.adjust_price_template_id = _tid
                    self.cfg.adjust_price_template_name = _name
                    save_config(self.cfg)
                    self.toast(f"已应用模板：{_name}")

                rows.append(ft.ListTile(title=ft.Text(name, size=13, color=TEXT),
                                        subtitle=ft.Text(tid, size=10, color=MUTED), on_click=apply))
            self.tpl_list.controls = rows or [ft.Text("无结果（接口路径可能需校正）", size=12, color="#FFB020")]
            self.tpl_status.value = f"共 {len(rows)} 个模板"
        except Exception as ex:
            self.tpl_status.value = f"搜索失败：{ex}"
            self.tpl_list.controls = []
        self.page.update()

    def _p_variant(self):
        self._current = 7
        self.src_attrs = ft.TextField(label="源属性（逗号分隔）", value="Model Number, Power (W)",
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.tgt_attrs = ft.TextField(label="目标属性（逗号分隔）", value="型号, 功率",
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.map_out = ft.Column(spacing=4)

        def preview(e):
            src = [x.strip() for x in (self.src_attrs.value or "").split(",") if x.strip()]
            tgt = [x.strip() for x in (self.tgt_attrs.value or "").split(",") if x.strip()]
            mapping = auto_map(src, tgt)
            self.map_out.controls = [ft.Text(f"{k}  →  {v}", size=12, color=MUTED) for k, v in mapping.items()] or \
                                    [ft.Text("无匹配结果", size=12, color="#FFB020")]
            self.page.update()

        card_map = card(ft.Column([h1("自动映射预览", 15), self.src_attrs, self.tgt_attrs,
                                   ft.ElevatedButton("生成映射", on_click=preview), self.map_out], spacing=10))

        # 可视化映射编辑器
        self.rows_col = ft.Column(spacing=6)
        self._mapping_rows: list[tuple[ft.TextField, ft.TextField]] = []

        def add_row(e, src="", dst=""):
            a = ft.TextField(value=src, hint_text="源属性", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            b = ft.TextField(value=dst, hint_text="目标属性", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            self._mapping_rows.append((a, b))
            self.rows_col.controls.append(ft.Row([a, b], spacing=8))
            self.page.update()

        def save_rows(e):
            data = [{"from": a.value, "to": b.value} for a, b in self._mapping_rows if (a.value or "").strip()]
            import json as _json
            self.cfg.variant_mappings = _json.dumps(data, ensure_ascii=False)
            save_config(self.cfg)
            self._log(f"映射已保存：{len(data)} 条")
            self.toast(f"已保存 {len(data)} 条映射")

        import json as _json
        try:
            for row in _json.loads(self.cfg.variant_mappings or "[]"):
                add_row(None, str(row.get("from", "")), str(row.get("to", "")))
        except Exception:
            pass

        editor = card(ft.Column([h1("映射编辑器", 15),
                                 ft.Row([ft.ElevatedButton("新增一行", icon=ft.Icons.ADD, on_click=add_row),
                                         ft.ElevatedButton("保存映射", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white",
                                                           on_click=save_rows)], spacing=10),
                                 self.rows_col], spacing=10))
        view = self._render("变种设置", schema_variant())
        return [view[0], card_map, editor] + view[1:]

    def _image_bench(self):
        self.aw_img = ft.TextField(label="图片 URL", bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.img_out = ft.TextField(label="处理结果", multiline=True, min_lines=6, read_only=True,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.aw_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)

        def run(kind):
            self.aw_busy.visible = True
            try:
                self.page.update()
            except Exception:
                pass
            threading.Thread(target=self._ai_run, args=(kind,), daemon=True).start()

        return card(ft.Column([h1("图片测试台", 15),
                               muted("贴一张图片 URL，按当前开关跑流水线或审查"),
                               self.aw_img,
                               ft.Row([ft.ElevatedButton("跑流水线", on_click=lambda e: run("image")),
                                       ft.ElevatedButton("图片审查", on_click=lambda e: run("review")),
                                       ft.ElevatedButton("读图估重", on_click=lambda e: run("weight")),
                                       self.aw_busy], spacing=10),
                               self.img_out], spacing=12))

    def _ai_run(self, kind):
        try:
            if not self.cfg.api_key:
                self.img_out.value = "请先在设置里填写 API Key"
                return
            if kind == "image":
                pipe = ImagePipeline(self.cfg)
                res = pipe.run(self.aw_img.value.strip())
                self.img_out.value = "\n".join(
                    f"{'✓' if s.ok else '✗'} {s.name}: {s.url or s.error}" for s in res.steps)
            elif kind == "review":
                self.img_out.value = ImagePipeline(self.cfg).review(self.aw_img.value.strip(), "main")
            else:
                r = TextPipeline(self.cfg).estimate_weight(self.aw_img.value.strip(), "")
                self.img_out.value = r.value if r.ok else f"失败: {r.error}"
            self._log(f"图片测试台 {kind} 完成")
        except Exception as ex:
            self.img_out.value = f"异常: {ex}"
            self._log(f"图片测试台 {kind} 异常: {ex}")
            self._maybe_quota(str(ex))
        finally:
            self.aw_busy.visible = False
            self.page.update()

    def _p_workbench(self):
        self._current = 9
        self.wb_urls = ft.TextField(label="图片 URL（每行一个）", multiline=True, min_lines=4, max_lines=8,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_status = ft.Text("未加载图片", color=MUTED, size=12)
        self.wb_results = ft.Column(spacing=4)
        self.wb_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)

        def load(e):
            urls = [u.strip() for u in (self.wb_urls.value or "").splitlines() if u.strip()]
            if not urls:
                self.toast("请输入图片 URL", False); return
            self.wb_status.value = f"共 {len(urls)} 个链接，开始下载…"
            self.page.update()
            threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

        def run(kind):
            if not self.picked:
                self.toast("请先加载图片", False); return
            if not self.cfg.api_key:
                self.toast("请先设置 API Key", False); return
            self.wb_busy.visible = True; self.page.update()
            threading.Thread(target=self._wb_process, args=(kind,), daemon=True).start()

        self.q = TaskQueue()
        self.q_text = ft.Text("", size=12, color=MUTED)
        self._refresh_queue()

        def enqueue(e):
            n = self.q.add_many(self.products)
            self._refresh_queue()
            self.toast(f"已入队 {n} 个产品")

        def process(e):
            items = self.q.pending()
            if not items:
                self.toast("队列为空", False); return
            threading.Thread(target=self._process_queue, daemon=True).start()

        self.q_panel = card(ft.Column([h1("任务队列", 15), self.q_text,
                                       ft.Row([ft.ElevatedButton("产品入队", icon=ft.Icons.ADD_TASK, on_click=enqueue),
                                               ft.ElevatedButton("处理队列(dry-run)", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT, color="white", on_click=process),
                                               ft.OutlinedButton("清理已完成", on_click=lambda e: (self.q.clear_done(), self._refresh_queue()))], spacing=10)], spacing=8))
        return [h1("图片工作台"), muted("批量白底 / 去水印 / 编辑（走你的 API 站点计费）"), self.q_panel,
                card(ft.Column([self.wb_urls,
                                ft.Row([ft.ElevatedButton("加载图片", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=load),
                                        ft.OutlinedButton("从已拉取产品取图", icon=ft.Icons.INVENTORY_2, on_click=lambda e: self._images_from_products()),
                                        ft.ElevatedButton("白底", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                                        ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                                        ft.ElevatedButton("编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                                        ft.OutlinedButton("暂停", on_click=lambda e: self._wb_ctl("pause")),
                                        ft.OutlinedButton("继续", on_click=lambda e: self._wb_ctl("resume")),
                                        ft.OutlinedButton("取消", on_click=lambda e: self._wb_ctl("cancel")),
                                        ft.ElevatedButton("上传结果到相册", icon=ft.Icons.CLOUD_UPLOAD, bgcolor="#F0A020", color="#1A1200",
                                                          on_click=lambda e: threading.Thread(target=self._upload_results, daemon=True).start()),
                                        self.wb_busy], spacing=10),
                                self.wb_status], spacing=10)),
                card(self.wb_results)]

    def _refresh_queue(self):
        st = self.q.stats()
        self.q_text.value = f"待处理 {st['pending']} · 处理中 {st['running']} · 完成 {st['done']} · 失败 {st['failed']}"
        try:
            self.page.update()
        except Exception:
            pass

    def _process_queue(self):
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
        except Exception as ex:
            self._log(f"队列：未连接浏览器（{ex}），将以离线模式运行")
            dxm = None
        pub = Publisher(self.cfg, dxm=dxm, ws_endpoint=self.ws_endpoint)
        ok = fail = 0
        by_id = {p["id"]: p for p in self.products}
        for item in self.q.pending():
            self.q.mark(item.product_id, "running")
            self._refresh_queue()
            base = by_id.get(item.product_id, {})
            prod = {"id": item.product_id,
                    "title": base.get("title") or item.title,
                    "skus": base.get("skus") or [],
                    "images": base.get("images") or [],
                    "cost": 0}
            res = pub.run(prod, dry_run=not self.cfg.auto_save_products)
            self.q.mark(item.product_id, "done" if res.ok else "failed", res.error)
            ok += 1 if res.ok else 0
            fail += 0 if res.ok else 1
            self._log(f"队列 {item.product_id}: {'完成' if res.ok else '失败 ' + res.error}")
            self._refresh_queue()
        history.record("queue", ok, fail)
        self.toast(f"队列处理完成：成功 {ok} / 失败 {fail}")

    def _images_from_products(self):
        urls: list[str] = []
        for p in self.products:
            for key in ("images", "imageList", "mainImages", "imgList"):
                v = p.get(key)
                if isinstance(v, list):
                    for it in v:
                        if isinstance(it, str):
                            urls.append(it)
                        elif isinstance(it, dict):
                            u = it.get("url") or it.get("imageUrl") or it.get("src")
                            if u:
                                urls.append(u)
            u1 = p.get("imgUrl") or p.get("mainImage") or p.get("image")
            if isinstance(u1, str):
                urls.append(u1)
        urls = [u for u in dict.fromkeys(urls) if str(u).startswith("http")]
        if not urls:
            self.toast("未取到图片，请先「拉取产品」或手动粘贴 URL", False); return
        self.wb_urls.value = "\n".join(urls[:60])
        self.toast(f"已填入 {min(len(urls),60)} 个图片链接")

    def _download_images(self, urls):
        import requests as _rq
        from pathlib import Path as _P
        import tempfile as _tf
        d = _P(_tf.gettempdir()) / "aurora-workbench"
        d.mkdir(parents=True, exist_ok=True)
        got = []
        for i, u in enumerate(urls):
            try:
                r = _rq.get(u, timeout=30)
                r.raise_for_status()
                p = d / f"img_{i:03d}.png"
                p.write_bytes(r.content)
                got.append(p)
            except Exception as ex:
                self._log(f"下载失败 {u[:60]}: {ex}")
        self.picked = got
        self.wb_status.value = f"已下载 {len(got)}/{len(urls)} 张"
        self.wb_status.color = ACCENT_2 if got else "#FF6B6B"
        self.page.update()

    def _upload_results(self):
        urls = [c.controls[1].value for c in self.wb_results.controls
                if len(c.controls) > 1 and isinstance(c.controls[1], ft.Text) and str(c.controls[1].value).startswith("http")]
        if not urls:
            self.toast("没有可上传的结果", False); return
        try:
            import asyncio
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            ok = 0
            for u in urls[:20]:
                try:
                    client.upload_image_from_url(u)
                    ok += 1
                except Exception as ex:
                    self._log(f"上传失败 {u[:50]}: {ex}")
            self.toast(f"已上传 {ok}/{len(urls)} 张到相册")
            self._log(f"图片上传完成：{ok}/{len(urls)}")
        except Exception as ex:
            self.toast(f"上传失败：{ex}", False)

    def _wb_ctl(self, action):
        r = getattr(self, "wb_runner", None)
        if not r:
            self.toast("当前没有运行中的任务", False); return
        getattr(r, action)()
        self._log(f"任务控制：{action}")
        self.toast({"pause": "已暂停", "resume": "已继续", "cancel": "已取消"}[action])

    def _wb_process(self, kind):
        svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout))
        runner = BatchRunner(self.cfg.concurrency, retries=1)
        self.wb_runner = runner
        self.wb_results.controls = []

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), self.cfg.edit_prompt or "clean product photo")

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            if not res.ok:
                self._maybe_quota(res.error)
            self.wb_results.controls.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                                    ft.Text(res.value if res.ok else res.error, size=11,
                                                            color=MUTED, selectable=True)], spacing=8))
            self.page.update()

        results = runner.run(list(self.picked), job, done)
        ok = sum(1 for r in results if r.ok)
        try:
            history.record("workbench:" + kind, ok, len(results) - ok)
        except Exception:
            pass
        self.wb_status.value = f"处理完成：成功 {ok} / {len(results)}"
        self.wb_busy.visible = False
        self.page.update()

    def _p_logs(self):
        self._current = 10
        self.log_level = ft.Dropdown(width=120, value="全部", bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     options=[ft.dropdown.Option(x) for x in ("全部","INFO","WARN","ERROR")],
                                     on_change=lambda e: self._refresh_log_table())
        self.log_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("时间", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("级别", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("内容", size=11, color=MUTED))],
            rows=[], column_spacing=14, heading_row_height=28, data_row_min_height=24,
        )
        self._refresh_log_table()

        def build_video(e):
            if not self.picked:
                self.toast("请先在「AI图片处理」选择图片", False); return
            info = video_probe()
            if not info["available"]:
                self.toast("未检测到 ffmpeg，请先安装", False); return
            out = Path(tempfile.gettempdir()) / "aurora-main-video.mp4"
            try:
                build_slideshow(list(self.picked), out)
                self._log(f"视频已生成：{out}")
                self.toast(f"视频已生成：{out}")
            except Exception as ex:
                self._log(f"视频生成失败：{ex}")
                self.toast(str(ex), False)

        def check_update(e):
            r = updater.check(self.cfg.license_server)
            if not r.get("ok"):
                self.toast(f"检查失败：{r.get('error')}", False); return
            msg = f"最新版本 {r['latest']}" if r.get("has_update") else "已是最新版本"
            self._log(f"更新检查：{msg}")
            self.toast(msg)

        def export(e):
            out = Path(tempfile.gettempdir()) / "aurora-logs.txt"
            rows = getattr(self, "log_rows", [])
            out.write_text("\n".join(f"{t} [{lv}] {m}" for t, lv, m in rows), encoding="utf-8")
            self.toast(f"已导出：{out}")

        bar = card(ft.Row([
            ft.ElevatedButton("生成主图视频", icon=ft.Icons.MOVIE, bgcolor=ACCENT, color="white", on_click=build_video),
            ft.ElevatedButton("检查更新", icon=ft.Icons.SYSTEM_UPDATE, bgcolor=CARD_2, color=TEXT, on_click=check_update),
            ft.ElevatedButton("导出日志", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=export),
            self.log_level,
            ft.ElevatedButton("清空", icon=ft.Icons.DELETE_OUTLINE, bgcolor=CARD_2, color=TEXT,
                              on_click=lambda e: (self.log_rows.clear(), self.logs.clear(), self._refresh_log_table())),
        ], spacing=10))
        try:
            jobs = self.scheduler.pending() if hasattr(self, "scheduler") else []
        except Exception:
            jobs = []
        sched_rows = [ft.Text(f"{j.product_id} · {time.strftime('%m-%d %H:%M', time.localtime(j.run_at))} · {j.title[:20]}",
                              size=11, color=TEXT) for j in jobs[:8]] or [ft.Text("暂无定时任务", size=11, color=MUTED)]
        return [h1("任务与日志"), bar,
                card(ft.Column([h1("定时任务", 14)] + sched_rows, spacing=6)),
                card(ft.Column([self.log_table], scroll=ft.ScrollMode.AUTO, height=360))]

    def _log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        if not hasattr(self, "log_rows"):
            self.log_rows = []
        self.log_rows.append((ts, level, str(msg)))
        self.log_rows = self.log_rows[-1000:]
        self.logs = [f"{t} [{lv}] {m}" for t, lv, m in self.log_rows]
        if getattr(self, "log_table", None) is not None:
            self._refresh_log_table()

    def _refresh_log_table(self):
        rows = getattr(self, "log_rows", [])
        lv = getattr(self, "log_level", None)
        want = lv.value if lv is not None else "全部"
        shown = [(t, x, m) for t, x, m in rows if want == "全部" or x == want]
        self.log_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(t, size=11, color=MUTED)),
                ft.DataCell(ft.Text(x, size=11, color=ACCENT_2 if x == "INFO" else ("#FFB020" if x == "WARN" else "#FF6B6B"))),
                ft.DataCell(ft.Text(m, size=11, color=TEXT)),
            ]) for t, x, m in reversed(shown[-300:])
        ]
        try:
            self.page.update()
        except Exception:
            pass

    def _p_settings(self):
        self._current = 9
        def recharge(e):
            try:
                self.page.launch_url("https://api.moneyfly.club")
            except Exception:
                self.toast("请在浏览器打开 https://api.moneyfly.club", False)

        recharge_card = card(ft.Column([
            h1("额度", 15),
            muted("额度不足时，点击下面按钮登录站点充值后回到本页即可继续使用（无需改任何配置）"),
            ft.Row([ft.ElevatedButton("额度不足？去充值", icon=ft.Icons.ACCOUNT_BALANCE_WALLET,
                                      bgcolor="#F0A020", color="#1A1200", on_click=recharge)], spacing=10),
        ], spacing=10))
        view = self._render("设置", schema_settings(self.cfg))
        self.inst_name = ft.TextField(label="新实例名称", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.inst_list = ft.Column(spacing=4)
        self._refresh_instances()

        def create(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                create_instance(name)
                self._log(f"实例已创建：{name}")
                self._refresh_instances()
                self.toast("实例已创建")
            except Exception as ex:
                self.toast(str(ex), False)

        def launch(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                launch_instance(name)
                self._log(f"实例已启动：{name}")
                self.toast("新实例已启动")
            except Exception as ex:
                self.toast(str(ex), False)

        inst_card = card(ft.Column([h1("多实例", 15),
                                    ft.Row([self.inst_name,
                                            ft.ElevatedButton("创建", on_click=create),
                                            ft.ElevatedButton("启动", on_click=launch)], spacing=10),
                                    self.inst_list], spacing=10))

        upd = card(ft.Column([h1("版本更新", 15),
                              ft.Row([ft.ElevatedButton("检查更新", icon=ft.Icons.SYSTEM_UPDATE,
                                                        on_click=lambda e: threading.Thread(target=self._check_update, daemon=True).start())])],
                             spacing=8))
        self.dbg_key = ft.Dropdown(label="接口", width=260, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                   options=[ft.dropdown.Option(k) for k in ENDPOINTS], value="album")
        self.dbg_path = ft.TextField(label="路径覆盖（留空用默认）", value="", bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.dbg_method = ft.Dropdown(label="方法", width=120, value="GET", bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                      options=[ft.dropdown.Option("GET"), ft.dropdown.Option("POST")])
        self.dbg_payload = ft.TextField(label="请求体 JSON（POST）", value="{}", multiline=True, min_lines=2,
                                        bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.dbg_out = ft.TextField(label="响应", multiline=True, min_lines=4, read_only=True,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def save_override(e):
            ov = dict(self.cfg.endpoint_overrides or {})
            if self.dbg_path.value.strip():
                ov[self.dbg_key.value] = self.dbg_path.value.strip()
            else:
                ov.pop(self.dbg_key.value, None)
            self.cfg.endpoint_overrides = ov
            save_config(self.cfg)
            self.toast("端点已保存")

        def send(e):
            self.dbg_out.value = "请求中…"; self.page.update()
            threading.Thread(target=self._debug_call, daemon=True).start()

        self.curl_text = ft.TextField(label="粘贴 cURL（自动识别路径）", multiline=True, min_lines=2,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def parse_curl(e):
            import re as _re
            txt = self.curl_text.value or ""
            m = _re.search(r"curl\s+['\"](https?://[^'\"]+)['\"]", txt)
            if not m:
                m = _re.search(r"(https?://[^\s'\"]+)", txt)
            if not m:
                self.toast("未识别到 URL", False); return
            url = m.group(1)
            path = url.split("?")[0]
            self.dbg_path.value = path
            for k, v in ENDPOINTS.items():
                if path.endswith(v.split("/")[-1]):
                    self.dbg_key.value = k
                    break
            self.toast(f"已识别：{path}")
            self.page.update()

        dbg = card(ft.Column([h1("接口调试 / 端点校正", 15),
                              muted("登录店小秘后，用这里试真实接口并把正确路径保存下来"),
                              ft.Row([self.dbg_key, self.dbg_method], spacing=10),
                              self.dbg_path,
                              self.dbg_payload,
                              self.curl_text,
                              ft.Row([ft.ElevatedButton("解析 cURL", icon=ft.Icons.CONTENT_PASTE, on_click=parse_curl),
                                      ft.ElevatedButton("发送", icon=ft.Icons.SEND, bgcolor=ACCENT, color="white", on_click=send),
                                      ft.ElevatedButton("保存端点", icon=ft.Icons.SAVE, on_click=save_override)], spacing=10),
                              self.dbg_out], spacing=10))
        return [view[0], recharge_card, upd, inst_card, dbg] + view[1:]

    def _debug_call(self):
        import json as _json
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            key = self.dbg_key.value
            if self.dbg_method.value == "POST":
                try:
                    payload = _json.loads(self.dbg_payload.value or "{}")
                except Exception:
                    payload = {}
                resp = client._post(key, payload)
            else:
                resp = client._get(key)
            self.dbg_out.value = _json.dumps(resp, ensure_ascii=False)[:2000]
            self._log(f"调试 {key} 成功")
        except Exception as ex:
            self.dbg_out.value = f"失败：{ex}"
            self._log(f"调试失败：{ex}")
        self.page.update()

    def _refresh_instances(self):
        try:
            items = list_instances()
        except Exception:
            items = []
        self.inst_list.controls = [ft.Text(f"{i['name']}  ·  {i['path']}", size=11, color=MUTED) for i in items] or \
                                  [ft.Text("暂无实例", size=11, color=MUTED)]

    def _check_update(self):
        r = updater.check(self.cfg.license_server)
        if not r.get("ok"):
            self.toast(f"检查失败：{r.get('error')}", False); return
        msg = f"发现新版本 {r['latest']}" if r.get("has_update") else "已是最新版本"
        self._log(msg)
        self.toast(msg)

    # ---------- 行为 ----------
    def _check_license(self):
        res = verify_license(self.cfg.license_server)
        self.license_text.value = res["message"]
        self.license_text.color = ACCENT_2 if res["ok"] else "#FF6B6B"
        try:
            self.page.update()
        except Exception:
            pass

    def _test(self):
        try:
            ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout).models()
            self.stat_api.value = "正常"; self.stat_api.color = ACCENT_2
            self.toast("连接成功")
        except Exception as e:
            self.stat_api.value = "连接失败"; self.stat_api.color = "#FF6B6B"
            self.toast(str(e), False)
        self.page.update()

    def _do_image(self, kind):
        svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout))
        self.img_results.controls = []
        runner = BatchRunner(self.cfg.concurrency)

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), self.cfg.edit_prompt or "clean product photo")

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            self.img_results.controls.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                                     ft.Text(res.value if res.ok else res.error, size=11,
                                                             color=MUTED, selectable=True)], spacing=8))
            self.page.update()

        results = runner.run(list(self.picked), job, done)
        ok = sum(1 for r in results if r.ok)
        self.img_status.value = f"完成：成功 {ok} / {len(results)}"
        self.img_status.color = ACCENT_2 if ok == len(results) else "#FFB020"
        self.img_busy.visible = False
        self.page.update()

    def _on_files(self, e):
        if e.files:
            self.picked = [Path(f.path) for f in e.files]
            self.img_status.value = f"已选择 {len(self.picked)} 张图"
            self.page.update()

    def _maybe_quota(self, msg: str) -> None:
        if any(k in str(msg) for k in ("额度", "余额", "quota", "insufficient", "欠费")):
            try:
                self.page.launch_url("https://api.moneyfly.club")
            except Exception:
                pass
            self.toast("额度不足，已打开充值页面", False)

    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self.page.update()


def run():
    ft.app(target=lambda page: App(page), assets_dir="assets")
