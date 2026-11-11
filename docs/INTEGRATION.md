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

产品字段：`id`、`productId`、`subject`（标题）、`imageURLs`（图片）、`productPrice`、`productStatusType`、`categoryId`。

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

## 五、待校正接口

| 功能 | 候选 | 状态 |
|---|---|---|
| 产品保存 | - | 未找到（需从编辑页抓包） |
| 上传签名参数 | `/api/cos/getSign.json` | 路径对，参数缺 |
| 上传回调 | `/api/cos_dxm_call_back.json` | 未验证 |
