"""按平台隔离配置：平台专属键、店铺、模板、Cookie 分开存。

Cookie 的持久化有两条路径，必须一起写：

1. ``config.json``（``save_config``）——用户粘贴/读取后马上落盘的主配置；
2. ``platforms/<platform>.json``——切平台时用来隔离店铺/模板/Cookie 的镜像。

如果只写主配置，重启时 :func:`load` 可能拿旧的平台镜像把新粘贴的 Cookie 覆盖掉
（表现就是「粘贴了 Cookie，重启后又不见了」），所以 :func:`set_cookie` 两边都写。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import config_dir, save_config

_ALIEXPRESS_EXTRAS = {
    "store_id", "store_name", "template_product_id", "template_product_title",
    "template_product_source", "template_source", "template_library",
    "template_snapshot", "template_values", "product_template_id",
    "image_ai_jobs", "detail_local_images",
}

# 每个平台自己的 Cookie 键：粘贴一个平台不会覆盖其他平台。
COOKIE_KEYS: dict[str, str] = {
    "alibaba": "alibaba_cookies",
    "aliexpress": "aliexpress_cookies",
    "amazon": "amazon_cookies",
    "tiktok": "tiktok_cookies",
    "shopee": "shopee_cookies",
}
_COOKIE_KEY_SET = frozenset(COOKIE_KEYS.values())


def _safe_pid(pid: str) -> str:
    return "".join(c for c in str(pid or "") if c.isalnum() or c in "-_") or "unknown"


def cookie_key(pid: str) -> str:
    """平台 → 该平台专属 Cookie 配置键（未知平台也保持「一平台一键」）。"""
    pid = str(pid or "").strip()
    if pid in COOKIE_KEYS:
        return COOKIE_KEYS[pid]
    if not pid:
        return "manual_cookies"
    return f"{_safe_pid(pid)}_cookies"


def _dir() -> Path:
    p = config_dir() / "platforms"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(pid: str) -> Path:
    return _dir() / f"{_safe_pid(pid)}.json"


def _keys(cfg, pid: str) -> dict:
    out = {}
    prefix = str(pid or "") + "_"
    try:
        data = cfg.as_dict()
    except Exception:
        data = {}
    for k, v in data.items():
        if str(k).startswith(prefix) or (pid == "aliexpress" and k in _ALIEXPRESS_EXTRAS):
            out[k] = v
    return out


def save(cfg, pid: str) -> None:
    try:
        p = _path(pid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_keys(cfg, pid), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def load(cfg, pid: str) -> bool:
    """加载平台镜像配置。

    例外：``config.json`` 里已经有非空 Cookie 时不再用镜像覆盖它。
    Cookie 的第一写入点是 ``config.json``（粘贴/读取后立即保存），镜像文件是
    切平台时才顺手写的；用镜像覆盖会让新粘贴的 Cookie 在重启后回退成旧的。
    """
    try:
        p = _path(pid)
        if not p.is_file():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        for k, v in data.items():
            # 平台镜像里的 Cookie 只作兜底：config.json 已有非空 Cookie 时以主配置为准，
            # 避免重启后回退成旧 Cookie。`_cookies` 后缀兜住将来新增的平台。
            if (k in _COOKIE_KEY_SET or str(k).endswith("_cookies")) \
                    and str(getattr(cfg, k, "") or "").strip():
                continue
            cfg.set(k, v)
        return True
    except Exception:
        return False


def set_cookie(cfg, pid: str, raw) -> str:
    """保存某平台 Cookie（字符串/dict/带前缀都能识别），主配置与平台镜像同步落盘。

    返回规范化后的 ``name=value; ...`` 字符串；解析不出 Cookie 时返回空串
    （调用方据此提示用户而不是静默保存一坨无效文本）。
    """
    from ..integrations.cookie_parse import cookie_header

    key = cookie_key(pid)
    value = cookie_header(raw)
    cfg.set(key, value)
    save_config(cfg)
    if str(pid or "").strip():
        save(cfg, str(pid).strip())
    return value


def migrate_legacy_cookie(cfg) -> None:
    """旧版本只有 manual_cookies：迁移到当前平台专属 Cookie，不再共享。

    旧值可能是 ``{"dxm_i": "...", "dxm_t": "..."}`` 这样的 dict——平台 Cookie 键
    约定存字符串，这里统一序列化成 ``name=value; ...``，否则界面/解析器会把它
    当成无法识别的对象（粘贴 Cookie 后显示「未识别」的另一个来源）。
    """
    try:
        from ..integrations.cookie_parse import cookie_header

        manual = getattr(cfg, "manual_cookies", None)
        if not manual:
            return
        pid = str(getattr(cfg, "active_platform", "") or "aliexpress")
        key = cookie_key(pid)
        value = cookie_header(manual)
        if not value:
            cfg.set("manual_cookies", {})
            save_config(cfg)
            return
        if not str(getattr(cfg, key, "") or "").strip():
            cfg.set(key, value)
            save(cfg, pid)
        cfg.set("manual_cookies", {})
        save_config(cfg)
    except Exception:
        pass
