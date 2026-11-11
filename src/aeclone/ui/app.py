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
from ..integrations.cdp import launch_edge, cookies_via_cdp, BrowserError
from ..integrations.bitbrowser import BitBrowser
from ..integrations.zhanfu import ZhanfuBrowser
from ..services.publisher import Publisher, demo_product
from ..integrations.dianxiaomi import DianxiaomiClient, ENDPOINTS
from ..services.video import build_slideshow, probe as video_probe
from ..core import updater
from ..core.instances import list_instances, create_instance, launch_instance
from ..services import history

ACCENT, ACCENT_2 = "#7C5CFF", "#00D2A8"
BG, CARD, CARD_2 = "#0B0D12", "#141824", "#1B2030"
TEXT, MUTED = "#E8EAF2", "#8B92A8"

BROWSERS = ["本机Edge", "比特浏览器", "站斧浏览器"]
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
        ("店铺设置", [Field("browser_type", "浏览器窗口", "select", BROWSERS),
                      Field("store_name", "店铺名称", hint="从店小秘拉取"),
                      Field("store_id", "店铺 ID"),
                      Field("use_template_category", "使用模板分类", "bool")]),
        ("产品模板", [Field("product_template_name", "模板产品名称"),
                      Field("product_template_id", "模板产品 ID / 链接"),
                      Field("product_id_title", "模板标题"),
                      Field("product_id_image", "模板主图 URL")]),
    ]


def schema_advanced():
    return [
        ("基础", [Field("edit_sum", "编辑产品数量"), Field("watermark", "水印图片路径"),
                  Field("declared_value", "货值默认值"), Field("title_prefix", "标题前缀"),
                  Field("title_suffix", "标题后缀")]),
        ("库存与 SKU", [Field("modify_stock_999", "库存改为 999", "bool"),
                        Field("stock_quantity", "固定库存数量"),
                        Field("max_sku_count", "SKU 数量上限"),
                        Field("custom_sku_prefix", "SKU 自定义前缀"),
                        Field("sku_split_char", "SKU 分隔符"),
                        Field("auto_generate_sku_code", "自动生成 SKU 编码", "bool"),
                        Field("use_number_replace_sku", "数字替换 SKU", "bool"),
                        Field("only_sku_prefix_checkbox", "仅 SKU 前缀", "bool"),
                        Field("copy_sku_to_detail", "SKU 复制到详情", "bool")]),
        ("其他", [Field("move_detail_text", "移动详情文字", "bool"),
                  Field("original_box", "原箱发货", "bool"),
                  Field("keep_temp_images", "保留临时图片", "bool"),
                  Field("check_banned_word", "违禁词检查", "bool")]),
    ]


def schema_ai():
    return [
        ("标题生成", [Field("enable_ai_title", "启用 AI 标题", "bool"),
                      Field("title_prompt", "标题提示词", "multiline")]),
        ("详情描述", [Field("enable_ai_detail", "启用 AI 详情", "bool"),
                      Field("detail_prompt", "详情提示词", "multiline")]),
        ("SKU 优化", [Field("enable_ai_sku", "启用 AI 优化 SKU", "bool"),
                      Field("sku_prompt", "SKU 提示词", "multiline")]),
        ("读图估算", [Field("enable_ai_weight", "启用读图估算", "bool"),
                      Field("weight_prompt", "重量规格提示词", "multiline"),
                      Field("weight_keep_existing", "保留已有重量", "bool"),
                      Field("pack_weight", "包装重量(kg)")]),
    ]


def schema_review():
    return [
        ("文本审查", [Field("review_text_enable", "启用文本审查", "bool"),
                      Field("review_text_prompt", "文本审查提示词", "multiline")]),
        ("图片审查", [Field("review_detail_img_enable", "详情图审查", "bool"),
                      Field("review_detail_img_prompt", "详情图审查提示词", "multiline"),
                      Field("review_main_img_enable", "主图审查", "bool"),
                      Field("review_main_img_prompt", "主图审查提示词", "multiline")]),
    ]


