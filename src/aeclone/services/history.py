"""任务历史与统计（JSONL 持久化）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(kind: str, ok: int, failed: int, detail: str = "") -> None:
    entry = {"ts": int(time.time()), "kind": kind, "ok": ok, "failed": failed, "detail": detail[:300]}
    with open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent(n: int = 50) -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[::-1]


def stats(days: int = 1) -> dict:
    cutoff = time.time() - days * 86400
    rows = [r for r in recent(5000) if r.get("ts", 0) >= cutoff]
    return {
        "runs": len(rows),
        "ok": sum(r.get("ok", 0) for r in rows),
        "failed": sum(r.get("failed", 0) for r in rows),
    }


# ---------------- 成本/用量统计 ----------------
def record_cost(kind: str, model: str, cost: float, detail: str = "") -> None:
    """记录一次 AI 调用成本（kind 如 图片编辑/标题/营销图）。"""
    if not cost or cost <= 0:
        return
    entry = {"ts": int(time.time()), "kind": "cost", "stage": str(kind)[:40],
             "model": str(model)[:60], "cost": round(float(cost), 6),
             "detail": str(detail)[:120]}
    try:
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cost_stats(days: int = 1) -> dict:
    """按时间窗口聚合成本：总花费、调用次数、按功能/模型拆分。"""
    cutoff = time.time() - days * 86400
    rows = [r for r in recent(5000) if r.get("ts", 0) >= cutoff and r.get("kind") == "cost"]
    by_stage: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    total = 0.0
    for r in rows:
        c = float(r.get("cost") or 0)
        total += c
        st = str(r.get("stage") or "-")
        md = str(r.get("model") or "-")
        by_stage.setdefault(st, {"n": 0, "cost": 0.0})
        by_stage[st]["n"] += 1
        by_stage[st]["cost"] += c
        by_model.setdefault(md, {"n": 0, "cost": 0.0})
        by_model[md]["n"] += 1
        by_model[md]["cost"] += c
    return {"calls": len(rows), "total": round(total, 4),
            "by_stage": dict(sorted(by_stage.items(), key=lambda kv: -kv[1]["cost"])),
            "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]))}
