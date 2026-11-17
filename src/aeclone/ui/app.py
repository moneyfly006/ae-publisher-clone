"""AuroraList · 现代化界面（schema 驱动，覆盖老软件全部设置项）。"""
from __future__ import annotations

import datetime
import sys
import tempfile
import os
import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient
from ..config import AppConfig, config_dir, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService
from ..services.pricing import calc_multiplier, calc_free_shipping
from ..services.variants import auto_map, parse_mapping_json
from ..integrations.cdp import launch_edge, launch_browser, open_url_via_cdp, BrowserError
from ..integrations.bitbrowser import BitBrowser
from ..integrations.zhanfu import ZhanfuBrowser
from ..services.publisher import Publisher, demo_product
from ..integrations.dianxiaomi import DianxiaomiClient, ENDPOINTS
from ..services.video import build_slideshow, generate_carousel_video, probe as video_probe
from ..core import updater
from ..core.instances import list_instances, create_instance, launch_instance
from ..services import history
from ..services.queue import TaskQueue
from ..services.scheduler import Scheduler
from ..services.text_pipeline import TextPipeline
from ..services.prompt_presets import TITLE_PRESETS, score_title
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
        ("运行", [Field("auto_save_products", "自动保存到店小秘（关闭=只试跑）", "bool"),
                  Field("publish_action", "发布动作", "select", ["save_only", "publish"]),
                  Field("browser_hide", "浏览器后台运行（连接后自动最小化）", "bool"),
                  Field("eu_qualification", "是否提交欧盟资质", "select", ["否", "是"],
                        hint="选「是」必须先填下面的欧盟责任人资料，否则保存会被站点拦截"),
                  Field("cdp_port", "浏览器调试端口（多开时每个实例不同）")]),
        # 选「是」时站点要求填写欧盟责任人，否则只能选否（ISSUES #4）
        ("欧盟资质 · 责任人资料（选「是」时必填）", [
            Field("eu_responsible_name", "欧盟责任人名称"),
            Field("eu_responsible_address", "欧盟责任人地址"),
            Field("eu_responsible_email", "电子邮箱"),
            Field("eu_responsible_phone", "联系电话"),
        ]),
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
        ("主图", [
            Field("main_image_generate_enable", "AI 生成主图（文生图）", "bool"),
            Field("image_gen_model", "生图模型"),
            Field("main_image_generate_prompt", "生成提示词", "multiline"),
            Field("main_image_generate_count", "生成数量"),
            Field("main_image_generate_use_refs", "文生图用现有主图当素材（图生图）", "bool"),
            Field("white_bg_enable", "主图白底", "bool"),
            Field("white_background_pic_1", "白底起始序号"),
            Field("white_background_pic_2", "白底结束序号"),
            Field("watermark_enable", "主图去水印/去Logo", "bool"),
            Field("remove_watermark_pic_1", "去水印起始序号"),
            Field("remove_watermark_pic_2", "去水印结束序号"),
            Field("translate_main_pic_enable", "主图文字翻译", "bool"),
            Field("translate_main_pic_indices", "翻译序号"),
            Field("designated_edit_enable", "主图指定编辑", "bool"),
            Field("designated_edit_indices", "编辑序号"),
            Field("designated_edit_prompt", "编辑提示词", "multiline"),
        ]),
        ("描述图", [
            Field("white_background_detail_pic_indices", "描述图序号（白底/去水印/编辑共用）"),
            Field("img_detail_white_bg", "描述图白底", "bool"),
            Field("img_detail_remove_wm", "描述图去水印", "bool"),
            Field("img_detail_edit", "描述图精细编辑", "bool"),
            Field("translate_detail_pic_enable", "描述图文字翻译", "bool"),
            Field("translate_detail_pic_indices", "翻译序号"),
            Field("detail_review_first_count", "审查前 N 张"),
            Field("detail_review_last_count", "审查后 N 张"),
        ]),
        ("SKU 图", [
            Field("white_background_sku_pic", "处理 SKU 图", "bool"),
            Field("img_sku_white_bg", "SKU 图白底", "bool"),
            Field("img_sku_remove_wm", "SKU 图去水印", "bool"),
            Field("img_sku_edit", "SKU 图精细编辑", "bool"),
            Field("translate_sku_pic", "SKU 图文字翻译", "bool"),
        ]),
        ("营销与批发", [
            Field("marketing_image_enable", "生成营销场景图", "bool"),
            Field("marketing_image_count", "营销图数量"),
            Field("marketing_image_prompt", "营销图提示词", "multiline"),
            Field("wholesale_enable", "启用批发价", "bool"),
            Field("wholesale_tiers", "批发阶梯 JSON（如 [{\"min_qty\":10,\"price\":9.9}]）", "multiline"),
        ]),
        ("通用", [
            Field("four_in_one", "四合一拼图", "bool"),
            Field("four_in_one_position", "四合一位置", "select", ["append", "first"]),
            Field("pad_main_images_from_extra", "主图不足用描述图补", "bool"),
            Field("main_pic_count", "主图目标数量"),
            Field("overlay_watermark_enable", "叠加水印", "bool"),
            Field("overlay_position", "水印位置"),
            Field("overlay_opacity", "水印透明度"),
            Field("background_gen_rules", "背景生成规则 JSON", "multiline"),
        ]),
        ("主图视频", [
            Field("generate_main_video", "发布时生成主图视频", "bool"),
            Field("video_slot_seconds", "每张停留秒数"),
            Field("video_transition_seconds", "转场秒数"),
            Field("video_size", "视频尺寸（如 1080x1080）"),
            Field("video_cover", "铺满裁剪（否则留白）", "bool"),
            Field("video_fade_edges", "首尾淡入淡出", "bool"),
            Field("video_output_dir", "视频输出目录（留空用默认）"),
        ]),
        ("精细编辑提示词", [
            Field("edit_enable", "启用通用编辑", "bool"),
            Field("edit_prompt", "通用编辑提示词", "multiline"),
            Field("translate_prompt", "翻译提示词", "multiline"),
            Field("background_gen_sku_prompt", "SKU 背景提示词", "multiline"),
        ]),
    ]
