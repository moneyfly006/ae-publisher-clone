# 授权一致性审计报告（后台 ↔ 软件 ↔ 门户）· 2026-11-25

## 一、结论

- 服务端/后台/门户/客户端的“有效权限”事实源统一为 `entitlement_effective()`（`revoked=0` + `expires_at >= today` + 每功能取最晚到期 + 平台注册表交集）。
- 后端一致性矩阵 **132/132 PASS**：无权限、全平台、功能子集、混合到期、部分过期、撤销部分/全部、禁用账号、解绑设备、跨账号隔离、重复开通、到期边界全部对齐。
- 本轮定位并修复 **14 个问题**（客户端 7、服务端 6、门户 1），详见下文。
- 剩余风险主要来自**旧客户端（≤1.0.10）** 和**发版/更新配置**，需要构建新版本后收尾（见第五节）。

## 二、已修复问题

### 客户端（src/aeclone/**）

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| 1 | AMAZON「类目与属性」页 feature=`listing`（应为 `category`） | 只开 listing 时该页可编辑类目（该锁却显示）；只开 category 时反被锁（开通不识别） | 改为 `category`；新增全平台页面 feature 不变量测试 |
| 2 | 46 号「任务队列」在权限检查前直接渲染 | 未开通平台也能打开队列页 | 门禁顺序移到 `_page_allowed` 之后 |
| 3 | 运行按钮只查 `has_platform`（任意功能包即可跑） | 只有类目/图片权限也能启动刊登 | `_license_active` 收紧为必须含 `listing`，并保留服务层 `core.guard.require` 兜底 |
| 4 | 功能到期只在加载 token 时过滤，长期运行/断网不重算 | 到期后功能一直可用（该锁却显示） | 周期 tick 先用缓存 token 本地重算并刷新导航/平台栏/当前页；离线也最迟 5 分钟锁定 |
| 5 | 隐藏配置 `license_platforms` 一配就全平台解锁 | 可绕过服务器授权 | 仅 `AURORA_LICENSE_OVERRIDE=1`/`AURORA_TEST=1`/`AURORA_NO_SAVE=1` 生效，生产配置忽略 |
| 6 | 账号 A→B 切换/退出后 rev 基线可能残留 | 心跳 rev 串号、误触发刷新 | 切换/退出重置 rev 基线，权限由当前 token 完全重算 |
| 7 | 旧客户端（≤1.0.10）无心跳 rev/自动重登 | 撤销/禁用最多延迟到刷新或 7 天离线宽限 | 代码无法修复；记录风险并提示升级 |

门禁路径审计（11 条）全部通过：`_page_allowed`、`_switch`（导航/深链/搜索/快捷键）、`_license_active`、运行入口、`_has_any_platform`、平台栏、锁定页、账号弹窗、购买/续费、设备管理、心跳/周期刷新；确认无越权解锁。

### 服务端（deploy/license-v3/**）

| # | 问题 | 修复 |
|---|---|---|
| 1 | `entitlement_summary($uid,false)` 严格模式仍并入已过期行功能 | 严格模式 SQL 加 `expires_at>=today` |
| 2 | `/api/me` 返回过期 entitlements 行、summary 宽松 | 只返回有效行 + strict summary |
| 3 | `user_entitlement_rows()` 未按平台注册表过滤 features | 与平台 features 取交集 |
| 4 | 后台 grant 传非法 features 时静默回退“全功能” | 抛「功能项不属于平台 X」且不落库 |
| 5 | 禁用账号的 `entitlement_revision()` 可能返回非空 | 非 active 账号一律返回空串 |
| 6 | `version_check` 字段不足、本地无路由、无 `min_version` | 补 `has_update/currentVersion/minVersion/forceUpdate` 等 + 本地路由 + 后台设置项 |

### 门户

