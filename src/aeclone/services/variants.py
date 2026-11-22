"""变种/属性映射与校验。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from math import isfinite, prod


@dataclass
class SkuIssue:
    sku: str
    reason: str


def normalize_name(name: str) -> str:
    return re.sub(r"[\s_/]+", " ", (name or "").strip().lower())


def auto_map(source_attrs: list[str], target_attrs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in source_attrs:
        ns = normalize_name(s)
        best, score = None, 0.0
        for t in target_attrs:
            nt = normalize_name(t)
            if ns == nt:
                best, score = t, 1.0
                break
            if ns in nt or nt in ns:
                sc = 0.8
            else:
                sw, tw = set(ns.split()), set(nt.split())
                sc = len(sw & tw) / max(1, len(sw | tw))
            if sc > score:
                best, score = t, sc
        if best and score >= 0.5:
            out[s] = best
    return out


def _token(value: object) -> str:
    """Only scalar attribute identifiers/values have an unambiguous meaning."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("属性键和值必须是非空标量")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("属性值不能是非有限数")
    text = str(value)
    if not text.strip():
        raise ValueError("属性键和值不能为空")
    return text


def _dimensions(sku: dict) -> tuple[str, dict[str, str]]:
    if not isinstance(sku, dict):
        raise ValueError("SKU 必须是字典")
    # Empty aliases may fall back to the next representation, as before; a
    # nonempty malformed alias must never silently hide behind a valid one.
    sources = [(key, sku[key]) for key in ("attributes", "prop", "aeopSKUProperty")
               if key in sku and sku[key] is not None and sku[key] != {} and sku[key] != []]
    if len(sources) > 1:
        raise ValueError("SKU 同时包含多种属性格式")
    if not sources:
        return "unknown", {}
    source, raw = sources[0]
    if source == "aeopSKUProperty":
        if not isinstance(raw, list):
            raise ValueError("aeopSKUProperty 必须是属性列表")
        pairs = []
        # 两种真实形态：
        # 1) 标准：{"skuPropertyId":..,"propertyValueId":..}
        # 2) 命名：{"Model Number":"YG-150","Power (W)":"60KW","skuImage":null,"extraImages":""}
        #    店小秘/速卖通采集数据常见命名形态，元数据字段（skuImage/extraImages 等）
        #    不是规格维度，必须忽略，否则会误报「属性键和值必须是非空标量」。
        _META = {
            "skuimage", "extraimages", "image", "img", "imageurllist",
            "skupropertyname", "propertyvaluename", "propertyvaluedefinitionname",
            "skuid", "sku", "id", "skupropertyid", "propertyvalueid",
            "parentpropertyid", "parentvalueid", "plug_parent_property_id",
            "plug_parent_value_id", "mobilemodelskuimage", "skupropertylist",
            "skucode", "currencycode", "skuprice", "ipmskustock", "skustock",
        }
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("标准属性必须是字典")
            if "skuPropertyId" in item or "propertyValueId" in item:
                pairs.append((item.get("skuPropertyId"), item.get("propertyValueId")))
                continue
            for key, value in item.items():
                low = str(key).strip().lower()
                if low in _META or value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                # 命名形态里的容器值（列表/字典）只会是采集元数据（图片列表等），
                # 不是规格维度；直接忽略，避免误报「必须是非空标量」。
                if isinstance(value, (list, dict)):
                    continue
                pairs.append((key, value))
        style = "standard"
    elif isinstance(raw, dict):
        pairs = list(raw.items())
        style = "named"
    elif isinstance(raw, list):
        pairs = list(enumerate(raw))
        style = "positional"
    else:
        raise ValueError("attributes/prop 必须是字典或标量列表")
    attrs: dict[str, str] = {}
    for key, value in pairs:
        key, value = _token(key), _token(value)
        if key in attrs:
            raise ValueError(f"重复属性维度 {key}")
        attrs[key] = value
    return style, attrs


