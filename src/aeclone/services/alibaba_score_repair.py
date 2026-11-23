"""One targeted repair pass over an already-scored Alibaba listing."""
from copy import deepcopy
import json
import re

from .alibaba_payload import (
    MAX_DETAIL_IMAGES, MAX_MAIN_IMAGES, build_alibaba_save, clean_keywords,
    detail_urls, main_images_from, resolve_attr_value, split_keywords, text_desc_plain,
    validate_alibaba_save,
)
from .alibaba_prompts import fabrication_warnings
from .alibaba_score import PLACEHOLDER_EXEMPT, PLACEHOLDER_VALUES, score_product
from .collect_budget import paid
from .collect_store import BudgetExceeded
from .platform_media import html_images, is_image_url


def score_problems(score):
    return [f"{item.label} {item.score:g}/5 · {problem}" for item in score.items for problem in item.problems]


def _plain_sources(product):
    return [text_desc_plain(str(product.get(key) or "")) for key in
            ("textDesc", "description", "detailHtml", "detail")]


def _attributes(value):
    try:
        raw = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def _attribute_facts(saved, definitions, description):
    current = {str(row.get("attribute_name") or "").casefold(): str(row.get("value_name") or "").strip()
               for row in _attributes(saved.get("attributes"))}
    result = {}
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        name = str(definition.get("nameEn") or definition.get("nameZh") or "").strip()
        key = name.casefold()
        value = current.get(key, "")
        if not key or (value and value.casefold() not in PLACEHOLDER_VALUES):
            continue
        if any(part in key for part in (*PLACEHOLDER_EXEMPT, "brand", "weight", "dimension", "capacity",
                                       "voltage", "stock", "price", "identifier", "battery", "hazard")):
            continue
        labels = [str(definition.get(k) or "").strip() for k in ("nameEn", "nameZh")]
        matches = {value.strip() for label in labels if label for value in re.findall(
            rf"(?im)^\s*{re.escape(label)}\s*[:：]\s*([^\r\n;；]{{1,100}})\s*$", description) if value.strip()}
        if len(matches) != 1:
            continue
        fitted = resolve_attr_value(definition, next(iter(matches)))
        if fitted.get("matched") and str(fitted.get("value_name") or "").casefold() not in PLACEHOLDER_VALUES:
            result[name] = fitted["value_name"]
    return result


def _grounded_attr_values(product, saved, required):
    """从商品原文中找出明确出现的平台候选值，作为可自动替换的依据。"""
    text = " ".join(str(product.get(k) or "") for k in ("subject", "keywords", "textDesc", "description"))
    text += " " + " ".join(_plain_sources(product))
    low = text.casefold()
    current = {str(row.get("attribute_name") or "").casefold(): str(row.get("value_name") or "").strip()
               for row in _attributes(saved.get("attributes"))}
    out = {}
    for d in required or []:
        name = str(d.get("nameEn") or d.get("nameZh") or "").strip()
        if not name or current.get(name.casefold(), "").casefold() not in PLACEHOLDER_VALUES:
            continue
        raw = d.get("values") or d.get("valueList") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        cands = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            val = str(row.get("name_en") or row.get("name") or row.get("valueName") or "").strip()
            if val and val.casefold() not in PLACEHOLDER_VALUES:
                cands.append(val)
        def _hit(value):
            lowv = value.casefold()
            forms = {lowv, lowv[:-1] if lowv.endswith("s") else lowv + "s"}
            return any(re.search(r"\b" + re.escape(form) + r"\b", low) for form in forms)
        hits = [v for v in cands if _hit(v)]
        # 只接受唯一命中，避免把 Swimming Pools 误配到别的类目词。
        if len(hits) == 1:
            out[name] = hits[0]
    return out


