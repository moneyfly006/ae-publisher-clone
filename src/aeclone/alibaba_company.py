"""Alibaba 店铺资料预设（只使用 Alibaba 店铺页/商品页已公开、可核实的事实）。

用户可一键填入，也可以继续手工修改；这里不生成认证、产能或客户名单。
"""
from __future__ import annotations

from typing import Any

COMPANY_PROFILE = {
    "company_name": "Zhengzhou Yugong Construction Machinery Co., Ltd.",
    "website": "https://zzyugong.en.alibaba.com/",
    "location": "Zhengzhou, Henan, China",
    "established": "2023",
    "registered_capital": "RMB 50,000,000",
    "business_license": "91410103MACWN58K1B",
    "staff": "104",
    "collaborating_factories": "30",
    "floorspace": "500 m²",
    "main_markets": "Southeast Asia, Southern Europe, Northern Europe",
    "services": ("Drawing-based customization; sample-based customization; "
                 "raw material identification and traceability; finished product inspection"),
    "verification": "On-site checked and verified by TÜV Rheinland on Alibaba.com",
    "store_metrics": "5.0/5 store rating; 100.0% on-time dispatch; ≤3h response time",
    "customization": "Drawing-based, sample-based and full customization",
}

FACTS = (
    "Zhengzhou Yugong Construction Machinery Co., Ltd. | Zhengzhou, Henan, China | "
    "Established 2023 | Registered capital RMB 50,000,000 | 104 staff | "
    "30 collaborating factories | 500 m² floorspace | "
    "Main markets: Southeast Asia, Southern Europe, Northern Europe | "
    "Verified on Alibaba.com by TÜV Rheinland"
)


def company_desc() -> str:
    p = COMPANY_PROFILE
    return (
        f"{p['company_name']} is a construction machinery supplier based in "
        f"{p['location']}. Established in {p['established']} with registered capital of "
        f"{p['registered_capital']}, we serve buyers in {p['main_markets']}. "
        f"We work with {p['collaborating_factories']} collaborating factories and a team of "
        f"{p['staff']} staff, with {p['floorspace']} of floorspace. Our services include "
        f"{p['services']}. Our Alibaba.com store has a 5.0/5 store rating, 100.0% on-time "
        f"dispatch and a response time of ≤3 hours, and has been on-site checked and "
        f"verified by TÜV Rheinland."
    )


def company_faq() -> str:
    return (
        "Q: Are you a manufacturer or trading company?\n"
        "A: We are a construction machinery supplier based in Zhengzhou, Henan, China, "
        "working with 30 collaborating factories.\n\n"
        "Q: What is your MOQ?\n"
        "A: 1 set or 1 piece for most models; please confirm the exact model with our team.\n\n"
        "Q: Can you customize the machine?\n"
        "A: Yes. Drawing-based, sample-based and full customization are available for "
        "selected products; share your drawing or sample requirements for confirmation.\n\n"
        "Q: What are your main markets?\n"
        "A: Southeast Asia, Southern Europe and Northern Europe.\n\n"
        "Q: What is the delivery time?\n"
        "A: Delivery is confirmed per order. Our current Alibaba.com store reports an "
        "average dispatch time of 50 days; please confirm the final date with our team.\n\n"
        "Q: What are the payment terms?\n"
        "A: T/T; final terms are confirmed on the order.\n\n"
        "Q: What warranty do you provide?\n"
        "A: The referenced concrete pump lists a 1-year warranty; warranty is confirmed "
        "per model."
    )


def apply_profile(cfg: Any, save=None) -> dict:
    """Fill company fields in cfg; returns the written values."""
    values = {
        "alibaba_company_name": COMPANY_PROFILE["company_name"],
        "alibaba_company_website": COMPANY_PROFILE["website"],
        "alibaba_company_address": COMPANY_PROFILE["location"],
        "alibaba_company_license": COMPANY_PROFILE["business_license"],
        "alibaba_company_desc": company_desc(),
        "alibaba_company_faq": company_faq(),
    }
    for key, value in values.items():
        cfg.set(key, value)
    if callable(save):
        save(cfg)
    return values