def check_consistency(
    skus: list[dict], required: list[str], *, require_complete: bool = False,
) -> list[SkuIssue]:
    """Validate attributes without assuming sparse catalogs are broken.

    Set require_complete=True only when the caller knows the catalog must be
    Cartesian. Counts of distinct tuples then detect missing combinations,
    including balanced sparse grids that per-value frequency checks miss.
    Observed values are the only domain source: entirely absent values cannot
    be inferred. Default checks still report malformed/mixed schemas, missing
    required attributes and duplicate combinations. No input is mutated.
    """
    issues: list[SkuIssue] = []
    seen: set[tuple] = set()
    schema = None
    domains: dict[str, set[str]] = {}
    valid = True
    for sku in skus:
        name = str(sku.get("sku") or sku.get("name") or "") if isinstance(sku, dict) else ""
        try:
            style, attrs = _dimensions(sku)
        except ValueError as exc:
            issues.append(SkuIssue(name, str(exc)))
            valid = False
            continue
        for r in required:
            if str(r) not in attrs:
                issues.append(SkuIssue(name, f"缺少属性 {r}"))
        current_schema = (style, frozenset(attrs))
        if schema is None:
            schema = current_schema
        elif schema != current_schema:
            issues.append(SkuIssue(name, "SKU 属性维度或格式不一致"))
            valid = False
        key = tuple(sorted(attrs.items()))
        if key in seen:
            issues.append(SkuIssue(name, "SKU 属性重复"))
        seen.add(key)
        for dim, value in attrs.items():
            domains.setdefault(dim, set()).add(value)
    if require_complete and valid and domains:
        expected = prod(len(values) for values in domains.values())
        if len(seen) != expected:
            issues.append(SkuIssue("", f"SKU 属性组合不完整：应有 {expected} 种，实际 {len(seen)} 种"))
    return issues


def limit_skus(skus: list[dict], limit: int | None = 256) -> list[dict]:
    """Keep whole dimension values, never an arbitrary prefix of SKU rows.

    Largest domains are reduced first; ties follow first-SKU dimension order.
    Values are removed from the end of their first-appearance order, retaining
    at least one value per dimension. Rows and their references keep input
    order. Unknown/mixed schemas or irreducible duplicate groups raise
    ValueError only when clipping is needed. No-op returns the original list.
    """
    if limit is None:
        return skus
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("SKU 数量上限必须是整数或 None")
    if limit <= 0 or len(skus) <= limit:
        return skus
    parsed = [_dimensions(sku) for sku in skus]
    style, first = parsed[0]
    if not first or any(s != style or set(attrs) != set(first) for s, attrs in parsed):
        raise ValueError("超限 SKU 属性未知、维度缺失或格式混合，无法安全裁剪")
    remaining = list(zip(skus, (attrs for _, attrs in parsed)))
    while len(remaining) > limit:
        domains = {dim: list(dict.fromkeys(attrs[dim] for _, attrs in remaining))
                   for dim in first}
        dim = max(domains, key=lambda key: len(domains[key]))
        values = domains[dim]
        if len(values) <= 1:
            raise ValueError("SKU 整值裁剪后仍超限，无法安全裁剪重复组合")
        # Reduce the largest dimension as a whole before cascading to others.
        counts = dict.fromkeys(values, 0)
        for _, attrs in remaining:
            counts[attrs[dim]] += 1
        count = len(remaining)
        while count > limit and len(values) > 1:
            count -= counts[values.pop()]
        keep = set(values)
        remaining = [(sku, attrs) for sku, attrs in remaining if attrs[dim] in keep]
    return [sku for sku, _ in remaining]



def sku_code(prefix: str, index: int, sep: str = "-") -> str:
    return f"{prefix}{sep}{index:03d}" if prefix else f"{index:03d}"


def parse_mapping_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text or "[]")
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        out = {}
        for row in data:
            if isinstance(row, dict) and row.get("from"):
                out[str(row["from"])] = str(row.get("to", ""))
        return out
    except Exception:
        return {}