def repair(product, cfg, overrides, saved, first_score, first_issues, required, schema):
    """Apply grounded changes, keeping only improvements without new validation issues."""
    options = deepcopy(overrides)
    score, issues = first_score, list(first_issues)
    actions = []
    source_texts = _plain_sources(product)
    facts = "\n".join([str(product.get("subject") or ""), *source_texts,
                       *(f"{row.get('attribute_name')}: {row.get('value_name')}" for row in
                         _attributes(product.get("attributes")) if row.get("attribute_name") and row.get("value_name"))])

    def improves(key, value, action):
        nonlocal options, saved, score, issues
        candidate = {**options, key: value}
        package = build_alibaba_save(product, cfg, candidate, attr_defs=required)
        after = score_product(product, cfg, save=package, required_attrs=required)
        checked = validate_alibaba_save(product, package, cfg, required, schema)
        if set(checked) - set(issues) or set(after.blockers) - set(score.blockers):
            return
        if after.total < score.total or (after.total == score.total and after.passed == score.passed
                                        and len(checked) >= len(issues)):
            return
        options, saved, score, issues = candidate, package, after, checked
        actions.append(action)

    def room(field):
        item = first_score.item(field)
        return item is not None and item.score < 5

    if room("keywords"):
        title = str(saved.get("subject") or "")
        original_terms = split_keywords(product.get("keywords"))
        candidates = [word for word in original_terms if word.isascii() and word.casefold() in facts.casefold()]
        candidates += clean_keywords(title, 5)
        if len(candidates) < 3:
            candidates += clean_keywords(facts, 5)
        keywords = list(dict.fromkeys(word.casefold() for word in candidates))[:5]
        if len(keywords) >= 3:
            improves("keywords", keywords, "从真实标题与描述补齐相关英文关键词")

    def grounded_detail():
        """用标题、关键词、真实属性拼出足够长的详情，不编造新参数。"""
        title = str(product.get("subject") or "").strip()
        keywords = [x.strip() for x in str(product.get("keywords") or "").split(",") if x.strip()]
        rows = [row for row in _attributes(saved.get("attributes"))
                if str(row.get("attribute_name") or "").strip()
                and str(row.get("value_name") or "").strip()
                and str(row.get("value_name") or "").strip().casefold() not in PLACEHOLDER_VALUES]
        specs = "\n".join(f"- {row.get('attribute_name')}: {row.get('value_name')}" for row in rows[:20])
        current = text_desc_plain(str(saved.get("textDesc") or ""))
        if len(current) >= 800:
            return current
        parts = [p for p in (current, title,
                             "Product features: " + ", ".join(keywords[:5]) if keywords else "",
                             "Specifications:\n" + specs if specs else "") if p]
        text = "\n\n".join(dict.fromkeys(parts))
        # 不凑虚假承诺；只补一段可核对的用途/规格说明，达到平台建议长度。
        if len(text) < 800:
            for row in rows:
                line = f"{row.get('attribute_name')}: {row.get('value_name')}."
                if line not in text:
                    text += ("\n" if text else "") + line
                if len(text) >= 800:
                    break
        return text

    if room("detail"):
        description = max(source_texts, key=len, default="")
        if len(description) > len(str(saved.get("textDesc") or "")):
            improves("text_desc", description, "恢复采集商品中已有的详情文字")
        existing = detail_urls(saved)
        collected = [url for key in ("description", "detailHtml", "detail") for url in html_images(product.get(key))]
        detail = list(dict.fromkeys(existing + [url for url in collected if is_image_url(url)]))[:MAX_DETAIL_IMAGES]
        if len(detail) > len(existing):
            improves("detail_images", detail, f"从采集详情补入 {len(detail) - len(existing)} 张原图")
        grounded = grounded_detail()
        if len(grounded) > len(description):
            improves("text_desc", grounded, "用商品真实属性补齐详情长度")

    if room("images"):
        existing = main_images_from(saved)
        originals = str(product.get("draftImgUrl") or "").split("|")
        images = list(dict.fromkeys(existing + [url.strip() for url in originals if is_image_url(url)]))
        if len(images) > len(existing):
            chosen = images[:MAX_MAIN_IMAGES]
            improves("main_images", chosen, f"从原商品图集中补入 {len(chosen) - len(existing)} 张主图")

    if room("attributes") and "native_attributes" not in options:
        values = options.get("attributes")
        if not values or isinstance(values, dict):
            matched = _attribute_facts(saved, required, "\n".join(source_texts))
            missing = {key: value for key, value in matched.items() if key.casefold() not in
                       {str(name).casefold() for name in (values or {})}}
            if missing:
                improves("replace_attributes", {**(options.get("replace_attributes") or {}), **missing}, "按原描述和类目候选补齐属性：" + "、".join(missing))
        # 先做纯规则映射：只有原文明确出现且唯一命中的平台候选值才自动替换。
        grounded = _grounded_attr_values(product, saved, required)
        if grounded:
            improves("replace_attributes", {**(options.get("replace_attributes") or {}), **grounded},
                     "按商品原文自动替换占位属性：" + "、".join(grounded))
        # 占位值无法从原文规则补齐时，让 AI 基于商品原文和类目候选推断，
        # 并且只采用命中平台枚举的建议（禁止编造属性仍会跳过）。
        if getattr(cfg, "alibaba_collect_auto_repair", True) and getattr(cfg, "api_key", ""):
            try:
                from .alibaba_ai import ai_suggest_attributes
                current = {str(row.get("attribute_name") or "").casefold():
                           str(row.get("value_name") or "").strip()
                           for row in _attributes(saved.get("attributes"))}
                placeholder_names = [name for name, value in current.items()
                                     if value.casefold() in PLACEHOLDER_VALUES]
                ai = ai_suggest_attributes(product, required, cfg=cfg,
                                           missing=placeholder_names or None, max_fields=6)
                accepted = {name: item.get("value_name")
                            for name, item in (ai.get("suggestions") or {}).items()
                            if item.get("value_name") and not item.get("needs_confirm")}
                if accepted:
                    improves("replace_attributes", {**(options.get("replace_attributes") or {}), **accepted},
                             "AI 按原文识别并替换占位属性：" + "、".join(accepted))
            except BudgetExceeded:
                raise
            except Exception as exc:
                from .progress import emit
                emit("Alibaba · 低分二次处理", "属性AI未采用", f"{type(exc).__name__}: {str(exc)[:100]}",
                     level="WARN", product=str(product.get("idStr") or product.get("id") or ""))

    if not score.passed and getattr(cfg, "alibaba_collect_ai", False) and getattr(cfg, "api_key", "") and facts.strip():
        from ..config import AppConfig
        from .collect_ai import _generate_field
        from .platform_presets import ALIBABA_PROMPTS, effective_prompt

        selected = json.loads(getattr(cfg, "alibaba_collect_ai_fields", "[]") or "[]")
        for dimension, kind, key, label in (("title", "title", "subject", "标题"),
                ("detail", "description", "text_desc", "详情"), ("keywords", "keywords", "keywords", "关键词")):
            if score.passed or kind not in selected or not room(dimension) or score.item(dimension).score >= 5:
                continue
            # The source comes from the original listing, independent of earlier AI drafts.
            source = dict(product, textDesc="\n".join(text for text in source_texts if text)[:6000])
            prompt_kind = {"title": "title", "description": "detail", "keywords": "keyword"}[kind]
            prompt_key = "alibaba_" + prompt_kind + "_prompt"
            reason = score.item(dimension).problems
            prompt = effective_prompt(cfg, prompt_key, ALIBABA_PROMPTS[prompt_kind])
            request_cfg = AppConfig(cfg.as_dict())
            request_cfg.set(prompt_key, prompt + "\n本次改进：" + "；".join(reason) +
                            "\n只使用以下商品原始事实。信息不足时保留真实信息，无需凑字数。\n" + facts[:2600])
            try:
                value, _ = paid(cfg, "alibaba", "score:" + kind, source, reason,
                                lambda: _generate_field("alibaba", source, request_cfg, kind))
                text = ", ".join(value) if isinstance(value, (tuple, list)) else str(value or "")
                if fabrication_warnings(text, facts):
                    continue
                improves(key, text, f"针对扣分原因重新生成{label}")
            except BudgetExceeded:
                raise
            except Exception as exc:
                from .progress import emit
                emit("Alibaba · 低分二次处理", "AI未采用", f"{label}：{exc}", level="WARN",
                     product=str(product.get("idStr") or product.get("id") or ""))

    return saved, score, issues, actions
