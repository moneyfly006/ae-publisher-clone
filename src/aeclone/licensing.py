"""平台 + 功能包授权（entitlement）解析与校验 —— 账号制 v3。

授权中心登录成功后下发 token（契约 §6，格式与已装 v1.0.10 客户端一致）：

    {"payload": {"machine_code": "...",
                 "platforms": {"alibaba": ["listing", "ai_text"], "amazon": ["listing"]},
                 "expires_at": "2027-11-17",
                 "feature_expiries": {"alibaba": {"listing": "2027-11-17"}},
                 "issued_at": 1790000000},
     "sig": "hex(HMAC_SHA256(canonical_json(payload), KEY))"}

客户端只用内置 HMAC KEY 本地验签（服务端可随时轮换 KEY），不走网络；
离线宽限 7 天（超过后视作需要重新登录/联网）。旧版设备授权码 / ECDSA voucher /
`license_identity` / legacy 全平台兼容已按契约 §10 删除。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field

# 服务端与客户端共享的签名密钥（与服务端 issue_entitlement_token 一致，不可随意改）
SIGN_KEY = b"aurora-list-platform-license-v1"

# 平台与功能包定义（契约 §1）
PLATFORM_FEATURES = {
    "alibaba": ["listing", "category", "trade", "ai_text", "ai_image", "audit"],
    "aliexpress": ["listing", "category", "pricing", "variants", "ai_text", "ai_image", "audit"],
    "amazon": ["listing", "category", "ai_text", "ai_image", "audit", "fba"],
    "tiktok": ["listing", "ai_text", "ai_image"],
    "shopee": ["listing", "ai_text", "ai_image"],
}
ALL_FEATURES = sorted({f for v in PLATFORM_FEATURES.values() for f in v})

# 离线宽限：token 超过这么多天没刷新就要求重新登录（契约 §10.3）
# 旧客户端风险（1.0.10 / 1.0.6，无法靠新代码修复，只能提示升级）：
#   - 没有心跳 license_rev，也没有解绑/禁用后自动重登；
#   - 后台撤销/解绑/禁用后，旧客户端最多仍可离线使用 7 天（或到下次启动/刷新）；
#   - 需要用户升级到 1.0.11+ 才能获得即时失效 + 自动重新登录绑定。
OFFLINE_GRACE_DAYS = 7

# 服务端明确失效（刷新时检测到）→ 本地 token 被清空，需要重新登录以重新绑定本机
SOURCE_DEVICE_UNBOUND = "device-unbound"        # 后台解除本机设备绑定
SOURCE_ACCOUNT_DISABLED = "account-disabled"    # 账号被禁用
INVALID_SOURCE_REASONS = {
    SOURCE_DEVICE_UNBOUND: "unbound",
    SOURCE_ACCOUNT_DISABLED: "disabled",
}

# 各失效原因的登录页提示（客户端统一文案）
INVALID_SOURCE_MESSAGES = {
    "unbound": "本设备授权已失效（可能已在后台解绑），请重新登录以重新绑定本机",
    "disabled": "账号已被禁用，请联系客服；如需恢复使用请重新登录",
}


def entitlements_invalid_reason(ent) -> str:
    """返回 '' / 'unbound' / 'disabled'（供 UI 决定是否弹重新登录）。"""
    try:
        return INVALID_SOURCE_REASONS.get(str(getattr(ent, "source", "") or ""), "")
    except Exception:
        return ""


def invalid_source_message(reason: str) -> str:
    return INVALID_SOURCE_MESSAGES.get(str(reason or ""), "")


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_payload(payload: dict, key: bytes = SIGN_KEY) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def verify_token(token: str | dict, key: bytes = SIGN_KEY) -> dict | None:
    """验签成功返回 payload，否则 None。"""
    try:
        obj = json.loads(token) if isinstance(token, str) else token
        if not isinstance(obj, dict):
            return None
        payload = obj.get("payload") or {}
        sig = str(obj.get("sig") or "")
        if not payload or not sig:
            return None
        if not hmac.compare_digest(sign_payload(payload, key), sig):
            return None
        return payload
    except Exception:
        return None


def issue_token(machine_code: str, platforms: dict[str, list[str]],
                expires_at: str, issued_at: int | None = None,
                key: bytes = SIGN_KEY,
                feature_expiries: dict | None = None) -> str:
    """签发 token（授权服务端/测试用）。"""
    payload = {"machine_code": str(machine_code),
               "platforms": {k: sorted(set(v)) for k, v in platforms.items()},
               "expires_at": str(expires_at),
               "issued_at": int(issued_at or time.time())}
    if feature_expiries:
        payload["feature_expiries"] = feature_expiries
    return json.dumps({"payload": payload, "sig": sign_payload(payload, key)},
                      ensure_ascii=False)


def parse_expiry(s: str) -> float:
    """把 YYYY-MM-DD（或带时间）解析成时间戳；日期型权限当天有效到 23:59:59。"""
    raw = str(s or "").strip()
    if not raw:
        return 0.0
    try:
        text = raw[:10] + " 23:59:59"
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _normalize_machine(value: str) -> str:
    return str(value or "").strip().upper().replace("-", "")


def _machine_code() -> str:
    from .license import machine_code
    return machine_code()


@dataclass
class Entitlements:
    platforms: dict[str, set[str]] = field(default_factory=dict)
    expires_at: str = ""
    machine_code: str = ""
    # override / token / token-offline-expired / machine-mismatch / invalid / none
    # device-unbound / account-disabled（刷新时服务端明确失效，本地 token 已清）
    source: str = "none"
    expired: bool = False
    platform_expiries: dict[str, str] = field(default_factory=dict)
    issued_at: int = 0

    def has_platform(self, platform_id: str) -> bool:
        return platform_id in self.platforms and not self.expired

    def has_feature(self, platform_id: str, feature: str) -> bool:
        return self.has_platform(platform_id) and feature in self.platforms[platform_id]

    def platforms_list(self) -> list[str]:
        return [] if self.expired else list(self.platforms)

    def platform_expiry(self, platform_id: str) -> str:
        """该平台（最早到期的功能）到期日；用于账号中心展示。"""
        if platform_id in self.platform_expiries:
            return str(self.platform_expiries.get(platform_id) or "")
        return str(self.expires_at or "")

    def is_empty(self) -> bool:
        return not self.platforms_list()


# 隐藏覆盖 `license_platforms` 只有在显式开发/测试环境下才生效，
# 普通用户即使配置文件里被写入该键也不会绕过服务端授权。
LICENSE_OVERRIDE_ENVS = ("AURORA_LICENSE_OVERRIDE", "AURORA_TEST", "AURORA_NO_SAVE")


def override_enabled() -> bool:
    """隐藏平台覆盖是否允许生效（仅显式开发/测试环境）。"""
    return any(str(os.environ.get(key, "") or "").strip() == "1"
               for key in LICENSE_OVERRIDE_ENVS)


def _override_entitlements(cfg) -> Entitlements | None:
    """测试/调试用平台覆盖（`license_platforms`，隐藏能力，正常登录不使用）。

    仅当 `AURORA_LICENSE_OVERRIDE=1`（或 AURORA_TEST/AURORA_NO_SAVE=1）时生效；
    生产环境下即使 config.json 里有 `license_platforms` 也直接忽略。
    """
    override = str(getattr(cfg, "license_platforms", "") or "").strip()
    if not override or not override_enabled():
        return None
    plats: dict[str, list[str]] = {}
    for part in override.split(","):
        pid, _, feats = part.strip().partition(":")
        if not pid:
            continue
        if feats:
            plats[pid] = [f.strip() for f in feats.split("+") if f.strip()]
        else:
            plats[pid] = list(PLATFORM_FEATURES.get(pid, []))
    return Entitlements(platforms={k: set(v) for k, v in plats.items()},
                        source="override")


def token_payload(cfg) -> dict | None:
    """读取配置里的 token 并验签；失败返回 None。"""
    token = str(getattr(cfg, "license_token", "") or "").strip()
    if not token:
        return None
    return verify_token(token)


def _feature_expiries(payload: dict) -> dict[str, dict[str, str]]:
    raw = payload.get("feature_expiries") or {}
    return raw if isinstance(raw, dict) else {}


def load_entitlements(cfg, now: float | None = None,
                      grace_days: int = OFFLINE_GRACE_DAYS,
                      machine: str | None = None) -> Entitlements:
    """只认登录 token（+ 隐藏的测试 override），解析平台/功能包权限。

    `machine` 传本机机器码；不传则现算（UI 会缓存后传入，避免重复读硬件）。
    机器码不匹配 / 验签失败 / 离线超过宽限 / 到期 → 返回锁定状态（无平台权限）。
    """
    now = now or time.time()
    override = _override_entitlements(cfg)
    if override is not None:
        return override

    token = str(getattr(cfg, "license_token", "") or "").strip()
    if not token:
        return Entitlements(source="none", expired=True)
    payload = verify_token(token)
    if not payload:
        return Entitlements(source="invalid", expired=True)

    payload_machine = str(payload.get("machine_code") or "")
    expires_at = str(payload.get("expires_at") or "")
    base = Entitlements(expires_at=expires_at, machine_code=payload_machine,
                        issued_at=int(payload.get("issued_at") or 0))

    if machine is None:
        machine = _machine_code()
    if _normalize_machine(payload_machine) != _normalize_machine(machine):
        base.source = "machine-mismatch"
        base.expired = True
        return base

    cached_ts = float(getattr(cfg, "license_token_ts", 0) or 0)
    if grace_days is not None and cached_ts:
        offline_days = (now - cached_ts) / 86400
        if offline_days > grace_days:
            base.source = "token-offline-expired"
            base.expired = True
            return base

    raw_platforms = payload.get("platforms") or {}
    if not isinstance(raw_platforms, dict):
        raw_platforms = {}
    feature_dates = _feature_expiries(payload)
    platforms: dict[str, set[str]] = {}
    platform_expiries: dict[str, str] = {}
    if feature_dates:
        for pid, features in raw_platforms.items():
            kept, dates = set(), []
            for feature in (features or []):
                day = str((feature_dates.get(pid) or {}).get(feature) or "")
                if day and now <= parse_expiry(day):
                    kept.add(feature)
                    dates.append(day)
                elif not day and now <= parse_expiry(expires_at):
                    kept.add(feature)
            if kept:
                platforms[str(pid)] = kept
                platform_expiries[str(pid)] = min(dates) if dates else expires_at
        expired = bool(raw_platforms) and not platforms
    else:
        platforms = {str(pid): set(v or []) for pid, v in raw_platforms.items() if v}
        platform_expiries = {pid: expires_at for pid in platforms}
        expired = bool(expires_at) and now > parse_expiry(expires_at)

    return Entitlements(platforms=platforms, expires_at=expires_at,
                        machine_code=payload_machine, source="token",
                        expired=expired, platform_expiries=platform_expiries,
                        issued_at=int(payload.get("issued_at") or 0))


def apply_license_token(cfg, token: str, save=None,
                        machine: str | None = None) -> Entitlements:
    """保存 token 并刷新时间戳；验签失败不写入。"""
    payload = verify_token(token)
    if not payload:
        raise ValueError("授权 token 验签失败")
    cfg.set("license_token", str(token))
    cfg.set("license_token_ts", int(time.time()))
    if save:
        save(cfg)
    return load_entitlements(cfg, machine=machine)


def silent_login_allowed(cfg, machine: str | None = None,
                         now: float | None = None,
                         grace_days: int = OFFLINE_GRACE_DAYS) -> bool:
    """启动时是否可静默直登：开启自动登录 + 本地 token 验签通过 + 机器码匹配 + 未过期。

    只读本地 token，不联网（契约 §10.1）。
    """
    try:
        if not bool(getattr(cfg, "license_auto_login", True)):
            return False
        if not str(getattr(cfg, "license_account_email", "") or "").strip():
            return False
        ent = load_entitlements(cfg, now=now, grace_days=grace_days, machine=machine)
        return ent.source == "token" and not ent.expired
    except Exception:
        return False
