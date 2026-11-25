"""Alibaba 六步发品流程的通用 Flet 组件。

设计约定（供接入方使用）：

- **只读**：组件只读取 ``app.cfg`` 与 ``app`` 上的只读状态，不写配置、不保存商品；
  所有写操作（保存草稿/发布/批量覆盖字段）由调用方通过回调注入。
- **防御性**：``app`` / ``app.cfg`` / 配置值缺失、类型异常、读取报错时统一降级为
  「未开始」，组件本身绝不因为配置缺 key 而抛异常。
- **可测试**：``ALIBABA_STEPS`` / ``alibaba_step_statuses()`` / ``configured_count()``
  是纯逻辑，不需要 Flet Page；三个 UI 组件也都能在无 Page 环境下构造。

导出：``ALIBABA_STEPS``、``alibaba_stepper``、``batch_override``、``save_next_bar``、
``status_badge``，以及辅助的 ``alibaba_step_statuses``、``configured_count``。
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Iterable

import flet as ft

__all__ = [
    "ALIBABA_STEPS",
    "alibaba_stepper",
    "batch_override",
    "save_next_bar",
    "status_badge",
    "alibaba_step_statuses",
    "configured_count",
]

# 六步顺序即产品使用顺序；页面号与 navigation.DESTINATIONS 保持一致。
ALIBABA_STEPS: list[tuple[int, str]] = [
    (1, "连接店铺"),
    (29, "采集箱商品"),
    (30, "类目与属性"),
    (31, "商品资料与价格"),
    (3, "文案与图片"),
    (33, "检查与发布"),
]

# 过渡期兼容：旧页面号映射到合并后的新步骤（与 app._switch 的旧路由兼容一致）。
_PAGE_ALIASES = {32: 31, 5: 3, 12: 29, 44: 33}
_INVALID_IDS = {"", "none", "null", "undefined", "0"}
_MIN_KEYWORDS = 3
_STATUS_LABELS = {
    "done": "已完成",
    "current": "当前",
    "partial": "待处理",
    "todo": "未开始",
}
_STATUS_LEVELS = {
    "done": "ok",
    "current": "current",
    "partial": "warn",
    "todo": "muted",
}
_LEVEL_ALIASES = {
    "ok": "ok",
    "success": "ok",
    "done": "ok",
    "warn": "warn",
    "warning": "warn",
    "partial": "warn",
    "current": "current",
    "accent": "current",
    "info": "current",
    "active": "current",
    "muted": "muted",
    "todo": "muted",
    "default": "muted",
    "": "muted",
}

_MISSING = object()


# ---------------------------------------------------------------------------
# 基础工具
def _safe_getattr(obj, name: str, default: Any = None) -> Any:
    """getattr 的异常安全版本（属性访问本身也可能抛异常）。"""
    if obj is None:
        return default
    try:
        return getattr(obj, name, default)
    except Exception:
        return default
# ---------------------------------------------------------------------------
def _ui():
    """惰性导入 ``aeclone.ui.app``，避免模块导入期的循环依赖。"""
    try:
        from . import app as ui  # noqa: PLC0415 - 必须惰性导入
        return ui
    except Exception:  # pragma: no cover - 极端环境下降级为内置配色
        return None


def _const(name: str, fallback: str) -> str:
    ui = _ui()
    if ui is None:
        return fallback
    try:
        value = getattr(ui, name, fallback)
    except Exception:
        return fallback
    return value if isinstance(value, str) and value else fallback


def _palette() -> dict[str, str]:
    """当前主题色；``aeclone.ui.app`` 不可用时退回深色主题常量。"""
    return {
        "accent": _const("ACCENT", "#58C9B7"),
        "accent_2": _const("ACCENT_2", "#80BCF4"),
        "border": _const("BORDER", "#2C3C45"),
        "card": _const("CARD", "#19262E"),
        "card_2": _const("CARD_2", "#21323C"),
        "text": _const("TEXT", "#EEF4F5"),
        "muted": _const("MUTED", "#A2B4BF"),
        "success": _const("SUCCESS_C", "#58C9B7"),
        "warn": _const("WARN_C", "#EAC078"),
        "danger": _const("DANGER_C", "#FF9298"),
        "on_accent": _const("ON_ACCENT", "#102B2B"),
    }


def _tint(color: str, opacity: float):
    try:
        return ft.Colors.with_opacity(opacity, color)
    except Exception:  # pragma: no cover - 老版本 Flet 兜底
        return None


def _cfg(app, key: str, default: Any = "") -> Any:
    """读取 ``app.cfg`` 的一个键；任何异常/缺失都返回 ``default``。"""
    cfg = _safe_getattr(app, "cfg", None)
    if cfg is None:
        return default
    getter = _safe_getattr(cfg, "get", None)
    if callable(getter):
        try:
            value = getter(key, default)
        except Exception:
            value = _MISSING
        if value is not _MISSING and value is not None:
            return value
    try:
        value = getattr(cfg, key)
    except Exception:
        return default
    return default if value is None else value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        return ""
    try:
        return str(value).strip()
    except Exception:  # pragma: no cover - 极端对象
        return ""


def _display(value: Any, limit: int = 80) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = _text(value)
    else:
        text = _text(value)
    if not text:
        return ""
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _valid_id(value: Any) -> str:
    text = _text(value)
    return "" if text.lower() in _INVALID_IDS else text


def _short_id(identifier: str) -> str:
    identifier = _text(identifier)
    if len(identifier) <= 14:
        return identifier
    return "…" + identifier[-8:]


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _keywords(app) -> list[str]:
    raw = _cfg(app, "alibaba_keywords", "")
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = (
            str(raw or "")
            .replace("，", ",")
            .replace("、", ",")
            .replace("\n", ",")
            .split(",")
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        word = _text(item).strip("\"'")
        if word and word not in seen:
            seen.add(word)
            result.append(word)
    return result


def _image_urls(value: Any) -> list:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, dict):
        items = [value]
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            items = []
        elif raw.startswith("["):
            decoded = _json_dict(raw) or raw
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                decoded = []
            items = decoded if isinstance(decoded, list) else []
        else:
            items = raw.replace("|", ",").split(",")
    else:
        items = []
    return [
        item
        for item in items
        if (isinstance(item, str) and item.strip()) or isinstance(item, dict)
    ]


def _product_state(app, product_id: str) -> tuple[dict, dict]:
    """读取商品详情的只读编辑态（由 alibaba_editor 维护）。"""
    states = _safe_getattr(app, "_alibaba_editor_states", None)
    if not isinstance(states, dict):
        return {}, {}
    state = states.get(product_id)
    if not isinstance(state, dict):
        return {}, {}
    product = state.get("product")
    overrides = state.get("overrides")
    return (
        product if isinstance(product, dict) else {},
        overrides if isinstance(overrides, dict) else {},
    )


def _configured(value: Any) -> bool:
    """配置值是否算「已设置」（空串/None/False/0/空容器 → 未设置）。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return bool(_text(value))


