# 📚 AuroraList 文档索引

## 🎯 上手

- **[docs/操作指南.md](docs/操作指南.md)** - 每个页面/按钮/设置项的作用，5 分钟上手

## 🛠️ 构建与开发

- **[docs/BUILD.md](docs/BUILD.md)** - 打包、依赖、自检
- **[docs/ADD-JSON-REVERSE-NOTES.md](docs/ADD-JSON-REVERSE-NOTES.md)** - add.json 保存接口逆向笔记（开发用）

---

## 🧪 测试（35 个文件）

| 文件 | 说明 |
|------|------|
| [test_ai_features_comprehensive.py](tests/test_ai_features_comprehensive.py) | 全面AI功能验证测试 |
| [test_ai_image_flow.py](tests/test_ai_image_flow.py) | AI图片处理专项测试 - 模拟真实流程但不调用真实API。 |
| [test_api.py](tests/test_api.py) | （无说明） |
| [test_api_preflight.py](tests/test_api_preflight.py) | API 预检测试：用本地假 HTTP 服务模拟各种返回。 |
| [test_api_save.py](tests/test_api_save.py) | api_save 单元测试（不联网）：payload 构建 + 属性/SKU 合并 + op 映射。 |
| [test_audit_pending_skip.py](tests/test_audit_pending_skip.py) | 「商品审核中」处理测试。 |
| [test_audit_safety.py](tests/test_audit_safety.py) | Offline regression checks: no application imports, network or customer config. |
| [test_build_scripts.py](tests/test_build_scripts.py) | 构建脚本与打包配置的一致性检查。 |
| [test_config_robustness.py](tests/test_config_robustness.py) | 配置健壮性测试：损坏的 config.json 不能把 API Key 悄悄弄丢。 |
| [test_content_review.py](tests/test_content_review.py) | Content verdicts must never replace HTML or silently pass failed checks. |
| [test_docs_consistency.py](tests/test_docs_consistency.py) | 文档 / 代码 / 测试 一致性检查。 |
| [test_download_cache.py](tests/test_download_cache.py) | Real filesystem caching in temporary folders, fake HTTP only. |
| [test_host_cleanup.py](tests/test_host_cleanup.py) | 图床清理测试（本地假服务，不联网）。 |
| [test_image_edit_modes.py](tests/test_image_edit_modes.py) | 图片通用编辑（老软件结构）逻辑测试。 |
| [test_image_hosting_fix.py](tests/test_image_hosting_fix.py) | 测试图片托管优化的BUG修复 |
| [test_image_hosting_optimization.py](tests/test_image_hosting_optimization.py) | Already-hosted images must skip redundant download and upload. |
| [test_image_hosting_protocol.py](tests/test_image_hosting_protocol.py) | 图床上传测试（不联网，用本地假服务模拟两种协议）。 |
| [test_image_job_regression.py](tests/test_image_job_regression.py) | Exercise real image-job control flow with fake services and temporary files. |
| [test_image_size_validation.py](tests/test_image_size_validation.py) | 图片尺寸校验（速卖通 ≥800×800）专项测试。 |
| [test_integration.py](tests/test_integration.py) | 功能集成测试 - 不启动GUI，直接测试核心服务逻辑。 |
| [test_local_features.py](tests/test_local_features.py) | AuroraList 本地功能测试脚本 |
| [test_packaging_spec.py](tests/test_packaging_spec.py) | 打包配置体检（不执行构建，几秒出结果）。 |
| [test_preflight_config.py](tests/test_preflight_config.py) | 启动前自检测试。 |
| [test_pricing_regression.py](tests/test_pricing_regression.py) | Load the pure pricing module without importing application services. |
| [test_product_isolation.py](tests/test_product_isolation.py) | Offline behavioral regressions; no customer config or network imports. |
| [test_prompt_plumbing.py](tests/test_prompt_plumbing.py) | 「提示词是否真的发给模型」验证（不联网，用本地假图片 API）。 |
| [test_publisher_pipeline_sim.py](tests/test_publisher_pipeline_sim.py) | Publisher 端到端模拟（不联网、不碰店铺）。 |
| [test_real_ai_execution.py](tests/test_real_ai_execution.py) | 真实环境AI功能执行测试 |
| [test_run_end_to_end.py](tests/test_run_end_to_end.py) | Publisher.run() 端到端离线测试（不联网、不碰店铺、不调用 AI）。 |
| [test_selfcheck_cli.py](tests/test_selfcheck_cli.py) | 命令行自检（--self-check）测试。 |
| [test_switch_effectiveness.py](tests/test_switch_effectiveness.py) | 界面开关到底生不生效 —— 自动审计。 |
| [test_ui_state_regression.py](tests/test_ui_state_regression.py) | UI state regressions: execute selected AST nodes, never import the real App. |
| [test_variants_regression.py](tests/test_variants_regression.py) | Pure-module regression coverage: no package startup, GUI, or configuration. |
| [test_weight_reuse.py](tests/test_weight_reuse.py) | An existing estimate response must not trigger another billed request. |
| [test_workbench_safety.py](tests/test_workbench_safety.py) | AST-isolated UI safety tests: never import the app or customer configuration. |
