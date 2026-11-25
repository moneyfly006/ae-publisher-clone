"""把 AI 产物 / 预设资料映射成 Alibaba(alibabaSave.txt) 保存结构，并做必填校验。"""
from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy

MAX_SUBJECT = 128
MAX_KEYWORDS = 5
MIN_KEYWORDS = 3
MAX_MAIN_IMAGES = 6
MAX_DETAIL_IMAGES = 20

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "your", "our", "are",
    "you", "can", "will", "all", "new", "high", "low", "good", "best", "hot",
    "sale", "free", "shipping", "custom", "customized", "oem", "odm",
}


def _cfg_get(cfg, key: str, default=""):
    """cfg 可能是 Config 对象、dict 或 None（测试/脚本）。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _as_list(v, sep=None) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                d = json.loads(s)
                return d if isinstance(d, list) else []
            except Exception:
                return []
        return [x.strip() for x in (s.split(sep) if sep else s.split(",")) if x.strip()]
    return []


def _json(v) -> str:
    return v if isinstance(v, str) else json.dumps(v if v is not None else [], ensure_ascii=False)


def main_images_from(product: dict, images: list | None = None) -> list[str]:
    if images:
        out = [str(u).strip() for u in images if str(u).strip()]
    else:
        out = [u for u in (product.get("mainImage") or "").split(",") if u.strip()]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq[:MAX_MAIN_IMAGES]


_PLATFORM_UNREADABLE_HOSTS = ("image.moneyfly.club",)


def _platform_readable(url: str) -> bool:
    low = str(url or "").lower()
    return not any(host in low for host in _PLATFORM_UNREADABLE_HOSTS)


def _original_main_images(product: dict) -> list[str]:
    out = []
    for key in ("mainImage", "draftImgUrl", "imgUrl"):
        raw = str((product or {}).get(key) or "")
        for part in raw.replace("|", ",").split(","):
            u = part.strip()
            if u.startswith(("http://", "https://")) and u not in out:
                out.append(u)
    # 原商品主图可能已被替换成 AE 托管图；productAttr/详情里仍保留 alicdn 原图。
    try:
        blob = json.dumps(product or {}, ensure_ascii=False)
        for u in re.findall(r'https?://[^"\\,\s]+?\.(?:jpg|jpeg|png|webp)', blob, re.I):
            if "alicdn.com" in u and u not in out:
                out.append(u)
    except Exception:
        pass
    return out


def _original_detail_urls(product: dict) -> list[str]:
    out = []
    for row in _as_list((product or {}).get("detailImage")):
        u = str((row or {}).get("url") or "").strip() if isinstance(row, dict) else ""
        if u.startswith(("http://", "https://")) and u not in out:
            out.append(u)
    return out


def _ensure_platform_images(imgs: list[str], product: dict, cfg=None) -> list[str]:
    """平台读不到的托管图回退原图；必要时补足 4 张主图。"""
    all_originals = [u for u in _original_main_images(product) if _platform_readable(u)]
    alicdn = [u for u in all_originals if "alicdn.com" in u]
    originals = alicdn or all_originals
    require_ali = bool(_cfg_get(cfg, "alibaba_ensure_min_images", False))
    out = []
    for index, url in enumerate(imgs or []):
        if _platform_readable(url) and not (require_ali and originals and "alicdn.com" not in url):
            out.append(url)
        elif originals:
            out.append(originals[index % len(originals)])
        # 没有可读原图时丢弃平台读不到的托管图，避免整件发布因图片失败。
    if require_ali:
        for url in originals:
            if len(out) >= 4:
                break
            if url not in out:
                out.append(url)
    # 主图内部去重，避免平台报重复图片。
    uniq = []
    for u in out:
        if u not in uniq:
            uniq.append(u)
    return uniq[:MAX_MAIN_IMAGES]


def detail_urls(product: dict, images: list | None = None) -> list[str]:
    if images:
        return [str(u).strip() for u in images if str(u).strip()]
    rows = _as_list(product.get("detailImage"))
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("url"):
            out.append(str(r["url"]))
        elif isinstance(r, str):
            out.append(r)
    return out


def ensure_save_platform_images(save: dict, product: dict, cfg=None) -> list[str]:
    """图片兼容处理会替换成托管图；此处在保存前再回退平台读不到的主图/详情图。"""
    if not isinstance(save, dict):
        return []
    notes = []
    imgs = [u.strip() for u in str(save.get("mainImage") or "").split(",") if u.strip()]
    fixed = _ensure_platform_images(imgs, product or {}, cfg)
    if fixed != imgs:
        save["mainImage"] = ",".join(fixed)
        save["draftImgUrl"] = "|".join(fixed)
        notes.append("主图已回退为平台可读原图")
    try:
        rows = _as_list(save.get("detailImage"))
    except Exception:
        rows = []
    if rows and isinstance(rows, list):
        require_ali = bool(_cfg_get(cfg, "alibaba_ensure_min_images", False))
        originals = [u for u in _original_detail_urls(product or {}) if _platform_readable(u)]
        _main_urls = {u.strip() for u in str(save.get("mainImage") or "").split(",") if u.strip()}
        alicdn_detail = []
        for u in originals + _original_main_images(product or {}):
            if ("alicdn.com" in u and _platform_readable(u)
                    and u not in alicdn_detail and u not in _main_urls):
                alicdn_detail.append(u)
        if require_ali and alicdn_detail:
            # 只用唯一的 alicdn 图，避免平台报「详情图片重复」；不足则精简，不循环。
            keep = min(len(rows), len(alicdn_detail))
            out = []
            for index in range(keep):
                row = dict(rows[index]) if isinstance(rows[index], dict) else {}
                row["url"] = alicdn_detail[index]
                out.append(row)
            save["detailImage"] = json.dumps(out, ensure_ascii=False) if out else "[]"
            notes.append("详情图已替换为唯一 alicdn 图片")
        else:
            changed = False
            out = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url") or "").strip()
                if _platform_readable(url):
                    out.append(row)
                elif originals:
                    row = dict(row)
                    row["url"] = originals[index % len(originals)]
                    out.append(row)
                    changed = True
            if changed:
                save["detailImage"] = json.dumps(out, ensure_ascii=False)
                notes.append("详情图已回退为平台可读原图")
    if notes:
        save.setdefault("_autofilled", []).extend(notes)
    return notes


def clean_keywords(text: str, limit: int = MAX_KEYWORDS) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", str(text or "").lower())
    seen, out = set(), []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.add(w); out.append(w)
        if len(out) >= limit:
            break
    return out


def split_keywords(raw, limit: int = MAX_KEYWORDS) -> list[str]:
    """把 keywords 字符串（逗号/分号/列表）按「短语」拆开并去重。

    与 clean_keywords 不同：这里保留短语原样（"fiberglass pool" 不会被拆成
    fiberglass/pool），用于保留商品原有语义。
    """
    if isinstance(raw, (list, tuple, set)):
        rows = [str(x).strip() for x in raw]
    else:
        s = str(raw or "").strip()
        if not s:
            return []
        sep = ";" if ("," not in s and ";" in s) else ","
        rows = [x.strip() for x in s.split(sep)]
    out, seen = [], set()
    for x in rows:
        key = x.lower()
        if x and key not in seen:
            seen.add(key)
            out.append(x)
        if len(out) >= limit:
            break
    return out


def smart_keywords_from_title(title: str, limit: int = 3) -> list[str]:
    """从标题提取 3 个高价值短语关键词（确定性规则，不调用 AI）。"""
    text = re.sub(r"[^A-Za-z0-9\- ]+", " ", str(title or "")).strip()
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)]
    stop = STOPWORDS | {"machine", "machinery", "equipment", "product", "products"}
    kept = [w for w in words if w.lower() not in stop]
    out, seen = [], set()
    for n in (3, 2, 1):
        for i in range(max(0, len(kept) - n + 1)):
            phrase = " ".join(kept[i:i + n]).strip().lower()
            if not phrase or phrase in seen or len(phrase) < 4:
                continue
            seen.add(phrase); out.append(phrase)
            if len(out) >= limit:
                return out
    return out


def keywords_for(save: dict, cfg=None, override: list | str | None = None) -> list[str]:
    """关键词：override > 配置 alibaba_keywords > 从标题提取，至少 3 个。"""
    kws = []
    if override:
        kws = ([x.strip() for x in override.split(",")] if isinstance(override, str)
               else [str(x).strip() for x in override])
    if not kws and cfg is not None:
        kws = [x.strip() for x in str(_cfg_get(cfg, "alibaba_keywords", "") or "").split(",") if x.strip()]
    kws = [k for k in kws if k]
    if len(kws) < MIN_KEYWORDS:
        for w in clean_keywords(save.get("subject", ""), MAX_KEYWORDS):
            if w not in kws:
                kws.append(w)
            if len(kws) >= MAX_KEYWORDS:
                break
    return kws[:MAX_KEYWORDS]


def keywords_effective(save: dict, cfg=None, override: list | str | None = None) -> list[str]:
    """关键词优先级：overrides.keywords > cfg.alibaba_keywords > 商品原 keywords > 标题提取。

    之前无配置时直接用标题提取，把商品原有关键词整个覆盖掉（如
    "fiberglass pool, container pool" → "fiberglass, swimming, pool"）。这里保留
    商品原有短语，只在完全没有关键词时才从标题生成。
    """
    if override:
        kws = split_keywords(override)
        if kws:
            return kws
    cfg_kws = split_keywords(_cfg_get(cfg, "alibaba_keywords", "") or "")
    if cfg_kws:
        return cfg_kws
    if bool(_cfg_get(cfg, "alibaba_auto_keywords", True)):
        smart = smart_keywords_from_title(save.get("subject") or "", min(3, MAX_KEYWORDS))
        if smart:
            return smart
    prod_kws = split_keywords(save.get("keywords") or "")
    if prod_kws:
        return prod_kws
    return clean_keywords(save.get("subject", ""), MAX_KEYWORDS)


def price_tiers(cfg=None, overrides: dict | None = None) -> list[dict]:
    """阶梯价：overrides['price_tiers'] > 配置 alibaba_price_tiers > 单品价。"""
    raw = None
    if overrides and overrides.get("price_tiers"):
        raw = overrides["price_tiers"]
    elif cfg is not None:
        raw = _cfg_get(cfg, "alibaba_price_tiers", "") or ""
    rows = _as_list(raw) if not isinstance(raw, list) else raw
    tiers = []
    for r in rows:
        if isinstance(r, dict):
            q, p = r.get("start_quantity") or r.get("qty"), r.get("price")
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            q, p = r[0], r[1]
        else:
            continue
        try:
            q, p = int(float(q)), float(p)
        except Exception:
            continue
        if q > 0 and p > 0:
            tiers.append({"start_quantity": q, "price": round(p, 2)})
    tiers.sort(key=lambda x: x["start_quantity"])
    if not tiers:
        price = None
        if overrides and overrides.get("price"):
            price = overrides["price"]
        elif cfg is not None:
            price = _cfg_get(cfg, "price_fixed", None)
        try:
            price = float(price) if price else 0
        except Exception:
            price = 0
        if price > 0:
            tiers = [{"start_quantity": 1, "price": round(price, 2)}]
    return tiers


def merge_attributes(product: dict, cfg=None, overrides: dict | None = None,
                     attr_defs: list | None = None) -> list[dict]:
    """保留原属性，按预设/模板补齐缺失项（模板值必须能匹配目标类目候选值）。"""
    attrs = _as_list(product.get("attributes"))
    attrs = [deepcopy(a) for a in attrs if isinstance(a, dict)]
    template_attrs = _as_list((overrides or {}).get("template_attributes"))
    if template_attrs:
        have_names = {str(a.get("attribute_name") or a.get("name") or "").strip().lower()
                      for a in attrs}
        defs = [d for d in (attr_defs or []) if isinstance(d, dict)]
        by_name, by_id = _build_def_index(defs) if defs else ({}, {})
        for a in template_attrs:
            if not isinstance(a, dict):
                continue
            name = str(a.get("attribute_name") or a.get("name") or "").strip()
            if not name or name.lower() in have_names:
                continue
            tpl_val = str(a.get("value_name") or a.get("value") or "").strip()
            if not tpl_val:
                continue
            if defs:
                # 目标类目有定义时：模板值必须命中目标类目候选，不能直接搬 value_id。
                d = by_name.get(_norm(name))
                if d is None:
                    for target in ATTRIBUTE_ALIASES.get(_norm(name), ()):
                        if target in by_name:
                            d = by_name[target]; break
                if d is None:
                    continue
                res = resolve_attr_value(d, tpl_val)
                if not res.get("matched") or res.get("value_id") in (None, ""):
                    continue
                attrs.append({"attribute_id": _def_attr_name_id(d) or "-1",
                              "attribute_name": _def_label(d),
                              "value_id": str(res.get("value_id")),
                              "value_name": str(res.get("value_name") or tpl_val)})
                have_names.add(_def_label(d).lower())
            else:
                attrs.append(deepcopy(a)); have_names.add(name.lower())
    presets = {}
    raw = (overrides or {}).get("attributes") or _cfg_get(cfg, "alibaba_attr_presets", "") or ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            presets = json.loads(raw)
        except Exception:
            presets = {}
    elif isinstance(raw, dict):
        presets = raw
    # 空值属性不算「已有」：采集商品常带 attribute_name 但 value 为空，
    # 必须让预设值填进去，否则保存前校验仍报必填缺失。
    by_name = {}
    for a in attrs:
        nm = str(a.get("attribute_name") or a.get("name") or "").strip().lower()
        if nm:
            by_name.setdefault(nm, a)
    have = {nm for nm, a in by_name.items()
            if str(a.get("value_name") or a.get("value") or "").strip()}
    for name, val in presets.items():
        key = str(name).strip().lower()
        if not key:
            continue
        v = str(val if val is not None else "").strip()
        if key in by_name:
            if not str(by_name[key].get("value_name") or by_name[key].get("value") or "").strip():
                by_name[key]["value_id"] = "-1"
                by_name[key]["value_name"] = v
            continue
        if key in have:
            continue
        attrs.append({"attribute_id": "-1", "attribute_name": str(name),
                      "value_id": "-1", "value_name": v})
    return attrs


def prepare_video_for_save(product: dict, cfg, save: dict, overrides: dict | None = None,
                           log=None) -> str:
    """采集视频优先；没有时用主图/详情图生成轮播视频并写 videoUrl。"""
    ov = overrides or {}
    source = str(_cfg_get(cfg, "alibaba_video_source", "auto") or "auto").lower()
    if source == "none":
        return ""
    for key in ("mainVideoId", "detailVideoId", "videoUrl", "video"):
        v = ov.get(key) or product.get(key)
        if v not in (None, "", 0, "0", "None"):
            save[key] = v
            return str(v)
    if source == "collected":
        return ""
    raw = (ov.get("main_images") or save.get("mainImage")
           or product.get("mainImage") or product.get("draftImgUrl") or "")
    urls = [x.strip() for x in str(raw).replace("|", ",").split(",") if x.strip()]
    try:
        _detail = product.get("detailImage") or []
        if isinstance(_detail, str):
            import json as _json
            _detail = _json.loads(_detail or "[]")
        for _d in _detail if isinstance(_detail, list) else []:
            _u = str((_d or {}).get("url") or "").strip() if isinstance(_d, dict) else ""
            if _u and _u not in urls:
                urls.append(_u)
    except Exception:
        pass
    if not urls:
        return ""
    try:
        import tempfile
        import time
        from pathlib import Path as _Path
        import requests
        from PIL import Image
        from .video import generate_carousel_video, find_ffmpeg
        from .hosting import upload_video_file
        if not find_ffmpeg():
            if log:
                log("没有找到 ffmpeg，无法生成轮播视频", "WARN")
            return ""
        tmp = _Path(tempfile.mkdtemp())
        imgs = []
        for i, u in enumerate(urls):
            if len(imgs) >= 6:
                break
            try:
                r = None
                for attempt in range(3):
                    r = requests.get(u, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code != 429:
                        break
                    time.sleep(1.0 * (attempt + 1))
                r.raise_for_status()
                raw_path = tmp / f"src{i}.img"
                raw_path.write_bytes(r.content)
                pth = tmp / f"img{i}.jpg"
                with Image.open(raw_path) as im:
                    im.convert("RGB").save(pth, "JPEG", quality=92)
                imgs.append(pth)
            except Exception as exc:
                if log:
                    log(f"视频素材第 {i+1} 张下载/解码失败：{str(exc)[:80]}", "WARN")
                continue
        if len(imgs) < 2:
            if log:
                log(f"视频素材不足（成功 {len(imgs)} 张），跳过轮播视频", "WARN")
            return ""
        out = tmp / "alibaba-carousel.mp4"
        generate_carousel_video(
            imgs, out,
            slot_seconds=float(_cfg_get(cfg, "alibaba_video_slot_seconds", 2.5) or 2.5),
            transition_seconds=float(_cfg_get(cfg, "alibaba_video_transition_seconds", 0.5) or 0.5),
            size=str(_cfg_get(cfg, "alibaba_video_size", "1080x1080") or "1080x1080"),
            cover=True, fade_edges=True)
        url = upload_video_file(out)
        if url:
            save["videoUrl"] = url
            if log:
                log(f"轮播视频已生成并托管：{url[-40:]}", "INFO")
            return url
    except Exception as exc:
        if log:
            log(f"轮播视频生成失败：{str(exc)[:120]}", "WARN")
    return ""


def normalize_publishable_main_images(client, product: dict, save: dict) -> list[str]:
    """发布前主图兜底：移除不可读/低尺寸图，并用详情图补足。"""
    import requests
    from io import BytesIO
    from PIL import Image
    raw = str(save.get("mainImage") or "").strip()
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    if not urls:
        return []
    def _custom(u: str) -> bool:
        return "moneyfly.club" in u.lower() or "132.226.1.44" in u
    detail = []
    try:
        rows = product.get("detailImage") or []
        if isinstance(rows, str):
            import json as _json
            rows = _json.loads(rows or "[]")
        for row in rows if isinstance(rows, list) else []:
            u = str((row or {}).get("url") or "").strip() if isinstance(row, dict) else ""
            if u and u not in urls and u not in detail:
                detail.append(u)
    except Exception:
        pass
    candidates = [u for u in urls if not _custom(u)] + detail
    kept, dropped, seen = [], [], set()
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        try:
            r = requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            with Image.open(BytesIO(r.content)) as im:
                w, h = im.size
            if w < 800 or h < 800 or (h and w / h < 0.75):
                dropped.append(f"{w}x{h}(比例{w/h:.2f})")
                continue
            kept.append(u)
            if len(kept) >= 6:
                break
        except Exception:
            dropped.append("无法读取")
            continue
    out = kept or urls
    if out != urls:
        save["mainImage"] = ",".join(out)
        if save.get("draftImgUrl"):
            save["draftImgUrl"] = "|".join(out)
        save.setdefault("_image_repair", []).append(
            f"主图仅保留可验证尺寸的图片（{len(out)} 张）"
            + (f"；剔除：{'、'.join(dropped[:5])}" if dropped else ""))
    return out



def resolve_category(product: dict, cfg=None, overrides: dict | None = None) -> tuple[str, str]:
    """确定本次保存使用的类目：(categoryId, fullCid)。

    优先级：overrides > cfg > 商品原值。只给了 categoryId 时，fullCid 用
    原 fullCid / cfg.fullCid 的路径前缀 + 新类目末段拼出来，避免「校验用新类目、
    保存用旧类目」这种 UI 选类目不生效的问题。
    """
    ov = dict(overrides or {})
    product = product or {}

    def _pick(*vals) -> str:
        for v in vals:
            if isinstance(v, (list, tuple)):
                s = ",".join(str(x).strip() for x in v if str(x).strip())
            else:
                s = str(v or "").strip()
            if s:
                return s
        return ""

    cid = _pick(ov.get("category_id"), ov.get("categoryId"),
                _cfg_get(cfg, "alibaba_category_id"), product.get("categoryId"))
    # 自动类目：商品没有类目时，用平台预测候选（forecastCategoryId）兜底；
    # 类目确定后再由 run_alibaba_product 按该类目拉取并补全必填属性。
    if not cid and bool(_cfg_get(cfg, "auto_detect_category", True)):
        try:
            from .smt_attributes import suggest_category_id
            cid = str(suggest_category_id(product) or "").strip()
        except Exception:
            cid = ""
    full = _pick(ov.get("full_cid"), ov.get("fullCid"),
                 _cfg_get(cfg, "alibaba_full_cid"), product.get("fullCid"))
    parts = [p.strip() for p in full.split(",") if p.strip()] if full else []
    if not cid and parts:
        cid = parts[-1]
    if cid:
        if parts and parts[-1] == cid:
            full = ",".join(parts)
        else:
            prefix = parts[:-1] if parts else []
            if not prefix:
                prod_full = str(product.get("fullCid") or "").strip()
                prod_parts = [p.strip() for p in prod_full.split(",") if p.strip()]
                prefix = prod_parts[:-1] if prod_parts else []
            full = ",".join(prefix + [cid])
    return cid, full


def auto_alibaba_category(client, product: dict, cfg=None, allowed=None) -> tuple[str, str]:
    """按商品标题/关键词调用 Alibaba 类目搜索，选择最匹配的叶子类目。

    返回 (categoryId, fullCid)；找不到返回 ("", "")。fullCid 使用平台
    nodePathId 的 "/" 路径转换成逗号分隔，保存时与 edit.json 的 fullCid 一致。
    """
    if client is None or not hasattr(client, "search_categories"):
        return "", ""
    title = str(product.get("subject") or product.get("title") or "").strip()
    keywords = str(product.get("keywords") or "").strip()
    raw = " ".join(x for x in (title, keywords) if x)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", raw)][:20]
    # 标题/关键词中词频越高越能代表商品主类目；pools/pool、rigs/rig 归一。
    def _stem(w: str) -> str:
        w = w.lower()
        return w[:-1] if len(w) > 4 and w.endswith("s") else w
    token_freq = {}
    for w in words:
        st = _stem(w)
        token_freq[st] = token_freq.get(st, 0) + 1
    words = list(token_freq.keys())
    phrases = []
    if title:
        phrases.append(title[:80])
    if keywords:
        for part in re.split(r"[,;，；]", keywords):
            if part.strip():
                phrases.append(part.strip())
    # 原长标题在 searchCategory 常返回空；单关键词和双词更接近页面搜索行为。
    # 按词频排序，先搜最能代表商品主类目的词（pool 优先于 design/window）。
    for w in sorted(token_freq, key=lambda x: (-token_freq[x], x))[:10]:
        phrases.append(w)
    for i in range(min(6, max(0, len(words) - 1))):
        phrases.append(" ".join(words[i:i + 3]))
    for i in range(max(0, len(words) - 1)):
        phrases.append(" ".join(words[i:i + 2]))
    seen, candidates = set(), []
    for phrase in phrases[:8]:
        try:
            rows = client.search_categories(phrase, max_nodes=60) or []
        except Exception:
            rows = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            cid = str(r.get("categoryId") or "").strip()
            if not cid or cid in seen:
                continue
            if not int(r.get("isleaf") or 0):
                # 搜索通常返回叶子；保留非叶子做兜底但不优先。
                pass
            seen.add(cid)
            name_en = str(r.get("nameEn") or "").lower()
            name_zh = str(r.get("nameZh") or "").lower()
            path = str(r.get("nodePath") or "").lower()
            title_l = raw.lower()
            title_words = {_stem(w) for w in words}
            name_words = {_stem(w) for w in re.findall(r"[a-z][a-z0-9\-]{2,}", name_en)}
            overlap = title_words & name_words
            path_hits = {w for w in title_words if w in path}
            score = 3.0 * sum(token_freq.get(w, 1) for w in overlap)
            score += 1.2 * sum(token_freq.get(w, 1) for w in path_hits)
            # 类目名本身就是标题里的词（如 Pool），优先于 Pool Lights 这类细分配件。
            name_stem = _stem(name_en)
            if name_stem and name_stem in title_words:
                score += 5.0 * token_freq.get(name_stem, 1)
            score -= max(0, len(name_words) - 2) * 0.8
            if int(r.get("isleaf") or 0):
                score += 1.5
            if str(r.get("categoryId") or "") == str(product.get("categoryId") or ""):
                score += 5.0
            candidates.append((score, len(path), r))
    # 店铺经营类目范围：平台只允许在线经营类目发品，不在范围必须先过滤。
    _allowed = {str(x) for x in (allowed or []) if str(x)}
    if _allowed:
        filtered = [x for x in candidates if str(x[2].get("categoryId") or "") in _allowed]
        if filtered:
            candidates = filtered
    if not candidates:
        return "", ""
    candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    best = candidates[0][2]
    cid = str(best.get("categoryId") or "").strip()
    if _allowed and cid not in _allowed:
        # 没有匹配到允许类目时，回退到「其他机械/工业设备」这类通用类目。
        try:
            for _cid in _allowed:
                _d = client.category_detail(_cid, None) if hasattr(client, "category_detail") else {}
                _nm = str((_d or {}).get("nameEn") or "")
                if "other machinery" in _nm.lower() or "其他机械" in _nm:
                    return str(_cid), str((_d or {}).get("fullCid") or _cid)
        except Exception:
            pass
        return "", ""
    path = str(best.get("nodePathId") or "").strip()
    full = ",".join(x for x in path.split("/") if x) if path else ""
    return cid, full


def build_alibaba_save(product: dict, cfg=None, overrides: dict | None = None,
                       client_cls=None, attr_defs: list | None = None,
                       client=None) -> dict:
    """edit.json 商品 + AI 产物 → alibabaSave 字典。"""
    from ..integrations.alibaba import AlibabaClient as _AC
    cls = client_cls or _AC
    save = cls.build_save_dict(product)
    ov = dict(overrides or {})
    _tpl_fields = ov.get("template_fields") if isinstance(ov.get("template_fields"), dict) else None
    if _tpl_fields:
        for _k in ("priceType", "bulkDiscountPrices", "wholesaleTrade", "salePkgDesc",
                   "logisticsProperty", "shipmentPlace", "warehouseType",
                   "marketSamplingQuantity", "marketSamplingPrice", "marketSample"):
            if not str(save.get(_k) or "").strip() or str(save.get(_k) or "").strip() in ("[]", "{}"):
                if str(_tpl_fields.get(_k) or "").strip():
                    save[_k] = _tpl_fields[_k]
    # 物流默认：只补缺失，不覆盖采集/模板已有真实值。
    try:
        _wt = save.get("wholesaleTrade")
        _wt = json.loads(_wt) if isinstance(_wt, str) and _wt.strip().startswith("{") else (_wt if isinstance(_wt, dict) else {})
        _wt = dict(_wt or {})
        _changed = False
        if not _wt.get("weight"):
            _w = float(_cfg_get(cfg, "alibaba_default_weight", 100) or 100)
            if _w > 0:
                _wt["weight"] = _w; _changed = True
        if not str(_wt.get("package_size") or "").strip():
            _ps = str(_cfg_get(cfg, "alibaba_default_package_size", "10X10X10") or "")
            if _ps:
                _wt["package_size"] = _ps; _changed = True
        if not str(_wt.get("shipping_line_template_id") or "").strip():
            _fid = ""
            _fname = str(_cfg_get(cfg, "alibaba_default_freight_name", "买卖双方协商物流") or "")
            if client is not None and _fname:
                try:
                    for _r in client.freight_templates(str(_cfg_get(cfg, "alibaba_shop_id", "") or "")):
                        if str(_r.get("templateName") or "").strip() == _fname:
                            _fid = str(_r.get("templateId") or "")
                            break
                except Exception:
                    _fid = ""
            if not _fid:
                _fid = str(_cfg_get(cfg, "alibaba_default_freight_id", "") or "")
            if _fid:
                _wt["shipping_line_template_id"] = _fid; _changed = True
        if _changed:
            save["wholesaleTrade"] = json.dumps(_wt, ensure_ascii=False)
        if not str(save.get("logisticsProperty") or "").strip():
            save["logisticsProperty"] = str(_cfg_get(cfg, "alibaba_default_logistics_property", "general_cargo_0") or "general_cargo_0")
        if str(save.get("marketSample") or "") in ("", "None"):
            save["marketSample"] = str(_cfg_get(cfg, "alibaba_default_sample_service", "0") or "0")
    except Exception:
        pass

    # 全局配置覆盖商品原有数据时留痕（包装/重量/HS/公司信息），供流水线提示复核。
    _overwrites: list[str] = []

    def _note_overwrite(label: str, old, new) -> None:
        old_s, new_s = str(old or "").strip(), str(new or "").strip()
        if old_s and new_s and old_s != new_s:
            _overwrites.append(f"{label}：{old_s} → {new_s}")

    # 类目：overrides > cfg > 商品原值。UI 里选的类目必须真的写进保存包，
    # 否则会「按新类目校验、按旧类目保存」。
    cid, full_cid = resolve_category(product, cfg, ov)
    if cid:
        save["categoryId"] = cid
    if full_cid:
        save["fullCid"] = full_cid
    # 模板属性由 merge_attributes 按目标类目候选逐项校验后补齐；
    # 类目不同时不再整批丢弃，也不会直接搬模板 value_id。

    subject = str(ov.get("subject", save.get("subject")) or "").strip()
    save["subject"] = subject[:MAX_SUBJECT]

    kws = keywords_effective(save, cfg, ov.get("keywords"))
    save["keywords"] = ", ".join(kws)

    if "main_images" in ov and len(ov["main_images"]) > 6:
        raise ValueError("Alibaba 商品主图最多 6 张，请删减后重试")
    imgs = _ensure_platform_images(
        main_images_from(product, ov.get("main_images")), product, cfg)
    if imgs or "main_images" in ov:
        if "main_images" in ov and not ov["main_images"]:
            imgs = []
        save["mainImage"] = ",".join(imgs)
        save["draftImgUrl"] = "|".join(imgs)

    if "detail_images" in ov and len(ov["detail_images"]) > MAX_DETAIL_IMAGES:
        ov["detail_images"] = list(ov["detail_images"])[:MAX_DETAIL_IMAGES]
        save.setdefault("_autofilled", []).append(
            f"详情图超过 {MAX_DETAIL_IMAGES} 张，已自动保留前 {MAX_DETAIL_IMAGES} 张")
    det = detail_urls(product, ov.get("detail_images"))
    if det or "detail_images" in ov:
        originals = _as_list(product.get("detailImage"))
        if "detail_images" in ov:
            det = list(ov["detail_images"])
        metadata = {}
        for row in originals:
            if isinstance(row, dict) and row.get("url"):
                metadata.setdefault(str(row["url"]), []).append(row)
        _det_originals = [u for u in _original_detail_urls(product) if _platform_readable(u)]
        _det_fixed = []
        for _di, _du in enumerate(det):
            if _platform_readable(_du):
                _det_fixed.append(_du)
            elif _det_originals:
                _det_fixed.append(_det_originals[_di % len(_det_originals)])
        det = _det_fixed[:MAX_DETAIL_IMAGES]
        rows = []
        for url in det:
            source = metadata.get(url, [])
            original = source.pop(0) if source else {}
            rows.append({"displayName": "default", "value": -2, "text": "", **original, "url": url})
        save["detailImage"] = json.dumps(rows, ensure_ascii=False)

    # 平台 textDesc 只接受纯文本且 ≤2000 字符（HTML 会报「不能输入HTML脚本」）。
    # 另外平台禁止站外联系方式：采集来的详情常带 WhatsApp/邮箱/电话，保存前统一移除，
    # 移除内容写入 _contact_removed 供流水线提示（不静默改内容）。
    _contact_removed: list[str] = []
    _desc = text_desc_plain(str(ov.get("text_desc", save.get("textDesc")) or ""))
    _desc_clean, _desc_removed = strip_contact_info(_desc)
    save["textDesc"] = _desc_clean
    _contact_removed.extend(_desc_removed)
    if cfg is not None:
        _company = str(getattr(cfg, "alibaba_company_desc", "") or "").strip()
        if _company:
            _c_clean, _c_removed = strip_contact_info(text_desc_plain(_company, limit=4000))
            save["companyDesc"] = _c_clean
            _contact_removed.extend(f"companyDesc:{x}" for x in _c_removed)
            _note_overwrite("公司介绍", product.get("companyDesc"), _c_clean)
        _faq = str(getattr(cfg, "alibaba_company_faq", "") or "").strip()
        if _faq:
            _f_clean, _f_removed = strip_contact_info(text_desc_plain(_faq, limit=4000))
            save["companyFaqDesc"] = _f_clean
            _contact_removed.extend(f"companyFaqDesc:{x}" for x in _f_removed)
    save["_contact_removed"] = _contact_removed

    attrs = merge_attributes(product, cfg, ov, attr_defs=attr_defs)
    if "native_attributes" in ov:
        if not isinstance(ov["native_attributes"], list):
            raise ValueError("当前商品属性必须是列表")
        attrs = deepcopy(ov["native_attributes"])
    # AI/自动修复允许显式替换已有占位属性（Other），普通 attributes 预设仍只补缺失。
    force_attrs = ov.get("replace_attributes")
    if isinstance(force_attrs, dict) and force_attrs:
        by_name = {str(a.get("attribute_name") or "").strip().casefold(): a for a in attrs}
        defs = [d for d in (attr_defs or []) if isinstance(d, dict)]
        def_index = {str(d.get("nameEn") or d.get("nameZh") or "").strip().casefold(): d
                     for d in defs if str(d.get("nameEn") or d.get("nameZh") or "").strip()}
        for raw_name, raw_value in force_attrs.items():
            name = str(raw_name or "").strip()
            value = str(raw_value or "").strip()
            if not name or not value:
                continue
            key = name.casefold()
            definition = def_index.get(key)
            resolved = resolve_attr_value(definition, value) if definition else {}
            value_name = str(resolved.get("value_name") or value)
            value_id = resolved.get("value_id") if resolved.get("matched") else "-1"
            target = by_name.get(key)
            if target is None:
                target = {"attribute_id": "-1", "attribute_name": name}
                attrs.append(target); by_name[key] = target
            target["attribute_name"] = name
            target["value_name"] = value_name
            target["value_id"] = str(value_id) if value_id not in (None, "") else "-1"
    filled, unresolved, attr_reasons = [], [], []
    if attr_defs:
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": attrs}, attr_defs, cfg, ov, reasons=attr_reasons)
    # Brand Name 占位值（NoEnName_Null / 空）会被平台判为数据不完整。
    _brand = str(_cfg_get(cfg, "alibaba_brand", "") or "Generic").strip() or "Generic"
    _bad_brand = {"", "noennname_null", "noenname_null", "none", "null", "n/a", "no brand"}
    for _a in attrs:
        if not isinstance(_a, dict):
            continue
        if str(_a.get("attribute_name") or "").strip().casefold() == "brand name":
            if str(_a.get("value_name") or "").strip().casefold() in _bad_brand:
                _a["value_name"] = _brand
                _a["value_id"] = "-1"
    if attrs:
        save["attributes"] = json.dumps(attrs, ensure_ascii=False)
    save["_autofilled"] = filled
    save["_unresolved"] = unresolved
    # 可读中文原因（UI 展示用）：unresolved 里是短标识，这里是「为什么没补齐/该怎么改」
    save["_unresolved_reasons"] = attr_reasons

    # 价格类型：sku_price 按规格 / ladder_price 按数量 / fob_price 单一区间
    # 没有显式覆盖时不能把商品原本的定价模型静默改掉（sku_price→空阶梯价、
    # fob_price→阶梯价）。只有用户在 UI 显式选过定价方式（alibaba_price_type_explicit）
    # 或给了阶梯价档位/overrides 时，才以配置为准。
    _VALID_PTYPES = {"sku_price", "ladder_price", "fob_price"}
    prod_ptype = str(product.get("priceType") or "").strip()
    cfg_ptype = str(_cfg_get(cfg, "alibaba_price_type", "") or "").strip()
    ov_ptype = str(ov.get("price_type") or "").strip()
    cfg_tiers = ov.get("price_tiers") or _cfg_get(cfg, "alibaba_price_tiers", "") or ""
    _explicit = str(_cfg_get(cfg, "alibaba_price_type_explicit", "") or "").strip().lower() \
        in ("1", "true", "yes", "on")
    # 只有「没选过 + 配置仍是默认 ladder_price + 没填阶梯档位」才算隐式默认，
    # 这时跟随商品原定价模型；配置里明确写了 sku_price/fob_price 则尊重配置。
    _implicit_default = (not _explicit) and (not cfg_tiers) and cfg_ptype in ("", "ladder_price")
    if ov_ptype:
        ptype = ov_ptype
    elif _explicit or cfg_tiers:
        ptype = cfg_ptype or (prod_ptype if prod_ptype in _VALID_PTYPES else "ladder_price")
    elif prod_ptype in _VALID_PTYPES and _implicit_default:
        # 跟随商品原有定价模型（更新已有商品最安全）
        ptype = prod_ptype
    else:
        ptype = cfg_ptype or (prod_ptype if prod_ptype in _VALID_PTYPES else "ladder_price")
    tiers = price_tiers(cfg, ov)
    _force_ladder = bool(_cfg_get(cfg, "alibaba_force_ladder_price", False))
    if _force_ladder and ptype != "fob_price":
        ptype = "ladder_price"
        if not tiers:
            # 从原 SKU 价提取首档，转为单档阶梯价；没有 SKU 时用标准价/商品阶梯价。
            try:
                _ps = json.loads(product.get("productSku") or "{}")
                _skus = _ps.get("special_skus") or [] if isinstance(_ps, dict) else []
                _p = next((float(x.get("price")) for x in _skus
                           if isinstance(x, dict) and x.get("price")), 0)
            except Exception:
                _p = 0
            if _p <= 0:
                try:
                    _raw_price = product.get("bulkDiscountPrices") or product.get("price") or 0
                    if isinstance(_raw_price, str):
                        _raw_price = json.loads(_raw_price)[0].get("price", 0) \
                            if _raw_price.strip().startswith("[") else _raw_price
                    _p = float(_raw_price or 0)
                except Exception:
                    _p = 0
            tiers = [{"start_quantity": 1, "price": round(_p, 2)}] if _p > 0 else []
    if ptype == "fob_price":
        fob = {
            "fob_unit_type": str(_cfg_get(cfg, "alibaba_unit_type", "") or ""),
            "fob_currency": str(_cfg_get(cfg, "alibaba_fob_currency", "USD") or "USD"),
            "fob_min_price": _num(_cfg_get(cfg, "alibaba_fob_min_price", "")),
            "fob_max_price": _num(_cfg_get(cfg, "alibaba_fob_max_price", "")),
            "min_order_quantity": int(_num(_cfg_get(cfg, "alibaba_min_order_quantity", 1)) or 1),
            "min_order_unit_type": str(_cfg_get(cfg, "alibaba_unit_type", "") or ""),
            "sale_type": str(_cfg_get(cfg, "alibaba_sale_type", "normal") or "normal"),
        }
        if _cfg_get(cfg, "alibaba_preserve_trade", False):
            original = product.get("sourcingTrade") or "{}"
            fob = json.loads(original) if isinstance(original, str) else deepcopy(original)
        save["priceType"] = "fob_price"
        save["sourcingTrade"] = json.dumps(fob, ensure_ascii=False)
        # FOB 模式不写阶梯价字段（写入 [] 仍会被平台当成“同时存在阶梯价”）。
        save.pop("bulkDiscountPrices", None)
    elif ptype == "sku_price":
        # 按规格定价：保留商品原 productSku，不能同时出现阶梯价字段。
        save["priceType"] = "sku_price"
        save.pop("bulkDiscountPrices", None)
    elif tiers:
        save["priceType"] = "ladder_price"
        save["bulkDiscountPrices"] = json.dumps(tiers, ensure_ascii=False)
        save.pop("productSku", None)
        save.pop("productSkuObj", None)
    else:
        save["priceType"] = ptype

    # wholesaleTrade：MOQ / 包装 / 重量 / HS / 库存
    try:
        trade = json.loads(save.get("wholesaleTrade") or "{}")
    except Exception:
        trade = {}
    if not isinstance(trade, dict):
        trade = {}
    # 保留商品原有贸易信息时，也要补平台必填的 MOQ（商品常缺省为 0）。
    if cfg is not None and _cfg_get(cfg, "alibaba_preserve_trade", False):
        try:
            _moq = float(_cfg_get(cfg, "alibaba_min_order_quantity", 1) or 1)
        except Exception:
            _moq = 1
        try:
            _cur_moq = int(float(trade.get("min_order_quantity") or 0))
        except Exception:
            _cur_moq = 0
        if _cur_moq <= 0:
            trade["min_order_quantity"] = int(_moq) if _moq > 0 else 1
            trade["unit_type"] = str(trade.get("unit_type") or _cfg_get(cfg, "alibaba_unit_type", "Piece") or "Piece")
        # 保留商品原贸易信息时，缺失的 HS/发货期仍要补，否则评分和平台校验会扣分。
        if not trade.get("deliver_periods"):
            try:
                _tiers = json.loads(str(_cfg_get(cfg, "alibaba_default_deliver_periods", "") or "[]"))
            except Exception:
                _tiers = []
            if isinstance(_tiers, list) and _tiers:
                trade["deliver_periods"] = _tiers
        if not trade.get("tariffsHsCode"):
            _hs = str(_cfg_get(cfg, "alibaba_hs_code", "") or "").strip()
            if _hs:
                trade["tariffsHsCode"] = [{"toCountry": "US", "hsCode": _hs}]
        _inv = trade.get("inventory") or []
        if not any(str((r or {}).get("value") or "0").strip() not in ("", "0") for r in _inv if isinstance(r, dict)):
            try:
                _stock = int(float(_cfg_get(cfg, "alibaba_stock", 10) or 10))
            except Exception:
                _stock = 10
            trade["inventory"] = [{"warehouseCode": "CN_LOCAL_01", "value": max(_stock, 1)}]
        save["wholesaleTrade"] = json.dumps(trade, ensure_ascii=False)
    if cfg is not None and not _cfg_get(cfg, "alibaba_preserve_trade", False):
        def num(key, default=None):
            v = getattr(cfg, key, default)
            try:
                return float(v) if v not in (None, "") else default
            except Exception:
                return default

        moq = num("alibaba_min_order_quantity", None)
        trade["min_order_quantity"] = int(moq) if moq and moq > 0 else int(trade.get("min_order_quantity") or 1)
        trade["unit_type"] = str(getattr(cfg, "alibaba_unit_type", trade.get("unit_type") or "Piece") or "Piece")
        sale_type = str(getattr(cfg, "alibaba_sale_type", "") or "normal")
        trade["sale_type"] = sale_type
        batch = str(getattr(cfg, "alibaba_batch_number", "") or "").strip()
        trade["batch_number"] = int(batch) if sale_type == "batch" and batch.isdigit() else None
        freight_id = str(getattr(cfg, "alibaba_freight_template_id", "") or "").strip()
        # 已由默认/模板补齐的协商运费模板优先；全局模板只在缺失时补。
        if freight_id and not str(trade.get("shipping_line_template_id") or "").strip():
            trade["shipping_line_template_id"] = freight_id
        pkg = str(getattr(cfg, "alibaba_package_size", "") or "").strip()
        _old_pkg = str(trade.get("package_size") or "").strip()
        _pkg_missing = (not _old_pkg) or _old_pkg in ("0X0X0", "0x0x0") \
            or not re.match(r"^\d+(\.\d+)?[xX*]\d+(\.\d+)?[xX*]\d+(\.\d+)?$", _old_pkg)
        if pkg and _pkg_missing:
            trade["package_size"] = pkg
        elif pkg and _old_pkg != pkg:
            # 商品已有真实包装尺寸时不能被全局默认值静默覆盖（否则 0.64kg 的小件会被
            # 写成 500kg 的机械默认值，运费/清关全错）。差异只提示，保留商品原值。
            _overwrites.append(f"包装尺寸：保留商品原值 {_old_pkg}（未用全局默认 {pkg}）")
        weight = num("alibaba_weight", None)
        _old_weight = trade.get("weight")
        try:
            _old_weight_val = float(_old_weight or 0)
        except Exception:
            _old_weight_val = 0.0
        if weight and weight > 0:
            _new_weight = str(int(weight) if weight == int(weight) else round(weight, 2))
            if _old_weight_val > 0:
                if abs(_old_weight_val - weight) > 1e-6:
                    _overwrites.append(
                        f"包装重量：保留商品原值 {_old_weight}（未用全局默认 {_new_weight}）")
            else:
                trade["weight"] = _new_weight
        hs = str(getattr(cfg, "alibaba_hs_code", "") or "").strip()
        if hs and ptype != "fob_price":
            rows = trade.get("tariffsHsCode") or []
            if not rows:
                rows = [{"toCountry": "US", "hsCode": hs}]
                _overwrites.append(f"HS 编码：商品原值为空，已填全局默认 {hs}")
            for r in rows:
                cur_hs = str(r.get("hsCode") or "").strip()
                if not cur_hs:
                    _note_overwrite("HS 编码", "（空）", hs)
                    r["hsCode"] = hs
                elif cur_hs != hs:
                    _overwrites.append(
                        f"HS 编码：保留商品原值 {cur_hs}（未用全局默认 {hs}）")
            trade["tariffsHsCode"] = rows
        stock = num("alibaba_stock", None)
        if stock and stock > 0:
            trade["inventory"] = [{"warehouseCode": "CN_LOCAL_01", "value": int(stock)}]
        # 发货期：平台发布必填（实测格式 [{"quantity":3,"process_period":15}]），
        # 缺失时发布会被拒「发货期信息未填写」。
        try:
            _qty = int(float(str(getattr(cfg, "alibaba_deliver_quantity", "") or "1")))
        except Exception:
            _qty = 1
        _raw_days = str(getattr(cfg, "alibaba_process_period", "") or "").strip()
        try:
            _days = int(float(_raw_days)) if _raw_days else int(float(
                getattr(cfg, "alibaba_lead_time", 30) or 30))
        except Exception:
            _days = 30
        # 已有发货期时优先保留（不因默认配置覆盖商品真实交期）
        _existing = trade.get("deliver_periods") or []
        _keep_existing = any(isinstance(x, dict) and int(x.get("process_period") or 0) > 0
                             for x in _existing) and not _raw_days
        if _keep_existing:
            pass
        elif _qty > 0 and _days > 0:
            # 显式配置了交期（process_period 或非默认 lead_time）就尊重配置；
            # 否则使用三档默认发货期。
            try:
                _default_lead = int(float(getattr(cfg, "alibaba_lead_time", 30) or 30))
            except Exception:
                _default_lead = 30
            try:
                _tiers = json.loads(str(getattr(cfg, "alibaba_default_deliver_periods", "") or "[]"))
            except Exception:
                _tiers = []
            if not _raw_days and _days == _default_lead and isinstance(_tiers, list) and _tiers:
                trade["deliver_periods"] = _tiers
            else:
                trade["deliver_periods"] = [{"quantity": _qty,
                                             "process_period": _days}]
    if ptype == "fob_price":
        # FOB 下不允许带 HS 关税行（前端逻辑：tariffsHsCodeConfig && !fob && !overseaSpot）
        trade.pop("tariffsHsCode", None)
    save["wholesaleTrade"] = json.dumps(trade, ensure_ascii=False)

    if cfg is not None:
        # 物流属性：平台校验「重量/长宽高/物流属性要么全填要么全清」，
        # 只填了重量尺寸而没有物流属性时，发布会被拒。
        _logistics = str(getattr(cfg, "alibaba_logistics_property", "") or "").strip()
        if not _logistics:
            _logistics = str(product.get("logisticsProperty") or "").strip()
        try:
            _wt = json.loads(save.get("wholesaleTrade") or "{}")
        except Exception:
            _wt = {}
        # 平台规则：重量/长宽高/物流属性必须同时填写或同时清空。
        if _logistics and _wt.get("package_size") and _wt.get("weight"):
            save["logisticsProperty"] = _logistics

    group = str(getattr(cfg, "alibaba_group_id", "") or "").strip() if cfg is not None else ""
    if group:
        save["groupId"] = group
    if getattr(cfg, "alibaba_company_desc", "") if cfg is not None else "":
        # 公司介绍不走 product 保存接口，这里留痕给发布层（companyDesc 需要 schema 字段）
        save.setdefault("_company", {})["companyDesc"] = str(getattr(cfg, "alibaba_company_desc", "") or "")
    # 当前商品的显式编辑最后应用；与批量缺省资料分开，保留未编辑的嵌套字段。
    editor_fields = ov.get("editor_fields") or {}
    for key in ("sourceUrl", "groupId", "motorIds", "publishType", "shipmentPlace",
                "warehouseType", "overseaCountryRegion", "salePkgDesc", "logisticsProperty",
                "marketSample", "marketSamplingQuantity", "marketSamplingPrice"):
        if key in editor_fields:
            save[key] = str(editor_fields[key] if editor_fields[key] is not None else "")
    if "wholesale_trade" in ov:
        patch = ov["wholesale_trade"]
        if not isinstance(patch, dict):
            raise ValueError("当前商品交易信息必须是对象")
        trade = json.loads(save.get("wholesaleTrade") or "{}")
        for key in ("package_size", "weight", "inventory", "deliver_periods"):
            if key in patch:
                trade[key] = patch[key]
        if "inventory" in patch:
            for row in trade["inventory"]:
                value = _num(row.get("value"))
                if value is None or value < 0 or int(value) != value:
                    raise ValueError("商品仓库库存必须是大于等于 0 的整数")
                row["value"] = int(value)
        if "deliver_periods" in patch:
            if len(trade["deliver_periods"]) > 3:
                raise ValueError("商品发货期最多 3 档")
            for row in trade["deliver_periods"]:
                for key in ("quantity", "process_period"):
                    value = _num(row.get(key))
                    if value is None or value < 1 or int(value) != value:
                        raise ValueError("发货期的数量及天数必须是正整数")
                    row[key] = int(value)
        save["wholesaleTrade"] = json.dumps(trade, ensure_ascii=False)
    if "sourcing_trade" in ov:
        trade = _json_container(save.get("sourcingTrade")) or {}
        for key in ("fob_min_price", "fob_max_price", "fob_currency"):
            if key in ov["sourcing_trade"]:
                trade[key] = ov["sourcing_trade"][key]
        save["sourcingTrade"] = json.dumps(trade, ensure_ascii=False)
    if "product_sku" in ov:
        if not isinstance(ov["product_sku"], dict):
            raise ValueError("当前商品 SKU 数据必须是对象")
        save["productSku"] = json.dumps(ov["product_sku"], ensure_ascii=False)
    save["_overwrites"] = _overwrites
    return save




def _num(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


GENERIC_VALUES = ("other", "others", "not applicable", "none", "no")

# 值别名：采集/人工/中文写法 → 平台枚举规范名（归一化 key）。
# 只放「语义等价、可确定」的别名；拿不准的一律不猜（宁可不填也不写错规格）。
VALUE_ALIASES = {
    "mainlandchina": ("china",),
    "chinamainland": ("china",),
    "prc": ("china",),
    "中国大陆": ("china",),
    "中华人民共和国": ("china",),
    "usa": ("unitedstates",),
    "us": ("unitedstates",),
    "america": ("unitedstates",),
    "uk": ("unitedkingdom",),
    "greatbritain": ("unitedkingdom",),
    "england": ("unitedkingdom",),
    "uae": ("unitedarabemirates",),
    "korea": ("southkorea", "northkorea"),
    # 材质同义：fiberglass = glass fiber（玻璃钢），不是臆造规格
    "fiberglass": ("glassfiber",),
    "fibreglass": ("glassfiber",),
    "glassfibre": ("glassfiber",),
    "glassfiberreinforcedplastic": ("frp", "glassfiber"),
    # 「按客户要求/定制」类值在平台枚举里语义等价于 Other（并非真实规格）
    "customized": ("other",),
    "customised": ("other",),
    "customizedcolor": ("other",),
    "customizedsize": ("other",),
    "customizedlogo": ("other",),
    "customcolor": ("other",),
    "custom": ("other",),
    "asrequest": ("other",),
    "accordingtorequest": ("other",),
    "accordingtoorder": ("other",),
    "customizable": ("other",),
    "anycolor": ("other",),
}

# 属性名别名：商品/采集侧常见写法 → 类目定义名（归一化 key）。
ATTRIBUTE_ALIASES = {
    "origin": ("placeoforigin",),
    "产地": ("placeoforigin", "原产地"),
    "model": ("modelnumber",),
    "modelno": ("modelnumber",),
    "型号": ("modelnumber",),
    "power": ("powerw",),
    "powerw": ("power",),
    "功率": ("powerw",),
    "weight": ("weightkg",),
    "weightkg": ("weight",),
    "color": ("colors",),
    "colors": ("color",),
}


def _norm(s) -> str:
    """归一化比较键：全角转半角、小写、去空白/标点/符号，保留中英文数字。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)


