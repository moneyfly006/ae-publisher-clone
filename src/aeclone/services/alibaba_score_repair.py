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

    if room("detail"):
        description = max(source_texts, key=len, default="")
        if len(description) > len(str(saved.get("textDesc") or "")):
            improves("text_desc", description, "恢复采集商品中已有的详情文字")
        existing = detail_urls(saved)
        collected = [url for key in ("description", "detailHtml", "detail") for url in html_images(product.get(key))]
        detail = list(dict.fromkeys(existing + [url for url in collected if is_image_url(url)]))[:MAX_DETAIL_IMAGES]
        if len(detail) > len(existing):
            improves("detail_images", detail, f"从采集详情补入 {len(detail) - len(existing)} 张原图")

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
                improves("attributes", {**(values or {}), **missing}, "按原描述和类目候选补齐属性：" + "、".join(missing))

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
