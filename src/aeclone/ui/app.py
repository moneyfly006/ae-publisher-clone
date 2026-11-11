"""AuroraList · 现代化界面（schema 驱动，覆盖老软件全部设置项）。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient
from ..config import AppConfig, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService

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
        self._switch(start % 9)
        page.update()

    # ---------- 壳 ----------
    def _build_shell(self):
        self.nav_buttons = []
        items = [("概览", ft.Icons.DASHBOARD_OUTLINED), ("店铺与模板", ft.Icons.STORE_OUTLINED),
                 ("高级设置", ft.Icons.TUNE), ("AI 智能填写", ft.Icons.AUTO_FIX_HIGH),
                 ("内容审查", ft.Icons.FACT_CHECK_OUTLINED), ("图片处理", ft.Icons.IMAGE_OUTLINED),
                 ("价格设置", ft.Icons.SELL_OUTLINED), ("变种设置", ft.Icons.ACCOUNT_TREE_OUTLINED),
                 ("设置", ft.Icons.SETTINGS_OUTLINED)]
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
                 self._p_image, self._p_price, self._p_variant, self._p_settings]
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

        return [h1("概览"), muted("AI 能力接入自建站点，全部调用按站点价格计费"),
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
        return self._render("店铺与模板", schema_store())

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
        return self._render("价格设置", schema_price())

    def _p_variant(self):
        self._current = 7
        return self._render("变种设置", schema_variant())

    def _p_settings(self):
        self._current = 8
        return self._render("设置", schema_settings(self.cfg))

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
