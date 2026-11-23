# 去重后的再次复查

本轮检查了四平台自动处理、AI 属性、Shopee 文案、图片异步回填、配置修复及最新实店错误。发现的问题已分组修复，速卖通页面和发布流程未修改。

## 已修复

| 问题 | 原因与处理 |
|---|---|
| Shopee 新版详情被“补齐缺失”意外重写 | `extended` 详情存放在 `descriptionInfoStr`，旧代码检查 `description`。现按实际字段判断，显式编辑优先，已有详情保留；AI 上下文也包含新版详情原文。 |
| AI 属性补齐使用旧类目或忽略已填预设 | 现按本次目标类目查询候选，换类目时不沿用旧类目的属性 ID / Amazon 产品类型；跳过用户显式填写的属性，保留有效原值。 |
| 空属性无法补齐或生成同 ID 的重复项 | 现区分属性项存在与值已填写；空值可补齐，TikTok / Shopee 按原生属性 ID 合并，不追加重复项。 |
| 图片重复执行、与自动任务重叠 | 按平台与商品登记进行中的任务，加锁检查；页面重建、连续确认都不能启动第二个相同任务。确认时再次检查自动任务状态，两个方向均阻止重叠。 |
| 图片结果在切换商品后回填 | 确认前、回填前核对商品、平台、店铺、站点、商品类型与原图列表。发生变化不启动或不回填，并记录原因。 |
| 图床无有效 URL 却计为成功 | 单品图片处理要求 HTTP(S) 地址及主机非空；无效响应报错，原商品图片保留。 |
| 损坏的批量 JSON 被单项设置覆盖 | 单项字段、属性 JSON 编辑均先验证原配置；格式损坏时保留原始内容，要求用显式修复区修正，避免丢失其余设置。 |

原始复现：`build/ui-redesign/recheck-reproduction.log`、`recheck-media-reproduction.log`。修复测试包含四平台目标类目、显式预设、Shopee 原生详情与异步图片等行为，不调用付费 AI。

## 验证

- 全量离线回归：1185 项通过，较上轮新增 17 项。`build/ui-redesign/recheck-full-final.log`。
- 控件回调：60 页 1211 项，0 异常。873 同步、279 后台延后、8 原生对话框、51 外部入口；延后或外部入口不代表真实远程操作成功。`build/ui-redesign/recheck-buttons.json`。
- 设置：494 项配置保存、153 项本地变化，共 647 次，无异常。`docs/ui-options-verification.json`。
- 静态控件审计：无缺失事件、占位点击、未使用布尔项或当前配置冲突。`build/ui-redesign/recheck-controls.log`。
- Shopee 文案页 41（深色）、Amazon 图片页 20（浅色）分别在 1280、1024 宽度实际渲染通过。`build/ui-redesign/visuals/`。
- 安装包签名与 `--verify-local` 自检通过；独立配置下分别启动 Alibaba 30、Amazon 20、TikTok 27、Shopee 41，均无运行错误日志。`build/ui-redesign/native-final/{platform}/result.json`。
- `git diff --check` 通过。

## 仍需真实资料或平台处理

本轮只读重查提供的账号，未保存、发布商品，也未新增付费 AI 调用。

- Alibaba 失败列表 1 件，仍报 `PUB_BIZCHECK_CAT_PUB_RESTRICT`：商品真实类目不在店铺经营范围，需要在卖家中心开通对应经营类目。
- Amazon 失败列表 27 件，其中 24 件仅有 `100521` 审核通知，应等待审核，避免重复提交。其余 3 件仍涉及有效 UPC/EAN/GTIN 或豁免、型录状态、真实 `variation_theme` 等；历史错误中还包含重复词或缺失字段提示。已存草稿不等于发布错误已解除。
- 两个账号均未绑定 TikTok 或 Shopee 店铺，本轮重新读取的店铺数量均为 0，无法进行这两个平台的真实商品发布验收。

最新只读证据：`build/publish-failures-inspection.json`、`build/ui-redesign/recheck-live-summary.json`、`build/ui-redesign/recheck-linked-shops.json`。

## 修复版

`dist/verified-followup-data-media-20261122-165738/AuroraList.app`

可执行文件 SHA256：`c97c23c06b018546b24125f0806c478b115511d4c5e36ad4819c208103c52c59`。

已用正常配置打开修复版，保留其他版本的用户窗口。以上为本轮检查范围内的结果，不将离线通过等同于所有平台实店发布通过。
