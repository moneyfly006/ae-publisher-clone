# 全界面按钮 / 开关 / 冲突体检报告

生成时间：2026-11-22T18:51:50+08:00

## 1. 按钮（AST 静态扫描）

- 按钮构造总数：**418**
- 无 on_click：**0**
- 默认 disabled=True：**1**
- 回调只有 toast/占位：**0**

### 默认 disabled

- L18137 `ElevatedButton` **立即安装并重启** handler=-

## 2. 开关（配置 bool 的读取点）

- bool 配置总数：**111**
- 只写不读（仅 UI 引用）：**0**
- 全仓未引用：**0**

## 3. 配置冲突

- 当前真实配置冲突：**0**（blocking=0）

## 3b. 开关翻转冲突矩阵

- 翻转布尔键：111；新增冲突组：**5**
- `auto_generate_sku_code`=False → warn:「自定义 SKU 前缀」已开启但前缀为空
- `custom_detail_text_enable`=True → warn:「添加自定义详情」已开启但没有任何内容
- `custom_sku_prefix_enable`=False → warn:「只用前缀」开启但「自定义前缀」关闭
- `overlay_watermark_enable`=True → error:水印叠加已开启但未设置水印
- `review_block_on_fail`=True → warn:「不合规时拦截发布」已开启，但没有任何审查项

## 3c. 运行时禁用控件（逐平台逐页）

- alibaba: 2 个禁用控件
  - p33(保存与发布) OutlinedButton 「结果上一页」
  - p33(保存与发布) OutlinedButton 「结果下一页」
- aliexpress: 0 个禁用控件
- amazon: 2 个禁用控件
  - p21(发布与结果) OutlinedButton 「结果上一页」
  - p21(发布与结果) OutlinedButton 「结果下一页」
- tiktok: 2 个禁用控件
  - p28(保存与发布) OutlinedButton 「结果上一页」
  - p28(保存与发布) OutlinedButton 「结果下一页」
- shopee: 2 个禁用控件
  - p43(保存与发布) OutlinedButton 「结果上一页」
  - p43(保存与发布) OutlinedButton 「结果下一页」

## 4. 运行时按钮审计交叉

- 回调：{"DEFERRED": 206, "OK": 400, "NATIVE": 8, "EXTERNAL": 15}

### 同页重复文案（前 20）

- alibaba:5 「+ 添加」×6
- alibaba:5 「批量 1-8 张」×6
- amazon:19 「AI」×5
- amazon:19 「套用」×5
- amazon:19 「恢复内置」×5
- alibaba:5 「翻译序号」×4
- tiktok:0 「前往 TikTok 刊登」×4
- alibaba:4 「TextField」×3
- amazon:0 「点一下去处理」×3
- tiktok:0 「点一下去处理」×3
- alibaba:5 「图片位置」×3
- alibaba:5 「AI提示词预设」×3
- alibaba:5 「删除这一条」×3
- alibaba:5 「AI 提示词」×3
- alibaba:5 「参考图（0/5）」×3
- alibaba:5 「清空参考图」×3
- alibaba:0 「商品标题关键词（留空处理全部）」×2
- alibaba:0 「前往账号与连接」×2
- alibaba:3 「套用」×2
- alibaba:3 「TextField」×2

原始数据：`docs/control-audit.json`。
