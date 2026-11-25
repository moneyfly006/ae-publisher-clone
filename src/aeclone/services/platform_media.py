"""Platform image groups and the native fields that carry their edits."""
from dataclasses import dataclass
from copy import deepcopy
from html import escape, unescape
import json
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ImageGroup:
    label: str
    limit: int
    listed: bool = True
    fixed_slots: bool = False


GROUPS = {
    "alibaba": {"main": ImageGroup("商品主图", 6), "detail": ImageGroup("详情图", 30),
                "sku": ImageGroup("规格图", 100, False, True)},
    "amazon": {"main": ImageGroup("主图（可处理第1-9张）", 9),
               "extra": ImageGroup("附图（可处理第1-8张）", 8)},
    "tiktok": {"main": ImageGroup("商品图片", 9), "detail": ImageGroup("产品描述中的图片", 30, False),
               "sku": ImageGroup("规格图", 100, False, True), "size": ImageGroup("尺码图", 1)},
    "shopee": {"main": ImageGroup("商品图片", 9), "detail": ImageGroup("新版描述中的图片", 12, False),
               "sku": ImageGroup("规格图", 100, False, True), "size": ImageGroup("尺码图", 1),
               "cover": ImageGroup("视频封面", 1)},
}

SKU_IMAGE_KEYS = ("imageUrl", "image_url", "skuImage", "sku_image", "variationImage", "variationImageUrl")

