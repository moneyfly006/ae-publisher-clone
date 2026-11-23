"""Alibaba 采集箱批量处理：参考速卖通流程，直接改/发采集箱商品，不需要逐个手选。"""
from __future__ import annotations

import json

from .alibaba_payload import match_attribute_value
from .alibaba_pipeline import run_alibaba_product
from .alibaba_publish import OP_SAVE, make_client

_PREFERRED = ("Other", "All", "China", "No", "None", "Not Applicable", "Standard")


def _prepare_overrides(client, row, cfg) -> dict:
    """为批量商品准备：保留自己的类目 + 自动补齐必填属性到合法候选值。

    采集箱商品来自不同类目，不能套用单一类目预设；这里逐商品读取必填属性，
    按「预设值 → 商品原值 → 通用候选 → 第一个候选」选合法值，保证能保存草稿。
    """
    pid = str(row.get("id") or "")
    cat = str(row.get("categoryId") or "").strip()
    full = str(row.get("fullCid") or "").strip().replace("/", ",")
    attrs_out: dict[str, str] = {}
    if not pid or not cat:
        return {"category_id": cat, "full_cid": full}
    try:
        detail = client.product_detail(pid)
    except Exception:
        detail = {}
    _own_kw = str(detail.get("keywords") or "").strip()

    try:
        required = client.required_attributes(cat)
    except Exception:
        required = []
    try:
        presets = json.loads(str(getattr(cfg, "alibaba_attr_presets", "") or "{}"))
        presets = {str(k).strip().lower(): str(v).strip()
                   for k, v in presets.items()} if isinstance(presets, dict) else {}
    except Exception:
        presets = {}
    have: dict[str, str] = {}
    try:
        for a in (detail.get("attributes") or []):
            if isinstance(a, dict):
                nm = str(a.get("attribute_name") or "").strip()
                if nm:
                    have[nm.lower()] = str(a.get("value_name") or "").strip()
    except Exception:
        pass
    for d in required or []:
        name = str(d.get("nameEn") or d.get("nameZh") or "").strip()
        if not name:
            continue
        cands = []
        for c in (d.get("values") or []):
            if isinstance(c, dict):
                nm = str(c.get("name_en") or c.get("name") or "").strip()
                if nm:
                    cands.append(nm)
        val = presets.get(name.lower()) or have.get(name.lower()) or ""
        canon = match_attribute_value(d, val) if val else ""
        if not canon:
            for pref in _PREFERRED:
                for c in cands:
                    if pref.lower() in c.lower():
                        canon = c
                        break
                if canon:
                    break
        if not canon and cands:
            canon = cands[0]
        if canon:
            attrs_out[name] = canon
    out = {"category_id": cat, "full_cid": full, "attributes": attrs_out}
    # 不能把全局推荐关键词套到所有类目商品上（会把泳池商品写成 machinery）；
    # 有商品自己的英文关键词时优先用它。少于 3 个的由流水线报告，不静默覆盖。
    if _own_kw:
        out["keywords"] = _own_kw
    return out


def list_collect_products(client, page_size: int = 100, state: str = "draft") -> list[dict]:
    """读取批量来源商品：采集箱（draft）或待发布（waitPublish）。"""
    d = client.list_products(1, page_size, state or "draft")
    po = ((d.get("data") or {}).get("page") or {})
    return [r for r in (po.get("list") or []) if str(r.get("id") or "").strip()]


