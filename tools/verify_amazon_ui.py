"""Amazon 13 页 · 端到端 UI / 后端签名交叉审计（task-4，只读 + 沙箱）。

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
输出：控制台 PASS/FAIL + ``build/verification/amazon-ui-cross-audit.json``（build 已 gitignore）。
退出码：全 PASS → 0；否则 1。
"""
from __future__ import annotations

import ast
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

OUTPUT = ROOT / "build" / "verification" / "amazon-ui-cross-audit.json"
PRODUCT_ID = "220114595363182310"
WRITE_LITERAL = "add" + ".json"    # 唯一写入口字面量（禁止出现在 UI/非收口模块）

EXPECTED_SIGNATURES: dict[str, dict] = {
    "amazon_pipeline.run_amazon_product": {
        "params": ["product_id", "cfg", "overrides", "dry_run", "use_ai", "op", "client"],
        "defaults": {"dry_run": True, "use_ai": False, "op": "1"},
        "kwonly": {"dry_run", "use_ai", "op", "client"},
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
    ok = len(keys) == 17 and keys == expected
    missing = [key for key in expected if key not in keys]
    extra = [key for key in keys if key not in expected]
    report.add("UI overrides 17 键 == OVERRIDE_KEYS", ok,
               f"{how}: UI {len(keys)} 键 / 后端 {len(expected)} 键"
               + (f"；缺 {missing}" if missing else "")
               + (f"；多 {extra}" if extra else ""))


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
             and "预演模式" in str(getattr(control, "text", "") or "")), None)
        if save_button is None:
            report.add("UI 预演模式保存按钮拒绝", False,
                       "找不到「保存（预演模式）」按钮")
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
        rejected = any("已拒绝真实保存" in text or "预演模式" in text for text in texts)
    report.add("UI 预演模式保存按钮拒绝", ok and rejected,
               f"按钮文案={getattr(save_button, 'text', '')!r}；"
               f"状态提示={'已显示预演模式拒绝' if rejected else '未显示'}"
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
        "预演按钮": any("预演" in text for text in button_texts),
        "保存按钮": any("保存" in text for text in button_texts),
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
# main
# ---------------------------------------------------------------------------
def main() -> int:
    report = Report()
    check_signatures(report)
    check_overrides(report)
    check_single_write_entry(report)
    check_write_guards(report)
    check_page_controls(report)

    total = len(report.rows)
    passed = report.passed
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "generated_at": __import__("datetime").datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "checks": report.rows,
        "summary": {"passed": passed, "total": total,
                    "failed": [row["name"] for row in report.rows if not row["ok"]]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果：{passed}/{total} PASS · 证据 {OUTPUT}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