def schema_image():
    return [
        ("简单编辑", [Field("white_bg_enable", "白底图", "bool"),
                      Field("white_bg_range", "白底范围（如 1-2）"),
                      Field("watermark_enable", "去水印", "bool"),
                      Field("watermark_range", "去水印范围"),
                      Field("translate_enable", "图片翻译", "bool"),
                      Field("translate_sku_pic", "翻译 SKU 图", "bool")]),
        ("通用编辑 / 背景生成", [Field("edit_enable", "启用通用编辑", "bool"),
                                 Field("edit_prompt", "通用编辑提示词", "multiline"),
                                 Field("background_gen_main_enable", "主图背景生成", "bool"),
                                 Field("background_gen_detail_enable", "详情图背景生成", "bool"),
                                 Field("background_gen_model", "背景生成模型"),
                                 Field("single_edit_model", "编辑模型")]),
    ]


def schema_price():
    return [
        ("价格模式", [Field("price_mode", "模式（multiplier / free_shipping）"),
                      Field("price_multiplier", "价格倍率"), Field("price_fixed_add", "固定加价"),
                      Field("regional_pricing", "启用区域调价", "bool")]),
        ("成本与汇率", [Field("currency", "目标币种", "select", CURRENCIES),
                        Field("exchange_rate", "汇率"), Field("goods_cost", "商品成本"),
                        Field("goods_cost_unit", "成本单位"),
                        Field("platform_commission", "平台佣金%"),
                        Field("sales_profit", "期望利润%"),
                        Field("single_item_discount_rate", "单品折扣%"),
                        Field("affiliate_commission", "联盟佣金%"),
                        Field("default_express_price", "默认运费")]),
        ("托管与模板", [Field("tuoguan_price_multiplier", "托管倍率"),
                        Field("tuoguan_price_fixed_addition", "托管固定加价"),
                        Field("adjust_price_template_id", "调价模板 ID"),
                        Field("adjust_price_template_name", "调价模板名称")]),
    ]


def schema_variant():
    return [
        ("映射方式", [Field("variant_mode", "模式（auto / manual）"),
                      Field("keep_custom_attributes", "保留自定义属性", "bool"),
                      Field("variant_mappings", "属性映射表（JSON）", "multiline",
                            hint='示例：[{"from":"Model Number","to":"型号"}]')]),
    ]


