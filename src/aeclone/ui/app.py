"""AuroraList · 现代化 Flet 界面。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient, ApiError
from ..config import AppConfig, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService

ACCENT = "#7C5CFF"
ACCENT_2 = "#00D2A8"
BG = "#0B0D12"
CARD = "#141824"
CARD_2 = "#1B2030"
TEXT = "#E8EAF2"
MUTED = "#8B92A8"

NAV = [
    ("概览", ft.Icons.DASHBOARD_OUTLINED),
    ("AI 智能填写", ft.Icons.AUTO_FIX_HIGH),
    ("内容审查", ft.Icons.FACT_CHECK_OUTLINED),
    ("图片处理", ft.Icons.IMAGE_OUTLINED),
    ("价格与变种", ft.Icons.ATTACH_MONEY),
    ("设置", ft.Icons.SETTINGS_OUTLINED),
]


def card(content, padding=20, expand=False, width=None):
    return ft.Container(content=content, bgcolor=CARD, border_radius=16, padding=padding,
                        expand=expand, width=width, border=ft.border.all(1, CARD_2))


def h1(text):
    return ft.Text(text, size=22, weight=ft.FontWeight.BOLD, color=TEXT)


def muted(text, size=12):
    return ft.Text(text, size=size, color=MUTED)


def field(label, value, multiline=False, password=False):
    return ft.TextField(label=label, value=value, multiline=multiline,
                        min_lines=2 if multiline else 1, max_lines=6 if multiline else 1,
                        password=password, can_reveal_password=password,
                        bgcolor=CARD_2, border_color=CARD_2, color=TEXT)


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg: AppConfig = get_config()
        self.logs: list[str] = []
        self.file_picker = ft.FilePicker(on_result=self._on_files)
        self.picked: list[Path] = []
        self.save_handlers: dict[str, callable] = {}

        page.title = "AuroraList · 极光上架"
        page.bgcolor = BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        page.overlay.append(self.file_picker)
        page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)

        self._build_shell()
        import os as _os
        try:
            start = int(_os.environ.get('AURORA_PAGE', '0'))
        except ValueError:
            start = 0
        self._switch(start % len(NAV))
        page.update()

    # ---------- 基础 ----------
    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self.page.update()

    def client(self):
        return ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)

    def run_bg(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def persist(self):
        for fn in self.save_handlers.values():
            fn()
        save_config(self.cfg)

    # ---------- 壳 ----------
    def _build_shell(self):
        self.nav_buttons = []
        col = ft.Column(spacing=6)
        for i, (label, icon) in enumerate(NAV):
            btn = ft.Container(
                content=ft.Row([ft.Icon(icon, size=18, color=MUTED), ft.Text(label, color=MUTED, size=14)], spacing=12),
                padding=ft.padding.symmetric(horizontal=14, vertical=11), border_radius=12,
                on_click=lambda e, idx=i: self._switch(idx), ink=True)
            self.nav_buttons.append(btn)
            col.controls.append(btn)

        self.license_text = ft.Text("检测中…", color=MUTED, size=13, weight=ft.FontWeight.BOLD)
        sidebar = ft.Container(width=232, bgcolor=CARD, padding=18, content=ft.Column([
            ft.Row([ft.Container(width=34, height=34, border_radius=11,
                                 bgcolor=ACCENT, content=ft.Text("A", size=16, weight=ft.FontWeight.BOLD, color="#0B0D12")),
                    ft.Column([ft.Text("AuroraList", weight=ft.FontWeight.BOLD, color=TEXT, size=15),
                               muted("极光上架 v0.1")], spacing=0)], spacing=10),
            ft.Container(height=18), col, ft.Container(expand=True),
            card(ft.Column([muted("授权状态"), self.license_text], spacing=4), padding=12),
        ]))
        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=18)
        self.page.add(ft.Row([sidebar, ft.Container(expand=True, content=self.content,
                                                    padding=ft.padding.only(left=28, right=28, top=22, bottom=22))],
                             expand=True, spacing=0))
        self.run_bg(self._check_license)

    def _switch(self, idx):
        for i, b in enumerate(self.nav_buttons):
            on = i == idx
            b.bgcolor = CARD_2 if on else None
            b.content.controls[0].color = ACCENT if on else MUTED
            b.content.controls[1].color = TEXT if on else MUTED
        pages = [self._p_overview, self._p_ai, self._p_review, self._p_image, self._p_price, self._p_settings]
        self.content.controls = pages[idx]()
        self.page.update()

    def _save_btn(self):
        def go(e):
            for fn in self.save_handlers.values():
                fn()
            save_config(self.cfg)
            self.toast("已保存")
        return ft.ElevatedButton("保存配置", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white", on_click=go)

    # ---------- 页面 ----------
    def _p_overview(self):
        self.stat_api = ft.Text("未检测", color=MUTED, size=13)

        def stat(label, ctrl, icon, color):
            return card(ft.Column([ft.Row([ft.Icon(icon, size=18, color=color), muted(label)], spacing=8), ctrl],
                                  spacing=10), padding=18, expand=True)

        return [
            h1("概览"), muted("AI 能力接入自建站点，所有调用按站点价格计费"),
            ft.Row([stat("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT),
                    stat("授权状态", ft.Text("见左下角", color=MUTED, size=13), ft.Icons.VERIFIED_OUTLINED, ACCENT_2),
                    stat("图片服务", ft.Text("~15 秒/张", color=ACCENT_2, size=13), ft.Icons.BOLT_OUTLINED, "#FFB020")],
                   spacing=14),
            card(ft.Column([h1("快速开始"), muted("1. 设置里填 Key  2. AI 智能填写配提示词  3. 图片处理跑白底/去水印"),
                            ft.Row([ft.ElevatedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                                      on_click=lambda e: self.run_bg(self._test)),
                                    ft.OutlinedButton("去设置", icon=ft.Icons.SETTINGS,
                                                      on_click=lambda e: self._switch(5))], spacing=12)], spacing=12)),
        ]

    def _p_ai(self):
        c = self.cfg
        self.sw_title = ft.Switch(label="启用 AI 标题", value=c.enable_ai_title, active_color=ACCENT)
        self.f_title = field("标题提示词", c.title_prompt, True)
        self.sw_detail = ft.Switch(label="启用 AI 详情", value=c.enable_ai_detail, active_color=ACCENT)
        self.f_detail = field("详情提示词", c.detail_prompt, True)
        self.sw_sku = ft.Switch(label="启用 AI 优化 SKU", value=c.enable_ai_sku, active_color=ACCENT)
        self.f_sku = field("SKU 提示词", c.sku_prompt, True)
        self.sw_weight = ft.Switch(label="启用读图估算重量规格", value=c.enable_ai_weight, active_color=ACCENT)
        self.f_weight = field("重量规格提示词", c.weight_prompt, True)

        def save():
            c.enable_ai_title, c.title_prompt = self.sw_title.value, self.f_title.value
            c.enable_ai_detail, c.detail_prompt = self.sw_detail.value, self.f_detail.value
            c.enable_ai_sku, c.sku_prompt = self.sw_sku.value, self.f_sku.value
            c.enable_ai_weight, c.weight_prompt = self.sw_weight.value, self.f_weight.value

        self.save_handlers["ai"] = save
        return [h1("AI 智能填写"), muted("对应老软件的 4 组提示词，全部可调"),
                card(ft.Column([self.sw_title, self.f_title, self.sw_detail, self.f_detail], spacing=12)),
                card(ft.Column([self.sw_sku, self.f_sku, self.sw_weight, self.f_weight], spacing=12)),
                self._save_btn()]

    def _p_review(self):
        c = self.cfg
        self.sw_rt = ft.Switch(label="详情文本审查", value=c.review_text_enable, active_color=ACCENT)
        self.f_rt = field("文本审查提示词", c.review_text_prompt, True)
        self.sw_rd = ft.Switch(label="详情图审查", value=c.review_detail_img_enable, active_color=ACCENT)
        self.f_rd = field("详情图审查提示词", c.review_detail_img_prompt, True)
        self.sw_rm = ft.Switch(label="主图审查", value=c.review_main_img_enable, active_color=ACCENT)
        self.f_rm = field("主图审查提示词", c.review_main_img_prompt, True)

        def save():
            c.review_text_enable, c.review_text_prompt = self.sw_rt.value, self.f_rt.value
            c.review_detail_img_enable, c.review_detail_img_prompt = self.sw_rd.value, self.f_rd.value
            c.review_main_img_enable, c.review_main_img_prompt = self.sw_rm.value, self.f_rm.value

        self.save_handlers["review"] = save
        return [h1("内容审查"), muted("过滤厂家信息、联系方式、促销、二维码等非商品内容"),
                card(ft.Column([self.sw_rt, self.f_rt, self.sw_rd, self.f_rd, self.sw_rm, self.f_rm], spacing=12)),
                self._save_btn()]

    def _p_image(self):
        c = self.cfg
        self.img_status = ft.Text("未选择图片", color=MUTED, size=12)
        self.img_results = ft.Column(spacing=6)
        self.img_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)
        self.f_edit = field("通用编辑提示词", c.edit_prompt, True)
        self.sw_white = ft.Switch(label="白底图", value=c.white_bg_enable, active_color=ACCENT_2)
        self.sw_wm = ft.Switch(label="去水印", value=c.watermark_enable, active_color=ACCENT_2)

        def pick(e):
            self.file_picker.pick_files(allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"])

        def run(kind):
            if not self.picked:
                self.toast("请先选择图片", False); return
            if not self.cfg.api_key:
                self.toast("请先在设置里填写 API Key", False); return
            self.img_busy.visible = True; self.page.update()
            self.run_bg(self._do_image, kind)

        return [h1("图片处理"),
                card(ft.Column([
                    ft.Row([ft.OutlinedButton("选择图片", icon=ft.Icons.UPLOAD_FILE, on_click=pick),
                            ft.ElevatedButton("白底图", icon=ft.Icons.WB_SUNNY_OUTLINED, bgcolor=ACCENT, color="white",
                                              on_click=lambda e: run("white")),
                            ft.ElevatedButton("去水印", icon=ft.Icons.AUTO_FIX_HIGH, bgcolor=ACCENT_2, color="#04231C",
                                              on_click=lambda e: run("watermark")),
                            self.img_busy], spacing=12),
                    self.f_edit,
                    ft.Row([ft.ElevatedButton("通用编辑", icon=ft.Icons.EDIT, bgcolor=CARD_2, color=TEXT,
                                              on_click=lambda e: run("edit"))]),
                    self.img_status, self.img_results], spacing=14))]

    def _p_price(self):
        c = self.cfg
        self.rg_mode = ft.RadioGroup(content=ft.Column([
            ft.Radio(value="multiplier", label="倍率价格"), ft.Radio(value="free_shipping", label="包邮价格")]),
            value="multiplier")
        self.f_mult = field("价格倍率", "1.2")
        self.f_add = field("最终价格固定增加", "0")
        self.rg_variant = ft.RadioGroup(content=ft.Column([
            ft.Radio(value="auto", label="自动匹配"), ft.Radio(value="manual", label="手动映射")]), value="auto")

        return [h1("价格与变种"), muted("价格模式、倍率、区域调价与 SKU 属性映射"),
                card(ft.Column([muted("价格模式"), self.rg_mode,
                                ft.Row([self.f_mult, self.f_add], spacing=12)], spacing=12)),
                card(ft.Column([muted("变种映射模式"), self.rg_variant,
                                muted("提示：手动映射需在「变种」页维护属性对照表（P1 实现）")], spacing=10)),
                muted("价格计算与区域调价将在 P1 接入海豚接口后启用")]

    def _p_settings(self):
        c = self.cfg
        self.f_base = field("API 地址", c.api_base)
        self.f_key = field("API Key", c.api_key, password=True)
        self.f_conc = field("并发数", str(c.concurrency))
        self.f_timeout = field("超时(秒)", str(c.timeout))

        def save():
            c.api_base = self.f_base.value.strip()
            c.api_key = self.f_key.value.strip()
            try: c.concurrency = max(1, int(self.f_conc.value))
            except Exception: c.concurrency = 3
            try: c.timeout = max(30, int(self.f_timeout.value))
            except Exception: c.timeout = 180

        self.save_handlers["settings"] = save
        return [h1("设置"),
                card(ft.Column([self.f_base, self.f_key, ft.Row([self.f_conc, self.f_timeout], spacing=12),
                                ft.Row([self._save_btn(), ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                                                            on_click=lambda e: self.run_bg(self._test))],
                                       spacing=12)], spacing=14))]

    # ---------- 行为 ----------
    def _check_license(self):
        res = verify_license(self.cfg.license_server)
        self.license_text.value = res["message"]
        self.license_text.color = ACCENT_2 if res["ok"] else "#FF6B6B"
        self.page.update()

    def _test(self):
        try:
            n = len(self.client().models())
            self.stat_api.value = f"正常 · {n} 个模型"
            self.stat_api.color = ACCENT_2
            self.toast(f"连接成功，{n} 个模型")
        except Exception as e:
            self.stat_api.value = "连接失败"; self.stat_api.color = "#FF6B6B"
            self.toast(str(e), False)
        self.page.update()

    def _do_image(self, kind):
        svc = ImageService(self.client())
        self.img_results.controls = []
        runner = BatchRunner(self.cfg.concurrency)

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), self.f_edit.value or "clean product photo")

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            self.img_results.controls.append(ft.Row([ft.Icon(icon, size=16, color=color),
                                                     ft.Text(res.value if res.ok else res.error, size=12,
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


def run():
    ft.app(target=lambda page: App(page), assets_dir="assets")