| # | 问题 | 修复 |
|---|---|---|
| 1 | 已到期权限在 `/account` 显示成「未开通」 | 改为「已到期 + 去续费」，从未开通才「未开通」；KPI 只算有效；与 `/account/entitlements` 一致 |

## 三、验证证据

- 后端一致性矩阵：`deploy/license-v3/bin/e2e-authz.py` → **PASS=132 FAIL=0**
- 客户端审计测试：`tests/test_license_consistency_audit.py` 16 OK、`test_platforms.py` 7 OK（含映射审计表）
- 客户端全量：`tools/test_offline.py` → **1414 tests OK**
- 后端 e2e：**43/43 PASS**；PHP lint 全通过
- 生产抽验：`/api/me` 严格过滤过期行；`version_check` 返回 1.0.11 + 完整字段；后台设置页显示「最低版本」；门户已到期卡片正常
- 测试账号/权限已清理

## 四、关键设计语义（避免误判）

- 客户端是**拉取式授权**：后台开通/撤销不会主动推送；新版客户端通过心跳 `license_rev`（120s）+ 5 分钟周期刷新 + 锁定页「刷新授权」按钮收敛延迟。
- 已解绑设备/禁用账号：客户端刷新返回 `UNBOUND/DISABLED` → 自动弹登录页重登（`bound=false`）；旧版最多 7 天离线宽限。
- 撤销记录保留（`revoked=1`）不物理删除；后台“平台权限”聚合表按平台合并（记录数可见），撤销单条记录时若同平台还有其他有效记录，平台仍有效。
- 权限按“平台 + 功能包”两级：页面级按 feature 锁定；平台级按 `has_platform` 显示已开通/未开通。

## 五、剩余风险与必须执行的动作

1. **构建并发布新客户端（必须）**：本轮客户端修复 7 项只在仓库代码中，GitHub 上最新 release 仍是旧的 v1.0.11 安装包。
   - 建议新版本号 **1.0.12**（同步 `app.spec`、`installer.iss`、`__version__`）。
   - 发布后将线上 `latest_version` 改为 1.0.12（当前已临时设为 1.0.11，可让 1.0.6/1.0.10 先升级到旧 1.0.11）。
2. **旧客户端（1.0.6/1.0.10）**：无 rev/自动重登，撤销/禁用需客户刷新/重启，最多 7 天离线宽限；尽快推动升级。
3. **legacy 兼容**：`legacy_verify_compat=1` 仅为老客户端保留；ECDSA voucher 离线不可吊销（撤销后旧客户端可用到 voucher 到期）。确认老客户端基本升级后改为 `0`（会阻断未升级旧客户端）。
4. **更新通道**：`latest_url` 当前是 GitHub release 页面（仅 GitHub API 不可达时的回退），单 URL 无法区分平台且不是安装包直链；建议：
   - 正常依赖 GitHub API（客户端更新器优先 GitHub）；
   - 若要服务端兜底，改为托管安装包并提供平台对应直链/`latest_sha256`（当前 sha256 为空，跳过校验）。
5. **离线宽限**：7 天为设计值；在线时新版删除/撤销约 2 分钟内生效，到期在 5 分钟内本地锁定；如业务要求更严格可缩短宽限天数（需客户端改动）。
6. **同平台多条记录**：后台撤销单条不会影响同平台其他有效记录；如需“撤销整个平台”，建议增加批量撤销入口（未实现，属产品增强）。

## 六、复跑方式

```bash
cd deploy/license-v3
rm -f /tmp/alv3-test.db*
LICENSE_V3_DB=/tmp/alv3-test.db php bin/init.php --force
LICENSE_V3_DB=/tmp/alv3-test.db php -S 127.0.0.1:8787 -t public public/index.php &
python3 bin/e2e-authz.py --base http://127.0.0.1:8787 --db /tmp/alv3-test.db   # PASS=132 FAIL=0

cd /Users/apple/Projects/alibaba-lister
./.venv/bin/python tools/test_offline.py                                    # 1414 tests OK
```
