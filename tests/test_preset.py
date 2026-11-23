"""打包预设：必须存在、不含密钥，且能作为新装默认值生效。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name} {extra}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


print("=" * 60)
print("打包预设测试")

p = ROOT / "assets" / "preset_defaults.json"
chk("预设文件存在", p.is_file())
data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
for secret in ("api_key", "image_host_key", "manual_cookies", "store_id",
               "template_product_id", "license_identity", "license_cache_ok"):
    chk(f"不含 {secret}", secret not in data)

from aeclone.config import DEFAULTS, _preset_path  # noqa: E402

chk("运行时能找到预设", _preset_path() is not None)
chk("预设已合并进 DEFAULTS",
    DEFAULTS.get("price_mode") == data.get("price_mode"),
    f"price_mode={DEFAULTS.get('price_mode')}")
chk("倍率来自预设", str(DEFAULTS.get("price_multiplier")) == str(data.get("price_multiplier")))
chk("API Key 仍为空", not DEFAULTS.get("api_key"))
# 打包默认平台必须是 alibaba（个人「上次停留的平台」不得进安装包）
chk("默认平台为 alibaba", str(data.get("active_platform") or "alibaba") == "alibaba",
    f"active_platform={data.get('active_platform')!r}")
chk("运行时默认平台为 alibaba", DEFAULTS.get("active_platform") == "alibaba",
    f"DEFAULTS={DEFAULTS.get('active_platform')!r}")

# 隐私硬约束：任何 Cookie / token / 店铺 ID 都不得进安装包。
# 2026-11-22 事故：amazon_cookies（真实 968 字符 Cookie）被导出进预设。
import re  # noqa: E402

_SENSITIVE_KEY = re.compile(
    r"(cookie|token|secret|password|api_key|shop_id|store_id|store_name)", re.I)
leaked_keys = sorted(k for k in data if _SENSITIVE_KEY.search(k))
chk("不含任何 Cookie/token/店铺 ID 键", not leaked_keys, str(leaked_keys[:8]))
_COOKIE_VALUE = re.compile(r"(JSESSIONID=|dxm_i=|dxm_t=|_dxm_ad_client_id=|sessionid)",
                           re.I)
leaked_values = sorted(k for k, v in data.items() if _COOKIE_VALUE.search(str(v or "")))
chk("预设值不含 Cookie 内容", not leaked_values, str(leaked_values[:8]))

print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)
