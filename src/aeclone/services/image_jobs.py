"""图片处理方案（内部存储；界面只显示表格/勾选，不需要用户写 JSON）。

两类数据：
- 简单规则：`[{group, range, ops:[white|watermark|translate]}]`  → 按范围批量做简单处理
- 精细任务：`[{group, index, refs, prompt, mode, count, insert}]` → 单张图用提示词 + 多图素材编辑/生成

group: main=主图 / detail=描述图 / sku=SKU 图
"""
from __future__ import annotations

import json

from .image_index import parse_indices

GROUPS = ("main", "detail", "sku")
GROUP_LABELS = {"main": "主图", "detail": "描述图", "sku": "SKU 图"}
OPS = ("white", "watermark", "translate")
OP_LABELS = {"white": "白底", "watermark": "去水印", "translate": "翻译"}


def _load(raw, default):
    if isinstance(raw, type(default)):
        return raw
    try:
        data = json.loads(raw or "")
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def dumps(rows) -> str:
    return json.dumps(rows or [], ensure_ascii=False)


# ---------------- 简单规则 ----------------
def parse_rules(raw) -> list[dict]:
    rows = _load(raw, [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        group = str(r.get("group") or "main").lower()
        if group not in GROUPS:
            group = "main"
        ops = [o for o in (r.get("ops") or []) if o in OPS]
        if not ops:
            continue
        out.append({"group": group, "range": str(r.get("range") or "1-5"), "ops": ops})
    return out


def rules_to_rows(raw) -> list[dict]:
    return parse_rules(raw)


def rows_to_rules(rows) -> str:
    return dumps(parse_rules(rows))


# ---------------- 精细任务 ----------------
def parse_jobs(raw) -> list[dict]:
    rows = _load(raw, [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        group = str(r.get("group") or "main").lower()
        if group not in GROUPS:
            group = "main"
        mode = str(r.get("mode") or "edit").lower()
        if mode not in ("edit", "generate"):
            mode = "edit"
        try:
            index = max(1, int(r.get("index") or 1))
        except Exception:
            index = 1
        refs = r.get("refs")
        if isinstance(refs, str):
            refs_text = refs.strip()
        elif isinstance(refs, (list, tuple)):
            refs_text = ",".join(str(int(x)) for x in refs if str(x).strip().isdigit())
        else:
            refs_text = ""
        insert = str(r.get("insert") or "first").lower()
        if insert not in ("first", "last", "replace"):
            insert = "first"
        try:
            count = max(1, min(3, int(r.get("count") or 1)))
        except Exception:
            count = 1
        job = {"group": group, "index": index, "refs": refs_text,
               "prompt": str(r.get("prompt") or "").strip(), "mode": mode,
               "count": count, "insert": insert,
               "preset": str(r.get("preset") or "").strip()}
        # 本地参考图（老软件：图1~图N 为本地参考图，其后才是产品原图）
        rp = r.get("ref_paths")
        if isinstance(rp, str):
            paths = [x.strip() for x in rp.replace("；", ";").split(";") if x.strip()]
        elif isinstance(rp, (list, tuple)):
            paths = [str(x).strip() for x in rp if str(x).strip()]
        else:
            paths = []
        job["ref_paths"] = paths[:5]
        if not job["prompt"]:
            continue
        out.append(job)
    return out


def jobs_to_rows(raw) -> list[dict]:
    return parse_jobs(raw)


def rows_to_jobs(rows) -> str:
    return dumps(parse_jobs(rows))


def refs_indices(refs, total: int, exclude: int | None = None) -> list[int]:
    text = str(refs or "").strip().lower()
    if total <= 0:
        return []
    if text in ("all", "*", "全部"):
        idx = list(range(1, total + 1))
    elif not text:
        idx = []
    else:
        idx = parse_indices(text, total)
    return [i for i in idx if i != exclude]


# ---------------- 旧配置迁移 ----------------
def migrate(cfg, with_global: bool = False) -> tuple[str, str]:
    """把老配置（逐图编辑方案 / 指定编辑 / 全局开关）迁到新表，返回 (rules_json, jobs_json)。

    只在用户没有新表时执行一次，由界面写回。
    """
    # 自动修复：开了「AI 生成主图」但提示词为空 = 误开，直接当作未启用，
    # 避免它每次都在计划里出现「提示词为空」吓到用户。
    try:
        if (getattr(cfg, "main_image_generate_enable", False)
                and not (getattr(cfg, "main_image_generate_prompt", "") or "").strip()):
            cfg.set("main_image_generate_enable", False)
    except Exception:
        pass

    rules = parse_rules(getattr(cfg, "image_simple_rules", "[]"))
    jobs = parse_jobs(getattr(cfg, "image_ai_jobs", "[]"))
    if rules or jobs:
        return rows_to_rules(rules), rows_to_jobs(jobs)

    # 1) 老「指定图片编辑」→ 精细任务（主图）
    try:
        if getattr(cfg, "designated_edit_enable", False):
            idx_expr = str(getattr(cfg, "designated_edit_indices", "") or "")
            prompt = str(getattr(cfg, "designated_edit_prompt", "") or "").strip()
            if prompt:
                for i in parse_indices(idx_expr, 12):
                    jobs.append({"group": "main", "index": i, "refs": "all", "prompt": prompt,
                                 "mode": "edit", "count": 1, "insert": "replace"})
    except Exception:
        pass
    # 2) 老「逐图编辑方案」（slot）；提示词为空时用全局指定编辑提示词
    default_prompt = str(getattr(cfg, "designated_edit_prompt", "") or "").strip()
    try:
        old = _load(getattr(cfg, "image_edit_plans", "[]"), [])
        for s in old if isinstance(old, list) else []:
            if not isinstance(s, dict):
                continue
            group = str(s.get("group") or "main")
            prompt = str(s.get("prompt") or "").strip() or default_prompt
            if not prompt:
                continue
            jobs.append({"group": group, "index": int(s.get("target") or 1),
                         "refs": str(s.get("refs") or ""), "prompt": prompt,
                         "mode": str(s.get("mode") or "edit"),
                         "count": 1,
                         "insert": "last" if s.get("add_new") else "replace"})
    except Exception:
        pass
    # 3) 老全局简单开关 → 简单规则（仅界面迁移时做，避免和旧流程重复处理）
    if not with_global:
        return rows_to_rules(rules), rows_to_jobs(jobs)
    try:
        if getattr(cfg, "white_bg_enable", False) or getattr(cfg, "watermark_enable", False):
            ops = []
            if getattr(cfg, "white_bg_enable", False):
                ops.append("white")
            if getattr(cfg, "watermark_enable", False):
                ops.append("watermark")
            if ops:
                rng = f"{getattr(cfg, 'white_background_pic_1', '1')}-{getattr(cfg, 'white_background_pic_2', '3')}"
                rules.append({"group": "main", "range": rng, "ops": ops})
        if getattr(cfg, "img_detail_white_bg", False) or getattr(cfg, "img_detail_remove_wm", False):
            ops = []
            if getattr(cfg, "img_detail_white_bg", False):
                ops.append("white")
            if getattr(cfg, "img_detail_remove_wm", False):
                ops.append("watermark")
            if ops:
                rules.append({"group": "detail",
                              "range": str(getattr(cfg, "white_background_detail_pic_indices", "") or "1-10"),
                              "ops": ops})
        if getattr(cfg, "white_background_sku_pic", False) and (
                getattr(cfg, "img_sku_white_bg", False) or getattr(cfg, "img_sku_remove_wm", False)):
            ops = []
            if getattr(cfg, "img_sku_white_bg", False):
                ops.append("white")
            if getattr(cfg, "img_sku_remove_wm", False):
                ops.append("watermark")
            if ops:
                rules.append({"group": "sku",
                              "range": str(getattr(cfg, "white_background_sku_pic_indices", "")
                                           or "1-20"),
                              "ops": ops})
    except Exception:
        pass
    
    # 4) 四合一：**不迁移成 AI 任务**
    #    四合一由 publisher 里的本地 PIL 拼图完成（免费、确定、无张数限制），
    #    迁移成 AI job 会额外花钱，而且生成结果不可控。
    
    return rows_to_rules(rules), rows_to_jobs(jobs)
