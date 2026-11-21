# Amazon 刊登 · 验证报告（初版 / 待 task-4 终版）

> 状态：**初版骨架**（task-3 产出，2026-11-22）。真实只读核验、契约测试、UI 沙箱
> 遍历与截图已有结果；端到端回归、UI↔后端交叉审计、真实写 #2 与最终结论由 task-4
> 填充，Lead 终审。**结论不夸大**：未做/未验证的项目明确标 TODO。
>
> 契约来源：`docs/AMAZON_DESIGN.md`（§1 真实接口、§6 校验、§7 安全、§8 验收）。

---

## 0. 结论速览

| 项目 | 结论 | 证据 |
|---|---|---|
| 真实账号只读核验 | **PASS 18/18**（登录/站点/三态计数/详情/属性/类目树/路径/历史/产品类型/币种/物流/品牌/违禁词/预测） | `docs/amazon-readonly-verification.json` |
| 离线契约测试 | **PASS 27/27**（39 SAVE_KEYS、op 语义、`±` 约定、ZIP+multipart 往返、只读脚本防回退） | `tests/test_amazon_contract.py` |
| UI 13 页沙箱 | 页面可打开，**26 个按钮 on_click 全部无异常**（网络 mock、`AURORA_TEST=1`） | `tools/amazon_ui_shots.py` |
| UI 截图 | 已生成（1560×2200） | `docs/design/amazon-listing-page.png` |
| 真实写入（Lead 执行，用户授权范围） | **写 #1 PASS**：`op=1` 往返保存 `code=0`，回读 `changed_count=0`，采集箱仍 16 | `docs/amazon-write-verification.json` |
| 真实写 #2（AI 文案 op=1） | **TODO**（Lead/backend 执行后回填） | 同上 |
| 全量回归 / compileall | **TODO**（task-4） | `docs/regression-verification.json` |
| UI↔后端签名交叉审计 + 唯一写入口断言 | **TODO**（task-4） | 本文件 §6 |

> 真实写由 **Lead** 在授权范围内执行；本任务的 `tools/verify_amazon.py` **保持只读**，
> 代码内以白名单 + 写入口硬断言拦截，任何写尝试都会记入 `blocked_attempts` 并判失败。

---

## 1. 真实账号只读核验（独立实现）

脚本：`tools/verify_amazon.py`（requests 自实现，**不 import 后端 `aeclone` 模块**）。

- Cookie 来源优先级：`--cookie-file` → `DXM_COOKIE_PATH` → `.tmp-probe/cookies.txt`；
  控制台仅打印前 6 位脱敏片段，报告**不写任何 Cookie 内容**（`cookie.content_logged=false`）。
- 只读白名单：14 个只读端点；唯一写入口以拼接常量 + 请求守卫显式拦截；
  本次 `request_count=21`、`blocked_attempts=[]`、`write_endpoints_never_called=true`。
- 生成时间：`2026-11-22T02:03:16+08:00`，账号 `QINGHEMAOYI` / puid `2728813`。

