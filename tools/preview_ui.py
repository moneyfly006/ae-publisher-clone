"""本地隔离预览；仅演示真实 Flet UI，外部服务禁用。

运行前安装兼容的 Web 预览依赖（桌面打包不依赖此环境）：
.venv/bin/python -m pip install --target build/ui-preview-deps --no-deps fastapi==0.115.6 starlette==0.41.3
PYTHONPATH=build/ui-preview-deps .venv/bin/python tools/preview_ui.py
"""
import flet as ft
from ui_sandbox import ROOT, build_app, sandbox


def main():
    with sandbox(threads=True):
        ft.app(target=build_app, view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=8891,
               assets_dir=str(ROOT / "assets"), web_renderer=ft.WebRenderer.CANVAS_KIT)


if __name__ == "__main__":
    main()
