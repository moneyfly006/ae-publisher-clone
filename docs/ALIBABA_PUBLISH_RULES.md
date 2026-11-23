# Alibaba 发布/审核硬规则（2026-11-22 真实账号实测）

> 账号：店小秘 ICBU shop 9466975；全部结论来自真实 `add.json(op=2)` 返回与 `edit.json` 回读。
> 这些规则不满足时，`op=1` 可能保存成功，但 `op=2` 发布一定失败——**保存成功 ≠ 审核通过**。

## 1. 发布是异步的：提交成功 ≠ 发布成功

`op=2` 返回 `data.code=0` 只代表「已提交」，平台随后异步校验：

| 阶段 | 可观察现象 | 含义 |
|---|---|---|
| 提交成功 | `data.msg="产品已提交发布…"` | 已进入平台发布队列 |
| 审核中 | `edit.json` 的 `errMsg` 含 `PUB_BIZCHECK_PRODUCT_IN_AUDITING`（您的产品目前正在审核中，请不要重新提交） | **已提交成功**，重复提交会被拒；不能当失败 |
| 发布失败 | `dxmState=offline` + `dxmOfflineState=publishFail` + `errMsg` | 平台校验不通过，按 errMsg 修复后重发 |
| 在线 | `dxmState=online` 且无 `errMsg` | 审核通过 |

代码位置：`alibaba_pipeline.run_alibaba_product` 在 `op=2` 保存后轮询 `edit.json` 并如实返回
`publish_state`；`PRODUCT_IN_AUDITING` 判为 `saved=True, auditing=True`，不再重复提交。

## 2. 经营类目范围（客户必须知道）

真实失败：

```
systemError:isp.system-service-error:PUB_BIZCHECK_CAT_PUB_RESTRICT:
当前商品类目不在线上经营类目范围内，无法发品，请前往店铺管理->管理公司信息完成经营类目设置
```

- 这是**店铺资质限制**，不是软件/参数问题；软件无法代开经营类目。
- 处理：Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → 经营类目，把商品所属大类/末级类目加入经营范围。
- 软件预检（近似）：`services/alibaba_scope.check_category_scope()` 扫描在线商品列表的
  `categoryId` 做样本。目标类目在样本中出现 → 大概率可发；未出现 → 提醒客户先开类目。
  最终结果以平台 `add.json` 返回为准。
- UI：「Alibaba 刊登 → 类目 → 检查类目经营范围」；发布预检与失败提示都会带客户指引。

## 3. textDesc（详情）只收纯文本，且 ≤2000 字符

实测错误（两次）：

```
textDesc:isv.parameter-parse-error:textDesc:不能超过1000个汉字或2000个字符
textDesc:isv.parameter-parse-error:textDesc:不能输入HTML脚本
```

- 在线商品的 `textDesc` 实测全部是**纯文本、无任何标签**。
- 保存时必须：去 HTML 标签/内联样式/script/img → 反转义实体 → 截断 2000 字符。
  代码：`alibaba_payload.text_desc_plain()`；AI 详情提示词也改为「800~1500 字符纯文本，不要 HTML」。
- 评分（`alibaba_score`）对「含 HTML」「>2000 字符」会扣分并给出修复建议。

## 4. 发货期 deliver_periods 必填

失败：`发货期信息未填写`。实测在线商品格式：

```json
"deliver_periods": [{"quantity": 3, "process_period": 15}]
```

- `quantity`：起订量阈值；`process_period`：发货/生产天数。
- 配置：`alibaba_process_period`（天）、`alibaba_deliver_quantity`（默认 1）；
  为空时回退 `alibaba_lead_time`；商品已有合法 `deliver_periods` 时优先保留。

## 5. 重量 / 长宽高 / 物流属性「全填或全清」

失败：`重量、长宽高、物流属性要么全部填写，要么全部清除`。

- 只填 `wholesaleTrade.package_size/weight` 而没有 `logisticsProperty` → 必失败。
- 实测在线商品物流属性示例：`logisticsProperty = "general_cargo_0"`（普货）；
  同时 `shipping_line_template_id` 指向真实运费模板（如 2055583029）。
- 配置：`alibaba_logistics_property`（默认 `general_cargo_0`）、`alibaba_freight_template_id`；
  仅当重量和尺寸都填时才写入 `logisticsProperty`，避免反向违反「全填或全清」。

## 6. 其他已确认规则（沿用）

- `priceType`：`ladder_price` / `sku_price` / `fob_price`，阶梯价 `bulkDiscountPrices`、FOB 走 `sourcingTrade`。
- `subject` ≤128 字符、纯英文；`keywords` 3~5 个英文短语。
- 图片：主图最多 6 张（1:1，建议 ≥1000×1000），详情图走 `detailImage`。
- 视频：`mainVideoId/detailVideoId` 由平台侧上传，保存接口暂不含视频字段；评分会单列提醒。
- 发布门禁：`op=2` 要求评分 ≥4.5（`alibaba_score.PASS_SCORE`），不达标在保存前阻断并列出修复项。
7. **主视频复用限制**：同一主视频被 >20 个商品使用会报
   `CHK_VIDEO_PRODUCT_LINK_LIMIT`。保存接口无视频字段，清空请求字段/先 op=1 再 op=2
   都无法绕过；需在卖家中心给商品更换或删除主视频后重发。
8. **发布失败列表核对**：店小秘「发布失败」= `list.json` 传
   `dxmState=offline & dxmOfflineState=publishFail`；该列表里的 row 带 `errMsg`，
   可直接读出平台拒绝原因。产品在列表里 = 未发布成功。
9. **op=3 / op=5 实测（2026-11-22）**
   - `op=3`（保存并移入待发布）：要求商品在**采集箱**；返回
     `操作成功，请到「待发布产品」中查看！`，状态 `draft → offline/waitPublish`。
     在待发布商品上执行会报「只有处在采集箱中的产品才能执行此操作」。
   - `op=5`（定时发布）：返回 `产品已提交发布，请到「 定时发布」中查看!`，
     状态 `draft → offline/publishingTiming`。
   - 定时时间字段：`alibabaSave.dxmScheduleTime` = 13 位毫秒时间戳（本地时间），
     实测 `2030-01-01 09:00:00 → 1893459600000` 被平台接受并在 `edit.json` 回读；
     不填时间也可提交，平台按默认排期。
