"""变种/属性映射与校验。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


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


def check_consistency(skus: list[dict], required: list[str]) -> list[SkuIssue]:
    issues: list[SkuIssue] = []
    seen: set[tuple] = set()
    for sku in skus:
        name = str(sku.get("sku") or sku.get("name") or "")
        attrs = sku.get("attributes") or sku.get("prop") or {}
        if isinstance(attrs, list):
            attrs = {str(i): v for i, v in enumerate(attrs)}
        for r in required:
            if not attrs.get(r):
                issues.append(SkuIssue(name, f"缺少属性 {r}"))
        key = tuple(sorted((str(k), str(v)) for k, v in attrs.items()))
        if key in seen:
            issues.append(SkuIssue(name, "SKU 属性重复"))
        seen.add(key)
    return issues


def limit_skus(skus: list[dict], limit: int = 256) -> list[dict]:
    return skus[: max(0, limit)]


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
