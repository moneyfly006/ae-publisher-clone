# add.json 保存接口逆向笔记（进行中）

## 目标
绕开 DOM 模拟，直接 API 保存速卖通商品，大幅加快编辑速度。

## 已确认的事实

### 1. 数据流
- `GET /api/smtProduct/edit.json?id=X` → 返回完整商品模型（192 字段），是表单的初始值来源
- `POST /api/smtProduct/add.json` → 保存（新建+更新都用它）
- `edit.json` 的 POST 返回 405（只读）

### 2. 传输格式（已定位）
前端 axios 封装：
```js
postForm(e, t={}, ...) {
  return ua({
    method: "post",
    data: typeof t == "string" ? t : qs.stringify(t),
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
  })
}
```
- 即 `qs.stringify` + `application/x-www-form-urlencoded`
- 嵌套对象/数组用**方括号**：`a[b]=c`、`arr[0]=x`

### 3. payload 结构（关键）
不是扁平的 edit.json，而是「模块化点号键」：
```js
c = {
  id:"", name, shopId, platform:"smt",
  // 基础字段（来自表单 state w.value，≈ edit.json 顶层）：subject/categoryId/brandId/skuCode/productUnit/...
  "packages.id":"", "packages.moduleType": PACKAGE,
  "packages.smtPackageData.grossWeight": ...,
  "packages.smtPackageData.packageLength/Width/Height": ...,
  "packages.smtPackageData.isPackSell/originalBox/baseUnit/addUnit/addWeight": ...,
  "template.id":"", "template.moduleType": TEMPLATE,
  "template.smtTemplateData.freightTemplateId": ...,
  "template.smtTemplateData.promiseTemplateId": ...,
  "property.id":"", "property.moduleType": PROPERTY,
  "property.smtPropertyData.categoryId": ...,
  "property.smtPropertyData.aeopAeProductPropertys": JSON.stringify(attrs),
  "property.smtPropertyData.groupId/groupIds": ...,
}
```

### 4. 校验规则（逆向自 add-DHGlRhym.js）
- productUnit 必填；packageType=1 时打包数 2~100000
- skuPrice 必填
- deliveryTime 1~60 整数
- wsValidNum 1~30（仅 St===2 或 4）

### 5. 浏览器请求头
API 请求**无特殊认证头**，只有 cookie + referer + content-type。

## 待解决

1. **完整 payload 字段清单**：base/sku/image/price/video 各模块的完整 getFormData 还没抽全。
   保存函数在某个未下载的 chunk 里（index.js 和 add-DHGlRhym.js 里都没有 "smtProduct/add.json" 字面量）。
2. **传输方式确认**：JSON body 返回 200 "保存失败！"（无字段细节）；form-urlencoded 返回 400 HTML。
   说明后端对 JSON 解析不了（或前端确实用 form-urlencoded + qs，我的编码和它的不一致）。
3. **详情错误**：后端只回 `{"code":-1,"msg":"保存失败！"}`，无字段级错误。

## 下一步（按目标 rounds 推进）
1. 列出 smt edit 页面加载的全部 JS chunk，逐个 grep "smtProduct/add"，找到真正的保存函数
2. 读该函数的 getFormData 完整拼装，抽出全部字段 + 映射
3. 实现 save_via_api()：edit.json 合并模板 → 拼 payload → qs.stringify → POST
4. 用最小商品验证，直到保存成功

## 决定性突破（第二轮）

### 传输格式（已彻底搞清）
保存函数在 service-DgqBlGba.js：
```js
async function q(n) {
  let r = await t(JSON.stringify(n), `smtEditSave.txt`);   // payload → txt 文件
  return e.fileUploadForm(`/api/smtProduct/add.json`, {file: r});
}
```
即：**multipart/form-data，字段名 `file`，文件名 `smtEditSave.txt`，内容 = JSON.stringify(payload)**。
- 已验证：这样 POST 返回 HTTP 200（不再是 400 HTML），只是 payload 内容还不完整 → "保存失败！"
- 之前的 json / form-urlencoded 都是错的。

### payload 组装架构（已彻底搞清）
add-DHGlRhym.js 第 258125 行：
```js
async function se(e) {
  let t = B.map(t => t.value?.getFormData?.(e));   // B = 注册的子表单数组
  let [n,r,i,a,o,s,c,l,u,d] = await Promise.all(t);
  return {...n,...r,...i,...a,...o,...s,...c,...s,...l,...u,...d, op:e, id:y.value.idStr};
}
```
最终 payload = **10 个子表单 getFormData 合并 + op + id**。

### 已识别的子表单（getFormData 注册）
| 注册名 | 模块 | getFormData 返回 |
|---|---|---|
| be | 基本信息 | `f({...w.value, subject})` — 顶层字段（shopId/categoryId/brandId/skuCode/...） |
| E | 店小秘信息 | `{sourceUrl, sourceList}` |
| (c) | 包装/模板/属性 | dot 键：packages.*/template.*/property.* |
| lo | 产品属性 | aeopAeProductPropertys(JSON) 等 |
| R/Pe/dr/I/we | 其余（SKU/图片/价格/视频等） | 待枚举 |

### 保存入口（add-DHGlRhym.js 261008）
```js
let r = await se(e);                 // 组装 payload
await ue(r);                         // 预检（价格检测 + 违禁词）
let a = await bn(r);                 // bn = q = multipart 保存
let s = a.code === 0 ? a.data : a;   // code===0 才算成功
```

### 校验规则补充
- 属性区（第 78514 行）：`400000603`=高关注化学品；`化学属性选了无(None)后不可再选其他选项`
- 必填属性错误文案：`必选属性`/`必填属性`

### 下一步
1. 枚举 R/Pe/dr/I/we 这 5 个子表单的 getFormData（都在 add.js 里，按函数名定位）
2. 写 build_save_payload(edit_json, template) 拼出完整 payload
3. 实现 save_via_api()（multipart file=smtEditSave.txt）
4. 迭代测试直到 code===0

## ✅ 完成（第三轮）

### 传输格式（最终）
```python
# multipart/form-data
files = {"file": ("blob", zip_bytes, "application/zip")}
# zip 里装 smtEditSave.txt，内容 = JSON.stringify(payload)
```

### payload 结构（60 个扁平键）
- 与 edit.json 的 product 对象几乎一一对应
- 两个复杂字段必须是 **JSON 字符串**：`aeopAeProductPropertys`、`aeopAeProductSKUs`
- 系统属性 {attrNameId, attrValueId}；自定义属性 {attrName, attrValue}
- `op`: 1=保存 2=发布 3=移入待发布 4=更新 5=定时发布

### 关键字段（必须非空，逐条实测）
| 字段 | 来源 |
|---|---|
| promiseTemplateId | "0"（店铺唯一服务模板 templateId） |
| hsCode | 模板（分类相同，json 字符串） |
| SKU.originalBox | 模板（"1"） |
| SKU.logisticAttrList | 模板（普货） |
| SKU.specialProductTypeList | 模板 |
| 4 个系统属性 | 模板（attrNameId/attrValueId） |

### 实测结果
- save_only (op=1) → `{"code":0,"msg":"您的产品编辑成功！"}`
- publish (op=2) → `{"code":0,"msg":"产品已提交发布…"}`

### 实现文件
- `src/aeclone/services/api_save.py`：build_save_payload / merge_skus / to_submit_props / OP_MAP
- `DianxiaomiClient.save_product_via_api(payload)`：zip + multipart POST
- `tests/test_api_save.py`：5 项单元测试
