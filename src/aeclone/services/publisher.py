"""发布流水线：产品 → 属性/变种 → AI 文本 → 图片 → 价格 → 上传 → 保存。

默认 dry-run：只做规划与本地计算，不写店小秘；真实保存需显式 dry_run=False
且店小秘接口已在 dianxiaomi.ENDPOINTS 校正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import calc_free_shipping, calc_multiplier
from .variants import auto_map, check_consistency, limit_skus


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PublishResult:
    product_id: str
    ok: bool
    steps: list[Step] = field(default_factory=list)
    error: str = ""

    def log(self) -> str:
        lines = [f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}" for s in self.steps]
        return "\n".join(lines)


class Publisher:
    def __init__(self, cfg, dxm=None, text_service=None, image_service=None):
        self.cfg = cfg
        self.dxm = dxm
        self.text = text_service
        self.image = image_service

    # ---------- 单步 ----------
    def _normalize(self, product: dict) -> dict:
        p = dict(product)
        p["title"] = (p.get("title") or "").strip()
        p["skus"] = p.get("skus") or []
        p["images"] = p.get("images") or []
        return p

    def _map_variants(self, p: dict) -> dict:
        attrs = [a.get("name", "") for a in (p.get("attributes") or []) if isinstance(a, dict)]
        target = [a.get("name", "") for a in (p.get("target_attributes") or [])]
        mapping = auto_map(attrs, target) if target else {}
        p["attr_mapping"] = mapping
        p["skus"] = limit_skus(p["skus"], int(self.cfg.max_sku_count or 256))
        required = [a.get("name", "") for a in (p.get("target_attributes") or [])]
        p["sku_issues"] = [f"{i.sku}: {i.reason}" for i in check_consistency(p["skus"], required)] if required else []
        return p

    def _price(self, p: dict) -> dict:
        base = float(p.get("cost") or self.cfg.goods_cost or 0)
        if self.cfg.price_mode == "free_shipping":
            r = calc_free_shipping(base, float(self.cfg.default_express_price or 0),
                                   float(self.cfg.sales_profit or 0) / 100)
        else:
            r = calc_multiplier(base, float(self.cfg.price_multiplier or 1.2),
                                float(self.cfg.price_fixed_add or 0))
        p["price_result"] = r
        return p

    # ---------- 主流程 ----------
    def run(self, product: dict, dry_run: bool = True) -> PublishResult:
        res = PublishResult(product_id=str(product.get("id", "")), ok=True)
        try:
            p = self._normalize(product)
            res.steps.append(Step("标准化", True, f"{len(p['skus'])} 个 SKU / {len(p['images'])} 张图"))

            p = self._map_variants(p)
            res.steps.append(Step("变种映射", True, f"{len(p.get('attr_mapping') or {})} 项映射"
                                                    + (f"，{len(p['sku_issues'])} 个问题" if p.get("sku_issues") else "")))

            if self.cfg.enable_ai_title and self.text and p["title"]:
                new_title = self.text.translate_title(p["title"])
                p["title_en"] = new_title
                res.steps.append(Step("AI 标题", True, new_title[:60]))
            else:
                res.steps.append(Step("AI 标题", True, "未启用，跳过"))

            p = self._price(p)
            r = p["price_result"]
            res.steps.append(Step("价格计算", True, f"¥{r.final}（{r.note}）"))

            if dry_run:
                res.steps.append(Step("保存到店小秘", True, "dry-run：未写入"))
            else:
                if not self.dxm:
                    raise RuntimeError("未连接店小秘")
                self.dxm.save_product(p)
                res.steps.append(Step("保存到店小秘", True, "已提交"))
            return res
        except Exception as e:  # noqa: BLE001
            res.ok = False
            res.error = str(e)
            res.steps.append(Step("异常", False, str(e)))
            return res


def demo_product() -> dict:
    return {
        "id": "DEMO-001",
        "title": "3 吨迷你蜘蛛吊车 20 米臂长",
        "cost": 35000,
        "skus": [{"sku": "A-1", "attributes": {"Model Number": "YG-109", "Power (W)": "16kw"}},
                 {"sku": "A-2", "attributes": {"Model Number": "YG-110", "Power (W)": "24kw"}}],
        "images": ["https://example.com/a.jpg"],
        "attributes": [{"name": "Model Number"}, {"name": "Power (W)"}],
        "target_attributes": [{"name": "型号"}, {"name": "功率"}],
    }
