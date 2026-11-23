"""已选商品 ID 优先于关键词/数量限制，只处理选中商品。"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig  # noqa: E402
from aeclone.services.collect_automation import run_collect  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    rows = [{"id": "1", "title": "A"}, {"id": "2", "title": "B"}, {"id": "3", "title": "C"}]
    cfg = AppConfig(dict(amazon_shop_id="s", amazon_site="X",
                         amazon_collect_selected_ids='["2"]',
                         amazon_collect_keyword="A", amazon_collect_limit=1,
                         amazon_collect_interval=0))
    calls = []

    class Client:
        def product_detail(self, pid):
            return {"product": {"id": pid, "dxmState": "draft"}}

    with patch("aeclone.services.collect_automation.snapshot", return_value=list(rows)), \
         patch("aeclone.services.collect_automation.process_one",
               side_effect=lambda *a, **k: calls.append(a[1]) or SimpleNamespace(ok=True, saved=True, error="")):
        run_collect(cfg, "amazon", rows=None, client=Client(), dry_run=False)
    check("已选 ID 覆盖关键词和数量限制", calls == ["2"], calls)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
