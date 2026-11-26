# 机器码 5B11795C-CD238492 日志分析与修复

- 机器当前绑定账号：`amanda202188@126.com`（user22，aliExpress 权限有效）
- 客户端版本：1.0.16；最近日志：2026-11-26 10:43；累计日志上传 2275 次（用户22 93 次）
- 说明：客户端每轮结束会上传 app.log 尾部，同一错误会在多次上传中重复出现；下面的“次数”按日志行统计，实际独立故障以商品/批次计。

## 一、定位到的错误

| # | 日志错误 | 独立影响 | 根因 | 性质 |
|---|---|---|---|---|
| 1 | `保存前商品规格发生变化，已停止使用旧的修复结果，请重新读取` | 10:36-10:43 一批 20 个商品，20/20 保存失败 | `apply_sku_repair` 把 `target`（店小秘 product_detail）与 `repair["before"]`（采集箱 raw）做 SKU 签名+严格上下文比较，两个来源天然不同 → 同商品也误报；旧 repair 反复复用 | **代码 bug** |
| 2 | `AI 标题: 调用异常，已跳过`（约一半商品）；`AI 详情 HTTPSConnectionPool ... Read timed out (30s)` | AI 标题间歇失败、详情偶发超时 | publisher 并行 title/SKU 的线程异常被吞（真实异常丢失）；AI 网关偶发超时/429/5xx，`ApiClient.chat` 无重试 | **代码 + 服务抖动** |
| 3 | `营销图上传失败：https://image.moneyfly.club（两种协议都试过了…）` | 49 行错误（多次上传重复） | 自定义图床 base/key 失败时不会回退默认图床；上传异常被吞无细节 | **代码兜底不足**（也可能叠加客户网络/自定义配置） |
| 4 | `'latin-1' codec can't encode character '\u063e'` | Alibaba 商品数量读取/自动处理失败 25 行 | Cookie 值含非 latin-1 字符，requests 写 Cookie 头时 UnicodeEncodeError | **代码 bug** |
| 5 | `还没有选择店铺（去「店铺与模板」读取并选择）` | 30 行 | 切换平台后未在店铺页选择店铺（store_id 为空） | **客户设置问题** |

## 二、代码修复（src/aeclone）

1. `services/sku_recovery.py`：
   - 去掉 target 与 repair["before"] 的 SKU 签名比较（跨来源误报根因）；
   - 保留真实上下文校验（id 必须同；店铺/类目双方有值才比较）；
   - 目标规格无效时按已验证 repair source 修复（`_validate_source`/`_replace` 仍校验同商品、SKU 编码完整覆盖、真实属性）；
   - 编码不匹配时给出可操作错误并清空坏 repair，避免无限复用。
2. `services/publisher.py`：AI 标题/SKU/并行线程的真实异常写入步骤（不再只写“调用异常”）。
3. `api.py`：`ApiClient.chat` 对 timeout/连接中断/429/5xx 指数退避重试（默认 2 次，`AURORA_AI_RETRIES` 可调）；鉴权/额度错误立即失败并显示原因。
4. `services/hosting.py`：上传失败先同配置重试一次；自定义 base 或 key 与默认不同时回退 `DEFAULT_HOST+DEFAULT_KEY`；错误包含最后 HTTP 状态/异常摘要。
5. `integrations/dianxiaomi.py`：Cookie 值非 latin-1 → percent-encode；Cookie 名非法 → 跳过并 WARN，避免整批读取崩溃。

## 三、验证

- 新增/更新测试：`tests/test_user22_log_fixes.py`（12）、`tests/test_sku_recovery.py`（9）、`tests/test_image_hosting_protocol.py`（6/6）
- 关联回归：图片托管/清理、AI 图片、publisher pipeline、API 预检、提示词全通过
- 全量客户端回归：`tools/test_offline.py` → **1472 tests OK（exit 0）**
- 未部署服务端改动（本次均为客户端代码）；本次发布版本 1.0.17

## 四、客户侧需要配合的事项

1. **升级客户端**：当前 1.0.16，需安装包含以上修复的新构建（1.0.17）。
2. **选择店铺**（设置问题）：先到「连接与店铺 / 店铺与模板」检测登录并选择店铺，再运行；否则报“还没有选择店铺”。
3. **图床上传**：新版会自动重试并回退官方图床；若仍频繁失败，检查客户网络能否访问 `image.moneyfly.club`、是否自定义了错误的图床地址/密钥。
4. **Cookie**：latin-1 崩溃已修；若仍提示登录/Cookie 失效，需要在店铺页重新读取 Chrome Cookie 或重新登录店小秘。
5. **AI 标题/详情**：超时会自动重试；若持续失败，检查 API Key 余额/网络（401/402 会直接显示，不重试）。

## 五、结论

- 失败主因（20/20 商品保存失败）是**代码 bug（SKU 修复误报）**，不是客户设置；
- AI 标题失败是**代码吞异常 + AI 网关偶发超时**，新版已加真实报错与重试；
- 营销图上传失败代码已加默认图床回退与错误细节；
- latin-1 是**Cookie 值编码 bug**；
- 只有“未选择店铺”属于**客户设置**，需先在店铺页选择店铺。
