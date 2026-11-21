"""AI → Alibaba 刊登流水线（服务层，不含 UI）。

一条链路：读商品详情 → 类目 required attrs / schema → AI 标题+详情 → 关键词
→ build_alibaba_save → validate_alibaba_save → 违禁词（只读）→ 保存/预演。

安全语义
--------
- ``dry_run=True``（默认）绝不调用保存接口，只返回 preview。
- ``dry_run=False`` 且命中 ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 时强制转预演。
- 真实保存前必须通过 ``core.guard.require``（``AURORA_SKIP_LICENSE=1`` 可跳过）。
- 任何异常都转成失败步骤并返回 ``ok=False``，不静默吞掉。
"""
from __future__ import annotations

import html as _html
import os
import re
from dataclasses import dataclass, field

from .alibaba_payload import (
    MAX_KEYWORDS, MAX_SUBJECT, MIN_KEYWORDS, build_alibaba_save,
    clean_keywords, detail_urls, keywords_effective, resolve_category,
    validate_alibaba_save,
)
from .alibaba_publish import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, OP_SCHEDULE, make_client,
)
from .publisher import is_failure_msg

ENV_NO_SAVE = "AURORA_NO_SAVE"
ENV_TEST = "AURORA_TEST"

OP_LABELS = {
    OP_SAVE: "保存草稿",
    OP_PUBLISH: "发布",
    OP_MOVE_PENDING: "保存并移入待发布",
    OP_SCHEDULE: "定时发布",
}


@dataclass
class AlibabaStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class AlibabaRunResult:
    """一次 AI→Alibaba 刊登录入的结果（UI 直接读这些字段渲染）。"""

    product_id: str = ""
    ok: bool = False
    error: str = ""
    steps: list[AlibabaStep] = field(default_factory=list)
    preview: bool = False
    saved: bool = False
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    subject: str = ""
    keywords: list[str] = field(default_factory=list)
    text_chars: int = 0
    category_id: str = ""
    op: str = OP_SAVE
    banned_words: list[str] = field(default_factory=list)
    save_result: dict = field(default_factory=dict)
    ai_used: bool = False

    @property
    def incomplete_steps(self) -> list[AlibabaStep]:
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
    """AURORA_NO_SAVE / AURORA_TEST 存在时任何真实写操作都必须降级为预演。"""
    return bool(os.environ.get(ENV_NO_SAVE) or os.environ.get(ENV_TEST))


# ---------------- 内部工具 ----------------
def _step(res: AlibabaRunResult, name: str, ok: bool, detail: str = "") -> None:
    res.steps.append(AlibabaStep(name, ok, detail))


def _fail(res: AlibabaRunResult, name: str, error: str) -> AlibabaRunResult:
    """记录失败步骤并返回（统一失败语义：ok=False + error 非空）。"""
    res.ok = False
    res.error = error
    _step(res, name, False, error)
    return res


def _tr_ok(r) -> bool:
    return bool(getattr(r, "ok", False))


def _tr_value(r) -> str:
    return str(getattr(r, "value", "") or "")


def _tr_error(r) -> str:
    return str(getattr(r, "error", "") or "") or "未知错误"


def _tr_notes(r) -> list[str]:
    notes = getattr(r, "notes", None) or []
    return [str(n) for n in notes]


def _plain_text(markup: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", str(markup or ""), flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def clip_title(title: str, limit: int = MAX_SUBJECT) -> str:
    """Alibaba subject ≤128：清洗 + 本地截断（按空格），不调用付费 AI 压缩。

    AI 有时输出 en/em dash（– —）、全角标点或中文；Alibaba 英文标题用 ASCII
    更稳妥，这里统一替换成普通连字符并去掉不可见控制字符。
    """
    t = str(title or "")
    t = (t.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
          .replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", ""))
    t = re.sub(r"[\u3000-\u303f\uff00-\uffef]", " ", t)  # 全角标点
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip("'")
    if len(t) <= limit:
        return t
    cut = t[:limit]
    head = cut.rsplit(" ", 1)[0].strip() if " " in cut else cut.strip()
    return head or cut.strip()


def _brands_from(product: dict) -> list[str]:
    out: list[str] = []
    for key in ("brandName", "brand", "brand_name"):
        v = str(product.get(key) or "").strip()
        if v and v.lower() not in ("none", "null"):
            out.append(v)
    for a in _as_attr_list(product.get("attributes")):
        if str(a.get("attribute_name") or "").strip().lower() in ("brand", "brand name", "品牌"):
            v = str(a.get("value_name") or "").strip()
            if v and v.lower() not in ("none", "null"):
                out.append(v)
    return list(dict.fromkeys(out))


def _as_attr_list(raw) -> list[dict]:
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict)]
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            import json
            data = json.loads(raw)
            return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _attributes_map(product: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in _as_attr_list(product.get("attributes")):
        name = str(a.get("attribute_name") or "").strip()
        val = str(a.get("value_name") or "").strip()
        if name and val:
            out.setdefault(name, val)
    return out


def _source_text(product: dict) -> str:
    for key in ("textDesc", "description", "detail", "detailHtml"):
        raw = product.get(key)
        text = _plain_text(raw) if isinstance(raw, str) else ""
        if text:
            return text[:6000]
    return ""


def _banned_hits(raw) -> list[str]:
    """兼容店小秘违禁词接口的几种返回形状，提取命中的词。"""
    hits: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            s = node.strip()
            if s and s.lower() not in ("none", "null", "false", "true", "ok"):
                hits.append(s)
        elif isinstance(node, dict):
            for key in ("word", "words", "bannedWord", "bannedWords", "banned_word",
                        "bannedWordList", "list", "result", "name", "msg"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, bool) or data in (None, "", 0, "0"):
            return []
        walk(data)
    else:
        walk(raw)
    # 接口若返回整句提示（含「违禁」字样），保留整句
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:10]