_IMAGE_TAG = re.compile(r"""<img\b(?:"[^"]*"|'[^']*'|[^'">])*>""", re.I)
_IMAGE_SRC = re.compile(r"""(\ssrc\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_INLINE_MEDIA = re.compile(r"<\s*(?:img|picture|source|svg|video|iframe|object|embed)\b", re.I)


def has_inline_media(text):
    return bool(_INLINE_MEDIA.search(str(text or "")))


def is_image_url(value):
    try:
        url = urlsplit(str(value or "").strip())
        return url.scheme in ("http", "https") and bool(url.netloc)
    except ValueError:
        return False


def _image_matches(text):
    for tag in _IMAGE_TAG.finditer(str(text or "")):
        src = _IMAGE_SRC.search(tag.group())
        if src:
            url = unescape(next(value for value in src.groups()[1:] if value is not None))
            if is_image_url(url):
                yield tag, src, url


def html_images(text):
    return [url for _, _, url in _image_matches(text)]


def replace_html_images(text, urls):
    """Replace image URLs in order and leave surrounding description markup intact."""
    text = str(text or "")
    parts, start = [], 0
    matches = list(_image_matches(text))
    for index, (tag, src, old) in enumerate(matches):
        parts.append(text[start:tag.start()])
        if index < len(urls):
            new = urls[index]
            original = tag.group()
            parts.append(original if new == old else original[:src.start()] + src.group(1)
                         + '"' + escape(new, quote=True) + '"' + original[src.end():])
        start = tag.end()
    parts.append(text[start:])
    parts.extend('<p><img src="' + escape(url, quote=True) + '"></p>' for url in urls[len(matches):])
    return "".join(parts)


def keep_description_images(new_text, old_text):
    """Keep the original image tags when AI generates replacement copy."""
    if not str(new_text or "").strip():
        return new_text
    images = {url for _, _, url in _image_matches(new_text)}
    return str(new_text) + "".join(f"<p>{tag.group()}</p>" for tag, _, url in _image_matches(old_text)
                                    if url not in images)


def _map_sku_images(node, visit, *, direct=False):
    if isinstance(node, dict):
        return {key: _map_sku_images(value, visit, direct=direct or key in SKU_IMAGE_KEYS)
                for key, value in node.items()}
    if isinstance(node, list):
        return [_map_sku_images(value, visit, direct=direct) for value in node]
    if direct and is_image_url(node):
        return visit(node)
    return node


def _structured_sku_images(raw, visit, *, direct=False):
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            if direct:
                return "|".join(visit(value) if is_image_url(value) else value for value in raw.split("|"))
            return raw
        return json.dumps(_map_sku_images(parsed, visit, direct=direct), ensure_ascii=False)
    return _map_sku_images(raw, visit, direct=direct)


def _variant_id(row):
    return str(row.get("idStr") or row.get("id") or row.get("modelIdStr") or row.get("modelId") or "")


def _visit_sku_images(platform, product, overrides, mode, visit):
    if platform == "alibaba":
        raw = overrides.get("product_sku", product.get("productSku"))
        from .shopee import json_value
        return _map_sku_images(json_value(raw, {}), visit)
    if platform == "tiktok":
        return _structured_sku_images(overrides.get("sku_images", product.get("skuImgs")), visit, direct=True)
    from .shopee import variation_rows
    edits = deepcopy(overrides.get("variant_edits") or {})
    rows = [row for row in variation_rows(product, mode) if isinstance(row, dict)]
    ids = [_variant_id(row) for row in rows]
    for row, key in zip(rows, ids):
        if not key or ids.count(key) != 1:
            continue
        for field in SKU_IMAGE_KEYS:
            if field not in row:
                continue
            old = edits.get(key, {}).get(field, row[field])
            if is_image_url(old):
                new = visit(old)
                if new != old:
                    edits.setdefault(key, {})[field] = new
    return edits


def read_groups(platform, product, overrides, mode="global"):
    from . import alibaba_payload, amazon_payload, shopee, tiktok_payload

    sku = []
    if "sku" in GROUPS[platform]:
        def collect(url):
            sku.append(url)
            return url
        _visit_sku_images(platform, product, overrides, mode, collect)
    if platform == "alibaba":
        return {"main": list(overrides.get("main_images", alibaba_payload.main_images_from(product))),
                "detail": list(overrides.get("detail_images", alibaba_payload.detail_urls(product))), "sku": sku}
    if platform == "amazon":
        main, extra, _ = amazon_payload._images(product)
        main = overrides.get("main_image", main)
        extras = overrides.get("extra_images",
                               [u.strip() for u in extra.split(";") if u.strip()])
        gallery = ([main] if main else []) + list(extras or [])
        return {"main": gallery, "extra": list(extras or [])}
    if platform == "tiktok":
        size = overrides.get("size_chart", product.get("sizeChart"))
        main = tiktok_payload._images_of(product) or tiktok_payload.split_pipe(product.get("draftImgUrl") or product.get("imgUrl"))
        return {"main": list(overrides.get("images", main)),
                "detail": html_images(overrides.get("description", product.get("description"))),
                "sku": sku, "size": [size] if is_image_url(size) else []}
    size = overrides.get("sizeChart", product.get("sizeChart"))
    cover = overrides.get("videoThumbnail", product.get("videoThumbnail"))
    return {"main": list(overrides.get("images", shopee.images(product, mode))),
            "detail": html_images(overrides.get("descriptionInfoStr", product.get("descriptionInfoStr")))
            if overrides.get("descriptionType", product.get("descriptionType")) == "extended" else [],
            "sku": sku, "size": [size] if is_image_url(size) else [], "cover": [cover] if is_image_url(cover) else []}


def write_group(platform, product, overrides, group, urls, mode="global"):
    if group not in GROUPS[platform]:
        raise ValueError("当前平台不支持此图片分组")
    if (platform == "shopee" and group == "detail"
            and overrides.get("descriptionType", product.get("descriptionType")) != "extended"):
        raise ValueError("Shopee 店铺使用普通描述，请在商品图片栏编辑图片")
    if group == "main":
        if platform == "amazon":
            overrides["main_image"] = urls[0] if urls else ""
            overrides["extra_images"] = list(urls[1:])
        else:
            overrides["main_images" if platform == "alibaba" else "images"] = list(urls)
    elif group == "extra":
        if platform == "amazon":
            overrides["extra_images"] = list(urls)
        else:
            raise ValueError("当前平台不支持此图片分组")
    elif group == "detail":
        if platform == "alibaba":
            overrides["detail_images"] = list(urls)
        else:
            key = "descriptionInfoStr" if platform == "shopee" else "description"
            overrides[key] = replace_html_images(overrides.get(key, product.get(key)), urls)
    elif group == "sku":
        existing = read_groups(platform, product, overrides, mode)["sku"]
        if len(existing) != len(urls):
            raise ValueError("规格图必须与原商品规格一一对应")
        values = iter(urls)
        key = {"alibaba": "product_sku", "tiktok": "sku_images", "shopee": "variant_edits"}[platform]
        overrides[key] = _visit_sku_images(platform, product, overrides, mode, lambda old: next(values))
    else:
        key = "videoThumbnail" if group == "cover" else "size_chart" if platform == "tiktok" else "sizeChart"
        overrides[key] = urls[0] if urls else ""


def batch_rules(cfg, platform):
    """Read saved visual rules or migrate the prior gallery positions in memory."""
    from .image_index import parse_indices
    from .platform_presets import IMAGE_STYLES

    groups = GROUPS[platform]
    prefix = platform + "_collect_image_"
    raw = getattr(cfg, prefix + "rules", "")
    if raw in (None, ""):
        positions = parse_indices(getattr(cfg, prefix + "indices", "1") or "1", groups["main"].limit)
        operation = str(getattr(cfg, prefix + "style", "white") or "white")
        if operation not in IMAGE_STYLES:
            operation = "custom"
        prompt = str(getattr(cfg, prefix + "prompt", "") or IMAGE_STYLES["white"][1])
        return [dict(group="main", position=str(i), operation=operation, prompt=prompt) for i in positions]
    rows = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(rows, list):
        raise ValueError("自动图片规则无效")
    result = []
    for row in rows:
        if platform == "amazon" and isinstance(row, dict) and row.get("group") == "detail":
            continue  # The previous release offered an unsupported description-image rule.
        if not isinstance(row, dict) or row.get("group") not in groups:
            raise ValueError("自动图片规则包含无效分组")
        key, pos = row["group"], str(row.get("position") or "")
        if pos != "all" and (not pos.isascii() or not pos.isdigit() or not 1 <= int(pos) <= groups[key].limit):
            raise ValueError("自动图片规则包含无效位置")
        prompt, operation = str(row.get("prompt") or "").strip(), str(row.get("operation") or "custom")
        if not prompt or operation not in (*IMAGE_STYLES, "custom"):
            raise ValueError("自动图片规则的处理方式或提示词无效")
        result.append(dict(group=key, position=pos, operation=operation, prompt=prompt))
    return result


def edit_and_host(pipeline, source, prompt, references=()):
    from .hosting import upload

    if references:
        paths = [pipeline._download(url) for url in (source, *references)]
        url = pipeline.svc.edit_many(paths, prompt)
    else:
        result = pipeline.run(source, white_bg=False, remove_wm=False, edit=True, prompt=prompt)
        if not result.ok:
            raise ValueError(next((step.error for step in result.steps if not step.ok), "图片处理失败"))
        url = result.last_url()
    if not is_image_url(url):
        raise ValueError("图片模型未返回有效地址")
    hosted = upload(pipeline._download(url), base=pipeline.cfg.image_host_url, key=pipeline.cfg.image_host_key)
    if not is_image_url(hosted):
        raise ValueError("图床未返回有效图片地址")
    return str(hosted).strip()
