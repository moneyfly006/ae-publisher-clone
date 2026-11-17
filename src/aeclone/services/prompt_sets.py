"""提示词方案库：按品类保存多套提示词，刊登时自动套用。"""
from __future__ import annotations

PROMPT_KEYS = ("title_prompt", "detail_prompt", "sku_prompt", "weight_prompt",
               "main_image_generate_prompt", "designated_edit_prompt",
               "translate_prompt", "background_gen_sku_prompt", "edit_prompt")


def list_sets(cfg) -> list[dict]:
    raw = getattr(cfg, "prompt_sets", None) or []
    return [x for x in raw if isinstance(x, dict) and x.get("name")]


def save_set(cfg, name: str, category: str = "") -> dict:
    item = {"name": name.strip(), "category": (category or "").strip()}
    for k in PROMPT_KEYS:
        item[k] = cfg.get(k, "")
    sets = [x for x in list_sets(cfg) if x.get("name") != item["name"]]
    sets.append(item)
    cfg.set("prompt_sets", sets)
    return item


def delete_set(cfg, name: str) -> bool:
    sets = [x for x in list_sets(cfg) if x.get("name") != name]
    cfg.set("prompt_sets", sets)
    return True


def find_set(cfg, name: str) -> dict | None:
    for x in list_sets(cfg):
        if x.get("name") == name:
            return x
    return None


def apply_set(cfg, name: str) -> bool:
    item = find_set(cfg, name)
    if not item:
        return False
    for k in PROMPT_KEYS:
        if item.get(k):
            cfg.set(k, item[k])
    cfg.set("active_prompt_set", name)
    return True


def match_by_category(cfg, category_text: str) -> dict | None:
    """按品类关键词匹配方案（商品类目/标题包含该品类词即命中）。"""
    if not getattr(cfg, "prompt_set_by_category", True):
        return None
    hay = (category_text or "").lower()
    if not hay:
        return None
    best = None
    for item in list_sets(cfg):
        cat = (item.get("category") or "").strip().lower()
        if cat and cat in hay:
            best = item
            break
    return best


def apply_for_product(cfg, category_text: str = "") -> str:
    """刊登前自动套用：优先按品类匹配，其次用当前激活方案。返回套用的方案名。"""
    item = match_by_category(cfg, category_text)
    if item is None:
        name = (getattr(cfg, "active_prompt_set", "") or "").strip()
        item = find_set(cfg, name) if name else None
    if not item:
        return ""
    for k in PROMPT_KEYS:
        if item.get(k):
            cfg.set(k, item[k])
    return item.get("name", "")
