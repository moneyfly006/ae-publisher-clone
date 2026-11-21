"""Amazon 刊登流水线（服务层，不含 UI）。

一条链路：读 editSp 详情 → 站点/币种 → 类目校验 →（可选）AI 文案 →
组装 39 键保存包 → 必填/合规校验 → 违禁词提示 → 预演或保存。

安全语义
--------
- ``dry_run=True``（默认）绝不调用 ``save_product`` / ``save_to_amazon``；
- ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 存在时任何真实写都强制降级为预演；
- 真实保存必须通过 ``core.guard.require(cfg)``，并统一走
  ``services.amazon_publish.save_to_amazon``（唯一写收口）；
- 任何异常都转成 ``ok=False`` + 失败步骤，不静默吞掉；
- AI 失败只记 notes/warnings，不阻断预演（保留手填内容继续校验）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .amazon_payload import (
    BULLET_MAX_ITEMS, DESCRIPTION_MAX_BYTES, SEARCH_TERMS_MAX_CHARS, TITLE_MAX,
    build_amazon_payload, build_warnings, join_bullets, split_bullets,
    validate_amazon_payload,
)
from .amazon_publish import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, make_client,
)
from .amazon_prompts import (
    BULLET_DEFAULT_COUNT, build_bullets_prompt, build_description_prompt,
    build_search_terms_prompt, build_title_prompt, clean_bullet, clean_for_site,
    clean_title, parse_bullets, parse_search_terms, sanitize_description,
    site_meta as prompt_site_meta,
)

ENV_NO_SAVE = "AURORA_NO_SAVE"
ENV_TEST = "AURORA_TEST"

OP_LABELS = {
    OP_SAVE: "保存草稿",
    OP_MOVE_PENDING: "保存并移入待发布",
    OP_PUBLISH: "保存并发布",
}

AI_FIELDS = ("title", "bullets", "description", "search_terms")
AI_LABELS = {"title": "标题", "bullets": "五点描述", "description": "产品描述",
             "search_terms": "Search Terms"}


@dataclass
class AmazonStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class AmazonRunResult:
    """一次 Amazon 刊登预演/保存的结果（UI 直接读这些字段渲染）。"""

    product_id: str = ""
    ok: bool = False
    error: str = ""
    steps: list[AmazonStep] = field(default_factory=list)
    preview: bool = False
    saved: bool = False
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    description: str = ""
    search_terms: str = ""
    site: str = ""
    site_code: str = ""
    currency: str = ""
    category_id: str = ""
    op: str = OP_SAVE
    banned_words: list[str] = field(default_factory=list)
    save_result: dict = field(default_factory=dict)
    ai_used: bool = False

    @property
    def incomplete_steps(self) -> list[AmazonStep]:
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
    """AURORA_NO_SAVE / AURORA_TEST 存在时真实写必须降级为预演。"""
    return bool(os.environ.get(ENV_NO_SAVE) or os.environ.get(ENV_TEST))


# ---------------- 内部工具 ----------------
def _cfg_get(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _step(res: AmazonRunResult, name: str, ok: bool, detail: str = "") -> None:
    res.steps.append(AmazonStep(name, ok, detail))


def _fail(res: AmazonRunResult, name: str, error: str) -> AmazonRunResult:
    res.ok = False
    res.error = error
    _step(res, name, False, error)
    return res


def _short(text, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:limit] if len(s) > limit else s


def _product_of(detail: dict) -> dict:
    """editSp 返回 data → product（兼容直接给 product 的桩）。"""
    if not isinstance(detail, dict):
        return {}
    product = detail.get("product")
    if isinstance(product, dict) and product:
        return dict(product)
    return dict(detail)


def _site_code(meta: dict) -> str:
    return str((meta or {}).get("code") or "")


def _fill_result(res: AmazonRunResult, payload: dict) -> None:
    """把保存包里的关键字段回填到结果对象。"""
    if not isinstance(payload, dict):
        return
    res.title = str(payload.get("title") or "")
    res.bullets = split_bullets(payload.get("bulletPoint") or "")
    res.description = str(payload.get("description") or "")
    res.search_terms = str(payload.get("searchTerms") or "")
    res.site = str(payload.get("marketPlaceId") or "") or res.site
    res.currency = str(payload.get("currency") or "") or res.currency
    res.category_id = str(payload.get("categoryId") or "") or res.category_id
    meta = prompt_site_meta(res.site) if res.site else {}
    if not res.site_code:
        res.site_code = _site_code(meta)


def _merge_overrides(overrides: dict | None) -> dict:
    """只保留 §3.2.0 约定的 overrides 键（空串视作未传）。"""
    from .amazon_payload import OVERRIDE_KEYS
    src = overrides or {}
    if not isinstance(src, dict):
        return {}
    return {k: v for k, v in src.items() if k in OVERRIDE_KEYS}


def _current_text_fields(product: dict, ov: dict) -> dict:
    """取当前生效的文案字段（overrides 优先，空则不覆盖商品原值）。"""
    title = str(ov.get("title") or product.get("title") or "")
    bullets = ov.get("bullets")
    if not bullets:
        bullets = split_bullets(product.get("bulletPoint") or "")
    if isinstance(bullets, str):
        bullets = split_bullets(bullets)
    desc = str(ov.get("description") or product.get("description") or "")
    terms = str(ov.get("search_terms") or product.get("searchTerms") or "")
    return {"title": title, "bullets": list(bullets or []),
            "description": desc, "search_terms": terms}


def _product_for_ai(product: dict, ov: dict, detail: dict | None = None) -> dict:
    """给 AI 的上下文：商品事实 + 当前表单值 + 站点等。"""
    p = dict(product or {})
    text = _current_text_fields(p, ov)
    p["title"] = text["title"]
    p["bulletPoint"] = join_bullets(text["bullets"])
    p["description"] = text["description"]
    p["searchTerms"] = text["search_terms"]
    for key in ("brand", "manufacturer", "sku"):
        if ov.get(key):
            p[key] = ov[key]
    if detail:
        p.setdefault("site", detail.get("site"))
        p.setdefault("productType", detail.get("productType"))
    return p


# ---------------- AI 生成 ----------------
def _ai_client(cfg):
    from ..api import ApiClient
    return ApiClient(_cfg_get(cfg, "api_base", ""), _cfg_get(cfg, "api_key", ""),
                     _cfg_get(cfg, "timeout", 180))


def _ai_model(cfg) -> str:
    from . import model_of
    return model_of(cfg, "text")


def _marketplace_for(product: dict, cfg, marketplace_id: str = "") -> str:
    mp = str(marketplace_id or "").strip()
    if not mp:
        mp = str(_cfg_get(cfg, "amazon_site", "") or "").strip()
    if not mp:
        mp = str((product or {}).get("marketPlaceId") or "").strip()
    return mp


def _bullets_count(cfg, count: int = 0) -> int:
    try:
        n = int(count or _cfg_get(cfg, "amazon_bullet_count", BULLET_DEFAULT_COUNT)
                or BULLET_DEFAULT_COUNT)
    except Exception:
        n = BULLET_DEFAULT_COUNT
    return max(1, min(n, BULLET_MAX_ITEMS))


def ai_generate_field(product: dict, cfg, field: str,
                      marketplace_id: str = "", count: int = 5) -> tuple[object, list[str]]:
    """生成单个字段：title(str) / bullets(list[str]) / description(str) / search_terms(str)。"""
    name = str(field or "").strip().lower()
    if name not in AI_FIELDS:
        return None, [f"未知字段：{field}（可选 {'/'.join(AI_FIELDS)}）"]
    mp = _marketplace_for(product or {}, cfg, marketplace_id)
    meta = prompt_site_meta(mp) if mp else {}
    want = _bullets_count(cfg, count)
    notes: list[str] = []
    if not mp:
        notes.append("未读取到站点，按英语处理")
    builders = {
        "title": lambda: build_title_prompt(product, cfg, mp),
        "bullets": lambda: build_bullets_prompt(product, cfg, mp, want),
        "description": lambda: build_description_prompt(product, cfg, mp),
        "search_terms": lambda: build_search_terms_prompt(product, cfg, mp),
    }
    max_tokens = {"title": 320, "bullets": 1800, "description": 2200, "search_terms": 220}
    try:
        client = _ai_client(cfg)
        text = client.chat(_ai_model(cfg), [
            {"role": "user", "content": builders[name]()},
        ], max_tokens=max_tokens[name])
    except Exception as exc:  # noqa: BLE001
        return _empty_ai_value(name), [f"{AI_LABELS[name]}生成失败：{_short(exc)}"]

    text = str(text or "").strip()
    if not text:
        return _empty_ai_value(name), [f"{AI_LABELS[name]}生成失败：模型返回空内容"]

    if name == "title":
        value = clean_title(clean_for_site(text, mp), TITLE_MAX)
        if not value:
            return "", [f"{AI_LABELS[name]}生成失败：清洗后为空"]
        if len(value) >= TITLE_MAX:
            notes.append(f"标题已按 {TITLE_MAX} 字符词边界截断")
        notes.append(f"已生成（{len(value)} 字符）")
        return value, notes

    if name == "bullets":
        parsed = parse_bullets(text, want)
        cleaned = [clean_bullet(clean_for_site(b, mp)) for b in parsed]
        cleaned = [b for b in cleaned if b][:BULLET_MAX_ITEMS]
        if len(cleaned) < want:
            notes.append(f"AI 只解析出 {len(cleaned)} 条（要求 {want} 条），请人工补齐")
        notes.append(f"已生成 {len(cleaned)} 条")
        return cleaned, notes

    if name == "description":
        value = sanitize_description(clean_for_site(text, mp), DESCRIPTION_MAX_BYTES)
        if not value:
            return "", [f"{AI_LABELS[name]}生成失败：清洗后为空"]
        notes.append(f"已生成（{len(value.encode('utf-8'))} 字节 HTML）")
        return value, notes

    # search_terms
    value = parse_search_terms(clean_for_site(text, mp))
    if len(value) > SEARCH_TERMS_MAX_CHARS:
        value = value[:SEARCH_TERMS_MAX_CHARS]
    notes.append(f"已生成（{len(value)} 字符 / {len(value.encode('utf-8'))} 字节）")
    return value, notes


def _empty_ai_value(name: str):
    if name == "bullets":
        return []
    return ""


def ai_generate_all(product: dict, cfg, marketplace_id: str = "",
                    count: int = 5) -> tuple[dict, list[str]]:
    """一次生成 4 个字段；每字段独立 try/except，失败只进 notes。"""
    out: dict = {"title": "", "bullets": [], "description": "", "search_terms": ""}
    notes: list[str] = []
    for name in AI_FIELDS:
        try:
            value, field_notes = ai_generate_field(product, cfg, name, marketplace_id, count)
            out[name] = value
            notes.extend(f"{AI_LABELS[name]}：{n}" for n in field_notes)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{AI_LABELS[name]}：生成异常：{_short(exc)}")
    return out, notes


# ---------------- 只读准备 ----------------
def prepare_save(product_id: str, cfg, overrides: dict | None = None,
                 client=None) -> tuple[dict, dict, list[str], list[str]]:
    """只读：读详情 → 组装保存包 → 校验；返回 (detail, payload, issues, warnings)。"""
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")
    c = client if client is not None else make_client(cfg)
    detail = c.product_detail(pid)
    if not detail:
        raise RuntimeError(f"读不到商品 {pid}（可能已删除/不属于当前店铺）")
    product = _product_of(detail)
    ov = _merge_overrides(overrides)
    mp = str(ov.get("marketplace_id") or product.get("marketPlaceId") or "")
    shop_id = str(product.get("shopId") or _cfg_get(cfg, "amazon_shop_id", "") or "")
    currency = str(ov.get("currency") or "").strip()
    if not currency and mp and shop_id:
        try:
            currency = c.currency(shop_id, mp) or ""
        except Exception:
            currency = ""
    if not currency:
        currency = str(product.get("standardPriceCurrency") or "")
    payload = build_amazon_payload(product, cfg, ov, currency=currency)
    meta = prompt_site_meta(str(payload.get("marketPlaceId") or mp))
    issues = validate_amazon_payload(payload, product=product, site_meta=meta)
    warnings = build_warnings(payload, product=product)
    return detail, payload, issues, warnings


# ---------------- 主流程 ----------------
def run_amazon_product(product_id: str, cfg, overrides: dict | None = None,
                       *, dry_run: bool = True, use_ai: bool = False,
                       op: str = OP_SAVE, client=None,
                       confirm_write: bool = False) -> AmazonRunResult:
    """读详情 → 站点/币种 → 类目 →（AI）→ 保存包 → 校验 → 违禁词 → 预演/保存。

    默认 ``dry_run=True``：只预演，绝不调用保存接口。
    真实保存需同时满足：``dry_run=False`` + **显式 ``confirm_write=True``**（防脚本误写）
    + 非 ``forced_preview()`` + ``core.guard.require`` 通过，才走
    ``amazon_publish.save_to_amazon``（唯一写收口，并复用步骤①的 client，不再新建客户端）。
    """
    pid = str(product_id or "").strip()
    op = str(op or OP_SAVE)
    res = AmazonRunResult(product_id=pid, op=op, preview=bool(dry_run))
    try:
        if not pid:
            return _fail(res, "参数校验", "缺少 product_id")
        if op not in OP_LABELS:
            return _fail(res, "参数校验", f"未知操作 op={op}（可选 1/2/3）")
        # 防脚本误写：dry_run=False 必须显式 confirm_write=True 才允许进入真实保存分支
        if not dry_run and not confirm_write:
            msg = ("真实保存需要显式 confirm_write=True（防脚本误写）；"
                   "预演请用 dry_run=True")
            res.ok = False
            res.saved = False
            res.preview = False
            res.error = msg
            _step(res, "写入确认", False, msg)
            return res

        # ① 连接 + 读详情
        try:
            c = client if client is not None else make_client(cfg)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "连接 Amazon", f"创建客户端失败：{_short(exc)}")
        _step(res, "连接 Amazon", True, "Cookie 可用，客户端已创建")
        try:
            detail = c.product_detail(pid)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "读取商品详情", f"读取商品 {pid} 失败：{_short(exc)}")
        product = _product_of(detail)
        if not product:
            return _fail(res, "读取商品详情", f"读不到商品 {pid}（接口返回空）")
        _step(res, "读取商品详情", True,
              f"{_short(product.get('title'), 60)} · 类目 {product.get('categoryId') or '未分类'}")

        # ② 站点 + 币种
        ov = _merge_overrides(overrides)
        mp = str(ov.get("marketplace_id") or product.get("marketPlaceId") or "")
        meta = prompt_site_meta(mp) if mp else {}
        res.site = mp
        res.site_code = _site_code(meta)
        shop_id = str(product.get("shopId") or _cfg_get(cfg, "amazon_shop_id", "") or "")
        currency = str(ov.get("currency") or "").strip()
        if not currency and mp and shop_id:
            try:
                currency = c.currency(shop_id, mp) or ""
            except Exception:
                currency = ""
        res.currency = currency or str(product.get("standardPriceCurrency") or "")
        if mp:
            _step(res, "站点与币种", True,
                  f"站点 {res.site_code or mp} · {meta.get('country') or ''} · "
                  f"{res.currency or '未知币种'} · {meta.get('lang_name') or ''}")
        else:
            _step(res, "站点与币种", False, "商品缺少 marketPlaceId，无法确定站点")
            res.issues.append("marketPlaceId 为空")

        # ③ 类目校验（显式路径时远程校验；否则沿用商品类目）
        category_path = str(ov.get("category_path") or "").strip()
        site_code = res.site_code
        if category_path and site_code:
            try:
                node = c.category_by_path(site_code, category_path)
            except Exception as exc:  # noqa: BLE001
                msg = f"类目路径不属于站点 {site_code} 或校验失败：{_short(exc)}"
                res.issues.append(msg)
                _step(res, "类目校验", False, msg)
            else:
                if node:
                    name = str(node.get("chineseName") or node.get("categoryName") or "")
                    _step(res, "类目校验", True,
                          f"{category_path} → {name or node.get('categoryId') or '已确认'}")
                else:
                    msg = f"类目路径不存在：{category_path}"
                    res.issues.append(msg)
                    _step(res, "类目校验", False, msg)
        else:
            cid = str(ov.get("category_id") or product.get("categoryId") or "").strip()
            _step(res, "类目校验", True,
                  f"沿用商品类目 {cid}" if cid else "未选择类目（保存前校验会拦截）")

        # ④ AI 生成（use_ai；overrides 已有值不覆盖）
        text = _current_text_fields(product, ov)
        ai_notes: list[str] = []
        if use_ai:
            missing = [f for f in AI_FIELDS if not text.get(f)]
            if missing:
                ai_product = _product_for_ai(product, ov, detail)
                generated, ai_notes = ai_generate_all(
                    ai_product, cfg, mp, _bullets_count(cfg, 0))
                res.ai_used = True
                ok_fields = 0
                for name in missing:
                    value = generated.get(name)
                    if name == "bullets":
                        if value:
                            ov["bullets"] = list(value)
                            ok_fields += 1
                    elif value:
                        ov[name] = value
                        ok_fields += 1
            else:
                _step(res, "AI 文案", True, "四项文案均已填写，跳过 AI 生成")
        else:
            _step(res, "AI 文案", True, "未启用 AI，使用当前文案/商品原文")
        if ai_notes:
            failed = [n for n in ai_notes if "失败" in n or "异常" in n]
            for note in ai_notes[:8]:
                res.warnings.append("AI：" + note)
            _step(res, "AI 文案", True if not failed else False,
                  ("；".join(_short(n, 80) for n in ai_notes[:4]))
                  + (f"（{len(failed)} 项失败，已保留手填内容）" if failed else ""))

        # ⑤ 组装保存包
        try:
            payload = build_amazon_payload(product, cfg, ov, currency=res.currency)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "组装保存包", f"build_amazon_payload 失败：{_short(exc)}")
        _fill_result(res, payload)
        res.op = op

        # ⑥ 必填/合规校验
        try:
            issues = validate_amazon_payload(payload, product=product,
                                             site_meta=prompt_site_meta(res.site))
            warnings = build_warnings(payload, product=product)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存前校验", f"校验异常：{_short(exc)}")
        res.issues.extend(issues)
        # AI 生成的卖点不足 3 条：AI 输出必须 5 条，解析不足要阻断提示
        if use_ai and ov.get("bullets"):
            n_bullets = len([b for b in split_bullets(payload.get("bulletPoint") or "") if b])
            if 0 < n_bullets < 3:
                res.issues.append(f"AI 生成的卖点不足 3 条（{n_bullets} 条），请补齐后再保存")
        res.warnings.extend(warnings)
        if res.issues:
            detail_msg = f"{len(res.issues)} 项未通过：" + "；".join(res.issues[:6])
            _step(res, "保存前校验", False, detail_msg)
            res.ok = False
            res.error = "保存前校验未通过：" + "；".join(res.issues)
            res.preview = bool(dry_run) or forced_preview()
            return res
        _step(res, "保存前校验", True, "必填项/合规项全部通过")

        # ⑦ 违禁词（只读，仅提示）
        try:
            banned = c.check_banned_words(
                title=res.title, description=res.description,
                bullet_point=payload.get("bulletPoint") or "",
                search_terms=res.search_terms)
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

        # ⑧ 预演
        save_detail = f"op={op}（{OP_LABELS[op]}）· 载荷 {len(payload)} 个字段"
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

        # 唯一写收口：save_to_amazon（内部还会再校验一次 forced_preview/guard/issues）
        try:
            from .amazon_publish import save_to_amazon
            # 复用步骤①创建的 client：注入 fake 时绝不新建真实客户端（防脚本误写事故）
            saved = save_to_amazon(pid, cfg, overrides=ov, op=op, client=c)
        except ValueError as exc:
            return _fail(res, "保存到 Amazon", f"保存前校验未通过：{_short(exc, 300)}")
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存到 Amazon", f"保存失败：{_short(exc, 300)}")
        res.save_result = saved.get("result") if isinstance(saved, dict) else {}
        if isinstance(res.save_result, dict):
            code = res.save_result.get("code")
            if code not in (0, "0"):
                msg = str(res.save_result.get("msg") or "")
                return _fail(res, "保存到 Amazon",
                             f"保存失败：{msg or f'code={code}'}")
        res.saved = True
        res.ok = True
        msg = ""
        if isinstance(res.save_result, dict):
            data = res.save_result.get("data")
            if isinstance(data, dict):
                msg = str(data.get("msg") or "")
            msg = msg or str(res.save_result.get("msg") or "")
        _step(res, "保存到 Amazon", True,
              f"{OP_LABELS[op]}成功" + (f"：{_short(msg, 110)}" if msg else ""))
        _step(res, "完成", True, "商品已保存/提交")
        return res
    except Exception as exc:  # noqa: BLE001
        return _fail(res, "流水线异常", f"{type(exc).__name__}: {_short(exc, 200)}")


__all__ = [
    "ENV_NO_SAVE", "ENV_TEST", "OP_SAVE", "OP_MOVE_PENDING", "OP_PUBLISH",
    "OP_LABELS", "AI_FIELDS", "AI_LABELS",
    "AmazonStep", "AmazonRunResult", "forced_preview", "prepare_save",
    "run_amazon_product", "ai_generate_field", "ai_generate_all",
]
