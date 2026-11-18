"""价格计算：倍率 / 包邮 / 佣金利润 / 阶梯倍率 / 区域倍率 / 批发价。

设计原则（第 3 轮）：
- 界面只展示表格与数字，JSON 只作为内部存储；
- 刊登与界面试算共用同一套 `compute_price`，避免「试算和实际不一致」；
- 阶梯按「成本区间」选倍率，再乘成本 + 固定加价；
- 区域倍率只是「结算货币展示」，不改变人民币成交价。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class PriceResult:
    base: float
    multiplier: float
    fixed_add: float
    final: float
    note: str = ""
    tier_multiplier: float | None = None
    regional: dict[str, float] = field(default_factory=dict)
    wholesale: list[dict] = field(default_factory=list)
    mode: str = "multiplier"


def calc_multiplier(base: float, multiplier: float, fixed_add: float = 0.0) -> PriceResult:
    return PriceResult(base, multiplier, fixed_add, round(base * multiplier + fixed_add, 2), "倍率价格",
                       mode="multiplier")


def calc_free_shipping(base: float, express: float, profit_rate: float = 0.0) -> PriceResult:
    return PriceResult(base, 1 + profit_rate, express, round((base + express) * (1 + profit_rate), 2),
                       "包邮价格", mode="free_shipping")


def calc_with_commission(base: float, multiplier: float, fixed_add: float,
                         commission_rate: float, profit_rate: float) -> PriceResult:
    import math
    if not math.isfinite(commission_rate) or not 0 <= commission_rate < 1:
        raise ValueError("平台佣金必须在 0%（含）到 100%（不含）之间")
    # Commission is deducted from the selling price, not from acquisition cost.
    final = (base + fixed_add) * multiplier * (1 + profit_rate) / (1 - commission_rate)
    return PriceResult(base, multiplier, fixed_add, round(final, 2), "含佣金/利润", mode="commission")


def convert_currency(amount: float, rate: float) -> float:
    return round(amount * rate, 2)


def regional_prices(base_price: float, rate_map: dict[str, float]) -> dict[str, float]:
    out = {}
    for cur, r in (rate_map or {}).items():
        try:
            out[str(cur)] = convert_currency(base_price, float(r))
        except Exception:
            continue
    return out


# ---------------- 表格数据（内部仍是 JSON，但用户只看到表格）----------------
def load_json(raw, default):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        data = json.loads(raw or "")
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def parse_tiers(raw) -> list[dict]:
    """阶梯倍率： [{min,max,multiplier}]，按成本区间。"""
    rows = load_json(raw, [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            lo = float(r.get("min") or 0)
            hi = float(r.get("max") or 1e18)
            mult = float(r.get("multiplier") or 1)
        except Exception:
            continue
        if hi <= lo:
            continue
        out.append({"min": lo, "max": hi, "multiplier": mult})
    return sorted(out, key=lambda x: x["min"])


def pick_tier_multiplier(cost: float, tiers: list[dict]) -> float | None:
    """按成本落在哪一档返回倍率（含下限、不含上限）。"""
    for t in tiers:
        if t["min"] <= cost < t["max"]:
            return t["multiplier"]
    return None


def parse_regional(raw) -> dict[str, float]:
    data = load_json(raw, {})
    out = {}
    for k, v in (data or {}).items():
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out


def parse_wholesale(raw) -> list[dict]:
    rows = load_json(raw, [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            qty = int(float(r.get("min_qty") or r.get("qty") or 0))
            price = float(r.get("price") or 0)
        except Exception:
            continue
        if qty > 0 and price > 0:
            out.append({"min_qty": qty, "price": round(price, 2)})
    return sorted(out, key=lambda x: x["min_qty"])


def compute_price(cfg, cost, *, commission: float | None = None, profit: float | None = None) -> PriceResult:
    """统一计价入口：界面试算和刊登都用它。"""
    base = float(cost or 0)
    mode = str(getattr(cfg, "price_mode", "multiplier") or "multiplier")
    try:
        mult = float(getattr(cfg, "price_multiplier", 1.2) or 1.2)
    except Exception:
        mult = 1.2
    try:
        add = float(getattr(cfg, "price_fixed_add", 0) or 0)
    except Exception:
        add = 0.0

    tiers = parse_tiers(getattr(cfg, "tuoguan_price_tiers", "[]"))
    tier_m = pick_tier_multiplier(base, tiers) if getattr(cfg, "tuoguan_price_tiered_enabled", False) else None
    if tier_m:
        mult = tier_m

    commission_rate = (float(commission) / 100.0) if commission is not None else (
        float(getattr(cfg, "platform_commission", 0) or 0) / 100.0)
    profit_rate = (float(profit) / 100.0) if profit is not None else (
        float(getattr(cfg, "sales_profit", 0) or 0) / 100.0)
    express = float(getattr(cfg, "default_express_price", 0) or 0)

    if mode == "commission":
        r = calc_with_commission(base, mult, add, commission_rate, profit_rate)
        if tier_m:
            r.note = f"含佣金/利润 · 阶梯倍率 {tier_m:g}"
    elif mode == "free_shipping":
        r = calc_free_shipping(base, express, profit_rate)
        if tier_m:
            # 包邮模式下阶梯倍率作为「对成本的加价倍率」
            r = PriceResult(base, mult, express, round((base * mult + express) * (1 + profit_rate), 2),
                            f"包邮价格 · 阶梯倍率 {mult:g}", mode="free_shipping")
    else:
        r = calc_multiplier(base, mult, add)
        if tier_m:
            r.note = f"倍率价格 · 阶梯倍率 {tier_m:g}"

    r.tier_multiplier = tier_m
    # 区域倍率：默认人民币成交价 × 倍率
    if getattr(cfg, "regional_pricing", False):
        rates = parse_regional(getattr(cfg, "adjust_price_template_detail", "{}"))
        if rates:
            r.regional = regional_prices(r.final, rates)
    if getattr(cfg, "wholesale_enable", False):
        r.wholesale = parse_wholesale(getattr(cfg, "wholesale_tiers", "[]"))
    return r


def apply_tier(price: float, tiers: list[dict]) -> float:
    """兼容旧接口：按「售价」区间乘倍率（新代码请用 compute_price）。"""
    for t in tiers or []:
        try:
            lo = float(t.get("min", 0)); hi = float(t.get("max", 1e18))
            if lo <= price < hi:
                return round(price * float(t.get("multiplier", 1)), 2)
        except Exception:
            continue
    return price


# ---------------- 表格编辑辅助（界面用）----------------
def tiers_to_rows(raw) -> list[dict]:
    return parse_tiers(raw)


def rows_to_tiers(rows: list[dict]) -> str:
    return json.dumps(parse_tiers(rows), ensure_ascii=False)


def regional_to_rows(raw) -> list[dict]:
    return [{"currency": k, "rate": v} for k, v in parse_regional(raw).items()]


def rows_to_regional(rows: list[dict]) -> str:
    out = {}
    for r in rows or []:
        cur = str(r.get("currency") or "").strip().upper()
        if not cur:
            continue
        try:
            out[cur] = float(r.get("rate"))
        except Exception:
            continue
    return json.dumps(out, ensure_ascii=False)


def wholesale_to_rows(raw) -> list[dict]:
    return parse_wholesale(raw)


def rows_to_wholesale(rows: list[dict]) -> str:
    return json.dumps(parse_wholesale(rows), ensure_ascii=False)