def _is_missing_value_id(vid) -> bool:
    """value_id 是否缺失：空/None/负数（-1/-2… 是采集侧自定义占位，不是平台枚举 id）。"""
    s = str(vid if vid is not None else "").strip()
    if s in ("", "None", "null", "nan"):
        return True
    if s.lstrip("-").isdigit() and int(s) < 0:
        return True
    return False


def _value_entries(attr_def: dict | None) -> list[dict]:
    """平台候选值标准化：[{id, name, names}]。

    店小秘 `values` 是 JSON 字符串（[{"id","name_en","has_child"}]），少部分类目
    只给 `valueList`；中文名在 `transValues[].trans_value`（按 id 关联）。
    """
    d = attr_def or {}
    raw_values = d.get("values")
    if isinstance(raw_values, str) and raw_values.strip().lower() in ("", "null", "none", "nan"):
        rows = []
    else:
        rows = _as_list(raw_values)
    if not rows:
        vl = d.get("valueList")
        rows = vl if isinstance(vl, list) else _as_list(vl)
    trans: dict[str, list[str]] = {}
    for t in _as_list(d.get("transValues")):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or "").strip()
        tv = str(t.get("trans_value") or t.get("transValue") or "").strip()
        if tid and tv:
            trans.setdefault(tid, []).append(tv)
    out: list[dict] = []
    for v in rows if isinstance(rows, list) else []:
        if not isinstance(v, dict):
            s = str(v or "").strip()
            if s:
                out.append({"id": None, "name": s, "names": [s]})
            continue
        vid = str(v.get("id") if v.get("id") is not None else "").strip()
        names: list[str] = []
        for key in ("name_en", "name", "name_zh", "nameZh", "value_name", "trans_value"):
            s = str(v.get(key) or "").strip()
            if s and s not in names:
                names.append(s)
        for s in trans.get(vid, []):
            if s not in names:
                names.append(s)
        if not names:
            continue
        primary = str(v.get("name_en") or v.get("name") or names[0]).strip()
        out.append({"id": vid or None, "name": primary or names[0], "names": names})
    return out


