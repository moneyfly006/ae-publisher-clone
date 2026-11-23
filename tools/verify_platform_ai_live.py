"""At most four paid text calls: exercise each platform prompt parser, never save."""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import AppConfig, get_config
from aeclone.services.collect_automation import isolated_config
from aeclone.services.collect_ai import generate_copy


def main():
    detail = json.loads((ROOT / "build/amazon-collect-before.json").read_text())
    source = detail.get("product", detail)
    text = str(source.get("description") or "")
    title = str(source.get("title") or "")
    products = {
        "amazon": source,
        "alibaba": dict(id="ai-readonly", subject=title, textDesc=text),
        "tiktok": dict(id="ai-readonly", productName=title, description=text, site="GB"),
        "shopee": dict(id="ai-readonly", itemName=title, description=text),
    }
    reports = []
    from aeclone.api import ApiClient
    from unittest.mock import patch
    original = ApiClient.chat
    for platform, product in products.items():
        cfg = isolated_config(AppConfig(get_config().as_dict()), platform)
        cfg.set("timeout", 60)
        cfg.set(platform + "_collect_ai_mode", "rewrite")
        cfg.set(platform + "_collect_ai_fields", '["bullets"]' if platform == "amazon" else '["title"]')
        if platform == "tiktok":
            cfg.set("tiktok_site", "GB")
        calls = []
        def tracked(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)
        start = time.monotonic()
        try:
            with patch.object(ApiClient, "chat", tracked):
                result = generate_copy(platform, product, cfg, {})
            row = dict(platform=platform, ok=bool(result), calls=len(calls),
                fields=list(result), counts={k: len(v) for k, v in result.items()},
                seconds=round(time.monotonic() - start, 2), saved=False)
            if platform == "amazon":
                row["five_bullets_one_call"] = len(result.get("bullets", [])) == 5 and len(calls) == 1
                row["ok"] = row["ok"] and row["five_bullets_one_call"]
            (ROOT / "build" / (platform + "-verified-ai-copy.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as exc:
            row = dict(platform=platform, ok=False, calls=len(calls), error=str(exc), saved=False)
        reports.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "build/platform-ai-live-verification.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
