"""模板产品选择器搜索：标题、商品 ID、待发布状态、全部状态。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.dianxiaomi import DianxiaomiClient  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def client_with_rows(rows):
    c = DianxiaomiClient({})
    c._post = lambda *a, **k: {"data": {"page": {"list": []}}}
    c.product_page = lambda *a, **k: (rows, {"total_pages": 1})
    return c




def test_products_by_ids_cross_status():
    c = DianxiaomiClient({})
    rows = {"draft": [{"id": "1", "title": "draft one"}],
            "editingRequired": [{"id": "2", "title": "wait two"}]}
    c.product_page = lambda page, size, shop, st: (rows.get(st, []), {"total_pages": 1})
    c.product_by_id = lambda pid: {"id": pid, "title": "detail"} if pid == "3" else None
    out = c.products_by_ids(["1", "2", "3"], shop_id="1")
    ids = {x["id"] for x in out}
    check("跨状态按 ID 取商品", ids == {"1", "2", "3"}, ids)


def main():
    print("=" * 60)
    # 本地 ID 命中
    c = client_with_rows([{"id": "220114600917618100", "title": "Online Product"}])
    hits = c.search_products("220114600917618100", shop_id="1", status="onSelling")
    check("本地按商品 ID 命中", len(hits) == 1 and hits[0]["id"] == "220114600917618100", hits)

    # 标题命中
    c = client_with_rows([{"id": "2", "title": "Yugong HDD Machine"}])
    hits = c.search_products("Yugong", shop_id="1", status="onSelling")
    check("本地按标题命中", len(hits) == 1, hits)

    # 本地列表没有时按 ID 查详情
    c = client_with_rows([])
    c._get = lambda path, params=None: {"data": {"product": {
        "id": "333", "subject": "Detail Product", "imageURLs": ""}}}
    hits = c.search_products("333", shop_id="1", status="editingRequired")
    check("列表无结果时按 ID 查详情", len(hits) == 1 and hits[0]["id"] == "333", hits)

    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    test_products_by_ids_cross_status()
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
