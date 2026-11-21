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
}


def main() -> int:
    cfg_path = config_dir() / "config.json"
    if not cfg_path.is_file():
        print(f"找不到配置：{cfg_path}")
        return 1
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    out = {}
    skipped = []
    for k, v in raw.items():
        if k in SECRET_KEYS:
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
