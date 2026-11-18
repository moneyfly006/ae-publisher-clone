"""AuroraList 入口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))


def _ensure_ca_bundle() -> None:
    """保证 TLS 根证书可用。

    打包后 certifi 的 cacert.pem 偶尔会缺失（例如重建时正在运行），
    这时退回系统证书库，避免所有 HTTPS 直接报
    "Could not find a suitable TLS CA certificate bundle"。
    """
    import os as _os
    try:
        import certifi
        if _os.path.exists(certifi.where()):
            return
    except Exception:
        pass
    for _cand in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem",
                  "/opt/homebrew/etc/openssl@3/cert.pem",
                  r"C:\Windows\system32\certmgr.msc"):
        if _os.path.exists(_cand):
            _os.environ.setdefault("REQUESTS_CA_BUNDLE", _cand)
            _os.environ.setdefault("SSL_CERT_FILE", _cand)
            return


_ensure_ca_bundle()


def _is_selfcheck(argv: list[str]) -> bool:
    flags = {"--self-check", "--selfcheck", "-c", "--check"}
    return any(a in flags for a in argv[1:])


if __name__ == "__main__":
    if _is_selfcheck(sys.argv):
        # 命令行自检：不打开界面，只打印环境/配置状态
        from aeclone.selfcheck import run as _selfcheck

        raise SystemExit(_selfcheck())

    from aeclone.ui.app import run  # noqa: E402

    run()
