"""采集 SKU 清洗：元数据/空值/重复组合不应阻断保存。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.product_data import clean_sku_rows  # noqa: E402
from aeclone.services.variants import check_consistency  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    rows = [
        {"skuCode": "A", "aeopSKUProperty": [
            {"Model Number": "YG-50", "skuImage": None, "extraImages": "",
             "imageUrlList": ["http://x"]}]},
        {"skuCode": "B", "aeopSKUProperty": [
            {"skuPropertyId": "1", "propertyValueId": None},
            {"Model Number": "YG-130"}]},
    ]
    cleaned = clean_sku_rows(rows)
    issues = [i.reason for i in check_consistency([{"sku": r.get("skuCode"),
                                                   "aeopSKUProperty": r.get("aeopSKUProperty")}
                                                  for r in cleaned], [])]
    check("清洗后无属性错误", not issues, issues)
    check("保留命名型号", cleaned[0]["aeopSKUProperty"][0]["Model Number"] == "YG-50", cleaned[0])
    check("移除空标准属性", all("skuPropertyId" not in p for p in cleaned[1]["aeopSKUProperty"]),
          cleaned[1])
    # 清洗后完全相同的组合应由重复校验识别，后续可由 recovery 合并/报清晰原因
    dup = clean_sku_rows([
        {"skuCode": "C", "aeopSKUProperty": [{"Model Number": "X"}]},
        {"skuCode": "D", "aeopSKUProperty": [{"Model Number": "X", "skuImage": None}]},
    ])
    reasons = [i.reason for i in check_consistency(
        [{"sku": r.get("skuCode"), "aeopSKUProperty": r.get("aeopSKUProperty")} for r in dup], [])]
    check("清洗后重复可见", any("重复" in r for r in reasons), reasons)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
