# ICBU 采集箱多商品端到端实测报告（选类目 → 必填属性 → 预演 → op=1 保存 → 回读）

- 生成时间：2026-11-22 01:39–01:52（Asia/Shanghai）
- 店铺：9466975（店小秘 ICBU 采集箱，28 个草稿）
- 工具：`tools/icbu_batch_e2e.py`（默认 dry-run；`--save` 才真实保存；`--post-check` 只读回读）
- 证据 JSON：`docs/icbu-batch-e2e.json`（含 inventory / dry-run / saves+changed_fields / post_check / write_summary / rollback_reference / defects）
- 真实写入范围：**仅 `op=1` 保存草稿，8 个商品**；未调用 op=2/3/5，未删除、未发布、未重试、未改价、未用付费 AI
- 保护商品：**220114598434958580 未改动**（回读与 Lead 的 `docs/alibaba-real-save-verification.json` 一致）

## 一、结论先行

| 环节 | 结果 | 说明 |
|---|---|---|
| 只读体检 28 草稿 | ✅ 完成 | 28/28 详情读取成功；**27 个 categoryId 为空**（采集箱导入未落类目） |
| dry-run 预演（8 个） | ✅ **8/8 通过** | `ok=True、issues=[]、preview=True、saved=False`，未发保存请求 |
| 真实 `op=1` 保存 | ✅ **8/8 成功** | 平台返回 `code=0` + `data.msg="您的产品编辑成功！"`，无失败、无重试 |
| 保存后回读 | ✅ 8/8 一致 | categoryId/fullCid 全部等于计划；priceType/productSku/mainImage 未被意外改变 |
| 商品当前状态 | ✅ 仍在草稿（draft） | 草稿总数仍 28（编辑非新建）；**未提交审核、未发布** |
| 发布链路（op=2/3/5） | ❌ **未验证** | 本轮无授权，报告不把「保存成功」等同于「可发布/审核通过」 |

一句话：**「选类目 → 必填属性补齐 → 本地校验 → op=1 保存 → 回读」这条链路在 5 个泳池 + 1 个电动 ATV + 1 个网袋 + 1 个喷泉喷头上全部打通**；但 8 个商品里有 3 个（ATV/网袋/喷泉）的必填属性是推断/占位值，另有 ATV 标题截断与葡萄牙语关键词问题，**提交审核前必须人工复核**。

## 二、商品清单与逐步结果

| # | product_id | 类型 | 价格模型（保存前后） | 写入类目 / fullCid | dry-run | op=1 保存 | changed_fields（回读 diff） |
|---|---|---|---|---|---|---|---|
| 1 | 220114598434958576 | 集装箱泳池 ladder | ladder_price → ladder_price | 202107201 / `18,100005657,100005663,202107201` | ✅ | ✅ | categoryId, fullCid, keywords, wholesaleTrade, attributes_count(19→20), sku(空→[]) |
| 2 | 220114598434958578 | 集装箱泳池 sku | sku_price → sku_price | 202107201 / `18,100005657,100005663,202107201` | ✅ | ✅ | categoryId, fullCid, keywords, bulkDiscountPrices(→[]), wholesaleTrade |
| 3 | 220114598434958574 | 集装箱泳池 ladder | ladder_price → ladder_price | 202107201 / `18,100005657,100005663,202107201` | ✅ | ✅ | categoryId, fullCid, keywords, wholesaleTrade, attributes_count(5→10), sku(空→[]) |
| 4 | 220114598434958570 | 集装箱泳池 sku | sku_price → sku_price | 202107201 / `18,100005657,100005663,202107201` | ✅ | ✅ | categoryId, fullCid, keywords, bulkDiscountPrices(→[]), wholesaleTrade, attributes_count(4→9) |
| 5 | 220114598434958562 | 集装箱泳池 ladder | ladder_price → ladder_price | 202107201 / `18,100005657,100005663,202107201` | ✅ | ✅ | categoryId, fullCid, keywords, wholesaleTrade, attributes_count(11→13), sku(空→[]) |
| 6 | 220114598434958114 | 电动 ATV（葡萄牙语标题） | sku_price → sku_price | 201139102 / `201275273,127676029,201139102` | ✅ | ✅ | categoryId, fullCid, **subject(147→122 截断)**, keywords, bulkDiscountPrices(→[]), wholesaleTrade, attributes_count(4→14) |
| 7 | 220114598434958546 | 网袋 | sku_price → sku_price | 201730007 / `1524,201709904,201730007` | ✅ | ✅ | categoryId, fullCid, keywords, bulkDiscountPrices(→[]), wholesaleTrade, attributes_count(3→8) |
| 8 | 220114598434958532 | 草坪喷泉喷头 | sku_price → sku_price | 100001819 / `15,125,100001813,100001819` | ✅ | ✅ | categoryId, fullCid, keywords, bulkDiscountPrices(→[]), wholesaleTrade, attributes_count(4→7) |

