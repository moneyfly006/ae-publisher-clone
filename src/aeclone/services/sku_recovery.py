"""Recover SKU specifications only from complete, same-product verified data."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..config import config_dir
from .api_save import validate_sku_combinations
from .product_data import clean_sku_rows, hydrate_product, sku_rows


def _codes(rows):
    codes = [str(row.get("skuCode") or "").strip() for row in rows]
    if not codes or not all(codes) or len(set(codes)) != len(codes):
        raise ValueError("恢复来源的 SKU 编码缺失或重复")
    return codes


def _context(product):
    return tuple(str(product.get(key) or "") for key in ("id", "shopId", "categoryId"))


def _validate_source(target, source):
    if not all(_context(target)) or _context(target) != _context(source):
        raise ValueError("恢复来源的商品、店铺或类目不一致")
    rows = sku_rows(source.get("aeopAeProductSKUs"))
    current = sku_rows(target.get("aeopAeProductSKUs"))
    if set(_codes(rows)) != set(_codes(current)) or len(rows) != len(current):
        raise ValueError("恢复来源未完整覆盖当前所有 SKU")
    for row in rows:
        props = row.get("aeopSKUProperty")
        if not isinstance(props, list) or not props or any(
                not isinstance(prop, dict) or not prop.get("skuPropertyId") or
                not (prop.get("propertyValueId") or prop.get("propertyValueDefinitionName")) for prop in props):
            raise ValueError("恢复来源缺少真实规格属性")
    validate_sku_combinations(rows)
    return rows


def _path(product, directory=None):
    digest = hashlib.sha256(json.dumps(_context(product)).encode()).hexdigest()
    return Path(directory or config_dir() / "sku-snapshots") / f"{digest}.json"


def _snapshot(product, directory=None):
    try:
        rows = _validate_source(product, product)
    except ValueError:
        return
    # Only identity/specification fields are needed; never restore stale prices or stock.
    entry = {key: product.get(key) for key in ("id", "shopId", "categoryId")}
    entry["aeopAeProductSKUs"] = [{key: deepcopy(row[key]) for key in
        ("skuCode", "id", "skuId", "aeopSKUProperty") if key in row} for row in rows]
    path = _path(product, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        json.dump(entry, output, ensure_ascii=False)
        temp = output.name
    os.replace(temp, path)


def _replace(target, source):
    rows = _validate_source(target, source)
    by_code = {str(row["skuCode"]).strip(): row for row in rows}
    repaired = deepcopy(target)
    result = sku_rows(target.get("aeopAeProductSKUs"))
    for row in result:
        saved = by_code[str(row["skuCode"]).strip()]
        for key in ("aeopSKUProperty", "id", "skuId"):
            if key in saved:
                row[key] = deepcopy(saved[key])
    repaired["aeopAeProductSKUs"] = result
    return repaired


def _merge_duplicate_rows(rows):
    """合并属性组合完全相同的 SKU：保留第一条，库存累加；返回 (rows, 合并数)。"""
    seen = {}
    out = []
    merged = 0
    for row in rows:
        props = row.get("aeopSKUProperty") or []
        # 平台查重只看 ID 组合，不看展示名；统一发货地后名称不同但 ID
        # 相同的 SKU 仍会被平台判重复，所以合并键不能带 propertyValueDefinitionName。
        combo = tuple(sorted(
            (str(p.get("skuPropertyId")), str(p.get("propertyValueId")),
             str(p.get("parentPropertyId") or p.get("plug_parent_property_id") or ""),
             str(p.get("parentValueId") or p.get("plug_parent_value_id") or ""))
            for p in props if isinstance(p, dict)))
        # 清空「发货地」后，只靠发货地区分的 SKU 会变成空属性组合；
        # 空组合同样必须合并，否则平台仍判重复规格，整单无法发布。
        key = combo or ("__EMPTY__",)
        if key in seen:
            kept = seen[key]
            try:
                kept["ipmSkuStock"] = str(int(float(kept.get("ipmSkuStock") or 0)) +
                                          int(float(row.get("ipmSkuStock") or 0)))
            except Exception:
                pass
            merged += 1
            continue
        seen[key] = row
        out.append(row)
    return out, merged


def recover_skus(product, client=None, directory=None):
    """Return a private product copy and repair note. No platform writes or AI calls."""
    result = deepcopy(product)
    raw = deepcopy(result.get("raw") or {})
    # 先清掉采集元数据/空值，再校验；否则会把 skuImage=None 之类误当规格，
    # 或者把清洗后才重复的组合放过去。
    rows = clean_sku_rows(sku_rows(raw.get("aeopAeProductSKUs")))
    raw["aeopAeProductSKUs"] = deepcopy(rows)
    result["raw"] = raw
    try:
        validate_sku_combinations(rows)
    except ValueError as original:
        candidates = []
        refreshed = None
        get = getattr(client, "_get", None)
        if callable(get):
            try:
                refreshed = (get("product_detail", {"id": str(result.get("id") or "")}).get("data") or {}).get("product")
                if isinstance(refreshed, dict):
                    candidates.append(("重新读取的商品详情", refreshed))
            except Exception:
                pass
        for candidate in (refreshed, raw):
            if isinstance(candidate, dict) and candidate.get("smtProductSkuList"):
                candidates.append(("同商品备用 SKU 数据", {**candidate,
                    "aeopAeProductSKUs": candidate["smtProductSkuList"]}))
        try:
            candidates.append(("本地已验证规格快照", json.loads(_path(raw, directory).read_text())))
        except (OSError, ValueError):
            pass
        for label, candidate in candidates:
            try:
                candidate = dict(candidate or {})
                candidate_rows = clean_sku_rows(sku_rows(candidate.get("aeopAeProductSKUs")))
                candidate["aeopAeProductSKUs"] = candidate_rows
                repaired = _replace(raw, candidate)
            except (ValueError, TypeError, KeyError):
                continue
            result = hydrate_product(result, repaired)
            result["_sku_spec_repair"] = {"before": deepcopy(raw), "source": repaired}
            _snapshot(repaired, directory)
            return result, f"从{label}恢复 {len(rows)} 个 SKU 规格，保留当前价格、库存和全部变种"
        # 最后兜底：平台要求变种组合唯一。找不到真实规格时，合并重复组合
        # （保留第一条、库存累加），否则整单永远发不出去。
        try:
            from ..config import get_config
            allow_merge = bool(getattr(get_config(), "auto_merge_duplicate_skus", True))
        except Exception:
            allow_merge = True
        if allow_merge and rows:
            merged_rows, merged_n = _merge_duplicate_rows(deepcopy(rows))
            if merged_n:
                repaired = {**raw, "aeopAeProductSKUs": merged_rows}
                result = hydrate_product(result, repaired)
                result["_sku_spec_repair"] = {"before": deepcopy(raw), "source": repaired,
                                              "merged": True}
                return result, (f"未找到真实规格，已自动合并 {merged_n} 个重复变种"
                                f"（保留每个组合第一条、库存累加）")
        raise ValueError(f"{original}；已自动核对最新详情、备用数据及已验证快照，未找到完整真实规格；请恢复原始规格，不能用模板或编号猜填") from original
    try:
        _snapshot(raw, directory)
    except OSError:
        pass  # Snapshot storage failure must not prevent an otherwise valid product.
    return result, ""


def apply_sku_repair(target, product):
    """Reapply a proven repair after re-fetch, rejecting concurrent specification changes."""
    repair = product.get("_sku_spec_repair")
    if not repair:
        return target
    current = sku_rows(target.get("aeopAeProductSKUs"))
    try:
        validate_sku_combinations(current)
    except ValueError:
        def signatures(value):
            rows = sku_rows(value.get("aeopAeProductSKUs"))
            return sorted((str(row.get("skuCode")), json.dumps(row.get("aeopSKUProperty"), sort_keys=True)) for row in rows)
        if _context(target) != _context(repair["before"]) or signatures(target) != signatures(repair["before"]):
            raise ValueError("保存前商品规格发生变化，已停止使用旧的修复结果，请重新读取")
        if repair.get("merged"):
            # 合并修复：修复后的行集就是最终要保存的行集（数量可能少于平台当前行）
            fixed = deepcopy(target)
            fixed["aeopAeProductSKUs"] = deepcopy(sku_rows(
                (repair["source"] or {}).get("aeopAeProductSKUs")))
            validate_sku_combinations(fixed["aeopAeProductSKUs"])
            return fixed
        return _replace(target, repair["source"])
    # Prefer valid fresh platform data over a historical repair.
    _validate_source(repair["source"], target)
    return target