def run_collect_batch(cfg, *, op: str = OP_SAVE, limit: int = 10,
                      use_ai: bool = False, client=None,
                      progress=None, only_current_category: bool = False,
                      current_category: str | None = None,
                      state: str = "draft") -> list[dict]:
    """对采集箱商品逐个真实保存/发布；返回每个商品的结果。

    progress(index, total, record) 可用于 UI 实时显示；异常只记录在该商品，不中途中止整批。
    """
    c = client or make_client(cfg)
    _cfg_cat = str(current_category or
                   getattr(cfg, "alibaba_batch_category_id", "") or
                   getattr(cfg, "alibaba_category_id", "") or "").strip()
    rows = list_collect_products(c, state=state)
    rows = rows[:max(0, int(limit or 0))] if limit else rows
    total = len(rows)
    out: list[dict] = []
    for i, row in enumerate(rows, 1):
        pid = str(row.get("id") or "")
        subject = str(row.get("subject") or "")
        _cat = str(row.get("categoryId") or "").strip()
        if str(op) == "3" and state != "draft":
            rec = {"id": pid, "subject": subject, "ok": False, "saved": False,
                   "auditing": False, "score": 0.0, "skipped": True,
                   "error": "「移入待发布」只适用于采集箱商品；待发布/在线商品不能执行 op=3",
                   "saved_msg": ""}
            out.append(rec)
            if progress:
                try:
                    progress(i, total, rec)
                except Exception:
                    pass
            continue
        if only_current_category and _cfg_cat and _cat and _cat != _cfg_cat:
            rec = {"id": pid, "subject": subject, "ok": False, "saved": False,
                   "auditing": False, "score": 0.0, "skipped": True,
                   "error": (f"类目 {_cat} 与当前推荐类目 {_cfg_cat} 不同，已跳过"
                             "（如需处理请关闭「只处理当前推荐类目」）"),
                   "saved_msg": ""}
            out.append(rec)
            if progress:
                try:
                    progress(i, total, rec)
                except Exception:
                    pass
            continue
        try:
            # 批量处理保留每个商品自己的类目，并按该类目自动补齐必填属性。
            ov = _prepare_overrides(c, row, cfg)
            res = run_alibaba_product(pid, cfg, dry_run=False, op=str(op),
                                      use_ai=use_ai, client=c, overrides=ov,
                                      repair_score=bool(getattr(cfg, "alibaba_collect_auto_repair", True)))
            rec = {"id": pid, "subject": subject, "ok": bool(res.ok),
                   "saved": bool(res.saved), "auditing": bool(getattr(res, "auditing", False)),
                   "score": float(getattr(res, "score", 0) or 0),
                   "autofilled": sorted((ov.get("attributes") or {}).keys()),
                   "skipped": False,
                   "error": str(res.error or ""),
                   "saved_msg": str(((res.save_result or {}).get("data") or {}).get("msg") or "")}
        except Exception as ex:  # noqa: BLE001
            rec = {"id": pid, "subject": subject, "ok": False, "saved": False,
                   "auditing": False, "score": 0.0,
                   "error": f"{type(ex).__name__}: {str(ex)[:160]}", "saved_msg": ""}
        out.append(rec)
        if progress:
            try:
                progress(i, total, rec)
            except Exception:
                pass
    return out


def precheck_collect_batch(cfg, *, limit: int = 10, client=None, progress=None,
                           only_current_category: bool = False,
                           current_category: str | None = None,
                           state: str = "draft") -> list[dict]:
    """发布前评分预检：只读评分/校验，不发任何写请求、不花 AI 费用。"""
    c = client or make_client(cfg)
    _cfg_cat = str(current_category or
                   getattr(cfg, "alibaba_batch_category_id", "") or
                   getattr(cfg, "alibaba_category_id", "") or "").strip()
    rows = list_collect_products(c, state=state)
    rows = rows[:max(0, int(limit or 0))] if limit else rows
    total = len(rows)
    out: list[dict] = []
    for i, row in enumerate(rows, 1):
        pid = str(row.get("id") or "")
        subject = str(row.get("subject") or "")
        cat = str(row.get("categoryId") or "").strip()
        if only_current_category and _cfg_cat and cat and cat != _cfg_cat:
            rec = {"id": pid, "subject": subject, "ok": False, "saved": False,
                   "skipped": True, "score": 0.0, "precheck": True,
                   "error": f"类目 {cat} 与目标类目 {_cfg_cat} 不同，已跳过"}
        else:
            try:
                ov = _prepare_overrides(c, row, cfg)
                res = run_alibaba_product(pid, cfg, dry_run=True, op="2",
                                          use_ai=False, client=c, overrides=ov)
                rec = {"id": pid, "subject": subject, "ok": bool(res.ok),
                       "saved": False, "skipped": False, "precheck": True,
                       "score": float(getattr(res, "score", 0) or 0),
                       "error": str(res.error or "")}
            except Exception as ex:  # noqa: BLE001
                rec = {"id": pid, "subject": subject, "ok": False, "saved": False,
                       "skipped": False, "precheck": True, "score": 0.0,
                       "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
        out.append(rec)
        if progress:
            try:
                progress(i, total, rec)
            except Exception:
                pass
    return out
