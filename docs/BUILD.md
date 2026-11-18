# 构建说明

两种目标平台，脚本都是「一条命令跑完」：检查依赖 → 静态体检 → 打包 → 验证产物 → 跑自检。

---

## macOS

```bash
./tools/build_macos.sh
```

产物：

```
dist/AuroraList/AuroraList        # 可执行文件（13.7 MB）
dist/AuroraList/_internal/        # 依赖（约 264 MB 总计）
```

启动：

```bash
dist/AuroraList/AuroraList
```

### 实测记录（2026-11-18）

```
== 依赖齐全
== 打包前体检 7 项全过
== 开始打包（约 3~5 分钟）
== 产物大小：264M
== 运行打包版自检 → 退出码 1（因为没填 API Key，属正常）
```

---

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

产物：

```
dist\AuroraList\AuroraList.exe
dist\AuroraList\_internal\
```

启动：双击 `dist\AuroraList\AuroraList.exe`

> **本机是 macOS，Windows 这一步没有实跑过。** 脚本按 `app.spec` 的配置写
> （`flet_desktop` 已收集、`console=False`、目录模式），逻辑与 macOS 版一致。

### 注意：窗口程序看不到控制台

`app.spec` 里 `console=False`，所以 Windows 上 `--self-check` 的输出在终端里看不见。
报告会自动写到：

```
%APPDATA%\AuroraList\last-selfcheck.txt
```

排查问题时把这个文件发过来即可。

---

## 打包前会被拦下的问题

脚本会先跑 `tests/test_packaging_spec.py`（7 项，几秒）：

| # | 检查 |
|---|---|
| 1 | 入口可导入（`main.py → aeclone.ui.app.run`） |
| 2 | flet / flet_desktop / playwright 都被收集 |
| 3 | hiddenimports 含 playwright.sync_api / _driver / browser_cookie3 / Crypto |
| 4 | `collect_submodules("aeclone")` 覆盖，抽查新模块可导入 |
| 5 | assets 目录存在且被打包 |
| 6 | excludes 不含 PIL / requests / playwright / flet |
| 7 | spec 语法可解析 |

---

## 曾经踩过的打包坑（已修）

**`VOUCHER_FAIL ModuleNotFoundError: No module named 'Crypto.PublicKey._nist_ecc'`**

`app.spec` 原来手写 hiddenimports：

```python
"Crypto", "Crypto.Cipher", "Crypto.Cipher.AES",
"Crypto.Protocol", "Crypto.Protocol.KDF",
```

pycryptodome 的 C 扩展按需 import，手写必漏 → 打包后授权校验直接失败。

改成整包收集：

```python
hiddenimports=collect_submodules("aeclone") + flet_hidden + pw_hidden
               + collect_submodules("Crypto")
               + collect_submodules("browser_cookie3") + [...]
```

现在 `--self-check` 会逐个点名检查
`Crypto.Cipher.AES` / `Hash.SHA256` / `PublicKey.ECC` / `Signature.DSS` / `Protocol.KDF`，
漏了会直接报 ✗。

---

## 构建后建议做的三件事

1. **跑自检**
   ```bash
   dist/AuroraList/AuroraList --self-check
   ```
   退出码 0 = 环境正常；1 = 有 ✗ 项。

2. **确认配置能被读到**
   自检里「配置文件 / 文件存在 / 加载来源」三行应该都是 ✓ / `file`。
   如果显示 `defaults`，说明配置坏了，去看 `config.corrupt-*.json`。

3. **不要直接拿真实商品试**
   先把「自动保存」关掉（`设置 → 未开自动保存` = 只预演），确认流程跑通再开。

---

## 常见构建失败

| 报错 | 原因 | 处理 |
|---|---|---|
| `没有 .venv` | 没建虚拟环境 | `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| `缺少依赖：flet` | 依赖没装全 | `pip install -r requirements.txt` |
| `Failed to collect submodules for 'flet.security'` | 缺 `cryptography` 包 | **可以忽略**，本程序不用 Flet 的 security |
| 打包成功但一打开就退出 | 缺数据/二进制 | 跑 `--self-check` 看哪一项 ✗ |
