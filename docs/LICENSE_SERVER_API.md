# 授权网站 API 约定（客户端已实现调用与验签）

客户端 `license_server.refresh_entitlements()` 会在启动/授权检查后自动调用，
按顺序尝试以下路径（POST JSON，失败再 GET 查询参数）：

```
POST {server}/api/license/entitlements
POST {server}/api/entitlement
POST {server}/api/license/platforms
POST {server}/api/version/entitlements
```

请求体：

```json
{"machine_code": "D6117EDB-F43215E2", "version": "1.0.5", "product": "AuroraList"}
```

响应（任一即可）：

```json
{"code": 0, "data": {"token": "<entitlement token>"}}
{"token": "..."}
{"data": {"entitlement_token": "..."}}
```

## token 内容

```json
{
  "payload": {
    "machine_code": "D6117EDB-F43215E2",
    "expires_at": "2027-11-17",
    "issued_at": 1795270000,
    "platforms": {
      "alibaba": ["listing","category","trade","ai_text","ai_image","audit"],
      "amazon": ["listing","category","ai_text","ai_image"]
    }
  },
  "sig": "hex hmac-sha256"
}
```

- 签名：`HMAC_SHA256(canonical_json(payload), KEY)`，KEY 客户端内置 `aurora-list-platform-license-v1`，
  服务端可轮换（换后需发新版客户端或通过 `license_public_key` 覆盖）。
- 客户端拿到后仍会**本地验签 + 机器码 + 到期 + 7 天离线宽限**，服务端不可伪造平台。

## 服务端最小实现（伪代码）

```python
payload = {"machine_code": m, "expires_at": expire,
           "platforms": customer_platforms, "issued_at": int(time.time())}
token = {"payload": payload, "sig": hmac_sha256(canonical(payload), KEY)}
return {"code": 0, "data": {"token": json.dumps(token)}}
```

## 套餐建议

| 套餐 | platforms |
|---|---|
| Alibaba 基础 | alibaba: listing + category |
| Alibaba + AI | alibaba: + ai_text + ai_image |
| Alibaba 全功能 | alibaba: + trade + audit |
| Amazon 基础 | amazon: listing + category |
| 双平台 | alibaba + amazon 各自功能包 |

未下发的平台在客户端显示为 🔒，点击弹出「去授权网站开通」；未下发的功能入口灰显锁定。
