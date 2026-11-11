"""AE Publisher Clone 入口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aeclone.ui.app import run  # noqa: E402

if __name__ == "__main__":
    run()
