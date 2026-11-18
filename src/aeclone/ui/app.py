"""AuroraList · 现代化界面（schema 驱动，覆盖老软件全部设置项）。"""
from __future__ import annotations

from .. import __version__ as VERSION

import datetime
import sys
import tempfile
import os
import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient, ApiError
from ..config import AppConfig, config_dir, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService, model_of
from ..services.pricing import calc_multiplier, calc_free_shipping
from ..services.variants import auto_map, parse_mapping_json
from ..services.publisher import Publisher, demo_product
from ..integrations.dianxiaomi import DianxiaomiClient, ENDPOINTS
from ..services.video import build_slideshow, generate_carousel_video, probe as video_probe
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

CURRENCIES = ["USD", "EUR", "GBP", "CNY", "RUB", "BRL"]
# 区域调价常用国家/地区（代码 → 名称）
REGIONS = [("US", "美国"), ("RU", "俄罗斯"), ("BR", "巴西"), ("ES", "西班牙"),
           ("FR", "法国"), ("DE", "德国"), ("GB", "英国"), ("IT", "意大利"),
           ("PL", "波兰"), ("TR", "土耳其"), ("SA", "沙特"), ("AE", "阿联酋"),
           ("IL", "以色列"), ("KR", "韩国"), ("JP", "日本"), ("MX", "墨西哥"),
           ("CA", "加拿大"), ("AU", "澳大利亚"), ("NL", "荷兰"), ("PT", "葡萄牙")]