| # | 检查 | 结果 | 关键字段 |
|---|---|---|---|
| 1 | `userIn` 登录 / 店铺 | PASS | account=QINGHEMAOYI, puid=2728813, 店铺 QingHe, merchantId=A26OV9RTCAYJMS, 默认站点 ATVPDKIKX0DER |
| 2 | `getCountryByShopId` 站点解析 | PASS | 4 站点：US/USD/ATVPDKIKX0DER(en_US)、CA/CAD/A2EUQ1WTGCTBG2(en_CA)、MX/MXN/A1AM78C64UM0Y8(es_MX)、BR/BRL/A2Q3Y263D00KWC(pt_BR) |
| 3 | `pageList` 采集箱(draft) | PASS | totalSize=16（rows=18，含变种行） |
| 4 | `pageList` 待发布(offline) | PASS | totalSize=5 |
| 5 | `pageList` 在线(online) | PASS | totalSize=142 |
| 6 | `leftStat` | PASS | waitPublish=5, publishFail=21 |
| 7 | `editSp` 详情 | PASS | site=CA, **164 字段**，bulletPoint 10 段（`±`），description HTML，productAttr 30 键 |
| 8 | `getProductAttrById` | PASS | data 为 JSON 字符串，30 键（含 is_assembly_required 等） |
| 9 | 类目树逐级（内部 id） | PASS | 顶层 17 → 12 → 15 → 3；子节点 `parentId` 等于请求内部 id |
| 10 | `getCategoryBySiteNodePath` | PASS | 路径 `6299024011/6256963011/6256964011/13553005011` → Backhoes(反铲)；非法路径 `1/2/3` → data=null |
| 11 | `listHistory` | PASS | 最近使用类目 5 条 |
| 12 | `gerProductTypes`（站点代码 CA） | PASS | 1604 个，含 OUTDOOR_LIVING |
| 13 | `gerProductTypes`（传 marketPlaceId） | PASS | 返回空数组（契约 §1.5 注意项） |
| 14 | `currency` | PASS | CAD |
| 15 | `logistics` | PASS | 1 个模板（legacy-template-id / Migrated Template） |
| 16 | `brand` | PASS | 端点正常，返回空数组（可为空） |
| 17 | `amazonCheckBannedWord` | PASS | code=0，data=null（契约允许；仅提示不阻断） |
| 18 | `productTypePredictCatesByItemName` | PASS | itemName="Backhoe Loader" → productType=NON_RIDING_TOY_VEHICLE |

> 说明：`amazon_edit.json` 抓包中的 `editSp` 字段数、`amazonSave_payload.json` 的 39 键结
> 构与真实接口一致；契约测试对证据文件做了结构断言（见 §2）。

---

## 2. 离线契约测试

命令：`.venv/bin/python tests/test_amazon_contract.py` → **27 tests OK**。

覆盖：

1. **设计契约**：`docs/AMAZON_DESIGN.md` 存在；解析保存包得到 **39 个 SAVE_KEYS**（保序）；
   op 语义 `1/3/2/5/6`；`±` 卖点约定（`split("±")`/`join("±")`）；ZIP(`amazonSave.txt`) +
   multipart（`file` 在前、`op` 在后）；SITE_META 四核心站点；§6 校验与 §7 安全边界；
   账号/计数事实（16/5/142、leftStat 5/21）。
2. **独立传输实现**：自实现 payload→ZIP→multipart，`zipfile` 解压往返一致；字段顺序
   `file`→`op`；非 ASCII 往返；用假 requests session 验证传输形状（**不联网、不写**）；
   校验真实抓包 `save_capture2.json` 仍是 ZIP+multipart（含 `op`）。
3. **只读脚本防回退**：`tools/verify_amazon.py` 不含唯一写入口完整字面量、不 import 后端、
   无写开关/save_product 调用；`ALLOWED_ENDPOINTS` 全为只读 `.json` 端点。
4. **证据 JSON 结构**：只读报告与 Lead 写证据的字段/成功码/脱敏/安全块可机器断言。

---

## 3. 真实写入（Lead 执行；验证脚本保持只读）

证据：`docs/amazon-write-verification.json`（生成 `2026-11-22T01:57:56`）。

- runs=3（2 dry-run + 1 真实写），`summary.writes=1`，`last_ok=true`；
- 真实写 #1：`op=1` 往返保存，商品 `220114595363182310`；
  `save_response.code=0`、`data=220114595363182310`、`changed_count=0`（内容零变更）；
  payload 39 键保序，Cookie 在证据里仅 `cookie_masked`。
- **写 #2（AI 文案 op=1）**：TODO —— Lead/backend 执行后在此回填写后回读差异与计数。
- 本任务证据：`tools/verify_amazon.py` 从未调用写入口；契约测试静态断言其源码不存在写路径
  字面量/写开关（见 §2.3），`safety.blocked_attempts=[]`。

> 写 #1 前后采集箱数量未变（16），与写 #2 之后需复核一致（TODO）。

---

## 4. UI（13 号「Amazon 刊登」页）

