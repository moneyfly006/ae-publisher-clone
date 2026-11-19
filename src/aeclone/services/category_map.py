"""类目自动匹配：关键词映射 + 从历史商品学习。

优先级：
  1. 精确关键词命中（用户在 UI 里配的，或学习得到的高频词）
  2. 商品自带类目（如果采集数据里有）
  3. 模板类目（兜底）
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ..config import config_dir

_MAP_FILE = "category_map.json"

# 通用词：出现在任何类目的标题里，学到会跨类目误判
_STOP = {
    "with", "for", "and", "the", "from", "into", "your", "this", "that", "use",
    "used", "new", "hot", "sale", "high", "low", "fast", "speed", "quality",
    "good", "best", "top", "auto", "automatic", "manual", "type", "types",
    "set", "sets", "kit", "kits", "pcs", "piece", "pieces", "inch", "inches",
    "large", "small", "mini", "big", "heavy", "light", "industrial", "machine",
    "machines", "equipment", "product", "products", "tool", "tools", "drive",
    "driven", "power", "powered", "motor", "electric", "diesel", "gas", "oil",
    "water", "steel", "iron", "plastic", "wood", "metal", "custom", "customized",
    "professional", "commercial", "home", "portable", "mobile", "design", "style",
    "model", "series", "version", "capacity", "size", "color", "colors",
}


def _path() -> Path:
    p = config_dir() / _MAP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    """返回 {"rules": [{"kw": "...", "categoryId": "..."}], "learned": {"kw": "cid"}}"""
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("rules", [])
            d.setdefault("learned", {})
            return d
    except Exception:
        pass
    return {"rules": [], "learned": {}}


def save(d: dict) -> None:
    try:
        _path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def match(title: str, mapping: dict | None = None) -> str:
    """按关键词匹配类目。先看用户规则（长的优先），再看学习表。"""
    m = mapping or load()
    t = _norm(title)
    if not t:
        return ""
    # 用户规则：关键词越长越具体，优先
    rules = [r for r in (m.get("rules") or []) if isinstance(r, dict)]
    rules.sort(key=lambda r: len(str(r.get("kw") or "")), reverse=True)
    for r in rules:
        kw = _norm(r.get("kw"))
        cid = str(r.get("categoryId") or "").strip()
        if kw and cid and kw in t:
            return cid
    # 学习表
    learned = m.get("learned") or {}
    hits = {kw: cid for kw, cid in learned.items()
            if kw and str(cid or "").strip() and _norm(kw) in t}
    if hits:
        return str(max(hits.items(), key=lambda kv: len(kv[0]))[1]).strip()
    return ""


def _words(title: str, min_len: int = 4) -> list[str]:
    """从英文标题里抽关键词（连续的字母词，长度 >= min_len）。"""
    t = str(title or "").lower()
    toks = re.findall(r"[a-z][a-z\-]{2,}", t)
    out, seen = [], set()
    # 单词
    for w in toks:
        if len(w) >= min_len and w not in seen:
            seen.add(w); out.append(w)
    # 相邻双词
    for i in range(len(toks) - 1):
        a, b = toks[i], toks[i + 1]
        two = f"{a} {b}"
        if len(two) >= 8 and two not in seen:
            seen.add(two); out.append(two)
    return out


def pick_title(item: dict) -> str:
    return str((item or {}).get("title") or (item or {}).get("subject") or "").strip()


def pick_category(item: dict) -> str:
    return str((item or {}).get("categoryId") or (item or {}).get("category") or "").strip()


def learn_from_products(items: list[dict], max_per_cat: int = 30) -> dict:
    """items: [{title, categoryId}] → 学习表 {keyword: categoryId}。

    同一关键词若指向多个类目，取出现次数最多的；词频太低的不收。
    """
    if not items:
        return {}
    kw_cat: dict[str, Counter] = {}
    for it in items:
        title = pick_title(it)
        cid = pick_category(it)
        if not title or not cid:
            continue
        for w in _words(title):
            kw_cat.setdefault(w, Counter())[cid] += 1
    # 关键词 -> 最高频类目；只保留在该类目下出现 >= 2 次且纯度 >= 60% 的
    learned: dict[str, str] = {}
    for kw, cnt in kw_cat.items():
        if kw in _STOP:
            continue
        if any(t in _STOP for t in kw.split()):
            continue
        if len(kw.replace(" ", "")) < 5:
            continue
        cid, n = cnt.most_common(1)[0]
        total = sum(cnt.values())
        if n >= 2 and total and n / total >= 0.6:
            learned[kw] = cid
    return learned


def merge_learned(new_learned: dict) -> dict:
    d = load()
    cur = dict(d.get("learned") or {})
    cur.update({k: v for k, v in (new_learned or {}).items() if k and v})
    d["learned"] = cur
    save(d)
    return d
