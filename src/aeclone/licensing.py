"""平台 + 功能包授权（entitlement）解析与校验。

授权网站下发的 token 形如：

    {"payload": {"machine_code": "...", "expires_at": "2027-11-17",
                 "platforms": {"alibaba": ["listing", "ai_text"], "aliexpress": ["listing"]},
                 "issued_at": 1795270000},
     "sig": "hex(HMAC_SHA256(canonical_json(payload), KEY))"}

客户端内置公钥不可用（仅标准库），这里用 HMAC + 内置密钥做防篡改，
服务端可随时轮换 KEY。离线时使用缓存 token，超过 grace_days 才失效。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

# 服务端与客户端共享的签名密钥（发布时通过 config.license_public_key 覆盖亦可）
SIGN_KEY = b"aurora-list-platform-license-v1"

# 平台与功能包定义
PLATFORM_FEATURES = {
    "alibaba": ["listing", "category", "trade", "ai_text", "ai_image", "audit"],
    "aliexpress": ["listing", "category", "pricing", "variants", "ai_text", "ai_image", "audit"],
    "amazon": ["listing", "category", "ai_text", "ai_image", "audit", "fba"],
    "tiktok": ["listing", "ai_text", "ai_image"],
    "shopee": ["listing", "ai_text", "ai_image"],
}
ALL_FEATURES = sorted({f for v in PLATFORM_FEATURES.values() for f in v})


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_payload(payload: dict, key: bytes = SIGN_KEY) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def verify_token(token: str | dict, key: bytes = SIGN_KEY) -> dict | None:
    """验签成功返回 payload，否则 None。"""
    try:
        obj = json.loads(token) if isinstance(token, str) else token
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
                key: bytes = SIGN_KEY) -> str:
    """签发 token（授权网站/测试用）。"""
    payload = {"machine_code": str(machine_code),
               "platforms": {k: sorted(set(v)) for k, v in platforms.items()},
               "expires_at": str(expires_at),
               "issued_at": int(issued_at or time.time())}
    return json.dumps({"payload": payload, "sig": sign_payload(payload, key)},
                      ensure_ascii=False)


def _parse_date(s: str) -> float:
    try:
        # A date-only entitlement remains valid through that local calendar day.
        return time.mktime(time.strptime(str(s).strip()[:10] + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


@dataclass
class Entitlements:
    platforms: dict[str, set[str]] = field(default_factory=dict)
    expires_at: str = ""
    machine_code: str = ""
    source: str = "none"          # token / legacy / override / none
    expired: bool = False

    def has_platform(self, platform_id: str) -> bool:
        return platform_id in self.platforms and not self.expired

    def has_feature(self, platform_id: str, feature: str) -> bool:
        return self.has_platform(platform_id) and feature in self.platforms[platform_id]

    def platforms_list(self) -> list[str]:
        return [] if self.expired else list(self.platforms)


def _legacy_migration_allowed(cfg) -> bool:
    """旧设备授权客户在迁移期临时开通全部平台。

    账号绑定后仍保留该标记，保证迁移客户不被账号套餐立刻降权；
    管理员可通过清空 license_legacy_all / 重新下发 token 回收。
    """
    try:
        if bool(getattr(cfg, "license_legacy_all", False)):
            return True
        if bool(getattr(cfg, "license_account_bound", False)):
            return True
        if str(getattr(cfg, "license_identity", "") or "").strip():
            return True
        return (bool(getattr(cfg, "license_cache_ok", False))
                and getattr(cfg, "license_cache_legacy", True) is not False)
    except Exception:
        return False


def _legacy_all_platforms(cfg) -> dict[str, list[str]]:
    """迁移期：旧授权客户临时开通全部平台全部功能。"""
    return {pid: list(feats) for pid, feats in PLATFORM_FEATURES.items()}


def load_entitlements(cfg, now: float | None = None,
                      grace_days: int = 7) -> Entitlements:
    """按优先级解析：手动覆盖 > 签名 token > 旧授权兼容。"""
    now = now or time.time()
    override = str(getattr(cfg, "license_platforms", "") or "").strip()
    if override:
        plats = {}
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

    token = str(getattr(cfg, "license_token", "") or "").strip()
    cached_ts = float(getattr(cfg, "license_token_ts", 0) or 0)
    payload = verify_token(token) if token else None
    if payload:
        # 离线宽限：token 上次校验时间过久则要求重新联网校验
        offline_days = (now - cached_ts) / 86400 if cached_ts else 0
        expired = bool(payload.get("expires_at") and
                       now > _parse_date(payload["expires_at"]))
        legacy_expiry = str(getattr(cfg, "license_cache_expiry", "") or "")
        legacy_valid = ((bool(getattr(cfg, "license_cache_ok", False))
                         and getattr(cfg, "license_cache_legacy", True) is not False
                         and (not legacy_expiry or now <= _parse_date(legacy_expiry)))
                        or _legacy_migration_allowed(cfg))
        if cached_ts and offline_days > grace_days:
            if legacy_valid:
                return Entitlements(platforms={pid: set(fs) for pid, fs in _legacy_all_platforms(cfg).items()},
                                    expires_at=legacy_expiry, source="legacy")
            return Entitlements(expires_at=str(payload.get("expires_at") or ""),
                                machine_code=str(payload.get("machine_code") or ""),
                                source="token-offline-expired", expired=True)
        feature_dates = payload.get("feature_expiries") or {}
        if feature_dates:
            platforms = {
                pid: {f for f in features if now <= _parse_date(feature_dates.get(pid, {}).get(f, ""))}
                for pid, features in (payload.get("platforms") or {}).items()
            }
            platforms = {pid: features for pid, features in platforms.items() if features}
            expired = not bool(platforms)
        else:
            platforms = {pid: set(v) for pid, v in (payload.get("platforms") or {}).items()}
        # A purchased account package must not remove an existing device license.
        if legacy_valid:
            for pid, features in _legacy_all_platforms(cfg).items():
                platforms.setdefault(pid, set()).update(features)
            expired = False
        return Entitlements(platforms=platforms,
                            expires_at=str(payload.get("expires_at") or ""),
                            machine_code=str(payload.get("machine_code") or ""),
                            source="token", expired=expired)

    if _legacy_migration_allowed(cfg):
        return Entitlements(platforms={k: set(v) for k, v in _legacy_all_platforms(cfg).items()},
                            source="legacy-migration")
    return Entitlements(source="none")


def apply_license_token(cfg, token: str, save=None) -> Entitlements:
    """保存 token 并刷新时间戳；验签失败不写入。"""
    payload = verify_token(token)
    if not payload:
        raise ValueError("授权 token 验签失败")
    cfg.set("license_token", str(token))
    cfg.set("license_token_ts", int(time.time()))
    if save:
        save(cfg)
    return load_entitlements(cfg)
