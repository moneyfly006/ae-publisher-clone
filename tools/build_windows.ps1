# 构建 Windows 产物（在项目根目录的 PowerShell 里执行）
#
#   powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
#
# 产物：dist\AuroraList\AuroraList.exe
# 构建完会自动跑一次 --self-check 验证打包完整性。

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

Write-Host "== 项目目录：$((Get-Location).Path)"

# 1) 虚拟环境
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
    Write-Host "== 已激活 .venv"
} elseif (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "== 使用 .venv\Scripts\python.exe"
} else {
    Write-Host "!! 没有 .venv，先创建："
    Write-Host "   python -m venv .venv"
    Write-Host "   .venv\Scripts\Activate.ps1"
    Write-Host "   pip install -r requirements.txt"
    exit 1
}

# 2) 选对 python（优先用 venv 里的）
$Py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

# 3) 依赖
& $Py -c @"
import importlib, sys
need = ['PyInstaller', 'flet', 'playwright', 'requests', 'PIL', 'Crypto']
missing = []
for m in need:
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
if missing:
    print('!! 缺少依赖：', ', '.join(missing))
    sys.exit(1)
print('== 依赖齐全')
"@

# 4) 打包前静态体检
& $Py tests\test_packaging_spec.py

# 5) 清理旧产物并打包
if (Test-Path "build\app") { Remove-Item -Recurse -Force "build\app" }
if (Test-Path "dist\AuroraList") { Remove-Item -Recurse -Force "dist\AuroraList" }
Write-Host "== 开始打包（约 3~8 分钟）"
& $Py -m PyInstaller app.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! 打包失败，看上面的报错"
    exit 1
}

# 6) 验证产物
$Exe = "dist\AuroraList\AuroraList.exe"
if (-not (Test-Path $Exe)) {
    Write-Host "!! 没有生成 $Exe"
    exit 1
}
$size = [math]::Round((Get-ChildItem -Recurse "dist\AuroraList" | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Write-Host "== 产物大小：$size MB"

# 7) 打包版自检
#    注意：窗口程序没有控制台，输出会写进 <配置目录>\last-selfcheck.txt
Write-Host "== 运行打包版自检"
& $Exe --self-check
Write-Host "== 自检退出码：$LASTEXITCODE（0=正常，1=有问题）"
Write-Host "   报告文件：$env:APPDATA\AuroraList\last-selfcheck.txt"

Write-Host ""
Write-Host "完成。启动方式：双击 dist\AuroraList\AuroraList.exe"
