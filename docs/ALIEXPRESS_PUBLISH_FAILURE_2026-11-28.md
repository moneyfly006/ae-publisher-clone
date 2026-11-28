# 速卖通发布失败排查与自动修复（2026-11-28）

> 店铺：某速卖通店铺（店小秘）
> 检查方式：只读调用店小秘接口，扫描「发布失败」状态商品 + 对照类目属性定义；**未在平台提交任何发布**。

## 一、结论

当前店铺「发布失败」共 **16 个商品**，全部集中在两类平台错误：

| 平台错误 | 数量 | 含义 |
|---|---|---|
| `isv.7CHK_CUSTOM_PROP_NAME_DUPLICATE_CAT_PROP` | 10 | 自定义属性名与类目系统属性英文名重复（如 `Model Number` / `Weight` / `Type` / `Material` / `Feature`） |
| `isv.7CHK_BASIC_ONEOF`（`sku::Input value not in candidate list`） | 6 | SKU 规格值不在当前类目的可选值列表中 |

两类错误都是**采集数据遗留**，不是网络/登录/图片问题。

另外抽查具体商品（如 `220114600917617864`）时还发现：
- **必填属性缺失**：类目要求 `Color`、`Ballon Size`（SKU 必填规格）为空，编辑器显示必填缺失；
- **价格缺失**：部分 SKU 的 `skuPrice` 为空，编辑器价格栏为空；
- 采集数据里的旧类目属性值 id（如颜色 Brown）与当前类目候选不一致。

## 二、根因

### 1. 自定义属性与类目系统属性重名（10 个）

- 采集商品（来自 Alibaba 等来源）把 `Model Number`、`Weight`、`Material`、`Type`、
  `Feature` 等**类目系统属性**带着值存成了「自定义属性」；
- 旧逻辑只对「本次补全过的属性」或模板里的系统属性做重名去重；而
  `Model Number` / `Weight` 这类**非必填的类目属性**，或类目里的**自由文本属性**，
  仍会被组装成 `{attrName: "Model Number", attrValue: "BQ"}` 提交，
  平台明确报：「custom property name is the same as the English name of the system
  product attribute → 不允许」。

### 2. SKU 规格值不在类目候选（6 个）

- 采集的 SKU 规格值 id 属于**旧类目/其它类目**（例如颜色 `350686 = Brown` 在当前
  类目 `Color` 候选中不存在），发布校验时平台报 `sku::CHK_BASIC_ONEOF`；
- 部分 SKU 还带着 **插件库规格**（`plug_parent_property_id`：Voltage/Plug 类），
  而当前类目 `hasPlugAttribute=0`（未开通插件库）时同样会被拒；
- 保存接口（op=1）不校验这些，**只有发布（op=2）才校验**，所以表现为「保存成功、
  发布失败」。

## 三、代码修复

### `src/aeclone/services/smt_attributes.py`

1. `repair_product_attributes()`：
   - 把**所有**类目系统属性的英文/中文名（含别名归一化）加入「自定义属性删除名单」，
     不再只处理必填/已补全属性；
   - `fill_attributes` 把类目**自由文本属性**（Model Number / Weight / Dimensions）
     产出成自定义属性的，保存时统一删除（这些属性无枚举 id，平台只认
     `attrNameId + attrValueId`，用自定义属性提交必然触发重名错误）。
2. `repair_product_attributes()` 增加：
   - 对照类目定义校验商品已有的**系统属性**：值 id 不在当前类目候选时，按标题/属性值
     重新匹配枚举；`attrNameId` 已不属于当前类目的旧系统属性删除；
   - **必填系统属性缺失时自动补上**（包括 Color 这类必填 SKU 规格在商品级的属性），
     避免编辑器/平台报「必填属性不能为空」。
3. 新增 `repair_sku_compatibility()`（纯函数）：
   - SKU 属性存在但值 id 不在当前类目候选 → 先按**标题 + 商品属性值**匹配枚举；
     匹配不到：非必填 → 移除该规格；必填 → 用类目候选兜底（保证 id 合法）；
   - SKU 属性不存在于类目定义、且带插件父级（Voltage/Plug）→ 当
     `hasPlugAttribute=0` 时移除；
   - **补齐类目必填 SKU 规格**（如 `Ballon Size`）：缺失时按标题/属性值选候选，
     选不到用中性候选兜底，保证每条 SKU 都满足类目必填要求；
   - 变更后同步重写 SKU 的 `id`（`14:值;...`），保证与规格属性一致。
4. `api_save.build_save_payload()` 增加价格兜底：SKU 缺 `skuPrice` 用商品价补，
   商品级 `productMinPrice/productMaxPrice` 缺失用第一个有效 SKU 价补，避免编辑器
   「价格缺失」。

### `src/aeclone/services/publisher.py`

- 新增 `_repair_sku_compat()`，在**保存/发布**前按最终类目做一次 SKU 规格修复，
  并在任务日志里记录「规格修复」步骤；
- 两条写入路径都接入：
  - 正常「编辑/发布」流程；
  - 「重试发布失败」的 `publish_only()` 流程；
- AI 覆盖（`build_ai_overrides` → `merge_skus` 重建 SKU）也会再修复一次，
  避免覆盖回旧规格。

## 四、验证

- 用真实的 16 个失败商品数据做只读修复仿真：
  - 10 个重名商品 → 合并后**不再存在与类目系统属性同名的自定义属性**；
  - 6 个 SKU 错误商品 → 修复后**所有 SKU 属性值都在当前类目候选内**，
    类目未开通插件库时插件规格已移除；
  - 16/16 全部通过本地校验。
- 新增单测 `tests/test_ae_publish_failure_repairs.py`（5 项）：
  重名属性删除、透明标题匹配 Clear、插件规格按类目开关保留/移除、
  非必填非法值移除、Publisher 集成步骤；
- 全量离线测试：**1486 tests OK**（skipped 19）。

## 五、使用

重新构建后，对「发布失败」商品点**「重试发布失败」**即可：软件会先做上述属性/规格
修复，再提交发布。若某个商品的颜色等规格被自动匹配得不符合预期，可在店小秘里手动
改回正确规格后再发布。
