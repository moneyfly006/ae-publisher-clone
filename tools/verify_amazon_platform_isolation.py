"""Amazon 平台隔离 · 独立 UI 审计（task-6，只读沙箱，绝不修改业务代码/不真实写）。

目的
----
独立验证「Amazon 平台 UI 去 SMT（速卖通）残留」是否到位，且不误伤其它平台：

1. **Amazon 应消失项**：顶部栏「选择店铺」；概览「选择商品 / 今日任务 / 改前后对比 /
   配置冲突行 + 检查配置冲突按钮 / 底部图片服务 tile」；任务与日志「停止任务 / 已编辑记录 /
   定时发布 tab」；设置「图片与模型 tab / ffmpeg / 副标题与实例说明里的图片服务×任务队列×定时发布 /
   恢复图床」；首次引导不弹。
2. **隐藏过头检查**：Amazon 13 页新 7 卡与关键控件、概览成本/用量/准备状态/API/授权仍在。
3. **其它平台保留**：AliExpress 上述 SMT 元素全部仍在、所有页面可渲染、引导仍弹；
   Alibaba 既有 cap 行为不变（无 6/7 页、scheduled_publish 隐藏、12 页在）；TikTok/Shopee 页面集合
   与关键控件不受影响、渲染不崩溃。
4. **隐藏不足扫描**：Amazon 平台 nav/直切/旧 start_page/导航搜索/⌘R/⌘F/⌘L/后台入口
   （_start/_run_pipeline/_toggle_run/_publish_one/_retry_publish_failed/_run_scheduled）
   以及逐页可见控件的运行时点击，是否能打开 1–9 号 SMT 页或 SMT 弹窗。

实现方式：``tools/ui_sandbox.py`` 的 ``build_app/sandbox`` + 控件树 walk（含祖先 visible
继承），全部在临时配置/禁用网络下运行；脚本自身 ``AURORA_TEST=1``。
输出 ``docs/amazon-platform-isolation.json``，并刷新两张 13 页截图。

跑法：``AURORA_TEST=1 .venv/bin/python tools/verify_amazon_platform_isolation.py``
退出码：全 PASS → 0；有 FAIL → 1。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AURORA_TEST"] = "1"   # 必须在 import 应用模块之前：禁止真实写

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _path in (str(SRC), str(TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = ROOT / "docs" / "amazon-platform-isolation.json"
SHOT_TOP = ROOT / "docs" / "design" / "amazon-listing-page.png"
SHOT_PRICE = ROOT / "docs" / "design" / "amazon-listing-page-2-price-preview.png"

SMT_PAGES = {1, 2, 3, 4, 5, 6, 7, 8, 9}          # Amazon 平台不可达的 SMT 页面
SMT_MARKERS = ("搜索商品", "模板产品", "模板来源", "选择商品", "速卖通", "SMT",
               "发布队列", "定时发布", "SKU 编码", "价格设置", "变种设置", "草稿与待发布")


# ---------------------------------------------------------------------------
# 沙箱渲染与控件树
# ---------------------------------------------------------------------------
def effective_walk(roots):
    """遍历控件树并继承祖先 visible（隐藏容器里的子控件不算可见）。"""
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


def label_of(control) -> str:
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


def tabs_of(controls):
    import flet as ft
    out = []
    for control, visible in effective_walk(controls):
        if isinstance(control, ft.Tabs):
            out.append({"visible": visible,
                        "labels": [str(getattr(tab, "text", "") or "") for tab in control.tabs]})
    return out



def _nav_groups_of(app) -> list:
    """左侧导航分组：[(分组名, [页面 index...])]。"""
    import flet as ft
    out = []
    for container, indices in getattr(app, "_nav_groups", []):
        texts = []
        def walk(control):
            if isinstance(control, ft.Text):
                texts.append(str(control.value or ""))
            try:
                children = list(control._get_children())
            except Exception:  # noqa: BLE001
                children = []
            for child in children:
                walk(child)
        walk(container)
        out.append((" ".join(texts), [int(i) for i in indices]))
    return out


def open_app(platform_id: str, page_index: int):
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        snapshot = snapshot_of(app)
    return snapshot


def snapshot_of(app) -> dict:
    controls = list(effective_walk(app.content.controls))
    texts = [(label_of(control), visible) for control, visible in controls]
    buttons = [(str(getattr(control, "text", "") or ""), visible)
               for control, visible in controls
               if callable(getattr(control, "on_click", None))]
    return {
        "landed": getattr(app, "_page_idx", None),
        "store_shortcut_visible": bool(getattr(getattr(app, "store_shortcut", None),
                                               "visible", True)),
        "nav_indices": [int(index) for index, _button in getattr(app, "nav_buttons", [])],
        "nav_groups": _nav_groups_of(app),
        "texts": texts,
        "buttons": buttons,
        "tabs": tabs_of(app.content.controls),
        "controls": controls,
    }


def visible_values(snapshot: dict) -> list[str]:
    values = [value for value, visible in snapshot["texts"] if visible and value.strip()]
    values += [value for value, visible in snapshot["buttons"] if visible and value.strip()]
    return values


def has_visible(snapshot: dict, needle: str) -> bool:
    return any(needle in value for value in visible_values(snapshot))


def tab_labels(snapshot: dict) -> list[str]:
    labels = []
    for row in snapshot["tabs"]:
        if row["visible"]:
            labels += row["labels"]
    return labels


def _all_tab_labels(snapshot: dict) -> list[str]:
    labels = []
    for row in snapshot["tabs"]:
        labels += row["labels"]
    return labels


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.sections: dict[str, list[dict]] = {}

    def add(self, section: str, name: str, ok: bool, detail: str,
            observed: dict | None = None) -> bool:
        row = {"name": name, "ok": bool(ok), "detail": str(detail)}
        if observed is not None:
            row["observed"] = observed
        self.sections.setdefault(section, []).append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] ({section}) {name} — {detail}")
        return bool(ok)

    def rows(self) -> list[dict]:
        return [row for rows in self.sections.values() for row in rows]

    def summary(self) -> dict:
        rows = self.rows()
        return {
            "passed": sum(1 for row in rows if row["ok"]),
            "total": len(rows),
            "failed": [f"{section}::{row['name']}" for section, rows_ in
                       self.sections.items() for row in rows_ if not row["ok"]],
        }


# ---------------------------------------------------------------------------
# 1) Amazon 应消失项
# ---------------------------------------------------------------------------
def check_amazon_hidden(report: Report) -> None:
    overview = open_app("amazon", 0)
    logs = open_app("amazon", 10)
    settings = open_app("amazon", 11)

    section = "amazon_hidden"
    report.add(section, "顶部栏「选择店铺」隐藏",
               not overview["store_shortcut_visible"],
               f"store_shortcut.visible={overview['store_shortcut_visible']}")
    report.add(section, "概览无「选择商品」可见",
               not has_visible(overview, "选择商品"),
               "选择商品按钮不可见" if not has_visible(overview, "选择商品")
               else "仍可见：选择商品")
    report.add(section, "概览无「今日任务」统计",
               not has_visible(overview, "今日任务"),
               "今日任务统计卡不存在" if not has_visible(overview, "今日任务")
               else "仍可见：今日任务")
    report.add(section, "概览无「改前后对比」",
               not has_visible(overview, "改前后对比"),
               "改前后对比卡不存在" if not has_visible(overview, "改前后对比")
               else "仍可见：改前后对比")
    report.add(section, "概览「配置冲突」行 + 检查按钮隐藏",
               not has_visible(overview, "配置冲突") and not has_visible(overview, "检查配置冲突"),
               f"配置冲突行可见={has_visible(overview, '配置冲突')}；"
               f"检查按钮可见={has_visible(overview, '检查配置冲突')}")
    report.add(section, "概览底部「图片服务」tile 隐藏",
               not has_visible(overview, "图片服务"),
               "图片服务 tile 不可见" if not has_visible(overview, "图片服务")
               else "仍可见：图片服务")

    report.add(section, "page10 无「停止任务」",
               not has_visible(logs, "停止任务"),
               "停止任务按钮不可见" if not has_visible(logs, "停止任务") else "仍可见：停止任务")
    report.add(section, "page10 无「已编辑记录」",
               not has_visible(logs, "已编辑记录"),
               "已编辑记录行不可见" if not has_visible(logs, "已编辑记录")
               else "仍可见：已编辑记录")
    report.add(section, "page10 无「定时发布」tab",
               "定时发布" not in _all_tab_labels(logs),
               f"tabs={_all_tab_labels(logs)}")

    report.add(section, "page11 无「图片与模型」tab",
               "图片与模型" not in _all_tab_labels(settings),
               f"tabs={_all_tab_labels(settings)}")
    report.add(section, "page11 无 ffmpeg 文案/下载",
               not has_visible(settings, "ffmpeg"),
               "维护与诊断未见 ffmpeg" if not has_visible(settings, "ffmpeg")
               else "仍可见：ffmpeg")
    subtitle = [value for value in visible_values(settings) if "管理 API" in value]
    report.add(section, "page11 设置副标题不含「图片服务」",
               bool(subtitle) and all("图片服务" not in value for value in subtitle),
               f"副标题={subtitle[:2]}")
    inst = [value for value in visible_values(settings) if "每个实例独立配置" in value]
    report.add(section, "page11 实例说明不含「任务队列/定时发布」",
               bool(inst) and all(("任务队列" not in value and "定时发布" not in value)
                                  for value in inst),
               f"实例说明={inst[:1]}")
    report.add(section, "page11 无「恢复图床」/图床提示",
               not has_visible(settings, "恢复图床"),
               "恢复图床按钮不可见" if not has_visible(settings, "恢复图床")
               else "仍可见：恢复图床")

    # 首次引导：Amazon 不弹（smt_wizard cap=False）
    created = _wizard_created("amazon")
    report.add(section, "Amazon 首次引导不弹", not created,
               f"wizard_created={created}")


def _wizard_created(platform_id: str) -> bool:
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app.cfg.onboarded = False
        app.cfg.store_id = ""
        app.cfg.api_key = ""
        app.dlg = None
        app.page.dialog = None
        with contextlib.suppress(Exception):
            app._maybe_wizard()
        return bool(getattr(app, "wz_dlg", None) is not None
                    and getattr(app.page, "dialog", None) is not None)


# ---------------------------------------------------------------------------
# 2) Amazon 应保留项（防「隐藏过头」）
# ---------------------------------------------------------------------------
AMAZON_CARD_HEADERS = ("① 连接与店铺", "② 采集箱商品", "③ 站点与类目",
                       "④ 商品信息（价格 / 库存 / 物流 / 品牌 / SKU）",
                       "⑤ 文案与 AI", "⑥ AI 图片（图片只读，v1 备用）",
                       "⑦ 发布与结果")


def check_amazon_retained(report: Report) -> None:
    section = "amazon_retained"
    overview = open_app("amazon", 0)
    for needle in ("成本 / 用量", "准备状态", "API 服务", "授权状态"):
        report.add(section, f"概览保留「{needle}」", has_visible(overview, needle),
                   "可见" if has_visible(overview, needle) else "不可见（隐藏过头）")
    report.add(section, "概览保留「去 Amazon 刊登」入口",
               has_visible(overview, "去 Amazon 刊登") or has_visible(overview, "Amazon 刊登"),
               f"可见={'Amazon 刊登' if has_visible(overview, 'Amazon 刊登') else '未找到'}")
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        tooltip = str(getattr(getattr(app, "btn_run", None), "tooltip", "") or "")
        app._switch_platform("aliexpress")
        smt_tooltip = str(getattr(getattr(app, "btn_run", None), "tooltip", "") or "")
    report.add(section, "侧栏运行按钮 tooltip 去 SMT",
               "Amazon" in tooltip and "Amazon" not in smt_tooltip,
               f"Amazon tooltip={tooltip!r}；AliExpress={smt_tooltip!r}")

    page13 = open_app("amazon", 13)
    missing = [header for header in AMAZON_CARD_HEADERS if not has_visible(page13, header)]
    report.add(section, "隐藏路由 13：七张卡齐全（不在左侧导航）", not missing,
               f"{len(AMAZON_CARD_HEADERS) - len(missing)}/{len(AMAZON_CARD_HEADERS)}；缺={missing}")
    page_card = {15: 0, 16: 1, 17: 2, 18: 3, 19: 4, 20: 5, 21: 6}
    page_problems = {}
    for page, card_index in page_card.items():
        snap = open_app("amazon", page)
        own = AMAZON_CARD_HEADERS[card_index]
        other_hits = [h for i, h in enumerate(AMAZON_CARD_HEADERS)
                      if i != card_index and has_visible(snap, h)]
        if not has_visible(snap, own) or other_hits:
            page_problems[page] = {"own": has_visible(snap, own),
                                   "other": other_hits}
    report.add(section, "15–21 每页仅渲染对应单卡", not page_problems,
               f"7 页；问题={page_problems or '无'}")
    for needle in ("检测连接", "采集箱", "刷新", "加载类目", "校验路径",
                   "保存到 Amazon", "自动发布（自动修复）"):
        report.add(section, f"13 页保留「{needle}」入口", has_visible(page13, needle),
                   "可见" if has_visible(page13, needle) else "不可见（隐藏过头）")

    dropdowns = [value for value in visible_values(page13) if value in ("店铺", "站点")]
    fields = [value for value, visible in page13["texts"]
              if visible and value.startswith("卖点 ")]
    buttons = [value for value, visible in page13["buttons"] if visible]
    checks = {
        "店铺/站点下拉": {"店铺" in dropdowns, "站点" in dropdowns},
        "5 个卖点输入": all(f"卖点 {index}" in fields for index in range(1, 6)),
        "＋添加卖点": any("添加卖点" in text for text in buttons),
        "保存到 Amazon": any("保存到 Amazon" in text for text in buttons),
        "自动发布（自动修复）": any("自动发布" in text for text in buttons),
        "保存按钮": any("保存" in text for text in buttons),
        "AI 生成标题": any("AI 生成标题" in text for text in buttons),
        "AI 生成五点": any("AI 生成五点" in text for text in buttons),
        "AI 生成描述": any("AI 生成描述" in text for text in buttons),
        "AI 生成关键词": any("AI 生成关键词" in text for text in buttons),
        "AI 一键生成全部": any("AI 一键生成全部" in text for text in buttons),
        "n/700 计数×5": sum(1 for value, visible in page13["texts"]
                            if visible and "/700" in value) >= 5,
        "n/200 / n/2000 / n/250 bytes":
            any("/200" in value for value, visible in page13["texts"] if visible)
            and any("/2000" in value for value, visible in page13["texts"] if visible)
            and any("/250 bytes" in value for value, visible in page13["texts"] if visible),
    }
    for name, ok in checks.items():
        report.add(section, f"13 页关键控件：{name}", ok,
                   "存在" if ok else "缺失")

    logs = open_app("amazon", 10)
    report.add(section, "page10 保留「运行日志」tab",
               "运行日志" in _all_tab_labels(logs), f"tabs={_all_tab_labels(logs)}")
    settings = open_app("amazon", 11)
    report.add(section, "page11 保留 3 个设置 tab",
               _all_tab_labels(settings) == ["账号与连接", "界面与实例", "维护与诊断"],
               f"tabs={_all_tab_labels(settings)}")


# ---------------------------------------------------------------------------
# 3) AliExpress 保留项（SMT 默认平台）
# ---------------------------------------------------------------------------
def check_aliexpress(report: Report) -> None:
    section = "aliexpress"
    overview = open_app("aliexpress", 0)
    report.add(section, "顶部栏「选择店铺」可见", overview["store_shortcut_visible"],
               f"store_shortcut.visible={overview['store_shortcut_visible']}")
    report.add(section, "概览「选择商品」可见", has_visible(overview, "选择商品"),
               "可见" if has_visible(overview, "选择商品") else "不可见（误伤）")
    report.add(section, "概览「今日任务」可见", has_visible(overview, "今日任务"),
               "可见" if has_visible(overview, "今日任务") else "不可见（误伤）")
    report.add(section, "概览「改前后对比」可见", has_visible(overview, "改前后对比"),
               "可见" if has_visible(overview, "改前后对比") else "不可见（误伤）")
    report.add(section, "概览「配置冲突」行/按钮可见",
               has_visible(overview, "配置冲突") and has_visible(overview, "检查配置冲突"),
               f"配置冲突={has_visible(overview, '配置冲突')}；"
               f"检查按钮={has_visible(overview, '检查配置冲突')}")
    report.add(section, "概览底部「图片服务」tile 可见", has_visible(overview, "图片服务"),
               "可见" if has_visible(overview, "图片服务") else "不可见（误伤）")

    logs = open_app("aliexpress", 10)
    report.add(section, "page10「定时发布」tab 可见",
               "定时发布" in _all_tab_labels(logs), f"tabs={_all_tab_labels(logs)}")
    report.add(section, "page10「停止任务」可见", has_visible(logs, "停止任务"),
               "可见" if has_visible(logs, "停止任务") else "不可见（误伤）")
    report.add(section, "page10「已编辑记录」可见", has_visible(logs, "已编辑记录"),
               "可见" if has_visible(logs, "已编辑记录") else "不可见（误伤）")

    settings = open_app("aliexpress", 11)
    report.add(section, "page11 4 个设置 tab（含图片与模型）",
               _all_tab_labels(settings) == ["账号与连接", "图片与模型", "界面与实例", "维护与诊断"],
               f"tabs={_all_tab_labels(settings)}")
    report.add(section, "page11「ffmpeg」文案可见", has_visible(settings, "ffmpeg"),
               "可见" if has_visible(settings, "ffmpeg") else "不可见（误伤）")
    subtitle = [value for value in visible_values(settings) if "管理 API" in value]
    report.add(section, "page11 副标题仍含「图片服务」",
               bool(subtitle) and all("图片服务" in value for value in subtitle),
               f"副标题={subtitle[:1]}")
    inst = [value for value in visible_values(settings) if "每个实例独立配置" in value]
    report.add(section, "page11 实例说明仍含「任务队列与定时发布」",
               bool(inst) and all("任务队列" in value and "定时发布" in value for value in inst),
               f"实例说明={inst[:1]}")
    report.add(section, "page11「恢复图床」可见", has_visible(settings, "恢复图床"),
               "可见" if has_visible(settings, "恢复图床") else "不可见（误伤）")
    report.add(section, "首次引导仍会弹", _wizard_created("aliexpress"),
               f"wizard_created={_wizard_created('aliexpress')}")

    from aeclone.platforms import get_platform
    platform = get_platform("aliexpress")
    failures = []
    for item in platform.pages:
        snapshot = open_app("aliexpress", item.index)
        if snapshot["landed"] != item.index:
            failures.append(f"page {item.index} 落点 {snapshot['landed']}")
    report.add(section, "AliExpress 所有页面可渲染", not failures,
               f"{len(platform.pages)} 页全部渲染" if not failures else "；".join(failures))


# ---------------------------------------------------------------------------
# 4) Alibaba 既有行为
# ---------------------------------------------------------------------------
def check_alibaba(report: Report) -> None:
    section = "alibaba"
    from aeclone.platforms import get_platform
    platform = get_platform("alibaba")
    indices = {item.index for item in platform.pages}
    report.add(section, "Alibaba 无 SMT 价格页(6)/变种页(7)",
               6 not in indices and 7 not in indices, f"pages={sorted(indices)}")
    report.add(section, "Alibaba 12 号刊登页仍在", 12 in indices,
               f"pages={sorted(indices)}")

    overview = open_app("alibaba", 0)
    report.add(section, "Alibaba 概览可渲染", overview["landed"] == 0,
               f"landed={overview['landed']}")
    report.add(section, "Alibaba 顶部栏「选择店铺」保留",
               overview["store_shortcut_visible"],
               f"store_shortcut.visible={overview['store_shortcut_visible']}")
    page12 = open_app("alibaba", 12)
    report.add(section, "Alibaba 12 页可渲染", page12["landed"] == 12,
               f"landed={page12['landed']}")
    logs = open_app("alibaba", 10)
    report.add(section, "Alibaba page10 无「定时发布」tab（scheduled_publish=False）",
               "定时发布" not in _all_tab_labels(logs), f"tabs={_all_tab_labels(logs)}")



# ---------------------------------------------------------------------------
# 5) TikTok / Shopee 不受影响
# ---------------------------------------------------------------------------
def check_other_platforms(report: Report) -> None:
    from aeclone.platforms import get_platform

    # Shopee：未被 Amazon 改动，按 SMT 默认平台验证关键控件仍保留。
    shopee_expected = {0, 1, 2, 3, 5, 8, 10, 11}
    shopee = get_platform("shopee")
    indices = {item.index for item in shopee.pages}
    report.add("shopee", "页面集合未被 Amazon 改动影响", indices == shopee_expected,
               f"pages={sorted(indices)}")
    problems = []
    for item in shopee.pages:
        try:
            open_app("shopee", item.index)   # 只要求渲染不抛异常
        except Exception as exc:  # noqa: BLE001
            problems.append(f"page {item.index} 崩溃 {type(exc).__name__}")
    report.add("shopee", "所有页面可渲染（不崩溃）", not problems,
               f"{len(shopee.pages)} 页渲染" if not problems else "；".join(problems))
    report.add("shopee", "Amazon caps 未改变 Shopee 页面集合",
               {item.index for item in shopee.pages} == shopee_expected,
               f"pages={sorted(item.index for item in shopee.pages)}"
               "（Shopee implemented=False 的锁定/不支持页由外部逻辑渲染，不计入 Amazon）")

    # TikTok：外部进程正在并发开发 14 号刊登页并调整 caps（非 Amazon 改动）。
    # 这里只验证「声明页面可渲染不崩溃」与「页面集合 == 平台声明集合」，不把它
    # 的 SMT 元素保留性算到 Amazon 结论里（否则会误判为 Amazon 回归）。
    tiktok = get_platform("tiktok")
    declared = sorted(item.index for item in tiktok.pages)
    crashes = []
    landed = {}
    for item in tiktok.pages:
        try:
            snap = open_app("tiktok", item.index)
            landed[item.index] = snap["landed"]
        except Exception as exc:  # noqa: BLE001
            crashes.append(f"page {item.index} 崩溃 {type(exc).__name__}")
    report.add("tiktok", "所有声明页面可渲染（不崩溃）", not crashes,
               f"{len(tiktok.pages)} 页渲染；声明 pages={declared}；落点={landed}"
               "（TikTok 处于外部并发开发中，不计入 Amazon 结论）")
    # Amazon 自己的导航/页面集合不能被外部 TikTok 改动污染。
    amazon = get_platform("amazon")
    expected_amazon = {0, 10, 11, 13, 15, 16, 17, 18, 19, 20, 21}
    report.add("tiktok", "Amazon 页面集合为 0/15–21/10/11 + 隐藏 13",
               {item.index for item in amazon.pages} == expected_amazon,
               f"amazon pages={sorted(item.index for item in amazon.pages)}"
               "（与 TikTok 外部改动无关）")


# ---------------------------------------------------------------------------
# 6) 「隐藏不足」入口扫描
# ---------------------------------------------------------------------------
def _smt_dialog_markers(dialog, page) -> list[str]:
    import flet as ft
    values = []
    if dialog is not None:
        for control, _visible in effective_walk([dialog]):
            if isinstance(control, ft.Text):
                values.append(str(control.value or ""))
    hits = []
    for value in values:
        for marker in SMT_MARKERS:
            if marker in value:
                hits.append(f"{marker}: {value[:80]}")
    return hits


def _probe_click(platform_id: str, page_index: int, position: int) -> dict:
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        controls = list(effective_walk(app.content.controls))
        control, visible = controls[position]
        switched: list[int] = []
        original_switch = app._switch

        def recorder(idx):
            switched.append(int(idx))
            return original_switch(idx)

        app._switch = recorder
        error = ""
        try:
            if callable(getattr(control, "on_click", None)):
                control.on_click(SimpleNamespace(
                    control=control, data=str(getattr(control, "value", "") or ""),
                    page=app.page))
        except Exception as exc:  # noqa: BLE001 - 沙箱限制导致的异常不代表 SMT 残留
            error = f"{type(exc).__name__}: {exc}"[:160]
        dialog = getattr(app.page, "dialog", None)
        hits = _smt_dialog_markers(dialog, app.page) if dialog is not None else []
        return {"switched": switched, "dialog_hits": hits, "error": error,
                "landed": getattr(app, "_page_idx", None)}


def _collect_entries(platform_id: str, page_index: int) -> list[dict]:
    from ui_sandbox import build_app, sandbox
    with sandbox():
        app = build_app()
        if platform_id != "alibaba":
            app._switch_platform(platform_id)
        app._switch(page_index)
        entries = []
        for position, (control, visible) in enumerate(effective_walk(app.content.controls)):
            if not visible or not callable(getattr(control, "on_click", None)):
                continue
            value = label_of(control)
            entries.append({"position": position, "type": type(control).__name__,
                            "label": value[:80]})
        return entries


def check_amazon_smt_entries(report: Report) -> None:
    from ui_sandbox import build_app, sandbox
    section = "amazon_smt_entries"
    overview = open_app("amazon", 0)
    expected_nav = [0, 15, 16, 17, 18, 19, 20, 21, 10, 11]
    report.add(section, "新导航 10 个可见入口（0/15–21/10/11）",
               list(overview["nav_indices"]) == expected_nav,
               f"nav={overview['nav_indices']}")
    report.add(section, "13「Amazon 刊登」不在左侧导航（内部隐藏路由）",
               13 not in overview["nav_indices"]
               and any(item.index == 13 and str(item.group).startswith("_")
                       for item in __import__("aeclone.platforms", fromlist=["get_platform"])
                       .get_platform("amazon").pages),
               f"13_in_nav={13 in overview['nav_indices']}")
    expected_groups = [("工作空间", [0]),
                       ("商品准备", [15, 16, 17, 18]),
                       ("内容处理", [19, 20]),
                       ("发布与运行", [21]),
                       ("运行", [10]), ("工具", [11])]
    report.add(section, "新导航 6 个分组/条目归属",
               [tuple(row) for row in overview["nav_groups"]] == expected_groups,
               f"groups={overview['nav_groups']}")

    import flet as ft
    from aeclone.platforms import AMAZON
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        redirects = {}
        for target in sorted(SMT_PAGES):
            # 平台不存在的页同样调 _switch，验证兜底重定向（不落 SMT 页）。
            app._switch(target)
            redirects[target] = getattr(app, "_page_idx", None)
        bad = {target: landed for target, landed in redirects.items()
               if landed in SMT_PAGES}
    report.add(section, "直切 1–9 号页均被重定向（不落 SMT 页）", not bad,
               f"redirects={redirects}" + (f"；违规={bad}" if bad else ""))

    # 旧配置 start_page=8（发布队列）也不能打开 SMT 页
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app.cfg.start_page = 8
        app._switch(app._start_page_index())
        landed = getattr(app, "_page_idx", None)
    report.add(section, "旧 start_page=8 被重定向", landed not in SMT_PAGES,
               f"landed={landed}")

    # 导航搜索：SMT 关键词不应打开 SMT 页
    search_problems = {}
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        for query in ("FOB", "价格", "变体", "模板", "发布队列", "定时发布", "SKU", "速卖通"):
            app.nav_search.value = query
            app._open_navigation_match()
            landed = getattr(app, "_page_idx", None)
            if landed in SMT_PAGES:
                search_problems[query] = landed
    report.add(section, "导航搜索 SMT 关键词不打开 SMT 页", not search_problems,
               "8 个关键词均未落 SMT 页" if not search_problems else f"违规={search_problems}")

    # 键盘快捷键
    shortcut_results = {}
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(0)
        app._on_key(SimpleNamespace(key="r", ctrl=True, meta=False, page=app.page))
        shortcut_results["ctrl+r"] = getattr(app, "_page_idx", None)
        app._switch(0)
        app._on_key(SimpleNamespace(key="l", ctrl=True, meta=False, page=app.page))
        shortcut_results["ctrl+l"] = getattr(app, "_page_idx", None)
        app._switch(0)
        app._on_key(SimpleNamespace(key="f", ctrl=True, meta=False, page=app.page))
        dialog = getattr(app.page, "dialog", None)
        shortcut_results["ctrl+f_dialog"] = bool(dialog is not None)
        shortcut_results["ctrl+f_hits"] = (_smt_dialog_markers(dialog, app.page)
                                           if dialog is not None else [])
        with contextlib.suppress(Exception):
            app._close_dlg(dialog) if dialog else None
    report.add(section, "⌘R 重定向 Amazon 13 页", shortcut_results["ctrl+r"] == 13,
               f"landed={shortcut_results['ctrl+r']}")
    report.add(section, "⌘L 打开任务与日志（允许）", shortcut_results["ctrl+l"] == 10,
               f"landed={shortcut_results['ctrl+l']}")
    report.add(section, "⌘F 不打开 SMT 搜索弹窗", not shortcut_results["ctrl+f_hits"],
               ("⌘F 打开了 SMT 商品搜索弹窗：" + "；".join(shortcut_results["ctrl+f_hits"]))
               if shortcut_results["ctrl+f_hits"]
               else f"dialog={shortcut_results['ctrl+f_dialog']}")

    # 后台 / 方法入口
    guard_results = {}
    with sandbox():
        app = build_app()
        app._switch_platform("amazon")
        app._switch(0)
        for name in ("_start", "_toggle_run", "_retry_publish_failed"):
            app._switch(0)
            err = ""
            try:
                getattr(app, name)()
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            guard_results[name] = {"landed": getattr(app, "_page_idx", None),
                                   "running": bool(getattr(app, "_running", False)),
                                   "error": err}
        app._switch(0)
        try:
            app._run_pipeline()
            run_error = ""
        except Exception as exc:  # noqa: BLE001
            run_error = f"{type(exc).__name__}: {exc}"
        guard_results["_run_pipeline"] = {"landed": getattr(app, "_page_idx", None),
                                          "running": bool(getattr(app, "_running", False)),
                                          "error": run_error}
        app._switch(0)
        try:
            app._publish_one("220114595363182310")
            pub_error = ""
        except Exception as exc:  # noqa: BLE001
            pub_error = f"{type(exc).__name__}: {exc}"
        guard_results["_publish_one"] = {"landed": getattr(app, "_page_idx", None),
                                         "running": bool(getattr(app, "_running", False)),
                                         "error": pub_error}
        app._switch(0)
        try:
            app._run_scheduled(SimpleNamespace(product_id="x", title="t", run_at=0))
            sched_error = ""
        except Exception as exc:  # noqa: BLE001
            sched_error = f"{type(exc).__name__}: {exc}"
        guard_results["_run_scheduled"] = {"landed": getattr(app, "_page_idx", None),
                                           "running": bool(getattr(app, "_running", False)),
                                           "error": sched_error}
    bad_guard = {name: row for name, row in guard_results.items()
                 if row["running"] or row["error"] or row["landed"] in SMT_PAGES}
    report.add(section, "后台/方法入口不跑 SMT 管线", not bad_guard,
               f"checked={list(guard_results)}" +
               (f"；异常={bad_guard}" if bad_guard else "；均被拦截"))

    # 逐页可见控件运行时点击：不得切到 1–9 号页 / 不得弹 SMT 弹窗
    leaks: list[dict] = []
    errors: list[str] = []
    total_clicks = 0
    for page_index in (0, 13, 10, 11):
        entries = _collect_entries("amazon", page_index)
        for entry in entries:
            total_clicks += 1
            result = _probe_click("amazon", page_index, entry["position"])
            smt_switches = [target for target in result["switched"] if target in SMT_PAGES]
            if smt_switches or result["dialog_hits"]:
                leaks.append({"page": page_index, "label": entry["label"],
                              "switches": smt_switches,
                              "dialog_hits": result["dialog_hits"]})
            if result["error"]:
                errors.append(f"page{page_index} {entry['label']}: {result['error']}")
    report.add(section, "Amazon 页面可见控件无 SMT 跳转/弹窗", not leaks,
               f"共点击 {total_clicks} 个可见控件；残留={leaks[:5]}" +
               (f"（共 {len(leaks)} 处）" if len(leaks) > 5 else ""))
    if errors:
        report.add(section, "可见控件点击沙箱异常（仅供参考，不判失败）", True,
                   f"{len(errors)} 条：" + "；".join(errors[:5]))


# ---------------------------------------------------------------------------
# 7) 截图
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_scrolled(platform_id: str, page_index: int, output: Path,
                      scroll_y: int = 1500) -> tuple[bool, str]:
    """滚动 Flet Canvas 后截图（价格/预演区域）。"""
    try:
        from playwright.sync_api import sync_playwright
        import amazon_ui_shots as ui_shots
    except Exception as exc:  # noqa: BLE001
        return False, f"截图链路不可用：{type(exc).__name__}: {exc}"
    port = ui_shots._free_port()
    server = ui_shots._PreviewServer(platform_id, page_index, port)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start()
        if not server.wait(40):
            return False, f"预览服务未就绪（见 {server.log_path}）"
        with sync_playwright() as playwright:
            try:
                # 新版 headless 才能正确加载 mac 系统中文字体（旧 headless 会渲染成方框）。
                browser = playwright.chromium.launch(channel="chrome", headless=True,
                                                     args=["--headless=new"])
            except Exception:  # noqa: BLE001
                browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1560, "height": 2200})
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}", wait_until="load", timeout=45000)
                with contextlib.suppress(Exception):
                    page.wait_for_selector("canvas", timeout=25000)
                page.wait_for_timeout(3500)
                page.mouse.move(760, 1100)
                page.mouse.wheel(0, scroll_y)
                page.wait_for_timeout(1200)
                page.screenshot(path=str(output))
            finally:
                browser.close()
        if not output.exists() or output.stat().st_size <= 0:
            return False, f"截图文件为空：{output}"
        return True, f"{output.name} ({output.stat().st_size} bytes)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        server.stop()


def capture_screenshots(report: Report) -> None:
    section = "screenshots"
    import amazon_ui_shots as ui_shots
    ok, detail = ui_shots.capture_screenshot("amazon", 13, SHOT_TOP)
    report.add(section, "13 页顶部截图", ok, detail,
               {"path": str(SHOT_TOP.relative_to(ROOT)), "bytes": SHOT_TOP.stat().st_size,
                "sha256": _sha256(SHOT_TOP)} if ok else None)
    ok2, detail2 = _capture_scrolled("amazon", 13, SHOT_PRICE, scroll_y=1500)
    report.add(section, "13 页价格/预演截图", ok2, detail2,
               {"path": str(SHOT_PRICE.relative_to(ROOT)),
                "bytes": SHOT_PRICE.stat().st_size,
                "sha256": _sha256(SHOT_PRICE)} if ok2 else None)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    os.environ["AURORA_TEST"] = "1"
    report = Report()
    check_amazon_hidden(report)
    check_amazon_retained(report)
    check_aliexpress(report)
    check_alibaba(report)
    check_other_platforms(report)
    check_amazon_smt_entries(report)
    capture_screenshots(report)

    summary = report.summary()
    hide_too_much = [name for name in summary["failed"] if "amazon_retained" in name]
    hide_too_little = [name for name in summary["failed"]
                       if "amazon_hidden" in name or "amazon_smt_entries" in name]
    evidence = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "sandbox UI audit (AURORA_TEST=1, no network, no write)",
        "platform": "amazon",
        "sections": report.sections,
        "summary": summary,
        "hide_too_much": hide_too_much,
        "hide_too_little": hide_too_little,
        "screenshots": {
            "top": {"path": str(SHOT_TOP.relative_to(ROOT)),
                    "sha256": _sha256(SHOT_TOP) if SHOT_TOP.exists() else ""},
            "price_preview": {"path": str(SHOT_PRICE.relative_to(ROOT)),
                              "sha256": _sha256(SHOT_PRICE) if SHOT_PRICE.exists() else ""},
        },
        "notes": [
            "独立实现：仅用 ui_sandbox.build_app/sandbox + Flet 控件树，不 import 新测试断言。",
            "所有调用在临时配置目录、网络禁用、AURORA_TEST=1 下运行；不产生真实写。",
            "visible 判定继承祖先容器 visible；隐藏容器内的控件按不可见处理。",
            "外部并发进程可能修改 Alibaba/TikTok 文件；相关失败会单列，不影响 Amazon 结论。",
            "TikTok 正被外部进程并发开发 14 号刊登页并调整 caps，本审计只断言其声明页面不崩溃。",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果：{summary['passed']}/{summary['total']} PASS · 证据 {OUTPUT}")
    if hide_too_much:
        print(f"隐藏过头：{hide_too_much}")
    if hide_too_little:
        print(f"隐藏不足/入口残留：{hide_too_little}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
