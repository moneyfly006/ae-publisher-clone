"""Per-run AI reservation and persistent result cache, before any paid operation."""
from . import collect_store as store
from .progress import emit


class Budget:
    def __init__(self, cfg, platform, run_id):
        self.cfg, self.platform, self.run_id = cfg, platform, run_id

    def call(self, kind, product, context, operation):
        prefix = self.platform + "_collect_"
        from . import model_of
        prompts = {key: value for key, value in self.cfg.as_dict().items()
                   if key.startswith(self.platform + "_") and ("prompt" in key or key.endswith("_site"))}
        facts = {key: value for key, value in product.items() if key not in (
            "errMsg", "err_msg", "dxmState", "dxmOfflineState", "updateTime", "modifiedTime", "publishTime", "lastUpdateTime")}
        key = store.digest(["v1", store.scope(self.cfg, self.platform), self.platform, kind, facts, context,
                            prompts, model_of(self.cfg, "image_edit" if kind == "image" else "text")])
        if getattr(self.cfg, prefix + "ai_cache", True):
            found, value = store.cached(key)
            if found:
                emit(self.platform + " · AI复用", "命中", kind + "使用已保存结果，无新增调用")
                return value
        rate = float(getattr(self.cfg, prefix + ("image_call_price" if kind == "image" else "text_call_price"), 0))
        cap = float(getattr(self.cfg, prefix + "ai_budget", 0))
        if cap and rate <= 0:
            raise store.BudgetExceeded("已设置金额上限，请先填写本次模型每次调用的预算单价")
        store.reserve(self.run_id, int(getattr(self.cfg, prefix + "ai_max_calls", 100)), cap, rate, key)
        value = operation()
        if kind == "sku":
            import re
            if not re.sub(r"[^A-Za-z0-9_-]", "", str(value or "")):
                raise ValueError("AI SKU结果无效，未缓存")
        if kind == "attributes":
            import json
            import re
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(value or "").strip())
            if not isinstance(json.loads(raw), dict):
                raise ValueError("AI属性结果格式无效，未缓存")
        if getattr(self.cfg, prefix + "ai_cache", True):
            store.cache_put(key, value)
        store.request_done(self.run_id, key)
        return value


def paid(cfg, platform, kind, product, context, operation):
    budget = getattr(cfg, "_collect_budget", None)
    return budget.call(kind, product, context, operation) if budget else operation()


def estimate(cfg, platform, count):
    import json
    prefix = platform + "_collect_"
    selected = json.loads(getattr(cfg, prefix + "ai_fields", "[]")) if getattr(cfg, prefix + "ai", False) else []
    text = count * (len(selected) + int(bool(getattr(cfg, prefix + "ai_sku", False))) + int(bool(getattr(cfg, prefix + "ai_attributes", False))))
    if platform == "alibaba" and getattr(cfg, prefix + "auto_repair", True):
        text += count * len(set(selected))
    images = 0
    if getattr(cfg, prefix + "images", False):
        from .platform_media import GROUPS, batch_rules
        images = count * sum(GROUPS[platform][row["group"]].limit if row["position"] == "all" else 1
                             for row in batch_rules(cfg, platform))
    rates = (float(getattr(cfg, prefix + "text_call_price", 0)), float(getattr(cfg, prefix + "image_call_price", 0)))
    known = (not text or rates[0] > 0) and (not images or rates[1] > 0)
    return dict(text=text, images=images, estimate=round(text * rates[0] + images * rates[1], 4) if known else None)