def _normalize_level(level: Any) -> str:
    return _LEVEL_ALIASES.get(_text(level).lower(), "muted")


def _level_color(level: str, palette: dict[str, str]) -> str:
    return {
        "ok": palette["success"],
        "warn": palette["warn"],
        "current": palette["accent"],
        "muted": palette["muted"],
    }.get(_normalize_level(level), palette["muted"])


def _normalize_page(page: Any) -> int | None:
    if page is None:
        return None
    try:
        number = int(page)
    except (TypeError, ValueError):
        return None
    return _PAGE_ALIASES.get(number, number)


def _current_page(app, current_page: Any = None) -> int | None:
    """优先使用显式传入的 current_page，否则从 app 的当前路由推断。"""
    if current_page is not None:
        return _normalize_page(current_page)
    for attr in ("_page_idx", "_current"):
        if app is None:
            break
        try:
            value = _safe_getattr(app, attr, None)
        except Exception:
            continue
        if value is None:
            continue
        page = _normalize_page(value)
        if page is not None:
            return page
    return None


def _page_update(page) -> None:
    update = _safe_getattr(page, "update", None)
    if callable(update):
        try:
            update()
        except Exception:
            pass


def _notify(app, message: str, ok: bool = True) -> None:
    toast = _safe_getattr(app, "toast", None)
    if not callable(toast):
        return
    try:
        toast(message, ok)
    except Exception:
        pass


