"""TikTok Shop 刊登流水线（服务层，不含 UI）。

一条链路（docs/TIKTOK_DESIGN.md §3.5）：读 ``gettiktokProduct`` 详情 → 店铺/站点/限额
→ 类目（叶子）校验 →（可选）AI 文案 → 组装 ``tiktokSave.txt`` 保存包 → 必填/限额/合规
校验 → 违禁词提示 → 预演 或 保存。

安全语义（与 Amazon 一致，见 docs §1.7 / §7）
--------------------------------------------
- ``dry_run=True``（默认）**绝不**触发真实写入；
- ``dry_run=False`` 必须再显式 ``confirm_write=True``，否则直接报错且不写；
- ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 存在时真实写强制降级/拒绝；
- 真实保存统一走 ``services.tiktok_publish.save_to_tiktok``（唯一写收口），
  并**复用步骤①创建的 client**：注入 fake 时绝不新建真实客户端；
- 写接口零自动重试；任何异常都转成 ``ok=False`` + 失败步骤，不静默吞掉；
- AI 失败只进 notes/warnings，不阻断预演（保留手填/商品原文继续校验）；
- 未绑定 TikTok 店铺时：``shops()`` 为空不阻断预演，只做提醒；真实保存由服务端
  拒绝并转成 ``TikTokNotReady`` 中文可操作提示。
"""
from __future__ import annotations

import copy
import os
import re
import time
from dataclasses import dataclass, field

from ..integrations.tiktok import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, OP_SCHEDULE, SAVE_MODE_AS_DRAFT,
    TikTokClient, TikTokError, TikTokNotReady,
)
from .tiktok_payload import (
    DEFAULT_LIMITS, OVERRIDE_KEYS, apply_attribute_suggestions, build_tiktok_payload,
    build_warnings, extract_detail_product, load_category_attributes,
    missing_mandatory_attributes, suggest_attributes, validate_tiktok_payload,
)
from .tiktok_prompts import (
    DESCRIPTION_MAX_CHARS, KEY_FEATURES_MAX, KEY_FEATURES_MIN,
    SEARCH_TERMS_TOTAL_MAX, TITLE_MAX, TITLE_MIN,
    build_description_prompt, build_key_features_prompt, build_search_terms_prompt,
    build_title_prompt, clean_for_site, clean_title, html_description,
    is_us_site, normalize_site, parse_key_features, parse_search_terms,
    site_meta as prompt_site_meta, strip_html,
)
from .tiktok_publish import make_client, save_to_tiktok

ENV_NO_SAVE = "AURORA_NO_SAVE"
ENV_TEST = "AURORA_TEST"

OP_LABELS = {
    OP_SAVE: "保存草稿",
    OP_MOVE_PENDING: "保存并移入待发布",
    OP_PUBLISH: "保存并发布",
    OP_SCHEDULE: "定时发布（v1 未支持）",
}
# 真实写支持的 op（§1.5）：1=保存、3=保存并移入待发布、2=发布（可 +AS_DRAFT）
SUPPORTED_OPS = (OP_SAVE, OP_MOVE_PENDING, OP_PUBLISH)
# 发布动作（校验用 for_publish=True，非 US 带五点/关键词会在发布前阻断）
PUBLISH_OPS = (OP_PUBLISH,)
SAVE_MODES = ("", SAVE_MODE_AS_DRAFT)

AI_FIELDS = ("title", "description", "key_features", "search_terms")
AI_LABELS = {
    "title": "标题",
    "description": "产品描述",
    "key_features": "五点描述",
    "search_terms": "搜索关键词",
}


@dataclass
class TikTokStep:
    """流水线单步结果（UI 直接渲染）。"""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class TikTokRunResult:
    """一次 TikTok 刊登预演/保存的结果（UI 直接读这些字段渲染）。"""

    product_id: str = ""
    ok: bool = False
    error: str = ""
    steps: list[TikTokStep] = field(default_factory=list)
    preview: bool = False
    saved: bool = False
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: str = ""
    description: str = ""
    key_features: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    site: str = ""
    site_name: str = ""
    currency: str = ""
    category_id: str = ""
    category_name: str = ""
    op: str = OP_SAVE
    banned_words: list[str] = field(default_factory=list)
    save_result: dict = field(default_factory=dict)
    ai_used: bool = False
    limits: dict = field(default_factory=dict)
    # 类目属性补全（UI 可据此渲染「属性」卡片：已补/仍缺/候选）
    attribute_stats: dict = field(default_factory=dict)
    attribute_suggestions: list[dict] = field(default_factory=list)
    missing_attributes: list[str] = field(default_factory=list)

    @property
    def incomplete_steps(self) -> list[TikTokStep]:
        return [s for s in self.steps if not s.ok]

    @property
    def completion_label(self) -> str:
        if self.preview:
            return "预演完成（未保存商品）" if self.ok else "预演失败（未保存商品）"
        if not self.ok:
            return "保存失败"
        return "保存成功" if self.saved else "未保存"

    def log(self) -> str:
        return "\n".join(f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}" for s in self.steps)


def forced_preview() -> bool:
    """AURORA_NO_SAVE / AURORA_TEST 存在时真实写必须降级为预演/拒绝。"""
    return bool(os.environ.get(ENV_NO_SAVE) or os.environ.get(ENV_TEST))


