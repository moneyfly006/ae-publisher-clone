"""Select isolated platform automation settings for each shop/site context."""
from copy import deepcopy
import json

from .collect_store import scope


def keys(cfg, platform):
    return [key for key in cfg.as_dict() if key.startswith(platform + "_collect_") and key != platform + "_collect_profiles"
            or key.startswith(platform + "_") and (key.endswith("_prompt") or key.endswith("_prompt_style"))]


def sync(app, platform):
    if platform not in ("alibaba", "amazon", "tiktok", "shopee"):
        return
    cfg = app.cfg
    now = scope(cfg, platform)
    marker = platform + "_collect_scope"
    previous = cfg.get(marker, "")
    if previous == now:
        return
    profile_key = platform + "_collect_profiles"
    raw = cfg.get(profile_key, "{}")
    profiles = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
    if not isinstance(profiles, dict):
        raise ValueError("店铺预设格式损坏，请先恢复配置备份")
    selected_keys = [key for key in keys(cfg, platform) if key != marker]
    if previous:
        target = profiles.get(now)
        # 只有上一个范围真实保存过预设时，才做「保存旧 / 恢复新」；
        # 否则只是页面初始化自动带出站点导致 scope 变化，不能把当前设置重置掉。
        _now_shop = str(getattr(cfg, platform + "_shop_id", "") or "")
        # 上一个范围未保存过预设时，只有切换到有店铺的范围（显式绑定店铺）
        # 才保存/重置；仅自动带出站点的情况保留当前设置。
        if previous in profiles or _now_shop:
            profiles[previous] = {key: deepcopy(cfg.get(key)) for key in selected_keys}
            from ..config import DEFAULTS
            for key in selected_keys:
                cfg.set(key, deepcopy(target.get(key, DEFAULTS.get(key, "")) if target else DEFAULTS.get(key, "")))
            if hasattr(app, "_collect_errors"):
                app._collect_errors.pop(platform, None)
            if hasattr(app, "_collect_results"):
                app._collect_results.pop(platform, None)
    cfg.set(marker, now)
    cfg.set(profile_key, json.dumps(profiles, ensure_ascii=False))
    from ..config import save_config
    save_config(cfg)


def label(cfg, platform):
    return " · ".join((str(getattr(cfg, platform + "_shop_name", "") or getattr(cfg, platform + "_shop_id", "") or "未绑定店铺"),
                       str(getattr(cfg, platform + "_site", "") or "默认站点"),
                       str(getattr(cfg, platform + "_mode", "") or ""))).strip(" ·")
