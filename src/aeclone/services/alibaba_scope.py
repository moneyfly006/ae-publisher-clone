"""Alibaba 经营类目范围预检。

平台发布前的硬校验之一是「商品类目是否在店铺经营类目范围内」：
    PUB_BIZCHECK_CAT_PUB_RESTRICT: 当前商品类目不在线上经营类目范围内...
这个状态只能由卖家中心「店铺管理 → 管理公司信息 → 经营类目」决定，
dxm/ICBU 接口没有提供查询经营类目清单的 API。

可用信号：**在线商品一定已经通过该类目校验**。因此扫描在线商品的 categoryId
做近似预检：
- 目标类目在样本中出现 → 大概率可发（仍以平台校验为准）；
- 未出现 → 提醒用户去卖家中心确认经营类目，避免发布后才发现被拒。
"""
from __future__ import annotations

from dataclasses import dataclass, field

RESTRICT_CODE = "PUB_BIZCHECK_CAT_PUB_RESTRICT"

CUSTOMER_ADVICE = (
    "经营类目是平台店铺资质限制：请到 Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → "
    "经营类目，把该商品所属大类/末级类目加入经营范围后再发布。"
    "软件无法代替平台开通经营类目。"
)


@dataclass
class CategoryScopeResult:
    category_id: str = ""
    in_scope_guess: bool = False
    evidence_count: int = 0
    scanned: int = 0
    total: int | None = None
    sample_categories: dict = field(default_factory=dict)
    advice: str = CUSTOMER_ADVICE
    detail: str = ""

    @property
    def coverage(self) -> float:
        try:
            if self.total and self.total > 0:
                return min(1.0, self.scanned / float(self.total))
        except Exception:
            pass
        return 0.0

    def message(self) -> str:
        if self.in_scope_guess:
            return (f"类目 {self.category_id} 在线商品样本中出现 {self.evidence_count} 次"
                    f"（扫描 {self.scanned}/{self.total or '?'} 个在线商品），可发可能性高")
        return (f"类目 {self.category_id} 未在 {self.scanned} 个在线商品类目样本中出现，"
                f"可能不在经营类目范围；若发布报 {RESTRICT_CODE}，需按提示开类目")


def check_category_scope(client, category_id: str, max_pages: int = 10) -> CategoryScopeResult:
    """只读预检：不发布、不写店铺。"""
    cid = str(category_id or "").strip()
    res = CategoryScopeResult(category_id=cid)
    if not cid or cid.lower() in ("none", "null"):
        res.detail = "未选择类目，无法预检经营类目范围"
        return res
    try:
        scope = client.online_category_scope(max_pages=max_pages)
    except Exception as ex:  # noqa: BLE001
        res.detail = f"经营类目预检失败（不影响后续保存，仅提示）：{type(ex).__name__}: {str(ex)[:100]}"
        return res
    cats = scope.get("categories") or {}
    res.sample_categories = cats
    res.scanned = int(scope.get("scanned") or 0)
    res.total = scope.get("total")
    res.evidence_count = int(cats.get(cid) or 0)
    res.in_scope_guess = res.evidence_count > 0
    res.detail = res.message()
    # 覆盖不足时降低「不在范围」结论的强度
    if not res.in_scope_guess and res.scanned and res.total and res.coverage < 0.2:
        res.detail += f"（样本覆盖率仅 {res.coverage:.0%}，结论仅供参考）"
    return res
