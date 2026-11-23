"""Install verified platform-specific login states and collection defaults locally."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import config_dir, get_config, save_config


def main():
    cfg = get_config()
    path = config_dir() / "config.json"
    before = json.loads(path.read_text()) if path.exists() else {}
    backup = ROOT / "build" / "config-before-collect-preset.json"
    if not backup.exists():
        backup.write_text(json.dumps(before, ensure_ascii=False, indent=2))
        backup.chmod(0o600)
    for platform, cookie_name in (("alibaba", "reference-cookie.txt"), ("amazon", "amazon-reference-cookie.txt")):
        report = json.loads((ROOT / "build" / f"{platform}-collect-live-save.json").read_text())
        if report.get("errors") or not any(r.get("saved") and r.get("ok") for r in report.get("save", [])):
            raise RuntimeError(platform + " 尚未完成真实保存验证，未更新配置")
        cfg.set(platform + "_cookies", (ROOT / "build" / cookie_name).read_text().strip())
        shops = report.get("shops") or []
        if len(shops) == 1:
            cfg.set(platform + "_shop_id", shops[0]["id"])
            cfg.set(platform + "_shop_name", shops[0]["name"])
    for platform in ("alibaba", "amazon", "tiktok", "shopee"):
        for suffix, value in (("limit", 10), ("keyword", ""), ("op", "1"), ("ai", False),
                              ("images", False), ("overrides", "{}")):
            key = platform + "_collect_" + suffix
            # Keep any deliberate setting already made in the new UI.
            if key not in before:
                cfg.set(key, value)
    save_config(cfg)
    print(json.dumps({"configured": ["alibaba", "amazon"], "config": str(path),
        "backup": str(backup), "collection_defaults": "10 items, draft save, optional keyword, preserve original data"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