def _attr_values(attr_def: dict | None) -> list[str]:
    """取类目属性的候选值文本（兼容旧调用）。"""
    return [e["name"] for e in _value_entries(attr_def) if e.get("name")]


def _is_free_text(attr_def: dict | None) -> bool:
    """没有枚举候选、且输入类型是文本/数字（或旧数据未给 inputType）→ 自由填写。"""
    d = attr_def or {}
    if _value_entries(d):
        return False
    it = str(d.get("inputType") or "").strip().lower()
    return it in ("input", "text", "textarea", "number", "")


def _candidate_sample(attr_def: dict | None, limit: int = 6) -> str:
    names = [e["name"] for e in _value_entries(attr_def) if e.get("name")]
    if not names:
        return ""
    return " / ".join(names[:limit]) + (" …" if len(names) > limit else "")


def _split_attr_values(value) -> list[str]:
    """把多选属性里用逗号/分号/加号拼接的多个值拆开。"""
    parts = re.split(r"[,，;；+]+", str(value or ""))
    return [p.strip() for p in parts if p.strip()]


def resolve_attr_value(attr_def: dict | None, value) -> dict:
    """把属性值解析成平台候选值（value_id/value_name）。

    返回 ``{"value_id", "value_name", "matched", "reason"}``：

    - matched=True 且 value_id 非空：可直接写入保存包；
    - matched=True 但 value_id 为空：平台候选值缺 id，**不能写空 value_id**（应阻断）；
    - matched=False：值不在平台枚举里（调用方决定阻断/仅提示）。

    匹配顺序：大小写完全一致 → 归一化一致（全角/符号）→ 值别名 → 唯一包含。
    中文名走 trans_value；歧义（如 "60 V" / "> 60V"）一律不猜。
    """
    v = str(value or "").strip()
    if not v:
        return {"value_id": None, "value_name": "", "matched": False, "reason": "值为空"}
    entries = _value_entries(attr_def)
    if not entries:
        return {"value_id": None, "value_name": v, "matched": False,
                "reason": "该属性无平台候选值（自由填写）"}

    low = v.lower()
    hits = [e for e in entries if any(n.lower() == low for n in e["names"])]
    if not hits:
        nv = _norm(v)
        if nv:
            hits = [e for e in entries
                    if _norm(e["name"]) == nv or any(_norm(n) == nv for n in e["names"])]
            if not hits:
                for alias in VALUE_ALIASES.get(nv, ()):
                    hits = [e for e in entries
                            if any(_norm(n) == alias for n in e["names"])]
                    if hits:
                        break
            if not hits:
                fuzzy = [e for e in entries
                         if any(nv and _norm(n) and (nv in _norm(n) or _norm(n) in nv)
                                for n in e["names"])]
                if len(fuzzy) == 1:
                    hits = fuzzy
                elif len(fuzzy) > 1:
                    return {"value_id": None, "value_name": v, "matched": False,
                            "reason": "候选值存在歧义：" + " / ".join(
                                e["name"] for e in fuzzy[:4])}
    if not hits:
        return {"value_id": None, "value_name": v, "matched": False,
                "reason": "不在平台候选值中"}
    e = hits[0]
    return {"value_id": e.get("id"), "value_name": e.get("name") or v,
            "matched": True, "reason": ""}


