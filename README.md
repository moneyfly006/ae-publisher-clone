# AuroraList · 极光上架（速卖通上架工具 · 重构版）

基于原「速卖通上架」软件的功能重写，UI 全新设计，AI 能力接入自建站点（默认 `https://api.moneyfly.club`）。

## 功能

- **AI 文本**：标题翻译、详情描述生成、SKU 名称优化（支持自定义提示词）
- **AI 图片**：白底图、去水印、通用图像编辑（调用 `/v1/images/edits`）
- **批量任务**：并发队列、失败重试、实时日志
- **授权**：对接 `license.moneyfly.club` 的设备授权校验
- **配置**：API 地址 / Key / 并发数本地持久化

## 运行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 打包（GitHub Actions）

推送 tag 会自动构建 Windows exe 并发布到 Releases：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## 目录

```
main.py                          入口（支持 --page N 指定启动页）
src/aeclone/config.py            配置存储（改任意项自动落盘）
src/aeclone/api.py               OpenAI 兼容客户端（文本/图像编辑/文生图）
src/aeclone/services/            业务服务
  ├─ publisher.py                刊登流程（模板/图片/AI/保存/发布/定时）
  ├─ image_edit_plans.py         逐图编辑方案（每张图独立提示词 + 多图素材）
  ├─ image_ops.py                本地图片：水印 / 四合一 / 补方图
  ├─ combo.py                    套装 SKU 展开
  ├─ album_cleanup.py            店小秘图片空间统计 / 清理旧图
  └─ scheduler.py                定时发布队列
src/aeclone/integrations/        店小秘 API / CDP / 编辑器自动化 / Cookie
src/aeclone/ui/app.py            Flet 界面
tools/verify_issues.py           离线回归（109 项）
tools/button_audit.py            全按钮回调遍历
.github/workflows/               CI 打包
```

## 图片处理要点（老软件思路）

- **先选分组**：主图 / 描述图 / SKU 图，三个 Tab 结构一致
- **简单处理（勾选，不用写提示词）**：图片范围 + 白底 / 去水印 / 翻译；支持「全部白底/去水印/翻译」
- **精细处理（给某几张图写提示词）**：第几张图 + 中文预设 + 提示词 + 素材多选 + 编辑/生成 + 生成数量 + 放哪里
  - 例：第 1 张选「多图合成主图」、素材勾 2/3/4 → 生成的新主图插到第 1 张，原图顺延
- **中文预设 + 英文底层**：界面显示中文名与说明，实际发送英文模板；手改后按自定义发送
- **四合一**：AI图片处理「四合一拼图」按钮 / 通用里设位置；图片工作台也有一键按钮
- **旧配置自动迁移**：老「主图指定编辑」「逐图编辑方案」会转成精细任务

## 授权（每台机器唯一）

- 设置页显示机器码（`XXXX-XXXX-XXXX-XXXX`），点「复制机器码」发给管理员登记
- 登记后点「重新检测」即可；未授权时不允许开始上架
- 机器码来自硬件指纹（Win MachineGuid / macOS IOPlatformUUID / Linux machine-id），换机/重装系统会变

## 效率与多开

- 产品/队列并发处理（设置里「并发数」）
- 图片结果缓存：同图同处理只调用一次（默认 7 天，可关）
- 价格/模型缓存 5 分钟，界面不再卡
- 多开：每实例独立配置、独立浏览器调试端口、窗口错开

## 价格设置要点（无 JSON）

- 模式：倍率 / 包邮 / 含佣金利润
- 阶梯价格：表格（成本下限、上限、倍率）
- 区域倍率：币种 + 倍率表格，可存成本地模板
- 批发价：起订量 + 单价表格
- 试算与刊登共用 `compute_price`，结果一致

## 路线图

- [x] 配置 / API 客户端 / 文本与图片服务 / 基础 UI
- [x] 店小秘采集与发布流程（真实保存 + 发布 + 定时）
- [x] 浏览器自动化（Playwright / CDP）
- [x] SKU 变种映射、价格模板、套装 SKU
- [x] 逐图编辑方案 / 多图素材 / 四合一 / 图片空间清理
