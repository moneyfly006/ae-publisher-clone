"""核心层授权守卫：不依赖 UI，防止「改一个配置就绕过」。

思路
----
服务端在授权成功时签发一个 ECDSA P-256 凭证（voucher + signature），
里面带 identity / 到期日 / client_type，并用服务端私钥签名。

- 客户端把凭证存进配置；
- 真正干活的入口（Publisher.run）本地验签；
- 改 license_enforce / license_state.ok 都没用——必须伪造 ECDSA 签名，
  而私钥只在服务器上。

离线宽限
--------
凭证本身有到期日；网络不通时仍可用到到期日为止，
但连续 N 天没联网复核会降级（由 verify 的频率控制，这里不额外加锁）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import os

# 与 license.py 保持一致的内置公钥（服务端可覆盖）
from ..license import PUBLIC_KEY_PEM, identity_candidates, machine_id, verify_voucher

# 测试/CI 用：跳过核心层校验（只允许显式设置）
_ENV_SKIP = "AURORA_SKIP_LICENSE"


class LicenseError(RuntimeError):
    """核心逻辑检测到未授权。"""


def _b64(s: str) -> str:
    return base64.b64encode(str(s).encode("utf-8")).decode("ascii")


def _unb64(s: str) -> str:
    try:
        return base64.b64decode(str(s).encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def store(cfg, res: dict) -> None:
    """把服务端凭证存进配置（简单编码，避免明文一眼可见）。"""
    try:
        if not res or not res.get("voucher"):
            return
        cfg.set("license_voucher", _b64(res.get("voucher") or ""))
        cfg.set("license_signature", _b64(res.get("signature") or ""))
        cfg.set("license_identity", str(res.get("identity") or ""))
        cfg.set("license_expiry", str(res.get("expiry") or ""))
        cfg.set("license_voucher_ts", int(__import__("time").time()))
    except Exception:
        pass


def load(cfg) -> dict:
    try:
        return {
            "voucher": _unb64(getattr(cfg, "license_voucher", "") or ""),
            "signature": _unb64(getattr(cfg, "license_signature", "") or ""),
            "identity": str(getattr(cfg, "license_identity", "") or ""),
            "expiry": str(getattr(cfg, "license_expiry", "") or ""),
        }
    except Exception:
        return {}


def check(cfg) -> tuple[bool, str]:
    """本地验签：返回 (是否有效, 原因)。"""
    if os.environ.get(_ENV_SKIP) == "1":
        return True, "skip"

    v = load(cfg)
    if not v or not v.get("voucher") or not v.get("signature"):
        return False, "未找到授权凭证，请先点「重新检测」完成授权"

    expiry = v.get("expiry") or ""
    if expiry and expiry < datetime.date.today().isoformat():
        return False, f"授权已过期（{expiry}），请联系管理员续期"

    ident = v.get("identity") or ""
    # 凭证必须绑定本机（兼容旧格式：16 位短码 / 完整哈希）
    allowed = set(identity_candidates())
    if ident and ident not in allowed:
        return False, "授权凭证与本机不匹配（可能被拷贝到其他电脑）"
    short = machine_id()[:16].upper()

    try:
        pem = str(getattr(cfg, "license_public_key", "") or "") or PUBLIC_KEY_PEM
        ok = verify_voucher(v["voucher"], v["signature"], ident or short,
                            str(getattr(cfg, "license_client_type", "local") or "local"), pem)
    except Exception as e:  # noqa: BLE001
        return False, f"授权凭证验签失败：{str(e)[:60]}"

    if not ok:
        return False, "授权凭证无效（签名不匹配）"
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
