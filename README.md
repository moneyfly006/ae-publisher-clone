# AE Publisher Clone（速卖通上架工具 · 重构版）

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
main.py                 入口
src/aeclone/config.py   配置存储
src/aeclone/api.py      OpenAI 兼容客户端
src/aeclone/services.py 业务服务（文本/图片）
src/aeclone/license.py  授权校验
src/aeclone/ui/app.py   Flet 界面
.github/workflows/      CI 打包
```

## 路线图

- [x] 配置 / API 客户端 / 文本与图片服务 / 基础 UI
- [ ] 店小秘采集与发布流程（需要接口协议）
- [ ] 浏览器自动化（Playwright）
- [ ] SKU 变种映射、价格模板
