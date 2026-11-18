"""命令行自检：不打开界面，直接把环境/配置/依赖的状态打出来。

用途：
  - 打包后的程序在别人机器上跑不起来时，让对方执行 `AuroraList --self-check`，
    把输出发过来就能定位（比"打不开"有用得多）。
  - 平时排查也能用：`python main.py --self-check`

只读：不改配置、不联网、不碰店铺数据。
"""
from __future__ import annotations

import importlib
import os
import platform
import sys
from pathlib import Path


def _line(ok, label, detail=""):
    icon = "✓" if ok is True else ("✗" if ok is False else "·")
    return f"{icon} {label}" + (f"：{detail}" if detail else "")


def run(stream=None) -> int:
    """打印自检报告；返回退出码（0 全通过 / 1 有致命项）。"""
    out = stream or sys.stdout
    fatal = 0
    buf: list[str] = []

    def p(s=""):
        buf.append(str(s))
        try:
            print(s, file=out)
        except Exception:
            pass

    p("=" * 60)
    p("AuroraList 自检")
    p("=" * 60)

    # ---- 运行环境 ----
    p("\n[运行环境]")
    p(_line(True, "Python", f"{sys.version.split()[0]} ({platform.machine()})"))
    p(_line(True, "系统", f"{platform.system()} {platform.release()}"))
    p(_line(True, "程序路径", str(Path(sys.argv[0]).resolve())))
    p(_line(True, "打包运行" if getattr(sys, "frozen", False) else "源码运行"))

    # ---- 关键依赖 ----
    p("\n[关键依赖]")
    for mod, label in (("flet", "Flet 界面"),
                       ("requests", "requests"), ("PIL", "Pillow"),
                       ("Crypto", "pycryptodome")):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "") or ""
            p(_line(True, label, ver))
        except Exception as ex:  # noqa: BLE001
            p(_line(False, label, f"导入失败：{ex}"))
            fatal += 1

    # ---- 加密子模块（打包最容易漏）----
    p("\n[加密子模块（打包常见坑）]")
    # 只查程序真正用到的（license.py / chrome_cookies.py）。
    # pycryptodome 的 _ec_nat 等私有扩展在源码模式下本来就不能直接 import，
    # 拿它当检查项会误报。
    for mod in ("Crypto.Cipher.AES", "Crypto.Hash.SHA256", "Crypto.PublicKey.ECC",
                "Crypto.Signature.DSS", "Crypto.Protocol.KDF"):
        try:
            importlib.import_module(mod)
            p(_line(True, mod))
        except Exception as ex:  # noqa: BLE001
            p(_line(False, mod, str(ex)[:60]))
            fatal += 1

    # ---- 配置 ----
    p("\n[配置]")
    try:
        from .config import AppConfig, config_dir
        path = AppConfig.path()
        p(_line(True, "配置文件", str(path)))
        p(_line(path.exists(), "文件存在", "是" if path.exists() else "否（首次运行会新建）"))
        cfg = AppConfig.load()
        p(_line(True, "加载来源", getattr(cfg, "_loaded_from", "file")))
        key = (getattr(cfg, "api_key", "") or "").strip()
        p(_line(bool(key), "API Key", f"已填（{len(key)} 字符）" if key else "未填 → AI 功能会全部失败"))
        p(_line(True, "API 地址", str(getattr(cfg, "api_base", ""))))
        p(_line(True, "店铺", str(getattr(cfg, "store_name", "") or getattr(cfg, "store_id", "") or "未选")))
        corrupts = sorted(config_dir().glob("config.corrupt-*.json"))
        if corrupts:
            p(_line(False, "发现损坏配置备份", corrupts[-1].name))
    except Exception as ex:  # noqa: BLE001
        p(_line(False, "读取配置失败", str(ex)[:80]))
        fatal += 1

    # ---- 图片/编辑器链路自检（纯静态）----
    p("\n[模块链路]")
    for mod in ("aeclone.services.publisher", "aeclone.services.image_jobs",
                "aeclone.services.preflight"):
        try:
            importlib.import_module(mod)
            p(_line(True, mod))
        except Exception as ex:  # noqa: BLE001
            p(_line(False, mod, str(ex)[:60]))
            fatal += 1

    # ---- 配置体检 ----
    p("\n[配置体检]")
    try:
        from .services.preflight import check
        from .config import AppConfig as _C
        issues = check(_C.load())
        if not issues:
            p(_line(True, "没有发现问题"))
        for i in issues:
            p(f"  {i.line()}")
            if i.level == "error":
                fatal += 1
    except Exception as ex:  # noqa: BLE001
        p(_line(False, "体检失败", str(ex)[:80]))

    # ---- 结论 ----
    p("\n" + "=" * 60)
    if fatal:
        p(f"结论：发现 {fatal} 个需要处理的问题（见上面 ✗）")
    else:
        p("结论：环境正常，可以启动")
    p("=" * 60)

    # 打包成窗口程序（console=False）时终端看不到输出，所以同时落一份文件。
    try:
        from .config import config_dir
        dst = Path(config_dir()) / "last-selfcheck.txt"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join(buf) + "\n", encoding="utf-8")
        try:
            print(f"（报告已保存：{dst}）", file=out)
        except Exception:
            pass
    except Exception:
        pass

    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(run())