# ---------------- 内部工具 ----------------
def _cfg_get(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _step(res: TikTokRunResult, name: str, ok: bool, detail: str = "") -> None:
    res.steps.append(TikTokStep(name, ok, detail))
    from .progress import emit
    emit("TikTok · " + name, "完成" if ok else "失败", detail,
         level="OK" if ok else "ERROR", product=res.product_id)


def _fail(res: TikTokRunResult, name: str, error: str) -> TikTokRunResult:
    res.ok = False
    res.error = error
    _step(res, name, False, error)
    return res


def _short(value, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    return s[:limit] if len(s) > limit else s


def _int_or_none(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _str_list(value) -> list[str]:
    """任意值 → 字符串列表（AI/详情/overrides 输出）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value
                if item is not None and not isinstance(item, (dict, list))
                and str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    if raw[:1] == "[":
        try:
            import json as _json
            data = _json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list):
            return _str_list(data)
    return [part.strip() for part in re.split(r"[\r\n|;；]+", raw) if part.strip()]


def _merge_overrides(overrides) -> dict:
    """只保留 §3.4 约定的 snake_case overrides 键；空串/None 由 payload 层忽略。"""
    if not isinstance(overrides, dict):
        return {}
    return {key: value for key, value in overrides.items() if key in OVERRIDE_KEYS}


def _client_for(cfg, client) -> tuple[TikTokClient, bool]:
    """返回 ``(client, injected)``；注入时必须复用，绝不新建真实客户端。"""
    if client is not None:
        return client, True
    return make_client(cfg), False


def _read_detail(client, product_id: str) -> tuple[dict, dict]:
    """读详情并取出商品对象；失败转中文可操作错误（含未绑定店铺场景）。"""
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")
    try:
        detail = client.product_detail(pid)
    except TikTokNotReady as exc:
        raise TikTokNotReady(
            f"读取商品 {pid} 失败：账号未绑定/未选择 TikTok 店铺，"
            f"请先在店小秘『授权店铺』绑定 TikTok Shop 后重试。（{_short(exc, 160)}）"
        ) from exc
    except TikTokError as exc:
        raise TikTokError(
            f"读取商品 {pid} 失败：{_short(exc, 200)}"
            "（该账号可能未绑定 TikTok 店铺，或商品不在 TikTok 采集箱）") from exc
    if not detail:
        raise RuntimeError(
            f"读不到商品 {pid}（接口返回空）：该账号可能未绑定 TikTok 店铺，"
            "或 TikTok 采集箱中没有该商品")
    product = extract_detail_product(detail)
    if not product:
        raise RuntimeError(f"读不到商品 {pid}（详情里没有 product 字段）")
    return detail, product


def _resolve_shop_site(product: dict, cfg) -> tuple[str, str]:
    """店铺/站点解析：详情优先，cfg 兜底（不伪造 siteIds、不擅自切站点）。"""
    shop_id = (_text(product.get("shopId"))
               or _text(_cfg_get(cfg, "tiktok_shop_id", "")))
    site = normalize_site(product.get("site") or _cfg_get(cfg, "tiktok_site", "") or "")
    return shop_id, site


def _site_limits(client, site: str) -> dict:
    """站点限额：``client.site_limits(site)``；失败/空站点回 DEFAULT_LIMITS。"""
    limits = dict(DEFAULT_LIMITS)
    if not site:
        return limits
    try:
        raw = client.site_limits(site)
    except Exception:
        return limits
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                limits[key] = value
    return limits


def _is_global(product: dict, detail: dict | None = None) -> bool:
    """是否全球产品（tiktokGlobalProduct；只允许英文，不支持五点/关键词）。"""
    for node in (detail, product):
        if not isinstance(node, dict):
            continue
        for key in ("isGlobalMode", "isGlobal", "isGlobalProduct", "globalProduct"):
            if key not in node:
                continue
            value = node.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return bool(int(value))
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "yes", "y"):
                    return True
                if low in ("0", "false", "no", "n", ""):
                    return False
    return False


def _shop_check(client, shop_id: str) -> tuple[str, str]:
    """店铺校验（best-effort）：返回 ``(status, 中文说明)``。

    status ∈ ok / mismatch / missing / none / unknown；none/unknown 只提醒不阻断
    （本账号未绑定 TikTok 店铺时 shops() 返回 []，仍允许预演）。
    """
    try:
        shops = client.shops()
    except Exception as exc:  # noqa: BLE001
        return "unknown", f"无法读取 TikTok 店铺列表（跳过店铺校验）：{_short(exc)}"
    if not isinstance(shops, list) or not shops:
        return "none", ("未检测到 TikTok 店铺：可先预演；真实保存前请在店小秘"
                        "『授权店铺』绑定 TikTok Shop")
    ids = {str(shop.get("id") or "").strip() for shop in shops
           if isinstance(shop, dict) and str(shop.get("id") or "").strip()}
    if not shop_id:
        return "missing", "详情未带店铺（shopId 为空），请在页面选择店铺后重试"
    if shop_id not in ids:
        shown = "、".join(sorted(ids)[:5]) or "无"
        return "mismatch", f"店铺 {shop_id} 不在已授权 TikTok 店铺列表（可用：{shown}）"
    return "ok", f"店铺 {shop_id} 已授权"


def _context(cfg, product: dict, detail: dict, client) -> dict:
    """店铺/站点/限额/全球标记等上下文（只读）。"""
    shop_id, site = _resolve_shop_site(product, cfg)
    cfg_site = normalize_site(_cfg_get(cfg, "tiktok_site", "") or "")
    notes: list[str] = []
    if cfg_site and site and cfg_site != site:
        notes.append(f"配置站点 {cfg_site} 与商品站点 {site} 不一致："
                     f"流水线按商品站点 {site} 校验/保存（如需改站点请先重选类目）")
    meta = prompt_site_meta(site) if site else {}
    status, shop_detail = _shop_check(client, shop_id)
    return {
        "client": client,
        "shop_id": shop_id,
        "site": site,
        "meta": meta,
        "limits": _site_limits(client, site),
        "is_global": _is_global(product, detail),
        "shop_status": status,
        "shop_detail": shop_detail,
        "notes": notes,
    }


def _apply_site_ids(payload: dict, client, shop_id: str, site: str) -> str:
    """``siteIds`` 是不透明 siteShopId：**不伪造**，只有 client 能给出映射时才更新。

    返回更新的 siteShopId（空串=未找到映射，详情原样保留，不阻断预演）。
    """
    if not site or client is None:
        return ""
    try:
        rows = client.sites(shop_id or "")
    except Exception:
        return ""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if normalize_site(row.get("site")) != site:
            continue
        site_shop_id = _text(row.get("site_shop_id"))
        if site_shop_id:
            payload["siteIds"] = [site_shop_id]
            return site_shop_id
        return ""
    return ""


def _build_payload(cfg, product: dict, ov: dict, ctx: dict, op: str) -> tuple[dict, str]:
    """组装保存包（详情深拷贝透传 + overrides），并尝试映射 siteIds。"""
    payload = build_tiktok_payload(product, cfg, ov, site=ctx.get("site", ""),
                                   shop_id=ctx.get("shop_id", ""), op=op)
    try:
        payload["op"] = int(str(op).strip())
    except Exception:
        payload["op"] = 1
    site_shop_id = _apply_site_ids(payload, ctx.get("client"), ctx.get("shop_id", ""),
                                   ctx.get("site", ""))
    return payload, site_shop_id


# ---------------- 类目叶子校验（pipeline 负责，payload 不写 isLeaf） ----------------
def _find_is_leaf(node, category_id: str, depth: int = 0) -> int | None:
    """在详情/类目树里找 ``categoryId == category_id`` 且带 ``isLeaf`` 的节点。"""
    if category_id in ("", None) or node is None or depth > 6:
        return None
    if isinstance(node, dict):
        # 详情里类目节点可能用 id（内部 id）或 categoryId（平台类目 id）命名；
        # 只有节点真的带 isLeaf 时才用它判定，避免误把内部 id 当类目 id。
        node_cid = _text(node.get("categoryId") or node.get("platformCategoryId"))
        if node_cid != category_id and node.get("isLeaf") is not None:
            if _text(node.get("id")) == category_id:
                node_cid = category_id
        if node_cid == category_id and node.get("isLeaf") is not None:
            value = _int_or_none(node.get("isLeaf"))
            if value is not None:
                return value
        for value in node.values():
            found = _find_is_leaf(value, category_id, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(node, (list, tuple)):
        for item in list(node)[:200]:
            found = _find_is_leaf(item, category_id, depth + 1)
            if found is not None:
                return found
    return None


def _remote_leaf_state(client, site: str, category_id: str, shop_id: str) -> int | None:
    """best-effort：用类目搜索确认 isLeaf（搜索不中/失败 → None，不阻断）。"""
    if client is None or not site or not category_id:
        return None
    try:
        rows = client.search_categories(category_id, site, shop_id=shop_id or "-1")
    except Exception:
        return None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if _text(row.get("categoryId")) != category_id:
            continue
        value = _int_or_none(row.get("isLeaf"))
        if value is not None:
            return value
    return None


def _leaf_eval(detail: dict, product: dict, category_id: str, ctx: dict) -> dict:
    """判定类目是否叶子：``{category_id,state,source,issues,warning}``。

    state: 1=叶子 / 0=非叶子 / None=无法确认（只提醒，不阻断预演，保存前请人工确认）。
    """
    cid = _text(category_id)
    out = {"category_id": cid, "state": None, "source": "", "issues": [], "warning": ""}
    if not cid:
        # validate_tiktok_payload 会报「未选择叶子类目」，这里不重复阻断
        return out

    state = _find_is_leaf(detail, cid)
    source = "详情"
    if state is None:
        state = _find_is_leaf(product, cid)
        source = "详情商品"
    if state is None:
        rows = product.get("categoryList")
        if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
            last = rows[-1]
            if _text(last.get("categoryId")) == cid and last.get("isLeaf") is not None:
                state = _int_or_none(last.get("isLeaf"))
                source = "categoryList"
    if state is None:
        state = _remote_leaf_state(ctx.get("client"), ctx.get("site", ""), cid,
                                   ctx.get("shop_id", ""))
        source = "类目搜索"

    out["state"] = state
    out["source"] = source if state is not None else ""
    if state is None:
        out["warning"] = (f"无法确认类目 {cid} 是否为叶子类目（详情/类目搜索均未返回 isLeaf），"
                          "保存前请在类目面板确认已选最末级类目")
    elif state != 1:
        out["issues"].append(
            f"当前类目 {cid} 不是叶子类目（isLeaf={state}），请选择最末级类目")
    return out


def _leaf_step_detail(leaf: dict) -> str:
    cid = leaf.get("category_id") or ""
    state = leaf.get("state")
    if not cid:
        return "未选择类目（保存前校验会拦截）"
    if state is None:
        return leaf.get("warning") or f"类目 {cid} 无法确认叶子状态"
    if state != 1:
        return f"类目 {cid} 不是叶子类目（isLeaf={state}）"
    return f"类目 {cid} 为叶子类目（来源：{leaf.get('source') or '详情'}）"


# ---------------- 类目属性补全（只读 + 只补空值） ----------------
def _complete_attributes(payload: dict, ctx: dict, *, use_ai: bool = False,
                         cfg=None) -> tuple[dict, dict]:
    """加载类目属性 → 本地规则（可选 AI）建议 → **只补空值**写回 ``productAttributes``。

    规则（docs §1.4 / §5）：
    - 只读：仅调用 ``attributeList.json``（type=3），任何失败只进 warnings，不阻断预演；
    - **不覆盖用户值**：详情里 ``productAttributes`` 已有值的属性一律保留；
    - 推导来源：标题/描述/卖点/SKU/品牌/已有属性；枚举按 valueId/name 匹配；
    - 仍缺的必填属性 → 返回 ``remaining``（由 ``_evaluate`` 升级为阻断项）。

    返回 ``(payload, info)``，``info`` 含 ``attrs/suggestions/filled/remaining/
    conditional/warnings/detail/loaded/stats``。
    """
    info: dict = {
        "attrs": [], "suggestions": None, "filled": 0, "remaining": [],
        "conditional": [], "warnings": [], "detail": "", "loaded": False,
        "stats": {},
    }
    site = _text(ctx.get("site"))
    category_id = _text(payload.get("categoryId"))
    if not site or not category_id:
        info["detail"] = "缺少站点/类目，跳过属性补全"
        return payload, info

    errors: list[str] = []
    attrs = load_category_attributes(site, category_id, ctx.get("client"), errors=errors)
    for err in errors:
        info["warnings"].append(err)
    if not attrs:
        info["detail"] = "未取到类目属性（接口失败或该类目无属性），已跳过"
        return payload, info
    info["attrs"] = attrs
    info["loaded"] = True

    try:
        # payload 是「详情深拷贝 + overrides」，比原始 product 更完整（含 AI 文案）
        suggestion = suggest_attributes(payload, attrs, site, use_ai=use_ai, cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        info["warnings"].append(f"属性建议失败（不阻断）：{_short(exc, 160)}")
        info["detail"] = "属性建议异常，已跳过"
        return payload, info

    info["suggestions"] = suggestion
    try:
        new_payload = apply_attribute_suggestions(payload, suggestion)
    except Exception as exc:  # noqa: BLE001
        info["warnings"].append(f"属性写回失败（不阻断，productAttributes 原样透传）："
                                f"{_short(exc, 160)}")
        info["detail"] = "属性写回异常，已跳过"
        return payload, info

    rows = suggestion.get("suggestions") or []
    filled = len([s for s in rows if s.get("source") != "existing"])
    remaining = missing_mandatory_attributes(new_payload, attrs)
    blocking = [r for r in remaining if not r.get("isConditional")]
    conditional = [r for r in remaining if r.get("isConditional")]
    mandatory_total = len([a for a in attrs if a.get("isMandatory")])
    info.update({
        "filled": filled,
        "remaining": blocking,
        "conditional": conditional,
        "stats": dict(suggestion.get("coverage") or {}),
    })
    info["stats"].update({
        "loaded": True,
        "attr_total": len(attrs),
        "mandatory_total": mandatory_total,
        "filled": filled,
        "remaining": len(blocking),
        "conditional_remaining": len(conditional),
    })
    for note in suggestion.get("notes") or []:
        info["warnings"].append(str(note))
    if filled:
        info["warnings"].append(
            f"已按规则补全 {filled} 项类目属性（不覆盖已填值），保存前请核对取值")
    if conditional:
        labels = "、".join(str(r.get("label") or r.get("attrId")) for r in conditional[:5])
        info["warnings"].append(
            f"条件必填属性未填：{labels}（仅当关联属性取值满足条件时才必填）")
    info["detail"] = (f"类目属性 {len(attrs)} 项（必填 {mandatory_total} 项）"
                      f"；自动补全 {filled} 项；仍缺必填 {len(blocking)} 项")
    return new_payload, info


# ---------------- 校验 ----------------
def _evaluate(product: dict, detail: dict, payload: dict, ctx: dict, op: str,
              site_shop_id: str = "", leaf: dict | None = None,
              attrs_info: dict | None = None) -> tuple[list[str], list[str]]:
    """保存前校验：返回 (issues 阻断项, warnings 提醒项)。"""
    site = ctx.get("site", "")
    for_publish = op in PUBLISH_OPS
    issues = list(validate_tiktok_payload(
        payload, limits=ctx.get("limits"), site=site,
        is_global=bool(ctx.get("is_global")), for_publish=for_publish))
    warnings = list(build_warnings(payload, site=site))

    # 类目叶子（pipeline 负责；payload 不写 isLeaf）
    if leaf is None:
        leaf = _leaf_eval(detail, product, _text(payload.get("categoryId")), ctx)
    issues = list(leaf.get("issues") or []) + issues
    if leaf.get("warning"):
        warnings.append(leaf["warning"])

    # 类目必填属性（属性接口已成功加载时才阻断；失败/无属性只提醒）
    if isinstance(attrs_info, dict):
        missing = [r for r in (attrs_info.get("remaining") or []) if isinstance(r, dict)]
        if missing and attrs_info.get("loaded"):
            labels = "、".join(str(r.get("label") or r.get("attrId")) for r in missing[:6])
            more = f" 等 {len(missing)} 项" if len(missing) > 6 else ""
            issues.append(f"缺少 {len(missing)} 项必填类目属性：{labels}{more}"
                          "（请在属性面板选择/填写，或开启属性补全后确认）")
        for warn in attrs_info.get("warnings") or []:
            warnings.append(warn)

    # 店铺（none/unknown 只提醒；mismatch/missing 阻断）
    status = ctx.get("shop_status")
    if status in ("mismatch", "missing"):
        issues.append(ctx.get("shop_detail") or "店铺校验未通过")
    elif ctx.get("shop_detail"):
        warnings.append(ctx["shop_detail"])

    # 站点 siteShopId 映射（无店铺时正常；不阻断）
    if not site_shop_id and not payload.get("siteIds"):
        warnings.append("站点没有 siteShopId 映射（siteIds 为空）：未绑定店铺时属正常，"
                        "绑定后请在页面重新选择站点")

    for note in ctx.get("notes") or []:
        warnings.append(note)

    return _dedupe(issues), _dedupe(warnings)


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def _fill_result(res: TikTokRunResult, payload: dict, ctx: dict) -> None:
    """把保存包关键字段回填到结果对象（UI 渲染）。"""
    meta = ctx.get("meta") or {}
    res.title = _text(payload.get("productName") or payload.get("title"))
    res.description = str(payload.get("description") or "")
    res.key_features = _str_list(payload.get("keyProductFeatures"))
    res.search_terms = _str_list(payload.get("searchTerms"))
    res.site = _text(payload.get("site")) or _text(ctx.get("site"))
    res.site_name = _text(meta.get("country"))
    res.currency = _text(meta.get("currency"))
    res.category_id = _text(payload.get("categoryId"))
    rows = payload.get("categoryList")
    if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
        res.category_name = _text(rows[-1].get("categoryName"))
    res.limits = dict(ctx.get("limits") or {})


# ---------------- 只读准备 ----------------
def prepare_save(product_id: str, cfg, overrides: dict | None = None,
                 client=None, *, op: str = OP_SAVE,
                 use_ai_attributes: bool = False
                 ) -> tuple[dict, dict, list[str], list[str]]:
    """只读：读详情 → 店铺/站点 → 组装保存包 → 类目属性补全 → 校验。

    返回 ``(detail, payload, issues, warnings)``；不创建真实客户端（``client``
    非空时复用），绝不写。``op`` 只影响校验严格度（发布动作 ``for_publish=True``）；
    属性补全**只补空值、不覆盖用户值**（``use_ai_attributes`` 才走 AI 兜底，
    默认关闭以保持纯离线/零 AI 开销）。
    """
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")
    op = str(op or OP_SAVE).strip() or OP_SAVE
    c, _ = _client_for(cfg, client)
    detail, product = _read_detail(c, pid)
    ov = _merge_overrides(overrides)
    ctx = _context(cfg, product, detail, c)
    payload, site_shop_id = _build_payload(cfg, product, ov, ctx, op)
    payload, attrs_info = _complete_attributes(
        payload, ctx, use_ai=use_ai_attributes, cfg=cfg)
    issues, warnings = _evaluate(product, detail, payload, ctx, op, site_shop_id,
                                 attrs_info=attrs_info)
    return detail, payload, issues, warnings


# ---------------- AI 生成 ----------------
def _ai_client(cfg):
    from ..api import ApiClient
    return ApiClient(_cfg_get(cfg, "api_base", ""), _cfg_get(cfg, "api_key", ""),
                     _cfg_get(cfg, "timeout", 180))


def _ai_model(cfg) -> str:
    from . import model_of
    return model_of(cfg, "text")


def _ai_count(cfg, count: int = 0) -> int:
    """五点条数：cfg.tiktok_key_feature_count / count，收敛到 TikTok 要求的 3–5。"""
    try:
        n = int(count or _cfg_get(cfg, "tiktok_key_feature_count", KEY_FEATURES_MAX)
                or KEY_FEATURES_MAX)
    except Exception:
        n = KEY_FEATURES_MAX
    return max(KEY_FEATURES_MIN, min(n, KEY_FEATURES_MAX))


def _ai_fields(site: str) -> tuple[str, ...]:
    """AI 可生成字段：非 US 只生成标题/描述（五点/关键词仅 US/4Seller）。"""
    if is_us_site(site):
        return AI_FIELDS
    return ("title", "description")


def _empty_ai_value(name: str):
    return [] if name in ("key_features", "search_terms") else ""


def ai_generate_field(product: dict, cfg, field: str, site: str = "",
                      count: int = 5) -> tuple[object, list[str]]:
    """生成单个字段：title(str) / description(str) / key_features(list) / search_terms(list)。

    每字段独立 try/except（由调用方 ai_generate_all/流水线捕获）；失败返回空值 + 中文
    notes，不抛异常。AI 结果只做「形状收敛」，保存前仍由本地校验二次把关。
    """
    name = str(field or "").strip().lower()
    if name not in AI_FIELDS:
        return None, [f"未知字段：{field}（可选 {'/'.join(AI_FIELDS)}）"]
    site = normalize_site(site) or normalize_site(
        (product or {}).get("site") or _cfg_get(cfg, "tiktok_site", "") or "")
    want = _ai_count(cfg, count)
    builders = {
        "title": lambda: build_title_prompt(product, cfg, site),
        "description": lambda: build_description_prompt(product, cfg, site),
        "key_features": lambda: build_key_features_prompt(product, cfg, site, want),
        "search_terms": lambda: build_search_terms_prompt(product, cfg, site),
    }
    max_tokens = {"title": 320, "description": 2600,
                  "key_features": 1800, "search_terms": 260}
    label = AI_LABELS[name]
    try:
        client = _ai_client(cfg)
        text = client.chat(_ai_model(cfg), [
            {"role": "user", "content": builders[name]()},
        ], max_tokens=max_tokens[name])
    except Exception as exc:  # noqa: BLE001
        return _empty_ai_value(name), [f"{label}生成失败：{_short(exc)}"]

    text = str(text or "").strip()
    if not text:
        return _empty_ai_value(name), [f"{label}生成失败：模型返回空内容"]

    clean = clean_for_site(text, site)
    if name == "title":
        value = clean_title(clean, site, TITLE_MAX)
        if not value:
            return "", [f"{label}生成失败：清洗后为空"]
        notes = [f"已生成（{len(value)} 字符）"]
        if len(value) < TITLE_MIN:
            notes.append(f"标题仅 {len(value)} 字符（TikTok 要求 ≥{TITLE_MIN}），需人工补足")
        return value, notes

    if name == "description":
        value = html_description(clean, DESCRIPTION_MAX_CHARS)
        if not value:
            return "", [f"{label}生成失败：清洗后为空"]
        notes = [f"已生成（{len(value)} 字符 HTML）"]
        if not re.search(r"<ul\b|<li\b", value, re.I):
            notes.append("描述缺少参数列表（建议补充 <ul><li> 规格参数）")
        return value, notes

    if name == "key_features":
        items = parse_key_features(clean, want)
        notes = [f"已生成 {len(items)} 条"]
        if len(items) < KEY_FEATURES_MIN:
            notes.append(f"仅 {len(items)} 条（TikTok 要求 {KEY_FEATURES_MIN}–"
                         f"{KEY_FEATURES_MAX} 条），需人工补齐")
        return items, notes

    # search_terms
    terms = parse_search_terms(clean)
    kept: list[str] = []
    for term in terms:
        if len(" ".join(kept + [term])) > SEARCH_TERMS_TOTAL_MAX:
            break
        kept.append(term)
    return kept, [f"已生成 {len(kept)} 个关键词"]


def ai_generate_all(product: dict, cfg, site: str = "",
                    count: int = 5) -> tuple[dict, list[str]]:
    """一次生成适用字段（非 US 只生成标题/描述）；每字段独立 try/except，失败只进 notes。"""
    out: dict = {"title": "", "description": "", "key_features": [], "search_terms": []}
    notes: list[str] = []
    for name in _ai_fields(site):
        try:
            value, field_notes = ai_generate_field(product, cfg, name, site, count)
            out[name] = value
            notes.extend(f"{AI_LABELS[name]}：{n}" for n in field_notes)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{AI_LABELS[name]}：生成异常：{_short(exc)}")
    return out, notes


def _current_values(product: dict, ov: dict) -> dict:
    """当前生效的文案（overrides 优先，空则不覆盖商品原值）——用于「已有值不覆盖」。"""
    product = product if isinstance(product, dict) else {}

    def pick(ov_key: str, prod_keys: tuple[str, ...]):
        value = ov.get(ov_key)
        if not _blank(value):
            return value
        for key in prod_keys:
            value = product.get(key)
            if not _blank(value):
                return value
        return None

    title = pick("title", ("productName", "title")) or ""
    description = pick("description", ("description",)) or ""
    features = pick("key_features", ("keyProductFeatures", "key_features"))
    terms = pick("search_terms", ("searchTerms", "search_terms"))
    return {
        "title": _text(title),
        "description": str(description or ""),
        "key_features": _str_list(features),
        "search_terms": _str_list(terms),
    }


def _missing_ai_fields(current: dict, site: str) -> list[str]:
    missing: list[str] = []
    for name in _ai_fields(site):
        value = current.get(name)
        if name == "description":
            if not strip_html(value or ""):
                missing.append(name)
        elif isinstance(value, (list, tuple)):
            if not value:
                missing.append(name)
        elif not _text(value):
            missing.append(name)
    return missing


def _product_for_ai(product: dict, current: dict, site: str) -> dict:
    p = copy.deepcopy(product) if isinstance(product, dict) else {}
    p["productName"] = current.get("title") or ""
    p["title"] = current.get("title") or ""
    p["description"] = current.get("description") or ""
    p["keyProductFeatures"] = list(current.get("key_features") or [])
    p["searchTerms"] = list(current.get("search_terms") or [])
    if site:
        p["site"] = site
    return p


# ---------------- 主流程 ----------------
def run_tiktok_product(product_id: str, cfg, overrides: dict | None = None,
                       *, dry_run: bool = True, use_ai: bool = False,
                       op: str = OP_SAVE, save_mode: str = "",
                       client: TikTokClient | None = None,
                       confirm_write: bool = False,
                       use_ai_attributes: bool = False, before_submit=None) -> TikTokRunResult:
    """读详情 → 店铺/站点/限额 → 类目 →（AI）→ 保存包 → 属性补全 → 校验 → 预演/保存。

    默认 ``dry_run=True``：只预演，绝不调用保存接口。
    真实保存需同时满足：``dry_run=False`` + **显式 ``confirm_write=True``**（防脚本误写）
    + 非 ``forced_preview()`` + ``core.guard.require`` 通过 + 无校验 issues，才走
    ``tiktok_publish.save_to_tiktok``（唯一写收口，并复用步骤①的 client）。
    ``use_ai_attributes=True`` 才对仍缺的必填属性做**一次**批量 AI 兜底（默认关闭）。
    """
    pid = str(product_id or "").strip()
    op = str(op or OP_SAVE).strip() or OP_SAVE
    save_mode = str(save_mode or "").strip()
    res = TikTokRunResult(product_id=pid, op=op, preview=bool(dry_run))
    try:
        # ---- 参数与写入确认（全部在创建客户端之前）----
        if not pid:
            return _fail(res, "参数校验", "缺少 product_id")
        if op == OP_SCHEDULE:
            return _fail(res, "参数校验",
                         "定时发布（op=5）v1 未支持：需要 dxmScheduleTimeStr 预约时间")
        if op not in SUPPORTED_OPS:
            return _fail(res, "参数校验", f"未知操作 op={op}（v1 仅支持 1/2/3）")
        if save_mode not in SAVE_MODES:
            return _fail(res, "参数校验", f"未知 saveMode={save_mode}（仅支持空或 AS_DRAFT）")
        if save_mode == SAVE_MODE_AS_DRAFT and op != OP_PUBLISH:
            return _fail(res, "参数校验", "saveMode=AS_DRAFT 仅用于发布动作（op=2）")
        if not dry_run and not confirm_write:
            msg = ("真实保存需要显式 confirm_write=True（防脚本误写）；"
                   "预演请用 dry_run=True")
            res.saved = False
            res.preview = False
            res.error = msg
            _step(res, "写入确认", False, msg)
            return res

        # ---- ① 连接 + 读详情 ----
        try:
            c, injected = _client_for(cfg, client)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "连接 TikTok", f"创建客户端失败：{_short(exc)}")
        _step(res, "连接 TikTok", True,
              "复用注入的客户端（未新建真实客户端）" if injected
              else "Cookie 可用，客户端已创建")
        try:
            detail, product = _read_detail(c, pid)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "读取商品详情", _short(exc, 300))
        _step(res, "读取商品详情", True,
              f"{_short(product.get('productName') or product.get('title'), 60)}"
              f" · 类目 {_text(product.get('categoryId')) or '未分类'}")

        # ---- ② 店铺 + 站点 + 限额 ----
        ov = _merge_overrides(overrides)
        ctx = _context(cfg, product, detail, c)
        res.site = ctx["site"]
        res.site_name = _text((ctx.get("meta") or {}).get("country"))
        res.currency = _text((ctx.get("meta") or {}).get("currency"))
        res.limits = dict(ctx.get("limits") or {})
        if ctx["site"]:
            _step(res, "店铺与站点", True,
                  f"站点 {ctx['site']} · {res.site_name or '未知'} · "
                  f"{res.currency or '未知币种'} · "
                  f"{_text((ctx.get('meta') or {}).get('lang_name')) or '英语'}；"
                  f"{ctx.get('shop_detail') or '店铺未知'}；"
                  f"限额 标题 {res.limits.get('nameMin')}–{res.limits.get('nameMax')}"
                  f"/描述 {res.limits.get('descMax')}"
                  f"/主图 {res.limits.get('imgCountMin')}–{res.limits.get('imgCountMax')}")
        else:
            res.issues.append("未选择站点（site 为空）")
            _step(res, "店铺与站点", False,
                  f"商品缺少 site，无法确定站点/币种/限额；{ctx.get('shop_detail') or ''}")

        # ---- ③ 类目校验（叶子判定由 pipeline 负责） ----
        category_id = (_text(ov.get("category_id"))
                       or _text(product.get("categoryId")))
        leaf = _leaf_eval(detail, product, category_id, ctx)
        res.category_id = category_id
        if leaf.get("issues"):
            res.issues.extend(leaf["issues"])
            _step(res, "类目校验", False, _leaf_step_detail(leaf))
        else:
            if leaf.get("warning"):
                res.warnings.append(leaf["warning"])
            _step(res, "类目校验", True, _leaf_step_detail(leaf))

        # ---- ④ AI 生成（use_ai；已有值不覆盖）----
        if use_ai:
            current = _current_values(product, ov)
            missing = _missing_ai_fields(current, ctx["site"])
            if not missing:
                _step(res, "AI 文案", True, "已有文案，跳过 AI 生成（不覆盖手填内容）")
            else:
                ai_product = _product_for_ai(product, current, ctx["site"])
                ai_notes: list[str] = []
                filled = 0
                for name in missing:
                    value, notes = ai_generate_field(
                        ai_product, cfg, name, ctx["site"], _ai_count(cfg, 0))
                    ai_notes.extend(f"{AI_LABELS[name]}：{n}" for n in notes)
                    if name in ("key_features", "search_terms"):
                        if value:
                            ov[name] = list(value)
                            filled += 1
                    elif value:
                        ov[name] = value
                        filled += 1
                res.ai_used = True
                for note in ai_notes[:8]:
                    res.warnings.append("AI：" + note)
                failed = [n for n in ai_notes if "失败" in n or "异常" in n]
                _step(res, "AI 文案", not failed,
                      f"已生成 {filled} 项" +
                      (f"；{len(failed)} 项失败，已保留手填内容，不阻断" if failed else ""))
        else:
            _step(res, "AI 文案", True, "未启用 AI，使用商品原文/手填内容")

        # ---- ⑤ 组装保存包 ----
        try:
            payload, site_shop_id = _build_payload(cfg, product, ov, ctx, op)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "组装保存包", f"build_tiktok_payload 失败：{_short(exc)}")
        _fill_result(res, payload, ctx)
        res.op = op

        # ---- ⑤b 类目属性补全（只读 + 只补空值；失败只提醒）----
        try:
            payload, attrs_info = _complete_attributes(
                payload, ctx, use_ai=use_ai_attributes, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            attrs_info = {"loaded": False, "warnings": [f"属性补全异常（不阻断）：{_short(exc)}"],
                          "stats": {}, "suggestions": None, "remaining": []}
        res.attribute_stats = dict(attrs_info.get("stats") or {})
        res.attribute_suggestions = list(
            (attrs_info.get("suggestions") or {}).get("suggestions") or [])
        res.missing_attributes = [
            str(r.get("label") or r.get("attrId")) for r in (attrs_info.get("remaining") or [])]
        for warn in attrs_info.get("warnings") or []:
            res.warnings.append(warn)
        _step(res, "类目属性补全", not attrs_info.get("remaining"),
              attrs_info.get("detail") or "已跳过")
        # 属性补全结果回写 overrides：真实保存会在 save_to_tiktok → prepare_save 里
        # 用同一份 overrides 重新组装保存包；不回写会导致 AI/规则补出的属性在保存时丢失。
        if attrs_info.get("loaded") and _text(payload.get("productAttributes")):
            ov["product_attributes"] = payload.get("productAttributes")

        # ---- ⑥ 必填/限额/合规校验 ----
        try:
            eval_leaf = leaf
            payload_cid = _text(payload.get("categoryId"))
            if payload_cid != _text(leaf.get("category_id")):
                eval_leaf = _leaf_eval(detail, product, payload_cid, ctx)
            issues, warnings = _evaluate(product, detail, payload, ctx, op,
                                         site_shop_id, eval_leaf, attrs_info)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存前校验", f"校验异常：{_short(exc)}")
        res.issues = _dedupe(list(res.issues) + list(issues))
        res.warnings = _dedupe(list(res.warnings) + list(warnings))
        if res.issues:
            msg = f"{len(res.issues)} 项未通过：" + "；".join(res.issues[:6])
            _step(res, "保存前校验", False, msg)
            res.ok = False
            res.error = "保存前校验未通过：" + "；".join(res.issues)
            res.preview = bool(dry_run) or forced_preview()
            return res
        _step(res, "保存前校验", True, "必填项/限额/合规项全部通过")

        # ---- ⑦ 违禁词（只读，仅提示）----
        try:
            banned = c.check_banned_words(title=res.title, description=res.description)
            if banned:
                res.banned_words = list(banned)
                warn = "违禁词命中：" + "、".join(banned[:8]) + "（建议替换，不阻断）"
                res.warnings.append(warn)
                _step(res, "违禁词检查", True, warn)
            else:
                _step(res, "违禁词检查", True, "未命中")
        except Exception as exc:  # noqa: BLE001
            res.warnings.append(f"违禁词检查失败（不影响预演/保存）：{_short(exc)}")
            _step(res, "违禁词检查", True, "跳过：" + _short(exc))

        # ---- ⑧ 预演 / 保存 ----
        save_detail = (f"op={op}（{OP_LABELS[op]}）"
                       + (f" · saveMode={save_mode}" if save_mode else "")
                       + f" · 载荷 {len(payload)} 个字段")
        if dry_run:
            res.preview = True
            res.ok = True
            _step(res, "预演", True, f"{save_detail}；dry-run 未调用保存接口")
            _step(res, "完成", True, "预演完成（未保存商品）")
            return res

        # 真实保存前的双保险：dry_run=False 但命中验收环境 → 明确「拒绝真实保存」
        if forced_preview():
            res.preview = True
            res.saved = False
            res.ok = False
            res.error = ("当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），"
                         "已拒绝真实保存")
            res.warnings.append("检测到 AURORA_NO_SAVE/AURORA_TEST：已拒绝真实保存")
            _step(res, "安全护栏", False, res.error)
            return res

        try:
            from ..core.guard import require as _require_license
            _require_license(cfg)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "授权校验", f"授权校验未通过：{_short(exc)}")
        _step(res, "授权校验", True, "通过")

        # 唯一写收口：save_to_tiktok（内部再校验 forced_preview/confirm/guard/issues）
        # 复用步骤①创建的 client：注入 fake 时绝不新建真实客户端。
        try:
            submit_options = {"before_submit": before_submit} if before_submit else {}
            saved = save_to_tiktok(pid, cfg, overrides=ov, op=op,
                                   save_mode=save_mode, client=c,
                                   confirm_write=True, **submit_options)
        except TikTokNotReady as exc:
            return _fail(res, "保存到 TikTok", _short(exc, 300))
        except ValueError as exc:
            return _fail(res, "保存到 TikTok", f"保存前校验未通过：{_short(exc, 300)}")
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存到 TikTok", f"保存失败：{_short(exc, 300)}")
        res.save_result = saved.get("result") if isinstance(saved, dict) else {}
        if isinstance(res.save_result, dict):
            code = res.save_result.get("code")
            if code not in (0, "0"):
                return _fail(res, "保存到 TikTok",
                             f"保存失败：{res.save_result.get('msg') or f'code={code}'}")
        res.saved = True
        res.ok = True
        msg = ""
        if isinstance(res.save_result, dict):
            data = res.save_result.get("data")
            if isinstance(data, dict):
                msg = _text(data.get("msg"))
            msg = msg or _text(res.save_result.get("msg"))
        _step(res, "保存到 TikTok", True,
              f"{OP_LABELS[op]}成功" + (f"：{_short(msg, 110)}" if msg else ""))

        if op in (OP_PUBLISH, OP_MOVE_PENDING):
            from .publish_outcome import judge
            _final = None
            for _i in range(5):
                time.sleep(2)
                try:
                    _raw = client.product_detail(pid)
                except Exception:
                    continue
                _p = _raw.get("product", _raw) if isinstance(_raw, dict) else {}
                if not isinstance(_p, dict) or not any(
                        k in _p for k in ("dxmState", "dxmOfflineState", "productStatusType", "errMsg")):
                    _final = None
                    break
                _st = str(_p.get("dxmOfflineState") or _p.get("productStatusType")
                          or _p.get("status") or _p.get("dxmState") or "")
                _final = judge("publish" if op == OP_PUBLISH else "save_to_waiting",
                               _st, str(_p.get("errMsg") or ""))
                if _final.get("category") in ("pending", "auditing", "online", "failed"):
                    break
            if _final is not None and not _final.get("ok"):
                _step(res, "发布结果", False,
                      f"已提交但未达成目标：{_final.get('label')}（{_final.get('state') or '状态未知'}）")
                res.ok = False
                res.error = f"发布结果未确认：{_final.get('label')}"
                return res
            if _final is not None:
                _step(res, "发布结果", True, _final.get("label") or "已确认")
        _step(res, "完成", True, "商品已保存/提交")
        return res
    except Exception as exc:  # noqa: BLE001
        return _fail(res, "流水线异常", f"{type(exc).__name__}: {_short(exc, 200)}")


__all__ = [
    "ENV_NO_SAVE", "ENV_TEST", "OP_LABELS", "SUPPORTED_OPS", "PUBLISH_OPS",
    "SAVE_MODES", "AI_FIELDS", "AI_LABELS",
    "TikTokStep", "TikTokRunResult", "forced_preview", "prepare_save",
    "run_tiktok_product", "ai_generate_field", "ai_generate_all",
]
