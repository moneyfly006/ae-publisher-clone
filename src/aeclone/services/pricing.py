"""价格计算：倍率 / 包邮 / 佣金 / 阶梯 / 区域价。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriceResult:
    base: float
    multiplier: float
    fixed_add: float
    final: float
    note: str = ""


def calc_multiplier(base: float, multiplier: float, fixed_add: float = 0.0) -> PriceResult:
    return PriceResult(base, multiplier, fixed_add, round(base * multiplier + fixed_add, 2), "倍率价格")


def calc_free_shipping(base: float, express: float, profit_rate: float = 0.0) -> PriceResult:
    return PriceResult(base, 1 + profit_rate, express, round((base + express) * (1 + profit_rate), 2), "包邮价格")


def calc_with_commission(base: float, multiplier: float, fixed_add: float,
                         commission_rate: float, profit_rate: float) -> PriceResult:
    cost = base * (1 - commission_rate) if commission_rate < 1 else base
    final = (cost + fixed_add) * multiplier * (1 + profit_rate)
    return PriceResult(base, multiplier, fixed_add, round(final, 2), "含佣金/利润")


def convert_currency(amount: float, rate: float) -> float:
    return round(amount * rate, 2)


def regional_prices(base_price: float, rate_map: dict[str, float]) -> dict[str, float]:
    return {cur: convert_currency(base_price, r) for cur, r in rate_map.items()}


def apply_tier(price: float, tiers: list[dict]) -> float:
    for t in tiers or []:
        try:
            lo = float(t.get("min", 0)); hi = float(t.get("max", 1e18))
            if lo <= price < hi:
                return round(price * float(t.get("multiplier", 1)), 2)
        except Exception:
            continue
    return price