def match_attribute_value(attr_def: dict | None, value: str) -> str | None:
    """把 AI/人工给的属性值匹配到类目候选值，返回候选的规范拼写。

    匹配顺序：大小写完全一致 → 去符号归一化唯一匹配 → 唯一包含匹配；
    任何歧义/匹配不到都返回 None（宁可不填，也不写错规格）。
    """
    if not _value_entries(attr_def):
        return None
    res = resolve_attr_value(attr_def, value)
    return res.get("value_name") if res.get("matched") else None


def _def_label(d: dict | None) -> str:
    return str((d or {}).get("nameEn") or (d or {}).get("nameZh")
               or (d or {}).get("attribute_name") or "").strip()


def _def_attr_name_id(d: dict | None) -> str:
    return str((d or {}).get("attrNameId") or (d or {}).get("attribute_id") or "").strip()


def _build_def_index(defs) -> tuple[dict, dict]:
    """类目定义索引：归一化名称 → def，attrNameId → def。"""
    by_name: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for d in defs or []:
        if not isinstance(d, dict):
            continue
        for n in (d.get("nameEn"), d.get("nameZh"), d.get("attribute_name")):
            k = _norm(n)
            if k:
                by_name.setdefault(k, d)
        aid = _def_attr_name_id(d)
        if aid and aid not in ("-1", "None", "null"):
            by_id.setdefault(aid, d)
    return by_name, by_id


