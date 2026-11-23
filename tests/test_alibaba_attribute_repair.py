"""Alibaba AI 自动修复：允许替换已有 Other 占位属性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.alibaba_payload import build_alibaba_save  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    product = {
        "id": "1", "subject": "Above Ground Pool", "keywords": "pool, swimming pool, outdoor",
        "attributes": [
            {"attribute_id": "1", "attribute_name": "Type", "value_id": "4", "value_name": "Other"},
            {"attribute_id": "2", "attribute_name": "Color", "value_id": "4", "value_name": "Other"},
        ],
    }
    defs = [
        {"nameEn": "Type", "required": 1, "values": [
            {"id": "190089526", "name_en": "Above Ground Pools"},
            {"id": "4", "name_en": "Other"}]},
        {"nameEn": "Color", "required": 1, "values": [
            {"id": "123085", "name_en": "Blue"}, {"id": "4", "name_en": "Other"}]},
    ]
    save = build_alibaba_save(product, None, {
        "replace_attributes": {"Type": "Above Ground Pools", "Color": "Blue"}},
        attr_defs=defs)
    import json
    attrs = {a.get("attribute_name"): (a.get("value_name"), a.get("value_id"))
             for a in json.loads(save.get("attributes") or "[]")}
    check("Type 已替换 Other", attrs.get("Type") == ("Above Ground Pools", "190089526"), attrs)
    check("Color 已替换 Other", attrs.get("Color") == ("Blue", "123085"), attrs)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