def _navigate(app, page) -> bool:
    """跳转到页面号；页面号/回调缺失时安静地什么都不做。"""
    if page is None or app is None:
        return False
    switch = _safe_getattr(app, "_switch", None)
    if not callable(switch):
        return False
    try:
        target = int(page)
    except (TypeError, ValueError):
        target = page
    try:
        switch(target)
    except Exception:
        return False
    return True


def _invoke(callback: Callable, event) -> Any:
    """调用回调：无参回调不传 event，有位置参数的回调传入 event。"""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        signature = None
    if signature is None:
        try:
            return callback(event)
        except TypeError:
            return callback()
    positional = [
        param
        for param in signature.parameters.values()
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if positional:
        return callback(event)
    return callback()


# ---------------------------------------------------------------------------
# 步骤状态（纯逻辑，不依赖 Flet Page）
# ---------------------------------------------------------------------------
def _shop_status(app) -> tuple[bool, bool, str]:
    shop_id = _valid_id(_cfg(app, "alibaba_shop_id"))
    shop_name = _text(_cfg(app, "alibaba_shop_name"))
    if not (shop_id or shop_name):
        return False, False, "未选择店铺（前往「连接店铺」）"
    label = shop_name or shop_id
    cookie = _text(_cfg(app, "alibaba_cookies"))
    if cookie:
        return True, False, f"店铺：{label} · Cookie 已保存"
    return True, False, f"店铺：{label} · Cookie 由连接页检测"


def _product_status(app) -> tuple[bool, bool, str]:
    product_id = _valid_id(_cfg(app, "alibaba_test_product_id"))
    if not product_id:
        return False, False, "未选择商品（在采集箱打开商品）"
    return True, False, f"商品：{_short_id(product_id)}"


def _category_status(app) -> tuple[bool, bool, str]:
    category_id = _valid_id(_cfg(app, "alibaba_category_id"))
    if not category_id:
        return False, False, "未选择类目"
    presets = _json_dict(_cfg(app, "alibaba_attr_presets", ""))
    suffix = f" · 属性预设 {len(presets)} 项" if presets else " · 属性预设未填写"
    return True, False, f"类目：{_short_id(category_id)}{suffix}"


def _profile_status(app) -> tuple[bool, bool, str]:
    company = bool(
        _text(_cfg(app, "alibaba_company_name"))
        or _text(_cfg(app, "alibaba_company_desc"))
        or _text(_cfg(app, "alibaba_company_faq"))
    )
    keywords = _keywords(app)
    keyword_ok = len(keywords) >= _MIN_KEYWORDS
    if company and keyword_ok:
        return True, False, f"公司资料已填 · 关键词 {len(keywords)} 个"
    if company:
        return False, True, f"公司资料已填 · 关键词 {len(keywords)}/{_MIN_KEYWORDS}"
    if keyword_ok:
        return False, True, f"关键词 {len(keywords)} 个 · 公司资料待填"
    return False, False, f"公司资料与关键词（≥{_MIN_KEYWORDS} 个）待填"


def _content_status(app) -> tuple[bool, bool, str]:
    product_id = _valid_id(_cfg(app, "alibaba_test_product_id"))
    if not product_id:
        return False, False, "先选择商品再生成文案/图片"
    product, overrides = _product_state(app, product_id)
    if not product and not overrides:
        return False, False, "商品详情未载入（回到采集箱打开商品）"

    subject = _text(overrides.get("subject", product.get("subject") or product.get("title")))
    desc = _text(
        overrides.get("text_desc", product.get("textDesc") or product.get("description"))
    )
    copy_ok = bool(subject and desc)

    if "main_images" in overrides:
        main_images = _image_urls(overrides.get("main_images"))
    else:
        main_images = _image_urls(
            product.get("mainImage") or product.get("draftImgUrl")
        )
    if "detail_images" in overrides:
        detail_images = _image_urls(overrides.get("detail_images"))
    else:
        detail_images = _image_urls(product.get("detailImage"))
    media_ok = bool(main_images)

    if copy_ok and media_ok:
        return (
            True,
            False,
            f"文案已就绪 · 主图 {len(main_images)} 张 · 详情图 {len(detail_images)} 张",
        )
    if copy_ok:
        return False, True, f"文案已就绪 · 主图待补（详情图 {len(detail_images)} 张）"
    if media_ok:
        return False, True, f"主图 {len(main_images)} 张 · 文案待生成"
    return False, False, "文案与主图待生成"


def _publish_seen(app) -> dict:
    seen = _safe_getattr(app, "_publish_seen", None)
    if isinstance(seen, dict):
        rows = seen.get("alibaba")
        if isinstance(rows, dict):
            return rows
    payload = _json_dict(_cfg(app, "publish_seen", ""))
    rows = payload.get("alibaba")
    return rows if isinstance(rows, dict) else {}


def _publish_fail_count(app) -> int:
    counts = _safe_getattr(app, "_publish_counts", None)
    if not isinstance(counts, dict):
        counts = _json_dict(_cfg(app, "publish_counts", ""))
    row = counts.get("alibaba") if isinstance(counts, dict) else None
    if not isinstance(row, dict):
        return 0
    try:
        return max(0, int(row.get("fail") or 0))
    except (TypeError, ValueError):
        return 0


def _publish_status(app) -> tuple[bool, bool, str]:
    product_id = _valid_id(_cfg(app, "alibaba_test_product_id"))
    if not product_id:
        return False, False, "先选择商品再保存/发布"
    raw = _publish_seen(app).get(product_id)
    state = _text(raw).lower()
    if state == "success" or raw is True:
        return True, False, f"商品 {_short_id(product_id)} 发布成功"
    if state == "fail" or raw is False:
        return False, True, f"商品 {_short_id(product_id)} 发布失败，需修复后重发"
    fail_count = _publish_fail_count(app)
    if fail_count:
        return False, True, f"本商品尚无结果 · 今日有 {fail_count} 条发布失败记录"
    return False, False, "尚未保存/发布"


def _step_checks(app) -> list[tuple[int, bool, bool, str]]:
    builders = (
        (1, _shop_status),
        (29, _product_status),
        (30, _category_status),
        (31, _profile_status),
        (3, _content_status),
        (33, _publish_status),
    )
    checks: list[tuple[int, bool, bool, str]] = []
    for page, builder in builders:
        try:
            done, partial, detail = builder(app)
        except Exception:
            done, partial, detail = False, False, "状态读取失败"
        checks.append((page, bool(done), bool(partial) and not done, _text(detail)))
    return checks


def alibaba_step_statuses(app, current_page: Any = None) -> list[dict]:
    """返回六步状态列表（纯逻辑，不创建 Flet 控件）。

    每项：``page``、``title``、``status``（done/current/partial/todo）、
    ``done``、``partial``、``detail``、``label``（中文状态）、``level``（配色级别）。
    ``app`` 或缺配置时全部降级为未开始，不会抛异常。
    """
    current = _current_page(app, current_page)
    try:
        checks = _step_checks(app)
    except Exception:  # pragma: no cover - _step_checks 已逐项兜底
        checks = []
    by_page = {
        page: (done, partial, detail) for page, done, partial, detail in checks
    }
    statuses: list[dict] = []
    for page, title in ALIBABA_STEPS:
        done, partial, detail = by_page.get(page, (False, False, ""))
        if current == page:
            state = "current"
        elif done:
            state = "done"
        elif partial:
            state = "partial"
        else:
            state = "todo"
        statuses.append(
            {
                "page": page,
                "title": title,
                "status": state,
                "done": bool(done),
                "partial": bool(partial),
                "detail": detail,
                "label": _STATUS_LABELS[state],
                "level": _STATUS_LEVELS[state],
            }
        )
    return statuses


# ---------------------------------------------------------------------------
# 通用小组件
# ---------------------------------------------------------------------------
def _badge_style(level: Any, palette: dict[str, str]) -> tuple[str, str]:
    normalized = _normalize_level(level)
    return normalized, _level_color(normalized, palette)


def _apply_badge(badge: ft.Container, text: Any, level: Any, palette: dict[str, str]) -> None:
    _, color = _badge_style(level, palette)
    try:
        badge.content.value = _text(text)
        badge.content.color = color
    except Exception:
        pass
    try:
        badge.bgcolor = _tint(color, 0.14)
        badge.border = ft.border.all(1, _tint(color, 0.45))
    except Exception:
        pass


def status_badge(text: Any, level: str = "ok") -> ft.Container:
    """小号状态徽标；``level`` 支持 ok / warn / muted / current（及常见别名）。"""
    palette = _palette()
    normalized, color = _badge_style(level, palette)
    label = _text(text) or "—"
    badge = ft.Container(
        content=ft.Text(label, size=11, color=color, weight=ft.FontWeight.W_500),
        padding=ft.padding.symmetric(horizontal=8, vertical=2),
        border_radius=10,
        bgcolor=_tint(color, 0.14),
        border=ft.border.all(1, _tint(color, 0.45)),
    )
    badge._alibaba_badge_level = normalized
    return badge


def _status_icon(state: str, color: str) -> ft.Icon:
    name = {
        "done": "CHECK_CIRCLE",
        "current": "PLAY_CIRCLE_FILLED",
        "partial": "PENDING",
        "todo": "RADIO_BUTTON_UNCHECKED",
    }.get(state, "CIRCLE")
    icon = getattr(ft.Icons, name, None) or getattr(ft.Icons, "CIRCLE", None)
    return ft.Icon(icon, size=13, color=color)


def _step_chip(app, status: dict, index: int, palette: dict[str, str]) -> ft.Container:
    state = status["status"]
    color = _level_color(status["level"], palette)
    is_current = state == "current"
    is_done = bool(status["done"])
    can_switch = callable(_safe_getattr(app, "_switch", None))
    on_click = (
        (lambda e, page=status["page"]: _navigate(app, page)) if can_switch else None
    )
    page = status["page"]
    return ft.Container(
        width=172,
        padding=ft.padding.symmetric(horizontal=10, vertical=8),
        border_radius=10,
        bgcolor=(
            _tint(palette["accent"], 0.14)
            if is_current
            else (_tint(palette["success"], 0.08) if is_done else palette["card_2"])
        ),
        border=ft.border.all(
            1,
            palette["accent"]
            if is_current
            else (palette["success"] if is_done else palette["border"]),
        ),
        ink=can_switch,
        on_click=on_click,
        tooltip=f"第 {index} 步 · {status['title']}：{status['detail'] or status['label']}",
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(
                            width=18,
                            height=18,
                            alignment=ft.alignment.center,
                            border_radius=9,
                            bgcolor=_tint(color, 0.18),
                            content=ft.Text(
                                str(index),
                                size=10,
                                color=color,
                                weight=ft.FontWeight.W_700,
                            ),
                        ),
                        ft.Text(
                            status["title"],
                            size=12.5,
                            color=palette["text"],
                            weight=ft.FontWeight.W_600 if is_current else ft.FontWeight.W_500,
                            expand=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        _status_icon(state, color),
                        ft.Text(status["label"], size=10.5, color=color),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    status["detail"] or "—",
                    size=10.5,
                    color=palette["muted"],
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=status["detail"] or None,
                ),
            ],
            spacing=3,
            tight=True,
        ),
    )


