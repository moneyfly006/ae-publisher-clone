"""升级/重启不得覆盖用户自定义配置（AI 提示词、Cookie、店铺）。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    with tempfile.TemporaryDirectory(prefix="aurora-upgrade-") as td:
        os.environ["AURORA_CONFIG_DIR"] = td
        custom = {
            "title_prompt": "我的自定义标题提示词：只写事实",
            "detail_prompt": "你是一名专业工业设备外贸营销专家。在保持原有文字基础之上写详情",
            "alibaba_cookies": "dxm_i=ALI-CUSTOM; dxm_t=1",
            "aliexpress_cookies": "dxm_i=AE-CUSTOM; dxm_t=2",
            "amazon_cookies": "dxm_i=AMZ-CUSTOM; dxm_t=3",
            "store_id": "custom-shop-123",
            "alibaba_ai_preset": "machinery_industrial",
        }
        p = Path(td) / "config.json"
        p.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
        cfg = AppConfig.load()
        check("自定义标题提示词保留", cfg.title_prompt == custom["title_prompt"], cfg.title_prompt)
        check("自定义详情提示词不被旧默认迁移",
              cfg.detail_prompt == custom["detail_prompt"], cfg.detail_prompt)
        check("阿里 Cookie 保留", cfg.alibaba_cookies == custom["alibaba_cookies"])
        check("速卖通 Cookie 保留", cfg.aliexpress_cookies == custom["aliexpress_cookies"])
        check("Amazon Cookie 保留", cfg.amazon_cookies == custom["amazon_cookies"])
        check("店铺设置保留", str(cfg.store_id) == "custom-shop-123", cfg.store_id)
        cfg.set("title_prompt", "升级后我自己改的")
        cfg.save()
        again = AppConfig.load()
        check("保存后不被默认值覆盖", again.title_prompt == "升级后我自己改的", again.title_prompt)
        raw = json.loads(p.read_text(encoding="utf-8"))
        check("写盘仍保留三平台各自 Cookie",
              raw.get("aliexpress_cookies") == custom["aliexpress_cookies"]
              and raw.get("amazon_cookies") == custom["amazon_cookies"])
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
