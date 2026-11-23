# 后台日志分析：两台速卖通机器（2026-11-23）

数据来源：license.moneyfly.club SQLite `app_logs` / `usage_events`，只读查询。

## 机器 30266817-636A0D9D（v1.0.5）
- 最近多次批量任务，典型结果：
  - 成功 28 / 失败 152 / 部分保存 14
  - 成功 20 / 失败 130 / 部分保存 13
  - 成功 1 / 失败 50 / 部分保存 1
- 主要失败：
  - `SKU 一致性检查失败：SKU-001: 属性键和值必须是非空标量`
  - `SKU 一致性检查失败：SKU-002: SKU 属性重复`
  - 原因：v1.0.5 没清洗采集 SKU 元数据/空值，重复组合未合并。
- 其他 WARN（不阻断）：
  - `营销图: 上传失败：https://image.moneyfly.club（两种协议都试过了...）`
  - `四合一: cannot identify image file ...Temp...`（临时图片损坏/不可读，已跳过）

## 机器 CE33ED3E-DFFD9BA8（v1.0.5）
- 最近一次：成功 0 / 失败 5。
- 失败：
  1. 4 件 `SKU 一致性检查失败：SKU-001: 属性键和值必须是非空标量`
  2. 1 件 `商品详情/SKU 身份校验失败：处理后的 SKU 无法匹配原商品`
- 同样是 v1.0.5 的 SKU 解析/清洗问题。

## 修复（v1.0.7）
- `product_data.clean_sku_rows`：解析前剔除 skuImage/extraImages/imageUrlList 等元数据、
  None/空串/list/dict；标准属性缺有效 valueId 时剔除该属性；
- `hydrate_product`/`sku_recovery` 使用同一份清洗结果；
- 清洗后重复组合由恢复流程自动合并（保留第一条、库存累加）；
- 新增 `tests/test_sku_clean.py`；
- 离线 1268 测试通过，构建 1.0.7 自检 0。

## 处理建议
1. 两台机器升级到 **1.0.7** 后重试同一批失败商品；
2. 若仍报某个商品，请把该商品 ID 和最新日志发回，按单商品数据修复；
3. 营销图上传失败是 image.moneyfly.club 外部服务/网络问题，不影响商品保存；可稍后重试或检查图床配置。
