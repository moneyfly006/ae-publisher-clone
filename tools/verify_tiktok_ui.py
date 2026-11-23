"""TikTok 14 号「TikTok 刊登」页 · 沙箱 UI 审计 + 按钮遍历 + 截图。

做什么
------
1. **就绪检测**：``platforms.TIKTOK.pages`` 含 index 14，且 ``App._p_tiktok_listing``
   已实现；就绪后用 ``tools/ui_sandbox.py`` 在隔离配置里切到 TikTok 平台、打开 14
   号页，断言落点 ``_page_idx == 14``。
2. **契约 §4.2 八张卡片**：连接/店铺站点、采集箱商品、类目与属性、文案与 AI、
   图片与视频、价格/库存/物流、保存与发布、诊断；以及关键控件/计数文案。
3. **按钮遍历**：同 ``tools/button_audit.py`` 的语义，逐个调用 14 页全部
   ``on_click / on_change / on_submit`` 回调（网络 mock、线程/文件选择器禁用），
   任何回调抛异常记为 ERROR；硬要求 ERROR=0。
4. **写安全**：``AURORA_TEST=1`` 下「保存到 TikTok」按钮必须被测试保护拒绝、不得调用 pipeline；
   ``tiktok_publish.save_to_tiktok`` 在 AURORA_TEST/AURORA_NO_SAVE 或缺少
   ``confirm_write`` 时必须拒绝；静态扫描确认唯一写入口仍收敛在
   ``services/tiktok_publish.py``，UI 无直接 HTTP。
5. **截图**：Flet Web 预览 + Playwright/Chrome，输出
   ``docs/design/tiktok-listing-page.png``、``-2-save.png``、``-3-diagnose.png``
   （滚动到保存与诊断卡片）。截图链路不可用时降级为控件清单
   （``build/verification/tiktok-ui-inventory.json``，不覆盖 PNG）。

全程 ``AURORA_TEST=1`` + ``tools/ui_sandbox.sandbox`` 隔离配置；不联网、不写平台。

跑法：``AURORA_TEST=1 .venv/bin/python tools/verify_tiktok_ui.py``
输出：控制台 PASS/FAIL + ``build/verification/tiktok-ui-audit.json``（build 已 gitignore）。
退出码：全 PASS → 0；否则 1。
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import inspect
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AURORA_TEST"] = "1"   # 必须在 import 应用模块之前：禁止任何真实写

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _path in (str(SRC), str(TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = ROOT / "build" / "verification" / "tiktok-ui-audit.json"
INVENTORY = ROOT / "build" / "verification" / "tiktok-ui-inventory.json"
SHOT_TOP = ROOT / "docs" / "design" / "tiktok-listing-page.png"
SHOT_SAVE = ROOT / "docs" / "design" / "tiktok-listing-page-2-save.png"
SHOT_DIAG = ROOT / "docs" / "design" / "tiktok-listing-page-3-diagnose.png"
WRITE_LITERAL = "/api/tiktokProduct/" + "add" + ".json"
VIEWPORT = {"width": 1560, "height": 2200}

EXPECTED_SIGNATURES: dict[str, dict] = {
    "tiktok_pipeline.run_tiktok_product": {
        "params": ["product_id", "cfg", "overrides", "dry_run", "use_ai", "op",
                   "save_mode", "client", "confirm_write"],
        "defaults": {"dry_run": True, "use_ai": False, "op": "1",
                     "save_mode": "", "client": None, "confirm_write": False},
        "kwonly": {"dry_run", "use_ai", "op", "save_mode", "client", "confirm_write"},
    },
    "tiktok_pipeline.prepare_save": {
        "params": ["product_id", "cfg", "overrides", "client", "op"],
        "defaults": {"overrides": None, "client": None, "op": "1"},
        "kwonly": {"op"},
    },
    "tiktok_pipeline.ai_generate_field": {
        "params": ["product", "cfg", "field", "site", "count"],
        "defaults": {"site": "", "count": 5},
    },
    "tiktok_pipeline.ai_generate_all": {
        "params": ["product", "cfg", "site", "count"],
        "defaults": {"site": "", "count": 5},
    },
    "tiktok_payload.build_tiktok_payload": {
        "params": ["detail", "cfg", "overrides", "site", "shop_id", "op"],
        "defaults": {"cfg": None, "overrides": None, "site": "", "shop_id": "",
                     "op": "1"},
        "kwonly": {"site", "shop_id", "op"},
    },
    "tiktok_payload.validate_tiktok_payload": {
        "params": ["payload", "limits", "site", "is_global", "for_publish"],
        "defaults": {"limits": None, "site": "", "is_global": False,
                     "for_publish": False},
        "kwonly": {"limits", "site", "is_global", "for_publish"},
    },
    "tiktok_payload.build_warnings": {
        "params": ["payload", "site"], "defaults": {"site": ""}, "kwonly": {"site"},
    },
    "tiktok_payload.apply_site": {
        "params": ["payload", "site", "shop_id"], "defaults": {"shop_id": ""},
        "kwonly": {"shop_id"},
    },
    "tiktok_publish.make_client": {"params": ["cfg"], "defaults": {}},
    "tiktok_publish.save_to_tiktok": {
        "params": ["product_id", "cfg", "overrides", "allow_invalid", "op",
                   "save_mode", "client", "confirm_write"],
        "defaults": {"overrides": None, "allow_invalid": False, "op": "1",
                     "save_mode": "", "client": None, "confirm_write": False},
    },
}

EXPECTED_CLIENT_METHODS = [
    "check_login", "user_in", "shops", "sites", "site_limits", "warehouses",
    "list_products", "counts", "product_detail", "product_variants",
    "categories", "search_categories", "category_attributes", "category_history",
    "brands", "check_banned_words", "save_product", "publish_products",
    "delete_products",
]

EXPECTED_BUTTON_FRAGMENTS = [
    "读取本机 Cookie", "测试连接", "刷新", "上一页", "下一页",
    "加载类目", "搜索类目", "编辑类目属性",
    "AI 生成标题", "AI 生成五点", "AI 生成描述", "AI 生成关键词",
    "AI 一键生成全部", "AI 一键翻译",
    "应用图片", "从商品详情恢复图片", "清空图片", "打开视频地址",
    "刷新仓库", "保存到 TikTok", "查看保存包（只读）",
    "运行诊断",
]

CARD_HEADINGS = [
    "连接与店铺 / 站点", "采集箱商品", "类目与属性", "文案与 AI",
    "图片与视频", "价格 / 库存 / 物流", "保存与发布", "诊断",
]
TAB_TITLES = ["连接与商品", "类目与属性", "文案与图片", "价格与物流", "保存与诊断"]


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, ok: bool, detail: str,
            observed: dict | None = None) -> bool:
        row = {"name": name, "ok": bool(ok), "detail": str(detail)}
        if observed is not None:
            row["observed"] = observed
        self.rows.append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        return bool(ok)

    @property
    def passed(self) -> int:
        return sum(1 for row in self.rows if row["ok"])


# ---------------------------------------------------------------------------
# 就绪检测 / 页面构建
# ---------------------------------------------------------------------------
def page14_item():
    from aeclone.platforms import get_platform
    platform = get_platform("tiktok")
    return platform.page(14) if platform else None


def is_ready() -> tuple[bool, str]:
    from aeclone.platforms import get_platform
    platform = get_platform("tiktok")
    if platform is None:
        return False, "platforms 中没有 tiktok 平台"
    if not bool(getattr(platform, "implemented", False)):
        return False, "platforms.TIKTOK.implemented 不为 True"
    item = platform.page(14)
    if item is None:
        return False, "platforms.TIKTOK.pages 无 index 14（完整工作台隐藏路由缺失）"
    visible = [p.index for p in platform.pages if not p.group.startswith("_")]
    if visible != [0, 22, 23, 24, 25, 26, 27, 28, 10, 11]:
        return False, (f"TikTok 左侧导航应为 0/22–28/10/11（按功能拆页），实际 "
                       f"{visible}")
    if platform.groups() != ["工作空间", "商品准备", "内容处理", "发布与运行", "工具与设置"]:
        return False, f"TikTok 导航分组异常：{platform.groups()}"
    try:
        from aeclone.ui.app import App
    except Exception as exc:  # noqa: BLE001
        return False, f"UI 应用模块不可导入：{type(exc).__name__}: {exc}"
    if not hasattr(App, "_p_tiktok_listing"):
        return False, "App._p_tiktok_listing 尚未实现"
    if not hasattr(App, "_p_tiktok_section"):
        return False, "App._p_tiktok_section（左侧功能页薄封装）尚未实现"
    return True, f"visible={visible} · internal=14 · groups={platform.groups()}"


def mocked_network():
    import requests

    class MockResponse:
        status_code = 200

        def __init__(self, payload=None):
            self._payload = payload or {"code": 0, "msg": "", "data": {}}

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload, ensure_ascii=False)

    def mock_request(*args, **kwargs):
        return MockResponse()

    context = contextlib.ExitStack()
    context.enter_context(patch.object(requests.sessions.Session, "request",
                                       mock_request))
    context.enter_context(patch.object(requests.sessions.Session, "get",
                                       mock_request))
    context.enter_context(patch.object(requests.sessions.Session, "post",
                                       mock_request))
    return context


def _blocked_io(stack) -> None:
    import threading
    import flet as ft
    stack.enter_context(patch.object(threading.Thread, "start",
                                     lambda thread: None))
    for name in ("pick_files", "save_file", "get_directory_path"):
        stack.enter_context(patch.object(ft.FilePicker, name,
                                         lambda *a, **kw: None))


# ---------------------------------------------------------------------------
# 契约签名 / overrides / 唯一写入口
# ---------------------------------------------------------------------------
def check_signatures(report: Report) -> None:
    from aeclone.integrations import tiktok as integration
    from aeclone.services import (tiktok_payload, tiktok_pipeline, tiktok_publish)

    modules = {
        "tiktok_payload": tiktok_payload,
        "tiktok_pipeline": tiktok_pipeline,
        "tiktok_publish": tiktok_publish,
    }
    problems: list[str] = []
    for dotted, spec in EXPECTED_SIGNATURES.items():
        module_name, func_name = dotted.split(".")
        func = getattr(modules[module_name], func_name, None)
        if not callable(func):
            problems.append(f"{dotted} 不存在")
            continue
        sig = inspect.signature(func)
        names = list(sig.parameters)
        missing = [name for name in spec["params"] if name not in names]
        if missing:
            problems.append(f"{dotted} 缺参数 {missing}")
        for name, expected in (spec.get("defaults") or {}).items():
            param = sig.parameters.get(name)
            if param is None:
                continue
            actual = None if param.default is inspect.Parameter.empty \
                else param.default
            if actual != expected:
                problems.append(f"{dotted}.{name} 默认值 {actual!r} != {expected!r}")
        for name in spec.get("kwonly") or ():
            param = sig.parameters.get(name)
            if param is not None and param.kind is not inspect.Parameter.KEYWORD_ONLY:
                problems.append(f"{dotted}.{name} 应为 keyword-only")
    report.add("契约 §3 函数签名", not problems,
               f"{len(EXPECTED_SIGNATURES)} 个函数存在且参数兼容" if not problems
               else "；".join(problems[:8]))

    method_problems = []
    for name in EXPECTED_CLIENT_METHODS:
        if not callable(getattr(integration.TikTokClient, name, None)):
            method_problems.append(f"TikTokClient.{name} 缺失")
    save_sig = inspect.signature(integration.TikTokClient.save_product)
    for name in ("payload", "op", "save_mode"):
        if name not in save_sig.parameters:
            method_problems.append(f"TikTokClient.save_product 缺参数 {name}")
    report.add("TikTokClient 方法齐全", not method_problems,
               f"{len(EXPECTED_CLIENT_METHODS)} 个方法存在；"
               f"save_product(payload, op, save_mode)" if not method_problems
               else "；".join(method_problems))


def _ui_override_keys() -> tuple[list[str], str]:
    """AST 提取 app.py **TikTok 页**内嵌 ``_overrides()`` 返回 dict 的键（保持源码顺序）。

    注意：Amazon 13 页也有同名 ``_overrides``（17 键），必须限定在
    ``_tiktok_cards`` 内查找，避免取错 UI 版本。
    """
    source = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    scope = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tiktok_cards":
            scope = node
            break
    search_root = scope if scope is not None else tree
    for node in ast.walk(search_root):
        if not isinstance(node, ast.FunctionDef) or node.name != "_overrides":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                keys = []
                for key in child.value.keys:
                    try:
                        keys.append(ast.literal_eval(key))
                    except Exception:  # noqa: BLE001
                        keys.append(None)
                how = "AST 提取（_tiktok_cards._overrides）" if scope is not None \
                    else "AST 提取（未定位 _tiktok_cards）"
                return keys, how
    return [], "未找到 TikTok 页 _overrides() 返回 dict"


def check_overrides(report: Report) -> None:
    from aeclone.services.tiktok_payload import OVERRIDE_KEYS
    keys, how = _ui_override_keys()
    expected = list(OVERRIDE_KEYS)
    missing = [key for key in expected if key not in keys]
    extra = [key for key in keys if key not in expected]
    report.add("UI overrides 26 键 == OVERRIDE_KEYS",
               len(keys) == len(expected) and not missing and not extra,
               f"{how}: UI {len(keys)} 键 / 后端 {len(expected)} 键"
               + (f"；缺 {missing}" if missing else "")
               + (f"；多 {extra}" if extra else ""))


def check_single_write_entry(report: Report) -> None:
    tiktok_files = [
        SRC / "aeclone" / "integrations" / "tiktok.py",
        *sorted((SRC / "aeclone" / "services").glob("tiktok_*.py")),
    ]
    callers: list[str] = []
    offenders: list[str] = []
    for path in tiktok_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        if ".save_product(" in text:
            callers.append(rel)
        if WRITE_LITERAL in text:
            offenders.append(rel)
    external = [rel for rel in callers
                if rel != "src/aeclone/integrations/tiktok.py"]
    bad_offenders = [rel for rel in offenders
                     if rel != "src/aeclone/integrations/tiktok.py"]
    report.add("唯一写入口 save_product 收敛",
               external == ["src/aeclone/services/tiktok_publish.py"]
               and not bad_offenders,
               f"外部调用方={external or '无'}；写入口字面量={offenders or '无'}"
               + (f"（越界={bad_offenders}）" if bad_offenders else ""))

    ui_files = list((SRC / "aeclone" / "ui").glob("*.py")) + [
        SRC / "aeclone" / "platforms.py", SRC / "aeclone" / "config.py"]
    ui_hits = []
    for path in ui_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for needle in ("requests.", "save_product(", WRITE_LITERAL, "urlopen("):
            if needle in text:
                ui_hits.append(f"{rel}:{needle}")
    report.add("UI 无直接 HTTP / 无写入口", not ui_hits,
               "UI 只经 _tiktok_backend()/pipeline" if not ui_hits
               else "；".join(ui_hits[:8]))

    app_text = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    needs = {
        "调用 pipeline.run_tiktok_product": "pipeline.run_tiktok_product(" in app_text,
        "调用 pipeline.prepare_save": "pipeline.prepare_save(" in app_text,
        "经 _tiktok_backend() 取 make_client": "_tiktok_backend()" in app_text,
    }
    report.add("UI 写路径收敛到后端收口", all(needs.values()),
               "；".join(f"{name}={ok}" for name, ok in needs.items()))


# ---------------------------------------------------------------------------
# 写安全护栏
# ---------------------------------------------------------------------------
def check_write_guards(report: Report) -> None:
    from aeclone.services import tiktok_publish as publish

    def scenario(env: dict, confirm: bool) -> tuple[bool, str]:
        clean = {key: value for key, value in os.environ.items()
                 if key not in ("AURORA_TEST", "AURORA_NO_SAVE")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            try:
                publish.save_to_tiktok("product-1", None, confirm_write=confirm)
            except RuntimeError as exc:
                return True, f"拒绝：{str(exc)[:100]}"
            except Exception as exc:  # noqa: BLE001
                return False, f"非预期异常 {type(exc).__name__}: {exc}"
        return False, "未拒绝真实保存"

    results = [
        scenario({"AURORA_TEST": "1"}, True),
        scenario({"AURORA_NO_SAVE": "1"}, True),
        scenario({"AURORA_TEST": "", "AURORA_NO_SAVE": ""}, False),
    ]
    report.add("AURORA_TEST/AURORA_NO_SAVE/缺 confirm_write 拒绝真实写",
               all(ok for ok, _ in results),
               "；".join(detail for _, detail in results))

    # UI 运行期：AURORA_TEST 下点「保存到 TikTok」必须被测试保护拒绝、不调用 pipeline。
    from ui_sandbox import build_app, sandbox, walk
    import flet as ft
    from aeclone.services import tiktok_pipeline

    def boom(*args, **kwargs):
        raise AssertionError("UI 测试保护模式不得调用 pipeline.run_tiktok_product")

    with sandbox():
        app = build_app()
        app._switch_platform("tiktok")
        app._switch(14)
        save_button = next(
            (control for control in walk(app.content.controls)
             if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                     ft.TextButton))
             and any(x in str(getattr(control, "text", "") or "")
                     for x in ("保存到 TikTok",))), None)
        if save_button is None:
            report.add("UI 测试保护模式保存按钮拒绝", False,
                       "找不到保存按钮（保存到 TikTok）")
            return
        with patch.object(tiktok_pipeline, "run_tiktok_product", boom), \
             patch.object(tiktok_pipeline, "prepare_save", boom):
            try:
                save_button.on_click(SimpleNamespace(control=save_button, data="",
                                                    page=app.page))
                ok = True
                error = ""
            except Exception as exc:  # noqa: BLE001
                ok = False
                error = f"{type(exc).__name__}: {exc}"
        texts = [str(getattr(control, "value", "") or "")
                 for control in walk(app.content.controls) if isinstance(control, ft.Text)]
        rejected = any(("测试保护" in text or "已拒绝" in text)
                       for text in texts)
    report.add("UI 测试保护模式保存按钮拒绝", ok and rejected,
               f"按钮文案={getattr(save_button, 'text', '')!r}；"
               f"状态提示={'已显示测试保护拒绝' if rejected else '未显示'}"
               + (f"；异常={error}" if error else ""))


# ---------------------------------------------------------------------------
# 14 页控件 / 卡片 / 计数
# ---------------------------------------------------------------------------
def check_page_controls(report: Report) -> None:
    from ui_sandbox import build_app, label, sandbox, walk
    import flet as ft

    with sandbox():
        app = build_app()
        app._switch_platform("tiktok")
        app._switch(14)
        landed = getattr(app, "_page_idx", None)
        controls = list(walk(app.content.controls))
        nav_count = len(getattr(app, "nav_buttons", []))
        app._switch(22)
        landed15 = getattr(app, "_page_idx", None)
        c15 = list(walk(app.content.controls))
        app._switch(28)
        landed21 = getattr(app, "_page_idx", None)
        c21 = list(walk(app.content.controls))
        from aeclone.platforms import get_platform as _gp
        _pf = _gp("tiktok")
        _visible = [p.index for p in _pf.pages if not p.group.startswith("_")]
        _groups = _pf.groups()

    dropdowns = [str(getattr(control, "label", "") or label(control))
                 for control in controls if isinstance(control, ft.Dropdown)]
    textfields = [str(getattr(control, "label", "") or label(control))
                  for control in controls if isinstance(control, ft.TextField)]
    button_texts = [str(getattr(control, "text", "") or "")
                    for control in controls
                    if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                            ft.TextButton, ft.IconButton))]
    texts = [str(getattr(control, "value", "") or "")
             for control in controls if isinstance(control, ft.Text)]
    all_text = "\n".join(texts + button_texts + dropdowns + textfields)
    _tabs_ctl = next((c for c in controls if isinstance(c, ft.Tabs)), None)
    b15 = [str(getattr(control, "text", "") or "") for control in c15
           if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                   ft.TextButton, ft.IconButton))]
    b21 = [str(getattr(control, "text", "") or "") for control in c21
           if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                   ft.TextButton, ft.IconButton))]

    checks = {
        "page_14": landed == 14,
        "导航拆分 10 页": (_visible == [0, 22, 23, 24, 25, 26, 27, 28, 10, 11]
                          and _groups == ["工作空间", "商品准备", "内容处理",
                                          "发布与运行", "工具与设置"]
                          and nav_count == 10),
        "22 页=连接与店铺": (landed15 == 22 and any("测试连接" in x for x in b15)
                          and not any("保存到 TikTok" in x for x in b15)),
        "28 页=保存与诊断": (landed21 == 28
                          and any("保存到 TikTok" in x for x in b21)
                          and any("运行诊断" in x for x in b21)),
        "8 张卡片标题": all(heading in all_text for heading in CARD_HEADINGS),
        "5 个分栏 Tab": ([str(t.text) for t in _tabs_ctl.tabs] == TAB_TITLES
                        if _tabs_ctl is not None else False),
        "店铺下拉": any("店铺（TikTok Shop）" in text for text in dropdowns),
        "站点下拉": any("站点（L7）" in text for text in dropdowns),
        "店铺/站点分离提示": "店铺与站点分开" in all_text,
        "无店铺引导": "未检测到 TikTok 店铺" in all_text,
        "无店铺站点提示": "未绑定店铺：可先选站点" in all_text,
        "站点限额文案": ("25–255" in all_text and "10000" in all_text
                         and "1–9" in all_text and "99999" in all_text),
        "采集箱三态按钮": all(
            any(state in text for text in button_texts)
            for state in ("采集箱", "待发布", "在线")),
        "类目控件": all(any(name in text for text in dropdowns)
                        for name in ("顶级类目", "最近使用类目")),
        "类目搜索": any("搜索类目关键词" in text for text in textfields),
        "标题计数/255": any("/255" in text for text in texts),
        "描述计数/10000": any("/10000" in text for text in texts),
        "五点计数/1500": any("/1500" in text for text in texts),
        "关键词计数/250": any("/250" in text for text in texts),
        "AI 开关": any(isinstance(control, ft.Switch) and
                       "使用 AI" in str(getattr(control, "label", ""))
                       for control in controls),
        "图片/视频控件": (any("主图 URL" in text for text in textfields)
                          and any("视频 ID" in text for text in textfields)),
        "价格/库存/物流控件": all(
            any(name in text for text in textfields)
            for name in ("价格", "库存", "SKU", "重量", "长 (cm)", "宽 (cm)", "高 (cm)")),
        "保存动作下拉": any("保存动作" in text for text in dropdowns),
        "保存/保存包按钮": all(
            any(fragment in text for text in button_texts)
            for fragment in ("保存到 TikTok", "查看保存包（只读）")),
        "诊断按钮": any("运行诊断" in text for text in button_texts),
        "全部关键按钮": all(any(fragment in text for text in button_texts)
                            for fragment in EXPECTED_BUTTON_FRAGMENTS),
        "不展示 SMT 卡片": all(marker not in all_text for marker in
                               ("营销图", "定时发布", "EU 资质", "模板商品",
                                "批量管线")),
    }
    missing = [name for name, ok in checks.items() if not ok]
    report.add(f"14 页关键控件（{len(checks)} 项）", not missing,
               f"{sum(checks.values())}/{len(checks)} 项通过"
               + (f"；缺 {missing}" if missing else ""),
               {"landed": landed, "controls": len(controls),
                "dropdowns": len(dropdowns), "textfields": len(textfields),
                "buttons": len(button_texts), "failures": missing})


# ---------------------------------------------------------------------------
# 按钮/回调遍历（全部 on_click / on_change / on_submit）
# ---------------------------------------------------------------------------
def _collect_specs() -> list[dict]:
    from ui_sandbox import build_app, label, sandbox, walk

    with sandbox(), mocked_network():
        app = build_app()
        app._switch_platform("tiktok")
        app._switch(14)
        specs = []
        for position, control in enumerate(walk(app.content.controls)):
            for event in ("on_click", "on_change", "on_submit"):
                callback = getattr(control, event, None)
                if not callable(callback) or bool(getattr(control, "disabled", False)):
                    continue
                specs.append({"position": position, "event": event,
                              "label": label(control),
                              "type": type(control).__name__})
    return specs


def traverse_callbacks() -> dict:
    import flet as ft
    from ui_sandbox import build_app, sandbox, walk

    specs = _collect_specs()
    results: list[dict] = []
    for spec in specs:
        status, detail = "OK", ""
        with sandbox() as _cfg, mocked_network(), contextlib.ExitStack() as stack:
            import threading
            deferred: list[str] = []
            native: list[str] = []
            stack.enter_context(patch.object(
                threading.Thread, "start",
                lambda thread: deferred.append(
                    getattr(getattr(thread, "_target", None), "__name__",
                            "background"))))
            for name in ("pick_files", "save_file", "get_directory_path"):
                stack.enter_context(patch.object(
                    ft.FilePicker, name,
                    lambda *a, _name=name, **kw: native.append(_name)))
            app = build_app()
            app._switch_platform("tiktok")
            app._switch(14)
            controls = list(walk(app.content.controls))
            if spec["position"] >= len(controls):
                results.append({**spec, "status": "ERROR",
                                "detail": "控件位置漂移（重建后不存在）"})
                continue
            control = controls[spec["position"]]
            callback = getattr(control, spec["event"], None)
            try:
                if spec["event"] == "on_change" and isinstance(
                        control, (ft.Switch, ft.Checkbox)):
                    control.value = not control.value
                data = str(getattr(control, "value", "") or "")
                callback(SimpleNamespace(control=control, data=data,
                                         page=app.page))
                outcome = ("NATIVE" if native else
                           "DEFERRED" if deferred else "OK")
                detail = ", ".join(native or deferred)
            except Exception as exc:  # noqa: BLE001 - 记录后继续遍历
                outcome = "ERROR"
                detail = f"{type(exc).__name__}: {exc}"[:240]
        results.append({**spec, "status": outcome, "detail": detail})
    errors = [row for row in results if row["status"] == "ERROR"]
    return {
        "callbacks": len(results),
        "ok": len(results) - len(errors),
        "errors": len(errors),
        "results": results,
    }


def check_traversal(report: Report) -> None:
    traversal = traverse_callbacks()
    report.add("14 页回调遍历 ERROR=0", traversal["errors"] == 0,
               f"{traversal['callbacks']} 个回调 · OK {traversal['ok']} · "
               f"ERROR {traversal['errors']}"
               + ("；" + "；".join(f"{row['label']}[{row['event']}]:{row['detail']}"
                                  for row in traversal["results"]
                                  if row["status"] == "ERROR")[:400]
                  if traversal["errors"] else ""),
               {"callbacks": traversal["callbacks"],
                "ok": traversal["ok"], "errors": traversal["errors"],
                "error_rows": [row for row in traversal["results"]
                               if row["status"] == "ERROR"]})
    return traversal


# ---------------------------------------------------------------------------
# 截图：Flet Web 预览 + Playwright/Chrome（滚动到预演/诊断区域）
# ---------------------------------------------------------------------------
class _PreviewServer:
    def __init__(self, platform_id: str, page_index: int, port: int, tab: int = 0):
        self.platform_id = platform_id
        self.page_index = page_index
        self.port = port
        self.tab = int(tab or 0)
        self.process: subprocess.Popen | None = None
        self.log_path = ROOT / "build" / "verification" / "tiktok-ui-preview.log"

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "AURORA_TEST": "1",
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "build" / "ui-preview-deps"), str(SRC), str(TOOLS)]),
            "AURORA_WINDOW": "1514x945",
            "AURORA_TAB": str(self.tab),
        })
        log = open(self.log_path, "w")
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--serve", "--port", str(self.port),
             "--platform", self.platform_id, "--page", str(self.page_index)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
        self._log = log

    def wait(self, timeout: float = 40.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.4)
                if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                    return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=6)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                process.kill()
        with contextlib.suppress(Exception):
            self._log.close()
        self.process = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def capture_screenshot(platform_id: str, page_index: int, output: Path,
                       scroll_y: int = 0,
                       viewport: dict | None = None,
                       tab: int = 0) -> tuple[bool, str]:
    """打开 Flet Web 预览，滚动 scroll_y 像素后截图。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return False, f"Playwright 不可用：{type(exc).__name__}: {exc}"
    port = _free_port()
    server = _PreviewServer(platform_id, page_index, port, tab=tab)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start()
        if not server.wait():
            return False, f"Flet 预览服务端口 {port} 未就绪（见 {server.log_path}）"
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel="chrome", headless=True, args=["--headless=new"])
            except Exception:  # noqa: BLE001 - 无 Chrome channel 时用内置 Chromium
                browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport=viewport or VIEWPORT, device_scale_factor=1)
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}", wait_until="load",
                          timeout=45000)
                with contextlib.suppress(Exception):
                    page.wait_for_selector("canvas", timeout=25000)
                page.wait_for_timeout(3500)  # CanvasKit 字体/布局稳定
                if scroll_y:
                    page.mouse.move(760, 1100)
                    page.mouse.wheel(0, scroll_y)
                    page.wait_for_timeout(1500)
                page.screenshot(path=str(output))
            finally:
                browser.close()
        if not output.exists() or output.stat().st_size <= 0:
            return False, f"截图文件为空：{output}"
        return True, f"{output} ({output.stat().st_size} bytes)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        server.stop()


