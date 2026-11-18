"""店小秘「图片空间」清理（ISSUES #5）。

为什么需要：
- 图片空间满了以后上传会失败（刊登本身还能用外链，但长期会越来越满）；
- 店小秘相册页提供三类能力，这里按「安全 → 激进」封装：
  1) 统计：图片/PDF 数量与占用；
  2) 自动清理：删除「N 天前上传、且没有被任何商品引用」的图片（最安全，站点侧异步任务）；
  3) 按时间批量删除：删除「N 天前上传」的图片（已锁定图片站点会自动跳过，但仍需用户确认）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpaceInfo:
    images: int = 0
    pdfs: int = 0
    brief: str = ""


@dataclass
class CleanupCandidate:
    id: str
    name: str = ""
    url: str = ""
    size: int = 0
    locked: bool = False
    created: str = ""


@dataclass
class CleanupPlan:
    days: int
    candidates: list[CleanupCandidate] = field(default_factory=list)
    scanned: int = 0
    locked: int = 0
    total: int = 0
    note: str = ""

    @property
    def delete_count(self) -> int:
        return len([c for c in self.candidates if not c.locked and c.id])

    @property
    def delete_size(self) -> int:
        return sum(int(c.size or 0) for c in self.candidates if not c.locked and c.id)

    def brief(self) -> str:
        return (f"扫描 {self.scanned} 张 · 可删 {self.delete_count} 张"
                f"（{human_size(self.delete_size)}）· 锁定跳过 {self.locked} 张")


def human_size(num) -> str:
    try:
        n = float(num or 0)
    except Exception:
        return "0M"
    if n <= 0:
        return "0M"
    mb = n / (1024 * 1024)
    if mb < 1:
        return f"{n / 1024:.0f}K"
    if mb < 1024:
        return f"{mb:.1f}M"
    return f"{mb / 1024:.2f}G"


def space(client) -> SpaceInfo:
    """图片/PDF 数量统计；接口不可用时返回空统计而不是抛错。"""
    try:
        data = client.album_counts() or {}
    except Exception:
        data = {}
    info = SpaceInfo(images=int(data.get("images") or 0), pdfs=int(data.get("pdfs") or 0))
    info.brief = f"图片 {info.images} 张 · PDF {info.pdfs} 个"
    return info


def to_candidates(files: list[dict]) -> list[CleanupCandidate]:
    """把 /api/album/list.json 的条目转成候选对象（保留锁定标记）。"""
    out = []
    for it in files or []:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("id") or "")
        if not cid:
            continue
        try:
            size = int(it.get("size") or 0)
        except Exception:
            size = 0
        out.append(CleanupCandidate(id=cid, name=str(it.get("name") or ""),
                                    url=str(it.get("url") or ""), size=size,
                                    locked=bool(it.get("locked")),
                                    created=str(it.get("created") or "")))
    return out


def scan(client, days: int = 30, full_cid: str = "", page_size: int = 100,
         max_pages: int = 20) -> CleanupPlan:
    """扫描「N 天前上传」的图片（只读，不删除）。"""
    import datetime as _dt
    try:
        days = max(0, int(days))
    except Exception:
        days = 30
    end = (_dt.datetime.now() - _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    plan = CleanupPlan(days=days)
    page = 1
    while page <= max_pages:
        try:
            items, meta = client.album_files(page=page, size=page_size,
                                             start_time="2000-01-01 00:00:00",
                                             end_time=end, full_cid=full_cid, file_type=0)
        except Exception as ex:  # noqa: BLE001
            plan.note = f"扫描失败：{str(ex)[:120]}"
            break
        cands = to_candidates(items)
        plan.candidates.extend(cands)
        plan.scanned += len(cands)
        plan.locked += len([c for c in cands if c.locked])
        if not items or page >= int(meta.get("total") or 0) / max(1, page_size):
            break
        page += 1
    plan.total = plan.scanned
    if not plan.note:
        plan.note = plan.brief()
    return plan


def delete_candidates(client, plan: CleanupPlan, batch: int = 50) -> dict:
    """删除扫描出的未锁定图片，返回 {ok, deleted, failed, errors}。"""
    ids = [c.id for c in plan.candidates if not c.locked and c.id]
    deleted = failed = 0
    errors: list[str] = []
    for i in range(0, len(ids), max(1, batch)):
        chunk = ids[i:i + batch]
        try:
            client.album_delete_ids(chunk)
            deleted += len(chunk)
        except Exception as ex:  # noqa: BLE001
            failed += len(chunk)
            errors.append(str(ex)[:120])
    return {"ok": failed == 0 and deleted > 0, "deleted": deleted,
            "failed": failed, "errors": errors[:3]}


def delete_ids(client, ids: list[str]) -> dict:
    """按选中的图片 id 删除（最安全可控；已锁定图片站点会跳过）。"""
    ids = [str(x) for x in (ids or []) if str(x).strip()]
    if not ids:
        return {"ok": False, "deleted": 0, "failed": 0, "errors": ["没有选中图片"]}
    deleted = failed = 0
    errors: list[str] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            client.album_delete_ids(chunk)
            deleted += len(chunk)
        except Exception as ex:  # noqa: BLE001
            failed += len(chunk)
            errors.append(str(ex)[:120])
    return {"ok": failed == 0 and deleted > 0, "deleted": deleted,
            "failed": failed, "errors": errors[:3]}


def delete_older_than(client, days: int = 30, full_cid: str = "") -> dict:
    """按时间条件批量删除（站点侧一次完成，已锁定图片自动跳过）。"""
    try:
        client.album_delete_before(int(days), full_cid=full_cid)
        return {"ok": True, "message": f"已提交删除：{days} 天前的图片"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "message": f"删除失败：{str(ex)[:140]}"}


def auto_clean(client, days: int = 30) -> dict:
    """创建「删除未引用图片」任务（最安全：被商品引用的图片不会删）。"""
    try:
        client.album_auto_delete(int(days))
        return {"ok": True, "message": f"已创建清理任务：删除 {days} 天前上传且未被引用的图片"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "message": f"创建清理任务失败：{str(ex)[:140]}"}


# 店小秘这个接口在不少账号上直接回「功能暂未开放！」（老软件也踩过，见 ISSUES #37）
_UNAVAILABLE_MARKS = ("功能暂未开放", "暂未开放", "not open", "not available", "unsupported")


def maybe_auto_clean(cfg, client) -> dict:
    """开关 album_auto_clean_enable：刊登完成后创建「删未引用图片」任务。

    days=None 时读配置 album_auto_clean_days。
    接口不可用（店小秘「功能暂未开放」）时返回 unavailable=True 并给出替代做法，
    **不会退化成乱删**。
    """
    if not getattr(cfg, "album_auto_clean_enable", False):
        return {"ok": False, "skipped": True, "message": "未开启自动清理"}
    try:
        days = int(getattr(cfg, "album_auto_clean_days", 7) or 7)
    except Exception:
        days = 7
    r = auto_clean(client, days)
    msg = str(r.get("message") or "")
    if not r.get("ok") and any(m in msg for m in _UNAVAILABLE_MARKS):
        return {
            "ok": False, "unavailable": True,
            "message": ("店小秘的「自动清理未引用图片」接口当前不可用（功能暂未开放）；"
                        "请改用「图片工作台 → 清理旧图」手动扫描删除"),
        }
    return r


def latest_task(client) -> dict:
    try:
        data = client.album_auto_delete_latest() or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