- 保存耗时 0.37–0.73s/个；保存后 sleep 2s 再回读。
- 平台返回逐条见 `docs/icbu-batch-e2e.json → saves[].result.save_result`（`code=0`、`data.code=0`、`data.productId` 与商品 id 一致）。
- 失败数 0，因此**没有触发「失败分类」流程**；未出现 `code=0 但 msg 失败` 的情况（`is_failure_msg` 未命中）。

## 三、dry-run 阶段有两个重要发现

1. **`overrides` 生效且「校验类目 = 保存类目」**：类目与 fullCid 全部通过 overrides 传入（不改全局 cfg），预演结果 `category_id` 与计划一致，`issues=[]`。
2. **默认 autofill 对无 `Other` 的 single_select 会取 `values[0]`**（缺陷，见第六节）：如果直接用默认配置保存，ATV 会被写成
   `Driving Mileage=≤30km、Transmission System=Shaft Drive、Power=< 500W、Voltage=48 V`，
   网袋 `Material=Felt`。本轮已用 overrides.attributes 显式预设规避，预演后 `_autofilled=[]、_unresolved=[]`（预设值由 `merge_attributes` 写入，不进入 `_autofilled` 列表）。

## 四、回读核对（保存后重新拉详情）

| 核对项 | 结果 | 证据 |
|---|---|---|
| categoryId/fullCid = 计划值 | ✅ 8/8 | `post_check.checks.all_categories_match_plan=true` |
| 仍为 draft、草稿总数不变 | ✅ 28→28 | `post_check.drafts_after.count=28` |
| priceType 保持原模型 | ✅ 8/8 | ladder 仍 ladder（原阶梯价 7751.6/6172.53/5821.65 保留）；sku 仍 sku_price（productSku 哈希未变） |
| productSku 未被改 | ✅ | 不在任何 changed_fields；`productSku_not_in_changed_fields=true` |
| mainImage 未被改 | ✅ | 不在任何 changed_fields；每个商品仍 6 张图 |
| textDesc 未被改 | ✅ | 不在 changed_fields（哈希一致） |
| subject | 7 个未变；ATV 147→122（≤128 保护截断） | `write_summary[].subject` |
| keywords | 全部由空 → 标题提取 5 个（预期行为） | `write_summary[].keywords_written` |
| bulkDiscountPrices | ladder 原值保留；sku 由空串归一为 `[]` | `field-normalization` |
| wholesaleTrade | 重写为规范结构（补 min_order_quantity/unit_type/sale_type），`package_size/weight/inventory` 业务值保留 | `field-normalization` |
| 220114598434958580 | ✅ 未改动，与 Lead 上次保存一致 | `post_check.protected_580.keywords_unchanged_vs_lead_save=true` |

## 五、推断/占位属性清单（⚠️ 提交审核前必须人工复核）

> 这些值是为了打通「必填属性 → 保存」链路而构造的，**不代表已验证的真实规格**。

