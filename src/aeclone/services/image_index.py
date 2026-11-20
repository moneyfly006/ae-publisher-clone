"""图片索引表达式解析：支持 "1-3,5" / "1,2,3" / 空。"""
from __future__ import annotations


def parse_indices(expr: str, total: int) -> list[int]:
    expr = (expr or "").strip()
    if not expr:
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            for i in range(lo, hi + 1):
                out.add(i)
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return sorted(i for i in out if 1 <= i <= total)


def pick(urls: list[str], expr: str) -> list[str]:
    idx = parse_indices(expr, len(urls))
    return [urls[i - 1] for i in idx if 0 <= i - 1 < len(urls)]