def write_control_inventory(traversal: dict) -> Path:
    """截图链路不可用时的降级产物：控件清单（写到 build/，不覆盖 PNG）。"""
    from ui_sandbox import build_app, label, sandbox, walk

    inventory = []
    with sandbox(), mocked_network():
        app = build_app()
        app._switch_platform("tiktok")
        app._switch(14)
        for position, control in enumerate(walk(app.content.controls)):
            inventory.append({
                "position": position, "type": type(control).__name__,
                "label": label(control)[:160],
                "has_on_click": callable(getattr(control, "on_click", None)),
                "disabled": bool(getattr(control, "disabled", False)),
            })
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps({
        "platform": "tiktok", "page": 14,
        "note": "PNG 截图链路不可用，降级为控件清单（控件树文本 dump）。",
        "controls": inventory, "traversal": traversal,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return INVENTORY


def check_screenshots(report: Report) -> dict:
    """三张截图：顶部 / 预演+保存 / 诊断（滚动到不同区域，且内容必须互不重复）。"""
    import hashlib

    # 页面已改为 workspace_tabs（与速卖通等页面一致）：
    # 顶部=第1栏、保存=第5栏顶部、诊断=第5栏向下滚动。
    targets = [
        ("14 页顶部截图", SHOT_TOP, 0, dict(VIEWPORT), 0),
        ("14 页保存截图", SHOT_SAVE, 0, dict(VIEWPORT), 4),
        ("14 页诊断截图", SHOT_DIAG, 700,
         {"width": 1560, "height": 1400}, 4),
    ]
    observed: dict = {}
    for name, path, scroll_y, viewport, tab in targets:
        ok, detail = False, ""
        for attempt in range(2):  # 截图链路偶发慢：重试一次
            ok, detail = capture_screenshot("tiktok", 14, path, scroll_y=scroll_y,
                                            viewport=viewport, tab=tab)
            if ok:
                break
            time.sleep(2)
        digest = ""
        if path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        observed[path.name] = {"ok": ok, "detail": detail, "tab": tab,
                               "scroll_y": scroll_y, "viewport": viewport,
                               "bytes": path.stat().st_size if path.exists() else 0,
                               "sha256": digest}
        report.add(name, ok, detail)
    digests = [row["sha256"] for row in observed.values() if row["ok"]]
    report.add("三张截图内容互不重复", len(digests) == 3 and len(set(digests)) == 3,
               f"{len(digests)} 张成功；唯一哈希 {len(set(digests))} 个",
               {"sha256": {key: row["sha256"] for key, row in observed.items()}})
    return observed


# ---------------------------------------------------------------------------
# --serve：Flet Web 预览进程（供截图子进程拉起）
# ---------------------------------------------------------------------------
CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


def _preview_assets_dir() -> tuple[Path, str]:
    """准备预览静态目录：真实 assets + 本地 CJK 字体。

    Flet Web(CanvasKit) 的中文字体回退依赖浏览器从 CDN 拉 Noto 字体，网络抖动时
    截图里的中文会变成方框。这里把本机 CJK 字体注册成 page.fonts，保证截图稳定。
    返回 (assets_dir, font_family)；找不到字体时返回 (assets, "")。
    """
    import shutil

    base = ROOT / "build" / "ui-preview-assets"
    base.mkdir(parents=True, exist_ok=True)
    real_assets = ROOT / "assets"
    link = base / "assets"
    if not link.exists():
        with contextlib.suppress(OSError):
            link.symlink_to(real_assets, target_is_directory=True)
    font_file = next((Path(p) for p in CJK_FONT_CANDIDATES if Path(p).is_file()), None)
    if font_file is None:
        return real_assets, ""
    dst = base / "aurora-cjk.ttf"
    if not dst.exists() or dst.stat().st_size != font_file.stat().st_size:
        with contextlib.suppress(OSError):
            shutil.copyfile(font_file, dst)
    if not dst.exists():
        return real_assets, ""
    return base, "AuroraCJK"


def serve(platform_id: str, page_index: int, port: int, theme: str) -> None:
    os.environ["AURORA_THEME"] = theme
    import flet as ft
    from ui_sandbox import build_app, sandbox

    assets_dir, font_family = _preview_assets_dir()

    def target(page):
        if font_family:
            with contextlib.suppress(Exception):
                page.fonts = {font_family: "aurora-cjk.ttf"}
        app = build_app(page)
        if font_family:
            # build_app 会用 App 主题把 font_family 设成 "PingFang SC"（系统字体，
            # CanvasKit 取不到 → 中文变方框）；截图进程里改成已注册的本地字体。
            with contextlib.suppress(Exception):
                for _th in (getattr(page, "theme", None),
                            getattr(page, "dark_theme", None)):
                    if _th is None:
                        continue
                    _th.font_family = font_family
                    _bm = None
                    with contextlib.suppress(Exception):
                        _bm = _th.text_theme.body_medium
                    _bm = _bm or ft.TextStyle()
                    _bm.font_family = font_family
                    _th.text_theme = ft.TextTheme(body_medium=_bm)
        app._switch_platform(platform_id)
        app._switch(page_index)
        with contextlib.suppress(Exception):
            page.update()

    with sandbox(threads=True):
        ft.app(target=target, view=ft.AppView.WEB_BROWSER, host="127.0.0.1",
               port=port, assets_dir=str(assets_dir),
               web_renderer=ft.WebRenderer.CANVAS_KIT)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TikTok 14 页沙箱 UI 审计 + 按钮遍历 + 截图")
    parser.add_argument("--page", type=int, default=14, help="页面号（默认 14）")
    parser.add_argument("--platform", default="tiktok", help="平台 id（默认 tiktok）")
    parser.add_argument("--output", default=str(OUTPUT), help="审计 JSON 路径")
    parser.add_argument("--theme", default="dark", help="dark / light")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--no-screenshots", action="store_true",
                        help="跳过截图（仅做控件/回调审计）")
    args = parser.parse_args(argv)

    if args.serve:
        serve(args.platform, args.page, args.port or 8892, args.theme)
        return 0

    ready, detail = is_ready()
    if not ready:
        print(f"NOT_READY: {detail}")
        return 0
    print(f"READY: {detail}")

    report = Report()
    check_signatures(report)
    check_overrides(report)
    check_single_write_entry(report)
    check_write_guards(report)
    check_page_controls(report)
    traversal = check_traversal(report)

    screenshots: dict = {}
    if not args.no_screenshots:
        screenshots = check_screenshots(report)
        if not all(row["ok"] for row in screenshots.values()):
            try:
                inventory = write_control_inventory(traversal)
                print(f"截图降级：控件清单 {inventory}")
            except Exception as exc:  # noqa: BLE001
                print(f"控件清单降级失败：{type(exc).__name__}: {exc}")

    total = len(report.rows)
    passed = report.passed
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "generated_at": __import__("datetime").datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "mode": "sandbox UI audit (AURORA_TEST=1; no network; no write)",
        "platform": args.platform,
        "page": args.page,
        "checks": report.rows,
        "summary": {"passed": passed, "total": total,
                    "failed": [row["name"] for row in report.rows
                               if not row["ok"]]},
        "traversal": {"callbacks": traversal["callbacks"],
                      "ok": traversal["ok"],
                      "errors": traversal["errors"],
                      "error_rows": [row for row in traversal["results"]
                                     if row["status"] == "ERROR"],
                      "statuses": {
                          status: sum(row["status"] == status
                                      for row in traversal["results"])
                          for status in ("OK", "DEFERRED", "NATIVE",
                                         "EXTERNAL", "ERROR")}},
        "screenshots": screenshots,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n结果：{passed}/{total} PASS · 回调 {traversal['callbacks']} 个 · "
          f"ERROR {traversal['errors']} · 证据 {output}")
    if screenshots:
        print("截图：" + "；".join(f"{name}={row['bytes']}B"
                                  for name, row in screenshots.items()))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