def _match_def_for_attr(attr: dict, by_name: dict, by_id: dict) -> dict | None:
    """把一条保存属性挂到类目定义：attribute_id → 英文/中文名 → 别名。"""
    aid = str((attr or {}).get("attribute_id") or "").strip()
    if aid and aid not in ("-1", "", "None", "null") and aid in by_id:
        return by_id[aid]
    k = _norm((attr or {}).get("attribute_name"))
    if not k:
        return None
    if k in by_name:
        return by_name[k]
    for target in ATTRIBUTE_ALIASES.get(k, ()):
        if target in by_name:
            return by_name[target]
    return None


def _parse_presets(cfg=None, overrides: dict | None = None) -> dict:
    """属性预设 → {归一化属性名: 值}；支持 str(JSON)/dict。"""
    raw = (overrides or {}).get("attributes") or _cfg_get(cfg, "alibaba_attr_presets", "") or ""
    if isinstance(raw, str):
        s = raw.strip()
        if not s.startswith("{"):
            return {}
        try:
            raw = json.loads(s)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {_norm(k): v for k, v in raw.items() if _norm(k)}


def _preset_value(val):
    """预设值归一：字符串或 {value_name/value_id} → (name, value_id|None)。"""
    if isinstance(val, dict):
        v_id = val.get("value_id")
        if v_id in (None, ""):
            v_id = val.get("id")
        return str(val.get("value_name") or val.get("name") or "").strip(), v_id
    return str(val if val is not None else "").strip(), None


