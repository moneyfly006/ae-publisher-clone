"""逐图编辑方案（按图片位置指定提示词与参考图）。

一条方案（slot）= 「对某组里的第 N 张图，用一组参考图 + 提示词做编辑或生成」：

    {"group": "main", "target": 1, "refs": "1,2,3", "prompt": "...",
     "mode": "edit", "add_new": false}

- group : main=主图 / detail=描述图 / sku=SKU 图
- target: 组内第几张（1 开始）；add_new=true 时忽略（结果追加为新图）
- refs  : "all" = 该组全部图片；"1,2,3-5" = 指定序号（自动排除 target）
- mode  : edit=以 target 为主图做编辑（多图作素材）；generate=以 refs（或 target）为素材生成新图
- prompt: 这一张图的提示词（留空则用全局 designated_edit_prompt）
"""
from __future__ import annotations

import json

from .image_index import parse_indices

GROUPS = ("main", "detail", "sku")
MODES = ("edit", "generate")
GROUP_LABELS = {"main": "主图", "detail": "描述图", "sku": "SKU 图"}


def parse(raw) -> list[dict]:
    """把配置里的 JSON（或列表）解析成规范化的方案列表。"""
    if isinstance(raw, str):
        text = (raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
    elif isinstance(raw, list):
        data = raw
    else:
        return []
    if not isinstance(data, list):
        return []
    return [s for s in (normalize(x) for x in data) if s]


def normalize(slot: dict) -> dict | None:
    if not isinstance(slot, dict):
        return None
    group = str(slot.get("group") or "main").strip().lower()
    if group not in GROUPS:
        group = "main"
    mode = str(slot.get("mode") or "edit").strip().lower()
    if mode not in MODES:
        mode = "edit"
    try:
        target = int(slot.get("target") or 1)
    except Exception:
        target = 1
    out = {
        "group": group,
        "target": max(1, target),
        "refs": str(slot.get("refs") or "").strip(),
        "prompt": str(slot.get("prompt") or "").strip(),
        "mode": mode,
        "add_new": bool(slot.get("add_new")),
    }
    if not out["prompt"] and not out["refs"] and not out["add_new"]:
        # 既没提示词也没参考图、也不追加：等同空方案，丢弃
        return None
    return out


def dumps(slots: list[dict]) -> str:
    return json.dumps([s for s in (normalize(x) for x in (slots or [])) if s], ensure_ascii=False)


def refs_indices(refs: str, total: int, exclude: int | None = None) -> list[int]:
    """解析参考图序号："all" 或 "1,3-4"。exclude 为 1 基的 target 序号。"""
    text = (refs or "").strip().lower()
    if total <= 0:
        return []
    if text in ("all", "*", "全部"):
        idx = list(range(1, total + 1))
    elif not text:
        idx = []
    else:
        idx = parse_indices(text, total)
    return [i for i in idx if i != exclude]


def describe(slots: list[dict]) -> list[str]:
    """界面 / 日志用的可读描述。"""
    out = []
    for s in slots:
        label = GROUP_LABELS.get(s["group"], s["group"])
        refs = s["refs"] or ("all" if s["mode"] == "generate" else "-")
        where = f"{label} +新图" if s["add_new"] else f"{label} 第{s['target']}张"
        mode = "编辑" if s["mode"] == "edit" else "生成"
        out.append(f"{where} · {mode} · 参考 {refs} · {(s['prompt'] or '')[:18]}")
    return out
