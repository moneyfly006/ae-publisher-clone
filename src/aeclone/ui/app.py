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
from ..services import BatchRunner, ImageService, TextService, model_of
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


def _tpl_lib_load(raw) -> dict:
    """模板库统一为 {name: record}；兼容旧版 list。"""
    import json as _j
    try:
        data = _j.loads(raw or "{}")
    except Exception:
        data = {}
    if isinstance(data, list):
        out = {}
        for i, x in enumerate(data):
            if isinstance(x, dict):
                out[str(x.get("name") or f"模板{i+1}")] = x
        return out
    return data if isinstance(data, dict) else {}


def _tpl_lib_dump(lib: dict) -> str:
    import json as _j
    return _j.dumps(lib or {}, ensure_ascii=False)


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
        ("上传", [Field("upload_full_cid", "店小秘相册分类 ID（可留空 = 未分类）")]),
        ("定时发布", [Field("schedule_publish", "启用定时发布", "bool"),
                      Field("schedule_time", "发布时间（如 2026-11-12 09:00）")]),
        ("套装", [Field("combo_enable", "生成套装 SKU", "bool"),
                  Field("combo_prefix", "套装前缀"), Field("combo_multiplier", "套装倍率")]),
        ("水印叠加", [Field("overlay_watermark_enable", "叠加水印到主图", "bool"),
                      Field("overlay_position", "位置（bottom-right/center…）"),
                      Field("overlay_opacity", "透明度 0-1")]),
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
        ("总开关", [
            Field("ai_image_enable", "启用 AI 图片处理（总开关）", "bool"),
            Field("image_concurrency", "单产品图片并发数（1-8，越大越快）"),
        ]),
        ("主图", [
            Field("main_image_generate_enable", "AI 生成主图（文生图）", "bool"),
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
            Field("img_main_edit", "主图精细编辑（用通用编辑提示词）", "bool"),
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
        ]),
        ("通用", [
            Field("overlay_watermark_enable", "主图叠加水印（发布时）", "bool"),
            Field("four_in_one", "四合一拼图", "bool"),
            Field("four_in_one_position", "四合一位置", "select", ["append", "first"]),
            Field("pad_main_images_from_extra", "主图不足用描述图补", "bool"),
            Field("main_pic_count", "主图目标数量"),
        ]),
        ("主图视频", [
            Field("generate_main_video", "发布时生成主图视频", "bool"),
            Field("upload_main_video", "保存时上传主图视频", "bool"),
            Field("use_template_video", "使用模板视频（需填路径）", "bool"),
            Field("template_video_path", "模板视频路径"),
            Field("overwrite_existing_video", "覆盖产品已有视频", "bool"),
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
                  Field("price_multiplier", "价格倍率（如 1.35）"),
                  Field("price_fixed_add", "固定加价（元）"),
                  Field("regional_pricing", "启用区域倍率", "bool")]),
        ("成本", [Field("goods_cost", "商品成本（元）"),
                  Field("platform_commission", "平台佣金 %"),
                  Field("sales_profit", "期望利润 %"),
                  Field("default_express_price", "默认运费（包邮模式用）")]),
        ("阶梯", [Field("tuoguan_price_tiered_enabled", "启用阶梯倍率（按成本区间）", "bool")]),
        ("批发", [Field("wholesale_enable", "启用批发价", "bool")]),
    ]


