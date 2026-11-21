"""Amazon AI 提示词预设库 · 独立验收（task-14：内容合规 + 功能 + UI + 平台隔离）。

只读/离线：不 import 后端实现做断言（用公开 API 实测）、不联网、不真实写。
覆盖：
1. 六类预设数量/命名/描述/长度；占位符白名单 + 未知占位符保留；
2. 非 image 类合规词扫描（禁止语境白名单）；SKU 约束；图片主图约束；
3. apply_to_cfg/resolve_selected/reset_to_default/render；build_*_prompt 确实使用新 prompt；
   ai_generate_field("sku")（FakeApi）、parse_sku 边界；
4. UI 预设下拉/套用/恢复/可编辑/复制/AI SKU/图片预设；后端缺失降级；
5. 平台隔离（AliExpress TEXT_PROMPT_PRESETS / Alibaba 行业预设不受影响，Amazon 独占 cfg 键）。

跑法：AURORA_TEST=1 .venv/bin/python tools/verify_amazon_presets.py
输出：docs/amazon-presets-verification.json + docs/design/amazon-presets.png。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AURORA_TEST"] = "1"

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _path in (str(SRC), str(TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = ROOT / "docs" / "amazon-presets-verification.json"
SHOT = ROOT / "docs" / "design" / "amazon-presets.png"

MIN_COUNTS = {"title": 6, "bullets": 5, "description": 5,
              "search_terms": 5, "sku": 5, "image": 10}
PLACEHOLDER_WHITELIST = {
    "country", "code", "language", "lang", "title_limit", "bullet_limit",
    "bullet_suggest", "count", "desc_bytes", "st_bytes", "title", "subject",
}
BANNED_TERMS = (
    r"\bbest\b", r"\bno\.?\s*1\b", r"100\s*%", r"\bfree\s+shipping\b",
    r"\bsale\b", r"\bguarantee[ds]?\b", r"https?://", r"@", r"电话",
    r"\bwhats\s*app\b", r"\btelephone\b", r"\bphone\b",
)
NEGATION_MARKERS = ("禁止", "不得", "不要", "不含", "避免", "切勿", "不能", "无", "禁")
RELEASE_BANNED = ("AURORA_", "预演模式", "本地验收", "测试连接", "沙箱", "mock",
                  "验收预演")


class Report:
    def __init__(self):
        self.sections: dict[str, list[dict]] = {}

    def add(self, section: str, name: str, ok: bool, detail: str,
            observed=None) -> bool:
        row = {"name": name, "ok": bool(ok), "detail": str(detail)}
        if observed is not None:
            row["observed"] = observed
        self.sections.setdefault(section, []).append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] ({section}) {name} — {detail}")
        return row["ok"]

    def summary(self) -> dict:
        rows = [r for rows in self.sections.values() for r in rows]
        return {"passed": sum(1 for r in rows if r["ok"]), "total": len(rows),
                "failed": [f"{sec}::{r['name']}" for sec, rows2 in self.sections.items()
                           for r in rows2 if not r["ok"]]}


def presets_module():
    from aeclone.services import amazon_presets
    return amazon_presets


# ---------------------------------------------------------------------------
# 1) 内容审计
# ---------------------------------------------------------------------------
def _prompt_placeholders(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\{([a-z_]+)\}", str(text or ""))))


def _clause_is_negative(clause: str) -> bool:
    return any(marker in clause for marker in NEGATION_MARKERS)


def scan_banned_terms(prompt: str) -> list[str]:
    """返回未被「禁止语境」豁免的促销/联系方式词命中列表。"""
    hits: list[str] = []
    for term in BANNED_TERMS:
        for match in re.finditer(term, str(prompt or ""), re.I):
            clause = str(prompt or "")
            # 取命中点所在分句（行/；/。/，）
            start = max(clause.rfind(ch, 0, match.start()) for ch in "\n；;。")
            end = min([idx for idx in (clause.find(ch, match.end()) for ch in "\n；;。")
                       if idx != -1] or [len(clause)])
            snippet = clause[start + 1 if start >= 0 else 0:end]
            if not _clause_is_negative(snippet):
                hits.append(snippet.strip()[:100])
    return hits


def check_content(report: Report) -> dict:
    module = presets_module()
    counts = {kind: len(module.PRESETS.get(kind) or {})
              for kind in module.PRESET_KINDS}
    report.add("content", "六类数量达标", all(counts[k] >= MIN_COUNTS[k] for k in counts),
               f"counts={counts}；min={MIN_COUNTS}")
    report.add("content", "PRESET_VERSION 存在", bool(module.PRESET_VERSION),
               f"version={module.PRESET_VERSION}")

    bad_meta = []
    for kind, presets in module.PRESETS.items():
        for key, item in presets.items():
            name = str(item.get("name") or "")
            desc = str(item.get("desc") or "")
            prompt = str(item.get("prompt") or "")
            if len(name) < 2 or len(desc) < 6:
                bad_meta.append(f"{kind}/{key} name/desc 过短")
            if len(prompt) < 120:
                bad_meta.append(f"{kind}/{key} prompt 过短({len(prompt)})")
    report.add("content", "命名/描述/prompt 非空且专业", not bad_meta,
               f"{sum(len(v) for v in module.PRESETS.values())} 条；问题={bad_meta[:5]}")

    unknown: list[dict] = []
    for kind, presets in module.PRESETS.items():
        for key, item in presets.items():
            for placeholder in _prompt_placeholders(item["prompt"]):
                if placeholder not in PLACEHOLDER_WHITELIST:
                    unknown.append({"kind": kind, "key": key,
                                    "placeholder": placeholder})
    render_unknown = module.render("A {country} {unknown_ph} {title}",
                                   {"country": "美国", "title": "T"})
    unknown_ok = "{unknown_ph}" in render_unknown
    report.add("content", "占位符仅白名单 + 未知占位符保留",
               not unknown and unknown_ok,
               f"未知占位符={unknown[:5]}; render 保留未知={unknown_ok}")

    compliance: dict[str, list] = {}
    for kind, presets in module.PRESETS.items():
        if kind == "image":
            continue
        for key, item in presets.items():
            hits = scan_banned_terms(item["prompt"])
            if hits:
                compliance[f"{kind}/{key}"] = hits
    report.add("content", "非 image 类无未豁免促销/联系方式示例", not compliance,
               f"命中={list(compliance)[:5]}" if compliance else
               f"扫描 {sum(len(v) for k, v in module.PRESETS.items() if k != 'image')} 条，"
               "「禁止…」语境已白名单")

    sku_bad = []
    for key, item in module.PRESETS["sku"].items():
        prompt = item["prompt"]
        needs = ("40" in prompt, "字母" in prompt, "空格" in prompt,
                 "只输出" in prompt)
        if not all(needs):
            sku_bad.append(f"{key} constrains={needs}")
    report.add("content", "SKU 预设约束 ≤40/字符集/无空格/只输出", not sku_bad,
               f"{len(module.PRESETS['sku'])} 条；问题={sku_bad}")

    main = module.PRESETS["image"].get("white_bg_main") or {}
    main_prompt = str(main.get("prompt") or "")
    main_needs = ["纯白", "文字", "水印", "产品本体", "Logo"]
    missing = [needle for needle in main_needs if needle not in main_prompt]
    report.add("content", "图片主图预设含纯白/无文字/无 Logo/水印/不改产品本体",
               not missing, f"缺={missing}")
    return {"counts": counts, "version": module.PRESET_VERSION,
            "names": {kind: {key: item.get("name") for key, item in presets.items()}
                      for kind, presets in module.PRESETS.items()},
            "unknown_placeholders": unknown, "compliance_hits": compliance}


# ---------------------------------------------------------------------------
# 2) 功能验收
# ---------------------------------------------------------------------------
def _cfg_with_all_keys(module) -> SimpleNamespace:
    data = {}
    for kind in module.PRESET_KINDS:
        data[module.PROMPT_CFG_KEYS[kind]] = ""
        data[module.SELECTED_CFG_KEYS[kind]] = ""
    data.update(amazon_presets_version="", amazon_sku_prompt="",
                # 其它平台无关键（验证不被误改）
                alibaba_title_prompt="ALI-TITLE", ali_prompt="ALIEXPRESS")
    return SimpleNamespace(**data)


def check_functional(report: Report) -> dict:
    module = presets_module()
    from aeclone.services import amazon_prompts as AP
    from aeclone.services import amazon_pipeline as PL

    result: dict = {}
    # 2.1 API 行为
    cfg = _cfg_with_all_keys(module)
    api_ok = True
    details = []
    for kind in module.PRESET_KINDS:
        opts = module.preset_options(kind)
        presets = module.list_presets(kind)
        if len(opts) != len(module.PRESETS[kind]) or len(presets) != len(opts):
            api_ok = False
            details.append(f"{kind}: options={len(opts)} presets={len(presets)}")
        key = opts[0][0] if opts and isinstance(opts[0], (tuple, list)) else ""
        if not key:
            api_ok = False
            details.append(f"{kind}: 无 options")
            continue
        prompt = module.apply_to_cfg(cfg, kind, key)
        marker_key = module.SELECTED_CFG_KEYS[kind]
        prompt_key = module.PROMPT_CFG_KEYS[kind]
        if getattr(cfg, prompt_key) != prompt or getattr(cfg, marker_key) != key:
            api_ok = False
            details.append(f"{kind}: apply 写 cfg 不一致")
        if module.resolve_selected(cfg, kind) != key:
            api_ok = False
            details.append(f"{kind}: resolve_selected 不回显")
        default_key = module.reset_to_default(cfg, kind)
        if getattr(cfg, marker_key) != default_key:
            api_ok = False
            details.append(f"{kind}: reset 未回默认")
    report.add("functional", "list/preset_options/get/apply/resolve/reset", api_ok,
               "；".join(details[:6]) if details else "六类 API 行为一致")

    render_ok = (module.render("Hi {country} {code} {unknown} {title}",
                               {"country": "美国", "code": "US", "title": "T"})
                 == "Hi 美国 US {unknown} T")
    report.add("functional", "render 已知替换 / 未知保留", render_ok,
               "替换已知占位符、未知原样保留" if render_ok else "render 行为异常")

    # 2.2 build_*_prompt 确实使用预设文本
    product = {"title": "5 Ton Backhoe Loader HW20-28", "sku": "WY-QHCA-LTM-1",
               "brand": "Generic", "manufacturer": "Generic",
               "description": "<p>China factory</p>",
               "bulletPoint": "A±B±C", "searchTerms": "backhoe",
               "marketPlaceId": "A2EUQ1WTGCTBG2",
               "categoryId": "13553005011"}
    builder_cfg = _cfg_with_all_keys(module)
    builder_rows = []
    for kind, builder in (("title", AP.build_title_prompt),
                          ("bullets", AP.build_bullets_prompt),
                          ("description", AP.build_description_prompt),
                          ("search_terms", AP.build_search_terms_prompt),
                          ("sku", AP.build_sku_prompt)):
        key = module.default_preset(kind)
        module.apply_to_cfg(builder_cfg, kind, key)
        prompt = builder(product, builder_cfg, "A2EUQ1WTGCTBG2")
        raw = module.PRESETS[kind][key]["prompt"]
        distinctive = raw.split("{")[0].strip()[:10] or raw[:10]
        ok = distinctive in prompt
        builder_rows.append({"kind": kind, "preset": key, "ok": ok})
    report.add("functional", "build_title/bullets/description/search_terms/sku 使用新 prompt",
               all(row["ok"] for row in builder_rows),
               "；".join(f"{row['kind']}={row['ok']}" for row in builder_rows))
    # 字段级 cfg 覆盖也生效
    override_cfg = _cfg_with_all_keys(module)
    override_cfg.amazon_title_prompt = "CUSTOM_MARKER_TITLE {country}"
    override_ok = "CUSTOM_MARKER_TITLE" in AP.build_title_prompt(
        product, override_cfg, "A2EUQ1WTGCTBG2")
    report.add("functional", "cfg 可编辑提示词覆盖生效", override_ok,
               "自定义 marker 已进入 build_title_prompt")

    # 2.3 ai_generate_field("sku") + parse_sku 边界
    class FakeApi:
        def chat(self, model, messages, max_tokens=120):
            return "Seller SKU: hw20 28/CA_01"

    with patch.object(PL, "_ai_client", lambda cfg: FakeApi()), \
         patch.object(PL, "_ai_model", lambda cfg: "fake"):
        value, notes = PL.ai_generate_field(product, builder_cfg, "sku",
                                            marketplace_id="A2EUQ1WTGCTBG2")
    sku_ok = bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(value or "")))
    report.add("functional", "ai_generate_field('sku') 走 FakeApi 返回合法 SKU",
               sku_ok, f"value={value!r} notes={notes[:2]}")
    boundaries = {
        "空": AP.parse_sku(""),
        "围栏+标签": AP.parse_sku("```\nSeller SKU: ab 12/中文_3\n```"),
        "超长": AP.parse_sku("A" * 60),
        "非法字符": AP.parse_sku("SKU: ab@cd 中文!"),
    }
    boundary_ok = all(re.fullmatch(r"[A-Za-z0-9_-]{0,40}", str(v or ""))
                      for v in boundaries.values()) and len(boundaries["超长"]) <= 40
    report.add("functional", "parse_sku 边界（空/围栏/超长/非法字符）", boundary_ok,
               str(boundaries))
    result.update({"api_ok": api_ok, "builder_rows": builder_rows,
                   "sku_value": value, "boundaries": boundaries})
    return result


# ---------------------------------------------------------------------------
# 3) UI 验收
# ---------------------------------------------------------------------------
def _render_page13():
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        yield_ = app
        return yield_


def _visible_controls(app):
    import flet as ft
    def walk(control, parent_visible=True):
        visible = parent_visible and bool(getattr(control, "visible", True))
        yield control, visible
        try:
            children = list(control._get_children())
        except Exception:  # noqa: BLE001
            children = []
        for child in children:
            yield from walk(child, visible)
    for root in app.content.controls:
        yield from walk(root)


def check_ui(report: Report) -> dict:
    import flet as ft
    from ui_sandbox import build_app, sandbox

    module = presets_module()
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(13)
        dropdowns = {}
        for control, visible in _visible_controls(app):
            if visible and isinstance(control, ft.Dropdown):
                dropdowns[str(getattr(control, "label", ""))] = control
        buttons = [str(getattr(c, "text", "") or "")
                   for c, v in _visible_controls(app)
                   if v and isinstance(c, (ft.ElevatedButton, ft.OutlinedButton,
                                           ft.TextButton, ft.IconButton))]
        textfields = [str(getattr(c, "label", "") or "")
                      for c, v in _visible_controls(app)
                      if v and isinstance(c, ft.TextField)]
        texts = [str(getattr(c, "value", "") or "")
                 for c, v in _visible_controls(app) if v and isinstance(c, ft.Text)]
        expected_labels = ["标题预设", "五点描述预设", "产品描述预设",
                           "Search Terms预设", "SKU预设", "图片预设"]
        counts_ok = all(label in dropdowns and
                        len(getattr(dropdowns[label], "options", []) or []) ==
                        len(module.PRESETS[kind])
                        for label, kind in zip(expected_labels,
                                               ("title", "bullets", "description",
                                                "search_terms", "sku", "image")))
        report.add("ui", "六类预设下拉 + 选项数量", counts_ok,
                   "；".join(f"{label}:{len(getattr(dropdowns.get(label), 'options', []) or [])}"
                            for label in expected_labels))
        report.add("ui", "套用×6 / 恢复内置×6 / 复制提示词", 
                   buttons.count("套用") >= 6 and buttons.count("恢复内置") >= 6
                   and "复制提示词" in buttons,
                   f"套用={buttons.count('套用')} 恢复={buttons.count('恢复内置')} "
                   f"复制={'复制提示词' in buttons}")
        editor_ok = all(any(label.startswith(kind) for label in textfields)
                        for kind in ("标题提示词", "五点描述提示词", "产品描述提示词",
                                     "Search Terms提示词", "SKU提示词", "图片提示词"))
        report.add("ui", "5 类文本提示词 + 图片提示词可编辑框", editor_ok,
                   f"匹配={[label for label in textfields if '提示词' in label][:8]}")
        report.add("ui", "当前预设名 / AI 生成 SKU 按钮",
                   any("当前预设：" in text for text in texts)
                   and "AI 生成 SKU" in buttons,
                   f"当前预设数={sum('当前预设：' in t for t in texts)}；"
                   f"AI SKU={'AI 生成 SKU' in buttons}")
        # 套用/恢复（UI 真实回调 + cfg 生效）
        widgets = getattr(app, "_amz_preset_widgets", {}) or {}
        title_widget = widgets.get("title") or {}
        title_dd = title_widget.get("dd")
        row_ok = False
        if title_dd is not None and getattr(title_dd, "options", None):
            title_dd.value = title_dd.options[0].key
            apply_buttons = [c for c, v in _visible_controls(app)
                             if v and isinstance(c, (ft.ElevatedButton,
                                                     ft.OutlinedButton,
                                                     ft.TextButton))
                             and str(getattr(c, "text", "") or "") == "套用"]
            with contextlib.suppress(Exception):
                apply_buttons[0].on_click(SimpleNamespace(
                    control=apply_buttons[0], data="", page=app.page))
            expected_prompt = module.PRESETS["title"][title_dd.value]["prompt"]
            row_ok = str(getattr(app.cfg, "amazon_title_prompt", "")) == expected_prompt
        report.add("ui", "套用按钮写入 amazon_title_prompt", row_ok,
                   "预设 prompt 已写入 cfg" if row_ok else "套用未生效")
    return {"dropdown_counts": {label: len(getattr(dropdowns.get(label),
                                                   "options", []) or [])
                                for label in expected_labels},
            "buttons": buttons, "textfields": textfields}


def check_ui_degradation(report: Report) -> bool:
    from ui_sandbox import build_app, sandbox
    ok = False
    detail = ""
    try:
        with patch.dict(sys.modules, {"aeclone.services.amazon_presets": None}):
            with sandbox():
                app = build_app()
                app._switch_platform("amazon")
                app._switch(13)
                ok = getattr(app, "_page_idx", None) == 13
                detail = "预设库缺失时 13 页仍可渲染（下拉禁用/提示未就绪）"
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
    if not ok:
        detail = detail or "13 页渲染失败"
    report.add("ui", "后端预设库缺失降级不崩溃", ok, detail)
    return ok


# ---------------------------------------------------------------------------
# 4) 平台隔离
# ---------------------------------------------------------------------------
def check_platform_isolation(report: Report) -> dict:
    from ui_sandbox import build_app, sandbox
    app_source = (SRC / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
    aliexpress_ok = "TEXT_PROMPT_PRESETS = {" in app_source
    alibaba_ok = (SRC / "aeclone" / "services" / "alibaba_prompts.py").exists()
    report.add("platform", "AliExpress TEXT_PROMPT_PRESETS 保留", aliexpress_ok,
               "app.py 内 AliExpress 文本预设仍存在")
    report.add("platform", "Alibaba 行业预设模块保留", alibaba_ok,
               "services/alibaba_prompts.py 仍在")
    # 其它平台页面可渲染
    failures = []
    for pid, page in (("aliexpress", 3), ("alibaba", 12), ("amazon", 13)):
        try:
            with sandbox():
                app = build_app()
                if pid != "alibaba":
                    app._switch_platform(pid)
                app._switch(page)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{pid}:{page} {type(exc).__name__}")
    report.add("platform", "AliExpress AI 文案 / Alibaba 12 / Amazon 13 可渲染",
               not failures, "；".join(failures) if failures else "三平台页面渲染正常")
    # Amazon 独占 cfg 键：apply 不修改其它平台键
    module = presets_module()
    cfg = _cfg_with_all_keys(module)
    before = {k: getattr(cfg, k) for k in ("alibaba_title_prompt", "ali_prompt")}
    for kind in module.PRESET_KINDS:
        module.apply_to_cfg(cfg, kind, module.default_preset(kind))
    after = {k: getattr(cfg, k) for k in before}
    isolation_ok = before == after
    report.add("platform", "Amazon 预设只写 amazon_* 独占键", isolation_ok,
               f"其它平台键 before={before} after={after}")
    return {"aliexpress_presets": aliexpress_ok, "alibaba_presets": alibaba_ok,
            "render_failures": failures, "cfg_isolation": isolation_ok}


# ---------------------------------------------------------------------------
# 5) 正式版文案扫描（Amazon 页面）
# ---------------------------------------------------------------------------
def check_release_scan(report: Report) -> dict:
    import flet as ft
    from ui_sandbox import build_app, sandbox
    leaks = []
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        for page in (0, 13, 10, 11):
            app._switch(page)
            for control, visible in _visible_controls(app):
                if not visible:
                    continue
                if isinstance(control, ft.Text):
                    values = [str(control.value or "")]
                elif isinstance(control, (ft.ElevatedButton, ft.OutlinedButton,
                                          ft.TextButton, ft.IconButton)):
                    values = [str(getattr(control, "text", "") or "")]
                elif isinstance(control, (ft.TextField, ft.Dropdown)):
                    values = [str(getattr(control, "label", "") or ""),
                              str(getattr(control, "hint_text", "") or "")]
                else:
                    continue
                for value in values:
                    for term in RELEASE_BANNED:
                        if term in value:
                            leaks.append({"page": page, "term": term,
                                          "text": value[:100]})
    report.add("release", "Amazon 页面正式版文案无测试痕迹", not leaks,
               "0 泄漏" if not leaks else str(leaks[:5]))
    return {"leaks": leaks}


# ---------------------------------------------------------------------------
# 6) 截图（滚动到 ④b 预设卡）
# ---------------------------------------------------------------------------
def capture_screenshot(report: Report) -> dict:
    try:
        from playwright.sync_api import sync_playwright
        import amazon_ui_shots as ui_shots
    except Exception as exc:  # noqa: BLE001
        report.add("screenshot", "预设卡截图", False, f"截图链路不可用：{exc}")
        return {}
    port = ui_shots._free_port()
    server = ui_shots._PreviewServer("amazon", 13, port)
    SHOT.parent.mkdir(parents=True, exist_ok=True)
    ok, detail = False, ""
    try:
        server.start()
        if not server.wait(40):
            detail = "预览服务未就绪"
        else:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(
                        channel="chrome", headless=True, args=["--headless=new"])
                except Exception:  # noqa: BLE001
                    browser = playwright.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(viewport={"width": 1560, "height": 3600})
                    page = ctx.new_page()
                    page.goto(f"http://127.0.0.1:{port}", wait_until="load",
                              timeout=45000)
                    with contextlib.suppress(Exception):
                        page.wait_for_selector("canvas", timeout=25000)
                    page.wait_for_timeout(4500)
                    page.screenshot(path=str(SHOT))
                finally:
                    browser.close()
            ok = SHOT.exists() and SHOT.stat().st_size > 0
            detail = f"{SHOT.name} ({SHOT.stat().st_size} bytes)" if ok else "截图为空"
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
    finally:
        server.stop()
    report.add("screenshot", "预设卡截图", ok, detail,
               {"path": str(SHOT.relative_to(ROOT)),
                "bytes": SHOT.stat().st_size,
                "sha256": hashlib.sha256(SHOT.read_bytes()).hexdigest()[:16]}
               if ok else None)
    return {"ok": ok, "path": str(SHOT.relative_to(ROOT)),
            "bytes": SHOT.stat().st_size if ok else 0}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    report = Report()
    content = check_content(report)
    functional = check_functional(report)
    ui = check_ui(report)
    check_ui_degradation(report)
    isolation = check_platform_isolation(report)
    release = check_release_scan(report)
    shot = capture_screenshot(report)
    summary = report.summary()
    evidence = {
        "generated_at": __import__("datetime").datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "mode": "read-only/offline audit (no network, no real write)",
        "preset_version": content.get("version"),
        "counts": content.get("counts"),
        "names": content.get("names"),
        "sections": report.sections,
        "summary": summary,
        "content_audit": content,
        "functional": functional,
        "ui": ui,
        "platform_isolation": isolation,
        "release_scan": release,
        "screenshot": shot,
        "notes": [
            "独立实现：用公开 API 与沙箱渲染做断言，不复制实现内部分支。",
            "合规扫描按分句判断：含「禁止/不得/不含…」的禁止语境豁免（白名单规则）。",
            "未知占位符保留且不报错；render 仅替换白名单占位符。",
            "UI 套用/恢复用真实 Flet 控件回调；未联网、未真实写。",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果：{summary['passed']}/{summary['total']} PASS · 证据 {OUTPUT}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
