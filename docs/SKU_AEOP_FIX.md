# 速卖通 SKU 一致性问题修复（2026-11-22）

## 报错

```
✗ 编辑失败：220114600917618096 Yugong Bolehole Drilling Machine ...
SKU 一致性检查失败：SKU-001: 属性键和值必须是非空标量
```

## 根因

该商品（速卖通采集数据）的 `aeopAeProductSKUs[].aeopSKUProperty` 是「命名属性」形态：

```json
{"Model Number":"YG-150","Power (W)":"60KW(80.46hp)","skuImage":null,"extraImages":""}
```

旧解析器按标准形态读取 `skuPropertyId/propertyValueId`，把 `skuImage=null`、`extraImages=""`
当成了规格属性值 → `_token(None)` 抛「属性键和值必须是非空标量」，整个商品编辑被中止。

## 修复

`services/variants.py::_dimensions`：

- 标准形态（含 `skuPropertyId/propertyValueId`）保持严格校验，缺值仍报错。
- 命名形态（无这两个键）按属性名取值，忽略元数据键：
  `skuImage/extraImages/image/img/skuPropertyName/propertyValueName/sku/skuId`
  以及 `None`/空字符串值。
- 真实商品 220114600917618096 的 SKU 解析后 **0 问题**。

## 验证

- 新增回归：`tests/test_variants_regression.py::test_aeop_named_property_metadata_is_ignored`
  - 命名形态 + 元数据 → 无问题；
  - 标准形态缺 propertyValueId → 仍报错（不放过真缺参）。
- 真实商品 SKU 回放：`docs/variant-aeop-fix-verification.json` → `PASS`。
- 相关测试：variants / integration / Alibaba 系列全部通过。