def schema_variant():
    return []


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


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self._ui_lock = threading.RLock()
        self._building_page = False
        self._update_err_ts = 0.0
        self._log_file_lock = threading.Lock()
        self._browser_lock = threading.RLock()
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
        # 多开时窗口错开，避免完全叠在一起
        try:
            pos = (os.environ.get("AURORA_WINDOW_POS") or "").strip()
            if "," in pos:
                x, y = pos.split(",", 1)
                page.window_left, page.window_top = int(x), int(y)
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
            ("店铺与上架", [("店铺设置", ft.Icons.STORE_OUTLINED, 1), ("属性设置", ft.Icons.TUNE, 2)]),
            ("AI 设置", [("AI图片设置", ft.Icons.AUTO_AWESOME, 3)]),
            ("图片", [("图片工作台", ft.Icons.GRID_VIEW, 9)]),
            ("价格与变种", [("价格设置", ft.Icons.SELL_OUTLINED, 6), ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED, 7)]),
            ("系统", [("设置", ft.Icons.SETTINGS_OUTLINED, 8), ("提示词", ft.Icons.EDIT_NOTE, 11),
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
        # 侧栏导航可滚动：1514×945 及更小的窗口也能点到「提示词 / 任务与日志」
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
        def _detect_cookie():
            try:
                from ..integrations.browser_cookies import describe, resolve
                src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None))
                self.cookie_src = src
                self._cookie_ts = time.time()
                self._log(f"Cookie 源：{describe(src)}" + ("" if src.ok else f"（{src.error[:60]}）"),
                          "INFO" if src.ok else "WARN")
            except Exception as ex:
                self._log(f"Cookie 检测异常：{ex}", "WARN")
        threading.Thread(target=_detect_cookie, daemon=True).start()

        if getattr(self, "tpl_preview_box", None) is None:
            self.tpl_preview_box = ft.Container(content=self._template_preview_row())
        if getattr(self, "tpl_snapshot_text", None) is None:
            self.tpl_snapshot_text = ft.Text("模板参数：未读取", size=12, color=MUTED)
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
                 self._p_logs, self._p_prompts]
        idx = idx % len(pages)
        self._page_idx = idx
        self._building_page = True
        try:
            controls = pages[idx]()
        finally:
            self._building_page = False
        self.content.controls = controls
        self._safe_update()
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
                    self._safe_update()
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
                self._safe_update()
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
        self._safe_update()

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
        # 已有店铺/API 配置的老用户不再弹首次引导
        if getattr(self.cfg, "store_id", "") or getattr(self.cfg, "api_key", ""):
            self.cfg.onboarded = True
            save_config(self.cfg)
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
        self._safe_update()

    def _wz_open(self, e):
        self.wz_status.value = "正在启动 Chrome…"
        self.cfg.browser_type = self.wz_browser.value
        save_config(self.cfg)
        self._safe_update()

        def go():
            try:
                self._connect_browser()
                if not self.ws_endpoint:
                    self.wz_status.value = "浏览器已启动，但未拿到调试端点"
                    self._safe_update(); return
                try:
                    open_url_via_cdp(self.ws_endpoint, "https://www.dianxiaomi.com/web/smt/smtProductList/offline")
                except Exception:
                    pass
                self.wz_status.value = "请在弹出的 Chrome 窗口登录店小秘（一次即可，之后记住登录），登录后自动同步…"
                self._safe_update()
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
                        self._safe_update()
                        return
                    except Exception:
                        continue
                self.wz_status.value = "等待登录超时，请点「读取店铺信息」重试"
            except Exception as ex:
                self.wz_status.value = f"启动失败：{ex}"
            self._safe_update()
        threading.Thread(target=go, daemon=True).start()

    def _wz_source_changed(self):
        self.cfg.template_source = self.wz_source.value
        save_config(self.cfg)
        self.toast(f"模板来源已切换为{'草稿' if self.wz_source.value == 'draft' else '在线'}")

    def _wz_sync(self, e):
        self.wz_status.value = "正在读取 Cookie 与店铺…"
        self._safe_update()

        def go():
            try:
                ck = self._cookies()
                if not ck:
                    self.wz_status.value = "未读取到 Cookie，请确认已在浏览器登录店小秘"
                    self._safe_update(); return
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
            self._safe_update()
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
        self._safe_update()
        self._switch(0)

    def _wz_close(self, mark):
        self.wz_dlg.open = False
        self._safe_update()

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
                self._safe_update()
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
                                ft.Row([ft.ElevatedButton("启动", icon=ft.Icons.ROCKET_LAUNCH, bgcolor=ACCENT, color="white",
                                                          on_click=lambda e: self._start()),
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
            self._safe_update()
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
            self._safe_update()
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
        self._save_api_fields()
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
        # 授权校验：未授权不允许开始；但不要在 UI 线程里阻塞等网络
        try:
            if getattr(self.cfg, "license_enforce", True) and not (self.license_state or {}).get("ok"):
                from ..license import machine_code as _mc
                state = self.license_state or {}
                cached_ok = bool(getattr(self.cfg, "license_cache_ok", False)) and \
                    (time.time() - float(getattr(self.cfg, "license_cache_ts", 0) or 0) < 72 * 3600)
                exp = str(getattr(self.cfg, "license_cache_expiry", "") or "")
                if cached_ok and exp and exp < __import__("datetime").date.today().isoformat():
                    cached_ok = False
                if not state:
                    threading.Thread(target=self._check_license, daemon=True).start()
                    if not cached_ok:
                        self._log("授权检测中，本次启动已阻止（请 1~3 秒后再点启动）", "WARN")
                        self.toast("正在检测授权，请稍后再点启动", False)
                        return
                    self._log("使用上次授权缓存启动，后台复核中…", "INFO")
                else:
                    self._show_license_block(str(state.get("message") or "未授权"))
                    return
        except Exception as ex:
            self._log(f"授权检查异常：{ex}", "WARN")
        if getattr(self, "_running", False):
            self.toast("正在运行，请等当前任务结束或点「停止」", False)
            return
        self._stop_requested = False
        try:
            if getattr(self, "run_status", None) is not None:
                self.run_status.value = "状态：运行中…"
                self.run_status.color = ACCENT_2
        except Exception:
            pass
        errs = self._validate()
        if errs:
            dlg = ft.AlertDialog(title=ft.Text("启动前检查未通过"),
                                 content=ft.Column([ft.Text("• " + e, color="#FF8A8A", size=12) for e in errs],
                                                   spacing=6, height=200, width=460),
                                 actions=[ft.TextButton("去修改", on_click=lambda e: (setattr(dlg, "open", False), self._switch(1), self._safe_update())),
                                          ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
            self.page.dialog = dlg; dlg.open = True; self._safe_update()
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
                ws = self._ensure_browser_session()
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

    def _save_api_fields(self):
        """把设置页 API 卡里的值落盘（输入即保存，启动前也会再存一次）。"""
        try:
            if getattr(self, "api_key_field", None) is not None:
                self.cfg.set("api_key", str(self.api_key_field.value or "").strip())
            if getattr(self, "cfg_concurrency", None) is not None:
                try:
                    self.cfg.set("concurrency", max(1, min(8, int(float(self.cfg_concurrency.value or 3)))))
                except Exception:
                    pass
            if getattr(self, "cfg_timeout", None) is not None:
                try:
                    self.cfg.set("timeout", max(30, int(float(self.cfg_timeout.value or 180))))
                except Exception:
                    pass
            save_config(self.cfg)
        except Exception:
            pass

    def _show_license_block(self, message: str = ""):
        from ..license import machine_code as _mc
        code = _mc()
        st = getattr(self, "license_text", None)
        if st is not None:
            st.value = "未授权"
            st.color = "#FF6B6B"
        dlg = ft.AlertDialog(
            title=ft.Text("未授权，暂时不能开始"),
            content=ft.Column([
                ft.Text("把这台电脑的机器码发给管理员登记后，点「重新检测」即可：", size=12, color=TEXT),
                ft.Text(code, size=16, weight=ft.FontWeight.BOLD, color=ACCENT_2, selectable=True),
                ft.Text(str(message or "")[:120], size=11, color="#FFB020"),
            ], spacing=8, width=460),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                     ft.TextButton("复制机器码", on_click=lambda e: self._copy_machine(code)),
                     ft.ElevatedButton("重新检测", bgcolor=ACCENT, color="white",
                                       on_click=lambda e: (setattr(dlg, "open", False),
                                                           threading.Thread(target=self._check_license,
                                                                            daemon=True).start(),
                                                           self._safe_update()))])
        self.page.dialog = dlg
        dlg.open = True
        self._safe_update()

    def _request_stop(self, e=None):
        self._stop_requested = True
        try:
            if getattr(self, "run_status", None) is not None:
                self.run_status.value = "状态：已请求停止（当前任务结束后停）"
                self.run_status.color = "#FFB020"
        except Exception:
            pass
        self._log("已请求停止：当前产品/任务结束后停止", "WARN")
        self.toast("已请求停止")
        self._safe_update()

    def _copy_machine(self, code: str = ""):
        from ..license import machine_code as _mc
        code = code or _mc()
        try:
            self.page.set_clipboard(code)
            self.toast("机器码已复制")
        except Exception:
            self.toast(f"请手动复制：{code}", False)

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
        """并发处理多个产品；带停止请求与心跳日志，避免看起来“卡死”。"""
        if getattr(self, "_running", False):
            self._log("已有任务在运行，忽略本次启动", "WARN")
            return
        self._running = True
        self._stop_requested = False
        hb_stop = threading.Event()
        state = {"done": 0, "current": "", "total": 0}

        def finish(msg: str = ""):
            self._running = False
            hb_stop.set()
            try:
                if getattr(self, "run_status", None) is not None:
                    self.run_status.value = "状态：" + (msg or "空闲")
                    self.run_status.color = ACCENT_2 if "完成" in (msg or "") else MUTED
            except Exception:
                pass
            try:
                if getattr(self, "ov_progress", None) is not None:
                    self.ov_progress.value = 1
                    self.ov_progress_text.value = msg or "空闲"
                    self._safe_update()
            except Exception:
                pass

        try:
            ck = self._cookies()
            if not getattr(self, "ws_endpoint", ""):
                self._ensure_browser_session(ck)
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            all_prods, _meta = dxm.product_page(1, 100, self.cfg.store_id or None, None)
            blocked = [p for p in all_prods if (p.get("status_type") or "") in ("auditing", "onSelling")]
            self._log(f"共 {len(all_prods)} 个产品，跳过审核中/已发布 {len(blocked)} 个", "INFO")
            limit = int(self.cfg.edit_sum or 0) or 0
            prods = dxm.pending_products(limit=limit, shop_id=self.cfg.store_id or None)
            prods = [x for x in prods if (x.get("status_type") or "") not in
                     ("auditing", "onSelling", "published", "online")]
            self.products = prods
            if not prods:
                self._log("没有可编辑（待发布）的产品", "WARN")
                finish("没有待处理产品")
                return
            state["total"] = len(prods)
            workers = max(1, min(int(getattr(self.cfg, "concurrency", 3) or 3), 8, len(prods)))
            self._log(f"本次待处理 {len(prods)} 个产品，并发 {workers}", "INFO")
            try:
                if getattr(self, "ov_progress", None) is not None:
                    self.ov_progress.value = None
                    self.ov_progress_text.value = f"并行处理中（{workers} 个线程）"
                    self._safe_update()
            except Exception:
                pass

            def heartbeat():
                while not hb_stop.wait(15):
                    self._log(f"运行中：已完成 {state['done']}/{state['total']} · 当前 {state['current'] or '-'}", "INFO")

            threading.Thread(target=heartbeat, daemon=True).start()

            ok_n = fail_n = 0
            lock = threading.Lock()

            def work(item):
                i, prod = item
                nonlocal ok_n, fail_n
                if self._stop_requested:
                    self._log(f"[{i}/{len(prods)}] 已停止，跳过 {prod.get('id')}", "WARN")
                    return
                state["current"] = f"{prod.get('title') or prod.get('id')}"[:40]
                self._log(f"[{i}/{len(prods)}] {prod['id']} {(prod.get('title') or '')[:40]}", "INFO")
                try:
                    local_ck = dict(ck)
                    local_dxm = DianxiaomiClient(local_ck, overrides=self.cfg.endpoint_overrides or {})
                    pub = Publisher(self.cfg, dxm=local_dxm, ws_endpoint=self.ws_endpoint,
                                    scheduler=getattr(self, "scheduler", None))
                    res = pub.run(prod, dry_run=not self.cfg.auto_save_products, manage_progress=False)
                    for line in res.log().splitlines():
                        self._log("   " + line, "INFO" if "✓" in line else "WARN")
                    with lock:
                        ok_n += 1 if res.ok else 0
                        fail_n += 0 if res.ok else 1
                    if self.cfg.auto_save_products and res.ok:
                        history.record("publish", 1, 0, prod["id"])
                except Exception as ex:  # noqa: BLE001
                    with lock:
                        fail_n += 1
                    self._log(f"   失败：{ex}", "ERROR")
                finally:
                    with lock:
                        state["done"] += 1

            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, list(enumerate(prods, 1))))
            self._log(f"完成：成功 {ok_n} / 失败 {fail_n}" + ("（已按请求停止）" if self._stop_requested else ""), "INFO")
            try:
                history.record("pipeline", ok_n, fail_n)
            except Exception:
                pass
            finish(f"完成：成功 {ok_n} / 失败 {fail_n}")
        except Exception as ex:
            self._log(f"运行异常：{ex}", "ERROR")
            finish("运行异常")
        finally:
            self._running = False
            hb_stop.set()

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
            self._safe_update()
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
        self.page.dialog = dlg; dlg.open = True; self._safe_update()

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
        if getattr(self, "tpl_preview_box", None) is None:
            self.tpl_preview_box = ft.Container(content=self._template_preview_row())
        else:
            self.tpl_preview_box.content = self._template_preview_row()
        if getattr(self, "tpl_snapshot_text", None) is None:
            self.tpl_snapshot_text = ft.Text("模板参数：未读取", size=12, color=MUTED)
        try:
            import json as _js
            _snap0 = _js.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            _f0 = _snap0.get("fields") or {}
            if _f0:
                self.tpl_snapshot_text.value = (
                    f"模板参数：类目 {_f0.get('category','-')} · 运费 {_f0.get('freight','-')}"
                    f" · 属性 {len(_snap0.get('attributes') or [])} 项 · 重量 {_f0.get('weight','-')}")
                self.tpl_snapshot_text.color = ACCENT_2
        except Exception:
            pass
        self.tpl_source_dd = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                         value=self.cfg.template_source or "draft",
                                         options=[ft.dropdown.Option("draft", "待发布"), ft.dropdown.Option("online", "在线")],
                                         on_change=lambda e: self._on_tpl_source())
        self.cookie_status = ft.Text(f"Cookie：{ck_text}",
                                     size=11, color=ACCENT_2 if ck_ok else "#FFB020",
                                     selectable=True)
        self.store_status = ft.Text("点击「读取并同步」获取店铺与模板", size=12, color=MUTED)

        def refresh_cookie(e):
            def go():
                ok, text = self._cookie_status_text(refresh=True)
                self.cookie_status.value = f"Cookie：{text}"
                self.cookie_status.color = ACCENT_2 if ok else "#FFB020"
                self.toast(("Cookie 可用：" if ok else "Cookie 仍不可用：") + text, ok)
            threading.Thread(target=go, daemon=True).start()

        def sync(e):
            self.store_status.value = "正在读取…"; self._safe_update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        connect_card = card(ft.Column([
            h1("1 · 连接店小秘", 15),
            muted("优先级：配置里注入过的 Cookie → 已打开的浏览器 → 本机 Chrome/Edge 浏览器库"),
            self.cookie_status,
            ft.Row([ft.ElevatedButton("读取并同步", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=sync),
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=refresh_cookie),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
                    ft.OutlinedButton("启动浏览器（兜底）", icon=ft.Icons.OPEN_IN_BROWSER,
                                      on_click=lambda e: threading.Thread(target=self._connect_browser, daemon=True).start())],
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

        if not getattr(self, "_store_autosynced", False):
            self._store_autosynced = True
            threading.Thread(target=self._sync_shops, daemon=True).start()
        return [h1("店铺设置"), muted("三步：读 Cookie → 选店铺 → 选模板"), connect_card, shop_card, tpl_card]

    def _on_shop_change(self):
        # 同步/重渲染时不要清掉已选模板：只有真的换了店铺才清
        sid_now = str(getattr(self, "shop_dd", None).value if getattr(self, "shop_dd", None) else "")
        if sid_now and sid_now == str(getattr(self.cfg, "store_id", "") or ""):
            return
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
    def _template_library_dialog(self):
        import json as _j
        lib = _tpl_lib_load(self.cfg.template_library)
        list_col = ft.Column(spacing=4, height=280, scroll=ft.ScrollMode.AUTO)

        def persist():
            self.cfg.template_library = _tpl_lib_dump(lib)
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
                lib[rec["name"]] = rec
                self.cfg.template_library = _tpl_lib_dump(lib)
                save_config(self.cfg)
                render()
                self.toast(f"模板已保存到本地：{rec['name']}（{len(vals)} 个字段）")
            except Exception as ex:
                self.toast(f"保存失败：{ex}", False)

        def apply_item(name, item):
            # 先在本地立即生效（不阻塞界面），表单值抓取放后台
            self.cfg.template_snapshot = _j.dumps({"fields": item.get("fields") or {},
                                                   "attributes": item.get("attributes") or []},
                                                  ensure_ascii=False)
            self.cfg.template_product_title = name
            self.cfg.template_product_id = item.get("product_id") or ""
            save_config(self.cfg)
            self._set_tpl_preview(None)
            f = (item.get("fields") or {})
            if getattr(self, "tpl_snapshot_text", None) is not None:
                self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                                f" · 属性 {len(item.get('attributes') or [])} 项")
            dlg.open = False
            self.toast(f"已应用模板：{name}")
            self._safe_update()
            try:
                threading.Thread(target=save_template_local, args=(name, item), daemon=True).start()
            except Exception:
                pass

            def fetch_values():
                try:
                    from ..integrations.editor_automation import read_form_values
                    from ..integrations.browser import ensure_browser
                    ws = self.ws_endpoint or ensure_browser(self.cfg).get("ws", "")
                    self.ws_endpoint = ws
                    vals = read_form_values(ws, str(item.get("product_id") or self.cfg.template_product_id))
                    if vals:
                        self.cfg.template_values = _j.dumps(vals, ensure_ascii=False)
                        save_config(self.cfg)
                        self.toast(f"已抓取模板 {len(vals)} 个字段值")
                except Exception as ex:  # noqa: BLE001
                    self._log(f"模板值抓取失败：{ex}", "WARN")

            threading.Thread(target=fetch_values, daemon=True).start()

        def rename_item(name, item):
            field = ft.TextField(label="新名称", value=name, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def do(e):
                nn = (field.value or "").strip()
                if nn and nn != name:
                    lib[nn] = lib.pop(name, item)
                    persist()
                    self.toast(f"已重命名为 {nn}")
                d2.open = False
                self._safe_update()

            d2 = ft.AlertDialog(title=ft.Text("重命名模板"), content=field,
                                actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(d2)),
                                         ft.ElevatedButton("确定", on_click=do)])
            self.page.dialog = d2; d2.open = True; self._safe_update()

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
            self._safe_update()

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
            self._safe_update()
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
            self._safe_update()

        dlg = ft.AlertDialog(
            title=ft.Text("SKU 图批量替换"),
            content=ft.Column([ft.Text("SKU 名 → 新图片 URL，保存后发布时生效", size=11, color=MUTED),
                               rows,
                               ft.ElevatedButton("新增一行", icon=ft.Icons.ADD,
                                                 on_click=lambda e: add())], spacing=8, width=620),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("保存", bgcolor=ACCENT, color="white", on_click=save)],
        )
        self.page.dialog = dlg; dlg.open = True; self._safe_update()

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
            self._safe_update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        dlg = ft.AlertDialog(title=ft.Text("手动粘贴 Cookie"),
                             content=ft.Column([field], width=560, height=200),
                             actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                                      ft.ElevatedButton("保存并同步", bgcolor=ACCENT, color="white", on_click=save)])
        self.page.dialog = dlg; dlg.open = True; self._safe_update()

    def _set_tpl_preview(self, images=None):
        self.tpl_preview_box.content = self._template_preview_row(images)
        try:
            self._safe_update()
        except Exception:
            pass

    def _read_template(self):
        """读取模板参数：优先走店小秘 API（不用打开浏览器），失败才用已连接的浏览器。"""
        import json as _j
        pid = self.cfg.template_product_id or self.cfg.product_template_id
        if not pid:
            self.toast("请先选择模板产品", False); return
        self.tpl_snapshot_text.value = "正在读取模板参数…"
        self._safe_update()
        snap = None
        # 1) API 读取（不依赖浏览器）
        try:
            dxm = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            snap = dxm.template_snapshot(str(pid))
            if snap and snap.get("fields"):
                self.cfg.template_product_id = str(pid)
                if snap.get("title"):
                    self.cfg.template_product_title = snap["title"]
                self.cfg.template_snapshot = _j.dumps({"fields": snap.get("fields") or {},
                                                       "attributes": snap.get("attributes") or []},
                                                      ensure_ascii=False)
                if snap.get("values"):
                    self.cfg.template_values = _j.dumps(snap["values"], ensure_ascii=False)
                save_config(self.cfg)
                f = snap["fields"]
                self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                                f" · 属性 {len(snap.get('attributes') or [])} 项 · 重量 {f.get('weight','-')}")
                self.tpl_snapshot_text.color = ACCENT_2
                self._set_tpl_preview()
                self._log(f"模板参数已读取（API）：{pid}")
                self._safe_update()
                return
        except Exception as ex:  # noqa: BLE001
            self._log(f"模板 API 读取失败：{str(ex)[:80]}，尝试浏览器", "WARN")
        # 2) 已连接的浏览器（不主动打开浏览器）
        if self.ws_endpoint:
            try:
                from ..integrations.template_reader import read_snapshot
                snap = read_snapshot(self.ws_endpoint, str(pid))
                if snap and snap.fields:
                    self.cfg.template_product_id = str(pid)
                    self.cfg.template_product_title = snap.title
                    self.cfg.template_snapshot = snap.to_json()
                    save_config(self.cfg)
                    f = snap.fields
                    self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                                    f" · 属性 {len(snap.attributes)} 项 · 重量 {f.get('weight','-')}")
                    self.tpl_snapshot_text.color = ACCENT_2
                    self._set_tpl_preview()
                    self._log(f"模板参数已读取（浏览器）：{pid}")
                    self._safe_update()
                    return
            except Exception as ex:  # noqa: BLE001
                self._log(f"模板浏览器读取失败：{str(ex)[:80]}", "WARN")
        self.tpl_snapshot_text.value = ("模板已选中；参数暂未读取（无需打开店小秘，刊登时会自动带模板）")
        self.tpl_snapshot_text.color = "#FFB020"
        self._set_tpl_preview()
        self._safe_update()

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
        self._safe_update()

    def _ensure_browser_session(self, ck: dict | None = None) -> str:
        """没有 CDP 端点时自动启动浏览器，并注入本机 Cookie（保存到待发布必需）。"""
        with self._browser_lock:
            if getattr(self, "ws_endpoint", ""):
                return self.ws_endpoint
            from ..integrations.browser import ensure_browser
            from ..integrations.cdp import inject_cookies
            if ck is None:
                ck = self._cookies()
            self._log("未连接浏览器，自动启动店小秘调试浏览器…")
            info = ensure_browser(self.cfg)
            ws = str(info.get("ws") or "")
            if not ws:
                raise RuntimeError("浏览器已启动但未拿到 CDP 端点")
            self.ws_endpoint = ws
            try:
                n = inject_cookies(ws, dict(ck or {}))
                self._log(f"浏览器已连接，已注入 Cookie {n // 2} 个" if n else "浏览器已连接（未注入 Cookie）")
            except Exception as ex:  # noqa: BLE001
                self._log(f"Cookie 注入失败：{str(ex)[:80]}（如未登录请点「启动浏览器」登录一次）", "WARN")
                return ws
            return ws

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
        self._safe_update()

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

    def _cookie_status_text(self, refresh: bool = False, allow_cdp: bool = False) -> tuple[bool, str]:
        """给界面显示的 Cookie 状态。

        默认**不读本机 Cookie 库**（macOS 解密可能弹 Keychain，导致界面像卡死），
        只用手动配置/缓存；点「重新检测」时才在后台线程真正读取。
        """
        from ..integrations.browser_cookies import describe, resolve
        cached = getattr(self, "cookie_src", None)
        if not refresh:
            if cached is not None and (time.time() - getattr(self, "_cookie_ts", 0)) < 60:
                return bool(cached.ok and cached.cookies), describe(cached)
            manual = getattr(self.cfg, "manual_cookies", None)
            if isinstance(manual, dict) and manual:
                src = resolve("dianxiaomi.com", manual=manual, ws_endpoint="", use_local=False)
                self.cookie_src = src
                self._cookie_ts = time.time()
                return True, describe(src)
            return False, "未检测（点「重新检测 Cookie」）"
        try:
            src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None),
                          ws_endpoint=self.ws_endpoint if allow_cdp else "")
        except Exception as ex:  # noqa: BLE001
            return False, f"读取异常：{str(ex)[:60]}"
        self.cookie_src = src
        self._cookie_ts = time.time()
        return bool(src.ok and src.cookies), describe(src)

    def _close_dlg(self, dlg):
        dlg.open = False
        self._safe_update()

    def _open_template_picker(self, source: str, on_pick=None):
        """先弹窗、后台拉数据，避免网络慢导致界面“卡死”。"""
        state = {"source": source if source in ("draft", "online") else "draft",
                 "kw": "", "data": {}, "totals": {}, "err": ""}
        list_col = ft.Column(spacing=2, height=420, scroll=ft.ScrollMode.AUTO)
        search = ft.TextField(hint_text="搜索标题或产品ID", prefix_icon=ft.Icons.SEARCH, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        tab_row = ft.Row(spacing=8)
        status = ft.Text("正在读取模板产品…", size=11, color=MUTED)

        def pick(pid, title, images):
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = title
            self.cfg.template_source = state["source"]
            save_config(self.cfg)
            self.toast(f"已选模板：{title[:24]}")
            dlg.open = False
            self._safe_update()
            if on_pick:
                on_pick(pid, title, images)
            threading.Thread(target=self._read_template, daemon=True).start()

        def render():
            items = (state["data"] or {}).get(state["source"], []) or []
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
            list_col.controls = rows or [ft.Text("无匹配结果" if state["data"] else "读取中…", size=12, color=MUTED)]
            data = state["data"] or {}
            totals = state["totals"] or {}
            tab_row.controls = [
                ft.ElevatedButton(f"待发布 {totals.get('editingRequired', len(data.get('draft') or []))}",
                                  bgcolor=ACCENT_2 if state["source"] == "draft" else CARD_2,
                                  color="#04231C" if state["source"] == "draft" else TEXT,
                                  on_click=lambda e: switch("draft")),
                ft.ElevatedButton(f"在线 {totals.get('onSelling', len(data.get('online') or []))}",
                                  bgcolor=ACCENT_2 if state["source"] == "online" else CARD_2,
                                  color="#04231C" if state["source"] == "online" else TEXT,
                                  on_click=lambda e: switch("online")),
            ]
            self._safe_update()

        def switch(src):
            state["source"] = src
            self.cfg.template_source = src
            save_config(self.cfg)
            render()

        def on_search(e):
            state["kw"] = e.control.value or ""
            render()

        search.on_change = on_search

        dlg = ft.AlertDialog(
            title=ft.Text("选择模板产品"),
            content=ft.Column([tab_row, search, status, list_col], spacing=10, width=620),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg
        dlg.open = True
        self._safe_update()
        render()

        def load():
            try:
                ck = self._cookies()
                c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                sid = self.cfg.store_id or None
                state["data"] = {"draft": c.drafts(limit=300, shop_id=sid),
                                 "online": c.online(limit=300, shop_id=sid)}
                state["totals"] = c.counts(sid) if sid else {}
                status.value = "读取完成"
                status.color = ACCENT_2
            except Exception as ex:  # noqa: BLE001
                state["data"] = {}
                status.value = f"读取失败：{str(ex)[:100]}"
                status.color = "#FF6B6B"
            render()

        threading.Thread(target=load, daemon=True).start()

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
            self._safe_update()
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
            try:
                _snap_chk = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            except Exception:
                _snap_chk = {}
            if not (_snap_chk.get("fields") or _snap_chk.get("attributes")):
                self.toast("当前没有模板参数可保存：请先选模板产品并读取参数", False)
                return
            name = self.cfg.template_product_title or "未命名模板"
            lib = _tpl_lib_load(self.cfg.template_library)
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
            lib[name] = rec
            self.cfg.template_library = _tpl_lib_dump(lib)
            save_config(self.cfg)
            self.toast(f"已保存到本地：{name}（{len(rec['values'])} 字段 / {len(rec['attributes'])} 属性）")
        except Exception as ex:
            self.toast(f"保存失败：{ex}", False)

    def _close_dialog(self):
        self.dlg.open = False
        self._safe_update()

    # ---------- 图片空间 / 清理旧图（ISSUES #5）----------
    def _album_client(self):
        return DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})

    def _load_space_info(self):
        from ..services import album_cleanup
        try:
            self._space_info = album_cleanup.space(self._album_client())
        except Exception:
            self._space_info = None

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

        def fmt_time(v) -> str:
            try:
                n = float(v)
                if n > 1e12:
                    n /= 1000.0
                return time.strftime("%Y-%m-%d", time.localtime(n)) if n else "-"
            except Exception:
                return str(v or "-")[:10]

        state = {"plan": None, "sel": set(), "timer": None}
        space_text = ft.Text("图片空间：读取中…", size=12, color=MUTED)
        days = ft.TextField(label="删除多少天前的图片（0=今天之前全部）", value="30", width=260,
                            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        status = ft.Text("先点「扫描」看看有哪些旧图", size=12, color=MUTED)
        result = ft.Column(spacing=2, height=280, scroll=ft.ScrollMode.AUTO)
        select_all = ft.Checkbox(label="全选可删", value=False)
        dlg = None

        def load_space():
            try:
                info = album_cleanup.space(self._album_client())
                space_text.value = "图片空间：" + info.brief
                space_text.color = ACCENT_2
            except Exception as ex:  # noqa: BLE001
                space_text.value = f"图片空间：读取失败（{str(ex)[:60]}）"
                space_text.color = "#FF6B6B"
            self._safe_update()

        def render():
            state["rendering"] = True
            plan = state["plan"]
            if not plan:
                result.controls = []
                state["rendering"] = False
                return
            rows = []
            unlocked = [c for c in plan.candidates if not c.locked]
            for c in plan.candidates[:120]:
                cb = ft.Checkbox(value=(c.id in state["sel"]), disabled=c.locked, width=36)
                if not c.locked:
                    def on_cb(e, _c=c):
                        if e.control.value:
                            state["sel"].add(_c.id)
                        else:
                            state["sel"].discard(_c.id)
                        self._safe_update()
                    cb.on_change = on_cb
                rows.append(ft.Row([
                    cb,
                    ft.Text(fmt_time(c.created), size=10, color=MUTED, width=90),
                    ft.Text(album_cleanup.human_size(c.size), size=10, color=MUTED, width=60),
                    ft.Text("🔒" if c.locked else "", size=10, color="#FFB020", width=20),
                    ft.Text((c.name or c.id)[:52], size=10, color=TEXT, expand=True),
                ], spacing=6))
            result.controls = rows
            select_all.value = bool(unlocked) and all(c.id in state["sel"] for c in unlocked)
            state["rendering"] = False
            self._safe_update()

        def scan_now():
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                status.value = "天数要填数字"; status.color = "#FF6B6B"; self._safe_update(); return
            status.value = "扫描中…"; status.color = MUTED; self._safe_update()
            try:
                plan = album_cleanup.scan(self._album_client(), d)
                state["plan"] = plan
                state["sel"] = set()
                status.value = plan.brief() if plan.scanned else "没有符合条件的图片（可把天数改成 0）"
                status.color = ACCENT_2 if plan.delete_count else "#FFB020"
                render()
            except Exception as ex:  # noqa: BLE001
                status.value = f"扫描失败：{str(ex)[:100]}"; status.color = "#FF6B6B"; self._safe_update()

        def on_select_all(e):
            if state.get("rendering"):
                return
            plan = state["plan"]
            if not plan:
                return
            if e.control.value:
                state["sel"] = {c.id for c in plan.candidates if not c.locked and c.id}
            else:
                state["sel"] = set()
            render()

        select_all.on_change = on_select_all

        def confirm_then(tip: str, action):
            confirm = ft.AlertDialog(
                title=ft.Text("确认删除图片？"),
                content=ft.Text(tip, color=MUTED, size=12),
                actions=[ft.TextButton("取消", on_click=lambda e: (setattr(confirm, "open", False), self._safe_update())),
                         ft.ElevatedButton("确认删除", bgcolor="#D9534F", color="white",
                                           on_click=lambda e: (setattr(confirm, "open", False), self._safe_update(), action()))])
            self.page.dialog = confirm
            confirm.open = True
            self._safe_update()

        def delete_selected():
            ids = list(state["sel"])
            if not ids:
                self.toast("请先勾选要删除的图片", False); return
            def go():
                status.value = "删除中…"; status.color = MUTED; self._safe_update()
                r = album_cleanup.delete_ids(self._album_client(), ids)
                msg = r.get("message") or (f"已删除 {r.get('deleted', 0)} 张"
                                           + (f"，失败 {r.get('failed', 0)} 张" if r.get("failed") else ""))
                if r.get("errors"):
                    msg += " · " + str(r["errors"][0])[:60]
                status.value = msg
                status.color = ACCENT_2 if r.get("ok") else "#FF6B6B"
                self._log("图片清理（选中）：" + msg)
                load_space(); scan_now()
            threading.Thread(target=go, daemon=True).start()

        def delete_range():
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                self.toast("天数要填数字", False); return
            tip = (f"将删除「{d} 天前」上传的所有图片（已锁定的会自动跳过）。\n"
                   f"注意：这个方式不判断图片是否正被商品使用，删除前请确认。\n"
                   f"想更安全就先用「扫描」勾选后再删。")
            def go():
                status.value = "删除中…"; status.color = MUTED; self._safe_update()
                r = album_cleanup.delete_older_than(self._album_client(), d)
                status.value = r.get("message") or ("完成" if r.get("ok") else "失败")
                status.color = ACCENT_2 if r.get("ok") else "#FF6B6B"
                self._log("图片清理（按时间）：" + status.value)
                load_space(); scan_now()
            confirm_then(tip, lambda: threading.Thread(target=go, daemon=True).start())

        def open_album(e):
            self._open_album_page()

        dlg = ft.AlertDialog(
            title=ft.Text("图片空间清理"),
            content=ft.Column([
                space_text,
                muted("支持两种：① 扫描后勾选删除（推荐，可控）② 按时间删除（快，但不判断是否在用）"),
                ft.Row([days, ft.ElevatedButton("扫描", icon=ft.Icons.SEARCH, bgcolor=ACCENT, color="white",
                                                on_click=lambda e: threading.Thread(target=scan_now, daemon=True).start()),
                        ft.ElevatedButton("删除选中", icon=ft.Icons.DELETE, bgcolor="#D9534F", color="white",
                                          on_click=lambda e: delete_selected()),
                        ft.OutlinedButton("按时间删除", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: delete_range())],
                       spacing=8, wrap=True),
                status, select_all, result,
                muted("说明：店小秘的「自动清理未引用图片」接口对当前账号未开放（返回：功能暂未开放），所以这里用勾选/按时间两种可用方式。"),
            ], spacing=8, width=720, height=560),
            actions=[ft.TextButton("打开相册页面", on_click=open_album),
                     ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self.page.dialog = dlg
        dlg.open = True
        self._safe_update()
        threading.Thread(target=load_space, daemon=True).start()
        threading.Thread(target=scan_now, daemon=True).start()

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
            self._safe_update()
            threading.Thread(target=self._search_attr_templates, daemon=True).start()

        attr_card = card(ft.Column([
            h1("属性模板", 15),
            muted("从店小秘属性模板读取「属性名=值」，发布时自动填入编辑器"),
            ft.Row([self.attr_kw,
                    ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, bgcolor=CARD_2, color=TEXT, on_click=attr_search),
                    ft.OutlinedButton("清空已应用属性", on_click=lambda e: self._clear_attrs())], spacing=10),
            self.attr_status, self.attr_list], spacing=8))

        sku_map_card = card(ft.Column([
            h1("SKU 图替换", 15),
            muted("把某个 SKU 的图片换成指定 URL；用表格填写，不用写 JSON"),
            ft.Row([ft.ElevatedButton("打开替换表", icon=ft.Icons.SWAP_HORIZ, bgcolor=CARD_2, color=TEXT,
                                      on_click=lambda e: self._sku_replace_dialog())], spacing=10, wrap=True),
        ], spacing=8))
        return [view[0], self._watermark_card(), self._combo_card(), sku_map_card, attr_card] + view[1:]

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
                self._safe_update()
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
        self.wm_pos = ft.Dropdown(label="水印位置", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                  value=str(getattr(self.cfg, "overlay_position", "bottom-right") or "bottom-right"),
                                  options=[ft.dropdown.Option(x, x) for x in
                                           ("bottom-right", "bottom-left", "top-right", "top-left", "center")])
        self.wm_opacity = ft.TextField(label="透明度 0-1", width=120,
                                       value=str(getattr(self.cfg, "overlay_opacity", "0.7") or "0.7"),
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        def save_wm_pos(e=None):
            self.cfg.set("overlay_position", str(self.wm_pos.value or "bottom-right"))
            save_config(self.cfg)
        def save_wm_opacity(e=None):
            try:
                v = float(self.wm_opacity.value or 0.7)
            except Exception:
                v = 0.7
            self.cfg.set("overlay_opacity", str(max(0.05, min(1.0, v))))
            save_config(self.cfg)
        self.wm_pos.on_change = save_wm_pos
        self.wm_opacity.on_change = save_wm_opacity
        refresh_status()
        return card(ft.Column([
            h1("水印", 15),
            muted("「叠加水印到主图」用的就是这张图（发布时逐张叠加，可同时回传店小秘相册）"),
            self.wm_enable,
            ft.Row([self.wm_pos, self.wm_opacity], spacing=10, wrap=True),
            ft.Row([self.wm_path,
                    ft.ElevatedButton("选择文件", icon=ft.Icons.IMAGE, bgcolor=CARD_2, color=TEXT, on_click=pick_file),
                    ft.OutlinedButton("测试叠加", icon=ft.Icons.SCIENCE,
                                      on_click=lambda e: threading.Thread(target=self._test_watermark, daemon=True).start())],
                   spacing=10, wrap=True),
            ft.Row([self.wm_preview, self.wm_status], spacing=10),
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
            self._safe_update()
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
                self._safe_update()
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

    # ---------- 图片处理：简单规则 + 精细任务（无 JSON）----------
    def _image_jobs_ui(self):
        from ..services.image_jobs import (parse_rules, rows_to_rules, parse_jobs, rows_to_jobs,
                                          migrate, GROUP_LABELS, OP_LABELS)
        from ..services.image_presets import presets_for, get_preset

        # 旧配置（指定编辑/逐图方案/全局开关）一次性迁移
        try:
            rj, jj = migrate(self.cfg, with_global=True)
            if rj != (self.cfg.image_simple_rules or "[]") or jj != (self.cfg.image_ai_jobs or "[]"):
                self.cfg.set("image_simple_rules", rj)
                self.cfg.set("image_ai_jobs", jj)
                save_config(self.cfg)
        except Exception:
            pass

        state = {"group": "main",
                 "rules": parse_rules(self.cfg.image_simple_rules),
                 "jobs": parse_jobs(self.cfg.image_ai_jobs),
                 "timer": None}
        body = ft.Column(spacing=8)
        status = ft.Text("", size=11, color=MUTED)
        tab_row = ft.Row(spacing=8)

        def persist():
            self.cfg.set("image_simple_rules", rows_to_rules(state["rules"]))
            self.cfg.set("image_ai_jobs", rows_to_jobs(state["jobs"]))
            save_config(self.cfg)
            g = state["group"]
            n_rules = len([r for r in state["rules"] if r["group"] == g])
            n_jobs = len([j for j in state["jobs"] if j["group"] == g])
            status.value = f"{GROUP_LABELS[g]}：{n_rules} 条简单规则 · {n_jobs} 条精细任务"
            status.color = ACCENT_2 if (n_rules or n_jobs) else MUTED
            try:
                self._safe_update()
            except Exception:
                pass

        def schedule_persist():
            t = state.get("timer")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            timer = threading.Timer(0.5, persist)
            timer.daemon = True
            state["timer"] = timer
            timer.start()

        # ---------- 简单规则行 ----------
        def rule_row(rule: dict, g: str):
            rng = ft.TextField(label="图片范围（1-5 / 1,3,4）", value=str(rule.get("range") or "1-5"),
                               width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            cbs = {}
            for op in ("white", "watermark", "translate"):
                cbs[op] = ft.Checkbox(label=OP_LABELS[op], value=op in (rule.get("ops") or []))

            def sync(e=None):
                rule["range"] = str(rng.value or "1-5")
                rule["ops"] = [op for op, cb in cbs.items() if cb.value]
                if not rule["ops"]:
                    try:
                        state["rules"].remove(rule)
                    except ValueError:
                        pass
                    render()
                persist()

            rng.on_change = lambda e: schedule_persist()
            for cb in cbs.values():
                cb.on_change = sync

            def remove(e):
                try:
                    state["rules"].remove(rule)
                except ValueError:
                    pass
                render(); persist()

            return ft.Container(
                content=ft.Row([rng, cbs["white"], cbs["watermark"], cbs["translate"],
                                ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                               spacing=8, wrap=True),
                bgcolor=CARD_2, border_radius=10, padding=10)

        # ---------- 精细任务行 ----------
        def job_row(job: dict, g: str):
            idx = ft.Dropdown(label="第几张图", width=110, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              value=str(min(8, max(1, int(job.get("index") or 1)))),
                              options=[ft.dropdown.Option(str(i)) for i in range(1, 9)])
            preset_dd = ft.Dropdown(label="中文预设", width=230, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                    value=str(job.get("preset") or "custom"),
                                    options=[ft.dropdown.Option("custom", "自定义（直接写）")] +
                                            [ft.dropdown.Option(p["key"], p["zh"]) for p in presets_for(g)])
            prompt = ft.TextField(label="这一张图的提示词（可改）", value=str(job.get("prompt") or ""),
                                  multiline=True, min_lines=2, max_lines=4, expand=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            desc = ft.Text("", size=10, color=MUTED)
            mode = ft.Dropdown(label="方式", width=150, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                               value=str(job.get("mode") or "edit"),
                               options=[ft.dropdown.Option("edit", "编辑（用这张图）"),
                                        ft.dropdown.Option("generate", "生成（用素材图）")])
            refs_box = ft.Row(spacing=2)
            insert = ft.Dropdown(label="放哪里", width=130, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                 value=str(job.get("insert") or "first"),
                                 options=[ft.dropdown.Option("first", "插到第 1 张"),
                                          ft.dropdown.Option("last", "放到最后"),
                                          ft.dropdown.Option("replace", "替换这张")])
            count = ft.Dropdown(label="生成数量", width=110, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                value=str(job.get("count") or 1),
                                options=[ft.dropdown.Option(str(i)) for i in (1, 2, 3)])
            refs_state = set(str(job.get("refs") or "").replace("，", ",").replace(" ", "").split(","))
            refs_state.discard("")

            def sync_refs(e=None):
                job["refs"] = ",".join(sorted([x for x in refs_state if x.isdigit()], key=int))
                schedule_persist()

            for i in range(1, 7):
                cb = ft.Checkbox(label=str(i), value=str(i) in refs_state, width=44)

                def on_cb(e, _i=i, _cb=cb):
                    if _cb.value:
                        refs_state.add(str(_i))
                    else:
                        refs_state.discard(str(_i))
                    sync_refs()

                cb.on_change = on_cb
                refs_box.controls.append(cb)

            def sync(e=None):
                try:
                    job["index"] = int(idx.value or 1)
                except Exception:
                    job["index"] = 1
                job["prompt"] = str(prompt.value or "").strip()
                job["mode"] = mode.value or "edit"
                job["insert"] = insert.value or "first"
                try:
                    job["count"] = int(count.value or 1)
                except Exception:
                    job["count"] = 1
                schedule_persist()

            def on_preset(e):
                p = get_preset(g, preset_dd.value)
                if p:
                    prompt.value = p["en"]
                    desc.value = p["desc"]
                    job["prompt"] = p["en"]
                    job["preset"] = p["key"]
                    schedule_persist()
                else:
                    job["preset"] = "custom"
                    schedule_persist()
                try:
                    self._safe_update()
                except Exception:
                    pass

            preset_dd.on_change = on_preset
            idx.on_change = sync
            mode.on_change = sync
            insert.on_change = sync
            count.on_change = sync
            def on_prompt_edit(e):
                # 手改提示词后视为自定义
                job["preset"] = "custom"
                preset_dd.value = "custom"
                sync()
            prompt.on_change = on_prompt_edit

            def remove(e):
                try:
                    state["jobs"].remove(job)
                except ValueError:
                    pass
                render(); persist()

            return ft.Container(
                content=ft.Column([
                    ft.Row([idx, preset_dd, mode, insert, count,
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                           spacing=8, wrap=True),
                    prompt,
                    ft.Row([ft.Text("素材图（多选，和第几张一起交给 AI）:", size=10, color=MUTED), refs_box],
                           spacing=6, wrap=True),
                    desc,
                ], spacing=6),
                bgcolor=CARD_2, border_radius=10, padding=10)

        # ---------- 渲染 ----------
        def render():
            g = state["group"]
            rules = [r for r in state["rules"] if r["group"] == g]
            jobs = [j for j in state["jobs"] if j["group"] == g]

            def add_rule(e=None):
                state["rules"].append({"group": g, "range": "1-3", "ops": ["white"] if g == "main" else ["watermark"]})
                render(); persist()

            def add_job(e=None):
                state["jobs"].append({"group": g, "index": 1, "refs": "", "prompt": "",
                                      "mode": "edit", "count": 1, "insert": "first"})
                render(); persist()

            def quick_all(ops):
                def _run(e):
                    state["rules"].append({"group": g, "range": "1-20", "ops": list(ops)})
                    render(); persist()
                return _run

            rules_card = card(ft.Column([
                h1("简单处理（勾选即可，不用写提示词）", 15),
                muted("白底 / 去水印 / 翻译，按范围批量做；费用低、速度快"),
                ft.Row([ft.ElevatedButton("添加一条", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=add_rule),
                        ft.OutlinedButton("全部白底", on_click=quick_all(["white"])),
                        ft.OutlinedButton("全部去水印", on_click=quick_all(["watermark"])),
                        ft.OutlinedButton("全部翻译", on_click=quick_all(["translate"]))], spacing=8, wrap=True),
                ft.Column([rule_row(r, g) for r in rules] or
                          [ft.Text("还没有简单规则。点「添加一条」，选范围并勾选要做的处理。", size=11, color=MUTED)],
                          spacing=6),
            ], spacing=8))

            jobs_card = card(ft.Column([
                h1("精细处理（给某几张图写提示词）", 15),
                muted("每条 = 第几张图 + 你要的效果（中文预设点一下就有）；素材多选表示「结合这几张图一起交给 AI」"),
                muted("例：第 1 张选「多图合成主图」、素材勾 2,3,4 → 生成的新主图插到第 1 张，原图顺延"),
                ft.Row([ft.ElevatedButton("添加一条", icon=ft.Icons.ADD, bgcolor=ACCENT_2, color="#04231C",
                                          on_click=add_job)], spacing=8, wrap=True),
                ft.Column([job_row(j, g) for j in jobs] or
                          [ft.Text("还没有精细任务。点「添加一条」，选第几张图和中文预设即可。", size=11, color=MUTED)],
                          spacing=6),
            ], spacing=8))

            body.controls = [jobs_card, rules_card]
            for b in tab_row.controls:
                on = getattr(b, "data", "") == g
                b.bgcolor = ACCENT if on else CARD_2
                b.color = "white" if on else TEXT
            try:
                self._safe_update()
            except Exception:
                pass

        def switch(g):
            state["group"] = g
            render()
            persist()

        for key in ("main", "detail", "sku"):
            b = ft.ElevatedButton(GROUP_LABELS[key], bgcolor=CARD_2, color=TEXT,
                                  on_click=lambda e, _g=key: switch(_g))
            b.data = key
            tab_row.controls.append(b)

        render()
        return card(ft.Column([
            h1("图片处理方案", 15),
            muted("先选分组（主图 / 描述图 / SKU 图），再决定「简单处理」还是「精细处理」；两侧可同时用"),
            tab_row,
            body, status,
        ], spacing=10))

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
            self._safe_update()
        except Exception:
            pass

    def _fast_image_mode(self, e=None):
        """老软件式快速模式：只保留白底/去水印/SKU白底 + 标题/详情。"""
        for _k in ("main_image_generate_enable", "img_main_edit", "designated_edit_enable",
                   "translate_main_pic_enable", "translate_detail_pic_enable", "translate_sku_pic",
                   "img_detail_white_bg", "img_detail_remove_wm", "img_detail_edit",
                   "img_sku_remove_wm", "img_sku_edit", "marketing_image_enable",
                   "generate_main_video", "enable_ai_weight",
                   "review_main_img_enable", "review_detail_img_enable", "review_text_enable",
                   "four_in_one", "overlay_watermark_enable"):
            self.cfg.set(_k, False)
        self.cfg.set("ai_image_enable", True)
        self.cfg.set("image_concurrency", 4)
        try:
            self.cfg.set("concurrency", max(4, int(getattr(self.cfg, "concurrency", 4) or 4)))
        except Exception:
            self.cfg.set("concurrency", 4)
        save_config(self.cfg)
        self.toast("已切换快速模式：只保留白底/去水印/SKU白底 + 标题/详情，图片并发 4")
        self._switch(3)

    def _p_ai(self):
        """AI图片设置：按老软件合并成 智能填写/内容审查/简单编辑/通用编辑/批处理。"""
        self._current = 3
        self.field_refs = []
        self._autosave_status = ft.Text("修改即自动保存", size=11, color=MUTED)

        def add_section(title: str, schemas, expanded: bool = False, extra=None):
            controls = []
            for name, fields in schemas:
                c, refs = section(name, fields, self.cfg)
                self.field_refs.extend(refs)
                for f in refs:
                    self._bind_autosave(f)
                controls.append(c)
            if extra is not None:
                controls.append(extra)
            return ft.ExpansionTile(title=ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=TEXT),
                                    controls=controls, initially_expanded=expanded,
                                    icon_color=ACCENT, collapsed_icon_color=MUTED)

        # 图片步骤字段：按老软件拆成「简单编辑 / 通用编辑」
        img_map = {f.key: f for _sec, fs in schema_image() for f in fs}
        simple_keys = [
            "white_bg_enable", "white_background_pic_1", "white_background_pic_2",
            "watermark_enable", "remove_watermark_pic_1", "remove_watermark_pic_2",
            "translate_main_pic_enable", "translate_main_pic_indices",
            "white_background_detail_pic_indices", "img_detail_white_bg", "img_detail_remove_wm",
            "translate_detail_pic_enable", "translate_detail_pic_indices",
            "white_background_sku_pic", "img_sku_white_bg", "img_sku_remove_wm",
            "translate_sku_pic", "translate_image_understanding",
        ]
        general_keys = [
            "ai_image_enable", "image_concurrency",
            "main_image_generate_enable", "main_image_generate_prompt", "main_image_generate_count",
            "main_image_generate_use_refs", "img_main_edit", "designated_edit_enable",
            "designated_edit_indices", "designated_edit_prompt",
            "marketing_image_enable", "marketing_image_count", "marketing_image_prompt",
            "background_gen_sku_prompt", "edit_enable", "edit_prompt",
        ]
        simple = []
        for name, fields in schema_image():
            fs = [f for f in fields if f.key in simple_keys]
            if fs:
                simple.append((name, fs))
        general = []
        for name, fields in schema_image():
            fs = [f for f in fields if f.key in general_keys]
            if fs:
                general.append((name, fs))

        cards = [
            add_section("AI 智能填写", schema_ai(), expanded=True),
            add_section("内容审查", schema_review(), expanded=False),
            add_section("图片简单编辑", simple, expanded=True),
            add_section("图片通用编辑", general, expanded=False, extra=self._image_jobs_ui()),
        ]
        fast_card = card(ft.Column([
            h1("速度 / 功能开关", 15),
            muted("每个 AI 步骤都有独立开关；关掉即跳过。快速模式关闭审查/估重/营销/翻译/视频等耗时项。"),
            ft.Row([ft.ElevatedButton("一键快速模式（推荐）", icon=ft.Icons.BOLT, bgcolor=ACCENT_2,
                                      color="#04231C", on_click=self._fast_image_mode)], spacing=8, wrap=True),
        ], spacing=8))
        batch = card(ft.Column([
            h1("AI 图片批处理", 15),
            muted("对选中的产品/图片单独跑 AI；进入图片工作台可加载产品、取图、批量白底/去水印/翻译/编辑。"),
            ft.Row([ft.ElevatedButton("打开图片工作台", icon=ft.Icons.GRID_VIEW, bgcolor=ACCENT,
                                      color="white", on_click=lambda e: self._switch(9)),
                    ft.OutlinedButton("选择本地图片", icon=ft.Icons.UPLOAD_FILE,
                                      on_click=lambda e: self.file_picker.pick_files(
                                          allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"]))],
                   spacing=10, wrap=True),
        ], spacing=8))
        return [h1("AI图片设置"),
                muted("API Key 仍在「设置」页填写；这里放 AI 文案、审查、图片处理的开关与范围"),
                *cards, fast_card, batch,
                ft.Row([self._autosave_status,
                        ft.OutlinedButton("恢复默认", icon=ft.Icons.RESTART_ALT, on_click=lambda e: self._reset())],
                       spacing=12)]

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
                self._safe_update()
            except Exception:
                pass
        self.rev_status.value = f"完成：{len(items)} 个产品，{bad} 个需处理"
        self.rev_status.color = "#FF6B6B" if bad else ACCENT_2
        self._log(f"内容审查完成：{len(items)} 个，{bad} 个需处理")
        try:
            self._safe_update()
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
            self.img_busy.visible = True; self._safe_update()
            threading.Thread(target=self._do_image, args=(kind,), daemon=True).start()

        self.img_quick_prompt = ft.TextField(
            label="通用编辑提示词（只对「通用编辑」按钮生效）", value=self.cfg.edit_prompt or "",
            multiline=True, min_lines=2, max_lines=4, expand=True,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            helper_text="逐张不同提示词，请用下面的「图片处理方案 → 精细处理」")
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
        def fast_mode(e):
            self._fast_image_mode(e)

        self.img_fast_btn = ft.ElevatedButton("一键快速模式（推荐）", icon=ft.Icons.BOLT,
                                              bgcolor=ACCENT_2, color="#04231C", on_click=fast_mode)
        fast_card = card(ft.Column([
            h1("速度 / 功能开关", 15),
            muted("每个 AI 步骤都有独立开关；关掉即跳过。快速模式会关闭审查/估重/营销/翻译/视频等耗时项，只保留白底、去水印、SKU白底与标题/详情。"),
            ft.Row([self.img_fast_btn], spacing=8, wrap=True),
        ], spacing=8))
        return [view[0], fast_card, tool, self._image_jobs_ui()] + view[1:]

    # ---------- 价格设置（表格化，不再让用户写 JSON）----------
    def _p_price(self):
        from ..services.pricing import (compute_price, tiers_to_rows, rows_to_tiers,
                                        regional_to_rows, rows_to_regional,
                                        wholesale_to_rows, rows_to_wholesale)
        self._current = 6
        view = self._render("价格设置", schema_price())

        # ---- 试算 ----
        try:
            _base_cost = float(self.cfg.goods_cost or 0)
        except Exception:
            _base_cost = 0.0
        self.base_input = ft.TextField(label="成本（试算）", value=str(_base_cost or 100),
                                       width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.price_out = ft.Text("—", color=ACCENT_2, size=15, weight=ft.FontWeight.BOLD, selectable=True)
        self.price_break = ft.Text("", size=11, color=MUTED, selectable=True)

        def calc(e=None):
            try:
                base = float(self.base_input.value or 0)
                r = compute_price(self.cfg, base)
                lines = [f"成本 ¥{base:.2f} → 售价 ¥{r.final:.2f}",
                         f"模式：{r.note or r.mode}"
                         + (f" · 阶梯倍率 {r.tier_multiplier:g}" if r.tier_multiplier else "")]
                if r.regional:
                    lines.append("区域价：" + " · ".join(f"{k} {v:.2f}" for k, v in list(r.regional.items())[:6]))
                if r.wholesale:
                    lines.append("批发价：" + " · ".join(f"≥{w['min_qty']}件 ¥{w['price']}"
                                                        for w in r.wholesale[:4]))
                self.price_out.value = f"¥{r.final:.2f}"
                self.price_break.value = "\n".join(lines)
            except Exception as ex:
                self.price_out.value = "计算失败"
                self.price_break.value = str(ex)[:120]
            try:
                self._safe_update()
            except Exception:
                pass

        calc_card = card(ft.Column([
            h1("价格试算", 15),
            muted("用的就是刊登时同一套算法（模式 → 阶梯 → 区域 → 批发）"),
            ft.Row([self.base_input, ft.ElevatedButton("计算", icon=ft.Icons.CALCULATE, bgcolor=ACCENT,
                                                       color="white", on_click=calc)], spacing=10),
            self.price_out, self.price_break,
        ], spacing=8))

        # ---- 阶梯价格表格 ----
        tier_state = {"rows": tiers_to_rows(getattr(self.cfg, "tuoguan_price_tiers", "[]"))}
        tier_col = ft.Column(spacing=6)
        tier_status = ft.Text("", size=11, color=MUTED)

        def tier_persist():
            self.cfg.set("tuoguan_price_tiers", rows_to_tiers(tier_state["rows"]))
            save_config(self.cfg)
            tier_status.value = (f"已保存 {len(tier_state['rows'])} 档（按成本区间套倍率）"
                                 if tier_state["rows"] else "未设置阶梯：全部按上面的价格倍率")
            tier_status.color = ACCENT_2 if tier_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def tier_row(row: dict):
            lo = ft.TextField(label="成本下限", value=f"{row.get('min', 0):g}", width=110,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            hi = ft.TextField(label="成本上限", value=f"{row.get('max', 0):g}", width=110,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            mult = ft.TextField(label="倍率", value=f"{row.get('multiplier', 1):g}", width=90,
                                bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            out = ft.Text("", size=11, color=ACCENT_2, width=110)

            def sync(e=None):
                try:
                    row["min"] = float(lo.value or 0)
                    row["max"] = float(hi.value or 1e18)
                    row["multiplier"] = float(mult.value or 1)
                except Exception:
                    return
                try:
                    out.value = f"成本 {row['min']:g} → ¥{row['min'] * row['multiplier']:g}"
                except Exception:
                    out.value = ""
                tier_persist()

            for c in (lo, hi, mult):
                c.on_change = sync
            try:
                out.value = f"成本 {row['min']:g} → ¥{row['min'] * row['multiplier']:g}"
            except Exception:
                pass

            def remove(e):
                try:
                    tier_state["rows"].remove(row)
                except ValueError:
                    pass
                tier_render(); tier_persist()

            return ft.Row([lo, hi, mult, out,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                          spacing=8, wrap=True)

        def tier_render():
            tier_col.controls = [tier_row(r) for r in tier_state["rows"]] or [
                ft.Text("还没有阶梯。点「添加一档」，例如 0-100 用 1.5 倍、100-1000 用 1.35 倍。", size=11, color=MUTED)]
            try:
                self._safe_update()
            except Exception:
                pass

        def tier_add(e=None):
            tier_state["rows"].append({"min": 0 if not tier_state["rows"] else
                                       tier_state["rows"][-1].get("max", 100), "max": 100, "multiplier": 1.35})
            tier_render(); tier_persist()

        def tier_template(e):
            tier_state["rows"] = [{"min": 0, "max": 100, "multiplier": 1.5},
                                  {"min": 100, "max": 500, "multiplier": 1.4},
                                  {"min": 500, "max": 100000, "multiplier": 1.3}]
            tier_render(); tier_persist()

        tier_render()
        tier_card = card(ft.Column([
            h1("阶梯价格", 15),
            muted("按成本落在哪一档，就用该档倍率（成本 × 倍率 + 固定加价）"),
            ft.Row([ft.ElevatedButton("添加一档", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=tier_add),
                    ft.OutlinedButton("套用示例（3 档）", icon=ft.Icons.AUTO_AWESOME, on_click=tier_template)],
                   spacing=10, wrap=True),
            tier_col, tier_status,
        ], spacing=8))

        # ---- 区域倍率表格 ----
        reg_state = {"rows": regional_to_rows(getattr(self.cfg, "adjust_price_template_detail", "{}"))}
        reg_col = ft.Column(spacing=6)
        reg_status = ft.Text("", size=11, color=MUTED)

        def reg_persist():
            self.cfg.set("adjust_price_template_detail", rows_to_regional(reg_state["rows"]))
            save_config(self.cfg)
            reg_status.value = (f"已设置 {len(reg_state['rows'])} 个币种倍率"
                                if reg_state["rows"] else "未设置区域倍率：开启「区域倍率」后生效")
            reg_status.color = ACCENT_2 if reg_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def reg_row(row: dict):
            cur = ft.Dropdown(label="币种", width=110, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              value=str(row.get("currency") or "USD"),
                              options=[ft.dropdown.Option(c) for c in CURRENCIES])
            rate = ft.TextField(label="倍率", value=f"{row.get('rate', 1):g}", width=100,
                                bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def sync(e=None):
                row["currency"] = cur.value or "USD"
                try:
                    row["rate"] = float(rate.value or 1)
                except Exception:
                    return
                reg_persist()

            cur.on_change = sync
            rate.on_change = sync

            def remove(e):
                try:
                    reg_state["rows"].remove(row)
                except ValueError:
                    pass
                reg_render(); reg_persist()

            return ft.Row([cur, rate,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                          spacing=8, wrap=True)

        def reg_render():
            reg_col.controls = [reg_row(r) for r in reg_state["rows"]] or [
                ft.Text("还没有区域倍率。点「添加币种」，或直接点「套用常用币种」。", size=11, color=MUTED)]
            try:
                self._safe_update()
            except Exception:
                pass

        def reg_add(e=None):
            used = {r.get("currency") for r in reg_state["rows"]}
            nxt = next((c for c in CURRENCIES if c not in used), "USD")
            reg_state["rows"].append({"currency": nxt, "rate": 1.0})
            reg_render(); reg_persist()

        def reg_common(e=None):
            reg_state["rows"] = [{"currency": c, "rate": r} for c, r in
                                 (("USD", 1.0), ("EUR", 0.92), ("GBP", 0.79), ("RUB", 12.5), ("BRL", 5.4))]
            reg_render(); reg_persist()

        reg_render()
        reg_card = card(ft.Column([
            h1("区域倍率", 15),
            muted("成交价按币种展示（人民币价 × 倍率），用于核对各站点价格；不影响商品人民币价"),
            ft.Row([ft.ElevatedButton("添加币种", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=reg_add),
                    ft.OutlinedButton("套用常用币种", icon=ft.Icons.AUTO_AWESOME, on_click=reg_common)],
                   spacing=10, wrap=True),
            reg_col, reg_status,
        ], spacing=8))

        # ---- 店小秘区域调价模板（搜索/选用）----
        self.tpl_kw = ft.TextField(label="模板关键词", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.tpl_list = ft.Column(spacing=4)
        self.tpl_status = ft.Text("", size=12, color=MUTED)
        self.tpl_selected = ft.Text("", size=11, color=ACCENT_2)

        def search(e):
            self.tpl_status.value = "搜索中…"
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._search_templates, daemon=True).start()

        def clear_selected(e):
            self.cfg.set("adjust_price_template_id", "")
            self.cfg.set("adjust_price_template_name", "")
            save_config(self.cfg)
            self.tpl_selected.value = "未选择店小秘模板"
            self._safe_update()

        self.tpl_selected.value = ("已选店小秘模板：" + str(self.cfg.adjust_price_template_name or "-"))
        tpl_card = card(ft.Column([
            h1("店小秘区域调价模板", 15),
            muted("从店小秘读取已建好的调价模板并选用；模板 ID/名称会记录到配置里用于对账"),
            ft.Row([self.tpl_kw, ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, on_click=search),
                    ft.OutlinedButton("清除已选", on_click=clear_selected)], spacing=10, wrap=True),
            self.tpl_selected, self.tpl_status, self.tpl_list,
        ], spacing=8))

        # ---- 批发价表格 ----
        ws_state = {"rows": wholesale_to_rows(getattr(self.cfg, "wholesale_tiers", "[]"))}
        ws_col = ft.Column(spacing=6)
        ws_status = ft.Text("", size=11, color=MUTED)

        def ws_persist():
            self.cfg.set("wholesale_tiers", rows_to_wholesale(ws_state["rows"]))
            save_config(self.cfg)
            ws_status.value = (f"已保存 {len(ws_state['rows'])} 档批发价"
                               if ws_state["rows"] else "未设置批发价")
            ws_status.color = ACCENT_2 if ws_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def ws_row(row: dict):
            qty = ft.TextField(label="起订量", value=str(row.get("min_qty", 10)), width=110,
                               bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            price = ft.TextField(label="单价（元）", value=str(row.get("price", 0)), width=120,
                                 bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def sync(e=None):
                try:
                    row["min_qty"] = int(float(qty.value or 0))
                    row["price"] = float(price.value or 0)
                except Exception:
                    return
                ws_persist()

            qty.on_change = sync
            price.on_change = sync

            def remove(e):
                try:
                    ws_state["rows"].remove(row)
                except ValueError:
                    pass
                ws_render(); ws_persist()

            return ft.Row([qty, price,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                          spacing=8, wrap=True)

        def ws_render():
            ws_col.controls = [ws_row(r) for r in ws_state["rows"]] or [
                ft.Text("未设置批发价。点「添加一档」，例如 ≥10 件 ¥9.9。", size=11, color=MUTED)]
            try:
                self._safe_update()
            except Exception:
                pass

        def ws_add(e=None):
            ws_state["rows"].append({"min_qty": 10, "price": 9.9})
            ws_render(); ws_persist()

        ws_render()
        ws_card = card(ft.Column([
            h1("批发价", 15),
            muted("按起订量分档；刊登时随价格结果一起计算（是否写入店铺看平台字段）"),
            ft.Row([ft.ElevatedButton("添加一档", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=ws_add)],
                   spacing=10, wrap=True),
            ws_col, ws_status,
        ], spacing=8))

        # ---- 本地区域模板（保存/套用倍数表）----
        self.lib_name = ft.TextField(label="模板名称", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.lib_dd = ft.Dropdown(width=220, bgcolor=CARD_2, color=TEXT, border_color=CARD_2, options=[])

        def _lib_refresh():
            lib = self.cfg.price_template_library or []
            if not isinstance(lib, list):
                lib = []
            self.lib_dd.options = [ft.dropdown.Option(str(t.get("name"))) for t in lib if isinstance(t, dict)]
            try:
                self._safe_update()
            except Exception:
                pass

        def lib_save(e):
            name = (self.lib_name.value or "").strip()
            if not name:
                self.toast("请输入模板名称", False); return
            rates = {r.get("currency"): r.get("rate") for r in reg_state["rows"] if r.get("currency")}
            lib = [t for t in (self.cfg.price_template_library or [])
                   if isinstance(t, dict) and t.get("name") != name]
            lib.append({"name": name, "rates": rates})
            self.cfg.price_template_library = lib
            save_config(self.cfg)
            _lib_refresh()
            self.toast(f"已保存模板：{name}（{len(rates)} 个币种）")

        def lib_apply(e):
            name = self.lib_dd.value
            for t in (self.cfg.price_template_library or []):
                if isinstance(t, dict) and t.get("name") == name:
                    reg_state["rows"] = [{"currency": k, "rate": v}
                                         for k, v in (t.get("rates") or {}).items()]
                    reg_render(); reg_persist()
                    self.toast(f"已套用模板：{name}")
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
        lib_card = card(ft.Column([h1("区域倍率模板", 15),
                                   muted("把当前币种倍率存成模板，换店铺/换品类一键套用"),
                                   ft.Row([self.lib_name,
                                           ft.ElevatedButton("保存当前", icon=ft.Icons.SAVE, bgcolor=CARD_2,
                                                             color=TEXT, on_click=lib_save),
                                           self.lib_dd,
                                           ft.ElevatedButton("应用", icon=ft.Icons.CHECK, bgcolor=ACCENT,
                                                             color="white", on_click=lib_apply),
                                           ft.OutlinedButton("删除", on_click=lib_del)], spacing=10, wrap=True)],
                                 spacing=8))

        return [view[0], calc_card, tier_card, reg_card, tpl_card, ws_card, lib_card] + view[1:]

    def _search_templates(self):
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            data = client.search_price_templates(self.tpl_kw.value or "")
            # 兼容 list / dict 两种返回（旧代码按 dict 取，必崩）
            if isinstance(data, dict):
                inner = data.get("data") or data
                items = inner.get("list") if isinstance(inner, dict) else inner
            else:
                items = data
            items = items or []
            rows = []
            for t in items[:30]:
                if not isinstance(t, dict):
                    continue
                name = t.get("name") or t.get("templateName") or str(t.get("id", ""))
                tid = str(t.get("id", ""))

                def apply(e, _tid=tid, _name=name):
                    self.cfg.set("adjust_price_template_id", _tid)
                    self.cfg.set("adjust_price_template_name", _name)
                    save_config(self.cfg)
                    self.tpl_selected.value = f"已选店小秘模板：{_name}（{_tid}）"
                    self.toast(f"已应用模板：{_name}")
                    try:
                        self._safe_update()
                    except Exception:
                        pass

                rows.append(ft.ListTile(title=ft.Text(name, size=13, color=TEXT),
                                        subtitle=ft.Text(tid, size=10, color=MUTED), on_click=apply))
            self.tpl_list.controls = rows or [ft.Text("没有找到模板（可先在店小秘里创建区域调价模板）", size=12, color="#FFB020")]
            self.tpl_status.value = f"共 {len(rows)} 个模板"
        except Exception as ex:
            self.tpl_status.value = f"搜索失败：{str(ex)[:120]}"
            self.tpl_list.controls = []
        try:
            self._safe_update()
        except Exception:
            pass

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
            self._safe_update()

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
            self._safe_update()

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
                self._safe_update()
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
            self._safe_update()

    def _p_workbench(self):
        self._current = 9
        ok, ck_text = self._cookie_status_text()
        self.wb_conn = ft.Text(("Cookie：" + ck_text), size=12,
                               color=ACCENT_2 if ok else "#FF6B6B")
        self.wb_conn_hint = muted("加载不了产品时先看这里：没有 Cookie 就点「粘贴 Cookie」或「启动浏览器」")
        self.wb_status = ft.Text("未加载产品", color=MUTED, size=12)
        self.wb_results = ft.Column(spacing=4)
        self.wb_wall = ft.Row(spacing=8, wrap=True)
        self.wb_sel_paths: set[str] = set()
        self.wb_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)
        self.wb_urls = ft.TextField(label="图片 URL（每行一个，也可从产品取图）", multiline=True,
                                    min_lines=3, max_lines=6, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_prompt = ft.TextField(label="「编辑」提示词（中文/英文均可；逐张不同请到「AI图片处理 → 精细任务」）",
                                      value=self.cfg.edit_prompt or "", multiline=True, min_lines=2, max_lines=3,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        # ---- 连接 ----
        def recheck(e):
            def go():
                ok2, txt2 = self._cookie_status_text(refresh=True)
                self.wb_conn.value = "Cookie：" + txt2
                self.wb_conn.color = ACCENT_2 if ok2 else "#FF6B6B"
                if ok2:
                    self._wb_check_login()
                else:
                    self.toast("仍未读到 Cookie，请粘贴 Cookie 或启动浏览器", False)
            threading.Thread(target=go, daemon=True).start()

        def open_browser(e):
            threading.Thread(target=self._connect_browser, daemon=True).start()

        conn_card = card(ft.Column([
            h1("1 · 连接", 15),
            ft.Row([self.wb_conn,
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=recheck),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
                    ft.OutlinedButton("启动浏览器（兜底）", icon=ft.Icons.OPEN_IN_BROWSER, on_click=open_browser),
                    ft.OutlinedButton("打开店小秘", icon=ft.Icons.LINK,
                                      on_click=lambda e: self._open_dxm_page())], spacing=8, wrap=True),
            self.wb_conn_hint,
                ], spacing=8))

        # ---- 产品列表 ----
        self.wb_status_sel = ft.Dropdown(
            value="editingRequired", width=150, bgcolor=CARD_2, color=TEXT, border_color=CARD_2,
            options=[ft.dropdown.Option("editingRequired", "草稿/待发布"),
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
            rows=[], column_spacing=16, heading_row_height=28, data_row_min_height=32)
        self.wb_prod_box = ft.Column([self.wb_prod_table], scroll=ft.ScrollMode.AUTO, height=300)

        def load_products(e=None):
            self.wb_page_no = 1
            self._wb_load_products(1)

        def prev_page(e):
            if self.wb_page_no > 1:
                self.wb_page_no -= 1
                self._wb_load_products(self.wb_page_no)

        def next_page(e):
            self.wb_page_no += 1
            self._wb_load_products(self.wb_page_no)

        def select_all(e):
            val = bool(e.control.value)
            for it in self.wb_rows:
                pid = str(it.get("id") or "")
                if val:
                    self.wb_selected.add(pid)
                else:
                    self.wb_selected.discard(pid)
            self._wb_sel_refresh()

        def pick_images(e):
            self._wb_pick_images()

        prod_card = card(ft.Column([
            h1("2 · 产品", 15),
            muted("先「加载」产品，再勾选产品 → 取图；也可以直接粘贴图片 URL"),
            ft.Row([self.wb_status_sel, self.wb_kw,
                    ft.ElevatedButton("加载", icon=ft.Icons.SEARCH, bgcolor=ACCENT, color="white",
                                      on_click=load_products)], spacing=8, wrap=True),
            ft.Row([ft.OutlinedButton("上一页", on_click=prev_page),
                    ft.OutlinedButton("下一页", on_click=next_page),
                    self.wb_page_info], spacing=8, wrap=True),
            ft.Row([ft.Checkbox(label="全选本页", on_change=select_all), self.wb_sel_text,
                    ft.ElevatedButton("取选中图片", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=ACCENT_2,
                                      color="#04231C", on_click=pick_images)], spacing=10, wrap=True),
            self.wb_prod_box,
        ], spacing=8))

        # ---- 图片处理 ----
        def run(kind):
            if not self.picked:
                self.toast("请先取图：加载产品 → 勾选 → 取选中图片", False); return
            if not self.cfg.api_key:
                self.toast("请先设置 API Key", False); return
            self.wb_busy.visible = True
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._wb_process, args=(kind,), daemon=True).start()

        def four_in_one(e):
            self._four_in_one_local()

        def make_video(e):
            threading.Thread(target=self._wb_make_video, daemon=True).start()

        def upload(e):
            threading.Thread(target=self._upload_results, daemon=True).start()

        def cleanup(e):
            self._open_cleanup_dialog()

        def fill_from_products(e):
            self._images_from_products()

        img_card = card(ft.Column([
            h1("3 · 图片处理", 15),
            muted("简单处理：白底 / 去水印 / 翻译 / 四合一；精细处理：填提示词后点「编辑」"),
            ft.Row([ft.ElevatedButton("白底", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                    ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                    ft.ElevatedButton("翻译成英文", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("translate")),
                    ft.ElevatedButton("编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                    ft.OutlinedButton("四合一拼图", icon=ft.Icons.GRID_VIEW, on_click=four_in_one),
                    ft.OutlinedButton("生成视频", icon=ft.Icons.MOVIE, on_click=make_video),
                    ft.OutlinedButton("上传结果到相册", icon=ft.Icons.CLOUD_UPLOAD, on_click=upload),
                    ft.OutlinedButton("清理旧图 / 空间", icon=ft.Icons.DELETE_SWEEP, on_click=cleanup),
                    self.wb_busy], spacing=8, wrap=True),
            ft.Row([self.wb_urls,
                    ft.OutlinedButton("加载图片", icon=ft.Icons.DOWNLOAD, on_click=lambda e: self._wb_load_urls()),
                    ft.OutlinedButton("从已拉取产品取图", icon=ft.Icons.INVENTORY_2, on_click=fill_from_products)],
                   spacing=8, wrap=True),
            self.wb_prompt,
            self.wb_status,
            ft.Text("图片墙：点勾选要处理的图（默认全选）", size=11, color=MUTED),
            self.wb_wall,
            self.wb_results,
        ], spacing=8))

        # ---- 队列 ----
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
                self.toast("没有可入队的产品：先「加载」产品列表", False)
                return
            chosen = ([pool[str(pid)] for pid in self.wb_selected if str(pid) in pool]
                      if self.wb_selected else list(pool.values()))
            before = len(chosen)
            chosen = [p for p in chosen if str(p.get("status_type") or "") not in
                      ("auditing", "onSelling", "published", "online")
                      and "PRODUCT_IN_AUDIT_PENDING" not in str((p.get("raw") or {}).get("errMsg") or "")]
            skipped = before - len(chosen)
            self._queue_products = pool
            n = self.q.add_many(chosen)
            self._refresh_queue()
            self.toast(f"已入队 {n} 个产品" + (f"，跳过审核中/已上架 {skipped} 个" if skipped else ""))

        def process(e):
            if not self.q.pending():
                self.toast("队列为空", False); return
            threading.Thread(target=self._process_queue, daemon=True).start()

        q_card = card(ft.Column([h1("4 · 任务队列", 15), self.q_text,
                                 ft.Row([ft.ElevatedButton("产品入队", icon=ft.Icons.ADD_TASK, on_click=enqueue),
                                         ft.ElevatedButton("处理队列(dry-run)", icon=ft.Icons.PLAY_ARROW,
                                                           bgcolor=ACCENT, color="white", on_click=process),
                                         ft.OutlinedButton("清理已完成",
                                                           on_click=lambda e: (self.q.clear_done(), self._refresh_queue())),
                                         ft.OutlinedButton("重试失败", icon=ft.Icons.REPLAY,
                                                           on_click=lambda e: self._retry_failed_queue())],
                                        spacing=10, wrap=True)], spacing=8))
        return [h1("图片工作台"), muted("加载产品 → 取图 → 白底/去水印/翻译/编辑/四合一 → 上传或合成视频"),
                conn_card, prod_card, img_card, q_card]

    # ---------- 工作台内部 ----------
    def _open_dxm_page(self):
        url = "https://www.dianxiaomi.com/web/smt/smtProductList/offline"
        if self.ws_endpoint:
            try:
                open_url_via_cdp(self.ws_endpoint, url)
                return
            except Exception:
                pass
        try:
            self.page.launch_url(url)
        except Exception:
            self.toast(f"请在浏览器打开 {url}", False)

    def _wb_check_login(self):
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            info = dxm.user_info()
            self.wb_conn.value = f"已登录：{info.get('account') or info.get('id') or '-'}"
            self.wb_conn.color = ACCENT_2
        except Exception as ex:  # noqa: BLE001
            self.wb_conn.value = f"登录检测失败：{str(ex)[:80]}"
            self.wb_conn.color = "#FF6B6B"
        try:
            self._safe_update()
        except Exception:
            pass

    def _wb_load_products(self, page: int = 1):
        self.wb_status.value = f"加载第 {page} 页…"
        self.wb_status.color = MUTED
        try:
            self._safe_update()
        except Exception:
            pass

        def work():
            try:
                ck = self._cookies()
                dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                status = self.wb_status_sel.value or None
                kw = (self.wb_kw.value or "").strip() or None
                sid = getattr(self.cfg, "shop_id", "") or self.cfg.store_id or None
                items, meta = dxm.product_page(page, 30, sid, status, kw)
                self.wb_rows = items or []
                rows = []
                for it in self.wb_rows:
                    pid = str(it.get("id") or "")
                    cb = ft.Checkbox(value=pid in self.wb_selected,
                                     on_change=lambda e, _p=pid: self._wb_toggle(_p, e.control.value))
                    rows.append(ft.DataRow(cells=[
                        ft.DataCell(cb),
                        ft.DataCell(ft.Text((it.get("title") or "")[:48] or pid, size=11, color=TEXT)),
                        ft.DataCell(ft.Text(str(it.get("status") or ""), size=10, color=MUTED)),
                        ft.DataCell(ft.Text(f"{len(it.get('images') or [])}/{len(it.get('skus') or [])}",
                                            size=10, color=MUTED)),
                    ]))
                self.wb_prod_table.rows = rows
                self.wb_page_info.value = (f"第 {meta.get('page') or page}/{meta.get('total_pages') or 1} 页 · "
                                           f"共 {meta.get('total') or 0} 个")
                self.wb_status.value = (f"已加载 {len(self.wb_rows)} 个产品"
                                        if self.wb_rows else "这一页没有产品（换个状态或关键词试试）")
                self.wb_status.color = ACCENT_2 if self.wb_rows else "#FFB020"
                self._wb_sel_refresh()
            except Exception as ex:  # noqa: BLE001
                self.wb_status.value = f"加载失败：{str(ex)[:120]}（先检查上面的 Cookie）"
                self.wb_status.color = "#FF6B6B"
                self.toast(f"加载失败：{ex}", False)
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _wb_pick_images(self):
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
        self.wb_status.color = MUTED
        try:
            self._safe_update()
        except Exception:
            pass
        threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

    def _wb_load_urls(self):
        urls = [u.strip() for u in (self.wb_urls.value or "").splitlines() if u.strip()]
        if not urls:
            self.toast("请输入图片 URL", False); return
        self.wb_status.value = f"下载 {len(urls)} 张图…"
        try:
            self._safe_update()
        except Exception:
            pass
        threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

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
            self._safe_update()
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
            self._safe_update()
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
            self._safe_update()
        except Exception:
            pass

    def _process_queue(self):
        """队列并发处理（受「并发数」限制），失败不影响其它任务。"""
        if getattr(self, "_running", False):
            self._log("已有任务在运行，队列稍后再处理", "WARN")
            self.toast("已有任务在运行", False)
            return
        self._running = True
        self._stop_requested = False
        try:
            ck = self._cookies()
            if not getattr(self, "ws_endpoint", ""):
                self._ensure_browser_session(ck)
            DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
        except Exception as ex:
            self._log(f"队列：未连接浏览器（{ex}），将以离线模式运行")
            ck = None
        by_id = {str(p.get("id")): p for p in (self.products or [])}
        by_id.update({str(k): v for k, v in getattr(self, "_queue_products", {}).items()})
        items = self.q.pending()
        if not items:
            self._running = False
            self.toast("队列为空", False)
            return
        workers = max(1, min(int(getattr(self.cfg, "concurrency", 3) or 3), 8, len(items)))
        lock = threading.Lock()
        counts = {"ok": 0, "fail": 0}

        def work(item):
            if self._stop_requested:
                self._log(f"已停止，跳过队列 {item.product_id}", "WARN")
                return
            base = by_id.get(item.product_id, {})
            if str(base.get("status_type") or "") in ("auditing", "onSelling", "published", "online") or \
                    "PRODUCT_IN_AUDIT_PENDING" in str((base.get("raw") or {}).get("errMsg") or ""):
                self._log(f"队列跳过审核中/已上架：{item.product_id}", "WARN")
                with lock:
                    self.q.mark(item.product_id, "failed", "审核中/已上架，已跳过")
                    self._refresh_queue()
                return
            prod = {"id": item.product_id,
                    "title": base.get("title") or item.title,
                    "skus": base.get("skus") or [],
                    "images": base.get("images") or [],
                    "status_type": base.get("status_type") or "",
                    "raw": base.get("raw") or {},
                    "cost": 0}
            with lock:
                self.q.mark(item.product_id, "running")
                self._refresh_queue()
            try:
                local_dxm = DianxiaomiClient(dict(ck) if ck else {}, overrides=self.cfg.endpoint_overrides or {}) if ck else None
                pub = Publisher(self.cfg, dxm=local_dxm, ws_endpoint=self.ws_endpoint,
                                scheduler=getattr(self, "scheduler", None))
                res = pub.run(prod, dry_run=not self.cfg.auto_save_products, manage_progress=False)
                status = "done" if res.ok else "failed"
                msg = res.error
            except Exception as ex:  # noqa: BLE001
                status, msg = "failed", str(ex)[:160]
            with lock:
                self.q.mark(item.product_id, status, msg)
                counts["ok" if status == "done" else "fail"] += 1
                self._refresh_queue()
                self._log(f"队列 {item.product_id}: {'完成' if status == 'done' else '失败 ' + msg}")

        self._log(f"队列开始：{len(items)} 个任务，并发 {workers}")
        import concurrent.futures as _cf
        try:
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, items))
        finally:
            self._running = False
        ok, fail = counts["ok"], counts["fail"]
        history.record("queue", ok, fail)
        self._log(f"队列完成：成功 {ok} / 失败 {fail}" + ("（已按请求停止）" if self._stop_requested else ""))
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
        self._safe_update()

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
        _translate = (getattr(self.cfg, "translate_prompt", "") or "").strip() or \
            "Translate all visible text in the image into natural English, keep layout, colors and typography unchanged; do not add new text."

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            if kind == "translate":
                return svc.edit(Path(path), _translate)
            return svc.edit(Path(path), _prompt)

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            if not res.ok:
                self._maybe_quota(res.error)
            self.wb_results.controls.append(ft.Row([ft.Icon(icon, size=15, color=color),
                                                    ft.Text(res.value if res.ok else res.error, size=11,
                                                            color=MUTED, selectable=True)], spacing=8))
            self._safe_update()

        targets = self._wb_selected_paths() if hasattr(self, "_wb_selected_paths") else list(self.picked)
        results = runner.run(list(targets), job, done)
        ok = sum(1 for r in results if r.ok)
        try:
            history.record("workbench:" + kind, ok, len(results) - ok)
        except Exception:
            pass
        self.wb_status.value = f"处理完成：成功 {ok} / {len(results)}"
        self.wb_busy.visible = False
        self._safe_update()

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
                self._safe_update()
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
            self._safe_update()
            threading.Thread(target=_do_ab, args=(src,), daemon=True).start()

        def _do_ab(src):
            from ..services import model_of
            from ..api import ApiClient
            from ..services.prompt_presets import title_variants
            mode = self.ab_mode.value or "标题"
            client = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)
            model = model_of(self.cfg, 'text')
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
                        col.controls = [ft.Text(f"{tag} · {model_of(None, 'image_gen')}", size=10, color=MUTED),
                                        ft.Image(src=str(url), width=320, height=320, fit=ft.ImageFit.CONTAIN),
                                        ft.Text(str(url)[:70], size=9, color=MUTED, selectable=True)]
                    self.try_status.value = f"完成：两张图各扣约 ¥{BUS.price_of(model_of(None, 'image_gen')):.2f}"
            except Exception as ex:
                self.try_status.value = f"失败：{str(ex)[:140]}"
            try:
                self._safe_update()
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

    def _p_logs(self):
        self._current = 10
        self.logs_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=8)
        self.logs_progress_text = ft.Text("空闲", size=12, color=MUTED)
        self.run_status = ft.Text("状态：空闲", size=12, color=MUTED)
        self.log_level = ft.Dropdown(width=120, value="全部", bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     options=[ft.dropdown.Option(x) for x in ("全部","INFO","WARN","ERROR")],
                                     on_change=lambda e: self._refresh_log_table())
        self.log_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("时间", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("级别", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("内容", size=11, color=MUTED))],
            rows=[], column_spacing=14, heading_row_height=28, data_row_min_height=24,
        )
        if not getattr(self, "log_rows", None):
            self.log_rows = []          # 表格只显示本次运行；历史见「打开日志文件」
        self.log_follow = ft.Checkbox(label="总是跳到最新日志", value=bool(getattr(self.cfg, "log_auto_follow", True)),
                                      on_change=self._on_log_follow_change)
        self.log_scroll = ft.Column([self.log_table], scroll=ft.ScrollMode.AUTO,
                                    auto_scroll=bool(getattr(self.cfg, "log_auto_follow", True)),
                                    height=360, on_scroll=self._on_log_scroll)
        self._refresh_log_table()

        def export(e):
            out = Path(tempfile.gettempdir()) / "aurora-logs.txt"
            rows = getattr(self, "log_rows", [])
            out.write_text("\n".join(f"{t} [{lv}] {m}" for t, lv, m in rows), encoding="utf-8")
            self.toast(f"已导出：{out}")

        bar = card(ft.Row([
            ft.ElevatedButton("停止任务", icon=ft.Icons.STOP_CIRCLE, bgcolor="#D9534F", color="white",
                              on_click=self._request_stop),
            ft.ElevatedButton("导出日志", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=export),
            ft.OutlinedButton("打开日志文件", icon=ft.Icons.DESCRIPTION, on_click=lambda e: self._open_log_file()),
            self.log_follow,
            self.log_level,
            ft.ElevatedButton("清空", icon=ft.Icons.DELETE_OUTLINE, bgcolor=CARD_2, color=TEXT,
                              on_click=lambda e: (self.log_rows.clear(), self.logs.clear(), self._refresh_log_table())),
        ], spacing=10, wrap=True))
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
            muted("注意：定时任务需要软件保持运行；关闭软件后不会执行。"),
            *(sched_rows or [ft.Text("暂无定时任务", size=11, color=MUTED)]),
        ], spacing=6))
        prog_card_logs = card(ft.Column([ft.Row([h1("实时进度", 15), self.run_status], spacing=12),
                                         self.logs_progress_text, self.logs_progress], spacing=8))
        return [prog_card_logs, h1("任务与日志"), bar, sched_card,
                card(self.log_scroll)]

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
            self._safe_update()
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
            self._safe_update()
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
            self._safe_update()
        except Exception:
            pass

    def _close_log_panel(self):
        self.log_panel_open = False
        try:
            self.log_panel_dlg.open = False
            self._safe_update()
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

    def _log_file(self):
        p = config_dir() / "logs" / "app.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _read_log_tail(self, limit: int = 300):
        """启动时把上次日志读回来，运行出错后不用再猜。"""
        try:
            lines = self._log_file().read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                # 2026-11-17 23:00:00 [INFO] xxx
                date, tm, rest = line.split(" ", 2)
                if not rest.startswith("["):
                    continue
                lv, msg = rest[1:].split("] ", 1)
                out.append((tm, lv, msg))
            except Exception:
                continue
        return out

    def _write_log_file(self, ts: str, level: str, msg: str):
        try:
            p = self._log_file()
            with self._log_file_lock:
                if p.exists() and p.stat().st_size > 2 * 1024 * 1024:
                    try:
                        p.replace(p.with_suffix(".1"))
                    except Exception:
                        pass
                with p.open("a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d')} {ts} [{level}] {msg}\n")
        except Exception:
            pass

    def _log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        if not hasattr(self, "log_rows"):
            self.log_rows = []
        self.log_rows.append((ts, level, str(msg)))
        self.log_rows = self.log_rows[-1000:]
        self.logs = [f"{t} [{lv}] {m}" for t, lv, m in self.log_rows]
        self._write_log_file(ts, level, str(msg))
        if getattr(self, "log_table", None) is not None:
            self._refresh_log_table()

    def _open_log_file(self):
        try:
            p = self._log_file()
            if not p.exists():
                self.toast("还没有日志文件", False); return
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                import subprocess as _sp
                _sp.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
        except Exception as ex:
            self.toast(f"打开失败：{ex}", False)

    def _on_log_follow_change(self, e):
        follow = bool(e.control.value)
        self.cfg.set("log_auto_follow", follow)
        save_config(self.cfg)
        try:
            self.log_scroll.auto_scroll = follow
            if follow:
                self.log_scroll.scroll_to(offset=10 ** 9, duration=150)
        except Exception:
            pass
        self._safe_update()

    def _on_log_scroll(self, e):
        if not bool(getattr(self.log_follow, "value", True)):
            self._log_scroll_pos = float(getattr(e, "pixels", 0) or 0)

    def _refresh_log_table(self):
        # 事件很多时不要每条都重建表格（会把 UI 拖死），0.5 秒合并一次
        now = time.time()
        if now - float(getattr(self, "_log_refresh_ts", 0) or 0) < 0.5:
            t = getattr(self, "_log_refresh_timer", None)
            if t is None or not t.is_alive():
                t = threading.Timer(0.5, self._refresh_log_table)
                t.daemon = True
                self._log_refresh_timer = t
                t.start()
            return
        self._log_refresh_ts = now
        rows = getattr(self, "log_rows", [])
        lv = getattr(self, "log_level", None)
        want = lv.value if lv is not None else "全部"
        shown = [(t, x, m) for t, x, m in rows if want == "全部" or x == want]
        self.log_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(t, size=11, color=MUTED)),
                ft.DataCell(ft.Text(x, size=11, color=ACCENT_2 if x == "INFO" else ("#FFB020" if x == "WARN" else "#FF6B6B"))),
                ft.DataCell(ft.Text(m, size=11, color=TEXT)),
            ]) for t, x, m in shown[-300:]
        ]
        try:
            self._safe_update()
        except Exception:
            pass
        try:
            if getattr(self, "log_scroll", None) is not None:
                if bool(getattr(self.log_follow, "value", True)):
                    self.log_scroll.scroll_to(offset=10 ** 9, duration=120)
                else:
                    pos = float(getattr(self, "_log_scroll_pos", 0) or 0)
                    if pos > 0:
                        self.log_scroll.scroll_to(offset=pos, duration=0)
        except Exception:
            pass

    def _p_settings(self):
        self._current = 8

        # ---------------- 授权 ----------------
        from ..license import machine_code as _mcode
        code = _mcode()
        self.machine_text = ft.Text(code, size=18, weight=ft.FontWeight.BOLD, color=TEXT, selectable=True)
        self.lic_status = ft.Text("检测中…", size=12, color=MUTED)
        self.lic_detail = ft.Text("", size=11, color=MUTED)

        def copy_code(e):
            try:
                self.page.set_clipboard(code)
                self.toast("机器码已复制，发给管理员即可登记授权")
            except Exception:
                self.toast(f"请手动复制：{code}", False)

        def recheck_lic(e):
            self.lic_status.value = "检测中…"
            self.lic_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._check_license, daemon=True).start()

        self.legacy_id_field = ft.TextField(
            label="旧版授权 ID（可选）", value=str(getattr(self.cfg, "license_identity", "") or ""),
            width=360, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            hint_text="老软件「设置」里复制的设备授权 ID，填这里可继续用原授权")

        def save_legacy(e=None):
            self.cfg.set("license_identity", str(self.legacy_id_field.value or "").strip())
            save_config(self.cfg)

        self.legacy_id_field.on_change = save_legacy

        lic_card = card(ft.Column([
            h1("授权", 15),
            muted("每台电脑的机器码都不同。把机器码发给管理员登记后，点「重新检测」即可使用"),
            ft.Row([ft.Text("机器码：", size=12, color=MUTED), self.machine_text], spacing=8, wrap=True),
            ft.Row([ft.ElevatedButton("复制机器码", icon=ft.Icons.COPY, bgcolor=ACCENT, color="white",
                                      on_click=copy_code),
                    ft.OutlinedButton("重新检测", icon=ft.Icons.REFRESH, on_click=recheck_lic)],
                   spacing=8, wrap=True),
            self.legacy_id_field,
            self.lic_status, self.lic_detail,
        ], spacing=8))
        self.lic_status.value = (self.license_state or {}).get("message") or "未检测"
        if (self.license_state or {}).get("ok"):
            self.lic_status.color = ACCENT_2
            exp = (self.license_state or {}).get("expiry") or ""
            self.lic_detail.value = f"到期：{exp}" if exp else "授权有效（长期）"
        else:
            self.lic_status.color = "#FF6B6B"
            self.lic_detail.value = "如果刚登记过，点「重新检测」；换电脑/重装系统后机器码会变，需要重新登记"

        # ---------------- API ----------------
        self.api_key_field = ft.TextField(label="API Key", value=str(self.cfg.api_key or ""),
                                          password=True, can_reveal_password=True, width=460,
                                          bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.api_status = ft.Text("", size=11, color=MUTED)
        self.cfg_concurrency = ft.TextField(label="并发数（同时处理几个产品）",
                                            value=str(self.cfg.concurrency or 3), width=220,
                                            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.cfg_timeout = ft.TextField(label="超时（秒）", value=str(self.cfg.timeout or 180), width=160,
                                        bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        # 输入即保存（0.6s 防抖），避免客户改完 Key 直接点启动却没落盘
        def _api_autosave(e=None):
            t = getattr(self, "_api_timer", None)
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            timer = threading.Timer(0.6, self._save_api_fields)
            timer.daemon = True
            self._api_timer = timer
            timer.start()
        for _c in (self.api_key_field, self.cfg_concurrency, self.cfg_timeout):
            _c.on_change = _api_autosave

        def save_api(e=None):
            self._save_api_fields()
            self.api_status.value = "已保存"
            self.api_status.color = ACCENT_2
            try:
                self._safe_update()
            except Exception:
                pass

        def test_api(e):
            save_api()
            self.api_status.value = "测试中…"
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                try:
                    models = ApiClient(self.cfg.api_base, self.cfg.api_key, 20).models(force=True)
                    self.api_status.value = f"连接正常：{len(models)} 个模型"
                    self.api_status.color = ACCENT_2
                except Exception as ex:  # noqa: BLE001
                    self.api_status.value = f"连接失败：{str(ex)[:90]}"
                    self.api_status.color = "#FF6B6B"
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        def buy_api(e):
            url = str(getattr(self.cfg, "api_purchase_url", "") or "https://api.moneyfly.club")
            try:
                self.page.launch_url(url)
            except Exception:
                self.toast(f"请在浏览器打开 {url}", False)

        api_card = card(ft.Column([
            h1("API", 15),
            muted("填好 API Key 即可用；没有 Key 或额度不足时，点「购买/充值」去你的 API 后台"),
            self.api_key_field,
            ft.Row([self.cfg_concurrency, self.cfg_timeout,
                    ft.ElevatedButton("保存", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white", on_click=save_api),
                    ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING, on_click=test_api),
                    ft.ElevatedButton("购买 / 充值 API", icon=ft.Icons.ACCOUNT_BALANCE_WALLET,
                                      bgcolor="#F0A020", color="#1A1200", on_click=buy_api)],
                   spacing=8, wrap=True),
            self.api_status,
        ], spacing=8))

        # ---------------- 模型（已固定，不提供选择） ----------------
        from ..services import FIXED_MODELS, FIXED_MODEL_LABELS
        self.model_status = ft.Text("模型已固定，无需设置；价格见下方「模型价格」", size=11, color=MUTED)
        model_rows = []
        for kind in ("text", "vision", "image_edit", "image_gen"):
            model_rows.append(ft.Row([
                ft.Text(FIXED_MODEL_LABELS[kind], size=12, color=MUTED, width=300),
                ft.Text(FIXED_MODELS[kind], size=12, color=TEXT, weight=ft.FontWeight.BOLD),
            ], spacing=10, wrap=True))
        model_card = card(ft.Column([
            h1("AI 模型", 15),
            muted("已按实测结果固定，客户无需选择，避免选到不可用/超慢/超贵的模型"),
            *model_rows,
            self.model_status,
        ], spacing=8))

        # ---------------- 多开 ----------------
        self.inst_name = ft.TextField(label="新实例名称（如 店铺2）", width=240,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.inst_list = ft.Column(spacing=4)
        self._refresh_instances()

        def create(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                info = create_instance(name)
                self._log(f"实例已创建：{name}（端口 {info.get('cdp_port')}）")
                self._refresh_instances()
                self.toast(f"实例已创建：{name}（端口 {info.get('cdp_port')}）")
            except Exception as ex:
                self.toast(str(ex), False)

        def launch(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                launch_instance(name)
                self._log(f"实例已启动：{name}")
                self.toast("新实例已启动（独立配置与调试端口）")
            except Exception as ex:
                self.toast(str(ex), False)

        def create_and_launch(e):
            create(e)
            launch(e)

        inst_card = card(ft.Column([
            h1("多开 / 多任务", 15),
            muted("每个实例独立配置、独立浏览器调试端口，可同时跑不同店铺；任务队列与定时发布也按实例隔离"),
            ft.Row([self.inst_name,
                    ft.ElevatedButton("创建实例", icon=ft.Icons.ADD, on_click=create),
                    ft.ElevatedButton("创建并启动", icon=ft.Icons.ROCKET_LAUNCH, bgcolor=ACCENT, color="white",
                                      on_click=create_and_launch)], spacing=8, wrap=True),
            self.inst_list,
        ], spacing=8))

        # ---------------- 更新 / 诊断 / 价格 ----------------
        try:
            from ..core.updater import CURRENT as _cur_ver
        except Exception:
            _cur_ver = ""
        upd = card(ft.Column([
            h1("版本更新", 15),
            ft.Text(f"当前版本：v{_cur_ver}" if _cur_ver else "当前版本：-", size=12, color=TEXT),
            ft.Row([ft.ElevatedButton("检查更新", icon=ft.Icons.SYSTEM_UPDATE,
                                      on_click=lambda e: threading.Thread(target=self._check_update, daemon=True).start())])],
            spacing=8))
        self.diag_list = ft.Column(spacing=4)
        diag_card = card(ft.Column([
            h1("诊断（出问题时再点）", 15),
            muted("检查 API、浏览器、店小秘、ffmpeg、磁盘；不影响正常使用"),
            ft.Row([ft.ElevatedButton("开始诊断", icon=ft.Icons.HEALTH_AND_SAFETY, bgcolor=ACCENT, color="white",
                                      on_click=lambda e: threading.Thread(target=self._run_diag, daemon=True).start()),
                    ft.ElevatedButton("全接口冒烟（约 ¥0.4）", icon=ft.Icons.WIFI_TETHERING, bgcolor="#F0A020",
                                      color="#1A1200", on_click=lambda e: self._smoke_confirm()),
                    ft.OutlinedButton("AI 自检", icon=ft.Icons.PLAY_CIRCLE_FILL,
                                      on_click=lambda e: threading.Thread(target=self._self_test, daemon=True).start())],
                   spacing=8, wrap=True),
            self.diag_list], spacing=8))
        self.price_list = ft.Column(spacing=3)
        self.price_status = ft.Text("价格加载中…", size=11, color=MUTED)
        price_card = card(ft.Column([
            h1("模型价格", 15), self.price_status, self.price_list,
            ft.Row([ft.ElevatedButton("刷新价格", icon=ft.Icons.REFRESH,
                                      on_click=lambda e: threading.Thread(target=self._load_prices, daemon=True).start())])],
            spacing=8))
        threading.Thread(target=self._load_prices, daemon=True).start()

        # ---------------- 额外选项（老软件「设置」页） ----------------
        def sw(key, label):
            ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)), active_color=ACCENT)
            def on(e, _k=key):
                self.cfg.set(_k, bool(e.control.value))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        self.publish_pos = ft.Dropdown(
            label="发布到什么地方", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "publish_action", "save_only") or "save_only"),
            options=[ft.dropdown.Option("save_only", "待发布"), ft.dropdown.Option("publish", "直接发布")])
        def set_pub(e):
            self.cfg.set("publish_action", str(self.publish_pos.value or "save_only"))
            save_config(self.cfg)
        self.publish_pos.on_change = set_pub
        extra_card = card(ft.Column([
            h1("额外选项", 15),
            muted("老软件「设置」页的常用开关；每个开关都会实际影响保存/发布流程"),
            ft.Row([sw("prefer_collected_weight", "优先使用采集重量尺寸"),
                    sw("four_in_one", "四合一主图"),
                    sw("main_as_detail", "启用主图作为详情图"),
                    sw("pad_main_images_from_extra", "主图不足6张时用额外图片补齐"),
                    sw("keep_main_images_local", "保留主图及AI生成图片至本地")], spacing=14, wrap=True),
            ft.Row([sw("custom_detail_text_enable", "添加自定义详情文本或图片"),
                    sw("delete_detail_text", "删除详情原有文本"),
                    sw("copy_sku_to_detail", "复制SKU至详情")], spacing=14, wrap=True),
            ft.Row([sw("modify_stock_999", "修改库存为999"),
                    sw("original_box", "是否原箱"),
                    sw("auto_generate_sku_code", "自动生成唯一商品编码"),
                    sw("check_banned_word", "违禁词检查")], spacing=14, wrap=True),
            ft.Row([sw("use_template_video", "使用模板产品视频"),
                    sw("generate_main_video", "生成主图轮播视频"),
                    sw("keep_custom_attributes", "保留自定义属性"),
                    sw("split_product_price", "启用产品信息/价格分离处理")], spacing=14, wrap=True),
            ft.Row([self.publish_pos], spacing=10, wrap=True),
        ], spacing=10))

        return [lic_card, api_card, model_card, extra_card, inst_card, upd, diag_card, price_card]

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
            self._safe_update()
            d = _P(_tf.mkdtemp())
            probe_img = d / "probe.png"
            _Img.new("RGB", (512, 512), (240, 240, 240)).save(probe_img)
            svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
            url = svc.edit(probe_img, "return the same simple image, no changes")
            self.model_status.value = f"图片模型可用：{model_of(None, 'image_edit')} → {str(url)[:70]}"
            self.toast("图片模型测试通过")
        except Exception as ex:
            self.model_status.value = f"图片模型不可用：{str(ex)[:120]}"
            self.toast(str(ex), False)
        try:
            self._safe_update()
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
        self._safe_update()

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
                self._safe_update()
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
            out = client.chat(model_of(self.cfg, 'text'), [{"role": "user", "content": "回复 OK"}], max_tokens=8)
            add("文本对话", bool(out), f"{model_of(self.cfg, 'text')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("文本对话", False, str(ex)[:90])
        # 4 视觉
        t0 = _t.time()
        try:
            out = client.chat(model_of(self.cfg, 'vision'), [{"role": "user", "content": [
                {"type": "text", "text": "图里是什么颜色？两个字"},
                {"type": "image_url", "image_url": {"url": "https://sc04.alicdn.com/kf/H36d2c1e2529d4ab18cab919a38f33e25D.jpg"}}]}],
                max_tokens=12)
            add("视觉读图", bool(out), f"{model_of(self.cfg, 'vision')} · {_t.time()-t0:.1f}s")
        except Exception as ex:
            add("视觉读图", False, str(ex)[:90])
        # 5 文生图
        t0 = _t.time()
        try:
            url = client.images_generate(model_of(self.cfg, 'image_gen'), "a simple white cube on gray background", "1024x1024", 1)
            add("文生图", bool(url), f"{model_of(self.cfg, 'image_gen')} · {_t.time()-t0:.1f}s")
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
            add("图像编辑", bool(url), f"{model_of(self.cfg, 'image_edit')} · {_t.time()-t0:.1f}s")
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
                self._safe_update()
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
            if getattr(self, "price_status", None) is not None:
                self.price_status.value = f"共 {len(rows)} 个模型（缓存 5 分钟）"
                self.price_status.color = MUTED
        except Exception as ex:
            self.price_list.controls = [ft.Text(f"价格获取失败：{ex}", size=12, color="#FF6B6B")]
            if getattr(self, "price_status", None) is not None:
                self.price_status.value = "价格获取失败，检查 API Key/网络后点刷新"
        try:
            self._safe_update()
        except Exception:
            pass
    def _refresh_instances(self):
        try:
            items = list_instances()
        except Exception:
            items = []
        rows = []
        for i in items:
            port = i.get("cdp_port") or "-"
            rows.append(ft.Row([
                ft.Text(f"{i['name']}", size=12, color=TEXT, width=110),
                ft.Text(f"调试端口 {port}", size=11, color=MUTED, width=110),
                ft.Text(str(i.get("path") or "")[:60], size=10, color=MUTED, expand=True),
            ], spacing=8))
        self.inst_list.controls = rows or [ft.Text("暂无实例", size=11, color=MUTED)]

    def _check_update(self):
        r = updater.check(self.cfg.license_server)
        if not r.get("ok"):
            self.toast(f"检查失败：{r.get('error')}", False); return
        if not r.get("has_update"):
            self.toast(f"已是最新版本（v{updater.CURRENT}）")
            self._log(f"更新检查：已是最新版本 v{updater.CURRENT}")
            return
        notes = str(r.get("notes") or "").strip()
        has_url = bool(str(r.get("url") or "").strip())
        self._log(f"发现新版本 {r.get('latest')}（当前 v{updater.CURRENT}）")
        dlg = ft.AlertDialog(
            title=ft.Text(f"发现新版本 {r.get('latest')}"),
            content=ft.Column([
                ft.Text(f"当前版本：v{updater.CURRENT}", size=12, color=TEXT),
                ft.Text(notes[:600] or "（服务器未提供更新说明）", size=11, color=MUTED, selectable=True),
                ft.Text("" if has_url else "服务器还没有配置安装包下载地址，请联系管理员。",
                        size=11, color="#FFB020"),
            ], spacing=8, width=460),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("下载并安装", bgcolor=ACCENT, color="white", disabled=not has_url,
                                       on_click=lambda e: do_download(e))],
        )
        self.page.dialog = dlg
        dlg.open = True
        self._safe_update()

        def do_download(e):
            dlg.open = False
            self._safe_update()
            self.toast("开始下载安装包…")

            def go():
                try:
                    path = updater.download(str(r["url"]))
                    want = str(r.get("sha256") or "").lower()
                    if want and updater.sha256(path).lower() != want:
                        raise RuntimeError("安装包校验失败（SHA256 不一致）")
                    self._log(f"更新包已下载：{path}")
                    self.toast("下载完成，正在启动安装程序…")
                    updater.launch_installer(path)
                except Exception as ex:  # noqa: BLE001
                    self._log(f"更新下载失败：{ex}", "ERROR")
                    self.toast(f"更新失败：{str(ex)[:80]}", False)
            threading.Thread(target=go, daemon=True).start()

    # ---------- 行为 ----------
    def _check_license(self):
        try:
            from ..core.updater import CURRENT as _ver
        except Exception:
            _ver = ""
        res = verify_license(self.cfg.license_server, version=_ver,
                             identity_override=str(getattr(self.cfg, "license_identity", "") or ""),
                             public_key_pem=str(getattr(self.cfg, "license_public_key", "") or ""))
        self.license_state = res
        try:
            if res.get("ok"):
                self.cfg.set("license_cache_ok", True)
                self.cfg.set("license_cache_expiry", str(res.get("expiry") or ""))
                self.cfg.set("license_cache_ts", int(time.time()))
            elif "无法连接授权服务器" in str(res.get("message") or ""):
                self._log("授权服务器暂不可达，保留上次授权缓存", "WARN")
            else:
                self.cfg.set("license_cache_ok", False)
                self.cfg.set("license_cache_ts", 0)
            save_config(self.cfg)
        except Exception:
            pass
        msg = res.get("message") or ("授权有效" if res.get("ok") else "未授权")
        self.license_text.value = "授权有效" if res.get("ok") else "未授权"
        self.license_text.color = ACCENT_2 if res.get("ok") else "#FF6B6B"
        # 设置页的授权卡片同步刷新
        for attr in ("lic_status", "lic_detail"):
            if getattr(self, attr, None) is None:
                continue
        if getattr(self, "lic_status", None) is not None:
            self.lic_status.value = msg
            self.lic_status.color = ACCENT_2 if res.get("ok") else "#FF6B6B"
        if getattr(self, "lic_detail", None) is not None:
            exp = res.get("expiry") or ""
            self.lic_detail.value = (f"到期：{exp}" if exp else
                                     ("授权有效（长期）" if res.get("ok")
                                      else "把上面的机器码发给管理员登记；登记后点「重新检测」"))
        self._apply_license_state()
        try:
            self._safe_update()
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
        self._safe_update()

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
            self._safe_update()

        targets = self._wb_selected_paths() if hasattr(self, "_wb_selected_paths") else list(self.picked)
        results = runner.run(list(targets), job, done)
        ok = sum(1 for r in results if r.ok)
        self.img_status.value = f"完成：成功 {ok} / {len(results)}"
        self.img_status.color = ACCENT_2 if ok == len(results) else "#FFB020"
        self.img_busy.visible = False
        self._safe_update()

    def _on_files(self, e):
        if e.files:
            self.picked = [Path(f.path) for f in e.files]
            self.img_status.value = f"已选择 {len(self.picked)} 张图"
            self._safe_update()

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
                                    f"· 图片编辑 {model_of(None, 'image_edit')}（¥{self._price_of(model_of(None, 'image_edit')):.3f}/张）\n"
                                    f"· 图片生成 {model_of(None, 'image_gen')}（¥{self._price_of(model_of(None, 'image_gen')):.3f}/张）"),
                    actions=[ft.TextButton("稍后", on_click=lambda e: self._close_dialog()),
                             ft.ElevatedButton("去充值", bgcolor="#F0A020", color="#1A1200", on_click=go)])
                self.page.dialog = self.dlg
                self.dlg.open = True
                self._safe_update()
            except Exception:
                self.toast("额度不足，请去站点充值", False)

    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self._safe_update()

    def _safe_update(self):
        """串行化 UI 更新：避免后台线程并发 page.update 导致卡死/重连。"""
        if getattr(self, "_building_page", False):
            return
        lock = getattr(self, "_ui_lock", None)
        try:
            if lock is not None:
                with lock:
                    self.page.update()
            else:
                self.page.update()
        except Exception as ex:  # noqa: BLE001
            now = time.time()
            if now - float(getattr(self, "_update_err_ts", 0) or 0) > 5:
                self._update_err_ts = now
                try:
                    from ..config import config_dir
                    p = config_dir() / "error.log"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with p.open("a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} UI_UPDATE {type(ex).__name__}: {ex}\n")
                except Exception:
                    pass


def _install_crash_log():
    """把主线程/子线程未捕获异常写到配置目录 error.log，便于排查“重启/闪退”。"""
    import sys as _sys
    import threading as _th
    from ..config import config_dir

    def _write(kind, exc_type, exc, tb):
        try:
            import traceback
            p = config_dir() / "error.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {kind} =====\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass

    def _hook(exc_type, exc, tb):
        _write("UNCAUGHT", exc_type, exc, tb)
        _sys.__excepthook__(exc_type, exc, tb)

    def _thook(args):
        _write("THREAD", args.exc_type, args.exc_value, args.exc_traceback)

    _sys.excepthook = _hook
    try:
        _th.excepthook = _thook
    except Exception:
        pass


def _assets_dir() -> str:
    """开发/打包两种环境都能找到 assets（打包后资源在 _internal/assets）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[3]
    p = base / "assets"
    return str(p if p.exists() else (Path("assets")))


def run():
    _install_crash_log()
    ft.app(target=lambda page: App(page), assets_dir=_assets_dir())