- `src/aeclone/platforms.py` 已有 `PageItem(13, "Amazon 刊登", "刊登", ...)`；`_switch` 落点 `_page_idx=13`。
- `tools/amazon_ui_shots.py`（`AURORA_TEST=1` + `tools/ui_sandbox.py`）：
  页面打开成功，**遍历 26 个按钮**（ElevatedButton/OutlinedButton/TextButton/IconButton），
  网络替换为返回空数据的 mock、线程/文件选择器禁用，**on_click 全部无异常**；
  含「测试连接」「采集箱/待发布/在线」「加载类目」「校验路径」「AI 生成标题/五点/描述/关键词」
  「预演（不保存）」「保存（预演模式）」等。
- 截图：`docs/design/amazon-listing-page.png`（1560×2200，sha256 前缀 `aa9fe68abc61e148`）。
- 限制：沙箱空数据 + mock 网络，只证明「页面可渲染、按钮回调不崩溃、不触发真实写」，
  不证明真实账号数据渲染与业务正确性（后者由真实只读核验与 task-4 交叉审计覆盖）。

---

## 5. 限制（如实列出）

1. 只读核验不覆盖写链路；真实写证据由 Lead 单独产出（`docs/amazon-write-verification.json`）。
2. 违禁词接口本轮 `data=null`，只能证明端点可调用，**不能证明词库命中规则**。
3. 产品类型预测为 best-effort：长标题会返回 `code=-1`，本轮用短词验证可用路径。
4. 品牌列表可为空（未做品牌备案），空数组视为端点正常。
5. UI 沙箱为 mock 数据，不代表真实店铺数据；截图是静态快照。
6. 真实写 #2（AI 文案闭环）与写后回读差异：TODO。
7. 全量回归、`compileall`、UI↔后端签名交叉审计：TODO（task-4）。

---

## 6. TODO（task-4 填充）

- [ ] `tools/verify_regressions.py` / `tools/test_offline.py` 纳入 Amazon 新测试并全绿。
- [ ] `python -m compileall -q src` 通过。
- [ ] 交叉审计：`inspect.signature` 断言契约 §3 全部函数存在且参数匹配。
- [ ] 断言 UI 唯一写入口 = `services.amazon_publish.save_to_amazon`；grep 断言 UI 无直接
      HTTP 写调用；`AURORA_NO_SAVE` / `AURORA_TEST` 下保存被拒绝（UI 与 publish 两层）。
- [ ] 真实写 #2：pipeline 写 AI 文案 op=1，写前 `editSp` 快照 → 写后回读对比，更新
      `docs/amazon-write-verification.json` 与本文结论表。
- [ ] 写 #2 后复核采集箱/待发布计数与 §0 期望值的关系（写 #1 未变，16 个）。
- [ ] 合并 `docs/regression-verification.json`（保留既有结构）与 P0/P1 问题清单。
- [ ] 本文终版：结论表、功能清单、证据链接、限制、复现命令定稿；Lead 终审。

---

## 7. 复现命令

```bash
# 1) 真实账号只读核验（需要 .tmp-probe/cookies.txt 或 DXM_COOKIE_PATH）
.venv/bin/python tools/verify_amazon.py
#    → docs/amazon-readonly-verification.json（期望 18/18 PASS）

# 2) 离线契约测试（不联网）
.venv/bin/python tests/test_amazon_contract.py

# 3) UI 13 页按钮遍历 + 截图
AURORA_TEST=1 .venv/bin/python tools/amazon_ui_shots.py
#    → docs/design/amazon-listing-page.png（未实现时打印 NOT_READY 并退出 0）

# 4) （task-4）回归
.venv/bin/python tools/verify_regressions.py
.venv/bin/python tools/test_offline.py
.venv/bin/python -m compileall -q src
```

---

## 8. 本任务产物

| 文件 | 说明 |
|---|---|
| `tools/verify_amazon.py` | 真实账号只读核验（白名单 + 写入口硬拦截，独立实现） |
| `tests/test_amazon_contract.py` | 离线契约测试（文档 + ZIP/multipart + 防回退 + 证据结构） |
| `tools/amazon_ui_shots.py` | 13 页按钮遍历 + 截图（NOT_READY 降级、失败降级控件清单） |
| `docs/amazon-readonly-verification.json` | 18 项只读核验 JSON 证据 |
| `docs/design/amazon-listing-page.png` | 13 页沙箱截图 |
| `docs/AMAZON_VERIFICATION.md` | 本文件（初版，task-4 终版） |
