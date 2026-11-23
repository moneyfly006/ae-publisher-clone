"""把当前机器上的配置导出成打包默认值（排除密钥/账号/店铺等个人数据）。

用法：python tools/export_preset.py
产物：assets/preset_defaults.json（随包发布，新装即用这套默认）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeclone.config import DEFAULTS, config_dir  # noqa: E402

# 绝不写进安装包的键
SECRET_KEYS = {
    "api_key", "image_host_key", "manual_cookies", "cookies", "cookie",
    "password", "token", "secret", "ws_endpoint",
    "store_id", "store_name", "shop_id",
    "template_product_id", "template_product_title", "template_snapshot",
    "template_values", "template_source",
    "start_page", "last_page", "license_identity", "license_cache_ok", "instance", "onboarded",
    "alibaba_cookies", "license_account_email", "license_platforms", "license_token",
    "license_token_ts", "license_server", "api_base",
    # 个人状态：导出的预设不得把「上次停留的平台」带进安装包
    # （2026-11-22 曾把 active_platform 导成 amazon，导致新装默认平台错误、
    #  test_ui_redesign 失败；打包默认平台必须由代码 DEFAULTS["alibaba"] 决定）。
    "active_platform",
    # 个人操作痕迹：用户点过「定价方式」后为 "1"，会把所有商品静默改成阶梯价，
    # 属于个人选择而非安装默认（2026-11-22 被导出后导致 test_alibaba_payload 回归）。
    "alibaba_price_type_explicit",
    # 采集处理默认不开启付费 AI/图片，避免新装没有 API Key 就启动失败；
    # 旧机器如果确实开了，用户可在对应页面自行开启，不再随安装包导出。
    "alibaba_collect_ai", "alibaba_collect_images",
    # 保存去向是个人任务选择，不能成为安装默认（否则新装会把商品移入待发布）
    "alibaba_collect_op", "amazon_collect_op", "tiktok_collect_op", "shopee_collect_op",
    "amazon_collect_ai", "amazon_collect_images",
    "tiktok_collect_ai", "tiktok_collect_images",
    "shopee_collect_ai", "shopee_collect_images",
    # Alibaba 刊登的推荐信息/商品状态属于可编辑业务数据，不随安装包预设导出；
    # 代码 DEFAULTS 里已经有机械类推荐值，用户仍可在「Alibaba 刊登」页一键填入。
    "alibaba_category_id", "alibaba_category_name", "alibaba_full_cid",
    "alibaba_package_size", "alibaba_weight", "alibaba_stock", "alibaba_hs_code",
    "alibaba_freight_template_id", "alibaba_process_period",
    "alibaba_deliver_quantity", "alibaba_company_desc", "alibaba_company_faq",
    "alibaba_keywords", "alibaba_test_product_id", "alibaba_shop_name",
    "alibaba_ai_preset", "alibaba_price_type", "alibaba_attr_presets",
}

# 授权/密钥类前缀一律不进安装包
SECRET_PREFIXES = ("license_", "alibaba_attr", "alibaba_cookies")

# 敏感/个人数据后缀：逐平台 Cookie、店铺 ID、店铺名、token 等全部排除。
# 2026-11-22 事故：amazon_cookies（968 字符真实 Cookie）曾被导出进安装包预设；
# *_shop_id（amazon_shop_id=9408005 等）也属于个人账号数据。
SECRET_SUFFIXES = (
    "_cookies", "_cookie", "_token", "_secret", "_password", "_api_key",
    "_shop_id", "_shop_name", "_store_id", "_store_name", "_site", "_site_code", "_site_name",
)


def _is_secret_key(key: str) -> bool:
    k = str(key or "")
    lower = k.lower()
    return (
        k in SECRET_KEYS
        or k.startswith(SECRET_PREFIXES)
        or lower.endswith(SECRET_SUFFIXES)
        or "token" in lower
        or "secret" in lower
        or "password" in lower
    )


def main() -> int:
    cfg_path = config_dir() / "config.json"
    if not cfg_path.is_file():
        print(f"找不到配置：{cfg_path}")
        return 1
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    out = {}
    skipped = []
    for k, v in raw.items():
        if _is_secret_key(k):
            skipped.append(k)
            continue
        if k not in DEFAULTS:
            continue
        out[k] = v
    dst = ROOT / "assets" / "preset_defaults.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    on = sorted(k for k, v in out.items() if v is True)
    print(f"已写入 {dst}（{len(out)} 项，跳过敏感/个人 {len(skipped)} 项）")
    print(f"开启的开关 {len(on)} 个：")
    for k in on:
        print(f"  ✓ {k}")
    if skipped:
        print("已排除：" + ", ".join(sorted(skipped)[:12]) + ("…" if len(skipped) > 12 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