def schema_price():
    return [
        ("模式", [Field("price_mode", "价格模式", "select", ["multiplier", "free_shipping", "commission"]),
                  Field("price_multiplier", "价格倍率（自定义，如 1.35）"),
                  Field("price_fixed_add", "固定加价（元）"),
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


def schema_prompts():
    return [
        ("文字类", [
            Field("title_prompt", "标题提示词", "multiline",
                  hint="要求 ≤125 字符；可写「品牌词加 for」等规则；模型会按此改写标题"),
            Field("detail_prompt", "描述提示词", "multiline",
                  hint="要求输出 HTML 结构；建议规定小标题、参数表、包装清单"),
            Field("sku_prompt", "SKU 提示词", "multiline",
                  hint="要求 ≤20 字符、纯英文、统一格式（如 3T-20M）"),
            Field("weight_prompt", "估重提示词", "multiline",
                  hint="建议要求输出 JSON：weight_kg / length_cm / width_cm / height_cm"),
        ]),
        ("图片类", [
            Field("main_image_generate_prompt", "主图生成提示词", "multiline",
                  hint="支持变量 {title}；建议写明白底、居中占比、光影、禁止文字水印"),
            Field("designated_edit_prompt", "指定编辑提示词", "multiline",
                  hint="用于精修/去水印/去促销文字，强调商品本体不变"),
            Field("translate_prompt", "图片翻译提示词", "multiline",
                  hint="强调保留排版、不新增文字、无文字则原样返回"),
            Field("background_gen_sku_prompt", "换背景提示词", "multiline",
                  hint="写明背景类型（纯白/浅灰/场景）与接触阴影要求"),
            Field("edit_prompt", "通用编辑提示词", "multiline"),
        ]),
        ("审查类", [
            Field("review_text_prompt", "文本审查提示词", "multiline",
                  hint="建议要求只输出 PASS 或 FAIL|原因，便于自动拦截"),
            Field("review_main_img_prompt", "主图审查提示词", "multiline"),
            Field("review_detail_img_prompt", "详情图审查提示词", "multiline"),
        ]),
    ]


def schema_settings(cfg):
    return [
        ("API 配置", [Field("api_key", "API Key"),
                      Field("concurrency", "并发数"), Field("timeout", "超时(秒)")]),
        ("上传", [Field("upload_full_cid", "相册分类 ID"), Field("upload_category_name", "相册分类名")]),
        ("视频", [Field("generate_main_video", "生成主图视频", "bool"),
                  Field("use_template_video", "使用模板视频", "bool"),
                  Field("overwrite_existing_video", "覆盖已有视频", "bool")]),
    ]


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg: AppConfig = get_config()
        self.picked: list[Path] = []
        self.wb_sel_paths: set[str] = set()
        self._subscribe_progress()
        self.wb_wall = None
        self.logs: list[str] = []
        self.products: list[dict] = []
        self.ws_endpoint = ""
        self.field_refs: list[Field] = []
        self.file_picker = ft.FilePicker(on_result=self._on_files)
        self.wm_picker = ft.FilePicker(on_result=self._on_watermark_file)
        self.api_state: dict = {}
        self.license_state: dict = {}
        self.cookie_src = None
        self._cookie_ts = 0.0

        page.title = "AuroraList · 极光上架"
        page.bgcolor = BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        # 小屏 / 截图验证时可固定窗口尺寸：AURORA_WINDOW=1514x945
        try:
            win = (os.environ.get("AURORA_WINDOW") or "").strip().lower()
            if "x" in win:
                w, h = win.split("x", 1)
                page.window_width, page.window_height = int(w), int(h)
                page.window_min_width, page.window_min_height = 1024, 640
        except Exception:
            pass
        page.overlay.append(self.file_picker)
        page.overlay.append(self.wm_picker)
        page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)
        self._build_shell()
        self._switch(self._start_page_index())
        page.update()
        # 启动即自动检测一次 API / 图片空间（ISSUES #9）
        self._probe_api_async()

    # ---------- 启动页（ISSUES #10：计划任务下环境变量可能不生效）----------
    def _start_page_index(self) -> int:
        """--page N > 页面文件 > AURORA_PAGE 环境变量 > 配置 start_page。"""
        argv = list(getattr(sys, "argv", []) or [])
        for i, a in enumerate(argv):
            if a == "--page" and i + 1 < len(argv):
                try:
                    return int(argv[i + 1])
                except ValueError:
                    pass
            if a.startswith("--page="):
                try:
                    return int(a.split("=", 1)[1])
                except ValueError:
                    pass
        candidates = [os.environ.get("AURORA_PAGE_FILE", "").strip()]
        try:
            candidates.append(str(config_dir() / "page.txt"))
        except Exception:
            pass
        for p in candidates:
            if not p:
                continue
            try:
                txt = Path(p).read_text(encoding="utf-8").strip()
                if txt:
                    return int(txt)
            except Exception:
                continue
        try:
            env = os.environ.get("AURORA_PAGE")
            if env is not None and str(env).strip() != "":
                return int(env)
        except ValueError:
            pass
        try:
            return int(getattr(self.cfg, "start_page", 0) or 0)
        except Exception:
            return 0

    # ---------- 壳 ----------
    def _build_shell(self):
        self.nav_buttons = []
        groups = [
            ("", [("概览", ft.Icons.DASHBOARD_OUTLINED, 0)]),
            ("店铺与上架", [("店铺设置", ft.Icons.STORE_OUTLINED, 1), ("高级设置", ft.Icons.TUNE, 2)]),
            ("AI 设置", [("AI 智能填写", ft.Icons.AUTO_FIX_HIGH, 3), ("内容审查", ft.Icons.FACT_CHECK_OUTLINED, 4)]),
            ("AI图片处理", [("AI图片处理", ft.Icons.IMAGE_OUTLINED, 5), ("图片工作台", ft.Icons.GRID_VIEW, 9)]),
            ("价格与变种", [("价格设置", ft.Icons.SELL_OUTLINED, 6), ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED, 7)]),
            ("系统", [("预览", ft.Icons.VISIBILITY, 12), ("设置", ft.Icons.SETTINGS_OUTLINED, 8), ("提示词", ft.Icons.EDIT_NOTE, 11),
                      ("任务与日志", ft.Icons.RECEIPT_LONG_OUTLINED, 10)]),
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
        # 侧栏导航可滚动：1514×945 及更小的窗口也能点到「提示词 / 任务与日志 / 预览」（ISSUES #7）
        nav_scroll = ft.Column([col], spacing=0, expand=True, scroll=ft.ScrollMode.AUTO)
        sidebar = ft.Container(width=216, bgcolor=CARD, padding=16, expand=True, content=ft.Column([
            ft.Row([ft.Container(width=32, height=32, border_radius=10, bgcolor=ACCENT,
                                 content=ft.Text("A", size=15, weight=ft.FontWeight.BOLD, color="#0B0D12")),
                    ft.Column([ft.Text("AuroraList", weight=ft.FontWeight.BOLD, color=TEXT, size=14),
                               muted("极光上架")], spacing=0)], spacing=10),
            ft.Container(height=12),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.ROCKET_LAUNCH, size=18, color="#0B0D12"),
                                ft.Text("启动上架", size=14, weight=ft.FontWeight.BOLD, color="#0B0D12")],
                               spacing=8, alignment=ft.MainAxisAlignment.CENTER),
                bgcolor=ACCENT, border_radius=12, padding=ft.padding.symmetric(vertical=12, horizontal=10),
                on_click=lambda e: self._start(), ink=True),
            ft.Container(height=10),
            nav_scroll,
            ft.Container(height=6),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.TERMINAL, size=16, color=MUTED),
                                ft.Text("日志监控", color=MUTED, size=13)], spacing=10),
                padding=ft.padding.symmetric(horizontal=12, vertical=8), border_radius=10,
                on_click=lambda e: self._open_log_panel(), ink=True),
            ft.Row([ft.Icon(ft.Icons.VERIFIED_USER, size=14, color=ACCENT_2), self.license_text], spacing=6),
        ], spacing=0, expand=True), tooltip="菜单可滚动")
        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=14)
        self.page.add(ft.Row([sidebar, ft.Container(expand=True, content=self.content,
                                                    padding=ft.padding.only(left=24, right=24, top=18, bottom=18))],
                             expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        threading.Thread(target=self._check_license, daemon=True).start()
        try:
            self.scheduler = Scheduler()
            self.scheduler.start(self._run_scheduled)
        except Exception as ex:
            self._log(f"调度器启动失败：{ex}", "WARN")
        try:
            from ..integrations.browser_cookies import describe, resolve
            src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None))
            self.cookie_src = src
            self._log(f"Cookie 源：{describe(src)}" + ("" if src.ok else f"（{src.error[:60]}）"),
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
                 self._p_image, self._p_price, self._p_variant, self._p_settings, self._p_workbench,
                 self._p_logs, self._p_prompts, self._p_preview]
        idx = idx % len(pages)
        self._page_idx = idx
        self.content.controls = pages[idx]()
        self.page.update()
        if idx == 0:
            self._probe_api_async()      # 概览页用缓存或重测，展示真实状态（ISSUES #9）

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
        out = [h1(title)]
        self._autosave_status = ft.Text("修改即自动保存", size=11, color=MUTED)
        for name, fields in schemas:
            c, refs = section(name, fields, self.cfg)
            self.field_refs.extend(refs)
            for f in refs:
                self._bind_autosave(f)
            out.append(c)
        if extra:
            out.append(extra)
        out.append(ft.Row([self._autosave_status,
                           ft.OutlinedButton("恢复默认", icon=ft.Icons.RESTART_ALT,
                                             on_click=lambda e: self._reset())], spacing=12))
        return out

    def _bind_autosave(self, f):
        if f.ctrl is None:
            return
        def handler(e, _f=f):
            try:
                self._autosave(_f)
            except Exception:
                pass
        try:
            f.ctrl.on_change = handler
        except Exception:
            pass

    def _autosave(self, f):
        """任意设置改动后 0.6 秒自动落盘（防抖，避免每键一写）。"""
        try:
            k, v = f.read()
            self.cfg.set(k, v)
        except Exception:
            return
        timer = getattr(self, "_autosave_timer", None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        def _flush():
            try:
                save_config(self.cfg)
                if getattr(self, "_autosave_status", None) is not None:
                    self._autosave_status.value = f"已自动保存 {time.strftime('%H:%M:%S')}"
                    self.page.update()
            except Exception:
                pass
        t = threading.Timer(0.6, _flush)
        t.daemon = True
        self._autosave_timer = t
        t.start()

    # ---------- 欧盟资质（ISSUES #4）----------
    EU_KEYS = ("eu_responsible_name", "eu_responsible_address",
               "eu_responsible_email", "eu_responsible_phone")

    def _eu_missing(self) -> list[str]:
        labels = {"eu_responsible_name": "名称", "eu_responsible_address": "地址",
                  "eu_responsible_email": "邮箱", "eu_responsible_phone": "电话"}
        return [labels[k] for k in self.EU_KEYS if not str(getattr(self.cfg, k, "") or "").strip()]

    def _bind_eu_guard(self):
        """选「是」时先要欧盟责任人资料，否则保存必被站点拦截。"""
        for f in self.field_refs:
            if f.key != "eu_qualification" or f.ctrl is None:
                continue
            prev = getattr(f.ctrl, "on_change", None)

            def handler(e, _prev=prev):
                if _prev:
                    try:
                        _prev(e)
                    except Exception:
                        pass
                try:
                    if str(e.control.value) == "是" and self._eu_missing():
                        self._ensure_eu_responsible()
                except Exception:
                    pass

            f.ctrl.on_change = handler

    def _ensure_eu_responsible(self):
        specs = (("eu_responsible_name", "欧盟责任人名称（公司/个人）", "例如：EU Responsible GmbH"),
                 ("eu_responsible_address", "欧盟责任人地址", "国家/城市/街道/邮编"),
                 ("eu_responsible_email", "电子邮箱", "name@example.com"),
                 ("eu_responsible_phone", "联系电话", "+49 ..."))
        fields = {}
        for key, label, hint in specs:
            fields[key] = ft.TextField(label=label, value=str(getattr(self.cfg, key, "") or ""),
                                       hint_text=hint, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        status = ft.Text("选「是」后站点要求填写欧盟责任人，否则保存会被拦截", size=11, color="#FFB020")

        def save(e):
            missing = [f.label for f in fields.values() if not str(f.value or "").strip()]
            if missing:
                status.value = "还缺：" + "、".join(missing)
                status.color = "#FF6B6B"
                self.page.update()
                return
            for k, f in fields.items():
                self.cfg.set(k, str(f.value or "").strip())
            self.cfg.set("eu_qualification", "是")
            save_config(self.cfg)
            dlg.open = False
            self.toast("欧盟责任人资料已保存（欧盟资质=是）")
            self._switch(2)

        def to_no(e):
            self.cfg.set("eu_qualification", "否")
            save_config(self.cfg)
            dlg.open = False
            self.toast("已改为「否」，不提交欧盟资质")
            self._switch(2)

        dlg = ft.AlertDialog(
            title=ft.Text("提交欧盟资质 → 需要欧盟责任人资料"),
            content=ft.Column([muted("填一次长期复用；也可以在「高级设置 → 欧盟资质」里随时修改。"),
                               *fields.values(), status], spacing=10, width=520),
            actions=[ft.TextButton("改为「否」", on_click=to_no),
                     ft.ElevatedButton("保存并提交", bgcolor=ACCENT, color="white", on_click=save)])
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()

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
        # 恢复默认前先备份，避免误点丢配置（API Key / 店铺 / 模板）
        try:
            import shutil as _sh, time as _t
            src = AppConfig.path()
            if src.exists():
                _sh.copy2(src, src.with_name(f"config.backup-{_t.strftime('%Y%m%d-%H%M%S')}.json"))
        except Exception:
            pass
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
        self.stat_api = ft.Text("检测中…", color=MUTED, size=12)
        self.stat_auth = ft.Text("检测中…", color=MUTED, size=12)
        self.stat_auth_detail = muted("", 10)
        self._current = 0

        self.ov_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        self.ov_progress_text = ft.Text("空闲", size=12, color=MUTED)
        self.ov_recent = ft.Column(spacing=2)
        prog_card = card(ft.Column([
            h1("实时进度", 15),
            self.ov_progress_text, self.ov_progress, self.ov_recent], spacing=8))

        def stat(label, ctrl, icon, color, extra=None):
            col = [ft.Row([ft.Icon(icon, size=17, color=color), muted(label)], spacing=8), ctrl]
            if extra is not None:
                col.append(extra)
            return card(ft.Column(col, spacing=8), padding=16, expand=True)

        st = {"runs": 0, "ok": 0, "failed": 0}
        try:
            st = history.stats(days=1)
        except Exception:
            pass

        def recheck_auth(e):
            self.stat_auth.value = "检测中…"
            self.stat_auth.color = MUTED
            try:
                self.page.update()
            except Exception:
                pass
            threading.Thread(target=self._check_license, daemon=True).start()

        auth_card = stat("授权状态", self.stat_auth, ft.Icons.VERIFIED_OUTLINED, ACCENT_2,
                         extra=ft.Row([self.stat_auth_detail,
                                       ft.TextButton("重新检测", on_click=recheck_auth)], spacing=4))
        self._apply_license_state()
        self._apply_api_state()
        return [prog_card, h1("概览"), muted("AI 能力接入自建站点，全部调用按站点价格计费"),
                card(ft.Row([ft.Text(f"今日任务 {st['runs']}", color=TEXT, size=13),
                             ft.Text(f"成功 {st['ok']}", color=ACCENT_2, size=13),
                             ft.Text(f"失败 {st['failed']}", color="#FF8A8A", size=13)], spacing=24)),
                ft.Row([stat("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT),
                        auth_card,
                        stat("图片服务", ft.Text("~15 秒/张", color=ACCENT_2, size=12), ft.Icons.BOLT_OUTLINED, "#FFB020")],
                       spacing=12),
                card(ft.Column([h1("快速开始", 15),
                                muted("三步完成上架：读 Cookie → 选店铺与模板 → 启动"),
                                ft.Row([ft.ElevatedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                                          on_click=lambda e: threading.Thread(target=self._test, daemon=True).start()),
                                        ft.ElevatedButton("启动", icon=ft.Icons.ROCKET_LAUNCH, bgcolor=ACCENT, color="white",
                                                          on_click=lambda e: self._start()),
                                        ft.ElevatedButton("一键自检", icon=ft.Icons.PLAY_CIRCLE_FILL,
                                                          bgcolor=ACCENT_2, color="#04231C",
                                                          on_click=lambda e: threading.Thread(target=self._self_test, daemon=True).start()),
                                        ft.OutlinedButton("去设置", icon=ft.Icons.SETTINGS,
                                                          on_click=lambda e: self._switch(8))], spacing=10)], spacing=10))]

    # ---------- 概览的状态刷新（ISSUES #8 / #9）----------
    def _apply_api_state(self):
        st = getattr(self, "api_state", None) or {}
        ctrl = getattr(self, "stat_api", None)
        if ctrl is None:
            return
        if not st:
            return
        try:
            if st.get("ok"):
                ctrl.value = f"正常 · {st.get('detail', '')} · {st.get('lat', '-')}s"
                ctrl.color = ACCENT_2
            else:
                ctrl.value = f"连接失败：{st.get('detail', '')}"
                ctrl.color = "#FF6B6B"
            self.page.update()
        except Exception:
            pass

    def _probe_api_async(self, force: bool = False):
        st = getattr(self, "api_state", None) or {}
        if not force and st and (time.time() - float(st.get("ts") or 0)) < 300:
            self._apply_api_state()
            return
        if getattr(self, "_api_probing", False):
            return
        self._api_probing = True

        def run():
            t0 = time.time()
            try:
                models = ApiClient(self.cfg.api_base, self.cfg.api_key, min(float(self.cfg.timeout or 60), 30)).models()
                self.api_state = {"ok": True, "detail": f"{len(models)} 个模型",
                                  "lat": round(time.time() - t0, 1), "ts": time.time()}
            except Exception as ex:  # noqa: BLE001
                self.api_state = {"ok": False, "detail": str(ex)[:70],
                                  "lat": round(time.time() - t0, 1), "ts": time.time()}
            finally:
                self._api_probing = False
            self._apply_api_state()

        threading.Thread(target=run, daemon=True).start()

    def _apply_license_state(self):
        st = getattr(self, "license_state", None) or {}
        ctrl = getattr(self, "stat_auth", None)
        detail = getattr(self, "stat_auth_detail", None)
        if ctrl is None or not st:
            return
        try:
            if st.get("ok"):
                exp = str(st.get("expiry") or "")
                ctrl.value = "已授权"
                ctrl.color = ACCENT_2
                if detail is not None:
                    detail.value = f"到期：{exp}" if exp else "永久有效"
            else:
                ctrl.value = "未授权"
                ctrl.color = "#FF6B6B"
                if detail is not None:
                    detail.value = str(st.get("message") or "点「重新检测」或检查网络")[:40]
            self.page.update()
        except Exception:
            pass

    def _validate(self) -> list[str]:
        cfg = self.cfg
        errs = []
        if not (cfg.api_key or "").strip():
            errs.append("未填写 API Key")
        if not (cfg.store_id or cfg.store_name):
            errs.append("未选择店铺（点「同步店铺/模板」）")
        if not any((cfg.product_template_id, cfg.product_template_name,
                    getattr(cfg, "template_product_id", ""), getattr(cfg, "template_product_title", ""),
                    (getattr(cfg, "template_snapshot", "") or "").strip() not in ("", "{}"))):
            errs.append("未选择产品模板（可在「店铺设置 → 选择模板」选一个）")
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
        # 定时发布：时间必须能解析且在未来（否则保存后永远不会发布）
        try:
            if getattr(cfg, "schedule_publish", False):
                from ..services.publisher import parse_run_at
                when = str(getattr(cfg, "schedule_time", "") or "").strip()
                if not when:
                    errs.append("已开启定时发布，但没填发布时间（例：2026-11-12 09:00）")
                else:
                    run_at = parse_run_at(when)
                    if run_at <= time.time():
                        errs.append(f"定时发布时间已过：{when}，请改成将来的时间")
        except Exception as ex:
            errs.append(f"定时发布时间格式不对：{str(ex)[:60]}")
        # 欧盟资质选「是」时必须有责任人资料，否则保存会被站点拦截（ISSUES #4）
        try:
            if str(getattr(cfg, "eu_qualification", "否")) == "是":
                miss = self._eu_missing()
                if miss:
                    errs.append("已选「提交欧盟资质」，还缺欧盟责任人：" + "、".join(miss)
                                + "（高级设置 → 欧盟资质）")
        except Exception:
            pass
        return errs

    def _start(self):
        # 启动前先把当前配置全部落盘（含未失焦的输入）
        try:
            for f in self.field_refs:
                try:
                    k, v = f.read()
                    self.cfg.set(k, v)
                except Exception:
                    continue
            save_config(self.cfg)
        except Exception:
            pass
        # 立刻跳到「任务与日志」，让用户看到实时进度
        try:
            self._switch(10)
        except Exception:
            pass
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
        """到点执行「定时发布」：商品在保存阶段已经写入店小秘，这里只做发布。"""
        self._log(f"定时发布触发：{job.product_id}（{job.title[:30]}）", "INFO")
        try:
            from ..integrations.editor_automation import publish_from_list
            ws = self.ws_endpoint
            if not ws:
                from ..integrations.browser import ensure_browser
                ws = ensure_browser(self.cfg).get("ws", "")
                self.ws_endpoint = ws
            if not ws:
                raise RuntimeError("没有可用的浏览器调试端点")
            r = publish_from_list(ws, str(job.product_id), confirm=True, dry_run=False,
                                  title_hint=job.title or "")
            self._log(f"定时发布结果：{r.message}", "INFO" if r.ok else "ERROR")
            if not r.ok:
                raise RuntimeError(r.message)
            try:
                history.record("scheduled_publish", 1, 0, str(job.product_id))
            except Exception:
                pass
        except Exception as ex:
            self._log(f"定时发布失败：{ex}", "ERROR")
            try:
                history.record("scheduled_publish", 0, 1, str(job.product_id))
            except Exception:
                pass
            raise

    def _publish_scheduled_now(self, job):
        """把「定时发布队列」里的任务立即执行（手动提前发布）。"""
        self._log(f"手动提前发布：{job.product_id}")
        try:
            self._run_scheduled(job)
            self.toast("定时任务已提前发布")
        except Exception as ex:
            self.toast(f"发布失败：{ex}", False)
        finally:
            try:
                self._switch(10)
            except Exception:
                pass

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
            pub = Publisher(self.cfg, dxm=dxm, ws_endpoint=self.ws_endpoint, scheduler=getattr(self, 'scheduler', None))
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
        step("标题生成", lambda: tp.make_title("3 吨迷你蜘蛛吊车 20 米臂长").value[:50])
        step("详情生成", lambda: tp.make_detail("3-Ton Mini Spider Crane").value[:40])
        step("SKU 优化", lambda: tp.make_skus(["手机壳适用于iPhone 15 Pro Max"]).value)
        step("文本审查", lambda: tp.review_text("厂家直销 电话13800000000 限时特惠").value[:30])
        img = "https://sc04.alicdn.com/kf/H36d2c1e2529d4ab18cab919a38f33e25D.jpg"
        step("读图估重", lambda: tp.estimate_weight(img, "吊车").value[:40])
        step("图片审查", lambda: ImagePipeline(cfg, c).review(img, "main")[:40])
        d = _P(_tf.mkdtemp()) / "a.jpg"
        d.write_bytes(_rq.get(img, timeout=60).content)
        svc = ImageService(c, self.cfg)
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

        self.ov_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        self.ov_progress_text = ft.Text("空闲", size=12, color=MUTED)
        self.ov_recent = ft.Column(spacing=2)
        prog_card = card(ft.Column([
            h1("实时进度", 15),
            self.ov_progress_text, self.ov_progress, self.ov_recent], spacing=8))

    def _p_store(self):
        self._current = 1
        ck_ok, ck_text = self._cookie_status_text()

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
        self.cookie_status = ft.Text(f"Cookie：{ck_text}",
                                     size=11, color=ACCENT_2 if ck_ok else "#FFB020",
                                     selectable=True)
        self.store_status = ft.Text("点击「读取并同步」获取店铺与模板", size=12, color=MUTED)

        def refresh_cookie(e):
            ok, text = self._cookie_status_text(refresh=True)
            self.cookie_status.value = f"Cookie：{text}"
            self.cookie_status.color = ACCENT_2 if ok else "#FFB020"
            try:
                self.page.update()
            except Exception:
                pass
            self.toast(("Cookie 可用：" if ok else "Cookie 仍不可用：") + text, ok)

        def sync(e):
            self.store_status.value = "正在读取…"; self.page.update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        connect_card = card(ft.Column([
            h1("1 · 连接店小秘", 15),
            muted("优先级：配置里注入过的 Cookie → 已打开的浏览器 → 本机 Chrome/Edge 浏览器库"),
            self.cookie_status,
            ft.Row([ft.ElevatedButton("读取并同步", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=sync),
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=refresh_cookie),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
                    ft.OutlinedButton("启动浏览器（兜底）", icon=ft.Icons.OPEN_IN_BROWSER,
                                      on_click=lambda e: threading.Thread(target=self._connect_browser, daemon=True).start()),
                    ft.OutlinedButton("从链接采集", icon=ft.Icons.LINK, on_click=lambda e: self._collect_dialog())],
                   spacing=10, wrap=True),
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
                    ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT, on_click=pick),
                    ft.ElevatedButton("保存模板到本地", icon=ft.Icons.SAVE, bgcolor=ACCENT_2, color="#04231C",
                                      on_click=lambda e: threading.Thread(target=self._save_tpl_local_quick, daemon=True).start()),
                    ft.OutlinedButton("模板库", icon=ft.Icons.LIBRARY_BOOKS, on_click=lambda e: self._template_library_dialog())], spacing=10, wrap=True),
            self.tpl_preview_box,
            ft.Row([ft.ElevatedButton("读取模板参数", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=CARD_2, color=TEXT,
                                      on_click=lambda e: threading.Thread(target=self._read_template, daemon=True).start()),
                    self.tpl_snapshot_text], spacing=10),
        ], spacing=10))

        # 图片空间：满了会导致上传被跳过（ISSUES #5）→ 这里直接给清理入口
        self.space_text = ft.Text("图片空间：点「查看占用」统计", size=12, color=MUTED)
        space_card = card(ft.Column([
            h1("4 · 图片空间", 15),
            muted("空间满了会跳过上传（刊登仍可用外链），可在这里清理旧图腾空间"),
            self.space_text,
            ft.Row([ft.OutlinedButton("查看占用", icon=ft.Icons.PIE_CHART_OUTLINE,
                                      on_click=lambda e: threading.Thread(target=self._load_space_info, daemon=True).start()),
                    ft.OutlinedButton("清理旧图", icon=ft.Icons.DELETE_SWEEP,
                                      on_click=lambda e: self._open_cleanup_dialog()),
                    ft.OutlinedButton("打开相册页面", icon=ft.Icons.OPEN_IN_BROWSER,
                                      on_click=lambda e: self._open_album_page())], spacing=10, wrap=True),
        ], spacing=10))

        if not getattr(self, "_store_autosynced", False):
            self._store_autosynced = True
            threading.Thread(target=self._sync_shops, daemon=True).start()
        return [h1("店铺设置"), muted("三步：读 Cookie → 选店铺 → 选模板"), connect_card, shop_card, tpl_card, space_card]

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

    def _template_library_dialog(self):
        import json as _j
        try:
            lib = _j.loads(self.cfg.template_library or "{}")
        except Exception:
            lib = {}
        if not isinstance(lib, dict):
            lib = {}
        list_col = ft.Column(spacing=4, height=280, scroll=ft.ScrollMode.AUTO)

        def persist():
            self.cfg.template_library = _j.dumps(lib, ensure_ascii=False)
            save_config(self.cfg)
            render()

        def save_template_local(name, item):
            """把当前模板（含抓取到的表单值）保存到本地模板库，读取在后台线程完成。"""
            try:
                vals = {}
                try:
                    import json as _jv
                    vals = _jv.loads(getattr(self.cfg, "template_values", "") or "{}")
                except Exception:
                    vals = {}
                rec = dict(item)
                rec["name"] = name or self.cfg.template_product_title or "未命名模板"
                rec["values"] = vals
                lib = [x for x in lib if x.get("name") != rec["name"]]
                lib.append(rec)
                self.cfg.template_library = _j.dumps(lib, ensure_ascii=False)
                save_config(self.cfg)
                render()
                self.toast(f"模板已保存到本地：{rec['name']}（{len(vals)} 个字段）")
            except Exception as ex:
                self.toast(f"保存失败：{ex}", False)

        def apply_item(name, item):
            self.cfg.template_snapshot = _j.dumps({"fields": item.get("fields") or {},
                                                   "attributes": item.get("attributes") or []},
                                                  ensure_ascii=False)
            # 抓取模板产品的完整表单值（品牌/型号/说明书/欧盟资质等），刊登时照抄
            try:
                from ..integrations.editor_automation import read_form_values
                from ..integrations.browser import ensure_browser
                ws = self.ws_endpoint or ensure_browser(self.cfg).get("ws", "")
                self.ws_endpoint = ws
                vals = read_form_values(ws, str(item.get("product_id") or self.cfg.template_product_id))
                if vals:
                    self.cfg.template_values = _j.dumps(vals, ensure_ascii=False)
                    self.toast(f"已抓取模板 {len(vals)} 个字段值")
            except Exception as ex:
                self._log(f"模板值抓取失败：{ex}", "WARN")
            self.cfg.template_product_title = name
            self.cfg.template_product_id = item.get("product_id") or ""
            save_config(self.cfg)
            self._set_tpl_preview(None)
            f = (item.get("fields") or {})
            self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                            f" · 属性 {len(item.get('attributes') or [])} 项")
            try:
                threading.Thread(target=save_template_local, args=(name, item), daemon=True).start()
            except Exception:
                pass
            self.toast(f"已应用模板：{name}")
            dlg.open = False
            self.page.update()

        def rename_item(name, item):
            field = ft.TextField(label="新名称", value=name, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def do(e):
                nn = (field.value or "").strip()
                if nn and nn != name:
                    lib[nn] = lib.pop(name, item)
                    persist()
                    self.toast(f"已重命名为 {nn}")
                d2.open = False
                self.page.update()

            d2 = ft.AlertDialog(title=ft.Text("重命名模板"), content=field,
                                actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(d2)),
                                         ft.ElevatedButton("确定", on_click=do)])
            self.page.dialog = d2; d2.open = True; self.page.update()

        def delete_item(name):
            lib.pop(name, None)
            persist()
            self.toast(f"已删除 {name}")

        def render():
            rows = []
            for name, item in lib.items():
                rows.append(ft.Row([
                    ft.Text(name[:28], size=12, color=TEXT, expand=True),
                    ft.TextButton("应用", on_click=lambda e, n=name, i=item: apply_item(n, i)),
                    ft.TextButton("重命名", on_click=lambda e, n=name, i=item: rename_item(n, i)),
                    ft.TextButton("删除", on_click=lambda e, n=name: delete_item(n)),
                ], spacing=6))
            list_col.controls = rows or [ft.Text("模板库为空，先点「保存当前模板」", size=12, color=MUTED)]
            self.page.update()

        def save_current(e):
            try:
                snap = _j.loads(self.cfg.template_snapshot or "{}")
            except Exception:
                snap = {}
            if not snap.get("fields"):
                self.toast("当前没有可保存的模板参数，请先读取模板", False); return
            name = (self.cfg.template_product_title or "未命名模板")[:40]
            lib[name] = {"fields": snap.get("fields"), "attributes": snap.get("attributes"),
                         "product_id": self.cfg.template_product_id}
            persist()
            self.toast(f"已保存模板：{name}")

        dlg = ft.AlertDialog(
            title=ft.Text("模板库"),
            content=ft.Column([ft.ElevatedButton("保存当前模板", icon=ft.Icons.SAVE, on_click=save_current),
                               ft.Divider(color=CARD_2), list_col], spacing=8, width=560),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg; dlg.open = True
        render()

    def _sku_replace_dialog(self):
        import json as _j
        try:
            mapping = _j.loads(self.cfg.sku_image_replace or "{}")
        except Exception:
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        rows = ft.Column(spacing=4, height=300, scroll=ft.ScrollMode.AUTO)
        state = {"map": dict(mapping)}

        def add(sku="", url=""):
            a = ft.TextField(value=sku, hint_text="SKU 名", width=180, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            b = ft.TextField(value=url, hint_text="新图片 URL", expand=True, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            rows.controls.append(ft.Row([a, b], spacing=8))
            self.page.update()
            return a, b

        pairs = [add(k, v) for k, v in mapping.items()]

        def save(e):
            out = {}
            for a, b in pairs:
                if (a.value or "").strip():
                    out[a.value.strip()] = (b.value or "").strip()
            self.cfg.sku_image_replace = _j.dumps(out, ensure_ascii=False)
            save_config(self.cfg)
            self.toast(f"已保存 {len(out)} 条 SKU 图替换")
            dlg.open = False
            self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("SKU 图批量替换"),
            content=ft.Column([ft.Text("SKU 名 → 新图片 URL，保存后发布时生效", size=11, color=MUTED),
                               rows,
                               ft.ElevatedButton("新增一行", icon=ft.Icons.ADD,
                                                 on_click=lambda e: add())], spacing=8, width=620),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("保存", bgcolor=ACCENT, color="white", on_click=save)],
        )
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
            self._cookie_cache = None      # 刚粘贴的 Cookie 立刻生效
            self._cookie_ts = 0.0
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
        pid = self.cfg.template_product_id or self.cfg.product_template_id
        if not pid:
            self.toast("请先选择模板产品", False); return
        self.tpl_snapshot_text.value = "正在读取模板参数…"
        self.page.update()
        try:
            from ..integrations.template_reader import read_snapshot
            if not self.ws_endpoint:
                try:
                    info = launch_browser("chrome")
                    self.ws_endpoint = info.get("ws", "")
                except Exception as be:
                    self.tpl_snapshot_text.value = f"读取失败：需要浏览器（{be}）"
                    self.page.update(); return
            snap = read_snapshot(self.ws_endpoint, str(pid))
            if not snap.fields:
                self.tpl_snapshot_text.value = "读取失败：浏览器未登录店小秘，请先点「启动浏览器（兜底）」登录一次"
                self.page.update(); return
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = snap.title
            self.cfg.template_snapshot = snap.to_json()
            save_config(self.cfg)
            f = snap.fields
            self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                            f" · 属性 {len(snap.attributes)} 项 · 重量 {f.get('weight','-')}")
            self._set_tpl_preview()      # 预览同步刷新（类目/运费/品牌/属性数/抓取字段数）
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
        self._cookie_cache = None          # 新浏览器会话，重新取 Cookie
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

    def _cookies(self, refresh: bool = False):
        """取 Cookie：手动配置 → 已开浏览器（CDP）→ 本机浏览器库（ISSUES #1）。"""
        from ..integrations.browser_cookies import resolve
        cache = getattr(self, "_cookie_cache", None)
        if (not refresh and cache and cache[0] == self.ws_endpoint
                and (time.time() - cache[1]) < 20):
            return dict(cache[2])
        src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None),
                      ws_endpoint=self.ws_endpoint)
        self.cookie_src = src
        self._cookie_ts = time.time()
        if src.ok and src.cookies:
            self._cookie_cache = (self.ws_endpoint, time.time(), dict(src.cookies))
            return dict(src.cookies)
        raise RuntimeError("未读取到店小秘 Cookie：请点「粘贴 Cookie」，或点「启动浏览器（兜底）」登录一次")

    def _cookie_status_text(self, refresh: bool = False) -> tuple[bool, str]:
        """给界面显示的 Cookie 状态（不抛错，也不阻塞太久）。"""
        from ..integrations.browser_cookies import describe, resolve
        cached = getattr(self, "cookie_src", None)
        if not refresh and cached is not None and (time.time() - getattr(self, "_cookie_ts", 0)) < 60:
            return bool(cached.ok and cached.cookies), describe(cached)
        try:
            src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None),
                          ws_endpoint=self.ws_endpoint)
        except Exception as ex:  # noqa: BLE001
            return False, f"读取异常：{str(ex)[:60]}"
        self.cookie_src = src
        self._cookie_ts = time.time()
        return bool(src.ok and src.cookies), describe(src)

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
            pub = Publisher(self.cfg, ws_endpoint=self.ws_endpoint, scheduler=getattr(self, 'scheduler', None))
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
        """模板预览：标题 + 类目/运费/品牌/属性数/抓取字段数（ISSUES #2）。"""
        import json as _j
        thumb = (images or [None])[0] if images else None
        title = self.cfg.template_product_title or "未选择模板"
        f: dict = {}
        attrs: list = []
        try:
            snap = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            if isinstance(snap, dict):
                f = snap.get("fields") or {}
                attrs = snap.get("attributes") or []
        except Exception:
            f, attrs = {}, []
        vals: dict = {}
        try:
            vals = _j.loads(getattr(self.cfg, "template_values", "") or "{}")
        except Exception:
            vals = {}
        if not isinstance(vals, dict):
            vals = {}

        def _pick(*keys):
            for k in keys:
                v = (f or {}).get(k)
                if v not in (None, "", "None", "NONE", "-"):
                    return str(v)
                item = vals.get(k)
                if isinstance(item, dict) and item.get("v"):
                    return str(item.get("v"))
                if isinstance(item, str) and item:
                    return item
            return ""

        brand = _pick("brand", "品牌")
        origin = _pick("origin", "产地")
        chips = [
            ("类目", _pick("category", "类目") or "-", "#6EC6FF"),
            ("运费", _pick("freight", "运费模板") or "-", "#6EC6FF"),
            ("品牌", brand or "-", ACCENT_2),
            ("产地", origin or "-", MUTED),
            ("属性", f"{len(attrs)} 项", "#FFB020"),
            ("抓取字段", f"{len(vals)} 个", ACCENT_2 if vals else MUTED),
        ]
        chip_row = ft.Row([
            ft.Container(
                content=ft.Row([ft.Text(f"{label} ", size=10, color=MUTED),
                                ft.Text(str(val)[:24], size=10, color=color, weight=ft.FontWeight.BOLD)],
                               spacing=2, tight=True),
                bgcolor=CARD_2, border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4))
            for label, val, color in chips], spacing=6, wrap=True)
        return ft.Column([
            ft.Row([
                ft.Container(width=44, height=44, border_radius=6, bgcolor=CARD_2,
                             clip_behavior=ft.ClipBehavior.HARD_EDGE,
                             content=ft.Image(src=thumb, fit=ft.ImageFit.COVER) if thumb else
                             ft.Icon(ft.Icons.IMAGE_OUTLINED, size=16, color=MUTED)),
                ft.Column([ft.Text(title, size=12, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                           muted(f"模板产品 ID：{self.cfg.template_product_id or '-'}", 10)], spacing=2),
            ], spacing=10),
            chip_row,
        ], spacing=8)

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

    def _search_attr_templates(self):
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            items = client.attribute_templates((getattr(self.cfg, 'shop_id', '') or self.cfg.store_id or None), (self.attr_kw.value or "").strip())
            rows = []
            for t in items[:40]:
                name = t.get("name") or t.get("templateName") or str(t.get("id", ""))
                tid = str(t.get("id", ""))
                attrs = t.get("attributes") or t.get("attrList") or t.get("data") or []
                pairs = []
                if isinstance(attrs, dict):
                    pairs = [f"{k}={v}" for k, v in attrs.items()]
                elif isinstance(attrs, list):
                    for a in attrs:
                        if isinstance(a, dict):
                            k = a.get("name") or a.get("attrName") or ""
                            v = a.get("value") or a.get("attrValue") or ""
                            if k:
                                pairs.append(f"{k}={v}")
                        elif isinstance(a, str) and "=" in a:
                            pairs.append(a)
                elif isinstance(attrs, str) and "=" in attrs:
                    pairs = [x for x in attrs.split("\n") if "=" in x]

                def apply(e, _tid=tid, _name=name, _pairs=pairs):
                    self.cfg.attribute_template_id = _tid
                    self.cfg.custom_attributes = _pairs
                    save_config(self.cfg)
                    self.attr_status.value = f"已应用 {_name}（{len(_pairs)} 条属性）"
                    self.toast(f"已应用属性模板：{_name}")

                rows.append(ft.ListTile(
                    title=ft.Text(name, size=13, color=TEXT),
                    subtitle=ft.Text(f"{tid} · {len(pairs)} 条属性", size=10, color=MUTED),
                    on_click=apply))
            self.attr_list.controls = rows or [ft.Text("没有属性模板（可先在店小秘创建）", size=12, color="#FFB020")]
            self.attr_status.value = f"共 {len(rows)} 个模板"
        except Exception as ex:
            self.attr_status.value = f"搜索失败：{ex}"
            self.attr_list.controls = []
        try:
            self.page.update()
        except Exception:
            pass

    def _clear_attrs(self):
        self.cfg.custom_attributes = []
        self.cfg.attribute_template_id = ""
        save_config(self.cfg)
        self.attr_status.value = "已清空"
        self.toast("已清空自定义属性")

    def _save_tpl_local_quick(self):
        """把当前使用的模板（快照 + 表单值）存到本地模板库；读取全部在后台。"""
        try:
            import json as _j
            name = self.cfg.template_product_title or "未命名模板"
            lib = []
            try:
                lib = _j.loads(self.cfg.template_library or "{}")
                if not isinstance(lib, list):
                    lib = []
            except Exception:
                lib = []
            rec = {"name": name, "product_id": self.cfg.template_product_id or "",
                   "fields": {}, "attributes": [], "values": {}}
            try:
                snap = _j.loads(self.cfg.template_snapshot or "{}")
                rec["fields"] = snap.get("fields") or {}
                rec["attributes"] = snap.get("attributes") or []
            except Exception:
                pass
            try:
                rec["values"] = _j.loads(self.cfg.template_values or "{}")
            except Exception:
                rec["values"] = {}
            lib = [x for x in lib if x.get("name") != name]
            lib.append(rec)
            self.cfg.template_library = _j.dumps(lib, ensure_ascii=False)
            save_config(self.cfg)
            self.toast(f"已保存到本地：{name}（{len(rec['values'])} 字段 / {len(rec['attributes'])} 属性）")
        except Exception as ex:
            self.toast(f"保存失败：{ex}", False)

    def _close_dialog(self):
        self.dlg.open = False
        self.page.update()

    # ---------- 图片空间 / 清理旧图（ISSUES #5）----------
    def _album_client(self):
        return DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})

    def _load_space_info(self):
        from ..services import album_cleanup
        ctrl = getattr(self, "space_text", None)
        try:
            client = self._album_client()
            info = album_cleanup.space(client)
            self._space_info = info
            if ctrl is not None:
                ctrl.value = "图片空间：" + info.brief
                ctrl.color = ACCENT_2 if info.images < 5000 else "#FFB020"
        except Exception as ex:  # noqa: BLE001
            if ctrl is not None:
                ctrl.value = f"图片空间：读取失败（{str(ex)[:60]}）"
                ctrl.color = "#FF6B6B"
        try:
            self.page.update()
        except Exception:
            pass

    def _open_album_page(self):
        url = "https://www.dianxiaomi.com/web/service/album"
        if self.ws_endpoint:
            try:
                open_url_via_cdp(self.ws_endpoint, url)
                self.toast("已在浏览器打开相册")
                return
            except Exception as ex:  # noqa: BLE001
                self._log(f"打开相册失败：{ex}", "WARN")
        try:
            self.page.launch_url(url)
        except Exception:
            self.toast(f"请在浏览器打开 {url}", False)

    def _open_cleanup_dialog(self):
        from ..services import album_cleanup
        days = ft.TextField(label="清理多少天前的图片", value="30", width=220,
                            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        mode = ft.RadioGroup(value="unreferenced", content=ft.Column([
            ft.Radio(value="unreferenced", label="安全清理：只删「未被任何商品引用」的旧图（推荐）"),
            ft.Radio(value="scan", label="扫描并删除：按时间列出旧图，确认后删除（锁定图片自动跳过）"),
        ], spacing=2))
        status = ft.Text("先填天数，再点「扫描」看会删多少张", size=12, color=MUTED)
        result = ft.Column(spacing=2, height=220, scroll=ft.ScrollMode.AUTO)
        state: dict = {"plan": None}

        def scan_now(show_dialog: bool = True):
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                status.value = "天数要填数字"
                status.color = "#FF6B6B"
                self.page.update()
                return None
            status.value = "扫描中…"
            status.color = MUTED
            self.page.update()
            try:
                plan = album_cleanup.scan(self._album_client(), d)
                state["plan"] = plan
                self._cleanup_plan = plan
                rows = [ft.Row([ft.Text(c.created[:19] or "-", size=10, color=MUTED, width=130),
                                ft.Text(album_cleanup.human_size(c.size), size=10, color=MUTED, width=70),
                                ft.Text("🔒" if c.locked else "", size=10, color="#FFB020", width=24),
                                ft.Text(c.name[:46] or c.id, size=10, color=TEXT, expand=True)], spacing=6)
                        for c in plan.candidates[:60]]
                result.controls = rows or [ft.Text("没有符合条件的旧图（或已被锁定）", size=11, color=MUTED)]
                status.value = plan.brief() + (f" · {plan.note}" if plan.note and plan.note != plan.brief() else "")
                status.color = ACCENT_2 if plan.delete_count else MUTED
            except Exception as ex:  # noqa: BLE001
                status.value = f"扫描失败：{str(ex)[:100]}"
                status.color = "#FF6B6B"
            self.page.update()
            return state["plan"]

        def do_clean(e):
            mode_val = mode.value or "unreferenced"
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                self.toast("天数要填数字", False)
                return
            plan = state["plan"]
            if mode_val == "scan" and plan is None:
                plan = scan_now()
                if plan is None:
                    return
            if mode_val == "scan" and plan.delete_count == 0:
                self.toast("没有可删除的旧图", False)
                return

            def really_clean(ev):
                confirm.open = False
                self.page.update()
                status.value = "清理中…"
                status.color = MUTED
                self.page.update()

                def go():
                    if mode_val == "unreferenced":
                        r = album_cleanup.auto_clean(self._album_client(), d)
                    else:
                        r = album_cleanup.delete_candidates(self._album_client(), state["plan"])
                    status.value = r.get("message") or (
                        f"已删除 {r.get('deleted', 0)} 张" + (f"，失败 {r.get('failed', 0)} 张" if r.get("failed") else ""))
                    status.color = ACCENT_2 if r.get("ok") else "#FF6B6B"
                    if r.get("ok"):
                        self._log("图片空间清理：" + status.value)
                    try:
                        self.page.update()
                    except Exception:
                        pass
                    self._load_space_info()

                threading.Thread(target=go, daemon=True).start()

            tip = (f"将删除 {d} 天前上传、且未被商品引用的图片（站点异步任务）。\n"
                   "被商品引用的图片不会删除，可放心执行。") if mode_val == "unreferenced" else \
                  (f"将立即删除扫描出的 {plan.delete_count} 张旧图（{album_cleanup.human_size(plan.delete_size)}）。\n"
                   "已锁定的图片站点会自动跳过。删除后不可恢复，请确认。")
            confirm = ft.AlertDialog(
                title=ft.Text("确认清理图片空间？"),
                content=ft.Text(tip, color=MUTED, size=12),
                actions=[ft.TextButton("取消", on_click=lambda ev: (setattr(confirm, "open", False), self.page.update())),
                         ft.ElevatedButton("确认清理", bgcolor="#D9534F", color="white", on_click=really_clean)])
            self.page.dialog = confirm
            confirm.open = True
            self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("清理旧图 · 图片空间"),
            content=ft.Column([
                muted("图片空间满了会导致上传被跳过；这里可安全释放空间。"),
                ft.Row([days, ft.ElevatedButton("扫描", icon=ft.Icons.SEARCH,
                                                on_click=lambda e: threading.Thread(
                                                    target=scan_now, daemon=True).start())], spacing=10),
                mode, status,
                ft.Divider(color=CARD_2),
                result,
            ], spacing=10, width=680, height=520),
            actions=[ft.TextButton("打开相册页面", on_click=lambda e: self._open_album_page()),
                     ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("开始清理", icon=ft.Icons.DELETE_SWEEP, bgcolor="#D9534F",
                                       color="white", on_click=do_clean)],
        )
        self.page.dialog = dlg
        dlg.open = True
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
        view = self._render("高级设置", deduped)
        self._bind_eu_guard()

        self.attr_kw = ft.TextField(label="属性模板关键词", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.attr_status = ft.Text("", size=12, color=MUTED)
        self.attr_list = ft.Column(spacing=4)

        def attr_search(e):
            self.attr_status.value = "搜索中…"
            self.page.update()
            threading.Thread(target=self._search_attr_templates, daemon=True).start()

        attr_card = card(ft.Column([
            h1("属性模板", 15),
            muted("从店小秘属性模板读取「属性名=值」，发布时自动填入编辑器"),
            ft.Row([self.attr_kw,
                    ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, bgcolor=CARD_2, color=TEXT, on_click=attr_search),
                    ft.OutlinedButton("清空已应用属性", on_click=lambda e: self._clear_attrs())], spacing=10),
            self.attr_status, self.attr_list], spacing=8))

        return [view[0], self._watermark_card(), self._combo_card(), attr_card] + view[1:]

    # ---------- 水印文件（可选文件 + 预览 + 状态）----------
    def _watermark_card(self):
        self.wm_path = ft.TextField(label="水印图片路径（透明 PNG 最佳）", width=520,
                                    value=str(self.cfg.watermark or ""),
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wm_status = ft.Text("", size=11, color=MUTED)
        self.wm_preview = ft.Image(width=120, height=90, fit=ft.ImageFit.CONTAIN, visible=False)

        def refresh_status():
            path = str(self.wm_path.value or "").strip()
            if not path:
                self.wm_status.value = "未设置：开启「叠加水印」前请先选一张水印图（也可填文字，如 @mybrand）"
                self.wm_status.color = MUTED
                self.wm_preview.visible = False
            elif Path(path).exists():
                try:
                    from PIL import Image as _Img
                    with _Img.open(path) as im:
                        self.wm_status.value = f"已找到：{Path(path).name} · {im.width}×{im.height}"
                        self.wm_status.color = ACCENT_2
                    self.wm_preview.src = path
                    self.wm_preview.visible = True
                except Exception:
                    self.wm_status.value = f"已找到文件：{Path(path).name}"
                    self.wm_status.color = ACCENT_2
                    self.wm_preview.visible = False
            else:
                self.wm_status.value = f"未找到该文件：{path}（也可以直接填文字做文字水印）"
                self.wm_status.color = "#FFB020"
                self.wm_preview.visible = False
            try:
                self.page.update()
            except Exception:
                pass

        def save_path(v):
            self.cfg.watermark = str(v or "").strip()
            save_config(self.cfg)

        def pick_file(e):
            try:
                self.wm_picker.pick_files(allow_multiple=False,
                                          allowed_extensions=["png", "jpg", "jpeg", "webp"])
            except Exception as ex:
                self.toast(f"选择文件失败：{ex}", False)

        def on_change(e):
            save_path(self.wm_path.value)
            refresh_status()

        self.wm_path.on_change = on_change

        def toggle_wm(e):
            self.cfg.set("overlay_watermark_enable", bool(e.control.value))
            save_config(self.cfg)
            self.toast("已开启叠加水印" if e.control.value else "已关闭叠加水印", bool(e.control.value))

        self.wm_enable = ft.Switch(label="叠加水印到主图（发布时逐张叠加）",
                                   value=bool(getattr(self.cfg, "overlay_watermark_enable", False)),
                                   active_color=ACCENT, on_change=toggle_wm)
        refresh_status()
        return card(ft.Column([
            h1("水印", 15),
            muted("「叠加水印到主图」用的就是这张图（发布时逐张叠加，可同时回传店小秘相册）"),
            self.wm_enable,
            ft.Row([self.wm_path,
                    ft.ElevatedButton("选择文件", icon=ft.Icons.IMAGE, bgcolor=CARD_2, color=TEXT, on_click=pick_file),
                    ft.OutlinedButton("测试叠加", icon=ft.Icons.SCIENCE,
                                      on_click=lambda e: threading.Thread(target=self._test_watermark, daemon=True).start())],
                   spacing=10, wrap=True),
            ft.Row([self.wm_preview, self.wm_status], spacing=10),
            muted("位置 / 透明度在「AI图片处理 → 通用」里设置（bottom-right、center…）"),
        ], spacing=8))

    def _on_watermark_file(self, e):
        try:
            if not e.files:
                return
            path = e.files[0].path
        except Exception:
            return
        self.cfg.watermark = path
        save_config(self.cfg)
        if hasattr(self, "wm_path"):
            self.wm_path.value = path
        try:
            self._p_advanced()
            self.toast(f"水印已设置：{Path(path).name}")
        except Exception:
            pass

    def _test_watermark(self):
        """用一张纯白测试图叠加水印，确认路径/位置/透明度真的生效。"""
        wm = str(self.cfg.watermark or "").strip()
        if not wm:
            self.toast("请先设置水印文件", False); return
        try:
            from PIL import Image as _Img
            from ..services.image_ops import add_watermark
            d = Path(tempfile.mkdtemp())
            base = d / "base.png"
            _Img.new("RGB", (800, 800), (235, 235, 235)).save(base)
            out = add_watermark(base, wm, d / "wm_out.png",
                                position=str(self.cfg.overlay_position or "bottom-right"),
                                opacity=float(self.cfg.overlay_opacity or 0.7))
            self.wm_preview.src = str(out)
            self.wm_preview.visible = True
            self.wm_status.value = f"测试成功：{out}"
            self.wm_status.color = ACCENT_2
            self._log(f"水印测试成功：{out}")
        except Exception as ex:
            self.wm_status.value = f"测试失败：{str(ex)[:100]}"
            self.wm_status.color = "#FF6B6B"
            self.toast(f"水印测试失败：{ex}", False)
        try:
            self.page.update()
        except Exception:
            pass

    # ---------- 套装 SKU 预览 ----------
    def _combo_card(self):
        from ..services.combo import preview as _preview
        self.combo_demo = ft.TextField(
            label="示例 SKU（逗号分隔，用于预览套装效果）", width=460,
            value="Red-XL, Blue-L, 3T-20M", bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.combo_preview = ft.Text("", size=11, color=MUTED, selectable=True)

        def render(e=None):
            try:
                factor = float(self.cfg.combo_multiplier or 2)
            except Exception:
                factor = 2.0
            names = [x.strip() for x in str(self.combo_demo.value or "").split(",") if x.strip()]
            demo = [{"sku": n, "price": 10.0} for n in names[:4]]
            rows = _preview(demo, str(self.cfg.combo_prefix or "套装"), factor,
                            float(self.cfg.goods_cost or 0) or None, limit=8)
            self.combo_preview.value = ("\n".join(rows) if rows else "（先填示例 SKU）") + \
                f"\n说明：套装价 = 原价 × {factor:g}；库存沿用原 SKU"
            try:
                self.page.update()
            except Exception:
                pass

        self.combo_demo.on_change = render
        render()
        return card(ft.Column([
            h1("套装 SKU", 15),
            muted("勾选「高级设置 → 套装 → 生成套装 SKU」后，每个主 SKU 会追加一个「前缀-主SKU」的套装 SKU"),
            ft.Row([self.combo_demo,
                    ft.OutlinedButton("预览", icon=ft.Icons.VISIBILITY, on_click=render)], spacing=10, wrap=True),
            self.combo_preview,
        ], spacing=8))

    # ---------- 逐图编辑方案（每张图单独提示词 + 多图素材）----------
    def _plans_card(self):
        from ..services.image_edit_plans import parse, dumps, describe, GROUP_LABELS

        state = {"slots": parse(self.cfg.image_edit_plans), "timer": None}
        rows_col = ft.Column(spacing=6)
        status = ft.Text("", size=11, color=MUTED)
        self._plan_rows_col = rows_col
        self._plan_state = state

        def persist():
            self.cfg.set("image_edit_plans", dumps(state["slots"]))
            save_config(self.cfg)
            desc = describe(state["slots"])
            status.value = (f"已保存 {len(state['slots'])} 条方案：" + "；".join(desc[:2])) if desc \
                else "未配置方案（刊登时不会做逐图编辑）"
            status.color = ACCENT_2 if state["slots"] else MUTED
            try:
                self.page.update()
            except Exception:
                pass

        def schedule_persist():
            t = state.get("timer")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            timer = threading.Timer(0.6, persist)
            timer.daemon = True
            state["timer"] = timer
            timer.start()

        def row_widgets(slot: dict):
            group_dd = ft.Dropdown(
                width=112, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                value=slot.get("group", "main"),
                options=[ft.dropdown.Option(k, v) for k, v in GROUP_LABELS.items()])
            target = ft.TextField(label="第几张", value=str(slot.get("target", 1)), width=78,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            refs = ft.TextField(label="参考图（1,2 / 3-5 / all）", value=str(slot.get("refs") or ""),
                                width=176, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                helper_text="素材：会和目标图一起交给模型")
            prompt = ft.TextField(label="这一张的提示词", value=str(slot.get("prompt") or ""),
                                  multiline=True, min_lines=2, max_lines=4, expand=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            mode = ft.Dropdown(
                width=168, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                value=slot.get("mode", "edit"),
                options=[ft.dropdown.Option("edit", "编辑（以目标图为主）"),
                         ft.dropdown.Option("generate", "生成（以参考图为素材）")])
            add_new = ft.Checkbox(label="追加为新图", value=bool(slot.get("add_new")))

            def sync(e=None):
                slot["group"] = group_dd.value or "main"
                try:
                    slot["target"] = max(1, int(target.value or 1))
                except Exception:
                    slot["target"] = 1
                slot["refs"] = str(refs.value or "").strip()
                slot["prompt"] = str(prompt.value or "").strip()
                slot["mode"] = mode.value or "edit"
                slot["add_new"] = bool(add_new.value)
                schedule_persist()

            for c in (group_dd, mode):
                c.on_change = sync
            for c in (target, refs, prompt):
                c.on_change = sync
            add_new.on_change = sync

            def remove(e):
                try:
                    state["slots"].remove(slot)
                except ValueError:
                    pass
                render()
                persist()

            return ft.Container(
                content=ft.Column([
                    ft.Row([group_dd, target, refs, mode], spacing=8, wrap=True),
                    prompt,
                    ft.Row([add_new,
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A",
                                          tooltip="删除这一条", on_click=remove)], spacing=8),
                ], spacing=6),
                bgcolor=CARD_2, border_radius=10, padding=10)

        def render():
            rows_col.controls = [row_widgets(s) for s in state["slots"]] or \
                [ft.Text("还没有逐图方案。点「添加一条」→ 选组（主图/描述图/SKU图）、第几张、参考图（可多张）和这一张的提示词。",
                         size=11, color=MUTED)]
            try:
                self.page.update()
            except Exception:
                pass

        def add_row(e=None):
            state["slots"].append({"group": "main", "target": len(state["slots"]) + 1,
                                   "refs": "all", "prompt": "", "mode": "edit", "add_new": False})
            render()

        def import_designated(e):
            """把老「主图指定编辑」一键转成方案。"""
            idx = str(getattr(self.cfg, "designated_edit_indices", "") or "").strip()
            prompt = str(getattr(self.cfg, "designated_edit_prompt", "") or "").strip()
            from ..services.image_index import parse_indices
            nums = parse_indices(idx, 12) if idx else []
            if not nums:
                self.toast("「主图指定编辑」里没有序号可导入", False); return
            for n in nums:
                state["slots"].append({"group": "main", "target": n, "refs": "all",
                                       "prompt": prompt, "mode": "edit", "add_new": False})
            render(); persist()
            self.toast(f"已导入 {len(nums)} 条主图指定编辑")

        def clear_all(e):
            state["slots"] = []
            render(); persist()

        render()
        status.value = (f"已保存 {len(state['slots'])} 条方案") if state["slots"] else "未配置方案（刊登时不会做逐图编辑）"
        status.color = ACCENT_2 if state["slots"] else MUTED
        return card(ft.Column([
            h1("逐图编辑方案（关键）", 15),
            muted("老软件那种「指定哪张图用什么提示词、素材用哪几张」：每条 = 目标组 + 第几张 + 参考图（可多张）+ 提示词"),
            muted("· 编辑：以目标图为主，参考图一起作为素材（图生图） · 生成：以参考图为素材生成新图或追加"),
            muted("· 参考图支持 1,2,3 / 3-5 / all（all = 该组全部图片）；参考图不在 1~4 张时超出部分会被忽略"),
            ft.Row([ft.ElevatedButton("添加一条", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=add_row),
                    ft.OutlinedButton("从「主图指定编辑」导入", icon=ft.Icons.SOUTH, on_click=import_designated),
                    ft.OutlinedButton("清空方案", icon=ft.Icons.DELETE_SWEEP, on_click=clear_all)], spacing=10, wrap=True),
            rows_col, status,
        ], spacing=8))

    # ---------- 背景生成规则（逐图换背景，可加/删）----------
    def _bg_rules_card(self):
        import json as _j
        try:
            rules = _j.loads(self.cfg.background_gen_rules or "[]")
            if not isinstance(rules, list):
                rules = []
        except Exception:
            rules = []
        state = {"rules": [r for r in rules if isinstance(r, dict)]}
        rows_col = ft.Column(spacing=6)
        status = ft.Text("", size=11, color=MUTED)

        def persist():
            self.cfg.set("background_gen_rules", _j.dumps(state["rules"], ensure_ascii=False))
            save_config(self.cfg)
            status.value = f"已保存 {len(state['rules'])} 条规则" if state["rules"] else "未配置规则"
            status.color = ACCENT_2 if state["rules"] else MUTED
            try:
                self.page.update()
            except Exception:
                pass

        POSITIONS = ["main_1", "main_2", "main_3", "main_4", "main_5", "main_6", "all_main",
                     "detail_1", "detail_2", "detail_3", "detail_4"]

        def row_widgets(rule: dict):
            pos = ft.Dropdown(width=150, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              value=str(rule.get("position") or "main_1"),
                              options=[ft.dropdown.Option(x) for x in POSITIONS])
            prompt = ft.TextField(label="换背景要求", value=str(rule.get("prompt") or ""),
                                  multiline=True, min_lines=2, max_lines=3, expand=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                  hint_text="例：换成浅灰摄影棚背景，保留产品与接触阴影")

            def sync(e=None):
                rule["position"] = pos.value or "main_1"
                rule["prompt"] = str(prompt.value or "").strip()
                persist()

            pos.on_change = sync
            prompt.on_change = sync

            def remove(e):
                try:
                    state["rules"].remove(rule)
                except ValueError:
                    pass
                render(); persist()

            return ft.Container(
                content=ft.Row([pos, prompt, ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A",
                                                           on_click=remove)], spacing=8),
                bgcolor=CARD_2, border_radius=10, padding=10)

        def render():
            rows_col.controls = [row_widgets(r) for r in state["rules"]] or \
                [ft.Text("未配置规则。添加一条 → 选「主图第几张 / 全部主图 / 描述图第几张」并写换背景要求。",
                         size=11, color=MUTED)]
            try:
                self.page.update()
            except Exception:
                pass

        def add_row(e=None):
            state["rules"].append({"position": f"main_{len(state['rules']) + 1}", "prompt": ""})
            render()

        render()
        status.value = f"已保存 {len(state['rules'])} 条规则" if state["rules"] else "未配置规则"
        status.color = ACCENT_2 if state["rules"] else MUTED
        return card(ft.Column([
            h1("背景替换规则（逐图换背景）", 15),
            muted("每条规则 = 一张图 + 一段换背景要求；刊登时按规则逐张调用图像编辑（保留商品本体）"),
            ft.Row([ft.ElevatedButton("添加规则", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=add_row)],
                   spacing=10, wrap=True),
            rows_col, status,
        ], spacing=8))

    def _four_in_one_local(self, e=None):
        """把当前选中的图（前 4 张）拼成一张，追加到图片墙与结果里。"""
        try:
            from ..services.image_ops import four_in_one
            from pathlib import Path as _P
            imgs = [p for p in (self.picked or []) if _P(p).exists()]
            if len(imgs) < 2:
                self.toast("至少需要 2 张图才能四合一", False); return
            out = _P(tempfile.gettempdir()) / f"four-in-one-{int(time.time())}.png"
            four_in_one(imgs[:4], out)
            self.picked = list(self.picked) + [out]
            self.wb_sel_paths.add(str(out))
            if getattr(self, "img_results", None) is not None:
                self.img_results.controls.append(ft.Row([
                    ft.Icon(ft.Icons.GRID_VIEW, size=15, color=ACCENT_2),
                    ft.Text(f"四合一完成：{out}", size=11, color=MUTED, selectable=True)], spacing=8))
            self.img_status.value = f"四合一完成：{len(imgs[:4])} 张 → {out.name}"
            self.img_status.color = ACCENT_2
            self._log(f"四合一：{len(imgs[:4])} 张 → {out}")
            try:
                self._wb_render_wall()
            except Exception:
                pass
            self.toast(f"四合一已生成：{out}")
        except Exception as ex:
            self.toast(f"四合一失败：{ex}", False)
        try:
            self.page.update()
        except Exception:
            pass

    def _p_ai(self):
        self._current = 3
        return self._render("AI 智能填写", schema_ai())

    def _p_review(self):
        self._current = 4
        view = self._render("内容审查", schema_review())

        self.rev_status = ft.Text("未开始", size=12, color=MUTED)
        self.rev_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("产品", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("文本", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("主图", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("结论", size=11, color=MUTED))],
            rows=[], column_spacing=14, heading_row_height=28, data_row_min_height=26)
        self.rev_scope = ft.Dropdown(value="editingRequired", width=140, bgcolor=CARD_2, color=TEXT,
                                     border_color=CARD_2,
                                     options=[ft.dropdown.Option("editingRequired", "草稿"),
                                              ft.dropdown.Option("onSelling", "在线")])
        self.rev_limit = ft.TextField(label="审查数量", value="10", width=110,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def start(e):
            threading.Thread(target=self._run_review, daemon=True).start()

        card_rev = card(ft.Column([
            h1("审查结果", 15),
            muted("拉取店铺产品，逐条跑文本审查 + 主图审查；不合规会标红（发布时可按设置拦截）"),
            ft.Row([self.rev_scope, self.rev_limit,
                    ft.ElevatedButton("开始审查", icon=ft.Icons.FACT_CHECK, bgcolor=ACCENT, color="white", on_click=start),
                    self.rev_status], spacing=10, wrap=True),
            self.rev_table,
        ], spacing=8))
        return [view[0], card_rev] + view[1:]

    def _run_review(self):
        try:
            limit = int(self.rev_limit.value or 10)
        except Exception:
            limit = 10
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            items = dxm.iter_products((getattr(self.cfg, 'shop_id', '') or self.cfg.store_id or None), self.rev_scope.value or None, limit, 3)
        except Exception as ex:
            self.toast(f"拉取产品失败：{ex}", False); return
        tp = TextPipeline(self.cfg)
        ip = ImagePipeline(self.cfg)
        self.rev_status.value = f"审查中… 0/{len(items)}"
        rows = []
        bad = 0
        for i, it in enumerate(items, 1):
            title = str(it.get("title") or "")
            text_verdict = "跳过"
            if self.cfg.review_text_enable and title:
                r = tp.review_text(title)
                _rv = (r.value or "").upper()
                _bad = ("FAIL" in _rv) or any(k in (r.value or "") for k in ("不符合", "违规", "建议删除"))
                text_verdict = "不合规" if (r.ok and _bad) else ("通过" if r.ok else "失败")
            img_verdict = "跳过"
            imgs = it.get("images") or []
            if self.cfg.review_main_img_enable and imgs:
                try:
                    v = ip.review(str(imgs[0]), "main")
                    _iv = (v or "").upper()
                    _ibad = ("FAIL" in _iv) or any(k in (v or "") for k in ("不符合", "违规", "建议删除", "含联系方式"))
                    img_verdict = "不合规" if _ibad else "通过"
                except Exception:
                    img_verdict = "失败"
            ok = "不合规" not in (text_verdict, img_verdict) and "失败" not in (text_verdict, img_verdict)
            bad += 0 if ok else 1
            color = ACCENT_2 if ok else "#FF6B6B"
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(title[:44], size=11, color=TEXT)),
                ft.DataCell(ft.Text(text_verdict, size=11, color=MUTED)),
                ft.DataCell(ft.Text(img_verdict, size=11, color=MUTED)),
                ft.DataCell(ft.Text("通过" if ok else "需处理", size=11, color=color)),
            ]))
            self.rev_table.rows = list(rows)
            self.rev_status.value = f"审查中… {i}/{len(items)}"
            try:
                self.page.update()
            except Exception:
                pass
        self.rev_status.value = f"完成：{len(items)} 个产品，{bad} 个需处理"
        self.rev_status.color = "#FF6B6B" if bad else ACCENT_2
        self._log(f"内容审查完成：{len(items)} 个，{bad} 个需处理")
        try:
            self.page.update()
        except Exception:
            pass


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

        self.img_quick_prompt = ft.TextField(
            label="通用编辑提示词（只对「通用编辑」按钮生效）", value=self.cfg.edit_prompt or "",
            multiline=True, min_lines=2, max_lines=4, expand=True,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            helper_text="逐图不同的提示词请用下面的「逐图编辑方案」")
        tool = card(ft.Column([
            h1("图片工具", 15),
            ft.Row([ft.OutlinedButton("选择图片", icon=ft.Icons.UPLOAD_FILE, on_click=pick),
                    ft.ElevatedButton("白底图", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                    ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                    ft.ElevatedButton("通用编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                    ft.OutlinedButton("四合一拼图", icon=ft.Icons.GRID_VIEW, on_click=self._four_in_one_local),
                    self.img_busy], spacing=10, wrap=True),
            self.img_quick_prompt,
            self.img_status, self.img_results], spacing=10))
        return [view[0], tool, self._plans_card(), self._bg_rules_card()] + view[1:]

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

        self.lib_name = ft.TextField(label="模板名称", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.lib_dd = ft.Dropdown(width=220, bgcolor=CARD_2, color=TEXT, border_color=CARD_2, options=[])

        def _lib_refresh():
            lib = self.cfg.price_template_library or []
            if not isinstance(lib, list):
                lib = []
            self.lib_dd.options = [ft.dropdown.Option(str(t.get("name"))) for t in lib if isinstance(t, dict)]
            try:
                self.page.update()
            except Exception:
                pass

        def lib_save(e):
            name = (self.lib_name.value or "").strip()
            if not name:
                self.toast("请输入模板名称", False); return
            try:
                rates = json.loads(self.cfg.adjust_price_template_detail or "{}")
            except Exception:
                self.toast("区域倍率 JSON 格式错误", False); return
            lib = [t for t in (self.cfg.price_template_library or []) if isinstance(t, dict) and t.get("name") != name]
            lib.append({"name": name, "rates": rates})
            self.cfg.price_template_library = lib
            save_config(self.cfg)
            _lib_refresh()
            self.toast(f"已保存模板：{name}")

        def lib_apply(e):
            name = self.lib_dd.value
            for t in (self.cfg.price_template_library or []):
                if isinstance(t, dict) and t.get("name") == name:
                    self.cfg.adjust_price_template_detail = json.dumps(t.get("rates") or {}, ensure_ascii=False)
                    save_config(self.cfg)
                    self.toast(f"已应用模板：{name}（保存配置后生效）")
                    return
            self.toast("未找到模板", False)

        def lib_del(e):
            name = self.lib_dd.value
            self.cfg.price_template_library = [t for t in (self.cfg.price_template_library or [])
                                              if not (isinstance(t, dict) and t.get("name") == name)]
            save_config(self.cfg)
            _lib_refresh()
            self.toast("已删除")

        _lib_refresh()
        lib_card = card(ft.Column([h1("本地区域模板", 15),
                                   muted("把当前「区域倍率 JSON」存成模板，换产品或换店铺时一键套用（不依赖店小秘）"),
                                   ft.Row([self.lib_name,
                                           ft.ElevatedButton("保存当前", icon=ft.Icons.SAVE, bgcolor=CARD_2, color=TEXT, on_click=lib_save),
                                           self.lib_dd,
                                           ft.ElevatedButton("应用", icon=ft.Icons.CHECK, bgcolor=ACCENT, color="white", on_click=lib_apply),
                                           ft.OutlinedButton("删除", on_click=lib_del)], spacing=10, wrap=True)], spacing=8))

        tpl_card = card(ft.Column([h1("区域调价模板", 15),
                                   ft.Row([self.tpl_kw, ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, on_click=search)], spacing=10),
                                   self.tpl_status, self.tpl_list], spacing=10))

        calc_card = card(ft.Column([h1("价格试算", 15), ft.Row([self.base_input,
                                                                 ft.ElevatedButton("计算", on_click=calc),
                                                                 self.price_out], spacing=14)], spacing=10))
        view = self._render("价格设置", schema_price())
        return [view[0], calc_card, lib_card, tpl_card] + view[1:]

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
        self.wb_prompt = ft.TextField(label="「编辑」用的提示词（逐张不同请到「AI图片处理 → 逐图编辑方案」）",
                                      value=self.cfg.edit_prompt or "", multiline=True, min_lines=2, max_lines=3,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_status = ft.Text("未加载图片", color=MUTED, size=12)
        self.wb_results = ft.Column(spacing=4)
        self.wb_wall = ft.Row(spacing=8, wrap=True, scroll=ft.ScrollMode.AUTO)
        self.wb_sel_paths: set[str] = set()
        self.wb_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)

        def load(e):
            urls = [u.strip() for u in (self.wb_urls.value or "").splitlines() if u.strip()]
            if not urls:
                self.toast("请输入图片 URL", False); return
            self.wb_status.value = f"共 {len(urls)} 个链接，开始下载…"
            self.page.update()
            threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

        def _start(kind):
            self.wb_busy.visible = True
            try:
                self.page.update()
            except Exception:
                pass
            threading.Thread(target=self._wb_process, args=(kind,), daemon=True).start()

        def run(kind):
            if not self.picked:
                self.toast("请先加载图片", False); return
            if not self.cfg.api_key:
                self.toast("请先设置 API Key", False); return
            targets = self._wb_selected_paths()
            n = len(targets)
            price = self._price_of(self.cfg.model_image_edit)
            total = n * price
            limit = float(self.cfg.cost_warn_threshold or 5)
            if price > 0 and total > limit:
                def cancel(e):
                    self._close_dialog()
                def go(e):
                    self._close_dialog()
                    _start(kind)
                self.dlg = ft.AlertDialog(
                    title=ft.Text("费用确认"),
                    content=ft.Text(f"本次将处理 {n} 张图（模型 {self.cfg.model_image_edit}，¥{price:.3f}/张）\n"
                                    f"预计花费 ¥{total:.2f}，超过提醒阈值 ¥{limit:.2f}。\n是否继续？"),
                    actions=[ft.TextButton("取消", on_click=cancel),
                             ft.ElevatedButton("继续", bgcolor=ACCENT, color="white", on_click=go)])
                self.page.dialog = self.dlg
                self.dlg.open = True
                self.page.update()
                return
            _start(kind)

        self.q = TaskQueue()
        self.q_text = ft.Text("", size=12, color=MUTED)
        self._refresh_queue()

        def enqueue(e):
            pool = {}
            for p in (self.products or []):
                pool[str(p.get("id"))] = p
            for p in (getattr(self, "wb_rows", None) or []):
                pool.setdefault(str(p.get("id")), p)
            if not pool:
                self.toast("没有可入队的产品：先「加载」产品列表或先跑一次上架", False)
                return
            chosen = ([pool[str(pid)] for pid in self.wb_selected if str(pid) in pool]
                      if getattr(self, "wb_selected", None) else list(pool.values()))
            self._queue_products = pool
            n = self.q.add_many(chosen)
            self._refresh_queue()
            self.toast(f"已入队 {n} 个产品（共 {len(chosen)} 个选中）")

        def process(e):
            items = self.q.pending()
            if not items:
                self.toast("队列为空", False); return
            threading.Thread(target=self._process_queue, daemon=True).start()

        # ---- 产品列表（草稿/在线/待审核 + 搜索 + 分页 + 全选） ----
        self.wb_status_sel = ft.Dropdown(
            value="editingRequired", width=150, bgcolor=CARD_2, color=TEXT, border_color=CARD_2,
            options=[ft.dropdown.Option("editingRequired", "草稿"),
                     ft.dropdown.Option("onSelling", "在线"),
                     ft.dropdown.Option("auditing", "待审核"),
                     ft.dropdown.Option("offline", "已下架"),
                     ft.dropdown.Option("", "全部")])
        self.wb_kw = ft.TextField(label="搜索标题", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_page_no = 1
        self.wb_page_info = ft.Text("", size=12, color=MUTED)
        self.wb_sel_text = ft.Text("已选 0 个产品 / 0 张图", size=12, color=MUTED)
        self.wb_selected: set[str] = set()
        self.wb_rows: list[dict] = []
        self.wb_prod_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("选", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("产品", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("状态", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("图/SKU", size=11, color=MUTED))],
            rows=[], column_spacing=16, heading_row_height=28, data_row_min_height=30,)

        def load_products(e=None):
            try:
                ck = self._cookies()
                dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            except Exception as ex:
                self.toast(f"未连接浏览器：{ex}", False); return
            st = self.wb_status_sel.value or None
            kw = (self.wb_kw.value or "").strip() or None
            try:
                items, meta = dxm.product_page(self.wb_page_no, 30, (getattr(self.cfg, 'shop_id', '') or self.cfg.store_id or None), st, kw)
            except Exception as ex:
                self.toast(f"加载失败：{ex}", False); return
            self.wb_rows = items
            rows = []
            for it in items:
                pid = str(it.get("id") or "")
                cb = ft.Checkbox(value=pid in self.wb_selected,
                                 on_change=lambda e, _p=pid: self._wb_toggle(_p, e.control.value))
                rows.append(ft.DataRow(cells=[
                    ft.DataCell(cb),
                    ft.DataCell(ft.Text((it.get("title") or "")[:52], size=11, color=TEXT)),
                    ft.DataCell(ft.Text(it.get("status") or "", size=11, color=MUTED)),
                    ft.DataCell(ft.Text(f"{len(it.get('images') or [])}/{len(it.get('skus') or [])}", size=11, color=MUTED)),
                ]))
            self.wb_prod_table.rows = rows
            self.wb_page_info.value = f"第 {meta.get('page') or self.wb_page_no}/{meta.get('total_pages') or 1} 页 · 共 {meta.get('total') or 0} 个"
            self._wb_sel_refresh()
            self.page.update()

        def prev_page(e):
            if self.wb_page_no > 1:
                self.wb_page_no -= 1
                load_products()

        def next_page(e):
            self.wb_page_no += 1
            load_products()

        def select_all(e):
            val = bool(e.control.value)
            for it in self.wb_rows:
                pid = str(it.get("id") or "")
                if val:
                    self.wb_selected.add(pid)
                else:
                    self.wb_selected.discard(pid)
            load_products()

        def pick_images(e):
            if not self.wb_selected:
                self.toast("请先勾选产品", False); return
            urls: list[str] = []
            for it in self.wb_rows:
                if str(it.get("id")) in self.wb_selected:
                    urls.extend([u for u in (it.get("images") or []) if isinstance(u, str)])
            urls = [u for i, u in enumerate(urls) if u and u not in urls[:i]]
            if not urls:
                self.toast("选中的产品没有图片", False); return
            self.wb_status.value = f"已选 {len(self.wb_selected)} 个产品，下载 {len(urls)} 张图…"
            self.page.update()
            threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

        self.wb_prod_card = card(ft.Column([
            h1("产品图片", 15),
            muted("从店小秘拉取产品，勾选后取图，可直接做白底/去水印/编辑/视频"),
            ft.Row([self.wb_status_sel, self.wb_kw,
                    ft.ElevatedButton("加载", icon=ft.Icons.SEARCH, bgcolor=CARD_2, color=TEXT, on_click=load_products)], spacing=8, wrap=True),
            ft.Row([ft.OutlinedButton("上一页", on_click=prev_page),
                    ft.OutlinedButton("下一页", on_click=next_page),
                    self.wb_page_info], spacing=8, wrap=True),
            ft.Row([ft.Checkbox(label="全选本页", on_change=select_all), self.wb_sel_text,
                    ft.ElevatedButton("取选中图片", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=ACCENT, color="white", on_click=pick_images),
                    ], spacing=10, wrap=True),
            self.wb_prod_table,
        ], spacing=8))

        self.q_panel = card(ft.Column([h1("任务队列", 15), self.q_text,
                                       ft.Row([ft.ElevatedButton("产品入队", icon=ft.Icons.ADD_TASK, on_click=enqueue),
                                               ft.ElevatedButton("处理队列(dry-run)", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT, color="white", on_click=process),
                                               ft.OutlinedButton("清理已完成", on_click=lambda e: (self.q.clear_done(), self._refresh_queue())),
                                               ft.OutlinedButton("重试失败", icon=ft.Icons.REPLAY,
                                                                 on_click=lambda e: self._retry_failed_queue())], spacing=10, wrap=True)], spacing=8))
        return [h1("图片工作台"), muted("批量白底 / 去水印 / 编辑 / 主图视频（走你的 API 站点计费）"), self.wb_prod_card, self.q_panel,
                card(ft.Column([self.wb_urls,
                                ft.Row([ft.ElevatedButton("加载图片", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=load),
                                        ft.OutlinedButton("从已拉取产品取图", icon=ft.Icons.INVENTORY_2, on_click=lambda e: self._images_from_products()),
                                        ft.ElevatedButton("白底", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                                        ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                                        ft.ElevatedButton("编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                                        ft.OutlinedButton("四合一拼图", icon=ft.Icons.GRID_VIEW,
                                                          on_click=self._four_in_one_local),
                                        ft.OutlinedButton("暂停", on_click=lambda e: self._wb_ctl("pause")),
                                        ft.OutlinedButton("继续", on_click=lambda e: self._wb_ctl("resume")),
                                        ft.OutlinedButton("取消", on_click=lambda e: self._wb_ctl("cancel")),
                                        ft.ElevatedButton("生成视频", icon=ft.Icons.MOVIE, bgcolor="#7C5CFF", color="white",
                                                          on_click=lambda e: threading.Thread(target=self._wb_make_video, daemon=True).start()),
                                        ft.ElevatedButton("上传结果到相册", icon=ft.Icons.CLOUD_UPLOAD, bgcolor="#F0A020", color="#1A1200",
                                                          on_click=lambda e: threading.Thread(target=self._upload_results, daemon=True).start()),
                                        ft.OutlinedButton("清理旧图 / 空间", icon=ft.Icons.DELETE_SWEEP,
                                                          on_click=lambda e: self._open_cleanup_dialog()),
                                        self.wb_busy], spacing=10, wrap=True),
                                self.wb_prompt,
                                self.wb_status,
                                ft.Text("点选要处理的图片（默认全选）", size=11, color=MUTED),
                                self.wb_wall], spacing=10)),
                card(self.wb_results)]

    def _wb_make_video(self):
        if not self.picked:
            self.toast("请先取图（选产品 → 取选中图片）", False); return
        info = video_probe()
        if not info["available"]:
            self.toast("未检测到 ffmpeg，请先安装", False); return
        try:
            outdir = Path(self.cfg.video_output_dir) if self.cfg.video_output_dir else (Path.home() / "Movies" / "AuroraList")
            outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"carousel-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
            _sel = [p for p in self._wb_selected_paths() if Path(p).exists()] or list(self.picked)
            self._log(f"开始合成视频：{len(_sel)} 张图 → {out}")
            generate_carousel_video(
                list(_sel), out,
                slot_seconds=float(self.cfg.video_slot_seconds or 2.5),
                transition_seconds=float(self.cfg.video_transition_seconds or 0.5),
                size=str(self.cfg.video_size or "1080x1080"),
                cover=bool(self.cfg.video_cover),
                fade_edges=bool(self.cfg.video_fade_edges),
            )
            self._log(f"视频已生成：{out}")
            self.toast(f"视频已生成：{out}")
        except Exception as ex:
            self._log(f"视频生成失败：{ex}")
            self.toast(str(ex), False)

    def _wb_render_wall(self):
        if getattr(self, "wb_wall", None) is None:
            self.wb_wall = ft.Row(spacing=8, wrap=True)
        tiles = []
        for pth in list(self.picked):
            key = str(pth)
            if key not in self.wb_sel_paths:
                self.wb_sel_paths.add(key)
            cb = ft.Checkbox(value=key in self.wb_sel_paths,
                             on_change=lambda e, _k=key: self._wb_pick_one(_k, e.control.value))
            tiles.append(ft.Column([
                ft.Image(src=key, width=92, height=92, fit=ft.ImageFit.COVER, border_radius=6),
                cb,
            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER, width=96))
        self.wb_wall.controls = tiles
        try:
            self.page.update()
        except Exception:
            pass

    def _wb_pick_one(self, key: str, on: bool):
        if on:
            self.wb_sel_paths.add(key)
        else:
            self.wb_sel_paths.discard(key)

    def _wb_selected_paths(self) -> list:
        sel = [pth for pth in self.picked if str(pth) in self.wb_sel_paths]
        return sel or list(self.picked)

    def _wb_toggle(self, pid: str, on: bool):
        if on:
            self.wb_selected.add(pid)
        else:
            self.wb_selected.discard(pid)
        self._wb_sel_refresh()

    def _wb_sel_refresh(self):
        n_img = 0
        for it in getattr(self, "wb_rows", []):
            if str(it.get("id")) in self.wb_selected:
                n_img += len(it.get("images") or [])
        self.wb_sel_text.value = f"已选 {len(self.wb_selected)} 个产品 / {n_img} 张图"
        try:
            self.page.update()
        except Exception:
            pass

    def _retry_failed_queue(self):
        n = self.q.retry_failed()
        self._refresh_queue()
        self.toast(f"已重新排队 {n} 个失败任务" if n else "没有失败任务")

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
        pub = Publisher(self.cfg, dxm=dxm, ws_endpoint=self.ws_endpoint, scheduler=getattr(self, 'scheduler', None))
        ok = fail = 0
        by_id = {str(p.get("id")): p for p in (self.products or [])}
        by_id.update({str(k): v for k, v in getattr(self, "_queue_products", {}).items()})
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
        self.toast(f"已填入 {min(len(urls),60)} 个图片链接，开始下载…")
        self.wb_status.value = f"下载 {min(len(urls), 60)} 张图…"
        try:
            threading.Thread(target=self._download_images, args=(urls[:60],), daemon=True).start()
        except Exception:
            pass

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
        self.wb_sel_paths = set()
        self.wb_status.value = f"已下载 {len(got)}/{len(urls)} 张"
        self.wb_status.color = ACCENT_2 if got else "#FF6B6B"
        try:
            self._wb_render_wall()
        except Exception:
            pass
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
        svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
        runner = BatchRunner(self.cfg.concurrency, retries=int(self.cfg.retry_max or 1), retry_delay=float(self.cfg.retry_delay or 2.0))
        self.wb_runner = runner
        self.wb_results.controls = []

        _prompt = str(getattr(getattr(self, "wb_prompt", None), "value", "") or "").strip() \
            or (self.cfg.edit_prompt or "clean product photo")

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), _prompt)

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            if not res.ok:
                self._maybe_quota(res.error)
            self.wb_results.controls.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                                    ft.Text(res.value if res.ok else res.error, size=11,
                                                            color=MUTED, selectable=True)], spacing=8))
            self.page.update()

        targets = self._wb_selected_paths() if hasattr(self, "_wb_selected_paths") else list(self.picked)
        results = runner.run(list(targets), job, done)
        ok = sum(1 for r in results if r.ok)
        try:
            history.record("workbench:" + kind, ok, len(results) - ok)
        except Exception:
            pass
        self.wb_status.value = f"处理完成：成功 {ok} / {len(results)}"
        self.wb_busy.visible = False
        self.page.update()

    def _p_prompts(self):
        self._current = 11
        view = self._render("提示词", schema_prompts())
        resets = []
        for key, label in (("title_prompt", "标题"), ("detail_prompt", "描述"), ("sku_prompt", "SKU"),
                           ("weight_prompt", "估重"), ("main_image_generate_prompt", "主图生成"),
                           ("designated_edit_prompt", "指定编辑"), ("translate_prompt", "翻译"),
                           ("background_gen_sku_prompt", "换背景"), ("edit_prompt", "通用编辑"),
                           ("review_text_prompt", "文本审查"), ("review_main_img_prompt", "主图审查"),
                           ("review_detail_img_prompt", "详情图审查")):
            def make(k=key, n=label):
                def _r(e):
                    default = AppConfig.DEFAULTS.get(k, "")
                    self.cfg.set(k, default)
                    save_config(self.cfg)
                    self.toast(f"{n}提示词已恢复默认")
                    self._switch(11)
                return _r
            resets.append(ft.OutlinedButton(f"恢复默认：{label}", on_click=make()))
        tip = card(ft.Column([
            h1("提示词说明", 15),
            muted("• 这里的提示词直接决定 AI 产出的专业度，改动会立即用于下一次刊登"),
            muted("• 主图生成提示词支持变量 {title}（会自动替换为产品标题）"),
            muted("• 审查提示词建议要求模型只回 PASS 或 FAIL|原因，才能自动拦截不合规内容"),
            ft.Row(resets, spacing=8, wrap=True)], spacing=8))
        # ---- 预设切换 ----
        self.preset_dd = ft.Dropdown(label="标题风格预设", width=260, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     value=self.cfg.get("prompt_preset", "standard") or "standard",
                                     options=[ft.dropdown.Option(k, v[0]) for k, v in TITLE_PRESETS.items()])

        def apply_preset(e):
            name = self.preset_dd.value or "standard"
            prompt_text = TITLE_PRESETS.get(name, TITLE_PRESETS["standard"])[1]
            self.cfg.set("title_prompt", prompt_text)
            self.cfg.set("prompt_preset", name)
            save_config(self.cfg)
            self.toast(f"已应用预设：{TITLE_PRESETS[name][0]}")
            self._switch(11)

        # ---- 方案库（按品类） ----
        from ..services.prompt_sets import list_sets, save_set, delete_set, apply_set
        self.ps_name = ft.TextField(label="方案名称", width=180, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.ps_cat = ft.TextField(label="绑定品类关键词（如 crane / 吊机）", width=240,
                                   bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.ps_dd = ft.Dropdown(label="已有方案", width=240, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])

        def _ps_refresh():
            sets = list_sets(self.cfg)
            self.ps_dd.options = [ft.dropdown.Option(s["name"],
                                                     f"{s['name']}" + (f"（{s.get('category')}）" if s.get("category") else ""))
                                  for s in sets]
            try:
                self.page.update()
            except Exception:
                pass

        def ps_save(e):
            name = (self.ps_name.value or "").strip()
            if not name:
                self.toast("请输入方案名称", False); return
            save_set(self.cfg, name, self.ps_cat.value or "")
            save_config(self.cfg)
            _ps_refresh()
            self.toast(f"方案已保存：{name}")

        def ps_apply(e):
            name = self.ps_dd.value
            if not name:
                self.toast("请选择方案", False); return
            if apply_set(self.cfg, name):
                save_config(self.cfg)
                self.toast(f"已套用方案：{name}")
                self._switch(11)
            else:
                self.toast("方案不存在", False)

        def ps_del(e):
            name = self.ps_dd.value
            if not name:
                self.toast("请选择方案", False); return
            delete_set(self.cfg, name)
            save_config(self.cfg)
            _ps_refresh()
            self.toast("已删除")

        _ps_refresh()
        ps_card = card(ft.Column([
            h1("提示词方案库（按品类）", 15),
            muted("把当前 9 条提示词存成一套方案并绑定品类；刊登时若商品类目/标题含该关键词，自动套用该方案"),
            ft.Row([self.ps_name, self.ps_cat,
                    ft.ElevatedButton("保存当前为方案", icon=ft.Icons.BOOKMARK_ADD, bgcolor=CARD_2, color=TEXT,
                                      on_click=ps_save)], spacing=10, wrap=True),
            ft.Row([self.ps_dd,
                    ft.ElevatedButton("套用", icon=ft.Icons.CHECK, bgcolor=ACCENT, color="white", on_click=ps_apply),
                    ft.OutlinedButton("删除", on_click=ps_del)], spacing=10, wrap=True)], spacing=8))

        preset_card = card(ft.Column([
            h1("标题风格预设", 15),
            muted("一键切换三种投放策略，会覆盖「标题提示词」，可随时恢复默认"),
            ft.Row([self.preset_dd,
                    ft.ElevatedButton("应用预设", icon=ft.Icons.AUTO_AWESOME, bgcolor=ACCENT, color="white",
                                      on_click=apply_preset)], spacing=10)], spacing=8))

        # ---- 试写 + A/B 对比 ----
        self.try_input = ft.TextField(label="试写商品（中文标题或关键词）", width=560,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                      value="3吨小型蜘蛛吊车 20米吊高 履带式电动")
        self.ab_mode = ft.Dropdown(label="对比对象", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                   value="标题", options=[ft.dropdown.Option(x) for x in ("标题", "描述", "图片")])
        self.try_b_prompt = ft.TextField(label="B 方案提示词（留空则用内置对照预设）", multiline=True,
                                         min_lines=3, max_lines=6, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.try_a_col = ft.Column(spacing=4)
        self.try_b_col = ft.Column(spacing=4)
        self.try_status = ft.Text("", size=11, color=MUTED)
        self.try_a_prompt_text = ""
        self.try_b_prompt_text = ""

        def _render_variants(col, variants, tag, prompt_text):
            col.controls = []
            for i, v in enumerate(variants, 1):
                if not v.ok:
                    col.controls.append(ft.Text(f"{tag} 失败：{v.error}", size=11, color="#FF6B6B"))
                    continue
                sc = score_title(v.text)
                color = ACCENT_2 if sc["score"] >= 90 else ("#FFB020" if sc["score"] >= 70 else "#FF6B6B")
                col.controls.append(ft.Column([
                    ft.Text(v.text, size=12, color=TEXT, selectable=True),
                    ft.Row([ft.Text(f"评分 {sc['score']} · {sc['chars']} 字符 · {sc['words']} 词", size=10, color=color),
                            ft.TextButton("采用", on_click=lambda e, _t=v.text, _p=prompt_text: self._adopt_title(_t, _p))],
                           spacing=8),
                ], spacing=2))

        def run_ab(e):
            src = (self.try_input.value or "").strip()
            if not src:
                self.toast("请输入要试写的商品", False); return
            if not self.cfg.api_key:
                self.toast("请先在设置里填 API Key", False); return
            self.try_status.value = "生成中…"
            self.page.update()
            threading.Thread(target=_do_ab, args=(src,), daemon=True).start()

        def _do_ab(src):
            from ..services import model_of
            from ..api import ApiClient
            from ..services.prompt_presets import title_variants
            mode = self.ab_mode.value or "标题"
            client = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)
            model = model_of(self.cfg, "text")
            try:
                if mode == "标题":
                    a_prompt = self.cfg.title_prompt
                    b_prompt = (self.try_b_prompt.value or "").strip() or TITLE_PRESETS["longtail"][1]
                    va = title_variants(client, model, a_prompt, src, 3)
                    vb = title_variants(client, model, b_prompt, src, 3)
                    _render_variants(self.try_a_col, va, "A", a_prompt)
                    _render_variants(self.try_b_col, vb, "B", b_prompt)
                    ba = max((score_title(v.text)["score"] for v in va if v.ok), default=0)
                    bb = max((score_title(v.text)["score"] for v in vb if v.ok), default=0)
                    self.try_status.value = f"完成：A 最高 {ba} 分 / B 最高 {bb} 分（{model}）"

                elif mode == "描述":
                    a_prompt = self.cfg.detail_prompt
                    b_prompt = (self.try_b_prompt.value or "").strip() or (
                        "你是速卖通详情页文案。用英文输出 HTML：一段 60 词的卖点、5 条要点、"
                        "参数表（Model/Material/Size/Weight/Power）、包装清单、售后说明。"
                        "全英文，禁止联系方式与促销语，300~400 词。")
                    def gen(prompt):
                        out = client.chat(model, [{"role": "system", "content": prompt},
                                                  {"role": "user", "content": src}], max_tokens=1200)
                        return out or ""
                    da, db = gen(a_prompt), gen(b_prompt)
                    for col, text, tag, pr in ((self.try_a_col, da, "A", a_prompt),
                                                (self.try_b_col, db, "B", b_prompt)):
                        col.controls = [
                            ft.Text(f"{tag} 长度 {len(text)} 字符 · 约 {len(text.split())} 词", size=10, color=MUTED),
                            ft.Text(text[:1200], size=11, color=TEXT, selectable=True),
                        ]
                    self.try_status.value = (f"完成：A {len(da)} 字符 / B {len(db)} 字符（{model}）")

                else:  # 图片
                    from ..services import ImageService
                    from ..services.progress import BUS
                    a_prompt = (self.cfg.main_image_generate_prompt or "").replace("{title}", src).replace("{subject}", src)
                    b_prompt = (self.try_b_prompt.value or "").strip() or (
                        f"Professional e-commerce product photo of {src}, pure white background, "
                        "soft studio light, centered, 1:1, no text, no watermark")
                    svc = ImageService(client, self.cfg)
                    ua = svc.generate(a_prompt, "1024x1024", 1)
                    ub = svc.generate(b_prompt, "1024x1024", 1)
                    for col, url, tag, pr in ((self.try_a_col, ua, "A", a_prompt),
                                                (self.try_b_col, ub, "B", b_prompt)):
                        col.controls = [ft.Text(f"{tag} · {self.cfg.model_image_gen}", size=10, color=MUTED),
                                        ft.Image(src=str(url), width=320, height=320, fit=ft.ImageFit.CONTAIN),
                                        ft.Text(str(url)[:70], size=9, color=MUTED, selectable=True)]
                    self.try_status.value = f"完成：两张图各扣约 ¥{BUS.price_of(self.cfg.model_image_gen):.2f}"
            except Exception as ex:
                self.try_status.value = f"失败：{str(ex)[:140]}"
            try:
                self.page.update()
            except Exception:
                pass

        ab_card = card(ft.Column([
            h1("试写 / A-B 对比", 15),
            muted("同一商品，用两套提示词各生成 3 条候选，带本地评分，选中即设为标题提示词"),
            ft.Row([self.try_input, self.ab_mode,
                    ft.ElevatedButton("生成对比", icon=ft.Icons.COMPARE_ARROWS, bgcolor=ACCENT, color="white",
                                      on_click=run_ab)], spacing=10, wrap=True),
            self.try_b_prompt,
            ft.Row([ft.Column([ft.Text("A 方案（当前提示词）", size=12, color=ACCENT_2), self.try_a_col], spacing=4, expand=True),
                    ft.Column([ft.Text("B 方案（对照）", size=12, color="#7C5CFF"), self.try_b_col], spacing=4, expand=True)],
                   spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
            self.try_status], spacing=10))

        return [view[0], ps_card, preset_card, ab_card, tip] + view[1:]

    def _adopt_title(self, text: str, prompt_text: str):
        """采用某条标题：把它记录为该提示词的样例，并保存到本地供下次参考。"""
        try:
            samples = self.cfg.get("title_samples", []) or []
            if not isinstance(samples, list):
                samples = []
            samples = ([text] + samples)[:20]
            self.cfg.set("title_samples", samples)
            save_config(self.cfg)
            self.toast("已记录为优质样例，下次同类产品可参考")
        except Exception as ex:
            self.toast(str(ex), False)

    def _p_preview(self):
        self._current = 12
        self.pv_dd = ft.Dropdown(label="选择产品（草稿）", width=520, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.pv_status = ft.Text("点「载入草稿」拉取产品", size=12, color=MUTED)
        self.pv_title = ft.Text("", size=15, color=TEXT, weight=ft.FontWeight.BOLD)
        self.pv_meta = ft.Column(spacing=2)
        self.pv_images = ft.Row(spacing=8, wrap=True, scroll=ft.ScrollMode.AUTO)
        self.pv_actions = ft.Column(spacing=2)
        self.pv_cost = ft.Text("", size=12, color=ACCENT_2)
        self.pv_risks = ft.Column(spacing=2)
        self._pv_products: list[dict] = []

        def load(e):
            self.pv_status.value = "拉取中…"; self.page.update()
            threading.Thread(target=_load, daemon=True).start()

        def _load():
            try:
                ck = self._cookies()
                dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                items, meta = dxm.product_page(1, 50, self.cfg.store_id or None, "editingRequired")
                self._pv_products = items[:30]
                self.pv_dd.options = [ft.dropdown.Option(str(p["id"]), (p.get("title") or "")[:60]) for p in self._pv_products]
                if self._pv_products:
                    self.pv_dd.value = str(self._pv_products[0]["id"])
                self.pv_status.value = f"共 {meta.get('total')} 个待发布，已载入 {len(self._pv_products)} 个"
                _render_preview(None)
            except Exception as ex:
                self.pv_status.value = f"拉取失败：{str(ex)[:100]}"
            try:
                self.page.update()
            except Exception:
                pass

        def _render_preview(e):
            pid = self.pv_dd.value
            prod = next((p for p in self._pv_products if str(p.get("id")) == str(pid)), None)
            if not prod:
                return
            title = prod.get("title") or ""
            imgs = [u for u in (prod.get("images") or []) if isinstance(u, str)]
            skus = prod.get("skus") or []

            self.pv_title.value = title[:110]
            # 元信息
            cost = float(self.cfg.goods_cost or 0) or 10.0
            price = calc_multiplier(cost, float(self.cfg.price_multiplier or 1.2), float(self.cfg.price_fixed_add or 0))
            self.pv_meta.controls = [
                ft.Text(f"产品 ID：{pid}    店铺：{self.cfg.store_name or '-'}", size=11, color=MUTED),
                ft.Text(f"图片：{len(imgs)} 张    变体：{len(skus)} 个    类目模板：{self.cfg.template_product_title or '未选择'}", size=11, color=MUTED),
                ft.Text(f"价格：成本 ¥{cost:.2f} × {self.cfg.price_multiplier} → 售价 ¥{price.final}", size=12, color=ACCENT_2),
            ]
            # 图片缩略图（最多 8）
            self.pv_images.controls = [ft.Column([
                ft.Image(src=u, width=96, height=96, fit=ft.ImageFit.COVER, border_radius=6),
                ft.Text(f"{i+1}", size=9, color=MUTED)], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
                for i, u in enumerate(imgs[:8])]

            # 将执行的动作
            acts = []
            if self.cfg.white_bg_enable:
                acts.append(f"主图白底（第 {self.cfg.white_background_pic_1}-{self.cfg.white_background_pic_2} 张）")
            if self.cfg.watermark_enable:
                acts.append("主图去水印")
            if self.cfg.enable_ai_title:
                acts.append(f"AI 标题（{self.cfg.model_text}）")
            if self.cfg.enable_ai_detail:
                acts.append("AI 描述（HTML）")
            if self.cfg.enable_ai_sku and skus:
                acts.append("AI SKU 优化")
            if (self.cfg.template_snapshot or "").strip() not in ("", "{}"):
                acts.append("写入模板（类目/运费/属性）")
            if self.cfg.generate_main_video:
                acts.append("生成并上传主图视频")
            _act = self.cfg.get("publish_action", "save_only")
            acts.append("保存到店小秘" + ("，并发布到速卖通" if _act == "publish" else "（留在待发布）"))
            self.pv_actions.controls = [ft.Text(f"{i}. {a}", size=11, color=TEXT) for i, a in enumerate(acts, 1)]

            # 费用预估
            edit_price = self._price_of(self.cfg.model_image_edit)
            n_edit = 0
            if self.cfg.white_bg_enable:
                n_edit += 1
            if self.cfg.watermark_enable:
                n_edit += 1
            est = n_edit * edit_price + 0.01
            self.pv_cost.value = f"预计费用：约 ¥{est:.2f}（图片 {n_edit} 次 × ¥{edit_price:.2f} + 文本约 ¥0.01）"

            # 风险
            risks = []
            if not imgs:
                risks.append("没有图片，无法发布")
            if len(title) < 20:
                risks.append("标题过短，建议补充关键词")
            if not (self.cfg.template_snapshot or "").strip() not in ("", "{}"):
                risks.append("未选择模板：类目/运费不会被自动填写")
            if not (self.cfg.api_key or "").strip():
                risks.append("未配置 API Key")
            if self.cfg.watermark and not Path(str(self.cfg.watermark)).exists():
                risks.append(f"水印文件不存在：{self.cfg.watermark}")
            self.pv_risks.controls = ([ft.Text(f"⚠ {r}", size=11, color="#FFB020") for r in risks]
                                      or [ft.Text("✓ 未发现明显风险", size=11, color=ACCENT_2)])
            try:
                self.page.update()
            except Exception:
                pass

        self.pv_dd.on_change = _render_preview

        def execute(e):
            pid = self.pv_dd.value
            prod = next((p for p in self._pv_products if str(p.get("id")) == str(pid)), None)
            if not prod:
                self.toast("请先载入并选择产品", False); return
            self._switch(10)   # 跳日志页
            self._log(f"预览执行：{pid}")
            def _run():
                try:
                    ck = self._cookies()
                    dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                    ws = self.ws_endpoint or ""
                    if not ws:
                        from ..integrations.browser import ensure_browser
                        ws = ensure_browser(self.cfg).get("ws", "")
                        self.ws_endpoint = ws
                    r = Publisher(self.cfg, dxm=dxm, ws_endpoint=ws, scheduler=getattr(self, 'scheduler', None)).run(prod, dry_run=not self.cfg.auto_save_products)
                    for line in r.log().splitlines():
                        self._log("   " + line)
                except Exception as ex:
                    self._log(f"执行失败：{ex}", "ERROR")
            threading.Thread(target=_run, daemon=True).start()

        card1 = card(ft.Column([
            h1("产品预览", 15),
            muted("选定一个草稿产品，先看清楚「长什么样、要做什么、花多少钱、有什么风险」再执行"),
            ft.Row([self.pv_dd,
                    ft.ElevatedButton("载入草稿", icon=ft.Icons.REFRESH, bgcolor=CARD_2, color=TEXT, on_click=load),
                    ft.ElevatedButton("执行这一个", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT, color="white", on_click=execute)],
                   spacing=10, wrap=True),
            self.pv_status], spacing=8))

        card2 = card(ft.Column([self.pv_title, self.pv_meta,
                                ft.Divider(height=1, color=CARD_2),
                                ft.Text("主图", size=11, color=MUTED), self.pv_images,
                                ft.Divider(height=1, color=CARD_2),
                                ft.Text("将执行的动作", size=11, color=MUTED), self.pv_actions,
                                self.pv_cost,
                                ft.Text("风险检查", size=11, color=MUTED), self.pv_risks], spacing=8))

        return [h1("预览"), card1, card2]

    def _p_logs(self):
        self._current = 10
        self.logs_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=8)
        self.logs_progress_text = ft.Text("空闲", size=12, color=MUTED)
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
            try:
                outdir = Path(self.cfg.video_output_dir) if self.cfg.video_output_dir else (Path.home() / "Movies" / "AuroraList")
                outdir.mkdir(parents=True, exist_ok=True)
                out = outdir / f"carousel-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
                generate_carousel_video(
                    list(self.picked), out,
                    slot_seconds=float(self.cfg.video_slot_seconds or 2.5),
                    transition_seconds=float(self.cfg.video_transition_seconds or 0.5),
                    size=str(self.cfg.video_size or "1080x1080"),
                    cover=bool(self.cfg.video_cover),
                    fade_edges=bool(self.cfg.video_fade_edges),
                )
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
            jobs = self.scheduler.pending_jobs() if hasattr(self, "scheduler") else []
        except Exception:
            jobs = []

        def cancel_job(e, job):
            try:
                n = self.scheduler.cancel(str(job.product_id))
                self._log(f"定时任务已取消：{job.product_id}（{n} 条）")
                self.toast("已取消定时发布")
            except Exception as ex:
                self.toast(f"取消失败：{ex}", False)
            self._switch(10)

        def publish_now(e, job):
            try:
                self.scheduler.cancel(str(job.product_id))
            except Exception:
                pass
            threading.Thread(target=self._publish_scheduled_now, args=(job,), daemon=True).start()

        sched_rows = []
        for j in jobs[:8]:
            remain = max(0, int(j.run_at - time.time()))
            sched_rows.append(ft.Row([
                ft.Text(str(j.product_id), size=11, color=TEXT, width=110),
                ft.Text(time.strftime("%m-%d %H:%M", time.localtime(j.run_at)), size=11, color="#6EC6FF", width=96),
                ft.Text(f"{remain // 60} 分钟后" if remain >= 60 else ("即将执行" if remain else "待执行"),
                        size=10, color=MUTED, width=76),
                ft.Text((j.title or "")[:24], size=11, color=MUTED, expand=True),
                ft.TextButton("立即发布", on_click=lambda e, _j=j: publish_now(e, _j)),
                ft.TextButton("取消", on_click=lambda e, _j=j: cancel_job(e, _j)),
            ], spacing=6))
        sched_card = card(ft.Column([
            ft.Row([h1("定时发布队列", 14),
                    ft.Text(f"{len(jobs)} 条待执行", size=11, color=MUTED),
                    ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda e: self._switch(10))], spacing=10),
            muted("到点后自动在「待发布」列表里批量发布；商品本身在保存阶段已写入店小秘"),
            *(sched_rows or [ft.Text("暂无定时任务", size=11, color=MUTED)]),
        ], spacing=6))
        prog_card_logs = card(ft.Column([h1("实时进度", 15), self.logs_progress_text, self.logs_progress], spacing=8))
        return [prog_card_logs, h1("任务与日志"), bar, sched_card,
                card(ft.Column([self.log_table], scroll=ft.ScrollMode.AUTO, height=360))]

    def _subscribe_progress(self):
        try:
            from ..services.progress import BUS
            BUS.subscribe(self._on_progress)
        except Exception:
            pass

    def _on_progress(self, ev):
        """总线事件 → 概览卡片实时刷新（后台线程调用）。"""
        try:
            if getattr(self, "ov_progress", None) is None:
                return
            from ..services.progress import BUS
            done, total, cost, used = BUS.progress()
            if total:
                self.ov_progress.value = min(1.0, done / max(1, total))
                self.ov_progress_text.value = f"步骤 {done}/{total} · {used:.1f}s · ¥{cost:.3f}"
            # 同步日志页进度
            if getattr(self, "logs_progress", None) is not None:
                if total:
                    self.logs_progress.value = min(1.0, done / max(1, total))
                    self.logs_progress_text.value = f"步骤 {done}/{total} · {used:.1f}s · 累计 ¥{cost:.3f}"
            # 把结构化事件也写进「任务与日志」表格
            try:
                if not hasattr(self, "log_rows"):
                    self.log_rows = []
                self.log_rows.append((time.strftime("%H:%M:%S", time.localtime(ev.ts)), ev.level, ev.text()))
                self.log_rows = self.log_rows[-1000:]
                if getattr(self, "log_table", None) is not None:
                    self._refresh_log_table()
            except Exception:
                pass
            self.ov_recent.controls = [
                ft.Text(f"{time.strftime('%H:%M:%S', time.localtime(e.ts))} {e.text()}", size=10,
                        color=self._log_color(e.level), selectable=True)
                for e in BUS.snapshot(4)]
            self.page.update()
        except Exception:
            pass

    def _open_log_panel(self):
        """全局日志侧栏：任意页面可唤出，实时刷新。"""
        if getattr(self, "log_panel_open", False):
            return
        self.log_panel_open = True
        self.log_panel_level = ft.Dropdown(width=110, value="全部", bgcolor=CARD_2, color=TEXT, border_color=CARD_2,
                                           options=[ft.dropdown.Option(x) for x in ("全部", "INFO", "WARN", "ERROR")],
                                           on_change=lambda e: self._render_log_panel())
        self.log_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        self.log_progress_text = ft.Text("空闲", size=11, color=MUTED)
        self.log_panel_list = ft.Column(spacing=2, height=360, scroll=ft.ScrollMode.AUTO, auto_scroll=True)
        self.log_panel_list.controls = [ft.Text(x, size=11, color=self._log_color(lv), selectable=True)
                                        for _, lv, x in self._log_panel_items()]
        self.log_panel_dlg = ft.AlertDialog(
            title=ft.Row([ft.Text("实时进度"), self.log_panel_level,
                          ft.TextButton("清空", on_click=lambda e: self._clear_logs()),
                          ft.TextButton("导出", on_click=lambda e: self._export_logs())], spacing=12),
            content=ft.Container(content=ft.Column([self.log_progress, self.log_progress_text,
                                                    ft.Divider(height=1, color=CARD_2),
                                                    self.log_panel_list], spacing=6, tight=True), width=820),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_log_panel())])
        try:
            self.page.dialog = self.log_panel_dlg
            self.log_panel_dlg.open = True
            self.page.update()
        except Exception:
            pass
        try:
            from ..services.progress import BUS
            from ..api import ApiClient as _AC
            rows_p = _AC(self.cfg.api_base, self.cfg.api_key, 15).pricing()
            BUS.set_prices({r["model"]: r["per_call"] for r in rows_p if r.get("per_call")})
        except Exception:
            pass
        threading.Thread(target=self._log_panel_loop, daemon=True).start()

    @staticmethod
    def _log_color(lv: str) -> str:
        return {"ERROR": "#FF6B6B", "WARN": "#FFB020"}.get(str(lv).upper(), MUTED)

    def _log_panel_items(self) -> list[tuple]:
        rows = getattr(self, "log_rows", [])[-300:]
        lv = getattr(self, "log_panel_level", None)
        want = (lv.value if lv is not None else "全部") or "全部"
        if want != "全部":
            rows = [r for r in rows if str(r[1]).upper() == want]
        return rows

    def _log_panel_loop(self):
        while getattr(self, "log_panel_open", False):
            try:
                self._render_log_panel()
            except Exception:
                break
            time.sleep(2.0)

    def _render_log_panel(self):
        if getattr(self, "log_panel_list", None) is None:
            return
        from ..services.progress import BUS
        evs = BUS.snapshot(400)
        want = (self.log_panel_level.value if getattr(self, "log_panel_level", None) is not None else "全部") or "全部"
        if evs:
            rows = []
            for e in evs:
                if want == "ERROR" and e.level != "ERROR":
                    continue
                if want == "WARN" and e.level not in ("WARN", "ERROR"):
                    continue
                if want == "INFO" and e.level not in ("INFO", "OK"):
                    continue
                ts = time.strftime("%H:%M:%S", time.localtime(e.ts))
                rows.append(ft.Text(f"{ts}  {e.text()}", size=11,
                                    color=self._log_color(e.level), selectable=True))
            self.log_panel_list.controls = rows or [ft.Text("暂无匹配日志", size=11, color=MUTED)]
        else:
            self.log_panel_list.controls = [ft.Text(f"{t} [{lv}] {m}", size=11, color=self._log_color(lv), selectable=True)
                                            for t, lv, m in self._log_panel_items()]
        done, total, cost, used = BUS.progress()
        if total:
            self.log_progress.value = min(1.0, done / max(1, total))
            self.log_progress_text.value = f"步骤 {done}/{total} · 用时 {used:.1f}s · 累计花费 ¥{cost:.3f}"
        else:
            self.log_progress.value = 0
            self.log_progress_text.value = f"空闲 · 累计花费 ¥{cost:.3f}" if cost else "空闲"
        try:
            self.page.update()
        except Exception:
            pass

    def _close_log_panel(self):
        self.log_panel_open = False
        try:
            self.log_panel_dlg.open = False
            self.page.update()
        except Exception:
            pass

    def _clear_logs(self):
        getattr(self, "log_rows", []).clear()
        self._render_log_panel()

    def _export_logs(self):
        from pathlib import Path as _P
        out = _P.home() / "Downloads" / f"aurora-logs-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(f"{t} [{lv}] {m}" for t, lv, m in getattr(self, "log_rows", [])), encoding="utf-8")
        self.toast(f"已导出：{out}")

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

        self.price_list = ft.Column(spacing=3)
        self._load_prices()
        # ---- 模型选择（按类型分开，显示价格） ----
        self.model_dd = {}
        self.model_cost_text = ft.Text("", size=12, color=ACCENT_2)
        self.model_status = ft.Text("", size=11, color=MUTED)

        def _build_model_card():
            dd = {}
            for kind, label in (("image_edit", "图片编辑模型（白底/去水印/精修）"),
                                ("image_gen", "图片生成模型（文生图）"),
                                ("text", "文本模型（标题/描述/SKU）"),
                                ("vision", "视觉模型（读图审查）")):
                dd[kind] = ft.Dropdown(label=label, width=430, bgcolor=CARD_2, border_color=CARD_2,
                                       color=TEXT, options=[])
                self.model_dd[kind] = dd[kind]
            return card(ft.Column([
                h1("模型选择", 15),
                muted("按类型自选模型，价格实时取自你的 API 站点；改完自动保存"),
                dd["image_edit"], dd["image_gen"], dd["text"], dd["vision"],
                ft.Row([ft.ElevatedButton("刷新模型与价格", icon=ft.Icons.REFRESH, bgcolor=CARD_2, color=TEXT,
                                          on_click=lambda e: self._load_models()),
                        ft.ElevatedButton("测试图片模型（会产生一次费用）", icon=ft.Icons.SCIENCE,
                                          bgcolor="#F0A020", color="#1A1200",
                                          on_click=lambda e: threading.Thread(target=self._test_image_model, daemon=True).start())],
                       spacing=10, wrap=True),
                self.model_cost_text, self.model_status], spacing=8))

        model_card = _build_model_card()
        self._load_models()

        self.diag_list = ft.Column(spacing=4)
        diag_card = card(ft.Column([
            h1("系统诊断", 15),
            muted("一键检查 API Key、站点、浏览器、店小秘、ffmpeg、磁盘"),
            ft.Row([ft.ElevatedButton("开始诊断", icon=ft.Icons.HEALTH_AND_SAFETY, bgcolor=ACCENT, color="white",
                                      on_click=lambda e: threading.Thread(target=self._run_diag, daemon=True).start()),
                    ft.ElevatedButton("全接口冒烟（约 ¥0.4）", icon=ft.Icons.WIFI_TETHERING, bgcolor="#F0A020", color="#1A1200",
                                      on_click=lambda e: self._smoke_confirm())], spacing=10, wrap=True),
            self.diag_list], spacing=8))

        price_card = card(ft.Column([
            h1("模型与价格", 15),
            muted("每次生成/调用按下面价格从你的额度扣除"),
            self.price_list,
            ft.Row([ft.ElevatedButton("刷新价格", icon=ft.Icons.REFRESH, on_click=lambda e: self._load_prices())]),
        ], spacing=8))

        return [view[0], model_card, recharge_card, diag_card, price_card, inst_card, upd] + view[1:]

    def _load_models(self):
        """从 /api/pricing 拉取模型，按类型分到 4 个下拉，并标注价格。"""
        try:
            from ..api import ApiClient
            rows = ApiClient(self.cfg.api_base, self.cfg.api_key, 20,
                             proxy=self.cfg.proxy_url if getattr(self.cfg, "proxy_enabled", False) else "").pricing()
        except Exception as ex:
            if hasattr(self, "model_status"):
                self.model_status.value = f"价格获取失败：{ex}"
            return
        img = [r for r in rows if r["per_call"] > 0]
        txt = [r for r in rows if r["per_call"] <= 0]
        cur = {"image_edit": self.cfg.model_image_edit, "image_gen": self.cfg.model_image_gen,
               "text": self.cfg.model_text, "vision": self.cfg.model_vision}

        def opts(items, with_price=True):
            out = []
            for r in sorted(items, key=lambda x: x["model"]):
                if str(r["model"]).startswith("gpt-"):
                    continue
                label = f"{r['model']} · ¥{r['per_call']:.3f}/张" if with_price else                         f"{r['model']} · 倍率 {r['ratio']:.2f}"
                out.append(ft.dropdown.Option(r["model"], label))
            return out

        if not hasattr(self, "model_dd"):
            return
        for kind in ("image_edit", "image_gen"):
            dd = self.model_dd.get(kind)
            if dd is None:
                continue
            dd.options = opts(img)
            names = [o.key for o in dd.options]
            dd.value = cur[kind] if cur[kind] in names else (names[0] if names else None)
            dd.on_change = lambda e, _k=kind: self._save_model(_k, e.control.value)
        for kind in ("text", "vision"):
            dd = self.model_dd.get(kind)
            if dd is None:
                continue
            dd.options = opts(txt, with_price=False)
            names = [o.key for o in dd.options]
            dd.value = cur[kind] if cur[kind] in names else (names[0] if names else None)
            dd.on_change = lambda e, _k=kind: self._save_model(_k, e.control.value)

        self.model_cost_text.value = self._model_cost_hint(img)
        self.model_status.value = f"已加载 {len(rows)} 个模型（图片 {len(img)} / 文本 {len(txt)}）"
        try:
            self.page.update()
        except Exception:
            pass

    def _model_cost_hint(self, img_rows=None) -> str:
        try:
            edit_price = next((r["per_call"] for r in (img_rows or [])
                               if r["model"] == self.cfg.model_image_edit), 0)
            gen_price = next((r["per_call"] for r in (img_rows or [])
                              if r["model"] == self.cfg.model_image_gen), 0)
            if not edit_price and not gen_price:
                return f"当前：编辑 {self.cfg.model_image_edit} / 生成 {self.cfg.model_image_gen}"
            return (f"单次预计费用：编辑 ¥{edit_price:.3f}/张 · 生成 ¥{gen_price:.3f}/张"
                    f"（文本/视觉按 token，通常 < ¥0.01/次）")
        except Exception:
            return ""

    def _save_model(self, kind: str, value: str):
        key = {"image_edit": "model_image_edit", "image_gen": "model_image_gen",
               "text": "model_text", "vision": "model_vision"}[kind]
        setattr(self.cfg, key, value)
        save_config(self.cfg)
        self.model_status.value = f"已保存：{key} = {value}"
        try:
            self.model_cost_text.value = self._model_cost_hint(
                [{"model": self.cfg.model_image_edit, "per_call": self._price_of(self.cfg.model_image_edit)},
                 {"model": self.cfg.model_image_gen, "per_call": self._price_of(self.cfg.model_image_gen)}])
            self.page.update()
        except Exception:
            pass
        self.toast(f"已切换到 {value}")

    def _price_of(self, model: str) -> float:
        try:
            from ..api import ApiClient
            rows = ApiClient(self.cfg.api_base, self.cfg.api_key, 20).pricing()
            return next((r["per_call"] for r in rows if r["model"] == model), 0.0)
        except Exception:
            return 0.0

    def _test_image_model(self):
        try:
            import tempfile as _tf
            from pathlib import Path as _P
            from PIL import Image as _Img
            self.model_status.value = "测试中…（调用一次生成模型）"
            self.page.update()
            d = _P(_tf.mkdtemp())
            probe_img = d / "probe.png"
            _Img.new("RGB", (512, 512), (240, 240, 240)).save(probe_img)
            svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
            url = svc.edit(probe_img, "return the same simple image, no changes")
            self.model_status.value = f"图片模型可用：{self.cfg.model_image_edit} → {str(url)[:70]}"
            self.toast("图片模型测试通过")
        except Exception as ex:
            self.model_status.value = f"图片模型不可用：{str(ex)[:120]}"
            self.toast(str(ex), False)
        try:
            self.page.update()
        except Exception:
            pass

    def _smoke_confirm(self):
        def go(e):
            self._close_dialog()
            threading.Thread(target=self._run_smoke, daemon=True).start()
        self.dlg = ft.AlertDialog(
            title=ft.Text("全接口冒烟测试"),
            content=ft.Text("将真实调用：模型列表 / 价格 / 文本 / 视觉 / 文生图 / 图像编辑。\n"
                            "其中文生图+编辑各一次，约花费 ¥0.4（按当前价格）。\n是否继续？"),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dialog()),
                     ft.ElevatedButton("开始", bgcolor=ACCENT, color="white", on_click=go)])
        self.page.dialog = self.dlg
        self.dlg.open = True
        self.page.update()

    def _run_smoke(self):
        import time as _t
        import tempfile as _tf
        from pathlib import Path as _P
        from PIL import Image as _Img
        from ..api import ApiClient
        from ..services import ImageService, model_of

        rows = []

        def add(name, ok, msg=""):
            color = ACCENT_2 if ok else "#FF6B6B"
            icon = ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE
            rows.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                ft.Text(name, size=12, color=TEXT, width=150),
                                ft.Text(msg, size=11, color=MUTED, expand=True)], spacing=8))
            self.diag_list.controls = list(rows)
            try:
                self.page.update()
            except Exception:
                pass

        client = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout,
                           proxy=self.cfg.proxy_url if getattr(self.cfg, "proxy_enabled", False) else "")
        # 1 模型列表
        t0 = _t.time()
        try:
            ms = client.models()
            add("模型列表", True, f"{len(ms)} 个 · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("模型列表", False, str(ex)[:90])
        # 2 价格
        t0 = _t.time()
        try:
            pr = client.pricing()
            add("价格接口", bool(pr), f"{len(pr)} 个 · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("价格接口", False, str(ex)[:90])
        # 3 文本
        t0 = _t.time()
        try:
            out = client.chat(model_of(self.cfg, "text"), [{"role": "user", "content": "回复 OK"}], max_tokens=8)
            add("文本对话", bool(out), f"{model_of(self.cfg,'text')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("文本对话", False, str(ex)[:90])
        # 4 视觉
        t0 = _t.time()
        try:
            out = client.chat(model_of(self.cfg, "vision"), [{"role": "user", "content": [
                {"type": "text", "text": "图里是什么颜色？两个字"},
                {"type": "image_url", "image_url": {"url": "https://sc04.alicdn.com/kf/H36d2c1e2529d4ab18cab919a38f33e25D.jpg"}}]}],
                max_tokens=12)
            add("视觉读图", bool(out), f"{model_of(self.cfg,'vision')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("视觉读图", False, str(ex)[:90])
        # 5 文生图
        t0 = _t.time()
        try:
            url = client.images_generate(model_of(self.cfg, "image_gen"), "a simple white cube on gray background", "1024x1024", 1)
            add("文生图", bool(url), f"{model_of(self.cfg,'image_gen')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("文生图", False, str(ex)[:90])
        # 6 图像编辑
        t0 = _t.time()
        try:
            d = _P(_tf.mkdtemp())
            probe = d / "p.png"
            _Img.new("RGB", (512, 512), (230, 230, 230)).save(probe)
            svc = ImageService(client, self.cfg)
            url = svc.edit(probe, "keep the image unchanged")
            add("图像编辑", bool(url), f"{model_of(self.cfg,'image_edit')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("图像编辑", False, str(ex)[:90])

        ok_n = sum(1 for r in rows if any(getattr(c, "color", "") == ACCENT_2 for c in r.controls))
        self._log(f"全接口冒烟：{ok_n}/{len(rows)} 通过")

    def _run_diag(self):
        rows = []

        def add(name, ok, msg="", warn=False):
            color = "#FFB020" if warn else (ACCENT_2 if ok else "#FF6B6B")
            icon = ft.Icons.WARNING_AMBER if warn else (ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE)
            rows.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                ft.Text(name, size=12, color=TEXT, width=110),
                                ft.Text(msg, size=11, color=MUTED, expand=True)], spacing=8))
            self.diag_list.controls = list(rows)
            try:
                self.page.update()
            except Exception:
                pass

        # 1) API 站点
        if not (self.cfg.api_key or "").strip():
            add("API 站点", False, "未配置 API Key（去「设置 → API Key」填写）")
        try:
            if not (self.cfg.api_key or "").strip():
                raise RuntimeError("skip")
            from ..api import ApiClient
            c = ApiClient(self.cfg.api_base, self.cfg.api_key, 20,
                          proxy=self.cfg.proxy_url if getattr(self.cfg, "proxy_enabled", False) else "")
            ms = c.models()
            add("API 站点", True, f"{self.cfg.api_base} · {len(ms)} 个模型")
        except Exception as ex:
            if str(ex) != "skip":
                add("API 站点", False, str(ex)[:90])

        # 2) 价格接口
        try:
            from ..api import ApiClient as _AC
            from ..services import client_from_config
            rows_p = client_from_config(self.cfg, 20).pricing()
            add("价格接口", bool(rows_p), f"{len(rows_p)} 个模型有价格")
        except Exception as ex:
            add("价格接口", False, str(ex)[:90])

        # 3) 浏览器（CDP）
        try:
            import requests as _rq
            port = getattr(self.cfg, "cdp_port", 9333) or 9333
            r = _rq.get(f"http://127.0.0.1:{port}/json/version", timeout=5)
            add("浏览器 CDP", r.status_code == 200, f"端口 {port} 可用")
        except Exception as ex:
            add("浏览器 CDP", False, f"端口不可用：{str(ex)[:60]}", warn=True)

        # 4) 店小秘
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            ok = dxm.check_login()
            add("店小秘登录", ok, "已登录" if ok else "未登录或 Cookie 过期")
        except Exception as ex:
            add("店小秘登录", False, str(ex)[:80], warn=True)

        # 5) ffmpeg
        try:
            info = video_probe()
            add("ffmpeg", info["available"], info.get("version", "")[:60] or "未安装", warn=not info["available"])
        except Exception as ex:
            add("ffmpeg", False, str(ex)[:60], warn=True)

        # 6) 磁盘
        try:
            import shutil as _sh
            total, used, free = _sh.disk_usage(str(Path.home()))
            gb = free / (1024 ** 3)
            add("磁盘空间", gb > 2, f"剩余 {gb:.1f} GB", warn=gb <= 2)
        except Exception as ex:
            add("磁盘空间", False, str(ex)[:60], warn=True)

        ok_n = sum(1 for r in rows if any(getattr(c, "color", "") == ACCENT_2 for c in r.controls))
        self._log(f"系统诊断完成：{ok_n}/{len(rows)} 项正常")

    def _load_prices(self):
        try:
            from ..api import ApiClient
            rows = ApiClient(self.cfg.api_base, self.cfg.api_key, 30).pricing()
            alias = {"gpt-6-luna", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
                     "gpt-image-2.5-flare", "gpt-image-2.5-flare-vip", "gpt-image-2.0"}
            usage = {
                "z-image-turbo": "文生图（主图生成）",
                "qwen-image-2.1-turbo": "图像编辑 / 多图融合",
                "qwen-image-3.0": "高质量文生图",
                "qwen-plus": "标题 / 描述 / SKU 文本",
                "qwen-turbo": "轻量文本",
                "qwen3-vl-plus": "图片审查 / 读图估重",
                "deepseek-chat": "备用文本",
                "deepseek-reasoner": "深度推理（备用）",
            }
            items = []
            for r in sorted(rows, key=lambda x: x["model"]):
                if r["model"] in alias:
                    continue
                if r["per_call"] > 0:
                    price = f"¥{r['per_call']:.3f} / 张"
                else:
                    price = f"按 token（倍率 {r['ratio']:.2f}）"
                items.append(ft.Row([
                    ft.Text(r["model"], size=12, color=TEXT, width=190),
                    ft.Text(usage.get(r["model"], "-"), size=11, color=MUTED, width=200),
                    ft.Text(price, size=12, color=ACCENT_2),
                ], spacing=12))
            if items:
                items.append(ft.Text("＊ 旧版模型名（gpt-6-luna、gpt-image-2.5-flare 等）已自动映射到对应模型，价格相同",
                                     size=10, color=MUTED))
            self.price_list.controls = items or [ft.Text("价格获取失败", size=12, color="#FFB020")]
        except Exception as ex:
            self.price_list.controls = [ft.Text(f"价格获取失败：{ex}", size=12, color="#FF6B6B")]
        try:
            self.page.update()
        except Exception:
            pass
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
        self.license_state = res
        self.license_text.value = res["message"]
        self.license_text.color = ACCENT_2 if res["ok"] else "#FF6B6B"
        self._apply_license_state()
        try:
            self.page.update()
        except Exception:
            pass

    def _test(self):
        t0 = time.time()
        try:
            models = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout).models()
            self.api_state = {"ok": True, "detail": f"{len(models)} 个模型",
                              "lat": round(time.time() - t0, 1), "ts": time.time()}
            self._apply_api_state()
            self.toast("连接成功")
        except Exception as e:
            self.api_state = {"ok": False, "detail": str(e)[:70],
                              "lat": round(time.time() - t0, 1), "ts": time.time()}
            self._apply_api_state()
            self.toast(str(e), False)
        self.page.update()

    def _do_image(self, kind):
        svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
        self.img_results.controls = []
        runner = BatchRunner(self.cfg.concurrency, retries=int(self.cfg.retry_max or 1), retry_delay=float(self.cfg.retry_delay or 2.0))

        _prompt = ""
        if kind == "edit":
            _pf = getattr(self, "img_quick_prompt", None)
            _prompt = str(getattr(_pf, "value", "") or "").strip() or (self.cfg.edit_prompt or "clean product photo")

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), _prompt)

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            self.img_results.controls.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                                     ft.Text(res.value if res.ok else res.error, size=11,
                                                             color=MUTED, selectable=True)], spacing=8))
            self.page.update()

        targets = self._wb_selected_paths() if hasattr(self, "_wb_selected_paths") else list(self.picked)
        results = runner.run(list(targets), job, done)
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
        txt = str(msg)
        if any(k in txt for k in ("额度", "余额", "quota", "insufficient", "欠费", "billing")):
            try:
                def go(e):
                    self._close_dialog()
                    try:
                        self.page.launch_url(self.cfg.api_base or "https://api.moneyfly.club")
                    except Exception:
                        pass
                self.dlg = ft.AlertDialog(
                    title=ft.Text("额度不足"),
                    content=ft.Text(f"本次调用被拒绝：{txt[:160]}\n\n当前价格参考：\n"
                                    f"· 图片编辑 {self.cfg.model_image_edit}（¥{self._price_of(self.cfg.model_image_edit):.3f}/张）\n"
                                    f"· 图片生成 {self.cfg.model_image_gen}（¥{self._price_of(self.cfg.model_image_gen):.3f}/张）"),
                    actions=[ft.TextButton("稍后", on_click=lambda e: self._close_dialog()),
                             ft.ElevatedButton("去充值", bgcolor="#F0A020", color="#1A1200", on_click=go)])
                self.page.dialog = self.dlg
                self.dlg.open = True
                self.page.update()
            except Exception:
                self.toast("额度不足，请去站点充值", False)

    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self.page.update()


def run():
    ft.app(target=lambda page: App(page), assets_dir="assets")
