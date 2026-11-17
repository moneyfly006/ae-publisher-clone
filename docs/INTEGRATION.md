# 真实系统对接记录（实测）

日期：2026-11-11　环境：Windows 11 虚拟机 Edge（已登录店小秘） + CDP

## 一、CDP 接入

- Edge 启动参数：`--remote-debugging-port=9222 --remote-allow-origins=*`
- 虚拟机内只监听 127.0.0.1，用 `netsh interface portproxy` 映射到 `0.0.0.0:9223`
- Playwright `connect_over_cdp("http://<vm-ip>:9223")` 连接后读取 cookies

## 二、店小秘接口（已实测）

| 功能 | 方法 | 路径 | 结果 |
|---|---|---|---|
| 用户信息 | GET | `/api/userIn.json` | 200 `{id, account, vipLevel...}` |
| 会员信息 | GET | `/api/getVipActiveTime.json` | 200 |
| 相册分类 | GET | `/api/album/index.json` | 200 `data.categoryTree[]` |
| 产品列表 | GET | `/api/smtProduct/list.json?page=1&pageSize=N` | 200 `data.page.list[]` |
| **产品保存** | POST | `/api/smtProduct/add.json` | 实测（form-data，编辑器「保存」触发） |
| **图片上传(URL)** | POST | `/api/album/toUploadPicFromNet.json` | 实测 200，参数名 `urls`；**我们的图片服务器 URL 也可直接上传成功** |
| 上传回调 | POST | `/api/cos/cosDxmCallBack.json` | 从 JS 提取 |
| 品牌历史 | POST | `/api/smtProduct/addProductBrandHistory.json` | 实测 |

产品字段：`id`、`productId`、`subject`（标题）、`imageURLs`（图片）、`productPrice`、`productStatusType`、`categoryId`。

> **重要**：`imageURLs` 是**分号拼接的多张图**（`url1;url2;...`），必须按 `;` 拆分，
> 否则会拿到 404 占位图（1080 字节的灰色 jpeg）。已修复。

## 三、注意事项

- **频率限制**：同一接口连续调用会返回 `code=2005 系统繁忙`，客户端已内置 2.5s 重试
- **登录检测**：用 `userIn` 而不是 `album`（相册 SPA 会先调用，容易触发限频）
- **上传签名**：`POST /api/cos/getSign.json` 存在，但参数未完全摸清（返回"参数缺失"），需继续抓包

## 四、客户端验证输出

```
login: True
user: wangmengruie | id: 2447945
albums: 1 first: 未分类
products: 100
```

## 五、保存（已打通，页面自动化）

API 保存是 `multipart/form-data`，Playwright 无法直接复放 body，因此采用与老软件一致的**页面自动化**：

```
打开 https://www.dianxiaomi.com/web/smt/edit?id=<productId>
 → 定位标题输入框（input[type=text] 第一个）
 → 填入新标题（或原值触发变更）
 → 点击「保存」
 → 监听 /api/smtProduct/add.json 响应
```

实测结果：

```
SaveResult(ok=True, message='保存接口 HTTP 200', title='Durable Rotary Water Well Drilling Equipment ...')
```

## 六、图片空间清理接口（2026-11-17 从相册页 JS 逆向）

来源：相册页 chunk `dxm-web/2026-11/assets/album-8OYLfqJZ.js`（`/web/service/album`）。

| 功能 | 方法 | 路径 | 参数 / 返回 |
|---|---|---|---|
| 文件列表 | GET | `/api/album/list.json` | `name,startTime,endTime,fileType(0=图片,1=PDF),fullCid,pageNo,pageSize` → `{picListPage:{list,totalSize,lockedCount}}`；条目字段 `id/idStr,name,imgUrl,imgCompressSize,imgWidth,imgHeight,lockState(1=锁定)` |
| 数量统计 | GET | `/api/album/getFileTypeCounts.json` | 同上（无分页）→ `{FILE_TYPE_IMAGE, FILE_TYPE_PDF}` |
| 按 id 删除 | POST(form) | `/api/album/delPic.json` | `ids=id1,id2`（逗号拼接；锁定图片站点自动跳过） |
| 按条件删除 | POST(form) | `/api/album/deleteBySearchCondition.json` | 同列表筛选参数 → 删除该时间段的文件 |
| 清理未引用图片 | POST(json) | `/api/album/createAutoDeleteTask.json` | `{startTime,endTime}`（毫秒时间戳）→ 删除窗口内**未被商品引用**的图片（站点异步任务，最安全） |
| 查询/取消任务 | GET / POST(json) | `/api/album/getLatestAutoDeleteTask.json`、`/api/album/cancelAutoDeleteTask.json` | `{taskId}` |

封装：`DianxiaomiClient.album_counts / album_files / album_delete_ids / album_delete_before / album_auto_delete`
+ `services/album_cleanup.py`（`space / scan / delete_candidates / delete_older_than / auto_clean`）
+ UI「清理旧图」（店铺设置第 4 卡、图片工作台按钮）。

> 空间不足时刊登流程仍会优雅跳过上传并改用外链 URL，同时在日志里引导去「清理旧图」。

## 七、待校正接口

| 功能 | 候选 | 状态 |
|---|---|---|
| 产品保存 | `/api/smtProduct/add.json` | ✅ 已实测 |
| 上传签名参数 | `/api/cos/getSign.json` | 路径对，参数缺 |
| 上传回调 | `/api/cos_dxm_call_back.json` | 未验证 |
| 编辑器内发布 | 复用 `batchPublish.json`（`_publish_in_page` 监听同名响应） | 🟡 已接，待虚拟机复跑确认按钮文案 |

## 八、站点模型与别名（保留策略）

真实模型（8 个）：
`z-image-turbo`、`qwen-image-2.1-turbo`、`qwen-image-3.0`、`qwen-plus`、`qwen3-vl-plus`、`deepseek-chat`、`deepseek-reasoner`（`qwen-turbo` 因阿里侧 limit_requests 已从渠道移除）

兼容别名（7 个，价格与目标模型一致）：

| 别名 | 指向 | 价格 |
|---|---|---|
| gpt-6-luna / gpt-4o / gpt-4o-mini | qwen-plus | 倍率 0.6 |
| gpt-3.5-turbo | qwen-plus（原 qwen-turbo 被上游限流，已改指向） | 倍率 0.6 |
| gpt-image-2.5-flare / -vip / gpt-image-2.0 | qwen-image-2.1-turbo | ¥0.12/张 |

实现：渠道 `model_mapping` + `abilities` 补行 + `ModelPrice`/`ModelRatio` 补价。
原因：老软件硬编码 `gpt-6-luna`、`gpt-image-2.5-flare*`，删别名会导致「API 密钥无效 / 模型配置有误」。