def schema_settings(cfg):
    return [
        ("API 与并发", [Field("api_base", "API 地址"), Field("api_key", "API Key"),
                        Field("concurrency", "并发数"), Field("timeout", "超时(秒)")]),
        ("授权", [Field("verify_mode", "验证模式（local / shop）"),
                  Field("license_server", "授权服务器")]),
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
        self._switch(start % 11)
        page.update()

    # ---------- 壳 ----------
    def _build_shell(self):
        self.nav_buttons = []
        items = [("概览", ft.Icons.DASHBOARD_OUTLINED), ("店铺与模板", ft.Icons.STORE_OUTLINED),
                 ("高级设置", ft.Icons.TUNE), ("AI 智能填写", ft.Icons.AUTO_FIX_HIGH),
                 ("内容审查", ft.Icons.FACT_CHECK_OUTLINED), ("图片处理", ft.Icons.IMAGE_OUTLINED),
                 ("价格设置", ft.Icons.SELL_OUTLINED), ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED),
                 ("设置", ft.Icons.SETTINGS_OUTLINED),
                 ("图片工作台", ft.Icons.GRID_VIEW),
                 ("任务与日志", ft.Icons.RECEIPT_LONG_OUTLINED)]
        col = ft.Column(spacing=4)
        for i, (label, icon) in enumerate(items):
            b = ft.Container(
                content=ft.Row([ft.Icon(icon, size=17, color=MUTED), ft.Text(label, color=MUTED, size=13)], spacing=10),
                padding=ft.padding.symmetric(horizontal=12, vertical=9), border_radius=10,
                on_click=lambda e, idx=i: self._switch(idx), ink=True)
            self.nav_buttons.append(b)
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

    def _switch(self, idx):
        for i, b in enumerate(self.nav_buttons):
            on = i == idx
            b.bgcolor = CARD_2 if on else None
            b.content.controls[0].color = ACCENT if on else MUTED
            b.content.controls[1].color = TEXT if on else MUTED
        self.field_refs = []
        pages = [self._p_overview, self._p_store, self._p_advanced, self._p_ai, self._p_review,
                 self._p_image, self._p_price, self._p_variant, self._p_settings, self._p_workbench, self._p_logs]
        self.content.controls = pages[idx]()
        self.page.update()

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
                                        ft.OutlinedButton("去设置", icon=ft.Icons.SETTINGS,
                                                          on_click=lambda e: self._switch(8))], spacing=10)], spacing=10))]

    def _p_store(self):
        self._current = 1
        self.store_status = ft.Text("未连接浏览器", color=MUTED, size=12)
        self.ws_endpoint = ""
        self.products: list[dict] = []

        def connect(e):
            self.store_status.value = "正在启动浏览器…"; self.page.update()
            threading.Thread(target=self._connect_browser, daemon=True).start()

        def login(e):
            threading.Thread(target=self._check_dxm_login, daemon=True).start()

        def fetch(e):
            threading.Thread(target=self._fetch_products, daemon=True).start()

        def picker(e):
            self._open_template_dialog()

        bar = card(ft.Column([
            h1("对接操作", 15),
            ft.Row([ft.ElevatedButton("① 启动浏览器", icon=ft.Icons.OPEN_IN_BROWSER, bgcolor=ACCENT, color="white", on_click=connect),
                    ft.ElevatedButton("② 检测店小秘登录", icon=ft.Icons.LOGIN, bgcolor=CARD_2, color=TEXT, on_click=login),
                    ft.ElevatedButton("③ 拉取产品", icon=ft.Icons.CLOUD_DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=fetch),
                    ft.ElevatedButton("④ 选择模板", icon=ft.Icons.CHECKLIST, bgcolor=ACCENT_2, color="#04231C", on_click=picker),
                    ft.ElevatedButton("⑤ 试跑发布(dry-run)", icon=ft.Icons.PLAY_CIRCLE_OUTLINE, bgcolor=CARD_2, color=TEXT, on_click=lambda e: threading.Thread(target=self._dry_run, daemon=True).start())], spacing=10),
            self.store_status], spacing=10))
        view = self._render("店铺与模板", schema_store())
        return [view[0], bar] + view[1:]

    # ---------- 对接动作 ----------
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
            else:
                info = launch_edge()
                self.ws_endpoint = info["ws"]
                self.store_status.value = f"Edge 已启动 · 端口 {info['port']} · {info['browser']}"
            self.store_status.color = ACCENT_2
        except BrowserError as e:
            self.store_status.value = f"启动失败：{e}"; self.store_status.color = "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"启动异常：{e}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _cookies(self):
        if not self.ws_endpoint:
            raise RuntimeError("请先启动浏览器")
        return cookies_via_cdp(self.ws_endpoint, domains=["dianxiaomi"])

    def _check_dxm_login(self):
        try:
            ck = self._cookies()
            if not ck:
                self.store_status.value = "未读取到店小秘 Cookie，请先在浏览器登录店小秘"
                self.store_status.color = "#FFB020"
            else:
                client = DianxiaomiClient(ck)
                ok = client.check_login()
                self.store_status.value = ("店小秘登录有效" if ok else "登录已失效，请在浏览器重新登录")
                self.store_status.color = ACCENT_2 if ok else "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"检测失败：{e}"; self.store_status.color = "#FF6B6B"
        self.page.update()

    def _fetch_products(self):
        try:
            client = DianxiaomiClient(self._cookies())
            data = client.product_list(page=1, size=50)
            items = (data.get("data") or {}).get("list") or data.get("data") or []
            self.products = items if isinstance(items, list) else []
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
            res = pub.run(prod, dry_run=True)
            for line in res.log().splitlines():
                self._log(line)
            self.store_status.value = ("试跑完成" if res.ok else f"试跑失败：{res.error}")
            self.store_status.color = ACCENT_2 if res.ok else "#FF6B6B"
        except Exception as e:
            self.store_status.value = f"试跑异常：{e}"; self.store_status.color = "#FF6B6B"
            self._log(f"试跑异常：{e}")
        self.page.update()

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
        return self._render("高级设置", schema_advanced())

    def _p_ai(self):
        self._current = 3
        return self._render("AI 智能填写", schema_ai())

    def _p_review(self):
        self._current = 4
        return self._render("内容审查", schema_review())

    def _p_image(self):
        self._current = 5
        view = self._render("图片处理", schema_image())
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

        return [h1("图片工作台"), muted("批量白底 / 去水印 / 编辑（走你的 API 站点计费）"),
                card(ft.Column([self.wb_urls,
                                ft.Row([ft.ElevatedButton("加载图片", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=load),
                                        ft.OutlinedButton("从已拉取产品取图", icon=ft.Icons.INVENTORY_2, on_click=lambda e: self._images_from_products()),
                                        ft.ElevatedButton("白底", bgcolor=ACCENT, color="white", on_click=lambda e: run("white")),
                                        ft.ElevatedButton("去水印", bgcolor=ACCENT_2, color="#04231C", on_click=lambda e: run("watermark")),
                                        ft.ElevatedButton("编辑", bgcolor=CARD_2, color=TEXT, on_click=lambda e: run("edit")),
                                        ft.OutlinedButton("暂停", on_click=lambda e: self._wb_ctl("pause")),
                                        ft.OutlinedButton("继续", on_click=lambda e: self._wb_ctl("resume")),
                                        ft.OutlinedButton("取消", on_click=lambda e: self._wb_ctl("cancel")),
                                        self.wb_busy], spacing=10),
                                self.wb_status], spacing=10)),
                card(self.wb_results)]

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
        self._current = 9
        self.log_view = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO, height=360)
        self._refresh_logs()

        def build_video(e):
            if not self.picked:
                self.toast("请先在「图片处理」选择图片", False); return
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
            out.write_text("\n".join(self.logs), encoding="utf-8")
            self.toast(f"已导出：{out}")

        bar = card(ft.Row([
            ft.ElevatedButton("生成主图视频", icon=ft.Icons.MOVIE, bgcolor=ACCENT, color="white", on_click=build_video),
            ft.ElevatedButton("检查更新", icon=ft.Icons.SYSTEM_UPDATE, bgcolor=CARD_2, color=TEXT, on_click=check_update),
            ft.ElevatedButton("导出日志", icon=ft.Icons.DOWNLOAD, bgcolor=CARD_2, color=TEXT, on_click=export),
            ft.ElevatedButton("清空", icon=ft.Icons.DELETE_OUTLINE, bgcolor=CARD_2, color=TEXT,
                              on_click=lambda e: (self.logs.clear(), self._refresh_logs())),
        ], spacing=10))
        return [h1("任务与日志"), bar, card(self.log_view)]

    def _log(self, msg):
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        self.logs = self.logs[-500:]
        if getattr(self, "log_view", None) is not None:
            self._refresh_logs()

    def _refresh_logs(self):
        self.log_view.controls = [ft.Text(l, size=11, color=MUTED, font_family="monospace") for l in self.logs] or \
                                 [ft.Text("暂无日志", size=12, color=MUTED)]
        try:
            self.page.update()
        except Exception:
            pass

    def _p_settings(self):
        self._current = 8
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

        dbg = card(ft.Column([h1("接口调试 / 端点校正", 15),
                              muted("登录店小秘后，用这里试真实接口并把正确路径保存下来"),
                              ft.Row([self.dbg_key, self.dbg_method], spacing=10),
                              self.dbg_path,
                              self.dbg_payload,
                              ft.Row([ft.ElevatedButton("发送", icon=ft.Icons.SEND, bgcolor=ACCENT, color="white", on_click=send),
                                      ft.ElevatedButton("保存端点", icon=ft.Icons.SAVE, on_click=save_override)], spacing=10),
                              self.dbg_out], spacing=10))
        return [view[0], upd, inst_card, dbg] + view[1:]

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
            n = len(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout).models())
            self.stat_api.value = f"正常 · {n} 个模型"; self.stat_api.color = ACCENT_2
            self.toast(f"连接成功，{n} 个模型")
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

    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self.page.update()


def run():
    ft.app(target=lambda page: App(page), assets_dir="assets")
