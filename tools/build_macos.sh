#!/usr/bin/env bash
# 构建 macOS 产物（在项目根目录执行）
#
#   ./tools/build_macos.sh
#
# 产物：dist/AuroraList/AuroraList
# 构建完会自动跑一次 --self-check 验证打包完整性。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "== 项目目录：$ROOT"

# 1) 虚拟环境
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  echo "== 已激活 .venv"
else
  echo "!! 没有 .venv，先创建：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# 2) 依赖
python - <<'PY'
import importlib, sys
need = ["PyInstaller", "flet", "playwright", "requests", "PIL", "Crypto"]
missing = []
for m in need:
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
if missing:
    print("!! 缺少依赖：", ", ".join(missing))
    print("   请执行：pip install -r requirements.txt")
    sys.exit(1)
print("== 依赖齐全")
PY

# 3) 打包前静态体检（几秒，能挡掉大部分低级问题）
python tests/test_packaging_spec.py

# 3.5) Nuitka 加固：把核心模块编译成原生 .so（默认开启，AURORA_HARDEN=0 可关）
if [ "${AURORA_HARDEN:-1}" = "1" ]; then
  echo "== Nuitka 加固：编译核心模块（约 5~10 分钟）"
  trap 'python tools/harden.py --restore >/dev/null 2>&1 || true' EXIT
  python tools/harden.py
fi

# 4) 清理旧产物并打包
rm -rf build/app dist/AuroraList
echo "== 开始打包（约 3~5 分钟）"
# 打包前把当前机器的配置固化为默认值（排除 API Key / Cookie / 店铺等）
# 不把本机账号配置导出成默认值；默认值以 src/aeclone/config.py 为准。

pyinstaller app.spec --noconfirm --log-level WARN

# 5) 验证产物
BIN="dist/AuroraList/AuroraList"
if [ ! -x "$BIN" ]; then
  echo "!! 没有生成可执行文件：$BIN"
  exit 1
fi
echo "== 产物大小：$(du -sh dist/AuroraList | cut -f1)"

echo "== 运行打包版自检"
set +e
"$BIN" --self-check
CODE=$?
set -e
echo "== 自检退出码：${CODE} (0=一切正常, 1=有问题, 见上面 x)"

echo
echo "完成。启动方式："
echo "  open dist/AuroraList/AuroraList.app 2>/dev/null || dist/AuroraList/AuroraList"