def autofill_required_attributes(product: dict, attr_defs: list | None, cfg=None,
                                 overrides: dict | None = None, *,
                                 reasons: list | None = None):
    """按类目 required 标记补齐必填属性，并补全平台 value_id。

    真实采集箱回归后强化的规则（2026-11-22）：
    - 已有属性按 attribute_id / 英文名 / 中文名 / 别名 挂到类目定义，不再只比英文名；
      采集草稿大量出现中文名（如「重量（千克）」）或 attribute_id=-1，旧逻辑会误判缺失；
    - value_id 从平台候选值按 name/name_en/中文名(trans_value)/值别名/大小写 解析；
      枚举属性解析不到平台 id 时**不写空/负数 value_id**（会触发平台校验），记 unresolved；
    - 无真实依据时只回退 Other/Not applicable 等通用值，绝不取候选第一项；
    - 必填但无依据时记 unresolved，并通过 reasons 给出可读中文原因（不静默丢弃）。

    返回 (attrs, filled, unresolved)；reasons 传 list 时追加可读中文原因。
    """
    def _reason(msg: str) -> None:
        if reasons is not None:
            reasons.append(msg)

    attrs = [deepcopy(a) for a in _as_list((product or {}).get("attributes"))
             if isinstance(a, dict)]
    defs = [d for d in (attr_defs or []) if isinstance(d, dict)]
    by_name, by_id = _build_def_index(defs)
    presets = _parse_presets(cfg, overrides)
    filled: list[str] = []
    unresolved: list[str] = []
    covered: set[int] = set()
    preset_handled: set[int] = set()

    def _set_attr(a: dict, d: dict, v_name: str, v_id) -> None:
        aid = _def_attr_name_id(d)
        if aid and aid not in ("-1", "None"):
            a["attribute_id"] = aid
        _label = _def_label(d)
        if _label:
            a["attribute_name"] = _label   # 统一成平台规范属性名（英文），避免中英文同义行
        a["value_name"] = str(v_name)
        if v_id not in (None, ""):
            a["value_id"] = str(v_id)
        elif str(a.get("value_id") or "") in ("", "None"):
            a["value_id"] = "-1"

    def _append_attr(d: dict, v_name: str, v_id) -> dict:
        a = {"attribute_id": _def_attr_name_id(d) or "-1",
             "attribute_name": _def_label(d),
             "value_id": str(v_id) if v_id not in (None, "") else "-1",
             "value_name": str(v_name)}
        attrs.append(a)
        covered.add(id(d))
        return a

    # A. 已有属性 → 对齐类目定义 + 补 value_id（含商品原值不在枚举的诚实标记）
    normalized: list[dict] = []
    for a in attrs:
        d = _match_def_for_attr(a, by_name, by_id)
        if d is None:
            normalized.append(a)
            continue
        covered.add(id(d))
        name = _def_label(d)
        req = int(d.get("required") or 0) == 1
        aid = _def_attr_name_id(d)
        if aid and aid not in ("-1", "None") and str(a.get("attribute_id") or "") in (
                "", "-1", "None"):
            a["attribute_id"] = aid
        entries = _value_entries(d)
        v_name = str(a.get("value_name") or "").strip()
        if not entries:
            if not _is_free_text(d):
                if req:
                    unresolved.append(f"{name}（类目属性没有候选值，无法确认取值）")
                    _reason(f"{name}：类目属性接口没有返回候选值，无法确认取值，请刷新类目后重试")
                normalized.append(a)
                continue
            if str(a.get("value_id") or "") in ("", "None"):
                a["value_id"] = "-1"
            if not v_name and req:
                unresolved.append(name)
                _reason(f"{name}：必填属性（自由填写）当前没有值，请补充属性预设")
            normalized.append(a)
            continue
        if not v_name:
            if req:
                unresolved.append(name)
                _reason(f"{name}：必填属性值为空；候选值：{_candidate_sample(d)}")
            normalized.append(a)
            continue
        # 多选属性：采集数据常把多个值塞在一个字段（"Children, Adult, All"）。
        # 平台多选是「同一 attribute_id 多行、每行一个 value_id」→ 拆行并逐个解析。
        multi = str(d.get("inputType") or "").lower() in ("multi_select", "multiselect") \
            or str(d.get("showType") or "").lower() in ("check_box",)
        if multi:
            parts = _split_attr_values(v_name)
            if len(parts) > 1:
                rows: list[tuple[str, str]] = []
                bad: list[str] = []
                for part in parts:
                    r = resolve_attr_value(d, part)
                    if r.get("matched") and r.get("value_id") not in (None, ""):
                        pair = (str(r["value_name"]), str(r["value_id"]))
                        if pair not in rows:
                            rows.append(pair)
                    else:
                        bad.append(part)
                if rows:
                    for rn, rid in rows:
                        na = deepcopy(a)
                        na["value_id"] = rid
                        na["value_name"] = rn
                        normalized.append(na)
                    if req:
                        filled.append(f"{name}={' / '.join(rn for rn, _ in rows)}(多选拆分)")
                else:
                    normalized.append(a)
                if bad and req:
                    unresolved.append(f"{name}（商品原值「{'、'.join(bad)}」不在平台候选值中）")
                    _reason(f"{name}：商品原值中「{'、'.join(bad)}」不在平台候选值中；"
                            f"候选值：{_candidate_sample(d)}；请选择平台候选值")
                continue
        res = resolve_attr_value(d, v_name)
        if not res.get("matched"):
            if req:
                why = res.get("reason") or "不在平台候选值中"
                # 必填属性的原值不合法时，自动换成平台合法候选（优先通用值，
                # 否则取第一个候选），避免整个商品因源数据脏值无法保存/发布；
                # 明细里标记「自动候选值，请复核」。
                _auto = next((e for e in entries
                              if str(e.get("name") or "").strip().lower()
                              in GENERIC_VALUES and e.get("id") not in (None, "")), None)
                if _auto is None:
                    _auto = next((e for e in entries if e.get("id") not in (None, "")), None)
                if _auto is not None:
                    _set_attr(a, d, str(_auto.get("name") or ""), str(_auto.get("id")))
                    filled.append(f"{name}={_auto.get('name')}(自动候选值，请复核；原值 "
                                  f"{v_name} {why})")
                    normalized.append(a)
                    continue
                unresolved.append(f"{name}（商品原值「{v_name}」{why}）")
                _reason(f"{name}：商品原值「{v_name}」{why}；候选值："
                        f"{_candidate_sample(d)}；请在属性预设中改填平台候选值")
            normalized.append(a)
            continue
        if res.get("value_id") in (None, ""):
            if req:
                unresolved.append(f"{name}（平台候选值「{res.get('value_name')}」缺少 value_id）")
                _reason(f"{name}：平台候选值「{res.get('value_name')}」没有 value_id"
                        "（平台枚举异常），不能写空 id")
            normalized.append(a)
            continue
        cur_vid = str(a.get("value_id") or "").strip()
        resolved_id = str(res["value_id"])
        resolved_name = str(res.get("value_name") or v_name)
        if _is_missing_value_id(cur_vid):
            if resolved_name and _norm(resolved_name) != _norm(v_name):
                a["value_name"] = resolved_name
            a["value_id"] = resolved_id
            if req and str(cur_vid) != resolved_id:
                filled.append(f"{name}={resolved_name}(value_id 补全)")
        elif resolved_name and _norm(resolved_name) == _norm(v_name):
            # 只是大小写/别名拼写不同 → 统一成平台规范拼写
            if str(a.get("value_name")) != resolved_name:
                a["value_name"] = resolved_name
        normalized.append(a)
    attrs = normalized

    # B. 预设：覆盖已有属性值 / 补齐缺失项（显式 value_id 也按平台候选校验）
    for d in defs:
        pkey = None
        for cand in (d.get("nameEn"), d.get("nameZh"), d.get("attribute_name")):
            if cand and _norm(cand) in presets:
                pkey = _norm(cand)
                break
        if pkey is None:
            continue
        preset_handled.add(id(d))
        name = _def_label(d)
        req = int(d.get("required") or 0) == 1
        v_name, v_id = _preset_value(presets[pkey])
        if not v_name and v_id in (None, ""):
            if req:
                unresolved.append(name)
                _reason(f"{name}：属性预设是空值，必填属性必须填写")
            continue
        entries = _value_entries(d)
        _matches = [x for x in attrs if _match_def_for_attr(x, by_name, by_id) is d]
        a = _matches[0] if _matches else None
        existed = a is not None
        if not entries:
            if not _is_free_text(d):
                if req:
                    unresolved.append(f"{name}（类目属性没有候选值，无法校验预设值「{v_name}」）")
                    _reason(f"{name}：类目属性接口没有返回候选值，无法校验预设值「{v_name}」，"
                            "请刷新类目后重试")
                continue
            if not v_name:
                if req:
                    unresolved.append(name)
                    _reason(f"{name}：必填属性（自由填写）预设值没有文本")
                continue
            if a is None:
                a = _append_attr(d, v_name, v_id if v_id not in (None, "") else "-1")
            else:
                _set_attr(a, d, v_name, v_id if v_id not in (None, "")
                          else (a.get("value_id") or "-1"))
            filled.append(f"{name}={v_name}(覆盖)" if existed else f"{name}={v_name}")
            continue
        res = resolve_attr_value(d, v_name)
        if not res.get("matched") and v_id not in (None, ""):
            hit = next((e for e in entries if str(e.get("id")) == str(v_id)), None)
            if hit:
                res = {"value_id": hit.get("id"), "value_name": hit.get("name"),
                       "matched": True, "reason": ""}
        if not res.get("matched"):
            why = res.get("reason") or "不在平台候选值中"
            if req:
                unresolved.append(f"{name}（预设值「{v_name}」{why}）")
                _reason(f"{name}：预设值「{v_name}」{why}；候选值："
                        f"{_candidate_sample(d)}；请改填平台候选值")
            continue
        if res.get("value_id") in (None, ""):
            if req:
                unresolved.append(f"{name}（平台候选值「{res.get('value_name')}」缺少 value_id）")
                _reason(f"{name}：平台候选值「{res.get('value_name')}」没有 value_id，不能写空 id")
            continue
        if a is None:
            _append_attr(d, res["value_name"], res["value_id"])
        else:
            # 同一属性可能同时存在中英文两行（模板/采集叠加），必须全部覆盖，
            # 否则残留的旧值（如 Other）会拉低评分并可能被平台判为占位属性。
            for _m in _matches:
                _set_attr(_m, d, res["value_name"], res["value_id"])
        filled.append(f"{name}={res['value_name']}(覆盖)" if existed
                      else f"{name}={res['value_name']}")

    # B2. 占位值（Other）→ 从商品原文/属性提取真实值并按平台候选值匹配。
    # 例：原文 "220V/380V" 要能匹配候选值 "220 V"/"380 V"，而不是只比空格。
    _PLACEHOLDER = {"other", "others", "n/a", "na", "none", "unknown"}
    _src_raw = " ".join([str(product.get(k) or "") for k in
                         ("subject", "keywords", "textDesc", "description")])
    _src_raw += " " + " ".join(str(a.get("value_name") or a.get("value") or "")
                               for a in attrs if isinstance(a, dict))
    _src_norm = _norm(_src_raw)
    for d in defs:
        name = _def_label(d)
        if not name or int(d.get("required") or 0) != 1:
            continue
        rows = [a for a in attrs if _match_def_for_attr(a, by_name, by_id) is d]
        if not rows:
            continue
        if not any(str(a.get("value_name") or "").strip().casefold() in _PLACEHOLDER for a in rows):
            continue
        entries = _value_entries(d)
        hits = []
        for e in entries:
            ev = str(e.get("name") or "").strip()
            if not ev or ev.casefold() in _PLACEHOLDER:
                continue
            if _norm(ev) and _norm(ev) in _src_norm:
                # 按候选值在原文中的位置排序，220V/380V 优先取 220V。
                pos = _src_norm.find(_norm(ev))
                hits.append((pos, e))
        if not hits:
            continue
        hits.sort(key=lambda x: x[0])
        # 同一位置长度不同的候选，取最靠前/最具体的那个。
        _, best = hits[0]
        for row in rows:
            if str(row.get("value_name") or "").strip().casefold() in _PLACEHOLDER:
                _set_attr(row, d, str(best.get("name") or ""), str(best.get("id") or "-1"))
                filled.append(f"{name}={best.get('name')}(按原文补全)")

    # C. 仍缺失的必填项：通用值 → unresolved（给可读原因，绝不取候选第一项）
    for d in defs:
        if int(d.get("required") or 0) != 1:
            continue
        if id(d) in covered or id(d) in preset_handled:
            continue
        name = _def_label(d)
        entries = _value_entries(d)
        generic = None
        for e in entries:
            if str(e.get("name") or "").strip().lower() in GENERIC_VALUES \
                    and e.get("id") not in (None, ""):
                generic = e
                break
        if generic is not None:
            _append_attr(d, generic["name"], generic["id"])
            filled.append(f"{name}={generic['name']}")
            continue
        unresolved.append(name)
        if entries:
            _reason(f"{name}：必填属性，商品原值/预设/通用值都没有可依据的取值；"
                    f"候选值：{_candidate_sample(d)}；请补充属性预设或人工选择")
        else:
            _reason(f"{name}：必填属性（自由填写），商品原值与预设都没有值，请补充属性预设")
    # 必填属性去重：中英文同义行只保留一行，避免重复/旧值污染评分与平台校验。
    # 注意：多选属性是「同一 attribute_id 多行、每行一个 value_id」，
    # 不能按 def 去重（2026-11-22 实测：多选拆行后只剩第一行、缺失项被误删）。
    def _dedup_def(_a: dict) -> dict | None:
        # 先按属性名精确匹配，再退回 id/别名匹配；避免不同 def 的 attrNameId
        # 相同（脏数据/测试夹具）时把正确属性误判成已出现。
        _nm = _norm(str(_a.get("attribute_name") or ""))
        if _nm and _nm in by_name:
            return by_name[_nm]
        return _match_def_for_attr(_a, by_name, by_id)

    _seen_req: set[tuple] = set()
    _deduped: list[dict] = []
    for _a in attrs:
        _d = _dedup_def(_a)
        if _d is not None and int(_d.get("required") or 0) == 1:
            _multi = (str(_d.get("inputType") or "").lower()
                      in ("multi_select", "multiselect")
                      or str(_d.get("showType") or "").lower() in ("check_box",))
            _key = (id(_d), str(_a.get("value_id") or "")) if _multi else (id(_d), "")
            if _key in _seen_req:
                continue
            _seen_req.add(_key)
        _deduped.append(_a)
    attrs = _deduped
    return attrs, filled, unresolved


