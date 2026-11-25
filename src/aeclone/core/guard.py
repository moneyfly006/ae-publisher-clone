"""核心层授权守卫：不依赖 UI，防止「改一个配置就绕过」。

思路（契约 §10.3）
------------------
授权中心登录成功后下发 HMAC 签名 token，里面带 machine_code / platforms /
expires_at。真正干活的入口（Publisher.run 等）在本地验签并检查：

1. token HMAC 验签通过（密钥只在服务端与客户端内置，改配置伪造不了）；
2. `payload.machine_code` 与本机机器码一致（拷贝到别的电脑无效）；
3. 未过期（`expires_at` 日期当天仍有效）；
4. 至少 1 个平台权限（没买套餐可登录但一切写操作被拦）。

旧版 ECDSA voucher / 设备授权码逻辑已按契约 §10 删除。
测试/CI 可用 `AURORA_SKIP_LICENSE=1` 显式放行。
"""
from __future__ import annotations

import hashlib
import os
import time

from ..license import machine_code as _local_machine_code
from ..licensing import parse_expiry, verify_token

# 测试/CI 用：跳过核心层校验（只允许显式设置）
_ENV_SKIP = "AURORA_SKIP_LICENSE"


class LicenseError(RuntimeError):
    """核心逻辑检测到未授权。"""


def _norm_machine(value: str) -> str:
    return str(value or "").strip().upper().replace("-", "")


def check(cfg) -> tuple[bool, str]:
    """本地校验登录 token：返回 (是否有效, 原因)。"""
    if os.environ.get(_ENV_SKIP) == "1":
        return True, "skip"

    token = str(getattr(cfg, "license_token", "") or "").strip()
    if not token:
        return False, "未登录授权账号：请登录并购买套餐后再开始"

    payload = verify_token(token)
    if not payload:
        return False, "授权 token 无效（验签失败），请重新登录"

    payload_machine = str(payload.get("machine_code") or "")
    try:
        local_machine = _local_machine_code()
    except Exception:
        return False, "无法读取本机机器码，请重启软件后重试"
    if _norm_machine(payload_machine) != _norm_machine(local_machine):
        return False, "授权与本机不匹配（可能被拷贝到其他电脑），请在本机重新登录"

    expires_at = str(payload.get("expires_at") or "")
    if expires_at and time.time() > parse_expiry(expires_at):
        return False, f"授权已过期（{expires_at}），请续费后重新登录"

    # 契约 §10.3：离线宽限 7 天，超过后要求重新联网登录（token 会被刷新）
    token_ts = float(getattr(cfg, "license_token_ts", 0) or 0)
    if token_ts and (time.time() - token_ts) / 86400 > 7:
        return False, "授权超过 7 天未联网校验，请重新登录后继续"

    platforms = payload.get("platforms") or {}
    if not isinstance(platforms, dict) or not any(bool(v) for v in platforms.values()):
        return False, "账号未开通任何平台，请先购买套餐"
    return True, "ok"


def require(cfg) -> None:
    """核心入口调用：不通过直接抛 LicenseError。"""
    ok, why = check(cfg)
    if not ok:
        raise LicenseError(why)


def fingerprint(*parts: str) -> str:
    """轻量指纹：给日志/上报用，不含敏感信息。"""
    try:
        h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
        return h[:16]
    except Exception:
        return ""
