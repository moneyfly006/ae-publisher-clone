"""现代风格 Flet 界面。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient, ApiError
from ..config import AppConfig, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, TextService

ACCENT = "#6C5CE7"
ACCENT_2 = "#00D2A8"
BG = "#0F1117"
CARD = "#171A23"
CARD_2 = "#1E2230"
TEXT = "#E6E8F0"
MUTED = "#8A90A6"


def _card(content, padding=20, expand=False, width=None):
    return ft.Container(
        content=content,
        bgcolor=CARD,
        border_radius=16,
        padding=padding,
        expand=expand,
        width=width,
        border=ft.border.all(1, CARD_2),
    )


def _title(text: str, size: int = 22):
    return ft.Text(text, size=size, weight=ft.FontWeight.BOLD, color=TEXT)


def _muted(text: str, size: int = 12):
    return ft.Text(text, size=size, color=MUTED)


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg: AppConfig = get_config()
        self.logs: list[str] = []
        self.file_picker = ft.FilePicker(on_result=self._on_files)
        self.picked: list[Path] = []

        self.page.title = "AuroraList · 极光上架"
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.overlay.append(self.file_picker)
        self.page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)

        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=18)
        self.nav_index = 0
        self._build_shell()
        self._switch(0)
        self.page.update()

    # ---------------- 基础设施 ----------------
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        self.logs = self.logs[-500:]
        if getattr(self, "log_view", None) is not None:
            self.log_view.controls = [ft.Text(l, size=12, color=MUTED, font_family="monospace") for l in self.logs]
            self.log_view.auto_scroll = True
            self.page.update()

    def toast(self, msg: str, ok: bool = True) -> None:
        self.page.snack_bar.content = ft.Text(msg, color=TEXT)
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self.page.update()

    def client(self) -> ApiClient:
        return ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)

    def run_bg(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ---------------- 布局 ----------------
    def _build_shell(self) -> None:
        self.nav_buttons = []
        items = [("概览", ft.Icons.DASHBOARD_OUTLINED), ("AI 文本", ft.Icons.TRANSLATE), ("图片处理", ft.Icons.IMAGE_OUTLINED), ("设置", ft.Icons.SETTINGS_OUTLINED)]
        col = ft.Column(spacing=6)
        for i, (label, icon) in enumerate(items):
            btn = ft.Container(
                content=ft.Row([ft.Icon(icon, size=18, color=MUTED), ft.Text(label, color=MUTED, size=14)], spacing=12),
                padding=ft.padding.symmetric(horizontal=14, vertical=11),
                border_radius=12,
                on_click=lambda e, idx=i: self._switch(idx),
                ink=True,
            )
            self.nav_buttons.append(btn)
            col.controls.append(btn)

        sidebar = ft.Container(
            width=220,
            bgcolor=CARD,
            padding=18,
            content=ft.Column([
                ft.Row([ft.Container(width=32, height=32, border_radius=10, bgcolor=ACCENT,
                                     content=ft.Text("A", size=16, weight=ft.FontWeight.BOLD, color="#0B0D12")),
                        ft.Column([ft.Text("AuroraList", weight=ft.FontWeight.BOLD, color=TEXT, size=15),
                                   _muted("极光上架 v0.1")], spacing=0)], spacing=10),
                ft.Container(height=18),
                col,
                ft.Container(expand=True),
                _card(ft.Column([_muted("授权状态"), ft.Text("检测中…", color=ACCENT_2, size=13, weight=ft.FontWeight.BOLD)],
                                spacing=4), padding=12),
            ]),
        )
        self.license_box = sidebar.content.controls[-1].content.controls[1]

        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=18)
        self.content_wrap = ft.Container(expand=True, padding=ft.padding.only(left=28, right=28, top=22, bottom=22),
                                         content=self.content)
        self.page.add(ft.Row([sidebar, self.content_wrap], expand=True, spacing=0))
        self.run_bg(self._check_license)

    def _switch(self, idx: int) -> None:
        self.nav_index = idx
        for i, b in enumerate(self.nav_buttons):
            active = i == idx
            b.bgcolor = CARD_2 if active else None
            b.content.controls[0].color = ACCENT if active else MUTED
            b.content.controls[1].color = TEXT if active else MUTED
        self.content.controls = [self._page_overview(), self._page_text(), self._page_image(), self._page_settings()][idx]
        self.page.update()

    # ---------------- 页面 ----------------
    def _page_overview(self):
        self.stat_api = ft.Text("未检测", color=MUTED, size=13)
        self.stat_license = ft.Text("检测中", color=MUTED, size=13)
        self.stat_models = ft.Text("—", color=MUTED, size=13)

        def card(label, ctrl, icon, color):
            return _card(ft.Column([ft.Row([ft.Icon(icon, size=18, color=color), _muted(label)], spacing=8),
                                    ctrl], spacing=10), padding=18, expand=True)

        return [
            _title("概览"),
            _muted("AI 能力已接入自建站点，所有调用按站点价格计费。"),
            ft.Row([
                card("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT),
                card("授权状态", self.stat_license, ft.Icons.VERIFIED_OUTLINED, ACCENT_2),
                card("可用模型", self.stat_models, ft.Icons.MODEL_TRAINING, "#FFB020"),
            ], spacing=14),
            _card(ft.Column([
                _title("快速开始", 16),
                _muted("1. 在「设置」里填入 API Key；2. 在「AI 文本」翻译标题；3. 在「图片处理」做白底/去水印。"),
                ft.Row([
                    ft.ElevatedButton("去设置", icon=ft.Icons.SETTINGS, bgcolor=ACCENT, color="white",
                                      on_click=lambda e: self._switch(3)),
                    ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING, on_click=lambda e: self.run_bg(self._test_connection)),
                ], spacing=12),
            ], spacing=12)),
        ]

    def _page_text(self):
        self.txt_in = ft.TextField(label="输入产品标题 / SKU 列表", multiline=True, min_lines=3, max_lines=6,
                                   bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.txt_out = ft.TextField(label="结果", multiline=True, min_lines=8, max_lines=16, read_only=True,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.text_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)
        mode = ft.Dropdown(
            options=[ft.dropdown.Option("标题翻译"), ft.dropdown.Option("详情描述"), ft.dropdown.Option("SKU 优化")],
            value="标题翻译", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
        )

        def run(e):
            text = (self.txt_in.value or "").strip()
            if not text:
                self.toast("请输入内容", False); return
            if not self.cfg.api_key:
                self.toast("请先在设置里填写 API Key", False); return
            self.text_busy.visible = True; self.page.update()
            self.run_bg(self._do_text, mode.value, text)

        return [
            _title("AI 文本"),
            _card(ft.Column([
                ft.Row([mode, ft.ElevatedButton("开始生成", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT, color="white", on_click=run),
                        self.text_busy], spacing=12),
                self.txt_in, self.txt_out,
            ], spacing=14)),
        ]

    def _page_image(self):
        self.img_status = ft.Text("未选择图片", color=MUTED, size=12)
        self.img_results = ft.Column(spacing=6)
        self.img_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)
        self.img_prompt = ft.TextField(label="编辑提示词（通用编辑用）", value="white background product photo",
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def pick(e):
            self.file_picker.pick_files(allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"])

        def run(kind):
            if not self.picked:
                self.toast("请先选择图片", False); return
            if not self.cfg.api_key:
                self.toast("请先在设置里填写 API Key", False); return
            self.img_busy.visible = True; self.page.update()
            self.run_bg(self._do_image, kind)

        return [
            _title("图片处理"),
            _card(ft.Column([
                ft.Row([ft.OutlinedButton("选择图片", icon=ft.Icons.UPLOAD_FILE, on_click=pick),
                        ft.ElevatedButton("白底图", icon=ft.Icons.WB_SUNNY_OUTLINED, bgcolor=ACCENT, color="white",
                                          on_click=lambda e: run("white")),
                        ft.ElevatedButton("去水印", icon=ft.Icons.AUTO_FIX_HIGH, bgcolor=ACCENT_2, color="#04231C",
                                          on_click=lambda e: run("watermark")),
                        self.img_busy], spacing=12),
                self.img_prompt,
                ft.Row([ft.ElevatedButton("通用编辑", icon=ft.Icons.EDIT, bgcolor=CARD_2, color=TEXT,
                                          on_click=lambda e: run("edit"))]),
                self.img_status,
                self.img_results,
            ], spacing=14)),
        ]

    def _page_settings(self):
        c = self.cfg
        self.f_base = ft.TextField(label="API 地址", value=c.api_base, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.f_key = ft.TextField(label="API Key", value=c.api_key, password=True, can_reveal_password=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.f_conc = ft.TextField(label="并发数", value=str(c.concurrency), width=120, bgcolor=CARD_2,
                                   border_color=CARD_2, color=TEXT)
        self.f_timeout = ft.TextField(label="超时(秒)", value=str(c.timeout), width=120, bgcolor=CARD_2,
                                      border_color=CARD_2, color=TEXT)
        self.f_title = ft.TextField(label="标题提示词", value=c.title_prompt, multiline=True, min_lines=2,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.f_detail = ft.TextField(label="详情提示词", value=c.detail_prompt, multiline=True, min_lines=2,
                                     bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.f_sku = ft.TextField(label="SKU 提示词", value=c.sku_prompt, multiline=True, min_lines=2,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def save(e):
            c.api_base = (self.f_base.value or "").strip()
            c.api_key = (self.f_key.value or "").strip()
            try:
                c.concurrency = max(1, int(self.f_conc.value or 3))
            except ValueError:
                c.concurrency = 3
            try:
                c.timeout = max(30, int(self.f_timeout.value or 180))
            except ValueError:
                c.timeout = 180
            c.title_prompt = self.f_title.value or c.title_prompt
            c.detail_prompt = self.f_detail.value or c.detail_prompt
            c.sku_prompt = self.f_sku.value or c.sku_prompt
            save_config(c)
            self.toast("已保存")

        return [
            _title("设置"),
            _card(ft.Column([self.f_base, self.f_key, ft.Row([self.f_conc, self.f_timeout], spacing=12),
                             ft.Row([ft.ElevatedButton("保存", icon=ft.Icons.SAVE, bgcolor=ACCENT, color="white", on_click=save),
                                     ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                                       on_click=lambda e: self.run_bg(self._test_connection))], spacing=12)],
                            spacing=14)),
            _card(ft.Column([_title("提示词", 16), self.f_title, self.f_detail, self.f_sku], spacing=14)),
        ]

    # ---------------- 行为 ----------------
    def _check_license(self) -> None:
        res = verify_license(self.cfg.license_server)
        self.license_box.value = res["message"]
        self.license_box.color = ACCENT_2 if res["ok"] else "#FF6B6B"
        self.page.update()

    def _test_connection(self) -> None:
        try:
            models = self.client().models()
            self.stat_api.value = f"正常 · {len(models)} 个模型"
            self.stat_api.color = ACCENT_2
            self.stat_models.value = str(len(models))
            self.stat_models.color = "#FFB020"
            self.toast(f"连接成功，可用模型 {len(models)} 个")
        except Exception as e:  # noqa: BLE001
            self.stat_api.value = "连接失败"
            self.stat_api.color = "#FF6B6B"
            self.toast(str(e), False)
        self.page.update()

    def _do_text(self, mode: str, text: str) -> None:
        svc = TextService(self.client(), self.cfg)
        try:
            if mode == "标题翻译":
                out = svc.translate_title(text)
            elif mode == "详情描述":
                out = svc.write_detail(text)
            else:
                out = "\n".join(svc.optimize_skus(text.splitlines()))
            self.txt_out.value = out
            self.toast("生成完成")
        except ApiError as e:
            self.toast(str(e), False)
        finally:
            self.text_busy.visible = False
            self.page.update()

    def _do_image(self, kind: str) -> None:
        svc = ImageService(self.client())
        self.img_results.controls = []
        runner = BatchRunner(self.cfg.concurrency)
        items = list(self.picked)

        def job(idx, path):
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            return svc.edit(Path(path), self.img_prompt.value or "clean product photo")

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else "#FF6B6B"
            text = res.value if res.ok else res.error
            self.img_results.controls.append(ft.Row([ft.Icon(icon, size=16, color=color),
                                                     ft.Text(text, size=12, color=MUTED, selectable=True)], spacing=8))
            self.page.update()

        results = runner.run(items, job, done)
        ok = sum(1 for r in results if r.ok)
        self.img_status.value = f"完成：成功 {ok} / {len(results)}"
        self.img_status.color = ACCENT_2 if ok == len(results) else "#FFB020"
        self.img_busy.visible = False
        self.page.update()

    def _on_files(self, e: ft.FilePickerResultEvent) -> None:
        if e.files:
            self.picked = [Path(f.path) for f in e.files]
            self.img_status.value = f"已选择 {len(self.picked)} 张图"
            self.page.update()


def run() -> None:
    ft.app(target=lambda page: App(page), assets_dir="assets")
