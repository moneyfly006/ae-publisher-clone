#!/usr/bin/env python3
"""把核心模块用 Nuitka 编译成原生扩展（.so / .pyd），替换掉 .py。

为什么
------
PyInstaller 打出来的是 Python 字节码，`pycdc` / `uncompyle6` 能还原个七七八八。
Nuitka 把 Python 编译成 C 再编成原生机器码，反编译难度高一个量级。

用法
----
    python tools/harden.py              # 编译默认核心模块（推荐）
    python tools/harden.py --all        # 编译全部业务模块（慢，但更彻底）
    python tools/harden.py --restore    # 从备份还原 .py（调试用）

注意
----
- 只编译「逻辑价值高、不需要被用户改」的模块；
- UI（ui/app.py）不编译，方便后续微调；
- 编译后的 .so 与原 .py 同名同导入路径，代码零改动；
- 备份在 build/harden_backup/，可随时 --restore。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows CI 控制台默认 cp1252/GBK，打印中文会 UnicodeEncodeError
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "aeclone"
BACKUP = ROOT / "build" / "harden_backup"

# 默认：破解者最想改的、以及核心业务逻辑
CORE = [
    "license.py",
    "core/guard.py",
    "core/report.py",
    "services/api_save.py",
    "services/publisher.py",
    "integrations/dianxiaomi.py",
    "config.py",
    "api.py",
]

# --all：再加这些
EXTRA = [
    "services/pricing.py",
    "services/image_ops.py",
    "services/hosting.py",
    "services/variants.py",
    "services/text_pipeline.py",
    "services/edited.py",
    "core/updater.py",
    "core/instances.py",
    "selfcheck.py",
]


def _ext() -> str:
    if sys.platform == "win32":
        return ".pyd"
    return ".so"


def _compiled_path(module: Path, outdir: Path) -> Path | None:
    """Nuitka 产物：<stem>.cpython-XX-<platform>.so 或 <stem>.pyd。"""
    cands = sorted(outdir.glob(f"{module.stem}*.so")) + sorted(outdir.glob(f"{module.stem}*.pyd"))
    return cands[0] if cands else None


def compile_one(rel: str) -> bool:
    module = SRC / rel
    if not module.exists():
        print(f"  ! 跳过（不存在）：{rel}")
        return True
    if module.suffix != ".py":
        print(f"  ! 跳过（不是 .py）：{rel}")
        return True

    print(f"  · 编译 {rel} …", flush=True)
    with tempfile.TemporaryDirectory() as td:
        outdir = Path(td)
        cmd = [sys.executable, "-m", "nuitka", "--module", str(module),
               f"--output-dir={outdir}", "--remove-output", "--no-pyi-file",
               "--assume-yes-for-downloads", "--quiet"]
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ✗ 编译失败：{rel}")
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-6:]
            for line in tail:
                print("    ", line)
            return False

        built = _compiled_path(module, outdir)
        if not built:
            print(f"  ✗ 没找到产物：{rel}")
            return False

        # 备份原 .py
        BACKUP.mkdir(parents=True, exist_ok=True)
        dst_rel = Path(rel)
        bak = BACKUP / dst_rel
        bak.parent.mkdir(parents=True, exist_ok=True)
        # 每次都刷新备份：否则 --restore 会把「第一次构建时的旧代码」盖回当前源码，
        # 导致构建后刚改的补丁神秘消失（已踩坑多次）。
        if module.exists():
            shutil.copy2(module, bak)

        # 放 .so 到原目录，删 .py
        target = module.parent / f"{module.stem}{_ext()}"
        # Nuitka 输出名带 cpython 标记时保留完整名（Python 才能识别）
        shutil.copy2(built, module.parent / built.name)
        try:
            module.unlink()
        except Exception:
            pass
        print(f"  ✓ {rel} → {built.name}")
    return True


def restore() -> int:
    if not BACKUP.exists():
        print("没有备份可还原")
        return 1
    n = 0
    for p in BACKUP.rglob("*.py"):
        rel = p.relative_to(BACKUP)
        dst = SRC / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        n += 1
    # 删掉编译产物
    for p in list(SRC.rglob("*.so")) + list(SRC.rglob("*.pyd")):
        if p.name.startswith("pyarmor_runtime"):
            continue
        try:
            p.unlink()
        except Exception:
            pass
    print(f"已还原 {n} 个 .py，并清理编译产物")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="编译全部业务模块")
    ap.add_argument("--restore", action="store_true", help="从备份还原")
    args = ap.parse_args()

    if args.restore:
        return restore()

    mods = CORE + (EXTRA if args.all else [])
    # 开跑前统一刷新备份：即使某个模块编译失败、没走到“成功后再备份”，
    # 退出时的 --restore 也不会把旧代码盖回当前源码。
    BACKUP.mkdir(parents=True, exist_ok=True)
    for rel in mods:
        src = SRC / rel
        if src.exists():
            dst = BACKUP / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    print(f"Nuitka 加固：编译 {len(mods)} 个模块（Python {sys.version.split()[0]}）")
    ok = 0
    for rel in mods:
        if compile_one(rel):
            ok += 1
    print(f"\n完成：{ok}/{len(mods)}")
    print("提示：打包前请确认 tests 仍能跑（python tests/test_api_save.py）")
    return 0 if ok == len(mods) else 1


if __name__ == "__main__":
    raise SystemExit(main())