| product_id | 写入的推断/占位值 | 依据 | 风险 |
|---|---|---|---|
| 220114598434958114（ATV） | Voltage=60 V、Power=> 1000W | 标题「60V 2000W」 | 低，但仍需核对铭牌 |
| 同上 | Driving Mileage=≥90km | 标题「90km de Autonomia」 | 低 |
| 同上 | Maximum Speed=75-100Km/h、Maximum Torque(Nm)=60-80Nm、Driving Type=4Wd、Charge Time(h)=7-9h、Grade Ability=20-25°、Transmission System=Chain Drive、Place of Origin=China | **商品数据无来源，按电动 ATV 常见值推断** | **高：需人工确认，否则等于发布错误规格** |
| 220114598434958546（网袋） | Material=Mesh | 标题 Mesh Bag | 中 |
| 同上 | Style=Other、Logo/Pattern=Other、Color=Other、Place of Origin=China | 无来源占位 | 中：Other 属占位值 |
| 220114598434958532（喷泉喷头） | Material=Plastic、Color=Other、Place of Origin=China | 品类常见值/占位 | 中 |
| 220114598434958574 / 570（泳池） | Place of Origin=China、Color=Other、Material=Other、Applicable People=All、Type=Above Ground Pools | 原商品缺这 5 个必填项，全部占位/推断 | 中高：Material/Color 为 Other |
| 220114598434958576 / 562（泳池） | 仅补齐缺失项（576 Color=Other；562 Color=Other、Applicable People=All） | 占位 | 低-中 |

商品原有属性一律保留，预设只补必填缺口；每个商品实际写了哪些预设见 `write_summary[].attrs_written_from_presets`，原有属性覆盖情况见 `attrs_preserved_existing`。

## 六、缺陷 / 数据问题清单

| 级别 | 问题 | 定位 | 证据 |
|---|---|---|---|
| **P0-数据** | 28 个草稿中 27 个 `categoryId` 为空/None，采集箱导入未落类目；直接保存会被本地校验拦截。本轮补齐 8 个，**仍有 19 个缺类目** | 数据问题（校验逻辑 `src/aeclone/services/alibaba_payload.py:523-535` 工作正常） | `inventory.empty_category_ids`、`post_check.drafts_after.empty_category_count=19` |
| **P1-代码** | `autofill_required_attributes` 对缺失必填属性、且候选值里没有 Other/China 等通用值时直接取 `values[0]`：ATV 会被填成 48V/<500W/≤30km/Shaft Drive，网袋 Material 填成 Felt。批量保存前必须提供类目属性预设 | `src/aeclone/services/alibaba_payload.py:450-459` | `defects[autofill-first-value].evidence`（含 8 个商品的默认 autofill 结果） |
| **P1-数据** | 全部草稿原 `keywords` 为空，保存时由标题自动提取 5 个词；非英文标题（ATV 葡萄牙语）产出葡萄牙语关键词，英文站需人工替换 | `src/aeclone/services/alibaba_pipeline.py:358-375` | `defects[keywords-from-title]` |
| **P2-代码** | 无 `productSku` 的 ladder 商品保存后 `productSku` 变成 `[]`（present-but-empty，`build_save_dict` 把 None 序列化为 `"[]"`）。ladder_price 校验不拦，但 SKU 编辑器会看到空 SKU 数据 | `src/aeclone/integrations/alibaba.py:223` | `defects[productsku-empty-array]`（576/574/562） |
| **P2-数据/预期** | subject 超 128 时按词边界截断（ATV 147→122），属保护行为但语义被截断；发布前需人工确认标题可读性 | `src/aeclone/services/alibaba_pipeline.py:126-133` | `defects[subject-truncated]` |
| **P3-预期归一化** | sku_price 商品 `bulkDiscountPrices` 由空串归一为 `[]`；`wholesaleTrade` 重写为规范结构（业务值保留） | `src/aeclone/services/alibaba_payload.py:336-390` | `defects[field-normalization]` |
| 数据风险（未改价） | 采集价疑似异常：ATV 6.7、网袋 6.16、喷泉 11.54；本工具未改任何价格，发布前需核对 | 采集源数据 | 本轮只读探针输出（未写入 JSON，报告备注） |

## 七、本次改动数据摘要 + 回滚参考

每个商品「写入前 → 写入后」的完整字段见 `docs/icbu-batch-e2e.json → rollback_reference` 与 `write_summary`。摘要：