_INVALID_CATEGORY_VALUES = {"none", "null", "nan", "undefined"}


import html as _html_mod

MAX_TEXT_DESC = 2000   # 平台实测：textDesc 不能超过 1000 汉字或 2000 字符


def schedule_time_ms(value) -> int | None:
    """把本地时间字符串解析成店小秘定时发布用的毫秒时间戳。

    实测 op=5 的 alibabaSave 字段：dxmScheduleTime = 13 位毫秒（本地时间）。
    支持 "YYYY-MM-DD HH:MM[:SS]" / "YYYY-MM-DD" / ISO "YYYY-MM-DDTHH:MM"。
    """
    import datetime as _dt
    s = str(value or "").strip()
    if not s:
        return None
    if s.isdigit():
        v = int(s)
        return v * 1000 if v < 10**12 else v
    s2 = s.replace("T", " ").replace("/", "-")
    if s2.endswith("Z"):
        s2 = s2[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = _dt.datetime.strptime(s2, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def text_desc_plain(raw: str, limit: int = MAX_TEXT_DESC) -> str:
    """把详情转成平台要求的纯文本（textDesc 不接受 HTML）。

    平台实测错误：
    - textDesc:isv.parameter-parse-error:textDesc:不能超过1000个汉字或2000个字符
    - textDesc:isv.parameter-parse-error:textDesc:不能输入HTML脚本
    在线商品的 textDesc 实测均为纯文本、无标签；因此这里统一：
    去 script/style/所有标签 → 块级标签转换行 → 反转义实体 → 压缩空白 → 截断 2000。
    """
    s = str(raw or "")
    s = re.sub(r"<(script|style|iframe)[^>]*>[\s\S]*?</\1>", " ", s, flags=re.I)
    s = re.sub(r"<!--[\s\S]*?-->", " ", s)
    # 块级/换行标签 → 换行；单元格/列表项 → 分隔
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|h[1-6]|ul|ol|table|div|section|article)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(li|tr)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(td|th)\s*>", " | ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_mod.unescape(s)
    s = s.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |\t") for ln in s.splitlines()]
    lines = [ln for ln in lines if ln and ln not in ("|",)]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(out) > limit:
        out = out[:limit]
        # 别把最后一行截成半个词
        if "\n" in out[-60:]:
            out = out[:out.rfind("\n")].rstrip()
    return out


# 兼容旧名字（早期实现叫 sanitize_detail_html）
sanitize_detail_html = text_desc_plain


# 平台禁止站外联系方式（实测在线商品详情全部没有电话/邮箱/WhatsApp）。
CONTACT_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CONTACT_URL_RE = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.I)
CONTACT_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s\-()]{7,}\d(?![\w.])")
CONTACT_CHANNEL_RE = re.compile(
    r"(?i)\b(?:whats\s*app|we\s*chat|weixin|skype|telegram|viber|qq"
    r"|e-?mail|phone(?:\s*(?:number|no\.?))?|mobile(?:\s*(?:number|no\.?))?|tel)\b\s*[:：]?")
_DATE_LIKE_RE = re.compile(r"\d{4}[-/]\d{1,4}(?:[-/]\d{1,2})?")


def _looks_like_contact_number(frag: str) -> bool:
    """区分电话/手机号与尺寸、年份区间等规格数字（宁可少删，不误删规格）。"""
    s = str(frag or "").strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) < 9:
        return False
    if _DATE_LIKE_RE.fullmatch(s):
        return False
    if "+" not in s and re.fullmatch(r"[\d\s]+", s):
        groups = re.findall(r"\d+", s)
        # 「1200 800 900」这类尺寸：无 +、纯数字/空格、≥3 组且每组 ≤4 位
        if len(groups) >= 3 and all(len(g) <= 4 for g in groups):
            return False
    return True


def strip_contact_info(text: str) -> tuple[str, list[str]]:
    """移除详情里的站外联系方式（邮箱/网址/电话/WhatsApp/WeChat…）。

    返回 ``(清理后的文本, 被移除片段列表)``。用途：
    - 平台 textDesc 不允许站外联系方式，带联系方式会降低评分并可能审核失败；
    - 只做「删除」，不生成任何新内容（不违反「不编造」）；
    - 移除片段回传给调用方写进 warning/证据，避免静默改内容。
    """
    s = str(text or "")
    removed: list[str] = []

    def _keep(frag: str) -> None:
        frag = str(frag or "").strip()
        if frag and frag not in removed:
            removed.append(frag)

    def _drop(m) -> str:
        _keep(m.group(0))
        return " "

    def _drop_phone(m) -> str:
        frag = m.group(0)
        if not _looks_like_contact_number(frag):
            return frag
        _keep(frag)
        return " "

    s = CONTACT_EMAIL_RE.sub(_drop, s)
    s = CONTACT_URL_RE.sub(_drop, s)
    s = CONTACT_PHONE_RE.sub(_drop_phone, s)
    s = CONTACT_CHANNEL_RE.sub(_drop, s)

    lines: list[str] = []
    for ln in s.splitlines():
        ln = re.sub(r"[ \t]{2,}", " ", ln).strip()
        # 只清理「删除联系方式后」留下的分隔符残渣，不动正文原有标点（如标题冒号）。
        ln = re.sub(r"(?:\s*[/|]\s*){2,}", " ", ln)
        ln = re.sub(r"\s*([,;:•·])\s*(?=[,;:•·])", "", ln)
        ln = re.sub(r"\b(or|and)\b(?:\s+\1\b)+", r"\1", ln, flags=re.I)
        ln = ln.strip(" |").strip()
        # 只剩符号/emoji 的行（原联系方式行被删空）直接丢掉
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", ln):
            continue
        lines.append(ln)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out, removed


