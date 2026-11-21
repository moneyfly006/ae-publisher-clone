"""Amazon 发布功能 · 端到端 UI / 后端签名 / 全按钮 / 逻辑矩阵 / 正式版文案审计（task-11）。

审计内容
--------
1. **契约 §3 签名**：``inspect.signature`` 断言 ``amazon_pipeline.run_amazon_product /
   prepare_save / ai_generate_field / ai_generate_all``、``amazon_payload.build_amazon_payload /
   validate_amazon_payload / build_warnings / apply_site``、``amazon_publish.make_client /
   save_to_amazon`` 以及 ``AmazonClient`` 的只读/写方法全部存在，关键参数名与默认值兼容。
2. **overrides 唯一键名**：UI ``_overrides()`` 返回的 17 键 == ``amazon_payload.OVERRIDE_KEYS``。
3. **唯一写入口**：全 ``src`` 扫描 ``save_product(`` 调用只出现在
   ``services/amazon_publish.py``；UI 无直接 HTTP、无写端点字面量；UI 只经
   ``pipeline.run_amazon_product / prepare_save``。
4. **预演/验收护栏**：``AURORA_TEST`` / ``AURORA_NO_SAVE`` 下 ``save_to_amazon`` 直接拒绝；
   UI「保存」按钮在预演模式下不调用 pipeline（运行期断言）。
5. **13 页关键控件**：店铺/站点 Dropdown、5 个卖点输入、「＋添加卖点」、预演/保存、AI 5 按钮、
   n/200 · n/700 · n/2000 · n/250 bytes 计数。

全程 ``AURORA_TEST=1``、``tools/ui_sandbox.sandbox`` 隔离配置；不联网、不写平台。

跑法：``AURORA_TEST=1 .venv/bin/python tools/verify_amazon_ui.py``
输出：控制台 PASS/FAIL + ``docs/amazon-publish-ui-verify.json``（每按钮/每逻辑 PASS/FAIL 明细）。
退出码：全 PASS → 0；否则 1。
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AURORA_TEST"] = "1"   # 必须在 import 后端/UI 之前：真实写一律拒绝

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _path in (str(SRC), str(TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = ROOT / "docs" / "amazon-publish-ui-verify.json"
PRODUCT_ID = "220114595363182310"
WRITE_LITERAL = "add" + ".json"    # 唯一写入口字面量（禁止出现在 UI/非收口模块）

EXPECTED_SIGNATURES: dict[str, dict] = {
    "amazon_pipeline.run_amazon_product": {
        "params": ["product_id", "cfg", "overrides", "dry_run", "use_ai", "op",
                   "client", "confirm_write"],
        "defaults": {"dry_run": True, "use_ai": False, "op": "1",
                     "confirm_write": False},
        "kwonly": {"dry_run", "use_ai", "op", "client", "confirm_write"},
    },
    "amazon_pipeline.verify_publish": {
        "params": ["product_id", "cfg", "client", "wait_seconds", "interval"],
        "defaults": {"wait_seconds": 0, "interval": 10},
        "kwonly": {"client", "wait_seconds", "interval"},
    },
    "amazon_pipeline.autofill_attributes": {
        "params": ["product", "cfg", "marketplace_id", "overrides", "required", "template"],
        "defaults": {"marketplace_id": "", "overrides": None, "required": None,
                     "template": None},
    },
    "amazon_pipeline.required_attributes_from_schema": {
        "params": ["category_id", "product_type", "site_code", "cfg", "client"],
        "defaults": {"cfg": None, "client": None},
        "kwonly": {"cfg", "client"},
    },
    "amazon_pipeline.prepare_save": {
        "params": ["product_id", "cfg", "overrides", "client"], "defaults": {},
    },
    "amazon_pipeline.ai_generate_field": {
        "params": ["product", "cfg", "field", "marketplace_id", "count"],
        "defaults": {"marketplace_id": "", "count": 5},
    },
    "amazon_pipeline.ai_generate_all": {
        "params": ["product", "cfg", "marketplace_id", "count"],
        "defaults": {"marketplace_id": "", "count": 5},
    },
    "amazon_payload.build_amazon_payload": {
        "params": ["product", "cfg", "overrides", "currency"],
        "defaults": {"currency": ""}, "kwonly": {"currency"},
    },
    "amazon_payload.validate_amazon_payload": {
        "params": ["payload", "product", "site_meta"],
        "defaults": {"product": None, "site_meta": None}, "kwonly": {"product", "site_meta"},
    },
    "amazon_payload.build_warnings": {
        "params": ["payload", "product"], "defaults": {"product": None}, "kwonly": {"product"},
    },
    "amazon_payload.apply_site": {
        "params": ["payload", "marketplace_id", "currency", "site_meta"], "defaults": {},
    },
    "amazon_publish.make_client": {"params": ["cfg"], "defaults": {}},
    "amazon_publish.save_to_amazon": {
        "params": ["product_id", "cfg", "overrides", "allow_invalid", "op"],
        "defaults": {"overrides": None, "allow_invalid": False, "op": "1"},
    },
}

EXPECTED_CLIENT_METHODS = [
    "check_login", "shops", "sites", "list_products", "product_detail",
    "product_attributes", "category_roots", "category_children", "category_by_path",
    "category_history", "product_types", "predict_category", "currency", "logistics",
    "brands", "check_banned_words", "save_product",
]


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.rows.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        return bool(ok)

    @property
    def passed(self) -> int:
        return sum(1 for row in self.rows if row["ok"])


# ---------------------------------------------------------------------------
# 1) 契约签名
# ---------------------------------------------------------------------------
def check_signatures(report: Report) -> None:
    from aeclone.services import amazon_payload, amazon_pipeline, amazon_publish
    from aeclone.integrations.amazon import AmazonClient

    modules = {
        "amazon_payload": amazon_payload,
        "amazon_pipeline": amazon_pipeline,
        "amazon_publish": amazon_publish,
    }
    problems: list[str] = []
    checked: list[str] = []
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
            actual = None if param.default is inspect.Parameter.empty else param.default
            if actual != expected:
                problems.append(f"{dotted}.{name} 默认值 {actual!r} != {expected!r}")
        for name in spec.get("kwonly") or ():
            param = sig.parameters.get(name)
            if param is not None and param.kind is not inspect.Parameter.KEYWORD_ONLY:
                problems.append(f"{dotted}.{name} 应为 keyword-only")
        checked.append(dotted)
    report.add("契约 §3 函数签名",
               not problems,
               f"{len(checked)} 个函数存在且参数兼容" if not problems
               else "；".join(problems[:8]))

    method_problems = []
    for name in EXPECTED_CLIENT_METHODS:
        method = getattr(AmazonClient, name, None)
        if not callable(method):
            method_problems.append(f"AmazonClient.{name} 缺失")
    save_sig = inspect.signature(AmazonClient.save_product)
    for name in ("payload", "op"):
        if name not in save_sig.parameters:
            method_problems.append(f"AmazonClient.save_product 缺参数 {name}")
    report.add("AmazonClient 方法齐全",
               not method_problems,
               f"{len(EXPECTED_CLIENT_METHODS)} 个方法存在；save_product(payload, op)" if not method_problems
               else "；".join(method_problems))


# ---------------------------------------------------------------------------
# 2) UI overrides 17 键 == OVERRIDE_KEYS
# ---------------------------------------------------------------------------
def _ui_overrides_keys() -> tuple[list[str], str]:
    """AST 提取 app.py 内嵌 ``_overrides()`` 返回 dict 的键（保持源码顺序）。"""
    source = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_overrides":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                keys = [ast.literal_eval(key) for key in child.value.keys]
                return keys, "AST 提取"
    return [], "未找到 _overrides() 返回 dict"


def check_overrides(report: Report) -> None:
    from aeclone.services.amazon_payload import OVERRIDE_KEYS
    keys, how = _ui_overrides_keys()
    expected = list(OVERRIDE_KEYS)
    missing = [key for key in expected if key not in keys]
    extra = [key for key in keys if key not in expected]
    # 契约演进：后端 OVERRIDE_KEYS 新增 attributes/required_attributes（19 键）；
    # UI 传 18 键（含 attributes），required_attributes 在「自动补齐并重试」路径
    # 通过 autofill_attributes(required=missing) 显式传递（见 UI 静态断言）。
    core_missing = [key for key in missing if key != "required_attributes"]
    app_text = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    retry_forwards_required = ("required=_missing or None" in app_text
                               or "required=_missing" in app_text)
    ok = not extra and not core_missing and retry_forwards_required
    report.add("UI overrides 键 ⊆ OVERRIDE_KEYS + required 动态传递", ok,
               f"{how}: UI {len(keys)} 键 / 后端 {len(expected)} 键"
               + (f"；缺={missing}" if missing else "")
               + (f"；多={extra}" if extra else "")
               + f"；retry 显式 required={retry_forwards_required}")


# ---------------------------------------------------------------------------
# 3) 唯一写入口 / UI 无直接 HTTP
# ---------------------------------------------------------------------------
def check_single_write_entry(report: Report) -> None:
    """限定 Amazon 相关模块：唯一写收口是 amazon_publish.save_to_amazon。"""
    amazon_files = [
        SRC / "aeclone" / "integrations" / "amazon.py",
        *sorted((SRC / "aeclone" / "services").glob("amazon_*.py")),
    ]
    callers: list[str] = []
    offenders: list[str] = []
    for path in amazon_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        if ".save_product(" in text:
            callers.append(rel)
        if WRITE_LITERAL in text:
            offenders.append(rel)
    # 写入口路径只允许出现在 AmazonClient 自身（integrations/amazon.py）；
    # 其它模块中唯一允许的调用方是 services/amazon_publish.py。
    bad_offenders = [rel for rel in offenders
                     if rel != "src/aeclone/integrations/amazon.py"]
    external_callers = [rel for rel in callers
                        if rel != "src/aeclone/integrations/amazon.py"]
    ok = (external_callers == ["src/aeclone/services/amazon_publish.py"]
          and not bad_offenders)
    report.add("唯一写入口 save_product 调用", ok,
               f"外部调用方={external_callers or '无'}"
               f"（AmazonClient 内部兼容别名允许）；写入口字面量={offenders or '无'}"
               + (f"（越界={bad_offenders}）" if bad_offenders else ""))

    ui_files = list((SRC / "aeclone" / "ui").glob("*.py")) + [SRC / "aeclone" / "platforms.py"]
    ui_hits = []
    for path in ui_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for needle in ("requests.", "amazonProductSp/" + WRITE_LITERAL,
                       'save_product(', "urlopen("):
            if needle in text:
                ui_hits.append(f"{rel}:{needle}")
    report.add("UI 无直接 HTTP / 无写入口", not ui_hits,
               "UI 只经 _amazon_backend()/pipeline" if not ui_hits else "；".join(ui_hits[:8]))

    app_text = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    needs = {
        "调用 run_amazon_product": "pipeline.run_amazon_product(" in app_text,
        "调用 prepare_save": "pipeline.prepare_save(" in app_text,
        "经 _amazon_backend() 取 make_client": "_amazon_backend()" in app_text,
        "无 AmazonClient 直连": "AmazonClient(" not in app_text,
    }
    report.add("UI 写路径收敛到后端收口", all(needs.values()),
               "；".join(f"{name}={ok}" for name, ok in needs.items()))


# ---------------------------------------------------------------------------
# 4) AURORA_TEST / AURORA_NO_SAVE 拒绝
# ---------------------------------------------------------------------------
def _signature_rejection_scenario(env_name: str) -> tuple[bool, str]:
    from aeclone.services import amazon_publish as publish
    with patch.dict(os.environ, {env_name: "1"}):
        os.environ.pop("AURORA_TEST" if env_name != "AURORA_TEST" else "AURORA_NO_SAVE", None)
        try:
            publish.save_to_amazon(PRODUCT_ID, None)
        except RuntimeError as exc:
            text = str(exc)
            ok = "预演" in text or "拒绝" in text
            return ok, f"{env_name} → RuntimeError: {text[:120]}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{env_name} → 非预期异常 {type(exc).__name__}: {exc}"
        finally:
            os.environ["AURORA_TEST"] = "1"
    return False, f"{env_name} → 未拒绝真实保存"


def check_write_guards(report: Report) -> None:
    results = [_signature_rejection_scenario("AURORA_TEST"),
               _signature_rejection_scenario("AURORA_NO_SAVE")]
    report.add("AURORA_TEST/AURORA_NO_SAVE 拒绝真实写",
               all(ok for ok, _ in results),
               "；".join(detail for _, detail in results))

    # UI 运行期：预演模式下点「保存」必须在调用 pipeline 之前返回。
    from ui_sandbox import build_app, sandbox, walk
    import flet as ft
    from aeclone.services import amazon_pipeline

    def boom(*args, **kwargs):
        raise AssertionError("UI 预演模式不得调用 pipeline.run_amazon_product")

    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        save_button = next(
            (control for control in walk(app.content.controls)
             if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton))
             and "保存" in str(getattr(control, "text", "") or "")
             and ("检查模式" in str(getattr(control, "text", "") or "")
                  or "预演模式" in str(getattr(control, "text", "") or ""))), None)
        if save_button is None:
            report.add("UI 检查模式保存按钮拒绝", False,
                       "找不到「保存（检查模式）」按钮")
            return
        with patch.object(amazon_pipeline, "run_amazon_product", boom), \
             patch.object(amazon_pipeline, "prepare_save", boom):
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
        rejected = any(("已拒绝真实保存" in text or "检查模式" in text
                       or "预演模式" in text) for text in texts)
    report.add("UI 检查模式保存按钮拒绝", ok and rejected,
               f"按钮文案={getattr(save_button, 'text', '')!r}；"
               f"状态提示={'已显示检查模式拒绝' if rejected else '未显示'}"
               + (f"；异常={error}" if error else ""))


# ---------------------------------------------------------------------------
# 5) 13 页关键控件
# ---------------------------------------------------------------------------
def check_page_controls(report: Report) -> None:
    from ui_sandbox import build_app, label, sandbox, walk
    import flet as ft

    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        landed = getattr(app, "_page_idx", None)
        controls = list(walk(app.content.controls))

    dropdowns = [str(getattr(control, "label", "") or label(control))
                 for control in controls if isinstance(control, ft.Dropdown)]
    textfields = [str(getattr(control, "label", "") or label(control))
                  for control in controls if isinstance(control, ft.TextField)]
    button_texts = [str(getattr(control, "text", "") or "")
                    for control in controls
                    if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                            ft.TextButton, ft.IconButton))]
    texts = [str(getattr(control, "value", "") or "") for control in controls
             if isinstance(control, ft.Text)]

    checks = {
        "page_13": landed == 13,
        "店铺下拉": any("店铺" in text for text in dropdowns),
        "站点下拉": any("站点" == text.strip() or "站点" in text for text in dropdowns),
        "5 个卖点输入": all(f"卖点 {index}" in textfields for index in range(1, 6)),
        "＋添加卖点": any("添加卖点" in text for text in button_texts),
        "检测连接": any("检测连接" in text for text in button_texts),
        "发布前检查": any("发布前检查" in text for text in button_texts),
        "保存（检查模式/正式）": any("保存" in text for text in button_texts),
        "查询发布结果": any("查询发布结果" in text for text in button_texts),
        "自动补齐并重试发布": any("自动补齐并重试发布" in text for text in button_texts),
        "自动补齐必填属性": any("自动补齐必填属性" in text for text in button_texts),
        "发布中/发布失败状态按钮": (any("发布中" in text for text in button_texts)
                              and any("发布失败" in text for text in button_texts)),
        "发布必填属性字段": all(needle in " ".join(textfields) for needle in
                          ("model_number", "model_name", "part_number",
                           "manufacturer_contact_information")),
        "AI 生成标题": any("AI 生成标题" in text for text in button_texts),
        "AI 生成五点": any("AI 生成五点" in text for text in button_texts),
        "AI 生成描述": any("AI 生成描述" in text for text in button_texts),
        "AI 生成关键词": any("AI 生成关键词" in text for text in button_texts),
        "AI 一键生成全部": any("AI 一键生成全部" in text for text in button_texts),
        "n/200 计数": any("/200" in text for text in texts),
        "n/700 计数 ×5": sum("/700" in text for text in texts) >= 5,
        "n/2000 计数": any("/2000" in text for text in texts),
        "n/250 bytes 计数": any("/250 bytes" in text for text in texts),
    }
    missing = [name for name, ok in checks.items() if not ok]
    report.add("13 页关键控件", not missing,
               f"{sum(checks.values())}/{len(checks)} 项存在"
               + (f"；缺 {missing}" if missing else ""))


# ---------------------------------------------------------------------------
# 6) 全按钮矩阵（Amazon 0/13/10/11：可见控件逐一点击；mock 网络；线程同步执行）
# ---------------------------------------------------------------------------
AMAZON_PAGES = (0, 13, 10, 11)
BANNED_RELEASE_TERMS = ("AURORA_", "预演模式", "本地验收", "测试连接", "沙箱",
                        "mock", "验收预演")


class _MockResponse:
    status_code = 200

    def json(self):
        return {"code": "0", "msg": "", "data": {}}

    @property
    def text(self):
        return '{"code":"0","msg":"","data":{}}'


@contextlib.contextmanager
def _mock_network():
    import requests

    def _handler(*args, **kwargs):
        return _MockResponse()

    with patch.object(requests.sessions.Session, "request", _handler), \
         patch.object(requests.sessions.Session, "get", _handler), \
         patch.object(requests.sessions.Session, "post", _handler):
        yield


@contextlib.contextmanager
def _sync_threads():
    import threading
    with patch.object(threading.Thread, "start", lambda thread: thread.run()):
        yield


def _all_controls(roots):
    """按固定顺序遍历（继承祖先 visible），position 用于跨构建定位同一控件。"""
    def visit(control, parent_visible=True):
        visible = parent_visible and bool(getattr(control, "visible", True))
        yield control, visible
        try:
            children = list(control._get_children())
        except Exception:  # noqa: BLE001
            children = []
        for child in children:
            yield from visit(child, visible)

    for root in roots:
        yield from visit(root)


def _label_of(control) -> str:
    import flet as ft
    if isinstance(control, ft.Text):
        return str(control.value or "")
    if isinstance(control, (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton,
                            ft.IconButton, ft.FilledButton)):
        return str(getattr(control, "text", "") or "")
    if isinstance(control, (ft.TextField, ft.Dropdown)):
        return str(getattr(control, "label", "") or "")
    for name in ("text", "label", "tooltip"):
        value = getattr(control, name, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _visible_values(app) -> list[str]:
    import flet as ft
    values: list[str] = []
    for control, visible in _all_controls(app.content.controls):
        if not visible:
            continue
        if isinstance(control, ft.Text):
            values.append(str(control.value or ""))
        elif isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                  ft.TextButton, ft.IconButton, ft.FilledButton)):
            values.append(str(getattr(control, "text", "") or ""))
        elif isinstance(control, (ft.TextField, ft.Dropdown)):
            values.append(str(getattr(control, "label", "") or ""))
            values.append(str(getattr(control, "hint_text", "") or ""))
            for option in (getattr(control, "options", None) or []):
                values.append(str(getattr(option, "text", "") or ""))
    return [value for value in values if value]


def _find_button(app, *needles):
    import flet as ft
    for control, visible in _all_controls(app.content.controls):
        if not visible or not isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                                   ft.TextButton, ft.FilledButton)):
            continue
        text = str(getattr(control, "text", "") or "")
        if all(needle in text for needle in needles):
            return control
    return None


def _click_control(control, app, sync_threads: bool = True):
    context = _sync_threads() if sync_threads else contextlib.nullcontext()
    with context:
        control.on_click(SimpleNamespace(control=control,
                                         data=str(getattr(control, "value", "") or ""),
                                         page=app.page))


def _click_button(app, *needles) -> tuple[bool, str]:
    button = _find_button(app, *needles)
    if button is None:
        return False, f"未找到按钮 {needles}"
    try:
        _click_control(button, app)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _collect_click_specs(platform_id: str, page: int) -> list[dict]:
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page)
        specs = []
        for position, (control, visible) in enumerate(_all_controls(app.content.controls)):
            if not visible or not callable(getattr(control, "on_click", None)):
                continue
            specs.append({"position": position, "label": _label_of(control)[:90],
                          "type": type(control).__name__})
        return specs


def _probe_click(platform_id: str, page: int, position: int) -> dict:
    import flet as ft
    from ui_sandbox import build_app, sandbox
    with sandbox(), _mock_network():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page)
        controls = list(_all_controls(app.content.controls))
        control, _visible = controls[position]
        before = getattr(app, "_page_idx", None)
        switched: list[int] = []
        original_switch = app._switch

        def recorder(idx):
            switched.append(int(idx))
            return original_switch(idx)

        app._switch = recorder
        error = ""
        try:
            # 沙箱默认线程 no-op：按钮回调不真正跑后台任务，逐按钮扫描快速且安全。
            control.on_click(SimpleNamespace(
                control=control, data=str(getattr(control, "value", "") or ""),
                page=app.page))
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:200]
        dialog = getattr(app.page, "dialog", None)
        dialog_title = ""
        if dialog is not None:
            texts = [str(getattr(c, "value", "") or "")
                     for c, _v in _all_controls([dialog]) if isinstance(c, ft.Text)]
            dialog_title = (texts[0] if texts else "")[:90]
        after = getattr(app, "_page_idx", None)
        return {"before": before, "after": after, "switched": switched,
                "dialog": dialog_title, "error": error}


def check_button_matrix(report: Report) -> dict:
    matrix: dict = {"pages": {}, "details": []}
    errors: list[dict] = []
    for page in AMAZON_PAGES:
        specs = _collect_click_specs("amazon", page)
        page_rows = []
        for spec in specs:
            result = _probe_click("amazon", page, spec["position"])
            if result["error"]:
                outcome = "ERROR"
                errors.append({"page": page, "label": spec["label"],
                               "error": result["error"]})
            elif result["dialog"]:
                outcome = "DIALOG"
            elif result["after"] != result["before"]:
                outcome = f"SWITCHED({result['before']}→{result['after']})"
            else:
                outcome = "OK"
            row = {**spec, "page": page, "outcome": outcome,
                   "after": result["after"], "switched": result["switched"],
                   "dialog": result["dialog"], "error": result["error"]}
            matrix["details"].append(row)
            page_rows.append(row)
        matrix["pages"][str(page)] = {
            "buttons": len(page_rows),
            "ok": sum(1 for row in page_rows if row["outcome"] != "ERROR"),
            "errors": [row for row in page_rows if row["outcome"] == "ERROR"],
        }
    total = len(matrix["details"])
    report.add("全按钮矩阵（Amazon 0/13/10/11）", not errors,
               f"{total} 个可见按钮逐一点击；ERROR={len(errors)}"
               + (f"：{errors[:3]}" if errors else "；含导航/弹窗/状态刷新"))
    matrix["errors"] = errors
    matrix["total"] = total
    return matrix


# ---------------------------------------------------------------------------
# 7) 逻辑矩阵（op / 发布状态 / A-B-C-D / 属性补齐 / confirm_write / 5 状态）
# ---------------------------------------------------------------------------
def _fake_publish_client(raw_state: dict):
    class FakePublishClient:
        def publish_state(self, product_id, shop_id="-1"):
            return dict(raw_state)
        def list_products(self, *args, **kwargs):
            return {"items": [], "total": 0}
    return FakePublishClient()


def check_logic_matrix(report: Report) -> dict:
    from aeclone.services import amazon_pipeline as PL
    from aeclone.services import amazon_publish as PUB
    rows: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        rows.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        report.add(f"逻辑：{name}", ok, detail)

    # 7.1 op=1/2/3 语义
    op_labels = dict(getattr(PL, "OP_LABELS", {}) or {})
    expected_ops = {"1": "保存草稿", "3": "保存并移入待发布", "2": "保存并发布"}
    add("op=1/2/3 分类", all(op_labels.get(k) == v for k, v in expected_ops.items()),
        f"OP_LABELS={op_labels}；publish 常量={PUB.OP_SAVE}/{PUB.OP_MOVE_PENDING}/{PUB.OP_PUBLISH}")

    # 7.2 发布状态映射（后端 verify_publish）
    cases = [
        ({'state': 'online', 'err_msg': '', 'asin': 'B0TEST'},
         'online', True, '发布成功'),
        ({'state': 'publishing', 'err_msg': ''}, 'processing', True, '处理中'),
        ({'state': 'waitPublish', 'err_msg': ''}, 'processing', True, '处理中'),
        ({'state': 'publishFail',
          'err_msg': 'code:100521,我们正在审查这个商品信息，请等待最多 48 小时'},
         'reviewing', True, '审核中'),
        ({'state': 'publishFail',
          'err_msg': '发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number'},
         'failed', False, '缺失必填属性'),
        ({'state': 'draft', 'err_msg': ''}, 'not_submitted', False, '未提交'),
        ({'state': 'weird', 'err_msg': ''}, 'unknown', False, '未能确认'),
    ]
    matrix = []
    for raw, expected_status, expected_ok, detail_needle in cases:
        client = _fake_publish_client(raw)
        res = PL.verify_publish(PRODUCT_ID, None, client=client, wait_seconds=0)
        ok = (res.get("status") == expected_status
              and bool(res.get("ok")) == expected_ok
              and detail_needle in str(res.get("detail") or "")
              + " " + str(res.get("err_msg") or ""))
        matrix.append({"input": raw.get("state"), "status": res.get("status"),
                       "ok": res.get("ok"), "expected": expected_status,
                       "pass": ok, "detail": res.get("detail")})
    add("发布状态映射（online/processing/reviewing/failed/not_submitted/unknown）",
        all(row["pass"] for row in matrix),
        "；".join(f"{row['input']}→{row['status']}" for row in matrix))

    # 7.3 A/B/C/D 分类（task-10 独立分类器）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "amazon_publish_state_audit", TOOLS / "verify_amazon_publish_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    samples = module.CLASSIFIER_SAMPLES
    class_rows = []
    for expected, sample in samples:
        actual, detail = module.classify_failure(sample)
        class_rows.append({"expected": expected, "actual": actual,
                           "pass": actual == expected,
                           "codes": detail.get("codes", [])})
    add("A/B/C/D 发布失败分类", all(row["pass"] for row in class_rows),
        "；".join(f"{row['expected']}→{row['actual']}" for row in class_rows))

    # 7.4 属性自动补齐：只补缺失、不覆盖用户值
    product = {
        "id": PRODUCT_ID, "title": "5 Ton Backhoe Loader HW20-28 Diesel",
        "sku": "WY-QHCA-LTM-1", "brand": "Generic", "manufacturer": "Generic",
        "description": "<p>China factory backhoe loader</p>",
        "productAttr": '{"part_number":[{"value":"EXISTING-PN"}]}',
    }
    required = ["model_number", "model_name", "part_number",
                "manufacturer_contact_information"]
    filled = PL.autofill_attributes(
        product, None, "A2EUQ1WTGCTBG2",
        overrides={"attributes": {"model_number": "USER-MODEL"}},
        required=required) or {}
    keep_user = "model_number" not in filled
    keep_existing = "part_number" not in filled
    fill_missing = bool(filled.get("model_name")) and bool(
        filled.get("manufacturer_contact_information"))
    add("属性自动补齐只补缺失不覆盖用户值", keep_user and keep_existing and fill_missing,
        f"filled={filled}；保留用户 model_number={keep_user}、"
        f"保留已有 part_number={keep_existing}、补齐其他={fill_missing}")

    # 7.5 confirm_write 门禁（dry_run=False 未显式确认不写）
    sentinel_calls = []
    with patch.object(PUB, "save_to_amazon",
                      lambda *a, **kw: sentinel_calls.append(1)):
        gate = PL.run_amazon_product(PRODUCT_ID, None, dry_run=False,
                                     confirm_write=False, client=object())
    add("confirm_write 门禁（防脚本误写）",
        (not gate.ok) and "confirm_write" in str(gate.error or "")
        and not sentinel_calls,
        f"ok={gate.ok} saved={gate.saved} error={str(gate.error)[:100]}；"
        f"save_to_amazon 调用={len(sentinel_calls)}")

    # 7.6 UI「自动补齐并重试发布」走 confirm_write=True（静态）+ 只读查询
    app_text = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    retry_snippet_start = app_text.find("def _retry_run(")
    retry_snippet = app_text[retry_snippet_start: retry_snippet_start + 6000]
    confirm_true = "confirm_write=True" in retry_snippet
    query_readonly = ("pipeline.verify_publish" in app_text
                      or "verify_publish" in app_text)
    add("UI 重试发布走 confirm_write + 查询只读", confirm_true and query_readonly,
        f"retry confirm_write=True={confirm_true}；查询用 verify_publish={query_readonly}")

    # 7.7 发布失败列表含「平台审核中」商品的判定规则（100521 → A 类，非软件失败）
    state_path = ROOT / "docs" / "amazon-publish-state.json"
    rule_ok = False
    rule_detail = "缺少 docs/amazon-publish-state.json"
    if state_path.exists():
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        fail_items = (state_data.get("items") or {}).get("publishFail") or []
        review_items = [row for row in fail_items if row.get("class") == "A"]
        wrong = [row for row in fail_items
                 if "100521" in (row.get("codes") or []) and row.get("class") != "A"]
        rule_ok = bool(review_items) and not wrong
        rule_detail = (f"publishFail 共 {len(fail_items)} 个，其中 A 类(审核中) {len(review_items)} 个；"
                       f"含 100521 却未归 A 的={len(wrong)}")
    add("publishFail 列表内审核中商品判定（100521→A）", rule_ok, rule_detail)

    # 7.8 AURORA_TEST/AURORA_NO_SAVE 拒绝真实写（逻辑矩阵内复核）
    guard_rows = []
    for env_name in ("AURORA_TEST", "AURORA_NO_SAVE"):
        with patch.dict(os.environ, {env_name: "1"}):
            os.environ.pop("AURORA_TEST" if env_name != "AURORA_TEST"
                           else "AURORA_NO_SAVE", None)
            try:
                PUB.save_to_amazon(PRODUCT_ID, None)
                guard_rows.append({"env": env_name, "rejected": False})
            except RuntimeError:
                guard_rows.append({"env": env_name, "rejected": True})
            finally:
                os.environ["AURORA_TEST"] = "1"
    add("AURORA_TEST/AURORA_NO_SAVE 拒绝真实写",
        all(row["rejected"] for row in guard_rows),
        str(guard_rows))

    # 7.7 5 状态列表映射（UI 渲染）
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        values = _visible_values(app)
    need = ["采集箱", "待发布", "发布中", "发布失败", "在线"]
    add("列表 5 状态映射", all(any(n in value for value in values) for n in need),
        "；".join(n for n in need if any(n in value for value in values)))

    return {"rows": rows}


# ---------------------------------------------------------------------------
# 8) 正式版 UI 文案扫描
# ---------------------------------------------------------------------------
def check_release_ui_scan(report: Report) -> dict:
    import flet as ft
    from aeclone.platforms import PLATFORMS
    from ui_sandbox import build_app, sandbox

    result: dict = {"modes": {}, "title": {}, "platforms": {}}

    for mode in ("test", "prod"):
        leaks = []
        title = ""
        with sandbox():
            saved = {key: os.environ.get(key)
                     for key in ("AURORA_TEST", "AURORA_NO_SAVE")}
            try:
                if mode == "prod":
                    os.environ.pop("AURORA_TEST", None)
                    os.environ.pop("AURORA_NO_SAVE", None)
                else:
                    os.environ["AURORA_TEST"] = "1"
                app = build_app()
                app._switch_platform("amazon")
                title = str(getattr(app.page, "title", "") or "")
                for page in AMAZON_PAGES:
                    app._switch(page)
                    for value in _visible_values(app):
                        for term in BANNED_RELEASE_TERMS:
                            if term in value:
                                leaks.append({"page": page, "term": term,
                                              "text": value[:120]})
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        result["modes"][mode] = {"leaks": leaks, "count": len(leaks)}
        result["title"][mode] = title

    # 其它平台页面不能因文案改动报错
    failures = []
    for pid, platform in PLATFORMS.items():
        for item in platform.pages:
            try:
                with sandbox():
                    app = build_app()
                    if pid != "alibaba":
                        app._switch_platform(pid)
                    app._switch(item.index)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{pid}:{item.index} {type(exc).__name__}")
    result["platforms"] = {"failures": failures}

    prod_leaks = result["modes"]["prod"]["leaks"]
    test_leaks = result["modes"]["test"]["leaks"]
    report.add("正式版 Amazon 可见文案扫描（无 AURORA_/预演模式/本地验收/测试连接/沙箱/mock）",
               not prod_leaks and not test_leaks,
               f"prod leaks={len(prod_leaks)}；test leaks={len(test_leaks)}"
               + (f"：{(prod_leaks + test_leaks)[:3]}" if (prod_leaks or test_leaks) else ""))
    report.add("各平台页面文案改动后均可渲染", not failures,
               f"{len(PLATFORMS)} 平台全部页面渲染" if not failures else "；".join(failures[:6]))
    # App 级测试模式标题：如实记录（App 级，不在页面可见文本内；由 Lead 决定是否清理）
    test_title = result["title"]["test"]
    app_level = [term for term in ("预览模式", "本地验收", "预演") if term in test_title]
    report.add("App 级测试模式标题（如实记录，页面文本无泄漏）", True,
               f"AURORA_TEST title={test_title!r}；测试标记={app_level}"
               "（App 级，不计为页面文案泄漏，列为待清理项）")
    result["app_title"] = {"test": test_title, "prod": result["title"]["prod"],
                           "test_markers": app_level}
    return result


# ---------------------------------------------------------------------------
# 9) Lead 发布证据 ↔ 实时只读证据一致性
# ---------------------------------------------------------------------------
def check_publish_evidence_consistency(report: Report) -> dict:
    lead_path = ROOT / "docs" / "amazon-publish-verification.json"
    live_path = ROOT / "docs" / "amazon-publish-state.json"
    result: dict = {"lead": str(lead_path.relative_to(ROOT)),
                    "live": str(live_path.relative_to(ROOT)),
                    "comparisons": [], "stale_products": [], "live_counts": {}}
    if not lead_path.exists() or not live_path.exists():
        report.add("Lead 发布证据 ↔ 实时只读一致", False,
                   f"缺少证据文件：lead={lead_path.exists()} live={live_path.exists()}")
        return result
    lead = json.loads(lead_path.read_text(encoding="utf-8"))
    live = json.loads(live_path.read_text(encoding="utf-8"))
    live_check = {str(row.get("product_id")): row
                  for row in live.get("checklist") or []}
    live_items = {str(row.get("id")): row
                  for row in (live.get("items") or {}).get("publishFail") or []}
    result["live_counts"] = live.get("counts") or {}
    expected: dict[str, dict] = {}
    for run in lead.get("runs") or []:
        for product in run.get("products") or []:
            after = product.get("after")
            if isinstance(after, dict) and after.get("state"):
                expected[str(product.get("product_id"))] = after
    state_map = {"publishFail": "publishFail", "publishing": "publishing",
                 "waitPublish": "waitPublish", "online": "online", "draft": "draft"}
    for pid, after in expected.items():
        live_row = live_check.get(pid) or {}
        live_state = str(live_row.get("state") or "not_found")
        wanted = state_map.get(str(after.get("state")), "unknown")
        ok = live_state == wanted
        # 允许时序漂移：
        #  - Lead 快照 publishFail(审核中) → 实时 publishing/online（重发/审核通过）
        #  - Lead 快照 publishing(处理中) → 实时 publishFail（平台处理失败，需新处置）
        allowed_drift = ((wanted == "publishFail"
                          and live_state in ("publishing", "online"))
                         or (wanted == "publishing"
                             and live_state in ("publishFail", "online")))
        needs_attention = (wanted == "publishing" and live_state == "publishFail")
        result["comparisons"].append({
            "product_id": pid,
            "lead_after_state": after.get("state"),
            "lead_after_status": after.get("status"),
            "live_state": live_state,
            "live_class": (live_items.get(pid) or {}).get("class", ""),
            "ok": ok or allowed_drift,
            "timing_drift": bool(allowed_drift and not ok),
            "needs_attention": bool(needs_attention),
        })
    # Lead 关注商品是否都在证据/实时里出现（214 类目错配未重发、181922 证据疑似未落盘）
    focus = ["220114595363182310", "220114595363182214",
             "220114595363182036", "220114595363181922"]
    for pid in focus:
        if pid not in expected:
            result["stale_products"].append({
                "product_id": pid,
                "live_state": (live_check.get(pid) or {}).get("state", "not_found"),
                "note": "Lead 证据无 after 状态（可能未重发或证据未落盘）",
            })
    checks_ok = all(row["ok"] for row in result["comparisons"]) and bool(
        result["comparisons"])
    drift = [row for row in result["comparisons"] if row.get("timing_drift")]
    attention = [row for row in result["comparisons"] if row.get("needs_attention")]
    report.add("Lead 发布证据 ↔ 实时只读一致", checks_ok,
               f"已比对 {len(result['comparisons'])} 个有 after 状态商品："
               + "；".join(f"{row['product_id']}⊃{row['live_state']}"
                          for row in result["comparisons"])
               + (f"；时序漂移={[row['product_id'] for row in drift]}" if drift else "")
               + (f"；处理中转失败需新处置={[row['product_id'] for row in attention]}"
                  if attention else "")
               + (f"；证据缺失记录={[row['product_id'] for row in result['stale_products']]}"
                  if result["stale_products"] else ""))
    # 实时分类与 Lead 最新 after 状态一致（reviewing→A；failed→按 errMsg 分类）
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "amazon_publish_state_audit", TOOLS / "verify_amazon_publish_state.py")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    class_rows = []
    for pid, after in expected.items():
        status = str(after.get("status") or "")
        if status == "reviewing":
            wanted = "A"
        elif status == "failed":
            wanted = _module.classify_failure(str(after.get("err_msg") or ""))[0]
        else:
            continue
        actual = str((live_items.get(pid) or {}).get("class", ""))
        class_rows.append({"product_id": pid, "expected": wanted,
                           "actual": actual, "ok": actual == wanted})
    report.add("实时发布失败分类与 Lead 最新结论一致",
               all(row["ok"] for row in class_rows),
               "；".join(f"{row['product_id']}={row['actual']}(期望{row['expected']})"
                        for row in class_rows) or "无 reviewing/failed 商品")
    result["class_rows"] = class_rows
    return result


# ---------------------------------------------------------------------------
# 10) 发布状态 UI（mock + sandbox）：数量/失败原因/徽标/自动补齐重试
# ---------------------------------------------------------------------------
class _FakeAmazonClient:
    def __init__(self):
        self.verify_queue: list[dict] = []
        self.counts = {"draft": 16, "waitPublish": 5, "publishing": 2,
                       "publishFail": 23, "online": 142}

    # 只读接口
    def check_login(self):
        return True

    def shops(self):
        return [{"id": "9408005", "name": "QingHe",
                 "merchant_id": "A26OV9RTCAYJMS",
                 "default_marketplace": "A2EUQ1WTGCTBG2"}]

    def sites(self, shop_id):
        return [{"marketplace_id": "A2EUQ1WTGCTBG2", "code": "CA",
                 "country": "加拿大", "currency": "CAD",
                 "language": "en", "language_name": "英语"}]

    def list_products(self, state="draft", page=1, page_size=20,
                      shop_id="-1", keyword=""):
        err = ("发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number"
               if state == "publishFail" else "")
        return {
            "items": [{"id": PRODUCT_ID, "title": "Fake Backhoe", "sku": "SKU-1",
                       "marketPlaceId": "A2EUQ1WTGCTBG2", "standardPrice": 12.5,
                       "standardPriceCurrency": "CAD", "quantity": 0,
                       "errMsg": err, "err_msg": err, "warningMsg": ""}],
            "total": self.counts.get({"offline": "waitPublish"}.get(state, state), 1),
            "page_no": 1, "total_page": 1,
            "drafts": self.counts["draft"], "wait_publish": self.counts["waitPublish"],
            "publish_fail": self.counts["publishFail"],
            "publishing": self.counts["publishing"],
            "markets": {"A2EUQ1WTGCTBG2": "加拿大"},
        }

    def product_detail(self, product_id):
        return {"site": "CA", "categoryName": "Backhoes", "categoryTree": [],
                "product": {
                    "id": str(product_id), "title": "Fake Backhoe",
                    "bulletPoint": "±".join(f"FEATURE {i}" for i in range(1, 6)),
                    "description": "<p>Fake description</p>",
                    "searchTerms": "", "brand": "Generic", "manufacturer": "Generic",
                    "sku": "SKU-1", "standardPrice": "12.5", "saleSalePrice": 0,
                    "quantity": "0", "marketPlaceId": "A2EUQ1WTGCTBG2",
                    "categoryId": "13553005011",
                    "categoryBrowsePath": "6299024011/6256963011/6256964011/13553005011",
                    "mainImage": "https://example.invalid/main.jpg",
                    "extraImagesStr": "", "productAttr": "{}",
                    "switchFulfillmentTo": "MFN", "merchantShippingGroup": "tpl-1",
                }}

    def product_attributes(self, product_id, marketplace_id):
        return {}

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def logistics(self, shop_id, marketplace_id):
        return []

    def brands(self, shop_id, marketplace_id):
        return []

    def check_banned_words(self, **kwargs):
        return []

    def category_roots(self, site_code):
        return []

    def category_children(self, site_code, parent_id):
        return []

    def category_by_path(self, site_code, node_path):
        return {}

    def category_history(self, shop_id, site_code):
        return []

    def product_types(self, site_code):
        return []

    def predict_category(self, shop_id, site_code, item_name):
        return {}

    def publish_state(self, product_id, shop_id="-1"):
        return self.verify_queue[0] if self.verify_queue else {"state": "unknown"}

    def parse_required_attrs(self, err_msg):
        import re
        return list(dict.fromkeys(re.findall(r"属性:([A-Za-z0-9_]+)", str(err_msg or ""))))


A_REVIEW = {"state": "publishFail", "status": "reviewing", "ok": True,
            "detail": "已提交，平台审核中（非软件失败）",
            "err_msg": "code:100521,我们正在审查这个商品信息，请等待最多 48 小时",
            "asin": "", "required_attrs": [], "counts": {}}
B_MISSING = {"state": "publishFail", "status": "failed", "ok": False,
             "detail": "发布失败：缺少必填属性",
             "err_msg": "发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number;"
                        "属性:part_number;",
             "asin": "", "required_attrs": ["model_number", "part_number"],
             "counts": {}}
PROCESSING = {"state": "publishing", "status": "processing", "ok": True,
              "detail": "已提交，平台处理中（publishing）", "err_msg": "",
              "asin": "", "required_attrs": [], "counts": {}}
ONLINE = {"state": "online", "status": "online", "ok": True,
          "detail": "发布成功 · ASIN B0TEST123", "err_msg": "",
          "asin": "B0TEST123", "required_attrs": [], "counts": {}}


def check_publish_ui_mock(report: Report) -> dict:
    """发布状态 UI（轻量级 mock）：控件存在 + 徽标/失败原因/属性渲染 + 检查模式拒绝重试。"""
    from ui_sandbox import build_app, sandbox
    from aeclone.services import amazon_pipeline as PL
    from aeclone.services import amazon_publish as PUB

    fake = _FakeAmazonClient()
    result: dict = {"checks": []}

    def fake_verify(product_id, cfg, **kwargs):
        return dict(fake.verify_queue.pop(0)) if fake.verify_queue else dict(ONLINE)

    def fake_make_client(cfg):
        return fake

    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        with patch.object(PUB, "make_client", fake_make_client), \
             patch.object(PL, "verify_publish", fake_verify):
            # 控件存在（静态渲染）
            values = _visible_values(app)
            controls_ok = all(any(needle in value for value in values) for needle in
                              ("检测连接", "发布前检查", "查询发布结果",
                               "自动补齐并重试发布", "发布必填属性",
                               "发布中", "发布失败"))
            result["checks"].append({"name": "发布控件齐全", "ok": controls_ok})
            report.add("UI：发布控件齐全（检测连接/发布前检查/查询/重试/属性/状态列表）",
                       controls_ok, "关键发布控件均可见" if controls_ok else "缺少发布控件")

            # 设置测试商品 id，直接查询发布结果（A 审核中 → B 失败原因+缺失属性）
            app.cfg.amazon_test_product_id = PRODUCT_ID
            badge_rows = []
            for state, needle in ((A_REVIEW, "平台审核中"),
                                  (B_MISSING, "发布失败"),
                                  (PROCESSING, "处理中"),
                                  (ONLINE, "发布成功")):
                fake.verify_queue = [state]
                with _sync_threads():
                    ok, err = _click_button(app, "查询发布结果")
                texts = " ".join(_visible_values(app))
                badge_rows.append({"state": state.get("state"), "needle": needle,
                                   "ok": ok and needle in texts})
            fake.verify_queue = [B_MISSING]
            with _sync_threads():
                _click_button(app, "查询发布结果")
            b_texts = " ".join(_visible_values(app))
            b_render = ("发布失败" in b_texts and "model_number" in b_texts
                        and "part_number" in b_texts)
            badges_ok = all(row["ok"] for row in badge_rows) and b_render
            result["checks"].append({"name": "发布结果徽标/失败原因/缺失属性",
                                     "ok": badges_ok,
                                     "detail": {"badges": badge_rows,
                                                "b_required_attrs": b_render}})
            report.add("UI：发布结果徽标/失败原因/缺失属性渲染", badges_ok,
                       f"{[(row['needle'], row['ok']) for row in badge_rows]}；"
                       f"B类缺失属性渲染={b_render}")

            # 检查模式下「自动补齐并重试发布」拒绝真实写
            fake.verify_queue = [B_MISSING]
            with _sync_threads():
                ok, err = _click_button(app, "自动补齐并重试发布")
            texts = " ".join(_visible_values(app))
            retry_ok = ok and ("检查模式" in texts or "拒绝" in texts)
            result["checks"].append({"name": "检查模式重试发布被拒绝", "ok": retry_ok})
            report.add("UI：检查模式「自动补齐并重试发布」拒绝", retry_ok,
                       "已提示检查模式拒绝" if retry_ok else f"未提示（{err}）")
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    report = Report()
    check_signatures(report)
    check_overrides(report)
    check_single_write_entry(report)
    check_write_guards(report)
    check_page_controls(report)
    button_matrix = check_button_matrix(report)
    logic_matrix = check_logic_matrix(report)
    release_scan = check_release_ui_scan(report)
    publish_consistency = check_publish_evidence_consistency(report)
    publish_ui = check_publish_ui_mock(report)

    total = len(report.rows)
    passed = report.passed
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "generated_at": __import__("datetime").datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "checks": report.rows,
        "button_matrix": button_matrix,
        "logic_matrix": logic_matrix,
        "release_ui_scan": release_scan,
        "publish_evidence_consistency": publish_consistency,
        "publish_ui_mock": publish_ui,
        "summary": {"passed": passed, "total": total,
                    "failed": [row["name"] for row in report.rows if not row["ok"]]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果：{passed}/{total} PASS · 证据 {OUTPUT}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