def _keyword_list(save: dict) -> list[str]:
    return [k.strip() for k in str(save.get("keywords") or "").split(",") if k.strip()]


def _configured_keywords(cfg) -> str:
    if cfg is None:
        return ""
    if isinstance(cfg, dict):
        return str(cfg.get("alibaba_keywords", "") or "")
    return str(getattr(cfg, "alibaba_keywords", "") or "")


def _top_up_keywords(kws: list[str], subject: str, plain_detail: str) -> list[str]:
    if len(kws) >= MIN_KEYWORDS:
        return kws[:MAX_KEYWORDS]
    for w in clean_keywords(f"{subject} {plain_detail[:600]}", MAX_KEYWORDS):
        if w not in kws:
            kws.append(w)
        if len(kws) >= MAX_KEYWORDS:
            break
    return kws[:MAX_KEYWORDS]


# ---------------- 主流程 ----------------
def run_alibaba_product(product_id: str, cfg, *, dry_run: bool = True,
                        op: str = OP_SAVE, use_ai: bool = True,
                        client=None, text_pipeline=None,
                        overrides: dict | None = None) -> AlibabaRunResult:
    """AI 文案 → Alibaba 保存结构 → 校验 → 违禁词 → 预演/保存。

    参数
    ----
    product_id : 店小秘/ICBU 商品 id（必须是已存在商品）
    cfg        : AppConfig（也兼容 dict）
    dry_run    : 默认 True，只预演；False 才可能真实保存
    op         : "1"保存 / "2"发布 / "3"移入待发布 / "5"定时发布
    use_ai     : True 调 TextPipeline（付费 API）；离线核验传 False
    client     : 可注入 AlibabaClient（测试/脚本）；None 时按 Cookie 顺序创建
    text_pipeline : 可注入 TextPipeline（测试/离线桩）
    overrides  : 覆盖保存字段（如 category_id/full_cid/price_type/price_tiers），
                 批量测试或 UI 临时选择类目时不必改全局 cfg

    返回 AlibabaRunResult；任何异常都以 ok=False + error 返回，不抛出。
    """
    pid = str(product_id or "").strip()
    op = str(op or OP_SAVE)
    res = AlibabaRunResult(product_id=pid, op=op, preview=bool(dry_run))
    try:
        if not pid:
            return _fail(res, "参数校验", "缺少 product_id")
        if op not in OP_LABELS:
            return _fail(res, "参数校验", f"未知操作 op={op}（可选 1/2/3/5）")

        # a. 客户端（Cookie：alibaba_cookies → manual_cookies → 本机浏览器）
        try:
            c = client if client is not None else make_client(cfg)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "连接 Alibaba", f"创建客户端失败：{e}")
        _step(res, "连接 Alibaba", True, "Cookie 可用，客户端已创建")

        # a. 商品详情
        try:
            product = c.product_detail(pid)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "读取商品详情", f"读取商品 {pid} 失败：{e}")
        if not product:
            return _fail(res, "读取商品详情", f"读不到商品 {pid}（可能已删除/不属于当前店铺）")
        _step(res, "读取商品详情", True,
              f"{str(product.get('subject') or '')[:60]} · 类目 {product.get('categoryId')}")

        # b. 类目 required attrs + schema（与保存用同一类目上下文）
        ov = dict(overrides or {})
        cid, _full_cid = resolve_category(product, cfg, ov)
        req: list = []
        schema: list = []
        if cid:
            try:
                req = c.required_attributes(cid)
                schema = c.category_schema(cid)["fields"]
            except Exception as e:  # noqa: BLE001
                return _fail(res, "读取类目规则", f"类目 {cid} 属性/schema 读取失败：{e}")
            _step(res, "读取类目规则", True, f"类目 {cid}：{len(req)} 个必填属性 / {len(schema)} 个模块字段")
        else:
            _step(res, "读取类目规则", True, "未配置类目（保存前校验会拦截）")

        # c. AI 标题 / 详情 / 关键词
        source_subject = str(product.get("subject") or "").strip()
        subject = clip_title(source_subject, MAX_SUBJECT)
        plain_source = _source_text(product)
        text_desc = str(product.get("textDesc") or "").strip()
        tp = None
        if use_ai:
            try:
                if text_pipeline is not None:
                    tp = text_pipeline
                else:
                    from .text_pipeline import TextPipeline
                    tp = TextPipeline(cfg)
            except Exception as e:  # noqa: BLE001
                _step(res, "AI 文案", False, f"AI 服务初始化失败：{str(e)[:160]}")
                res.ok = False
                res.error = f"AI 服务初始化失败：{str(e)[:160]}"

        if tp is not None:
            res.ai_used = True
            # 标题（Alibaba ≤128；make_title 内部按 125 控制，这里兜底截断到 128）
            try:
                tr = tp.make_title(source_subject, _brands_from(product))
                if _tr_ok(tr):
                    subject = clip_title(_tr_value(tr), MAX_SUBJECT)
                    detail = subject[:70] + (f"（{len(subject)} 字符，≤{MAX_SUBJECT}）"
                                             if len(subject) >= MAX_SUBJECT else "")
                    _step(res, "AI 标题", True, detail)
                    for note in _tr_notes(tr)[:3]:
                        res.warnings.append(note)
                        _step(res, "AI 标题提醒", True, note)
                else:
                    _step(res, "AI 标题", False, f"生成失败：{_tr_error(tr)[:160]}")
                    res.ok = False
                    res.error = f"AI 标题生成失败：{_tr_error(tr)[:160]}"
            except Exception as e:  # noqa: BLE001
                _step(res, "AI 标题", False, f"调用异常：{str(e)[:160]}")
                res.ok = False
                res.error = f"AI 标题调用异常：{str(e)[:160]}"

            # 详情（HTML → textDesc）
            try:
                dr = tp.make_detail(subject or source_subject, _attributes_map(product),
                                    source_text=plain_source, images=detail_urls(product))
                if _tr_ok(dr):
                    text_desc = _tr_value(dr)
                    _step(res, "AI 详情", True,
                          f"HTML {len(text_desc)} 字符 · 纯文本 {len(_plain_text(text_desc))} 字符")
                    for note in _tr_notes(dr)[:3]:
                        res.warnings.append(note)
                else:
                    _step(res, "AI 详情", False, f"生成失败：{_tr_error(dr)[:160]}")
                    res.ok = False
                    res.error = f"AI 详情生成失败：{_tr_error(dr)[:160]}（保留商品原详情）"
            except Exception as e:  # noqa: BLE001
                _step(res, "AI 详情", False, f"调用异常：{str(e)[:160]}")
                res.ok = False
                res.error = f"AI 详情调用异常：{str(e)[:160]}（保留商品原详情）"
        else:
            _step(res, "AI 文案", True, "未启用 AI，使用商品原标题/详情")

        # 关键词：overrides/cfg > 商品原 keywords > 标题提取，补足到 3~5 个。
        # 商品原有短语不能因为「没配置关键词」就被标题提取整段覆盖。
        kws = keywords_effective(
            {"subject": subject or source_subject,
             "keywords": str(product.get("keywords") or "")}, cfg)
        # 已有来源（cfg/商品原值）时只规整、不从标题扩写；只有完全没有关键词
        # （keywords_effective 从标题提取且不足）时才用详情文字补足。
        _has_source_kws = bool(_configured_keywords(cfg).strip()
                               or str(product.get("keywords") or "").strip())
        if not _has_source_kws and len(kws) < MIN_KEYWORDS:
            kws = _top_up_keywords(kws, subject or source_subject,
                                   _plain_text(text_desc) or plain_source)
        if len(kws) < MIN_KEYWORDS:
            warn = f"关键词仅 {len(kws)} 个（建议 {MIN_KEYWORDS}~{MAX_KEYWORDS} 个）"
            res.warnings.append(warn)
            _step(res, "关键词", True, warn)
        else:
            _step(res, "关键词", True, ", ".join(kws[:MAX_KEYWORDS]))

        # d. 构建 + 校验
        try:
            save_ov = dict(ov)
            save_ov.setdefault("subject", subject or source_subject)
            save_ov.setdefault("text_desc", text_desc)
            save_ov.setdefault("keywords", kws)
            save = build_alibaba_save(product, cfg, save_ov, attr_defs=req)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "构建保存载荷", f"build_alibaba_save 失败：{e}")

        res.subject = str(save.get("subject") or "")
        res.keywords = _keyword_list(save)
        res.text_chars = len(_plain_text(save.get("textDesc") or ""))
        res.category_id = str(save.get("categoryId") or "")

        try:
            issues = validate_alibaba_save(product, save, cfg, req, schema)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "保存前校验", f"validate_alibaba_save 异常：{e}")
        res.issues = list(issues)
        if issues:
            _step(res, "保存前校验", False, f"{len(issues)} 项未通过：" + "；".join(issues[:5]))
            res.ok = False
            res.error = "保存前校验未通过：" + "；".join(issues)
            if dry_run:
                res.preview = True
            return res
        _step(res, "保存前校验", True, "必填项全部满足")
        if not res.error:
            res.ok = True

        # e. 违禁词（只读接口，仅作提示，不阻断；所以不记失败步骤）
        try:
            banned = _banned_hits(c.check_banned_words(
                " ".join([res.subject] + res.keywords)))
            if banned:
                res.banned_words = banned
                warn = "违禁词命中：" + ", ".join(banned) + "（保存前建议替换）"
                res.warnings.append(warn)
                _step(res, "违禁词检查", True, warn)
            else:
                _step(res, "违禁词检查", True, "未命中")
        except Exception as e:  # noqa: BLE001
            warn = f"违禁词检查失败（不影响预演/保存）：{str(e)[:120]}"
            res.warnings.append(warn)
            _step(res, "违禁词检查", True, "跳过：" + str(e)[:100])

        # AI 文案失败：只出预演结果，不执行真实保存（避免把原文当 AI 产物写回）
        if not res.ok:
            _step(res, "停止保存", False, f"{res.error}；未调用保存接口")
            res.preview = True
            return res

        save_detail = f"op={op}（{OP_LABELS[op]}）· 载荷 {len(save)} 个字段"

        # f. dry_run：绝不调用保存
        if dry_run:
            res.preview = True
            _step(res, "预演", True, f"{save_detail}；dry-run 未调用保存接口")
            _step(res, "完成", True, "预演完成（未保存商品）")
            return res

        # g. 真实保存前的双保险
        if forced_preview():
            res.preview = True
            res.warnings.append("检测到 AURORA_NO_SAVE/AURORA_TEST，已强制预演")
            _step(res, "安全护栏", True, "AURORA_NO_SAVE/AURORA_TEST 生效：强制预演，不保存商品")
            _step(res, "完成", True, "预演完成（未保存商品）")
            return res

        try:
            from ..core.guard import require as _require_license
            _require_license(cfg)
            _step(res, "授权校验", True, "通过")
        except Exception as e:  # noqa: BLE001
            return _fail(res, "授权校验", f"授权校验未通过：{e}")

        try:
            resp = c.save_product(product, save, op=op)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "保存到 Alibaba", f"保存失败：{e}")

        res.save_result = resp if isinstance(resp, dict) else {"result": resp}
        code = resp.get("code") if isinstance(resp, dict) else None
        if code not in (0, "0"):
            msg = str(resp.get("msg") or "") if isinstance(resp, dict) else ""
            return _fail(res, "保存到 Alibaba", f"保存失败：{msg or f'code={code}'}")
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        msg = str(data.get("msg") or resp.get("msg") or "")
        # 店小秘可能出现 code=0 但 msg 是失败语义（「只有处在采集箱中的产品…」）
        if is_failure_msg(msg):
            return _fail(res, "保存到 Alibaba", f"保存失败：{msg[:200]}")
        res.saved = True
        res.ok = True
        _step(res, "保存到 Alibaba", True,
              f"{OP_LABELS[op]}成功" + (f"：{msg[:110]}" if msg else ""))
        _step(res, "完成", True, "商品已保存/提交")
        return res
    except Exception as e:  # noqa: BLE001
        return _fail(res, "流水线异常", f"{type(e).__name__}: {str(e)[:200]}")


__all__ = [
    "AlibabaRunResult", "AlibabaStep", "run_alibaba_product", "forced_preview",
    "clip_title", "OP_LABELS", "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_SCHEDULE",
]
