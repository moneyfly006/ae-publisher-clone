#!/usr/bin/env python3
"""全界面按钮/开关/冲突体检（静态 + 配置矩阵 + 运行时按钮审计交叉）。

输出 docs/control-audit.json / docs/CONTROL_AUDIT.md：
1. 按钮：AST 扫描 app.py 的按钮构造函数，标记「无 on_click / 空回调 / 只有 toast / disabled 默认 True」；
2. 开关：遍历 DEFAULTS 的 bool，统计 app.py 与 services/integrations 的读取点，标记「只写不读」；
3. 冲突：对真实配置与关键布尔组合跑 services.conflicts.check，列出 error/warn；
4. 交叉：读 docs/ui-button-audit.json 统计每页回调/ERROR 与重复文案。

用法：.venv/bin/python tools/audit_controls.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "aeclone" / "ui" / "app.py"
OUT_JSON = ROOT / "docs" / "control-audit.json"
OUT_MD = ROOT / "docs" / "CONTROL_AUDIT.md"

BTN_CTORS = {"ElevatedButton", "OutlinedButton", "TextButton", "IconButton", "FilledButton"}
TOAST_ONLY = re.compile(r"\b(_toast|toast)\s*\(|MYJ\.message|#\s*(todo|暂未实现|未实现)", re.I)


def _const_str(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return ""


def scan_buttons(path=APP) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    # 后置接线：x.on_click = fn / self.x.on_click = fn
    later = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Attribute) and tgt.attr in ("on_click", "on_change", "on_submit"):
                base = tgt.value
                name = ""
                if isinstance(base, ast.Name):
                    name = base.id
                elif isinstance(base, ast.Attribute):
                    name = base.attr
                if name:
                    later.setdefault(name, []).append(getattr(node, "lineno", 0))
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name not in BTN_CTORS:
            continue
        label = ""
        for arg in node.args:
            label = _const_str(arg) or label
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        assign_to = ""
        if not label:
            label = _const_str(kwargs.get("text", ast.Constant(""))) or ""
        on_click = kwargs.get("on_click")
        disabled = kwargs.get("disabled")
        disabled_literal = isinstance(disabled, ast.Constant) and disabled.value is True
        handler = ""
        if isinstance(on_click, ast.Name):
            handler = on_click.id
        elif isinstance(on_click, ast.Attribute):
            handler = on_click.attr
        elif isinstance(on_click, ast.Lambda):
            handler = "<lambda>"
        trivial = False
        if isinstance(on_click, ast.Lambda):
            body = on_click.body
            src = ast.unparse(body) if hasattr(ast, "unparse") else ""
            trivial = bool(TOAST_ONLY.search(src)) or src.strip() in ("pass", "None")
        rows.append({
            "line": getattr(node, "lineno", 0),
            "type": name,
            "label": label[:80],
            "has_on_click": on_click is not None,
            "handler": handler,
            "disabled_default": disabled_literal,
            "toast_only_lambda": trivial,
        })
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            var = ""
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                var = node.targets[0].id
            elif (len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute)):
                var = node.targets[0].attr
            if var:
                assigned[getattr(node.value, "lineno", 0)] = var
    for r in rows:
        r["assigned_to"] = assigned.get(r["line"], "")
        names = [r["assigned_to"]] + ([r["handler"]] if r["handler"] else [])
        r["deferred_wired"] = any(n and n in later for n in names)
    no_handler = [r for r in rows if not r["has_on_click"] and not r["deferred_wired"]]
    for row in rows:
        row["file"] = str(path.relative_to(ROOT))
    return {"total": len(rows), "no_on_click": no_handler,
            "disabled_default": [r for r in rows if r["disabled_default"]],
            "toast_only": [r for r in rows if r["toast_only_lambda"]],
            "strings": rows}



def scan_all_buttons():
    sources = [scan_buttons(path) for path in sorted((ROOT / "src/aeclone/ui").glob("*.py"))]
    return {"total": sum(source["total"] for source in sources),
            **{key: [row for source in sources for row in source[key]]
               for key in ("no_on_click", "disabled_default", "toast_only", "strings")}}


def scan_switches() -> dict:
    from aeclone.config import DEFAULTS
    app_text = APP.read_text(encoding="utf-8")
    other_text = {}
    for p in (ROOT / "src" / "aeclone").rglob("*.py"):
        if p.name == "app.py":
            continue
        other_text[str(p)] = p.read_text(errors="ignore")
    bools = sorted(k for k, v in DEFAULTS.items() if isinstance(v, bool))
    rows = []
    for key in bools:
        pat = re.compile(r"\b" + re.escape(key) + r"\b")
        in_ui = bool(pat.search(app_text))
        readers = [os.path.relpath(p, ROOT) for p, t in other_text.items() if pat.search(t)]
        if "_collect_" in key:
            suffix = key.split("_collect_", 1)[1]
            dynamic = [os.path.relpath(p, ROOT) for p, t in other_text.items()
                       if "_collect_" in t and ('"' + suffix + '"' in t or "_collect_" + suffix in t)]
            readers = sorted(set(readers + dynamic))
            in_ui = in_ui or bool(dynamic)
        rows.append({"key": key, "ui_reference": in_ui,
                     "readers": readers[:6], "reader_count": len(readers),
                     "write_only": in_ui and not readers})
    return {"total": len(rows), "write_only": [r for r in rows if r["write_only"]],
            "unused_anywhere": [r for r in rows if not r["ui_reference"] and not r["readers"]],
            "rows": rows}


def scan_conflicts() -> dict:
    from aeclone.config import get_config
    from aeclone.services import conflicts
    try:
        cfg = get_config()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"读取配置失败：{exc}"}
    items = conflicts.check(cfg)
    return {
        "baseline": [{"level": c.level, "keys": c.keys,
                      "title": c.title, "detail": c.detail, "fix": c.fix}
                     for c in items],
        "blocking": len([c for c in items if c.level == "error"]),
    }


def scan_disabled_runtime() -> dict:
    """沙箱逐平台逐页收集 disabled 按钮/开关（用户点不动的控件）。"""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import flet as ft  # noqa: F401
        from ui_sandbox import build_app, sandbox, walk
        from aeclone.platforms import get_platform
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = {}
    try:
        with sandbox():
            app = build_app()
            for pid in ("alibaba", "aliexpress", "amazon", "tiktok", "shopee"):
                app._switch_platform(pid)
                pf = get_platform(pid)
                rows = []
                for page in pf.pages:
                    try:
                        app._switch(page.index)
                    except Exception:
                        continue
                    for ctrl in walk(app.content.controls):
                        if isinstance(ctrl, (ft.ElevatedButton, ft.OutlinedButton,
                                             ft.TextButton, ft.IconButton, ft.Switch,
                                             ft.Checkbox)) and bool(getattr(ctrl, "disabled", False)):
                            label = ""
                            for attr in ("text", "label"):
                                v = getattr(ctrl, attr, "")
                                if isinstance(v, str) and v:
                                    label = v
                                    break
                            if not label and getattr(ctrl, "content", None) is not None:
                                texts = [c.value for c in walk([ctrl.content])
                                         if isinstance(c, ft.Text) and c.value]
                                label = " / ".join(texts)
                            rows.append({"page": page.index, "page_label": page.label,
                                         "type": type(ctrl).__name__,
                                         "label": label[:80]})
                out[pid] = rows
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    return out


def conflict_matrix() -> dict:
    """逐个布尔开关翻转，观察是否引入新配置冲突（互斥/依赖）。"""
    from aeclone.config import DEFAULTS, get_config
    from aeclone.services import conflicts
    base = get_config()
    def keys(cfg):
        return {(c.level, tuple(c.keys), c.title) for c in conflicts.check(cfg)}
    try:
        base_keys = keys(base)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}
    new_conflicts = []
    from aeclone.config import AppConfig
    from copy import deepcopy
    for key, default in sorted(DEFAULTS.items()):
        if not isinstance(default, bool):
            continue
        cfg = AppConfig(deepcopy(base.as_dict()))
        flip = not bool(getattr(base, key, default))
        cfg.set(key, flip)
        added = sorted(keys(cfg) - base_keys)
        if added:
            new_conflicts.append({"key": key, "set_to": flip,
                                  "new": [{"level": l, "key": k, "title": t} for l, k, t in added]})
    return {"toggled_keys": sum(1 for v in DEFAULTS.values() if isinstance(v, bool)),
            "new_conflicts": new_conflicts}


def cross_button_audit() -> dict:
    p = ROOT / "docs" / "ui-button-audit.json"
    if not p.exists():
        return {"missing": True}
    data = json.loads(p.read_text())
    rows = data.get("results") or data if isinstance(data, list) else data.get("results", [])
    status = Counter(r.get("status") for r in rows)
    per_page = defaultdict(Counter)
    dup = defaultdict(int)
    for r in rows:
        per_page[f"{r.get('platform')}:{r.get('page')}"][r.get("status")] += 1
        dup[(r.get("platform"), r.get("page"), r.get("label"))] += 1
    duplicates = [{"platform": k[0], "page": k[1], "label": k[2], "count": v}
                  for k, v in dup.items() if v > 1 and k[2]]
    return {"total": len(rows), "status": dict(status),
            "per_page": {k: dict(v) for k, v in per_page.items()},
            "duplicate_labels": sorted(duplicates, key=lambda x: -x["count"])[:40]}


def main() -> int:
    from aeclone.config import DEFAULTS
    result = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "buttons": scan_all_buttons(),
        "switches": scan_switches(),
        "conflicts": scan_conflicts(),
        "runtime_button_audit": cross_button_audit(),
        "disabled_runtime": scan_disabled_runtime(),
        "conflict_matrix": conflict_matrix(),
        "defaults_bool_count": sum(1 for v in DEFAULTS.values() if isinstance(v, bool)),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    b = result["buttons"]
    s = result["switches"]
    lines = [
        "# 全界面按钮 / 开关 / 冲突体检报告",
        "",
        f"生成时间：{result['generated_at']}",
        "",
        "## 1. 按钮（AST 静态扫描）",
        "",
        f"- 按钮构造总数：**{b['total']}**",
        f"- 无 on_click：**{len(b['no_on_click'])}**",
        f"- 默认 disabled=True：**{len(b['disabled_default'])}**",
        f"- 回调只有 toast/占位：**{len(b['toast_only'])}**",
        "",
    ]
    for title, rows in (("无 on_click", b["no_on_click"]),
                        ("默认 disabled", b["disabled_default"]),
                        ("回调仅 toast/占位", b["toast_only"])):
        if not rows:
            continue
        lines += [f"### {title}", ""]
        for r in rows[:60]:
            lines.append(f"- L{r['line']} `{r['type']}` **{r['label'] or '(无文本)'}** "
                         f"handler={r['handler'] or '-'}")
        lines.append("")
    lines += ["## 2. 开关（配置 bool 的读取点）", "",
              f"- bool 配置总数：**{s['total']}**",
              f"- 只写不读（仅 UI 引用）：**{len(s['write_only'])}**",
              f"- 全仓未引用：**{len(s['unused_anywhere'])}**", ""]
    if s["write_only"]:
        lines += ["### 只写不读", ""]
        for r in s["write_only"]:
            lines.append(f"- `{r['key']}`（UI 有引用，services 无读取）")
        lines.append("")
    if s["unused_anywhere"]:
        lines += ["### 全仓未引用", ""]
        for r in s["unused_anywhere"]:
            lines.append(f"- `{r['key']}`")
        lines.append("")
    c = result["conflicts"]
    lines += ["## 3. 配置冲突", ""]
    if "error" in c:
        lines += [f"- {c['error']}"]
    else:
        lines += [f"- 当前真实配置冲突：**{len(c['baseline'])}**（blocking={c['blocking']}）"]
        for it in c["baseline"]:
            lines.append(f"- [{it['level']}] {it['title']}：{it['detail']} → {it['fix']}")
    dm = result.get("conflict_matrix") or {}
    lines += ["", "## 3b. 开关翻转冲突矩阵", "",
              f"- 翻转布尔键：{dm.get('toggled_keys', '?')}；新增冲突组：**{len(dm.get('new_conflicts') or [])}**"]
    for row in (dm.get("new_conflicts") or [])[:20]:
        lines.append(f"- `{row['key']}`={row['set_to']} → " +
                     "；".join(f"{x['level']}:{x['title']}" for x in row["new"]))
    dis = result.get("disabled_runtime") or {}
    lines += ["", "## 3c. 运行时禁用控件（逐平台逐页）", ""]
    if "error" in dis:
        lines.append(f"- 检测失败：{dis['error'][:160]}")
    else:
        for pid, rows in dis.items():
            lines.append(f"- {pid}: {len(rows)} 个禁用控件")
            for r in rows[:8]:
                lines.append(f"  - p{r['page']}({r['page_label']}) {r['type']} 「{r['label']}」")
    rb = result["runtime_button_audit"]
    lines += ["", "## 4. 运行时按钮审计交叉", "",
              f"- 回调：{json.dumps(rb.get('status', {}), ensure_ascii=False)}"]
    if rb.get("duplicate_labels"):
        lines += ["", "### 同页重复文案（前 20）", ""]
        for d in rb["duplicate_labels"][:20]:
            lines.append(f"- {d['platform']}:{d['page']} 「{d['label']}」×{d['count']}")
    lines += ["", "原始数据：`docs/control-audit.json`。" ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "buttons": b["total"], "no_on_click": len(b["no_on_click"]),
        "disabled_default": len(b["disabled_default"]),
        "toast_only": len(b["toast_only"]),
        "bool_total": s["total"], "write_only": len(s["write_only"]),
        "unused": len(s["unused_anywhere"]),
        "conflicts": len(c.get("baseline", [])) if isinstance(c, dict) else -1,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