# ---------------------------------------------------------------------------
# 六步步骤条
# ---------------------------------------------------------------------------
def alibaba_stepper(app, current_page: Any = None) -> ft.Control:
    """一行式六步状态条：小、紧凑、不遮挡页面核心功能。"""
    palette = _palette()
    statuses = alibaba_step_statuses(app, current_page)
    done_count = sum(1 for status in statuses if status["done"])
    current = next((s for s in statuses if s["status"] == "current"), None)
    can_switch = callable(_safe_getattr(app, "_switch", None))

    def compact_chip(status: dict, index: int) -> ft.Container:
        state = status["status"]
        color = _level_color(status["level"], palette)
        is_current = state == "current"
        on_click = (lambda e, page=status["page"]: _navigate(app, page)) if can_switch else None
        return ft.Container(
            width=94, height=26, padding=ft.padding.symmetric(horizontal=4, vertical=1),
            border_radius=7,
            bgcolor=(_tint(palette["accent"], 0.14) if is_current
                     else (_tint(palette["success"], 0.08) if status["done"] else palette["card_2"])),
            border=ft.border.all(1, palette["accent"] if is_current else
                                 (palette["success"] if status["done"] else palette["border"])),
            ink=can_switch, on_click=on_click,
            tooltip=f"第 {index} 步 · {status['title']}：{status['detail'] or status['label']}",
            content=ft.Row([
                ft.Text(str(index), size=8.5, color=color, weight=ft.FontWeight.W_700, width=8),
                ft.Text(status["title"], size=9.5, color=palette["text"],
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
            ], spacing=3, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    row_controls: list[ft.Control] = [
        ft.Text(f"已完成 {done_count}/6", size=10.5, color=palette["muted"]),
        *[compact_chip(status, i) for i, status in enumerate(statuses, 1)],
    ]
    refresh_page = current["page"] if current else _current_page(app, current_page)
    if refresh_page is not None and can_switch:
        row_controls.append(ft.TextButton(
            "刷新状态", icon=ft.Icons.REFRESH,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=4, vertical=0)),
            on_click=lambda e, page=refresh_page: _navigate(app, page),
        ))
    # 不包大卡片：整条步骤只占一行，避免遮挡核心功能。
    return ft.Row(row_controls, spacing=5, wrap=False,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


# ---------------------------------------------------------------------------
# 批量覆盖（折叠）
# ---------------------------------------------------------------------------
def _field_entries(fields: Iterable | None) -> list[dict]:
    """把 ``fields`` 归一化为 ``[{"key", "label", "third"}]``；坏数据直接跳过。"""
    entries: list[dict] = []
    if fields is None:
        return entries
    try:
        iterator = iter(fields)
    except TypeError:
        return entries
    for item in iterator:
        key = label = third = None
        if isinstance(item, dict):
            key = item.get("config_key", item.get("key", item.get("cfg_key")))
            label = item.get("label", item.get("title", key))
            third = item.get(
                "control_factory", item.get("factory", item.get("control"))
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            key, label = item[0], item[1]
            if len(item) >= 3:
                third = item[2]
        if not key:
            continue
        key_text = _text(key)
        if not key_text:
            continue
        entries.append(
            {"key": key_text, "label": _text(label) or key_text, "third": third}
        )
    return entries


def configured_count(app, fields: Iterable | None) -> int:
    """统计 ``fields`` 中配置值已设置的数量（只读，不调用 control_factory）。"""
    count = 0
    for entry in _field_entries(fields):
        try:
            if _configured(_cfg(app, entry["key"], None)):
                count += 1
        except Exception:
            continue
    return count


def _build_override_control(third, value):
    """按契约构造控件：``control_factory(current_value)`` 或 ``control_factory()``。"""
    if third is None:
        return None
    if isinstance(third, ft.Control):
        return third
    if not callable(third):
        return None
    try:
        signature = inspect.signature(third)
    except (TypeError, ValueError):
        signature = None
    if signature is None:
        try:
            return third(value)
        except Exception:
            try:
                return third()
            except Exception:
                return None
    positional = [
        param
        for param in signature.parameters.values()
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    try:
        return third(value) if positional else third()
    except Exception:
        return None


def _watch_change(control, refresh: Callable[[], None]) -> None:
    """在不改变原回调语义的前提下，字段变化后刷新「已设置」计数。"""
    original = getattr(control, "on_change", None)
    if not callable(original):
        return

    def handler(event):
        try:
            original(event)
        finally:
            try:
                refresh()
            except Exception:
                pass

    try:
        control.on_change = handler
    except Exception:
        pass


def batch_override(
    app,
    page,
    fields: Iterable | None,
    *,
    title: str = "批量覆盖（可选）",
    expanded: bool = False,
) -> ft.ExpansionTile:
    """折叠的「批量覆盖」区：显示已设置数量，字段控件由调用方提供。

    ``fields`` 支持三种写法：

    - ``(config_key, label)``：只读展示当前配置值；
    - ``(config_key, label, control_factory)``：``control_factory(current_value)``
      或 ``control_factory()`` 返回控件；也接受现成的 ``ft.Control``。
    - ``{"config_key": ..., "label": ..., "control_factory"|"factory"|"control": ...}``。

    组件**只读** ``app.cfg`` 用于计数与展示，不写配置、不保存；写入逻辑由
    control_factory 内部的回调负责。所有字段/回调异常都会被吞掉并降级显示。
    """
    palette = _palette()
    entries = _field_entries(fields)
    total = len(entries)
    initial_count = configured_count(app, entries)

    count_badge = status_badge(
        f"已设置 {initial_count}/{total} 项" if total else "无字段",
        "current" if initial_count else "muted",
    )

    rows: list[ft.Control] = []
    refreshers: list[Callable[[], None]] = []

    def refresh() -> None:
        current = configured_count(app, entries)
        _apply_badge(
            count_badge,
            f"已设置 {current}/{total} 项" if total else "无字段",
            "current" if current else "muted",
            palette,
        )
        for refresher in refreshers:
            try:
                refresher()
            except Exception:
                continue
        target = page if page is not None else _safe_getattr(app, "page", None)
        _page_update(target)

    for entry in entries:
        key, label = entry["key"], entry["label"]
        value = _cfg(app, key, None)
        badge = status_badge(
            "已设置" if _configured(value) else "未设置",
            "current" if _configured(value) else "muted",
        )
        control = _build_override_control(entry["third"], value)
        if control is not None:
            _watch_change(control, refresh)
            holder: ft.Control = control
        else:
            display = _display(value)
            value_text = ft.Text(
                display or "未设置",
                size=11.5,
                color=palette["text"] if display else palette["muted"],
                selectable=True,
            )
            holder = value_text

            def refresh_readonly(
                badge=badge, value_text=value_text, key=key, palette=palette
            ) -> None:
                current = _cfg(app, key, None)
                display = _display(current)
                value_text.value = display or "未设置"
                value_text.color = palette["text"] if display else palette["muted"]
                _apply_badge(
                    badge,
                    "已设置" if _configured(current) else "未设置",
                    "current" if _configured(current) else "muted",
                    palette,
                )

            refreshers.append(refresh_readonly)

        def refresh_badge(badge=badge, key=key, palette=palette) -> None:
            current = _cfg(app, key, None)
            _apply_badge(
                badge,
                "已设置" if _configured(current) else "未设置",
                "current" if _configured(current) else "muted",
                palette,
            )

        refreshers.append(refresh_badge)

        rows.append(
            ft.Column(
                [
                    ft.Row(
                        [
                            badge,
                            ft.Text(label, size=12, color=palette["text"]),
                            ft.Text(
                                key,
                                size=10,
                                color=palette["muted"],
                                selectable=True,
                            ),
                        ],
                        spacing=8,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    holder,
                ],
                spacing=4,
            )
        )

    footer = ft.Row(
        [
            ft.Text(
                "只覆盖这里填写/选择的字段；留空表示沿用商品或默认设置。",
                size=10.5,
                color=palette["muted"],
                expand=True,
            ),
            ft.TextButton(
                "刷新已设置数量",
                icon=ft.Icons.REFRESH,
                on_click=lambda e: refresh(),
            ),
        ],
        spacing=8,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    content = ft.Column(
        [*(rows or [ft.Text("未提供批量字段。", size=11, color=palette["muted"])]), footer],
        spacing=10,
    )
    tile = ft.ExpansionTile(
        title=ft.Row(
            [
                ft.Text(
                    title,
                    size=13.5,
                    color=palette["text"],
                    weight=ft.FontWeight.W_600,
                ),
                count_badge,
            ],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        controls=[content],
        initially_expanded=bool(expanded),
        icon_color=palette["accent"],
    )
    try:
        tile.on_change = lambda e: refresh()
    except Exception:
        pass
    return tile


# ---------------------------------------------------------------------------
# 底部「上一步 / 保存并下一步」
# ---------------------------------------------------------------------------
def save_next_bar(
    app,
    prev_page,
    next_page,
    on_save: Callable | None = None,
    *,
    prev_label: str = "上一步",
    save_label: str = "保存并下一步",
    hint: str | None = None,
) -> ft.Control:
    """底部操作条：上一步 / 保存并下一步。

    ``on_save`` 可选：无参或接收 event 均可；
    返回 ``False`` 表示保存失败/需要停留，组件不会再跳转；
    抛异常时提示并停留在当前页。缺省（不做保存）时直接跳转。
    页面跳转通过 ``app._switch(page)``；回调/页面缺失时安静降级。
    """
    palette = _palette()
    hint_text = hint or "保存成功后进入下一步；保存失败会停留在当前页。"

    def go_prev(event):
        _navigate(app, prev_page)

    def go_next(event):
        result = None
        if callable(on_save):
            try:
                result = _invoke(on_save, event)
            except Exception as error:  # noqa: BLE001 - UI 回调必须兜底
                _notify(app, f"保存失败：{_text(error)[:160]}", False)
                return
        if result is False:
            return
        _navigate(app, next_page)

    buttons: list[ft.Control] = []
    if prev_page is not None:
        buttons.append(
            ft.TextButton(
                prev_label,
                icon=ft.Icons.CHEVRON_LEFT,
                on_click=go_prev,
            )
        )
    buttons.append(
        ft.ElevatedButton(
            save_label,
            icon=ft.Icons.SAVE,
            bgcolor=palette["accent"],
            color=palette["on_accent"],
            on_click=go_next,
        )
    )

    body = ft.Row(
        [
            ft.Text(hint_text, size=11, color=palette["muted"], expand=True,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            *buttons,
        ],
        spacing=10,
        wrap=False,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    ui = _ui()
    if ui is not None:
        try:
            return ui.card(body, padding=12)
        except Exception:  # pragma: no cover - 防御性兜底
            pass
    return ft.Container(
        content=body,
        bgcolor=palette["card"],
        border=ft.border.all(1, palette["border"]),
        border_radius=12,
        padding=12,
    )