# AI图片编辑提示词预设
IMAGE_PROMPT_PRESETS = {
    "marketing": {
        "工地作业场景": """生成专业的产品营销场景图（工程机械类）：\n1. 商品置于真实施工现场：土方工地/矿山/道路工程\n2. 商品结构、颜色、标识完全不变，只换背景与光影\n3. 自然日光，画面干净有纵深感\n4. 商品占画面 55~70%，为视觉主体\n5. 不要文字、水印、Logo、促销标签\n6. 1:1 构图""",
        "白底棚拍": """生成专业白底棚拍营销图：\n1. 纯白背景（#FFFFFF），无渐变\n2. 商品居中，占画面 60~70%\n3. 柔和棚拍光 + 自然接触阴影\n4. 保持商品原有颜色与细节\n5. 不要文字、水印、Logo\n6. 1:1 构图""",
        "展会/交付场景": """生成商务展示场景图：\n1. 商品置于展会现场或客户交付场景\n2. 环境整洁、专业、有商务感\n3. 商品为视觉主体，比例 55~70%\n4. 光线柔和自然，无过曝\n5. 不要文字、水印、Logo\n6. 1:1 构图""",
        "户外实拍": """生成户外实拍风格营销图：\n1. 商品置于开阔户外环境（山地/工地/道路）\n2. 天空、地面层次自然，有空间感\n3. 商品结构比例不变，仅换环境\n4. 不要人物正脸，不要文字水印\n5. 1:1 构图""",
    },
    "main": {
        "专业电商主图": """基于上传的图片融合生成专业电商主图。
生成要求：
1. 主体清晰突出，构图饱满平衡
2. 纯白或浅灰背景，突出商品
3. 柔和自然光，无突兀阴影
4. 不添加任何文字，去除logo、水印
5. 一个欧洲面孔的美女正在操作机器，""",
        "白底棚拍": """将产品图片转换为专业白底棚拍效果：
1. 纯白背景（#FFFFFF），无渐变无阴影
2. 产品居中，占画面60-70%
3. 保持产品原有颜色和细节
4. 清除水印、logo、背景杂物
5. 专业摄影棚光照效果""",
        "使用场景图": """生成产品的真实使用场景图：
1. 将产品置于符合其用途的真实环境中
2. 环境自然真实，光线柔和
3. 突出产品在使用中的状态
4. 可添加人物互动（欧美面孔）
5. 场景干净整洁，无杂乱元素""",
        "多角度展示": """基于参考图生成产品多角度展示图：
1. 正面、侧面、背面、细节等多个角度
2. 统一的白底或场景背景
3. 每个角度突出不同特点
4. 保持产品一致性
5. 专业拍摄质感""",
    },
    "detail": {
        "细节特写": """生成产品细节特写图：
1. 聚焦产品关键部位（接口、按钮、材质、工艺等）
2. 清晰展示细节特征
3. 适当景深，突出焦点
4. 柔和补光，无过曝
5. 可添加标注线和说明文字""",
        "尺寸规格图": """生成产品尺寸规格展示图：
1. 清晰的尺寸标注（长×宽×高）
2. 标注线规范，字体清晰
3. 可添加比例参照物
4. 白底或浅灰底，便于阅读
5. 多角度尺寸标注""",
        "功能展示": """生成产品功能特性展示图：
1. 展示产品的核心功能和特点
2. 分步骤或分区域说明
3. 配合图标和文字说明
4. 对比效果展示（使用前后）
5. 清晰直观，易于理解""",
        "包装配件": """生成产品包装和配件展示图：
1. 完整展示产品包装外观
2. 列出所有配件和附件
3. 整齐排列，布局美观
4. 白底或场景拍摄
5. 可添加清单文字""",
    },
    "sku": {
        "SKU白底图": """生成SKU规格白底图：
1. 纯白背景（#FFFFFF）
2. 产品居中，占比适中
3. 清晰展示该SKU的特征（颜色/尺寸/款式）
4. 无水印、无logo、无文字
5. 保持系列一致性""",
        "颜色对比": """生成不同颜色SKU的对比图：
1. 多个颜色并排展示
2. 统一角度和光照
3. 准确还原颜色差异
4. 白底或浅灰底
5. 便于买家选择""",
        "规格差异": """生成不同规格SKU的对比图：
1. 展示尺寸、容量、型号等差异
2. 添加清晰的规格标注
3. 并排或层叠对比
4. 参照物体现大小差异
5. 帮助买家理解规格""",
    }
}


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
        ("店铺", [Field("store_name", "店铺名称"), Field("store_id", "店铺 ID")]),
        ("模板（API 直存必需）", [
            Field("product_template_name", "模板名称", hint="从店小秘选择或粘贴模板商品，属性/品牌/说明书等将从模板补齐"),
            Field("product_template_id", "模板商品 ID", hint="必填：API 直存会从该模板 edit.json 合并缺失字段"),
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
            Field("write_main_images_to_product", "AI 主图写回商品（替换主图）", "bool"),
            Field("write_detail_html_to_product", "AI 详情写回商品", "bool"),
            Field("write_sku_codes_to_product", "SKU 编码写回商品", "bool"),
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
        ]),
        ("SKU 图", [
            Field("white_background_sku_pic_indices", "SKU 图序号（白底/去水印/编辑共用）",
                  hint="支持 1-3,5 这种写法；留空=全部"),
            Field("white_background_sku_pic", "处理 SKU 图", "bool"),
            Field("img_sku_white_bg", "SKU 图白底", "bool"),
            Field("img_sku_remove_wm", "SKU 图去水印", "bool"),
            Field("img_sku_edit", "SKU 图精细编辑", "bool"),
            Field("img_sku_edit_prompt", "SKU 图编辑提示词", "multiline"),
            Field("translate_sku_pic", "SKU 图文字翻译", "bool"),
        ]),
        ("营销", [
            Field("marketing_auto_generate", "生成营销图（写进营销图片 1:1 / 3:4）", "bool"),
            Field("marketing_image_prompt", "营销图提示词", "multiline"),
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
            Field("upload_main_video", "保存时上传主图视频", "bool",
                  hint="暂未接入 API 直存（视频上传）"),
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
        ("写回", [Field("write_price_to_product", "价格写回商品（零售价）", "bool")]),
    ]


def schema_variant():
    return []


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

        page.title = f"AuroraList · 极光上架 v{VERSION}"
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
            ("上架准备", [("店铺设置", ft.Icons.STORE_OUTLINED, 1),
                          ("商品设置", ft.Icons.TUNE, 2)]),
            ("AI 处理", [("AI 文案", ft.Icons.EDIT_NOTE, 3),
                         ("内容审查", ft.Icons.FACT_CHECK_OUTLINED, 4),
                         ("AI 图片", ft.Icons.AUTO_AWESOME, 5)]),
            ("价格与变种", [("价格设置", ft.Icons.SELL_OUTLINED, 6),
                            ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED, 7)]),
            ("工具", [("图片工作台", ft.Icons.GRID_VIEW, 9)]),
            ("系统", [("设置", ft.Icons.SETTINGS_OUTLINED, 8),
                      ("任务与日志", ft.Icons.RECEIPT_LONG_OUTLINED, 10)]),
        ]
        col = ft.Column(spacing=3)
        for group, items in groups:
            if group:
                col.controls.append(ft.Container(
                    content=ft.Text(group, size=9.5, color="#5D6478", weight=ft.FontWeight.BOLD),
                    padding=ft.padding.only(left=8, top=8, bottom=1)))
            for label, icon, page_idx in items:
                b = ft.Container(
                    content=ft.Row([ft.Icon(icon, size=15, color=MUTED), ft.Text(label, color=MUTED, size=12.5)],
                                   spacing=8),
                    padding=ft.padding.symmetric(horizontal=8, vertical=7), border_radius=8,
                    on_click=lambda e, idx=page_idx: self._switch(idx), ink=True)
                self.nav_buttons.append((page_idx, b))
                col.controls.append(b)
        # 启动/停止 切换按钮（运行时变红并显示「停止」）
        self.btn_run_icon = ft.Icon(ft.Icons.ROCKET_LAUNCH, size=13, color="#0B0D12")
        self.btn_run_text = ft.Text("启动", size=12, weight=ft.FontWeight.BOLD, color="#0B0D12")
        self.btn_run = ft.Container(
            content=ft.Row([self.btn_run_icon, self.btn_run_text],
                           spacing=4, tight=True, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ACCENT, border_radius=7,
            padding=ft.padding.symmetric(vertical=4, horizontal=12),
            on_click=self._toggle_run, ink=True)
        self.license_text = ft.Text("检测中…", color=MUTED, size=12, weight=ft.FontWeight.BOLD)
        # 侧栏导航可滚动：1514×945 及更小的窗口也能点到「提示词 / 任务与日志」
        nav_scroll = ft.Column([col], spacing=0, expand=True, scroll=ft.ScrollMode.AUTO)
        sidebar = ft.Container(width=168, bgcolor=CARD, padding=10, content=ft.Column([
            ft.Row([ft.Container(width=32, height=32, border_radius=10, bgcolor=ACCENT,
                                 content=ft.Text("A", size=15, weight=ft.FontWeight.BOLD, color="#0B0D12")),
                    ft.Column([ft.Text("AuroraList", weight=ft.FontWeight.BOLD, color=TEXT, size=14),
                               muted(f"极光上架 v{VERSION}")], spacing=0)], spacing=10),
            ft.Container(height=10),
            ft.Row([self.btn_run], alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            nav_scroll,
            ft.Container(height=6),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.TERMINAL, size=16, color=MUTED),
                                ft.Text("日志监控", color=MUTED, size=13)], spacing=10),
                padding=ft.padding.symmetric(horizontal=8, vertical=7), border_radius=8,
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
                 self._p_logs]
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
        def do_reset(e):
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
            self.dlg.open = False
            self.update()
        
        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认恢复默认设置"),
            content=ft.Text("所有配置将恢复为默认值（API密钥、店铺、模板等将丢失）。\n\n当前配置会自动备份为 config.backup-*.json"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog()),
                ft.FilledButton("确认恢复", on_click=do_reset),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(self.dlg)
        self.dlg.open = True
        self.update()

    def _close_dialog(self):
        if hasattr(self, 'dlg'):
            self.dlg.open = False
            self.update()

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
        self.wz_store = ft.Dropdown(label="店铺", width=320, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_source = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     value=self.cfg.template_source or "draft",
                                     options=[ft.dropdown.Option("draft", "草稿"), ft.dropdown.Option("online", "在线")],
                                     on_change=lambda e: self._wz_source_changed())
        self.wz_tpl = ft.Dropdown(label="模板产品", width=380, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_status = ft.Text("① 点「读取已登录 Chrome」自动读取登录态", size=12, color=MUTED)
        self.wz_btn = ft.ElevatedButton("① 读取已登录 Chrome", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=self._wz_sync)

        self.wz_dlg = ft.AlertDialog(
            title=ft.Text("欢迎使用 AuroraList · 首次设置"),
            content=ft.Column([
                ft.Text("第 1 步：直接读取你 Chrome 里已登录的店小秘（需要先在 Chrome 登录过）", size=12, color=TEXT),
                ft.Row([self.wz_btn], spacing=10),
                ft.ElevatedButton("读取店铺信息（我已登录）", icon=ft.Icons.SYNC, on_click=self._wz_sync),
                ft.Divider(color=CARD_2),
                ft.Row([self.wz_store, self.wz_source], spacing=10),
                ft.Row([ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT,
                                          on_click=lambda e: self._open_template_picker(self.wz_source.value,
                                                                                        on_pick=self._wz_template_picked)),
                        self.tpl_preview_box], spacing=10),
                self.wz_status,
            ], spacing=12, width=520),
            actions=[ft.TextButton("稍后设置", on_click=lambda e: self._wz_close(False)),
                     ft.ElevatedButton("完成", bgcolor=ACCENT_2, color="#04231C", on_click=self._wz_finish)],
        )
        self.page.dialog = self.wz_dlg
        self.wz_dlg.open = True
        self._safe_update()

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

    def _wz_template_picked(self, pid, title, images):
        # picker 已保存真实选择（含来源）；同步向导，不能让隐藏下拉框的首项覆盖它。
        self.wz_tpl.value = str(pid)
        self.wz_tpl.options = [ft.dropdown.Option(str(pid), title)]
        self.wz_source.value = self.cfg.template_source
        self._set_tpl_preview(images)

    def _wz_finish(self, e):
        if self.wz_store.value:
            self.cfg.store_id = str(self.wz_store.value)
            name = next((o.text for o in self.wz_store.options if o.key == self.wz_store.value), "")
            self.cfg.store_name = name or self.cfg.store_name
        if self.wz_tpl.value and not self.cfg.template_product_id:
            self.cfg.template_product_id = str(self.wz_tpl.value)
            title = next((o.text for o in self.wz_tpl.options if o.key == self.wz_tpl.value), "")
            self.cfg.template_product_title = title or ""
        self.cfg.template_source = self.wz_source.value
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

        # 准备状态：4 项关键就绪度，自动检测
        self.ready_cookie = ft.Text("检测中…", size=12, color=MUTED)
        self.ready_tpl = ft.Text("检测中…", size=12, color=MUTED)
        self.ready_api = ft.Text("检测中…", size=12, color=MUTED)
        self.ready_auth = ft.Text("检测中…", size=12, color=MUTED)

        def _ready_row(icon, label, ctrl):
            return ft.Row([ft.Icon(icon, size=16, color=MUTED),
                           ft.Text(label, size=13, color=TEXT),
                           ctrl], spacing=8, expand=True)

        ready_card = card(ft.Column([
            h1("准备状态", 15),
            muted("都变绿就能一键启动；红色项点击可直接去对应页修复"),
            _ready_row(ft.Icons.COOKIE_OUTLINED, "Cookie", self.ready_cookie),
            _ready_row(ft.Icons.COPY_OUTLINED, "模板商品", self.ready_tpl),
            _ready_row(ft.Icons.CLOUD_OUTLINED, "AI API", self.ready_api),
            _ready_row(ft.Icons.VERIFIED_OUTLINED, "授权", self.ready_auth),
        ], spacing=8))

        def _refresh_ready():
            # Cookie
            try:
                from ..integrations.browser_cookies import describe, resolve
                src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None))
                self.cookie_src = src
                self._cookie_ts = time.time()
                self.ready_cookie.value = "✓ " + describe(src) if (src.ok and src.cookies) else "✗ 未读到（点这里去粘贴）"
                self.ready_cookie.color = ACCENT_2 if (src.ok and src.cookies) else "#FF6B6B"
            except Exception as _e:
                self.ready_cookie.value = f"✗ {str(_e)[:40]}"
                self.ready_cookie.color = "#FF6B6B"
            # 模板
            _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
            self.ready_tpl.value = ("✓ " + str(getattr(self.cfg, "template_product_title", "") or "已选")[:24]) if _tid else "✗ 未选模板（API 保存必需）"
            self.ready_tpl.color = ACCENT_2 if _tid else "#FF6B6B"
            # API / 授权：先触发自动检测，再等结果（最多 12 秒），不要一上来就显示 ✗
            self.ready_api.value = "检测中…"
            self.ready_api.color = MUTED
            self.ready_auth.value = "检测中…"
            self.ready_auth.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            try:
                self._probe_api_async(force=False)
            except Exception:
                pass
            if not (getattr(self, "license_state", None) or {}):
                try:
                    threading.Thread(target=self._check_license, daemon=True).start()
                except Exception:
                    pass
            _deadline = time.time() + 12
            while time.time() < _deadline:
                _st = getattr(self, "api_state", None) or {}
                _ls = getattr(self, "license_state", None) or {}
                if _st and _ls:
                    break
                time.sleep(0.5)
            _st = getattr(self, "api_state", None) or {}
            if _st.get("ok"):
                self.ready_api.value = "✓ " + str(_st.get("detail", "正常"))[:30]
                self.ready_api.color = ACCENT_2
            else:
                self.ready_api.value = ("✗ " + str(_st.get("detail", "API 未连通"))[:34]) if _st else "✗ API 未检测"
                self.ready_api.color = "#FF6B6B"
            _ls = getattr(self, "license_state", None) or {}
            if _ls.get("ok"):
                self.ready_auth.value = "✓ " + str(_ls.get("message", "已授权"))[:24]
                self.ready_auth.color = ACCENT_2
            else:
                self.ready_auth.value = ("✗ " + str(_ls.get("message", "未授权"))[:24]) if _ls else "✗ 未授权"
                self.ready_auth.color = "#FF6B6B"
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=_refresh_ready, daemon=True).start()

        return [prog_card, h1("概览"), muted("AI 能力接入自建站点，全部调用按站点价格计费"),
                ready_card,
                card(ft.Row([ft.Text(f"今日任务 {st['runs']}", color=TEXT, size=13),
                             ft.Text(f"成功 {st['ok']}", color=ACCENT_2, size=13),
                             ft.Text(f"失败 {st['failed']}", color="#FF8A8A", size=13)], spacing=24)),
                self._cost_card(),
                self._resume_card(),
                self._diff_card(),
                ft.Row([stat("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT),
                        auth_card,
                        stat("图片服务", ft.Text("~15 秒/张", color=ACCENT_2, size=12), ft.Icons.BOLT_OUTLINED, "#FFB020")],
                       spacing=12),
                card(ft.Column([h1("运行模式", 15),
                                muted("正式运行前先确认这里是「真实写入」；点左上角「启动」开始"),
                                self._run_mode_row()], spacing=10))]

    # ---------- 概览的状态刷新（ISSUES #8 / #9）----------
    def _diff_card(self):
        """改前后对比预览（标题 / 主图 / 详情长度）。"""
        try:
            from ..services import preview as pv
            rows = pv.latest(5)
        except Exception:
            rows = []
        if not rows:
            return card(ft.Column([h1("改前后对比", 15),
                                   muted("还没有处理记录；跑一次后这里会显示改前/改后差异", 11)], spacing=6))

        def _item(r):
            b = r.get("before") or {}
            a2 = r.get("after") or {}
            bt, at = str(b.get("title") or "-"), str(a2.get("title") or "-")
            bi = (b.get("images") or [""])[0]
            ai = (a2.get("images") or [""])[0]
            bd, ad = len(str(b.get("detail") or "")), len(str(a2.get("detail") or ""))
            thumbs = ft.Row([ft.Image(src=bi, width=70, height=70, fit=ft.ImageFit.COVER,
                                      error_content=ft.Text("改前", size=9, color=MUTED)),
                             ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=MUTED),
                             ft.Image(src=ai, width=70, height=70, fit=ft.ImageFit.COVER,
                                      error_content=ft.Text("改后", size=9, color=MUTED))], spacing=6)
            return ft.Column([
                ft.Text(f"{r.get('id')} · {r.get('title','')[:30]}", size=12, color=TEXT),
                ft.Row([ft.Text("改前", size=10, color=MUTED, width=30),
                        ft.Text(bt[:80], size=11, color=MUTED, expand=True)], spacing=6),
                ft.Row([ft.Text("改后", size=10, color=ACCENT_2, width=30),
                        ft.Text(at[:80], size=11, color=TEXT, expand=True)], spacing=6),
                ft.Row([ft.Text(f"详情 {bd}→{ad} 字符", size=10, color=MUTED), thumbs], spacing=12),
            ], spacing=3)

        return card(ft.Column([h1("改前后对比", 15),
                               muted("最近处理的商品：标题 / 详情 / 主图变化", 11)]
                              + [_item(r) for r in rows], spacing=10))

    def _resume_card(self):
        """断点续传：显示上次批量剩余/失败，提供「继续」。"""
        try:
            from ..services import batch_state as bs
            info = bs.load()
        except Exception:
            info = {}
        rem = len(info.get("remaining") or [])
        fail = len(info.get("failed") or [])
        if not rem and not fail:
            return card(ft.Column([h1("断点续传", 15),
                                   muted("没有未完成的批量；启动一次批量后会自动记录进度", 11)], spacing=6))
        txt = (f"上次批量：完成 {info.get('done', 0)}/{info.get('total', 0)}"
               f"，剩余 {rem} 个" + (f"，失败 {fail} 个" if fail else ""))
        return card(ft.Column([
            h1("断点续传", 15),
            ft.Text(txt, size=12, color=TEXT),
            ft.Row([ft.ElevatedButton("继续处理剩余", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT,
                                      color="white", on_click=lambda e: self._start()),
                    ft.OutlinedButton("清空断点", icon=ft.Icons.CLEAR,
                                      on_click=lambda e: (bs.clear(), self._switch(0)))],
                   spacing=10, wrap=True),
            muted("「继续」会重新扫描待处理商品，已成功的自动跳过，只处理剩余的", 11),
        ], spacing=8))

    def _cost_card(self):
        """成本/用量统计卡片（今日 + 近 7 天 + 按功能拆分）。"""
        try:
            d1 = history.cost_stats(days=1)
            d7 = history.cost_stats(days=7)
        except Exception:
            d1, d7 = {"calls": 0, "total": 0.0, "by_stage": {}}, {"calls": 0, "total": 0.0, "by_stage": {}}
        by_stage = d1.get("by_stage") or {}
        rows = []
        for k, v in list(by_stage.items())[:5]:
            rows.append(ft.Row([ft.Text(f"{k}", size=12, color=TEXT, width=110),
                                ft.Text(f"{v['n']} 次", size=12, color=MUTED, width=70),
                                ft.Text(f"¥{v['cost']:.3f}", size=12, color=ACCENT_2)], spacing=8))
        if not rows:
            rows = [muted("今天还没有 AI 调用记录", 11)]
        return card(ft.Column([
            h1("成本 / 用量", 15),
            ft.Row([ft.Text(f"今日 ¥{d1['total']:.3f}", size=13, color=ACCENT_2),
                    ft.Text(f"（{d1['calls']} 次调用）", size=11, color=MUTED),
                    ft.Text(f"近 7 天 ¥{d7['total']:.3f}", size=13, color=TEXT),
                    ft.Text(f"（{d7['calls']} 次）", size=11, color=MUTED)], spacing=18),
            ft.Column(rows, spacing=3),
        ], spacing=8))

    def _run_mode_row(self):
        """把「试跑 / 真实写入」做成显眼的一行，避免点了半天发现什么都没写。"""
        import flet as ft

        real = bool(getattr(self.cfg, "auto_save_products", False))
        badge = ft.Container(
            content=ft.Text("真实写入" if real else "试跑（不写入店小秘）",
                            size=12, weight=ft.FontWeight.BOLD,
                            color="white" if real else "#3A2A00"),
            bgcolor=ACCENT_2 if real else "#F0A020",
            border_radius=6, padding=ft.padding.symmetric(horizontal=8, vertical=3))
        hint = ft.Text(
            "当前会真的处理图片/文本并保存到店小秘" if real
            else "当前只做预演：不调 AI、不保存。点右边开关切成真实写入",
            size=11, color=MUTED)

        sw = ft.Switch(value=real, active_color=ACCENT_2)

        def on_toggle(e):
            val = bool(sw.value)
            self.cfg.set("auto_save_products", val)
            save_config(self.cfg)
            badge.content.value = "真实写入" if val else "试跑（不写入店小秘）"
            badge.content.color = "white" if val else "#3A2A00"
            badge.bgcolor = ACCENT_2 if val else "#F0A020"
            hint.value = ("当前会真的处理图片/文本并保存到店小秘" if val
                          else "当前只做预演：不调 AI、不保存。点右边开关切成真实写入")
            self._log(f"运行模式：{'真实写入' if val else '试跑（不写入）'}", "INFO")
            try:
                self._safe_update()
            except Exception:
                pass

        sw.on_change = on_toggle
        return ft.Container(
            content=ft.Row([badge, sw, hint], spacing=10, wrap=True),
            bgcolor="#FFF7E6" if not real else "#EAFBF3",
            border_radius=8, padding=10)

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

    # 需要 AI 的开关（任意一个开就要预检）
    _AI_GATES = ("ai_title_enable", "ai_desc_enable", "enable_ai_sku", "review_enable",
                 "estimate_weight_enable", "ai_image_enable", "translate_main_pic_enable",
                 "translate_detail_pic_enable", "translate_sku_pic")

    def _preflight_api(self) -> str:
        """启动前检查 API Key。返回非空字符串 = 需要中止并展示的原因。

        只拦「Key 无效 / 额度不足」这类一定跑不通的情况；
        网络抖动只记 WARN，不阻断（可能只是瞬时）。
        """
        gates = ("ai_title_enable", "ai_desc_enable", "enable_ai_sku", "review_enable",
                 "estimate_weight_enable", "ai_image_enable", "translate_main_pic_enable",
                 "translate_detail_pic_enable", "translate_sku_pic")
        try:
            if not any(bool(getattr(self.cfg, k, False)) for k in gates):
                return ""
        except Exception:
            pass
        key = (getattr(self.cfg, "api_key", "") or "").strip()
        if not key:
            return "未填写 API Key（设置 → API 配置）"
        try:
            client = ApiClient(self.cfg.api_base, key, min(float(self.cfg.timeout or 60), 15))
            models = client.models()
            self._log(f"API 预检通过：{len(models)} 个模型可用", "INFO")
            return ""
        except ApiError as ex:
            from ..api import classify_api_error
            kind = classify_api_error(ex)
            status = int(getattr(ex, "status", 0) or 0)
            msg = str(ex)
            if kind == "auth":
                return f"API Key 无效或已过期（HTTP {status or 401}：{msg[:70]}）——请到设置页重新填写"
            if kind == "quota":
                return f"API 额度不足或请求受限（HTTP {status}：{msg[:70]}）——请充值后重试"
            self._log(f"API 预检异常（不阻断）：{msg[:80]}", "WARN")
            return ""
        except Exception as ex:  # noqa: BLE001
            self._log(f"API 预检网络异常（不阻断）：{str(ex)[:80]}", "WARN")
            return ""

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
        # 会「静默失效」的配置（选了通用编辑却没提示词、三组都没启用……）
        try:
            from ..services.preflight import blocking
            for _b in blocking(cfg):
                if _b not in errs:
                    errs.append(_b)
        except Exception:
            pass
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

    def _sync_run_button(self):
        """根据运行状态切换「启动/停止」按钮外观。"""
        try:
            running = bool(getattr(self, "_running", False))
            stopping = bool(getattr(self, "_stop_requested", False))
            if running or stopping:
                self.btn_run_icon.name = ft.Icons.STOP_CIRCLE
                self.btn_run_icon.color = "white"
                self.btn_run_text.value = "停止中…" if stopping else "停止"
                self.btn_run_text.color = "white"
                self.btn_run.bgcolor = "#D9534F"
            else:
                self.btn_run_icon.name = ft.Icons.ROCKET_LAUNCH
                self.btn_run_icon.color = "#0B0D12"
                self.btn_run_text.value = "启动"
                self.btn_run_text.color = "#0B0D12"
                self.btn_run.bgcolor = ACCENT
            self._safe_update()
        except Exception:
            pass

    def _toggle_run(self, e=None):
        """一个按钮两种状态：运行中→停止，空闲→启动。"""
        if bool(getattr(self, "_running", False)) or bool(getattr(self, "_stop_requested", False)):
            self._request_stop()
        else:
            self._start()
        (getattr(self, '_sync_run_button', None) or (lambda: None))()

    def _warn_conflicts_before_run(self) -> bool:
        """启动前检查开关冲突。返回 True 表示已拦截（用户选择去修改）。"""
        # 先做一次自动修复：开了「AI 生成主图」但提示词为空 = 误开，自动关掉
        try:
            if (getattr(self.cfg, "main_image_generate_enable", False)
                    and not (getattr(self.cfg, "main_image_generate_prompt", "") or "").strip()):
                self.cfg.set("main_image_generate_enable", False)
                save_config(self.cfg)
                self._log("自动修复：「AI 生成主图」开着但提示词为空，已关闭该功能", "WARN")
        except Exception:
            pass
        try:
            from ..services.image_task_plan import detect_conflicts
            cf = detect_conflicts(self.cfg)
        except Exception:
            return False
        hard = [c for c in cf if c.get("level") in ("high", "mid")]
        if not hard:
            # 没有冲突：明确告诉用户「没问题」
            self._log("开关检查：未发现冲突，配置正常", "INFO")
            self.toast("配置检查通过，没有冲突")
            return False
        lines = []
        for c in hard[:6]:
            lines.append(f"[{c['level'].upper()}] {c['title']}\n  {c['detail']}\n  → {c['fix']}")
        text = "\n\n".join(lines)
        self._log("启动前发现开关冲突：" + "；".join(c["title"] for c in hard[:4]), "WARN")

        def go_fix(e):
            self.dlg.open = False
            self._safe_update()
            self._switch(5)   # AI 图片页

        def go_anyway(e):
            self.dlg.open = False
            self._safe_update()
            self._log("用户选择忽略冲突继续启动", "WARN")
            self._start(ignore_conflicts=True)

        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("发现可能的开关冲突"),
            content=ft.Column([
                ft.Text("这些开关同时开启可能互相覆盖，建议先处理：", size=12, color=TEXT),
                ft.Text(text, size=11, color=MUTED, selectable=True),
            ], spacing=8, width=560, scroll=ft.ScrollMode.AUTO, height=320),
            actions=[ft.OutlinedButton("去修改（推荐）", on_click=go_fix),
                     ft.TextButton("仍然启动", on_click=go_anyway)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog = self.dlg
        self.dlg.open = True
        self._safe_update()
        return True

    def _start(self, ignore_conflicts: bool = False):
        # 启动前先把当前配置全部落盘（含未失焦的输入）
        self._save_api_fields()
        # 启动前检查图片开关冲突：有冲突先提醒，用户可强制继续
        if not ignore_conflicts:
            try:
                if self._warn_conflicts_before_run():
                    return
            except Exception:
                pass
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
        """到点执行「定时发布」：商品已保存，这里用 API op=2 直接发布（无浏览器）。"""
        self._log(f"定时发布触发：{job.product_id}（{job.title[:30]}）", "INFO")
        try:
            from ..integrations.dianxiaomi import DianxiaomiClient
            from ..services.api_save import build_save_payload
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            pid = str(job.product_id)
            _tpl = str(getattr(self.cfg, "template_product_id", "") or "").strip()
            target = dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
            tpl = None
            if _tpl:
                tpl = dxm._get("product_detail", {"id": _tpl}).get("data", {}).get("product", {})
            if not target:
                raise RuntimeError("未取到商品详情")
            payload = build_save_payload(target, tpl, action="publish",
                                         custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None))
            resp = dxm.save_product_via_api(payload)
            _code = resp.get("code") if isinstance(resp, dict) else None
            if _code != 0:
                raise RuntimeError(str(resp.get("msg") or "发布失败")[:120])
            self._log(f"定时发布成功：{pid}", "INFO")
            try:
                history.record("scheduled_publish", 1, 0, pid)
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
        self._abort_now = True
        try:                       # 心跳线程也一起停，否则窗口关了它还在跑
            _hb = getattr(self, "_hb_stop", None)
            if _hb is not None:
                _hb.set()
        except Exception:
            pass
        try:
            if getattr(self, "run_status", None) is not None:
                self.run_status.value = "状态：已请求停止（当前任务结束后停）"
                self.run_status.color = "#FFB020"
        except Exception:
            pass
        self._log("已请求停止：当前产品/任务结束后停止", "WARN")
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
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
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
        self._stop_requested = False
        hb_stop = threading.Event()
        self._hb_stop = hb_stop
        self._abort_now = False
        state = {"done": 0, "current": "", "total": 0}

        def finish(msg: str = ""):
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
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
            # 先把「这次会做什么」写进日志，出错时能对照
            try:
                from ..services.preflight import summary as _pf_summary
                for _ln in _pf_summary(self.cfg).splitlines():
                    self._log(_ln, "INFO")
            except Exception:
                pass
            # 启动前 API 预检：Key 无效/额度不足时直接停，不要整批任务全崩在 "Invalid token"
            why = self._preflight_api()
            if why:
                self._log(f"API 预检未通过：{why}", "ERROR")
                finish("API 预检未通过，已停止")
                return
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            all_prods, _meta = dxm.product_page(1, 100, self.cfg.store_id or None, None)
            blocked = [p for p in all_prods if (p.get("status_type") or "") in ("auditing", "onSelling")]
            self._log(f"共 {len(all_prods)} 个产品，跳过审核中/已发布 {len(blocked)} 个", "INFO")
            limit = int(self.cfg.edit_sum or 0) or 0
            _fetch = (max(limit * 4, 40) if limit else 0)
            prods = dxm.pending_products(limit=_fetch, shop_id=self.cfg.store_id or None)
            prods = [x for x in prods if (x.get("status_type") or "") not in
                     ("auditing", "onSelling", "published", "online")]
            _tpl_ids = {str(getattr(self.cfg, "template_product_id", "") or ""),
                        str(getattr(self.cfg, "product_template_id", "") or "")}
            _tpl_ids.discard("")
            if _tpl_ids:
                prods = [x for x in prods if str(x.get("id")) not in _tpl_ids]
            try:
                from ..services import edited as _edited
                prods, _skip_edited = _edited.filter_new(prods)
                if _skip_edited:
                    self._log(f"跳过已编辑过 {_skip_edited} 个产品", "INFO")
            except Exception:
                pass
            if limit:
                prods = prods[:limit]
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

            ok_n = fail_n = skipped_n = 0
            audit_streak = 0
            lock = threading.Lock()
            failed_ids: list[str] = []
            processed_ids: list[str] = []

            def work(item):
                i, prod = item
                nonlocal ok_n, fail_n, skipped_n, audit_streak
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
                    # 让「停止」能中断正在处理的图片，而不是等整批跑完
                    pub.should_stop = lambda: bool(getattr(self, "_abort_now", False))
                    if getattr(self, "_abort_now", False):
                        self._log(f"[{i}/{len(prods)}] 已停止，跳过 {prod.get('id')}", "WARN")
                        return
                    res = pub.run(prod, dry_run=not self.cfg.auto_save_products, manage_progress=False)
                    for line in res.log().splitlines():
                        self._log("   " + line, "INFO" if "✓" in line else "WARN")
                    if getattr(res, "skipped_auditing", False):
                        # 审核中的商品不是失败，也不算成功；单独计数，下次再处理
                        with lock:
                            skipped_n += 1
                            audit_streak += 1
                        self._log("   → 商品审核中，已跳过（不计入失败）", "WARN")
                        # 连续 3 个都因「审核中」被拒，说明这家店整体处于审核锁，
                        # 继续跑只会白花时间（AI 已经调用过了），直接停。
                        if audit_streak >= 3 and not self._stop_requested:
                            self._log("   连续 3 个商品都因「站点审核中」被拒 —— "
                                      "这家店当前整体不能保存，已停止本批（避免继续消耗 AI）",
                                      "ERROR")
                            self._stop_requested = True
                    else:
                        with lock:
                            audit_streak = 0
                            ok_n += 1 if res.ok else 0
                            fail_n += 0 if res.ok else 1
                        if self.cfg.auto_save_products and res.ok:
                            history.record("publish", 1, 0, prod["id"])
                except Exception as ex:  # noqa: BLE001
                    with lock:
                        fail_n += 1
                        failed_ids.append(str(prod.get("id", "")))
                    self._log(f"   失败：{ex}", "ERROR")
                finally:
                    with lock:
                        state["done"] += 1
                        processed_ids.append(str(prod.get("id", "")))

            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, list(enumerate(prods, 1))))
            # 断点：记录剩余/失败，供「继续上次」使用
            try:
                from ..services import batch_state as _bs
                _all_ids = [str(x.get("id", "")) for x in prods]
                _left = [x for x in _all_ids if x not in set(processed_ids)]
                if _left or failed_ids:
                    _bs.save(total=len(prods), done=len(processed_ids),
                             remaining=_left, failed=failed_ids,
                             stopped=bool(self._stop_requested))
                    self._log(f"断点已记录：剩余 {len(_left)} 个，失败 {len(failed_ids)} 个", "WARN")
                else:
                    _bs.clear()
            except Exception:
                pass
            # 刊登完成后，按开关创建「删除未引用图片」任务（店小秘侧异步执行，
            # 已被商品引用的图片不会删，所以是安全的清理方式）
            if not self._stop_requested:
                try:
                    from ..services.album_cleanup import maybe_auto_clean
                    _ck2 = self._cookies()
                    _cli = DianxiaomiClient(_ck2, overrides=self.cfg.endpoint_overrides or {})
                    _r = maybe_auto_clean(self.cfg, _cli)
                    self._log(f"相册清理：{_r.get('message', '')}", "INFO" if _r.get("ok") else "WARN")
                except Exception as _ex:  # noqa: BLE001
                    self._log(f"相册清理失败：{str(_ex)[:80]}", "WARN")

            _tail = f" / 审核中跳过 {skipped_n}" if skipped_n else ""
            _stop = "（已按请求停止）" if self._stop_requested else ""
            self._log(f"完成：成功 {ok_n} / 失败 {fail_n}{_tail}{_stop}", "INFO")
            try:
                history.record("pipeline", ok_n, fail_n)
            except Exception:
                pass
            finish(f"完成：成功 {ok_n} / 失败 {fail_n}{_tail}")
        except Exception as ex:
            self._log(f"运行异常：{ex}", "ERROR")
            finish("运行异常")
        finally:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
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
            muted("自动读取本机 Chrome/Edge 已登录的店小秘；读不到就粘贴一次 Cookie"),
            self.cookie_status,
            ft.Row([ft.ElevatedButton("读取并同步", icon=ft.Icons.SYNC, bgcolor=ACCENT, color="white", on_click=sync),
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=refresh_cookie),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie())],
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
        sid_now = str(getattr(self, "shop_dd", None).value if getattr(self, "shop_dd", None) else "")
        sid_saved = str(getattr(self.cfg, "store_id", "") or "")
        # 只有「有旧店铺」AND「换了不同店铺」才清模板，避免初始化时误清
        if not sid_now or not sid_saved or sid_now == sid_saved:
            # 店铺未变，仍需同步 store_id/name（避免漏存）
            if sid_now and sid_now != sid_saved:
                pass  # 走下面的清模板流程
            else:
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
                    dxm = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    snap = dxm.template_snapshot(
                        str(item.get("product_id") or self.cfg.template_product_id))
                    vals = (snap or {}).get("values") or {}
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
            self.tpl_snapshot_text.value = f"模板参数读取失败：{str(ex)[:80]}"
            self.tpl_snapshot_text.color = "#FF6B6B"
            self._log(f"模板参数读取失败（API）：{str(ex)[:80]}", "WARN")

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

        def sw(key, label, tooltip_text=""):
            ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                            active_color=ACCENT, tooltip=tooltip_text or None)
            def on(e, _k=key):
                self.cfg.set(_k, bool(e.control.value))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        def tf(key, label, width=200, tooltip_text=""):
            ctl = ft.TextField(label=label, value=str(getattr(self.cfg, key, "") or ""),
                               width=width, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                               tooltip=tooltip_text or None)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or ""))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        def dd(key, label, options, width=220, tooltip_text=""):
            ctl = ft.Dropdown(label=label, value=str(getattr(self.cfg, key, options[0][0]) or options[0][0]),
                              options=[ft.dropdown.Option(k, v) for k, v in options],
                              width=width, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              tooltip=tooltip_text or None)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or ""))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        # ---------- 顶部：最常用的两个设置 ----------
        top_card = card(ft.Column([
            h1("运行设置", 15),
            ft.Row([
                tf("edit_sum", "处理产品数量", width=140,
                   tooltip_text="每次运行处理多少个产品（1=只跑1个，0或空=全部）"),
                dd("publish_action", "编辑完成后去向", [
                    ("save_only", "留在采集箱"),
                    ("save_to_waiting", "移入待发布"),
                    ("publish", "直接发布"),
                ], width=200, tooltip_text="产品保存后放在哪个位置"),
                sw("check_banned_word", "违禁词检查",
                   tooltip_text="发布前检查标题/详情是否有违禁词"),
                sw("auto_save_products", "真实写入",
                   tooltip_text="关闭=只试跑预演，不调AI、不保存；开启=真实处理并保存到店小秘"),
            ], spacing=16, wrap=True),
        ], spacing=10))

        # ---------- 图片设置 ----------
        four_in_one_on = bool(getattr(self.cfg, "four_in_one", False))
        four_warn = ft.Container(
            content=ft.Text(
                "⚠ 四合一开启后会将主图1-4合成插入第一位，与「AI精细化编辑·主图1」冲突。\n"
                "建议：关闭「AI精细化编辑·主图1」，或把四合一插入位置改为「末尾」。",
                size=10, color="#FFB020"),
            bgcolor="#FFF7E6", border_radius=8, padding=8,
            visible=four_in_one_on)

        four_sw = ft.Switch(label="四合一主图（将主图1-4合成一张）",
                            value=four_in_one_on, active_color=ACCENT)

        def on_four(e):
            self.cfg.set("four_in_one", bool(four_sw.value))
            save_config(self.cfg)
            four_warn.visible = bool(four_sw.value)
            try:
                self._safe_update()
            except Exception:
                pass

        four_sw.on_change = on_four

        img_card = card(ft.Column([
            h1("图片设置", 15),
            ft.Row([four_sw,
                    dd("four_in_one_position", "四合一位置",
                       [("first", "插入第一位"), ("append", "追加末尾")], width=180,
                       tooltip_text="四合一图片插入的位置（建议「末尾」，避免与AI精细编辑冲突）")],
                   spacing=12, wrap=True),
            four_warn,
            ft.Row([
                sw("main_as_detail", "主图同时作为详情图",
                   tooltip_text="把所有主图也追加到详情图里"),
                sw("pad_main_images_from_extra", "主图不足时从详情图补齐",
                   tooltip_text="主图张数不足时，从详情图中取图补充到指定数量"),
                sw("keep_main_images_local", "保留主图到本地",
                   tooltip_text="将处理后的主图额外保存到本地文件夹"),
            ], spacing=12, wrap=True),
            ft.Divider(height=1, color=CARD_2),
            h1("水印设置", 13),
            self._watermark_card(),
        ], spacing=10))

        # ---------- 详情设置 ----------
        detail_card = card(ft.Column([
            h1("详情设置", 15),
            ft.Row([
                sw("custom_detail_text_enable", "添加自定义详情文本/图片",
                   tooltip_text="在详情HTML头部或尾部插入自定义文字或图片"),
                sw("copy_sku_to_detail", "复制SKU到详情",
                   tooltip_text="把SKU名称列表追加到详情页"),
                sw("delete_detail_text", "删除详情原有文本",
                   tooltip_text="开启后，AI 详情/自定义文本会替换原有详情，而不是叠加"),
            ], spacing=12, wrap=True),
        ], spacing=10))

        # ---------- 基础设置 ----------
        base_card = card(ft.Column([
            h1("规格与库存", 15),
            ft.Row([
                sw("custom_sku_prefix_enable", "启用自定义SKU前缀",
                   tooltip_text="为SKU编码添加自定义前缀"),
                sw("modify_stock_999", "库存改为999",
                   tooltip_text="将所有SKU的库存统一设为999"),
                sw("auto_generate_sku_code", "自动生成SKU编码",
                   tooltip_text="自动为SKU生成唯一编码"),
                sw("original_box", "是否原箱",
                   tooltip_text="商品是否以原箱发货"),
            ], spacing=12, wrap=True),
            ft.Row([
                tf("spec_weight", "重量(kg)", width=120, tooltip_text="默认包装重量（优先使用采集到的重量，未采集时用此值）"),
                tf("spec_length", "长(cm)", width=100),
                tf("spec_width", "宽(cm)", width=100),
                tf("spec_height", "高(cm)", width=100),
            ], spacing=8, wrap=True),
            ft.Row([
                tf("declared_value", "货值默认值", width=160,
                   tooltip_text="申报货值（速卖通草稿必填，建议填实际货值），未填则默认 100"),
                sw("prefer_collected_weight", "优先使用采集重量尺寸",
                   tooltip_text="勾选：用采集到的重量/尺寸（克自动换算为kg）；不勾：用上方填写的值"),
            ], spacing=12, wrap=True),
        ], spacing=10))

        # ---------- 类目属性默认值 ----------
        attr_default_card = card(ft.Column([
            h1("类目属性默认值", 15),
            muted("模板读不到时使用的兜底值"),
            ft.Row([
                dd("high_concerned_chemical", "高关注化学品",
                   [("否", "否"), ("是", "是")], width=160),
                dd("charge_status", "是否带电",
                   [("否", "否"), ("是", "是")], width=160),
            ], spacing=12, wrap=True),
        ], spacing=10))

        # ---------- 属性模板 / SKU替换 ----------
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
            muted("把某个 SKU 的图片换成指定 URL"),
            ft.Row([ft.ElevatedButton("打开替换表", icon=ft.Icons.SWAP_HORIZ, bgcolor=CARD_2, color=TEXT,
                                      on_click=lambda e: self._sku_replace_dialog())], spacing=10, wrap=True),
        ], spacing=8))

        # ---------- 欧盟资质 ----------
        eu_card = card(ft.Column([
            h1("欧盟资质（需要时才填）", 15),
            muted("选「是」时速卖通要求填写，否则保存会被拦截"),
            ft.Row([
                dd("eu_qualification", "是否提交欧盟资质",
                   [("否", "否"), ("是", "是")], width=180),
            ], spacing=12),
            ft.Row([
                tf("eu_responsible_name", "欧盟责任人名称", width=240),
                tf("eu_responsible_address", "责任人地址", width=240),
            ], spacing=12, wrap=True),
            ft.Row([
                tf("eu_responsible_email", "电子邮箱", width=240),
                tf("eu_responsible_phone", "联系电话", width=180),
            ], spacing=12, wrap=True),
        ], spacing=10))

        self._bind_eu_guard()

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
        # 视频来源：采集视频 / 模板视频 / 主图轮播生成 / 不要视频
        self.video_src_dd = ft.Dropdown(
            label="视频来源", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "video_source", "collected") or "collected"),
            options=[ft.dropdown.Option("collected", "采集的产品视频（默认）"),
                     ft.dropdown.Option("template", "模板产品视频（填路径）"),
                     ft.dropdown.Option("generated", "用主图合成轮播视频"),
                     ft.dropdown.Option("none", "不要视频")])

        def _on_video_src(e):
            self.cfg.set("video_source", str(self.video_src_dd.value or "collected"))
            save_config(self.cfg)
            self._log(f"视频来源：{self.video_src_dd.value}")

        self.video_src_dd.on_change = _on_video_src
        self.video_path_tf = ft.TextField(
            label="模板视频路径（选「模板产品视频」时必填）", width=380,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "template_video_path", "") or ""),
            hint_text="/Users/you/video.mp4")

        def _on_video_path(e):
            self.cfg.set("template_video_path", str(e.control.value or "").strip())
            save_config(self.cfg)

        self.video_path_tf.on_change = _on_video_path

        extra_card = card(ft.Column([
            h1("发布设置", 15),
            muted("视频来源、发布去向、相册清理、属性去重等与刊登相关的开关"),
            ft.Row([self.video_src_dd, self.video_path_tf], spacing=12, wrap=True),
            ft.Row([sw("auto_clean_dup_attributes",
                       "保存时清理与系统属性重名的自定义属性（关掉=完全不动属性表）")],
                   spacing=14, wrap=True),
            ft.Row([sw("prefer_dianxiaomi_album", "AI 出图直接传店小秘图片空间（不用图床）"),
                    sw("album_auto_clean_enable", "刊登完成后自动清理相册未引用图片"),
                    ft.TextField(label="清理 N 天前的", width=140,
                                 value=str(getattr(self.cfg, "album_auto_clean_days", 7)),
                                 bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                 on_change=lambda e: (self.cfg.set("album_auto_clean_days",
                                                                  str(e.control.value or "7")),
                                                      save_config(self.cfg)))],
                   spacing=14, wrap=True),
            muted("「自动清理」只删「N 天前上传且没有被任何商品引用」的图片，已被引用的不会动。"),
            ft.Row([self.publish_pos], spacing=10, wrap=True),
            ft.Row([self._edited_count_text(), ft.TextButton("清空已编辑记录", icon=ft.Icons.RESTART_ALT,
                                                              on_click=lambda e: self._clear_edited())],
                   spacing=10, wrap=True),
        ], spacing=10))

        # ---------- 详情本地图 ----------
        def _dl_load():
            import json as _jd
            raw = getattr(self.cfg, "detail_local_images", "[]")
            try:
                return [str(x) for x in (json.loads(raw) if isinstance(raw, str) else raw) if str(x)]
            except Exception:
                return []

        self.dl_paths = _dl_load()
        self.dl_list = ft.Column(spacing=3)
        self.dl_status = ft.Text("", size=11, color=MUTED)

        def _dl_refresh():
            import json as _jd
            self.cfg.set("detail_local_images", _jd.dumps(self.dl_paths, ensure_ascii=False))
            save_config(self.cfg)
            self.dl_list.controls = [
                ft.Row([ft.Icon(ft.Icons.IMAGE_OUTLINED, size=14, color=MUTED),
                        ft.Text(str(x)[-60:], size=11, color=TEXT, expand=True),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=14, icon_color="#FF8A8A",
                                      tooltip="移除",
                                      on_click=lambda e, _x=x: (self.dl_paths.remove(_x), _dl_refresh()))],
                       spacing=6)
                for x in self.dl_paths
            ] or [ft.Text("未选择本地详情图", size=11, color=MUTED)]
            self.dl_status.value = f"已选 {len(self.dl_paths)} 张，刊登时上传图床后插入详情"
            try:
                self._safe_update()
            except Exception:
                pass

        def _dl_pick(e):
            self._dl_picker.pick_files(allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"])

        def _dl_on_result(e):
            if e.files:
                for f in e.files:
                    if f.path and f.path not in self.dl_paths:
                        self.dl_paths.append(f.path)
                _dl_refresh()

        self._dl_picker = ft.FilePicker(on_result=_dl_on_result)
        try:
            self.page.overlay.append(self._dl_picker)
        except Exception:
            pass
        _dl_refresh()
        detail_img_card = card(ft.Column([
            h1("详情本地图", 15),
            muted("从电脑选图片，刊登时上传图床并插入商品详情（头/尾可选）"),
            ft.Row([ft.ElevatedButton("选择图片", icon=ft.Icons.ADD_PHOTO_ALTERNATE, bgcolor=ACCENT,
                                      color="white", on_click=_dl_pick),
                    ft.OutlinedButton("清空", icon=ft.Icons.DELETE_SWEEP,
                                      on_click=lambda e: (self.dl_paths.clear(), _dl_refresh())),
                    ft.Dropdown(width=150, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                value=str(getattr(self.cfg, "detail_local_image_position", "tail") or "tail"),
                                options=[ft.dropdown.Option("head", "插到开头"),
                                         ft.dropdown.Option("tail", "插到结尾"),
                                         ft.dropdown.Option("both", "头尾都插")],
                                on_change=lambda e: (self.cfg.set("detail_local_image_position", e.control.value),
                                                     save_config(self.cfg)))], spacing=10, wrap=True),
            self.dl_list, self.dl_status,
        ], spacing=8))

        return [h1("商品设置"), top_card, img_card, detail_card, base_card,
                attr_default_card, attr_card, sku_map_card, eu_card,
                detail_img_card, extra_card, self._combo_card()]

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
        """图片通用编辑（完全对齐老软件）。

        老软件结构：
            图片通用编辑
              ☑ 启用                      ← 一个总开关，开了就自动执行
              [通用编辑][精细化编辑]       ← 二选一
              通用编辑   ：一条提示词 + 主图范围（编辑到第几张）
              精细化编辑 ：主图编辑 / 详情图编辑 / SKU 图片编辑
                          每组各自「启用」+ 逐条（位置 + 提示词 + 参考图）+ 「添加」按钮
        """
        from ..services.image_jobs import (parse_rules, rows_to_rules, parse_jobs, rows_to_jobs,
                                           migrate, GROUP_LABELS)

        # 旧配置一次性迁移（四合一/水印/逐图方案 → 新结构）
        try:
            rj, jj = migrate(self.cfg, with_global=True)
            if rj != (self.cfg.image_simple_rules or "[]") or jj != (self.cfg.image_ai_jobs or "[]"):
                self.cfg.set("image_simple_rules", rj)
                self.cfg.set("image_ai_jobs", jj)
                save_config(self.cfg)
        except Exception:
            pass

        state = {"jobs": parse_jobs(self.cfg.image_ai_jobs),
                 "rules": parse_rules(self.cfg.image_simple_rules),
                 "timer": None}

        def _get(key, default=None):
            return getattr(self.cfg, key, default)

        summary = ft.Text("", size=11, color=MUTED)

        def persist():
            self.cfg.set("image_ai_jobs", rows_to_jobs(state["jobs"]))
            self.cfg.set("image_simple_rules", rows_to_rules(state["rules"]))
            save_config(self.cfg)
            refresh_summary()
            try:
                self._safe_update()
            except Exception:
                pass

        def schedule():
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

        def refresh_summary():
            n = {g: len([j for j in state["jobs"] if j["group"] == g]) for g in ("main", "detail", "sku")}
            parts = []
            if _get("img_fine_main_enable", True) and n["main"]:
                parts.append(f"AI主图 {n['main']} 条")
            if _get("img_fine_detail_enable", False) and n["detail"]:
                parts.append(f"AI详情图 {n['detail']} 条")
            if _get("img_fine_sku_enable", False) and n["sku"]:
                parts.append(f"AI SKU {n['sku']} 条")
            summary.value = " · ".join(parts) if parts else "未启用任何AI图片编辑"
            summary.color = ACCENT_2 if parts else MUTED

        body = ft.Column(spacing=12)

        # ---------- 参考图选择器（复用同一个，避免堆积 overlay）----------
        if not getattr(self, "_ref_picker", None):
            self._ref_picker = ft.FilePicker(on_result=lambda e: None)
            try:
                self.page.overlay.append(self._ref_picker)
            except Exception:
                pass

        def pick_refs(job, rerender):
            def on_res(e):
                if not e.files:
                    return
                cur = list(job.get("ref_paths") or [])
                for f in e.files:
                    if len(cur) >= 5:
                        break
                    p = str(getattr(f, "path", "") or "")
                    if p and p not in cur:
                        cur.append(p)
                job["ref_paths"] = cur[:5]
                rerender()
                persist()
            self._ref_picker.on_result = on_res
            try:
                self._ref_picker.pick_files(allow_multiple=True,
                                            allowed_extensions=["png", "jpg", "jpeg", "webp"])
            except Exception as ex:
                self.toast(f"选择参考图失败：{ex}", False)

        # ---------- 精细化编辑：单条任务 ----------
        MAX_POS = {"main": 10, "detail": 12, "sku": 10}

        def job_row(job, g, rerender):
            pos = ft.Dropdown(
                label="图片位置", width=132, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                value=str(min(MAX_POS[g], max(1, int(job.get("index") or 1)))),
                options=[ft.dropdown.Option(str(i), f"{GROUP_LABELS.get(g, g)} {i}")
                         for i in range(1, MAX_POS[g] + 1)])

            # AI提示词预设下拉框
            preset_options = ["自定义"] + list(IMAGE_PROMPT_PRESETS.get(g, {}).keys())
            current_preset = job.get("preset") or "自定义"
            if current_preset not in preset_options:
                current_preset = "自定义"

            preset_dropdown = ft.Dropdown(
                label="AI提示词预设",
                width=180,
                value=current_preset,
                options=[ft.dropdown.Option(p) for p in preset_options],
                bgcolor=CARD_2,
                border_color=CARD_2,
                color=TEXT
            )

            prompt = ft.TextField(label="AI 提示词", value=str(job.get("prompt") or ""),
                                  multiline=True, min_lines=3, max_lines=12, expand=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            refs = list(job.get("ref_paths") or [])
            refs_btn = ft.TextButton(
                f"参考图（{len(refs)}/5）", icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                tooltip="上传本地参考图（最多5张）。图片顺序：先本地参考图，后产品原图。提示词里用「图1」「图2」引用。",
                on_click=lambda e: pick_refs(job, rerender))
            refs_label = ft.Text(
                "、".join(Path(p).name for p in refs) if refs else "",
                size=10, color=MUTED)

            def clear_refs(e=None):
                job["ref_paths"] = []
                rerender()
                persist()

            refs_clear = ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color="#FF8A8A",
                                       tooltip="清空参考图", visible=bool(refs), on_click=clear_refs)

            def on_preset(e):
                selected = preset_dropdown.value
                job["preset"] = selected
                if selected != "自定义":
                    # 填充预设提示词
                    preset_text = IMAGE_PROMPT_PRESETS.get(g, {}).get(selected, "")
                    if preset_text:
                        prompt.value = preset_text.strip()
                        job["prompt"] = preset_text.strip()
                schedule()
                try:
                    self._safe_update()
                except Exception:
                    pass

            def on_pos(e):
                try:
                    job["index"] = max(1, int(pos.value or 1))
                except Exception:
                    job["index"] = 1
                schedule()

            def on_prompt(e):
                job["prompt"] = str(prompt.value or "").strip()
                # 如果用户手动修改了提示词，切换到"自定义"
                if job["prompt"] and preset_dropdown.value != "自定义":
                    # 检查是否还匹配预设
                    preset_text = IMAGE_PROMPT_PRESETS.get(g, {}).get(preset_dropdown.value, "")
                    if job["prompt"].strip() != preset_text.strip():
                        preset_dropdown.value = "自定义"
                        job["preset"] = "自定义"
                schedule()

            pos.on_change = on_pos
            preset_dropdown.on_change = on_preset
            prompt.on_change = on_prompt

            def remove(e):
                try:
                    state["jobs"].remove(job)
                except ValueError:
                    pass
                rerender()
                persist()

            return ft.Container(
                content=ft.Column([
                    ft.Row([pos, preset_dropdown,
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A",
                                          tooltip="删除这一条", on_click=remove)],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
                    prompt,
                    ft.Row([refs_btn, refs_clear, refs_label], spacing=4, wrap=True),
                ], spacing=4),
                bgcolor=CARD_2, border_radius=10, padding=10)

        # ---------- 精细化编辑：一个分组 ----------
        def group_section(g: str, title: str, enable_key: str, rerender,
                          extra_controls: list | None = None):
            enabled = ft.Checkbox(
                label=title,
                value=bool(_get(enable_key, g == "main")),
                label_style=ft.TextStyle(size=12, weight=ft.FontWeight.BOLD),
                tooltip=f"勾选后上架时对{title}启用 AI 处理")

            jobs = [j for j in state["jobs"] if j["group"] == g]
            rows_box = ft.Column([job_row(j, g, rerender) for j in jobs], spacing=8)

            def on_enable(e):
                self.cfg.set(enable_key, bool(enabled.value))
                # 只要有任何分组开启，就确保总开关也是开的
                if enabled.value:
                    self.cfg.set("ai_image_enable", True)
                save_config(self.cfg)
                refresh_summary()
                try:
                    self._safe_update()
                except Exception:
                    pass

            enabled.on_change = on_enable

            def add_job(e=None):
                state["jobs"].append({"group": g, "index": 1, "refs": "", "prompt": "",
                                      "mode": "edit", "count": 1, "insert": "replace",
                                      "preset": "", "ref_paths": []})
                rerender()
                persist()

            def batch_add(e=None):
                existing_positions = {j.get("index") for j in jobs}
                default_preset_key = list(IMAGE_PROMPT_PRESETS.get(g, {}).keys())[0] if IMAGE_PROMPT_PRESETS.get(g) else None
                default_prompt = IMAGE_PROMPT_PRESETS.get(g, {}).get(default_preset_key, "") if default_preset_key else ""
                max_pos = MAX_POS.get(g, 10)
                added = 0
                for i in range(1, min(max_pos + 1, 9)):
                    if i not in existing_positions:
                        state["jobs"].append({
                            "group": g, "index": i, "refs": "",
                            "prompt": default_prompt, "mode": "edit", "count": 1,
                            "insert": "replace", "preset": default_preset_key or "自定义", "ref_paths": []
                        })
                        added += 1
                if added > 0:
                    rerender()
                    persist()
                    self.toast(f"已批量添加 {added} 条任务", True)
                else:
                    self.toast("所有位置都已有任务", False)

            job_count = ft.Text(
                f"{len(jobs)} 条任务" if jobs else "未添加任务",
                size=11, color=ACCENT_2 if jobs else MUTED)

            add_btn = ft.OutlinedButton(
                f"+ 添加", icon=ft.Icons.ADD, on_click=add_job,
                tooltip=f"逐个添加{title}任务，自定义每张图的 AI 提示词")
            batch_btn = ft.OutlinedButton(
                "批量 1-8 张", icon=ft.Icons.PLAYLIST_ADD, on_click=batch_add,
                tooltip=f"一键为所有{title}（1-8张）添加任务，使用默认预设")

            controls = [
                ft.Row([enabled, job_count, ft.Row([add_btn, batch_btn], spacing=6)],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                *((extra_controls or [])),
                rows_box if jobs else None,
            ]
            controls = [c for c in controls if c is not None]

            return ft.Container(
                content=ft.Column(controls, spacing=8),
                border=ft.border.all(1, CARD_2), border_radius=12, padding=12)

        # ---------- 精细化编辑面板 ----------
        def fine_panel(rerender):
            det_replace = ft.Checkbox(
                label="删除原有详情图，仅保留 AI 编辑结果",
                value=bool(_get("img_fine_detail_replace", False)),
                tooltip="开启后，原详情图全部清除，只保留本次 AI 编辑生成的图片")

            def on_det_replace(e):
                self.cfg.set("img_fine_detail_replace", bool(det_replace.value))
                save_config(self.cfg)

            det_replace.on_change = on_det_replace

            return ft.Column([
                group_section("main", "AI 主图编辑", "img_fine_main_enable", rerender),
                group_section("detail", "AI 详情图编辑", "img_fine_detail_enable", rerender,
                              extra_controls=[det_replace]),
                group_section("sku", "AI SKU 图编辑", "img_fine_sku_enable", rerender),
            ], spacing=12)

        # ---------- 渲染 ----------
        def render():
            body.controls = [fine_panel(render)]
            refresh_summary()
            try:
                self._safe_update()
            except Exception:
                pass

        render()

        # ---------- 图片尺寸校验（速卖通 ≥800×800）----------
        size_on = ft.Checkbox(label="图片尺寸校验", value=bool(_get("img_min_size_enable", True)))
        size_val = ft.TextField(label="最小边长(px)", width=140,
                                value=str(_get("img_min_size", 800)),
                                bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        size_drop = ft.Checkbox(
            label="连原图里也不合规的一起去掉（默认关，避免误删主图）",
            value=bool(_get("img_min_size_drop_invalid", False)))

        def on_size_on(e):
            self.cfg.set("img_min_size_enable", bool(size_on.value))
            save_config(self.cfg)

        def on_size_val(e):
            try:
                v = max(1, int(str(size_val.value or "800").strip()))
            except Exception:
                v = 800
            self.cfg.set("img_min_size", v)
            schedule()

        def on_size_drop(e):
            self.cfg.set("img_min_size_drop_invalid", bool(size_drop.value))
            save_config(self.cfg)

        size_on.on_change = on_size_on
        size_val.on_change = on_size_val
        size_drop.on_change = on_size_drop

        audit_status = ft.Text("", size=11, color=MUTED)

        def run_audit(e=None):
            prods = list(getattr(self, "products", None) or [])
            if not prods:
                self.toast("还没有加载商品，请先在「上架」页拉取产品", False)
                return
            ms = int(_get("img_min_size", 800) or 800)

            def work():
                from ..services.image_validation import audit_products
                audit_status.value = f"检查中… 0/{len(prods)}"
                try:
                    self._safe_update()
                except Exception:
                    pass

                def prog(n, total, title):
                    audit_status.value = f"检查中… {n}/{total} {title}"
                    try:
                        self._safe_update()
                    except Exception:
                        pass

                try:
                    res = audit_products(prods, ms, progress=prog)
                except Exception as ex:  # noqa: BLE001
                    audit_status.value = f"检查失败：{ex}"
                    audit_status.color = "#FF8A8A"
                    self._log(f"图片体检失败：{ex}", "WARN")
                    try:
                        self._safe_update()
                    except Exception:
                        pass
                    return
                bad_lines = []
                for pr in res["products"]:
                    for idx, url, info in pr["bad"]:
                        bad_lines.append(f"[{pr['id']}] 第{idx}张 {info}")
                ok = res["failed"] == 0
                audit_status.value = (f"{'✓' if ok else '✗'} 共 {res['total']} 张："
                                      f"合格 {res['passed']} / 不合格 {res['failed']}（阈值 {ms}px）")
                audit_status.color = ACCENT_2 if ok else "#FF8A8A"
                self._log(audit_status.value, "INFO" if ok else "WARN")
                for line in bad_lines[:20]:
                    self._log("  · " + line, "WARN")
                try:
                    self._show_audit_dialog(res, ms)
                except Exception:
                    pass
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        audit_btn = ft.OutlinedButton("检查图片尺寸", icon=ft.Icons.SEARCH,
                                      on_click=lambda e: run_audit())

        # ---------- 使用说明 ----------
        usage_guide = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color="#4B3B8F"),
                    ft.Text("使用说明", size=12, weight=ft.FontWeight.BOLD, color="#4B3B8F")
                ], spacing=6),
                ft.Text(
                    "• 分组开关：控制主图/详情图/SKU图是否处理\n"
                    "• 添加任务：为每张图片设置独立的AI提示词\n"
                    "• 批量添加：一键为所有图片添加任务（使用默认预设）",
                    size=10, color="#4B3B8F"),
                ft.Text(
                    "💡 常见场景：\n"
                    "  - 只处理主图：勾选「AI 主图编辑」，添加主图任务\n"
                    "  - 批量编辑主图：点击「批量 1-8 张」，一键添加所有主图任务",
                    size=10, color="#6B5B9F"),
            ], spacing=6),
            bgcolor="#EFEBFB", border_radius=10, padding=12)

        return card(ft.Column([
            h1("图片通用编辑", 15),
            ft.Row([summary], spacing=12),
            body,
            ft.Divider(height=1, color=CARD_2),
            ft.Row([size_on, size_val, audit_btn], spacing=10, wrap=True),
            muted("AI 产出的图小于最小边长时不采用（保留原图）；速卖通要求主图/SKU图/详情图 ≥800×800"),
            size_drop,
            audit_status,
        ], spacing=10))

    def _plan_preview(self):
        """「这次会做什么」——把配置翻译成人话，并指出静默失效的项。"""
        box = ft.Column(spacing=2)
        status = ft.Text("", size=10, color=MUTED)

        def refresh(e=None):
            try:
                from ..services.preflight import image_plan_lines, check
                lines = image_plan_lines(self.cfg)
                issues = [i for i in check(self.cfg) if i.level in ("error", "warn")]
                ctrls = [ft.Row([ft.Icon(ft.Icons.PLAY_ARROW, size=14, color=ACCENT_2),
                                 ft.Text(t, size=11, color=TEXT, selectable=True)], spacing=6)
                         for t in lines]
                for i in issues[:6]:
                    color = "#FF8A8A" if i.level == "error" else "#FFB020"
                    ctrls.append(ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14, color=color),
                                         ft.Text(i.text, size=11, color=color, selectable=True)], spacing=6))
                box.controls = ctrls or [ft.Text("（暂无图片处理配置）", size=11, color=MUTED)]
                status.value = "已按当前配置生成，改完上面的设置点刷新"
                try:
                    self._safe_update()
                except Exception:
                    pass
            except Exception as ex:  # noqa: BLE001
                box.controls = [ft.Text(f"生成预览失败：{ex}", size=11, color="#FF8A8A")]
                try:
                    self._safe_update()
                except Exception:
                    pass

        refresh()
        return ft.Container(
            content=ft.Column([
                ft.Row([ft.Text("这次会做什么", size=13, weight=ft.FontWeight.BOLD, color=TEXT),
                        ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=refresh)], spacing=8),
                box, status,
            ], spacing=6),
            bgcolor="#F4F7FF", border_radius=10, padding=10)

    def _show_audit_dialog(self, res: dict, min_size: int):
        """展示图片尺寸体检结果（只读，不改任何商品数据）。"""
        rows = []
        for pr in res.get("products", []):
            bad = pr.get("bad") or []
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(pr.get("title") or pr.get("id") or "", size=11, color=TEXT)),
                ft.DataCell(ft.Text(f"{pr.get('ok', 0)}/{pr.get('checked', 0)}", size=11, color=TEXT)),
                ft.DataCell(ft.Text("—" if not bad else f"{len(bad)} 张",
                                    size=11, color=ACCENT_2 if not bad else "#FF8A8A")),
                ft.DataCell(ft.Text("；".join(f"第{i}张 {info}" for i, _u, info in bad[:3]) or "",
                                    size=10, color=MUTED, selectable=True)),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("产品", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("合格", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("不合格", size=11, color=MUTED)),
                     ft.DataColumn(ft.Text("明细", size=11, color=MUTED))],
            rows=rows, column_spacing=14, heading_row_height=28, data_row_min_height=26)
        head = ft.Text(
            f"阈值 {min_size}×{min_size}：共 {res.get('total', 0)} 张，"
            f"合格 {res.get('passed', 0)}，不合格 {res.get('failed', 0)}",
            size=12, color=TEXT, weight=ft.FontWeight.BOLD)
        self.dlg = ft.AlertDialog(
            title=ft.Text("图片尺寸体检"),
            content=ft.Container(width=760, height=420,
                                 content=ft.Column([head,
                                                    ft.Row([table], scroll=ft.ScrollMode.AUTO)],
                                                   spacing=8, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dialog())],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.overlay.append(self.dlg)
        self.dlg.open = True
        self._safe_update()

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
            if getattr(self, "img_status", None) is not None:
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

        cards = [
            add_section("AI 文案（标题 / 详情 / SKU / 估重）", schema_ai(), expanded=True),
        ]
        return [h1("AI 文案"),
                muted("标题 / 详情 / SKU / 估重的提示词与开关；图片处理见「AI 图片」，审查见「内容审查」"),
                *cards,
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

        # 审查设置（从 AI 页移过来）
        self.field_refs = []
        rev_sections = []
        for name, fields in schema_review():
            c, refs = section(name, fields, self.cfg)
            self.field_refs.extend(refs)
            for f in refs:
                self._bind_autosave(f)
            rev_sections.append(c)

        card_rev = card(ft.Column([
            h1("审查结果", 15),
            muted("拉取店铺产品，逐条跑文本审查 + 主图审查；不合规会标红（发布时可按设置拦截）"),
            ft.Row([self.rev_scope, self.rev_limit,
                    ft.ElevatedButton("开始审查", icon=ft.Icons.FACT_CHECK, bgcolor=ACCENT, color="white", on_click=start),
                    self.rev_status], spacing=10, wrap=True),
            self.rev_table,
        ], spacing=8))
        return [view[0], *rev_sections, card_rev] + view[1:]

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
        """AI 图片：只保留两大块 —— 简单编辑 / 精细化编辑。"""
        self._current = 5
        view = self._render("AI 图片", [])
        self.field_refs = []

        def sw(key, label, tip=""):
            ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                            active_color=ACCENT, tooltip=tip or None)
            def on(e, _k=key):
                self.cfg.set(_k, bool(e.control.value))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        def add_section(title, schemas, expanded=True):
            controls = []
            for name, fields in schemas:
                c, refs = section(name, fields, self.cfg)
                self.field_refs.extend(refs)
                for f in refs:
                    self._bind_autosave(f)
                controls.append(c)
            return ft.ExpansionTile(
                title=ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=TEXT),
                controls=controls, initially_expanded=expanded,
                icon_color=ACCENT, collapsed_icon_color=MUTED)

        # ---- 写回（统一成一个卡片，措辞直白）----
        write_card = card(ft.Column([
            h1("处理结果写回商品", 15),
            muted("AI 处理完的图片 / 详情 / SKU 是否写回商品；全关=只在本地生成、不写回"),
            ft.Row([sw("write_main_images_to_product", "图片写回主图"),
                    sw("write_detail_html_to_product", "详情写回商品"),
                    sw("write_sku_codes_to_product", "SKU 名称写回")], spacing=16, wrap=True),
        ], spacing=8))

        # ---- 营销图（可选增强）----
        _mk_on = ft.Switch(label="生成营销图（写进营销图片 1:1 / 3:4）",
                           value=bool(getattr(self.cfg, "marketing_auto_generate", False)),
                           active_color=ACCENT)

        def _on_mk_on(e):
            self.cfg.set("marketing_auto_generate", bool(_mk_on.value))
            save_config(self.cfg)

        _mk_on.on_change = _on_mk_on

        self.mk_src_dd = ft.Dropdown(
            label="营销图来源", width=300, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "marketing_source", "local") or "local"),
            options=[ft.dropdown.Option("local", "本地裁切（店小秘同款·免费·秒出）"),
                     ft.dropdown.Option("ai", "AI 生成场景图（花钱·约15秒/张）")])

        def _on_mk_src(e):
            self.cfg.set("marketing_source", str(self.mk_src_dd.value or "local"))
            save_config(self.cfg)
            self._log(f"营销图来源：{self.mk_src_dd.value}")

        self.mk_src_dd.on_change = _on_mk_src

        _mk_presets = list(IMAGE_PROMPT_PRESETS.get("marketing", {}).keys())
        self.mk_preset_dd = ft.Dropdown(
            label="AI 提示词预设（机械类目）", width=260, bgcolor=CARD_2, border_color=CARD_2,
            color=TEXT, value=_mk_presets[0] if _mk_presets else None,
            options=[ft.dropdown.Option(k) for k in _mk_presets])

        self.mk_prompt = ft.TextField(
            label="营销图提示词", multiline=True, min_lines=5, max_lines=9,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "marketing_image_prompt", "") or ""))

        def _on_mk_preset(e):
            _k = self.mk_preset_dd.value
            _txt = IMAGE_PROMPT_PRESETS.get("marketing", {}).get(_k)
            if _txt:
                self.mk_prompt.value = _txt
                self.cfg.set("marketing_image_prompt", _txt)
                save_config(self.cfg)
                self._safe_update()

        self.mk_preset_dd.on_change = _on_mk_preset

        def _on_mk_prompt(e):
            self.cfg.set("marketing_image_prompt", str(e.control.value or ""))
            save_config(self.cfg)

        self.mk_prompt.on_change = _on_mk_prompt

        marketing_card = card(ft.Column([
            h1("营销图（可选）", 15),
            muted("本地裁切=店小秘「一键生成」同款，免费秒出；AI 场景图=按提示词生成，约15秒/张"),
            _mk_on,
            ft.Row([self.mk_src_dd, self.mk_preset_dd], spacing=12, wrap=True),
            self.mk_prompt,
        ], spacing=10))

        # ---- 简单编辑：白底 / 去水印 / 翻译（带范围）----
        simple_keys = [
            "white_background_pic_1", "white_background_pic_2", "white_bg_enable",
            "remove_watermark_pic_1", "remove_watermark_pic_2", "watermark_enable",
            "translate_main_pic_enable", "translate_main_pic_indices",
            "white_background_detail_pic_indices", "img_detail_white_bg", "img_detail_remove_wm",
            "translate_detail_pic_enable", "translate_detail_pic_indices",
            "white_background_sku_pic_indices", "white_background_sku_pic",
            "img_sku_white_bg", "img_sku_remove_wm",
            "translate_sku_pic",
        ]
        simple = []
        for name, fields in schema_image():
            fs = [f for f in fields if f.key in simple_keys]
            if fs:
                simple.append((name, fs))
        simple_tab = ft.Column([
            add_section("主图 / 详情图 / SKU 图：白底 · 去水印 · 翻译", simple, expanded=True),
            card(ft.Column([
                muted("勾选后按「范围」处理；范围写法 1-3,5 或留空=全部。", 11),
                ft.Row([sw("translate_image_understanding", "AI 判断图片是否需要翻译"),
                        sw("img_sku_edit", "SKU 图也做精细编辑")], spacing=16, wrap=True),
            ], spacing=6)),
        ], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

        # ---- 精细化编辑：主图 / 详情图 / SKU（逐条：位置+提示词+参考图）----
        refined_tab = ft.Column([self._image_jobs_ui()], spacing=10,
                                scroll=ft.ScrollMode.AUTO, expand=True)

        # ---- 开关冲突提示（自动检测）----
        try:
            from ..services.image_task_plan import detect_conflicts
            _cf = detect_conflicts(self.cfg)
        except Exception:
            _cf = []
        _colors = {"high": "#FF6B6B", "mid": "#FFB020", "low": MUTED}
        _icon = {"high": ft.Icons.ERROR_OUTLINE, "mid": ft.Icons.WARNING_AMBER_ROUNDED,
                 "low": ft.Icons.INFO_OUTLINE}
        conflict_card = card(ft.Column([
            h1("开关检查", 15),
            muted("同时开多个图片开关时的冲突提示；红色=建议先处理"),
            *([ft.Row([ft.Icon(_icon.get(c["level"], ft.Icons.INFO_OUTLINE), size=15,
                               color=_colors.get(c["level"], MUTED)),
                       ft.Column([ft.Text(c["title"], size=12, color=TEXT),
                                  ft.Text(c["detail"], size=10.5, color=MUTED),
                                  ft.Text("→ " + c["fix"], size=10.5, color=ACCENT_2)],
                                 spacing=1, expand=True)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
               for c in _cf] if _cf else
              [ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, size=15, color=ACCENT_2),
                       ft.Text("没有检测到开关冲突", size=12, color=ACCENT_2)], spacing=8)]),
        ], spacing=6))

        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=150,
            tabs=[ft.Tab(text="简单编辑", content=simple_tab),
                  ft.Tab(text="精细化编辑", content=refined_tab)],
            expand=True,
        )
        return [view[0], muted("简单编辑：整体白底/去水印/翻译；精细化编辑：逐张指定提示词与参考图"),
                write_card, conflict_card, marketing_card, tabs, view[-1]]

    # ---------- 价格设置（表格化，不再让用户写 JSON）----------
    def _p_price(self):
        from ..services.pricing import compute_price, tiers_to_rows, rows_to_tiers
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
            h1("阶梯倍率（内部计价）", 15),
            muted("按成本落在哪一档，就用该档倍率（成本 × 倍率 + 固定加价）；这是本地计价，不是平台阶梯价"),
            ft.Row([ft.ElevatedButton("添加一档", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=tier_add),
                    ft.OutlinedButton("套用示例（3 档）", icon=ft.Icons.AUTO_AWESOME, on_click=tier_template)],
                   spacing=10, wrap=True),
            tier_col, tier_status,
        ], spacing=8))

        # ---- 区域调价（店小秘风格：按国家设绝对价）----
        def _rq_load():
            raw = getattr(self.cfg, "regional_quotes", "[]")
            if isinstance(raw, str):
                try:
                    import json as _jq
                    raw = _jq.loads(raw)
                except Exception:
                    raw = []
            return [dict(r) for r in (raw or []) if isinstance(r, dict)]

        rq_state = {"rows": _rq_load()}
        rq_col = ft.Column(spacing=6)
        rq_status = ft.Text("", size=11, color=MUTED)

        def rq_persist():
            import json as _jq
            self.cfg.set("regional_quotes", _jq.dumps(rq_state["rows"], ensure_ascii=False))
            save_config(self.cfg)
            rq_status.value = (f"已设置 {len(rq_state['rows'])} 个国家/地区"
                               if rq_state["rows"] else "未设置区域价：开启「启用区域调价」后生效")
            rq_status.color = ACCENT_2 if rq_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def rq_row(row: dict):
            cur = ft.Dropdown(label="国家/地区", width=170, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              value=str(row.get("country") or "US"),
                              options=[ft.dropdown.Option(c, f"{c} · {n}") for c, n in REGIONS])
            price = ft.TextField(label="绝对价（元）", value=str(row.get("price", 0)), width=130,
                                 bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def sync(e=None):
                row["country"] = cur.value or "US"
                try:
                    row["price"] = float(price.value or 0)
                except Exception:
                    return
                rq_persist()

            cur.on_change = sync
            price.on_change = sync

            def remove(e):
                try:
                    rq_state["rows"].remove(row)
                except ValueError:
                    pass
                rq_render(); rq_persist()

            return ft.Row([cur, price,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="#FF8A8A", on_click=remove)],
                          spacing=8, wrap=True)

        def rq_render():
            rq_col.controls = [rq_row(r) for r in rq_state["rows"]] or [
                ft.Text("还没有区域价。点「添加国家」，例如 US 12.99、RU 15.99。", size=11, color=MUTED)]
            try:
                self._safe_update()
            except Exception:
                pass

        def rq_add(e=None):
            used = {r.get("country") for r in rq_state["rows"]}
            nxt = next((c for c, _ in REGIONS if c not in used), "US")
            rq_state["rows"].append({"country": nxt, "price": 0})
            rq_render(); rq_persist()

        rq_render()
        reg_card = card(ft.Column([
            h1("区域调价", 15),
            muted("按国家/地区设置绝对成交价（店小秘同款：选中哪个国家就用哪个价）"),
            ft.Switch(label="启用区域调价", value=bool(getattr(self.cfg, "regional_quote_enable", False)),
                      active_color=ACCENT,
                      on_change=lambda e: (self.cfg.set("regional_quote_enable", bool(e.control.value)),
                                           save_config(self.cfg))),
            ft.Row([ft.ElevatedButton("添加国家", icon=ft.Icons.ADD, bgcolor=ACCENT, color="white", on_click=rq_add)],
                   spacing=10, wrap=True),
            rq_col, rq_status,
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

        # ---- 批发（店小秘风格：购买 N 件减免 X% + 一件代发折扣）----
        def _num_field(key, label, width, tooltip):
            ctl = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                               color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                               tooltip=tooltip)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or "").strip())
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        self.bulk_order = _num_field("bulk_order", "购买 N 件以上", 140, "2~100000")
        self.bulk_discount = _num_field("bulk_discount", "减免 X%", 110, "1~99")
        self.dropship_discount = _num_field("dropship_discount", "一件代发折扣 Y%", 150, "0~99")

        ws_card = card(ft.Column([
            h1("批发", 15),
            muted("店小秘同款：勾选后，买家一次购买满 N 件自动减免 X%"),
            ft.Switch(label="启用批发", value=bool(getattr(self.cfg, "wholesale_enable", False)),
                      active_color=ACCENT,
                      on_change=lambda e: (self.cfg.set("wholesale_enable", bool(e.control.value)),
                                           save_config(self.cfg))),
            ft.Row([self.bulk_order, self.bulk_discount, self.dropship_discount], spacing=12, wrap=True),
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
        # SKU 编码写回开关（多 SKU 时是硬约束：关了才能继续）
        sku_write_cb = ft.Checkbox(
            label="SKU 编码写回商品（多 SKU 商品需要先关掉才能继续）",
            value=bool(getattr(self.cfg, "write_sku_codes_to_product", True)))

        def on_sku_write(e):
            self.cfg.set("write_sku_codes_to_product", bool(sku_write_cb.value))
            save_config(self.cfg)
            self._log(f"SKU 编码写回：{'开' if sku_write_cb.value else '关'}")

        sku_write_cb.on_change = on_sku_write
        write_card = card(ft.Column([
            h1("SKU 写回", 15),
            sku_write_cb,
            muted("产品有多于 1 个 SKU 时，编辑器行身份还无法可靠匹配；"
                  "先关掉这个开关即可继续上架（编码不写回，价格/库存照常）。"),
        ], spacing=8))

        view = self._render("变种设置", schema_variant())
        return [view[0], write_card] + view[1:]

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
        self.wb_index = ft.TextField(label="只处理第N张（留空=全部）", width=160,
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                       value="", tooltip="只对选中图片里的第 N 张做 AI 编辑")
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
                    self.toast("仍未读到 Cookie，请粘贴 Cookie", False)
            threading.Thread(target=go, daemon=True).start()

        conn_card = card(ft.Column([
            h1("1 · 连接", 15),
            ft.Row([self.wb_conn,
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=recheck),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
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
                    ft.ElevatedButton("加水印", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("add_watermark")),
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
            self.wb_index, self.wb_prompt,
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
            try:
                from ..services import edited as _edited
                chosen, _skip_ed = _edited.filter_new(chosen)
            except Exception:
                _skip_ed = 0
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
        _sel = [p for p in self._wb_selected_paths() if Path(p).exists()]
        if not _sel:
            self.toast("请先勾选存在的图片", False); return
        info = video_probe()
        if not info["available"]:
            self.toast("未检测到 ffmpeg，请先安装", False); return
        try:
            outdir = Path(self.cfg.video_output_dir) if self.cfg.video_output_dir else (Path.home() / "Movies" / "AuroraList")
            outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"carousel-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
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
        return [pth for pth in self.picked if str(pth) in self.wb_sel_paths]

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
        """离线队列预览：不连接账号、不保存商品、不标记实际已编辑。"""
        if getattr(self, "_running", False):
            self._log("已有任务在运行，队列稍后再处理", "WARN")
            self.toast("已有任务在运行", False)
            return
        self._running = True
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
        self._stop_requested = False
        # This entry point is always an offline preview, regardless of save settings.
        by_id = {str(p.get("id")): p for p in (self.products or [])}
        by_id.update({str(k): v for k, v in getattr(self, "_queue_products", {}).items()})
        items = self.q.pending()
        if not items:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
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
                pub = Publisher(self.cfg)
                res = pub.run(prod, dry_run=True, manage_progress=False)
                status = "done" if res.ok else "failed"
                msg = "仅预览完成，未保存商品、未标记已编辑" if res.ok else res.error
            except Exception as ex:  # noqa: BLE001
                status, msg = "failed", str(ex)[:160]
            with lock:
                self.q.mark(item.product_id, status, msg)
                counts["ok" if status == "done" else "fail"] += 1
                self._refresh_queue()
                self._log(f"队列预览 {item.product_id}: {msg if status == 'done' else '失败 ' + msg}")

        self._log(f"队列预览开始：{len(items)} 个任务，并发 {workers}")
        import concurrent.futures as _cf
        try:
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, items))
        finally:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
        ok, fail = counts["ok"], counts["fail"]
        history.record("queue:preview", ok, fail)
        self._log(f"队列预览完成（未保存商品）：成功 {ok} / 失败 {fail}" + ("（已按请求停止）" if self._stop_requested else ""))
        self.toast(f"队列预览完成（未保存商品）：成功 {ok} / 失败 {fail}")

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
        self.wb_sel_paths = {str(p) for p in got}
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
        targets = self._wb_selected_paths()
        if not targets:
            self.wb_busy.visible = False
            self.toast("请先勾选要处理的图片", False)
            self._safe_update()
            return
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

        # 只处理第 N 张（1 基）时，过滤 targets
        _nth = ""
        try:
            _nth = str(getattr(getattr(self, "wb_index", None), "value", "") or "").strip()
        except Exception:
            _nth = ""
        if _nth and _nth.isdigit():
            _i = int(_nth) - 1
            if 0 <= _i < len(targets):
                targets = {_i: targets[_i]}
            else:
                self.toast(f"第 {_nth} 张超出范围（共 {len(targets)} 张）", False)
                self.wb_busy.visible = False
                self._safe_update()
                return
        results = runner.run(list(targets), job, done)
        ok = sum(1 for r in results if r.ok)
        try:
            history.record("workbench:" + kind, ok, len(results) - ok)
        except Exception:
            pass
        self.wb_status.value = f"处理完成：成功 {ok} / {len(results)}"
        self.wb_busy.visible = False
        self._safe_update()

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

    def _edited_count_text(self):
        try:
            from ..services import edited
            return muted(f"已编辑记录：{edited.stats().get('count', 0)} 个（这些产品不会再自动编辑）")
        except Exception:
            return muted("已编辑记录：-")

    def _clear_edited(self):
        from ..services import edited
        count = edited.count()
        if count == 0:
            self.toast("没有已编辑记录")
            return
        
        def do_clear(e):
            try:
                n = edited.clear()
                self.toast(f"已清空 {n} 条已编辑记录；这些产品下次会被重新处理")
                self._switch(8)
            except Exception as ex:
                self.toast(f"清空失败：{ex}", False)
            finally:
                self._close_dialog()
        
        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认清空已编辑记录"),
            content=ft.Text(f"将清空 {count} 条已编辑记录。\n\n这些产品将在下次队列处理时被重新编辑（会再次调用 AI 并消耗费用）。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog()),
                ft.FilledButton("确认清空", on_click=do_clear),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(self.dlg)
        self.dlg.open = True
        self.update()

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
        # 输入立即保存；切页会重建控件，延迟读取 self 上的字段会丢掉新输入。
        def _api_autosave(e=None):
            self._save_api_fields()
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
                from ..api import classify_api_error
                key = (self.cfg.api_key or "").strip()
                if not key:
                    self.api_status.value = "连接失败：未填写 API Key"
                    self.api_status.color = "#FF6B6B"
                    try:
                        self._safe_update()
                    except Exception:
                        pass
                    return
                try:
                    models = ApiClient(self.cfg.api_base, key, 20).models(force=True)
                    self.api_status.value = f"连接正常：{len(models)} 个模型"
                    self.api_status.color = ACCENT_2
                except Exception as ex:  # noqa: BLE001
                    kind = classify_api_error(ex)
                    hint = {"auth": " → Key 无效，请重新复制粘贴",
                            "quota": " → 余额不足，请充值",
                            "other": " → 网络或服务端问题，可稍后重试"}.get(kind, "")
                    self.api_status.value = f"连接失败：{str(ex)[:80]}{hint}"
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

        # ---------------- 图床 ----------------
        self.host_url = ft.TextField(
            label="图床地址", width=340,
            value=str(getattr(self.cfg, "image_host_url", "") or ""),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.host_key = ft.TextField(
            label="图床密钥", width=220, password=True, can_reveal_password=True,
            value=str(getattr(self.cfg, "image_host_key", "") or ""),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.host_status = ft.Text("", size=11, color=MUTED)
        self.host_keep = ft.TextField(
            label="保留天数（清理时删更早的）", width=210,
            value=str(getattr(self.cfg, "host_keep_days", 7)),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def clean_host(e=None):
            try:
                _days = max(0, int(float(self.host_keep.value or 7)))
            except Exception:
                _days = 7
            self.cfg.set("host_keep_days", _days)
            save_config(self.cfg)
            self.host_status.value = "清理中…"
            self.host_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                try:
                    from ..services.hosting import clean_old
                    r = clean_old()          # 天数由 services 读配置决定
                    self.host_status.value = r.get("message", "")
                    self.host_status.color = ACCENT_2 if r.get("ok") else "#FF6B6B"
                    self._log(f"图床清理：{r.get('message','')}", "INFO" if r.get("ok") else "WARN")
                except Exception as ex:  # noqa: BLE001
                    self.host_status.value = f"清理失败：{str(ex)[:110]}"
                    self.host_status.color = "#FF6B6B"
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        def save_host(e=None):
            self.cfg.set("image_host_url", str(self.host_url.value or "").strip())
            self.cfg.set("image_host_key", str(self.host_key.value or "").strip())
            save_config(self.cfg)
            self.host_status.value = "已保存"
            self.host_status.color = ACCENT_2
            try:
                self._safe_update()
            except Exception:
                pass

        def test_host(e=None):
            save_host()
            self.host_status.value = "测试中…"
            self.host_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                try:
                    import tempfile as _tf
                    from pathlib import Path as _P
                    from ..services.hosting import upload as _up
                    from PIL import Image, ImageDraw
                    d = _P(_tf.mkdtemp()) / "host_test.png"
                    im = Image.new("RGB", (1200, 1200), (250, 250, 250))
                    ImageDraw.Draw(im).rectangle([300, 300, 900, 900], fill=(60, 120, 220))
                    im.save(d)
                    url = _up(d)
                    self.host_status.value = f"上传成功：{url}"
                    self.host_status.color = ACCENT_2
                    self._log(f"图床上传成功：{url}", "INFO")
                except Exception as ex:  # noqa: BLE001
                    self.host_status.value = f"上传失败：{str(ex)[:110]}"
                    self.host_status.color = "#FF6B6B"
                    self._log(f"图床上传失败：{ex}", "WARN")
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        self.host_url.on_change = lambda e: save_host()
        self.host_key.on_change = lambda e: save_host()
        host_card = card(ft.Column([
            h1("图床", 15),
            muted("AI 出图先放这里，店小秘编辑器再按 URL 取图。"
                  "AI 返回的是约 1 天过期的临时地址，不能直接写进商品。"),
            ft.Row([self.host_url, self.host_key], spacing=10, wrap=True),
            ft.Row([self.host_keep, ft.ElevatedButton("清理旧图", icon=ft.Icons.DELETE_SWEEP,
                                                     on_click=lambda e: clean_host())],
                   spacing=8, wrap=True),
            ft.Row([ft.ElevatedButton("保存", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white",
                                      on_click=save_host),
                    ft.OutlinedButton("测试上传", icon=ft.Icons.CLOUD_UPLOAD, on_click=test_host)],
                   spacing=8, wrap=True),
            self.host_status,
            muted("支持 Cloudflare-ImgBed（POST /rest/upload）和老自建服务（POST /upload），自动识别。"),
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
                                      on_click=lambda e: threading.Thread(target=self._run_diag, daemon=True).start())],
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

        return [lic_card, api_card, host_card, model_card, inst_card, upd, diag_card, price_card]

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

        # 3) 店小秘
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
        if getattr(self, "_license_checking", False):
            return
        self._license_checking = True
        try:
            self._check_license_inner()
        finally:
            self._license_checking = False

    def _check_license_inner(self):
        try:
            from ..core.updater import CURRENT as _ver
        except Exception:
            _ver = ""
        try:
            from ..license import machine_code as _dbg_mc
            self._log(f"授权检测：机器码 {_dbg_mc()} / 旧ID {getattr(self.cfg, 'license_identity', '') or '-'}")
        except Exception:
            pass
        res = verify_license(self.cfg.license_server, version=_ver,
                             identity_override=str(getattr(self.cfg, "license_identity", "") or ""),
                             public_key_pem=str(getattr(self.cfg, "license_public_key", "") or ""))
        try:
            self._log(f"授权结果：{res.get('message')} / tried {res.get('tried')}")
        except Exception:
            pass
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
        if getattr(self, "_building_page", False) or getattr(self, "_page_dead", False):
            return
        lock = getattr(self, "_ui_lock", None)
        try:
            if lock is not None:
                with lock:
                    self.page.update()
            else:
                self.page.update()
        except Exception as ex:  # noqa: BLE001
            # 窗口/事件循环已关闭 → 界面已经死了，后续 UI 更新全部静默跳过，
            # 否则心跳线程会每 15 秒往 error.log 刷一条 "Event loop is closed"。
            if "Event loop is closed" in str(ex) or "has been closed" in str(ex):
                self._page_dead = True
                return
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
