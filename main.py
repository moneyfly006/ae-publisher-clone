"""AuroraList 入口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))


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