| product_id | 类目 | 关键词（写入） | 标题 | 属性 |
|---|---|---|---|---|
| 220114598434958576 | 空 → 202107201 | 空 → `large, size, prefabricated, container, swimming` | 不变(115) | 19→20（补 Color=Other） |
| 220114598434958578 | 空 → 202107201 | 空 → `container, swimming, pool, prefab, above` | 不变(120) | 19→19（原属性已覆盖全部必填） |
| 220114598434958574 | 空 → 202107201 | 空 → `high-end, luxury, container, swimming, pool` | 不变(101) | 5→10（补 5 项占位） |
| 220114598434958570 | 空 → 202107201 | 空 → `container, pool, luxury, outdoor, swimming` | 不变(113) | 4→9（补 5 项占位） |
| 220114598434958562 | 空 → 202107201 | 空 → `sinoy, portable, prefab, container, pools` | 不变(127) | 11→13 |
| 220114598434958114 | 空 → 201139102 | 空 → `quadriciclo, off-road, sem, escovas, para` | 147→122（截断） | 4→14（10 项推断/占位） |
| 220114598434958546 | 空 → 201730007 | 空 → `outdoor, swimming, pool, large, capacity` | 不变(121) | 3→8（5 项推断/占位） |
| 220114598434958532 | 空 → 100001819 | 空 → `dual, spray, water, fountain, adjustable` | 不变(101) | 4→7（3 项推断/占位） |

回滚要点：把 `rollback_reference[pid].before.categoryId`（空）与 `before.keywords`（空）写回即可回到采集箱原始状态；`priceType / bulkDiscountPrices（ladder）/ productSku（sku 商品）/ mainImage / textDesc` 均未被本工具修改。

## 八、复现命令

```bash
# 只读体检 + dry-run（不保存任何商品，进程内强制 AURORA_NO_SAVE=1）
.venv/bin/python tools/icbu_batch_e2e.py

# 真实 op=1 保存（用户授权范围：最多 8 个；禁止 580；先 unset AURORA_NO_SAVE/AURORA_TEST）
.venv/bin/python tools/icbu_batch_e2e.py --save

# 只读回读刷新报告（不保存；生成 post_check/write_summary/rollback_reference/defects）
.venv/bin/python tools/icbu_batch_e2e.py --post-check \
  --product-ids 220114598434958576,220114598434958578,220114598434958574,220114598434958570,220114598434958562,220114598434958114,220114598434958546,220114598434958532

# 给剩下 19 个缺类目草稿做批量补齐（用 --plan-json 提供类目/属性预设，先跑默认 dry-run 再 --save）
.venv/bin/python tools/icbu_batch_e2e.py --product-ids <id1,id2,...> --plan-json /tmp/icbu_plan.json
```

工具内建硬约束：`--save` 时只允许 op=1、最多 8 个、拒绝 220114598434958580、检测到 `AURORA_NO_SAVE/AURORA_TEST` 直接拒绝；dry-run 阶段用只读哨兵客户端把 `save_product` 变成会抛错的断言；保存前必须走 `run_alibaba_product` 的 validate + 授权 guard（本轮 `授权校验: 通过`）。

## 九、建议（按优先级）

1. **先人工复核 3 个非泳池商品的推断属性**（ATV 10 项、网袋 5 项、喷泉 3 项），确认后再考虑提交审核；不要把本轮保存成功当作可发布。
2. **给 19 个仍缺类目的泳池草稿批量补 202107201**：建议在同一工具上加 `--plan-json` 批量跑 dry-run → 人工抽查 → `--save`；注意默认 autofill 会乱填 Material/Color，需按类目给 `alibaba_attr_presets`。
3. **ATV 标题与关键词语种**：标题 147 字符被截到 122，关键词为葡萄牙语；发布前应改成英文标题/关键词（这属于 AI 文案流水线职责，非保存链路）。
4. **修 P1 autofill 缺陷**：`alibaba_payload.py:450-459` 在无通用值时应优先使用类目属性预设，或标记 `unresolved` 交给人工，而不是静默取第一个值。
5. **P2 productSku `[]`**：`build_save_dict` 对缺失 productSku 保留空串而非 `"[]"`，避免「空 SKU 数据」污染。
6. **发布/审核链路单独授权测试**：本轮只验证 op=1；op=2/3/5、运费模板、海关属性、违禁词阻断等仍未实测。
