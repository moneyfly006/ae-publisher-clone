"""图片 API 瞬时 524/超时不应导致整件商品失败。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.collect_automation import _transient_image_error  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    check("HTTP 524 可软跳过", _transient_image_error("ApiError: HTTP 524"))
    check("Read timed out 可软跳过", _transient_image_error("Read timed out"))
    check("普通校验错误仍失败", not _transient_image_error("图片格式不支持"))
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
