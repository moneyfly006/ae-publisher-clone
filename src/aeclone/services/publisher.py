"""发布流水线：产品 → 属性/变种 → AI 文本 → 图片 → 价格 → 上传 → 保存。

默认 dry-run：只做规划与本地计算，不写店小秘；真实保存需显式 dry_run=False
且店小秘接口已在 dianxiaomi.ENDPOINTS 校正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import calc_free_shipping, calc_multiplier
from .text_pipeline import TextPipeline
from .image_pipeline import ImagePipeline
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
    def __init__(self, cfg, dxm=None, text_service=None, image_service=None, ws_endpoint: str = ""):
        self.cfg = cfg
        self.dxm = dxm
        self.text = text_service
        self.image = image_service
        self.ws_endpoint = ws_endpoint

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

    def _local_image_ops(self, p: dict, res) -> dict:
        """水印 / 四合一：下载 → 本地处理 → 托管 → 回填 URL。"""
        import tempfile
        import requests as _rq
        from pathlib import Path as _P
        from .image_ops import add_watermark, four_in_one
        from .hosting import upload as host_upload

        d = _P(tempfile.mkdtemp())
        local = []
        for i, u in enumerate(p.get("images", [])[:8]):
            try:
                r = _rq.get(str(u), timeout=60); r.raise_for_status()
                fp = d / f"src_{i}.png"; fp.write_bytes(r.content); local.append(fp)
            except Exception:
                continue
        if not local:
            return p

        if self.cfg.watermark:
            outs = []
            for i, fp in enumerate(local):
                try:
                    outs.append(add_watermark(fp, self.cfg.watermark, d / f"wm_{i}.png"))
                except Exception:
                    outs.append(fp)
            local = outs

        if self.cfg.four_in_one and len(local) >= 2:
            try:
                local.append(four_in_one(local[:4], d / "four.png"))
            except Exception:
                pass

        urls = []
        for fp in local:
            try:
                urls.append(host_upload(fp))
            except Exception:
                continue
        if urls:
            p["images"] = urls
        res.steps.append(Step("本地图片处理", bool(urls), f"{len(urls)} 张已托管"))
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

            tp = TextPipeline(self.cfg)
            if self.cfg.enable_ai_title and p["title"]:
                r = tp.make_title(p["title"], p.get("brands"))
                if r.ok:
                    p["title_en"] = r.value
                    res.steps.append(Step("AI 标题", True, r.value[:70]))
                else:
                    res.steps.append(Step("AI 标题", False, r.error))
            else:
                res.steps.append(Step("AI 标题", True, "未启用，跳过"))

            if self.cfg.enable_ai_detail and p["title"]:
                r = tp.make_detail(p.get("title_en") or p["title"], p.get("attributes_map"))
                res.steps.append(Step("AI 详情", r.ok, (r.value[:60] if r.ok else r.error)))
                if r.ok:
                    html = r.value
                    head = (self.cfg.detail_text_info or "").strip()
                    tail = (self.cfg.detail_text_info_tail or "").strip()
                    if head:
                        html = f"<p>{head}</p>" + html
                    if tail:
                        html = html + f"<p>{tail}</p>"
                    p["detail_html"] = html

            if self.cfg.enable_ai_sku and p.get("skus"):
                names = [str(s.get("sku") or s.get("name") or "") for s in p["skus"]]
                r = tp.make_skus(names)
                if r.ok:
                    new_names = r.value.splitlines()
                    for sku, nm in zip(p["skus"], new_names):
                        sku["optimized_name"] = nm
                res.steps.append(Step("AI SKU", r.ok, (r.value[:50].replace("\n", " ") if r.ok else r.error)))

            if self.cfg.four_in_one or self.cfg.watermark:
                try:
                    p = self._local_image_ops(p, res)
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("本地图片处理", False, str(ex)[:120]))

            if not dry_run and self.dxm and p.get("images"):
                uploaded, failed = 0, 0
                for u in p["images"][:20]:
                    try:
                        self.dxm.upload_image_from_url(str(u))
                        uploaded += 1
                    except Exception as ex:  # noqa: BLE001
                        failed += 1
                        p.setdefault("upload_errors", []).append(str(ex)[:120])
                res.steps.append(Step("图片上传", uploaded > 0 or failed == 0,
                                      f"成功 {uploaded} / 失败 {failed}"))

            if (self.cfg.review_main_img_enable or self.cfg.review_detail_img_enable) and p.get("images"):
                ip = ImagePipeline(self.cfg)
                try:
                    verdict = ip.review(str(p["images"][0]), "main")
                    p["image_review"] = verdict[:300]
                    res.steps.append(Step("图片审查", True, verdict[:60].replace("\n", " ")))
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("图片审查", False, str(ex)[:120]))

            p = self._price(p)
            r = p["price_result"]
            res.steps.append(Step("价格计算", True, f"¥{r.final}（{r.note}）"))

            if dry_run:
                res.steps.append(Step("保存到店小秘", True, "dry-run：未写入"))
            else:
                if not self.ws_endpoint:
                    raise RuntimeError("未连接浏览器（缺少 CDP 端点）")
                from ..integrations.editor_automation import update_and_save
                r = update_and_save(self.ws_endpoint, str(p.get("id", "")),
                                    p.get("title_en") or None)
                if not r.ok:
                    raise RuntimeError(r.message)
                res.steps.append(Step("保存到店小秘", True, r.message))
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