def _json_container(v):
    """把 save 里的 JSON 字符串/对象统一成 dict/list；解析失败返回 None。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def sku_summary(product_sku) -> tuple[bool, int, bool]:
    """productSku 体检：返回 (是否有数据, 有效 SKU 数, 是否有 SKU 维度属性)。

    真实 edit.json 的 productSku 是 JSON 字符串：
    {"attributes": [...], "special_skus": [{"price":..., "sku_code":..., "attributes":[...]}]}
    """
    obj = _json_container(product_sku)
    if obj is None:
        return False, 0, False
    if isinstance(obj, list):
        rows, dims = obj, False
    elif isinstance(obj, dict):
        rows = obj.get("special_skus") or obj.get("skus") or obj.get("sku_list") or []
        dims = bool(obj.get("attributes"))
    else:
        return False, 0, False
    if not isinstance(rows, list):
        rows = []
    valid = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("exclude"):
            continue
        try:
            price = float(r.get("price") or r.get("sku_price") or 0)
        except Exception:
            price = 0
        has_code = bool(str(r.get("sku_code") or r.get("code") or "").strip())
        attrs = r.get("attributes") or []
        has_attr = isinstance(attrs, list) and any(
            isinstance(a, dict) and (a.get("attribute_name") or a.get("value_name")) for a in attrs)
        if price > 0 and (has_code or has_attr):
            valid += 1
    return True, valid, dims


def _duplicate_sku_signatures(product_sku) -> int:
    """返回重复的 SKU 属性组合数量（平台会拒绝重复变种）。"""
    obj = _json_container(product_sku)
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        rows = obj.get("special_skus") or obj.get("skus") or obj.get("sku_list") or []
    else:
        return 0
    seen, dup = set(), 0
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("exclude"):
            continue
        attrs = r.get("attributes") or []
        sig = tuple(sorted(
            (str(a.get("attribute_name") or a.get("attributeName") or ""),
             str(a.get("value_name") or a.get("valueName") or a.get("value") or ""))
            for a in attrs if isinstance(a, dict)))
        if not sig:
            continue
        if sig in seen:
            dup += 1
        seen.add(sig)
    return dup


def validate_alibaba_save(product: dict, save: dict, cfg=None,
                          required_attrs: list | None = None,
                          schema_fields: list | None = None) -> list[str]:
    """保存前校验：返回问题列表（空 = 通过）。必填项基于类目真实 required 标记。"""
    issues: list[str] = []
    # 类目合法性与脏值：9466975 有 27 个草稿 categoryId 是空/None，批量保存会把脏值提交。
    cid = str(save.get("categoryId") or "").strip()
    if not cid or cid.lower() in _INVALID_CATEGORY_VALUES:
        issues.append("类目（categoryId）为空或非法值，请先选择末级类目")
    elif not cid.isdigit() or int(cid) <= 0:
        issues.append(f"类目（categoryId={cid}）不是有效数字，请重新选择末级类目")
    else:
        full = str(save.get("fullCid") or "").strip()
        parts = [p.strip() for p in full.split(",") if p.strip()]
        if parts and parts[-1] != cid:
            issues.append(f"fullCid 末段（{parts[-1]}）与 categoryId（{cid}）不一致，请重新选择类目")
        if any(p.lower() in _INVALID_CATEGORY_VALUES or not p.isdigit() for p in parts):
            issues.append(f"fullCid 含非法类目路径段：{full or '空'}")

    subject = str(save.get("subject") or "").strip()
    if not subject:
        issues.append("产品名称（subject）为空")
    elif len(subject) > MAX_SUBJECT:
        issues.append(f"产品名称 {len(subject)} 字符，超过 {MAX_SUBJECT}")

    kws = [k for k in str(save.get("keywords") or "").split(",") if k.strip()]
    if not kws:
        issues.append(f"关键词为空（Alibaba 必填 {MIN_KEYWORDS} 个，最多 {MAX_KEYWORDS} 个）")
    elif len(kws) < MIN_KEYWORDS:
        issues.append(f"关键词至少 {MIN_KEYWORDS} 个（当前 {len(kws)} 个；Alibaba 要求 3 个必填关键词）")
    elif len(kws) > MAX_KEYWORDS:
        issues.append(f"关键词 {len(kws)} 个，最多 {MAX_KEYWORDS} 个")

    imgs = [u for u in str(save.get("mainImage") or "").split(",") if u.strip()]
    if not imgs:
        issues.append("主图为空")
    elif len(imgs) > MAX_MAIN_IMAGES:
        issues.append(f"主图 {len(imgs)} 张，最多 {MAX_MAIN_IMAGES} 张")
    bad_imgs = [u for u in imgs if not u.lower().startswith(("http://", "https://"))]
    if bad_imgs:
        issues.append(f"主图必须是 HTTP/HTTPS 地址：{bad_imgs[0][:80]}")

    ptype = str(save.get("priceType") or "ladder_price")
    tiers = _as_list(save.get("bulkDiscountPrices"))
    if ptype == "fob_price":
        try:
            fob = json.loads(save.get("sourcingTrade") or "{}")
        except Exception:
            fob = {}
        if not fob.get("fob_min_price") or not fob.get("fob_max_price"):
            issues.append("FOB 价格区间（fob_min_price / fob_max_price）未填写")
        elif float(fob.get("fob_min_price")) > float(fob.get("fob_max_price")):
            issues.append("FOB 最低价不能大于最高价")
        if not fob.get("fob_currency"):
            issues.append("FOB 币种未填写")
    elif ptype == "sku_price":
        has_sku, valid_skus, has_dims = sku_summary(save.get("productSku"))
        if not has_sku:
            issues.append("按规格定价（sku_price）但商品缺少 SKU 数据（productSku 为空），"
                          "请补 SKU 或改用按数量定价")
        else:
            if not has_dims:
                issues.append("按规格定价（sku_price）但 productSku 缺少 SKU 维度属性（attributes）")
            if valid_skus == 0:
                issues.append("按规格定价（sku_price）但 productSku 无有效 SKU"
                              "（special_skus 为空、全部 exclude 或无价格/规格），"
                              "请补 SKU 或改用按数量定价")
            _dup_sku = _duplicate_sku_signatures(save.get("productSku"))
            if _dup_sku:
                issues.append(f"SKU 属性组合重复 {_dup_sku} 组，平台会拒绝；请合并重复规格")
    elif not tiers:
        issues.append("阶梯价为空（按数量定价必须有数量区间）")
    _dup_sku_all = _duplicate_sku_signatures(save.get("productSku"))
    if _dup_sku_all and not any("SKU 属性组合重复" in x for x in issues):
        issues.append(f"SKU 属性组合重复 {_dup_sku_all} 组，平台会拒绝；请合并重复规格")
    if tiers:
        qs = [int(t.get("start_quantity") or 0) for t in tiers if isinstance(t, dict)]
        if qs != sorted(qs) or len(set(qs)) != len(qs):
            issues.append("阶梯价数量区间必须递增且不重复")
        if any(int(t.get("start_quantity") or 0) <= 0 for t in tiers if isinstance(t, dict)):
            issues.append("阶梯价起订数量必须 > 0")

    try:
        trade = json.loads(save.get("wholesaleTrade") or "{}")
    except Exception:
        trade = {}
    if int(trade.get("min_order_quantity") or 0) <= 0:
        issues.append("MOQ（min_order_quantity）必须 > 0")
    if trade.get("package_size") in (None, "", "0X0X0"):
        issues.append("包装尺寸 package_size 未填写")
    if not _num(trade.get("weight")):
        issues.append("包装重量 weight 未填写")

    # 必填属性四项阻断：缺失 / 值为空 / value_id 缺失 / 值不在平台候选枚举。
    save_attrs = [a for a in _as_list(save.get("attributes")) if isinstance(a, dict)]
    req_defs = [d for d in (required_attrs or []) if isinstance(d, dict)]
    req_by_name, req_by_id = _build_def_index(req_defs)
    def _rows_for_def(d):
        rows = [a for a in save_attrs if _match_def_for_attr(a, req_by_name, req_by_id) is d]
        if rows:
            return rows
        want = _norm(_def_label(d))
        aliases = set(ATTRIBUTE_ALIASES.get(want, ())) | {want}
        for a in save_attrs:
            nm = _norm((a or {}).get("attribute_name") or "")
            if nm and (nm in aliases or any(x in aliases for x in ATTRIBUTE_ALIASES.get(nm, ()))):
                rows.append(a)
        return rows
    for d in req_defs:
        name = _def_label(d)
        if not name:
            continue
        rows = _rows_for_def(d)
        if not rows:
            issues.append(f"必填属性缺失：{name}")
            continue
        entries = _value_entries(d)
        if not entries:
            # 自由填写属性：有非空值即可（枚举校验不适用）
            if not any(str(a.get("value_name") or "").strip() for a in rows):
                issues.append(f"必填属性「{name}」值为空，请填写后保存")
            continue
        sample = _candidate_sample(d)
        bad = None
        for a in rows:
            v_name = str(a.get("value_name") or "").strip()
            if not v_name:
                bad = (f"必填属性「{name}」值为空，请从平台候选值选择"
                       + (f"（候选示例：{sample}）" if sample else ""))
                break
            res = resolve_attr_value(d, v_name)
            if not res.get("matched"):
                why = res.get("reason") or "不在平台候选值中"
                bad = (f"必填属性「{name}」的值「{v_name}」{why}"
                       + (f"（候选示例：{sample}）" if sample else "")
                       + "，请从平台枚举选择")
                break
            if res.get("value_id") in (None, ""):
                bad = (f"必填属性「{name}」缺少 value_id"
                       f"（平台候选值「{res.get('value_name')}」没有 id），请重新选择")
                break
            vid = str(a.get("value_id") or "").strip()
            rid = str(res["value_id"])
            # 注意：平台国家枚举的「other」候选 id 就是 -1，不能一律当缺失。
            if vid in ("", "None") or (_is_missing_value_id(vid)
                                       and not _is_missing_value_id(rid)):
                bad = (f"必填属性「{name}」缺少 value_id"
                       f"（值「{res.get('value_name')}」应写平台 id={rid}），请从平台候选值重新选择")
                break
            if vid != rid:
                bad = (f"必填属性「{name}」的 value_id={vid} 与平台候选值"
                       f"「{res.get('value_name')}」的 id={rid} 不一致，请重新选择")
                break
        if bad:
            issues.append(bad)

    _desc = str(save.get("textDesc") or "")
    if len(_desc) > 2000:
        issues.append(f"详情 textDesc {len(_desc)} 字符，超过平台上限 2000（1000 汉字）")

    field_ids = [f.get("fieldId") for f in (schema_fields or []) if isinstance(f, dict)]
    if "detailImage" in field_ids and not _as_list(save.get("detailImage")):
        issues.append("详情图片（detailImage）为空")
    if "textDesc" in field_ids and not str(save.get("textDesc") or "").strip():
        issues.append("商品卖点/详情文字（textDesc）为空")
    return issues
