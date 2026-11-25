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
import time
import re
from dataclasses import dataclass, field

from .alibaba_payload import (
    MAX_KEYWORDS, MAX_SUBJECT, MIN_KEYWORDS, build_alibaba_save,
    clean_keywords, detail_urls, keywords_effective, resolve_category,
    text_desc_plain, validate_alibaba_save,
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
    score: float = 0.0
    initial_score: float | None = None
    score_problems: list[str] = field(default_factory=list)
    initial_score_problems: list[str] = field(default_factory=list)
    score_actions: list[str] = field(default_factory=list)
    score_passed: bool = False
    score_fixes: list[str] = field(default_factory=list)
    publish_state: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    auditing: bool = False

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
    from .progress import emit
    emit("Alibaba · " + name, "完成" if ok else "失败", detail,
         level="OK" if ok else "ERROR", product=res.product_id)


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


MAX_TEXT_DESC = 2000   # 平台实测：textDesc 不能超过 1000 汉字或 2000 字符


def clip_detail_html(html: str, limit: int = MAX_TEXT_DESC) -> str:
    """把详情 HTML 截断到平台上限内（尽量保持标签闭合）。

    平台报错示例：textDesc:isv.parameter-parse-error:不能超过1000个汉字或2000个字符。
    优先在标签边界截断，并补上未闭合的常用标签，避免残缺 HTML 再次被拒。
    """
    raw = str(html or "")
    if len(raw) <= limit:
        return raw
    cut = raw[:limit]
    # 截断点若在标签中间，退到最后一个完整的 '>'
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[:cut.rfind("<")]
    # 记录未闭合标签并补齐
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*?(/?)>", cut):
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if closing:
            if stack and stack[-1] == tag:
                stack.pop()
            elif tag in stack:
                while stack and stack[-1] != tag:
                    stack.pop()
                if stack:
                    stack.pop()
        elif not selfclose and tag not in ("br", "img", "hr", "meta", "input", "link"):
            stack.append(tag)
    tail = "".join(f"</{t}>" for t in reversed(stack))
    if len(cut) + len(tail) > limit:
        cut = cut[:max(0, limit - len(tail))]
    return cut + tail


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


def _keywords_unrelated(kws: list[str], subject: str, plain_detail: str) -> bool:
    """关键词短语与本商品标题/详情完全无重叠 → 很可能套用了别的商品的全局预设。

    用「短语」而不是单词判断：详情里出现 construction/equipment 这类泛词，
    不代表 "construction equipment" 就是本商品关键词。
    """
    hay = re.sub(r"\s+", " ", f"{subject or ''} {plain_detail or ''}".lower())
    phrases = [re.sub(r"\s+", " ", str(k).strip().lower()) for k in kws if str(k).strip()]
    if not phrases:
        return False
    return not any(p in hay for p in phrases)


# ---------------- 主流程 ----------------
def run_alibaba_product(product_id: str, cfg, *, dry_run: bool = True,
                        op: str = OP_SAVE, use_ai: bool = True,
                        client=None, text_pipeline=None,
                        overrides: dict | None = None, repair_score=False,
                        before_submit=None) -> AlibabaRunResult:
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
    from .progress import emit
    from .collect_store import BudgetExceeded
    emit("Alibaba · 任务", "开始", OP_LABELS.get(op, op), product=pid)
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
        emit("Alibaba · 读取商品详情", "开始", product=pid)
        try:
            product = c.product_detail(pid)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "读取商品详情", f"读取商品 {pid} 失败：{e}")
        if not product:
            return _fail(res, "读取商品详情", f"读不到商品 {pid}（可能已删除/不属于当前店铺）")
        _step(res, "读取商品详情", True,
              f"{str(product.get('subject') or '')[:60]} · 类目 {product.get('categoryId')}")

        # b. 类目 required attrs + schema（与保存用同一类目上下文）
        emit("Alibaba · 读取类目规则", "开始", product=pid)
        ov = dict(overrides or {})
        # 模板快照 + 自动类目：批量采集商品也必须走与单品保存相同的上下文。
        try:
            from .alibaba_template import load_snapshot, build_snapshot, snapshot_attrs_for_payload
            _snap = load_snapshot(cfg)
            _tpl_id = str(getattr(cfg, "alibaba_template_product_id", "") or "").strip()
            if _tpl_id and str(_snap.get("product_id") or "") != _tpl_id:
                try:
                    _snap = build_snapshot(c, _tpl_id)
                except Exception:
                    _snap = {}
            if _snap:
                ov.setdefault("template_attributes", snapshot_attrs_for_payload(_snap))
                ov.setdefault("template_fields", _snap.get("fields") or {})
        except Exception:
            _snap = {}
        _cat_src = str(getattr(cfg, "alibaba_category_source", "auto") or "auto")
        if not str(ov.get("category_id") or "").strip():
            if _cat_src == "auto":
                _desired = (str(product.get("categoryId") or "").strip(),
                            str(product.get("fullCid") or ""))
                _allowed = None
                try:
                    _allowed = set(str(x) for x in
                                   (c.online_category_scope(max_pages=3).get("categories") or {}))
                except Exception:
                    _allowed = None
                if _desired[0] and _allowed and _desired[0] not in _allowed:
                    _desired = ("", "")   # 商品旧类目不在经营范围内，必须重新自动选
                if not _desired[0]:
                    try:
                        from .alibaba_payload import auto_alibaba_category
                        _desired = auto_alibaba_category(c, product, cfg,
                                                         allowed=_allowed) or _desired
                    except Exception:
                        pass
                if not _desired[0] and _snap:
                    _desired = (str(_snap.get("category_id") or ""),
                                str(_snap.get("full_cid") or ""))
                ov["category_id"] = _desired[0] or "-"
                ov["full_cid"] = _desired[1] or ""
            elif _cat_src == "template" and _snap and str(_snap.get("category_id") or "").strip():
                ov["category_id"] = str(_snap.get("category_id"))
                ov["full_cid"] = str(_snap.get("full_cid") or "")
        cid, _full_cid = resolve_category(product, cfg, ov)
        if not cid and bool(getattr(cfg, "auto_detect_category", True)):
            try:
                from .alibaba_payload import auto_alibaba_category
                _allowed = None
                try:
                    _allowed = set(str(x) for x in
                                   (c.online_category_scope(max_pages=3).get("categories") or {}))
                except Exception:
                    _allowed = None
                _cid, _full = auto_alibaba_category(c, product, cfg, allowed=_allowed)
                if _cid:
                    ov["category_id"] = _cid
                    if _full:
                        ov["full_cid"] = _full
                    cid, _full_cid = _cid, _full
            except Exception:
                pass
        if not cid and _snap and str(_snap.get("category_id") or "").strip():
            ov["category_id"] = str(_snap.get("category_id"))
            ov["full_cid"] = str(_snap.get("full_cid") or "")
            cid, _full_cid = ov["category_id"], ov["full_cid"]
        if (_cat_src == "auto" and bool(getattr(cfg, "auto_detect_category", True))
                and (not cid or str(cid) == "-") and locals().get("_allowed")):
            return _fail(res, "类目经营范围",
                         "商品原类目和自动匹配类目都不在店铺经营类目范围内，已阻止处理。"
                         "请到 Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → 经营类目，"
                         "把商品类目加入经营范围，或手动选择允许的类目。")
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
        _ai_preset = None
        if use_ai:
            emit("Alibaba · AI 文案", "开始", "按 Alibaba 提示词处理标题与详情", product=pid)
            try:
                from . import alibaba_prompts as _AP
                _ai_preset = _AP.resolve_preset(
                    str(getattr(cfg, "alibaba_ai_preset", "") or "")) or {}
                _ai_preset = dict(_ai_preset)
                for _key in ("title_prompt", "detail_prompt", "review_prompt", "keyword_prompt"):
                    _custom = str(getattr(cfg, "alibaba_" + _key, "") or "").strip()
                    if _custom:
                        _ai_preset[_key] = _custom
                if text_pipeline is not None:
                    tp = text_pipeline
                else:
                    from ..config import AppConfig
                    from .text_pipeline import TextPipeline
                    # 关键：Alibaba 的标题/详情/关键词提示词与速卖通不同，不能在
                    # 全局 cfg 上永久覆盖，也不能继续用速卖通默认提示词。
                    # AppConfig 的浅拷贝共享 _d，套用预设会污染速卖通配置。
                    _ai_cfg = AppConfig(cfg.as_dict() if hasattr(cfg, "as_dict") else dict(cfg))
                    _AP.apply_to_cfg(_ai_cfg, _ai_preset)
                    _kwp = str(_ai_preset.get("keyword_prompt") or "").strip()
                    if _kwp:
                        _ai_cfg.set("alibaba_keyword_prompt", _kwp)
                    tp = TextPipeline(_ai_cfg)
            except Exception as e:  # noqa: BLE001
                _step(res, "AI 文案", False, f"AI 服务初始化失败：{str(e)[:160]}")
                res.ok = False
                res.error = f"AI 服务初始化失败：{str(e)[:160]}"

        if tp is not None:
            res.ai_used = True
            # 标题（Alibaba ≤128；make_title 内部按 125 控制，这里兜底截断到 128）
            try:
                _mk_title = getattr(tp, "make_alibaba_title", None)
                if callable(_mk_title):
                    tr = _mk_title(source_subject,
                                   prompt=str((_ai_preset or {}).get("title_prompt") or ""),
                                   limit=MAX_SUBJECT, brands=_brands_from(product))
                else:
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
                    _raw_desc = _tr_value(dr)
                    text_desc = text_desc_plain(_raw_desc)
                    _clipped = len(text_desc) != len(_raw_desc)
                    _step(res, "AI 详情", True,
                          f"纯文本 {len(text_desc)} 字符"
                          + (f"（已从 {len(_raw_desc)} 截断，平台上限 {MAX_TEXT_DESC}）"
                             if _clipped else "")
                          + " · 平台 textDesc 仅收纯文本")
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
            _step(res, "AI 文案", True,
                  "本次未调用 AI（AI开关关闭，或补齐模式已有全部文案）；保留商品原标题/详情")

        # AI 编造体检（只提醒不阻断）：原文没有的尺寸/材质/认证/绝对化词。
        if tp is not None:
            try:
                from . import alibaba_prompts as _AP
                _src_text = f"{source_subject} {plain_source}"
                _gen_text = f"{subject or ''} {_plain_text(text_desc)}"
                _fw = _AP.fabrication_warnings(_gen_text, _src_text)
                for _w in _fw[:5]:
                    res.warnings.append("AI 编造风险：" + _w)
                    _step(res, "AI 编造体检", True, _w)
                if not _fw:
                    _step(res, "AI 编造体检", True, "未发现明显编造")
            except Exception:  # noqa: BLE001
                pass

        # AI 关键词：商品原 keywords 常是采集来的小语种（葡语/西语），Alibaba 需要
        # 英文搜索词；只有用户没配置关键词、也没显式给 overrides 时才用 AI 生成。
        ai_kws: list[str] = []
        _ov_kws = str((overrides or {}).get("keywords") or "").strip()
        if tp is not None and not _configured_keywords(cfg).strip() and not _ov_kws:
            _mk = getattr(tp, "make_keywords", None)
            if callable(_mk):
                try:
                    kr = _mk(subject or source_subject,
                             _plain_text(text_desc) or plain_source)
                    if _tr_ok(kr):
                        got = [x.strip() for x in str(_tr_value(kr)).split(",") if x.strip()]
                        if MIN_KEYWORDS <= len(got) <= MAX_KEYWORDS:
                            ai_kws = got
                            _step(res, "AI 关键词", True, ", ".join(ai_kws))
                        else:
                            _step(res, "AI 关键词", True,
                                  f"生成 {len(got)} 个（不足 {MIN_KEYWORDS} 个），改用商品关键词/标题")
                    else:
                        _step(res, "AI 关键词", True, f"跳过：{_tr_error(kr)[:60]}")
                except Exception as e:  # noqa: BLE001
                    _step(res, "AI 关键词", True, f"跳过：{str(e)[:60]}")

        # 关键词：overrides/cfg > 商品原 keywords > AI 英文关键词 > 标题提取。
        # 商品原有短语不能因为「没配置关键词」就被标题提取整段覆盖。
        if ai_kws:
            kws = ai_kws[:MAX_KEYWORDS]
        else:
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
        # 全局关键词预设套到不相关商品时不能静默：关键词与标题/详情无任何重叠，
        # 通常意味着 alibaba_keywords 是别的品类的词（会拉低相关性评分）。
        if _keywords_unrelated(kws, subject or source_subject,
                               _plain_text(text_desc) or plain_source):
            warn = ("关键词与本商品标题/详情无任何重叠，可能套用了别的商品的全局关键词；"
                    "请在「设置 → Alibaba 刊登」改成本品类关键词，或为本商品单独填关键词")
            res.warnings.append(warn)
            _step(res, "关键词相关性", True, warn)

        # d. 构建 + 校验
        try:
            save_ov = dict(ov)
            save_ov.setdefault("subject", subject or source_subject)
            save_ov.setdefault("text_desc", text_desc)
            save_ov.setdefault("keywords", kws)
            save_ov["text_desc"] = text_desc_plain(save_ov.get("text_desc") or "")
            save = build_alibaba_save(product, cfg, save_ov, attr_defs=req, client=c)
        except Exception as e:  # noqa: BLE001
            return _fail(res, "构建保存载荷", f"build_alibaba_save 失败：{e}")
        res.warnings.append("DEBUG mainImage=" + str(save.get("mainImage"))[:80] + " len=" + str(len(str(save.get("mainImage") or ""))))
        try:
            from .alibaba_payload import normalize_publishable_main_images
            _fixed = normalize_publishable_main_images(c, product, save)
            if _fixed:
                res.warnings.append("主图兼容处理：已转为 JPEG 重新托管，避免平台抓图失败")
                _step(res, "主图兼容处理", True, f"{len(_fixed)} 张已转 JPEG")
            elif save.get("_image_repair_errors"):
                res.warnings.append("主图兼容处理未完成：" + "；".join(save["_image_repair_errors"][:2]))
        except Exception as e:  # noqa: BLE001
            res.warnings.append("主图兼容处理失败：" + str(e)[:120])

        try:
            from .alibaba_payload import prepare_video_for_save
            def _vlog(msg: str, lv: str = "INFO"):
                if str(lv).upper() == "WARN":
                    res.warnings.append(msg)
                else:
                    _step(res, "商品视频", True, msg)
            _video = prepare_video_for_save(product, cfg, save, save_ov, log=_vlog)
            if _video:
                res.warnings.append("商品视频：已写入保存包")
        except Exception as e:  # noqa: BLE001
            res.warnings.append("商品视频处理失败：" + str(e)[:120])

        from .alibaba_score import PASS_SCORE, score_product
        from .alibaba_score_repair import repair, score_problems
        try:
            issues = validate_alibaba_save(product, save, cfg, req, schema)
            score = score_product(product, cfg, save=save, required_attrs=req)
            res.initial_score = score.total
            res.initial_score_problems = score_problems(score)
            if repair_score and not dry_run and not score.passed:
                emit("Alibaba · 低分二次处理", "开始", f"初评 {score.total:.2f}/5 · " +
                     "；".join(res.initial_score_problems[:4]), product=pid)
                save, score, issues, res.score_actions = repair(product, cfg, save_ov, save, score, issues, req, schema)
                note = "；".join(res.score_actions) or "需要补充商品原始资料"
                _step(res, "低分二次处理", score.passed,
                      f"{res.initial_score:.2f} → {score.total:.2f}/5 · {note}")
            res.score = score.total
            res.score_passed = score.passed
            res.score_problems = score_problems(score)
            res.score_fixes = list(score.fixes[:8])
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            return _fail(res, "刊登预检", f"无法完成校验与评分：{e}")

        if op == OP_SCHEDULE:
            from .alibaba_payload import schedule_time_ms
            _ms = schedule_time_ms(getattr(cfg, "alibaba_schedule_time", ""))
            if _ms:
                save["dxmScheduleTime"] = _ms
                save["dxmScheduleTimeStr"] = str(getattr(cfg, "alibaba_schedule_time", ""))
            else:
                res.warnings.append(
                    "定时发布未设置发布时间：平台按默认排期；可在「价格与贸易」填「定时发布时间」")

        res.subject = str(save.get("subject") or "")
        res.keywords = _keyword_list(save)
        res.text_chars = len(_plain_text(save.get("textDesc") or ""))
        res.category_id = str(save.get("categoryId") or "")

        # 保存包生成后的「不静默」提示：属性自动补齐/全局覆盖/联系方式清理
        # 都会改变商品数据，必须让用户看到并复核（缺依据的字段不能悄悄写）。
        _autofill = [str(x) for x in (save.get("_autofilled") or [])]
        if _autofill:
            _step(res, "属性自动补齐", True, "；".join(_autofill)[:400])
        for _a in _autofill:
            if "请复核" in _a or "自动候选值" in _a:
                res.warnings.append("属性自动候选值需复核：" + _a)
        for _why in [str(x) for x in (save.get("_unresolved_reasons") or [])][:8]:
            res.warnings.append("属性未补齐：" + _why)
        _contact = [str(x) for x in (save.get("_contact_removed") or [])]
        if _contact:
            warn = ("详情/公司信息含站外联系方式，已自动移除："
                    + "、".join(_contact[:6])
                    + "（平台禁止站外联系；请确认清理后的正文通顺）")
            res.warnings.append(warn)
            _step(res, "联系方式清理", True, warn)
        for _ow in [str(x) for x in (save.get("_overwrites") or [])][:6]:
            warn = "全局配置与商品数据不一致：" + _ow + "；请确认后发布"
            res.warnings.append(warn)
            _step(res, "全局数据核对", True, warn)

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

        # 刊登评分门禁：op=2 提交审核/发布必须 ≥4.5；低分要指出缺什么。
        _step(res, "刊登评分", score.passed, f"{score.total:.2f}/5（发布线 {PASS_SCORE:.1f}）")
        res.warnings.extend("评分建议：" + fix for fix in score.fixes[:3])
        if op == OP_PUBLISH and not score.passed:
            why = "；".join((score.blockers + res.score_problems)[:5])
            res.preview = True
            return _fail(res, "发布门禁", f"刊登评分 {score.total:.2f}/5 未达发布线 {PASS_SCORE:.1f}，"
                         f"已阻止提交审核。需要修复：{why}")

        # d2. 经营类目范围预检（只读；平台硬校验，提前提醒避免发布后才发现）
        if op == OP_PUBLISH:
            try:
                from .alibaba_scope import check_category_scope
                _scope = check_category_scope(c, res.category_id, max_pages=6)
                res.scope = {"category_id": _scope.category_id,
                             "in_scope_guess": _scope.in_scope_guess,
                             "evidence_count": _scope.evidence_count,
                             "scanned": _scope.scanned,
                             "total": _scope.total,
                             "detail": _scope.detail,
                             "advice": _scope.advice}
                _step(res, "类目经营范围", _scope.in_scope_guess, _scope.detail)
                if not _scope.in_scope_guess:
                    res.warnings.append(
                        "经营类目预检：该末级类目未出现在在线商品类目样本中，"
                        "发布可能被平台拒绝（PUB_BIZCHECK_CAT_PUB_RESTRICT）。"
                        + _scope.advice)
                # 经营类目是平台硬门禁；在提交前拦截，别等平台异步返回 publishFail。
                try:
                    _cats = set(str(x) for x in
                                (c.online_category_scope(max_pages=3).get("categories") or {}))
                    if _cats and str(res.category_id or "") not in _cats:
                        return _fail(res, "类目经营范围",
                                     f"类目 {res.category_id} 不在店铺经营类目范围内，已阻止提交。"
                                     "请到 Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → 经营类目，"
                                     "把该类目加入经营范围，或改用自动类目。")
                except Exception:
                    pass
            except Exception as e:  # noqa: BLE001
                _step(res, "类目经营范围", True, f"预检跳过：{str(e)[:80]}")

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

        if before_submit is not None:
            before_submit()
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
        inner_code = data.get("code")
        # 已在审核中：说明之前已提交成功，重复提交被平台拒绝；按「已提交审核」处理。
        if "PRODUCT_IN_AUDITING" in msg:
            res.saved = True
            res.ok = True
            res.auditing = True
            _step(res, "保存到 Alibaba", True, "商品已在平台审核中，无需重复提交")
            _step(res, "发布结果", True, "审核中（平台拒绝重复提交，视为已提交成功）")
            return res
        # 外层 code=0 不代表成功：实测发布缺发货期时返回
        # {"code":0,"data":{"msg":"发货期信息未填写","code":-1}}。
        if inner_code not in (None, 0, "0"):
            if "CHK_VIDEO_PRODUCT_LINK_LIMIT" in msg:
                return _fail(res, "保存到 Alibaba",
                             "发布被平台拒绝（主视频复用超限）：同一主视频已被超过 20 个商品使用。"
                             "保存接口没有视频字段，无法通过软件清除平台侧视频；"
                             "请到 Alibaba 卖家中心给这些商品更换/删除主视频后再发布。")
            if "PUB_BIZCHECK_CAT_PUB_RESTRICT" in msg:
                from .alibaba_scope import CUSTOMER_ADVICE
                _cid = res.category_id or str(save.get("categoryId") or "")
                return _fail(res, "保存到 Alibaba",
                             f"发布被平台拒绝（经营类目）：类目 {_cid} 不在店铺经营类目范围内。"
                             + CUSTOMER_ADVICE)
            if "CHK_COMP_DATA_ERROR" in msg or "数据填写不完整" in msg:
                return _fail(res, "保存到 Alibaba",
                             "发布被平台拒绝（CHK_COMP_DATA_ERROR）：公司资料/发布必填信息不完整。"
                             "请到 Alibaba 卖家中心 → 店铺管理 → 公司信息，补全公司名称、地址、"
                             "联系人、营业执照/经营类目等资料后重试；软件侧的商品类目/属性/物流已通过校验。")
            return _fail(res, "保存到 Alibaba",
                         f"保存失败（业务码 {inner_code}）：{msg[:200]}")
        # 店小秘可能出现 code=0 但 msg 是失败语义（「只有处在采集箱中的产品…」）
        if is_failure_msg(msg):
            return _fail(res, "保存到 Alibaba", f"保存失败：{msg[:200]}")
        res.saved = True
        res.ok = True
        _step(res, "保存到 Alibaba", True,
              f"{OP_LABELS[op]}成功" + (f"：{msg[:110]}" if msg else ""))

        # op=2 提交后平台会异步做发布前校验（类目经营范围、资质等），几秒后可能变为
        # 「发布失败」。必须回读并如实报告，不能把「已提交」当成「发布成功」。
        if op == OP_PUBLISH:
            _state = {}
            for _i in range(6):
                time.sleep(2)
                try:
                    _p = c.product_detail(pid)
                except Exception:
                    continue
                _state = {"dxmState": _p.get("dxmState"),
                          "dxmOfflineState": _p.get("dxmOfflineState"),
                          "productStatusType": _p.get("productStatusType"),
                          "errMsg": str(_p.get("errMsg") or ""),
                          "solutionMsg": str(_p.get("solutionMsg") or "")}
                if not any(k in _p for k in ("dxmState", "dxmOfflineState",
                                             "productStatusType", "errMsg")):
                    break   # 离线桩/测试替身：没有真实状态字段，不做轮询
                _err = _state["errMsg"]
                if _err or _state["dxmOfflineState"] == "publishFail" or \
                        _state["productStatusType"] in ("publishFail", "auditFail"):
                    break
                if _state["dxmState"] in ("online", "offline") and not _err:
                    # 已进入在线/下架状态且无错误：视为平台已接受
                    if _state["dxmState"] == "online":
                        break
            res.publish_state = _state
            _err = str(_state.get("errMsg") or "")
            # 已提交成功、平台正在审核：不是失败；回读会一直带 AUDITING，
            # 之前这里会误报「平台发布校验未通过」。
            if "PRODUCT_IN_AUDITING" in _err or "审核中" in _err:
                res.auditing = True
                _step(res, "发布结果", True,
                      "平台审核中（已提交成功；审核结果可在「发布队列」查看）")
                _step(res, "完成", True, "商品已提交审核")
                return res
            if _err or _state.get("dxmOfflineState") == "publishFail":
                _hint = ""
                if "CAT_PUB_RESTRICT" in _err or "经营类目" in _err:
                    _hint = ("；处理：到 Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → "
                             "经营类目，把该类目加入经营范围后再发布")
                _step(res, "发布结果", False,
                      "平台发布校验未通过：" + (_err[:120] or "状态为发布失败") + _hint)
                res.ok = False
                res.error = ("已提交，但平台发布校验未通过：" +
                             (_err[:300] or "状态=发布失败") + _hint)
                return res
            from .publish_outcome import judge
            _st = str(_state.get("dxmOfflineState") or _state.get("productStatusType")
                      or _state.get("dxmState") or "")
            _verifiable = any(_state.get(k) for k in ("dxmState", "dxmOfflineState", "productStatusType"))
            _out = judge("publish", _st, _err)
            if _verifiable and not _out.get("ok"):
                _step(res, "发布结果", False,
                      f"已提交但未进入审核：{_out.get('label')}（{_st or '状态未知'}）")
                res.ok = False
                res.error = f"发布结果未确认：{_out.get('label')}（{_st or '状态未知'}）"
                return res
            res.auditing = _out.get("category") == "auditing"
            _step(res, "发布结果", True,
                  f"{_out.get('label')}（{_st or '-'}）")

        if op == OP_MOVE_PENDING:
            from .publish_outcome import judge
            _final = None
            for _i in range(5):
                time.sleep(2)
                try:
                    _p = c.product_detail(pid)
                except Exception:
                    continue
                if not any(k in _p for k in ("dxmState", "dxmOfflineState", "productStatusType", "errMsg")):
                    _final = None
                    break
                _st = str(_p.get("dxmOfflineState") or _p.get("productStatusType") or _p.get("dxmState") or "")
                _final = judge("save_to_waiting", _st, str(_p.get("errMsg") or ""))
                if _final.get("category") in ("pending", "failed"):
                    break
            if _final is not None and not _final.get("ok") and _final.get("category") != "uncertain":
                _step(res, "待发布结果", False,
                      f"已保存但未进入待发布：{_final.get('label')}（{_final.get('state') or '状态未知'}）")
                res.ok = False
                res.error = f"保存后未进入待发布：{_final.get('label')}"
                return res
            if _final is not None:
                _step(res, "待发布结果", True, _final.get("label") or "已进入待发布")

        _step(res, "完成", True, "商品已保存/提交")
        return res
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(res, "流水线异常", f"{type(e).__name__}: {str(e)[:200]}")


__all__ = [
    "AlibabaRunResult", "AlibabaStep", "run_alibaba_product", "forced_preview",
    "clip_title", "OP_LABELS", "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_SCHEDULE",
]
