"""发布流水线：产品 → 属性/变种 → AI 文本 → 图片 → 价格 → 上传 → 保存。

默认 dry-run：只做规划与本地计算，不写店小秘；真实保存需显式 dry_run=False
且店小秘接口已在 dianxiaomi.ENDPOINTS 校正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import calc_free_shipping, calc_multiplier, calc_with_commission
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
        self.image_pipeline = ImagePipeline(cfg)

    # ---------- 单步 ----------
    def _normalize(self, product: dict) -> dict:
        p = dict(product)
        p["title"] = (p.get("title") or "").strip()
        prefix = (getattr(self.cfg, "custom_sku_prefix", "") or "").strip()
        split_ch = getattr(self.cfg, "sku_split_char", "-") or "-"
        stock = getattr(self.cfg, "stock_quantity", "") or ""
        for i, sku in enumerate(p.get("skus") or [], 1):
            name = str(sku.get("sku") or sku.get("name") or "").strip()
            if prefix and not name.startswith(prefix):
                name = f"{prefix}{split_ch}{name}" if name else f"{prefix}{split_ch}{i}"
            if getattr(self.cfg, "auto_generate_sku_code", False):
                name = f"{prefix}{split_ch}{i:03d}" if prefix else f"SKU{split_ch}{i:03d}"
            sku["sku"] = name
            if getattr(self.cfg, "modify_stock_999", False):
                sku["stock"] = 999
            elif stock:
                sku["stock"] = stock
        try:
            limit = int(self.cfg.max_sku_count or 256)
            p["skus"] = (p.get("skus") or [])[:limit]
        except Exception:
            p["skus"] = p.get("skus") or []
        p["images"] = p.get("images") or []
        return p

    def _apply_template(self, p: dict, res) -> dict:
        """套用模板快照：类目/运费/属性（属性缺失时用模板的）。"""
        import json as _j
        try:
            snap = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
        except Exception:
            snap = {}
        fields = snap.get("fields") or {}
        if not fields:
            res.steps.append(Step("模板参数", True, "未设置模板，跳过"))
            return p
        p["template_fields"] = fields
        p["template_attributes"] = snap.get("attributes") or []
        if not p.get("attributes_map"):
            amap = {}
            for a in p["template_attributes"]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    amap[k.strip()] = v.strip()
            p["attributes_map"] = amap
        res.steps.append(Step("模板参数", True,
                              f"类目 {fields.get('category','-')} / 运费 {fields.get('freight','-')} / 属性 {len(p['template_attributes'])} 项"))
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
        import json as _j
        base = float(p.get("cost") or self.cfg.goods_cost or 0)
        mode = self.cfg.price_mode
        if mode == "commission":
            r = calc_with_commission(base, float(self.cfg.price_multiplier or 1.2),
                                     float(self.cfg.price_fixed_add or 0),
                                     float(self.cfg.platform_commission or 0) / 100,
                                     float(self.cfg.sales_profit or 0) / 100)
        elif mode == "free_shipping":
            r = calc_free_shipping(base, float(self.cfg.default_express_price or 0),
                                   float(self.cfg.sales_profit or 0) / 100)
            if self.cfg.regional_pricing:
                from .pricing import regional_prices
                p["regional"] = regional_prices(r.final, {"USD": 1.0, "EUR": 0.92, "GBP": 0.79})
        elif mode == "multiplier" or True:
            r = calc_multiplier(base, float(self.cfg.price_multiplier or 1.2),
                                float(self.cfg.price_fixed_add or 0))
            tpl_raw = (getattr(self.cfg, "adjust_price_template_detail", "") or "").strip()
            if tpl_raw not in ("", "{}") and self.cfg.regional_pricing:
                try:
                    import json as _j2
                    rate_map = _j2.loads(tpl_raw)
                    if isinstance(rate_map, dict) and rate_map:
                        from .pricing import regional_prices
                        p["regional"] = regional_prices(r.final, {k: float(v) for k, v in rate_map.items()})
                except Exception:
                    pass

            if self.cfg.tuoguan_price_tiered_enabled:
                try:
                    tiers = _j.loads(self.cfg.tuoguan_price_tiers or "[]")
                    if tiers:
                        from .pricing import apply_tier
                        new_final = apply_tier(base, tiers)
                        if new_final != base:
                            r = type(r)(r.base, r.multiplier, r.fixed_add, new_final, "阶梯倍率")
                except Exception:
                    pass
        p["price_result"] = r
        return p

    def _run_ops(self, url: str, white_bg: bool, remove_wm: bool, edit: bool) -> str:
        ip = self.image_pipeline
        old = (ip.cfg.white_bg_enable, ip.cfg.watermark_enable, ip.cfg.edit_enable)
        try:
            ip.cfg.white_bg_enable, ip.cfg.watermark_enable, ip.cfg.edit_enable = white_bg, remove_wm, edit
            r = ip.run(str(url))
            return r.last_url() or str(url)
        finally:
            ip.cfg.white_bg_enable, ip.cfg.watermark_enable, ip.cfg.edit_enable = old

    def _process_urls(self, urls: list[str], limit: int = 8, white_bg: bool = True,
                      remove_wm: bool = False, edit: bool = False) -> tuple[list[str], int]:
        out, done = [], 0
        for u in urls[:limit]:
            try:
                nu = self._run_ops(u, white_bg, remove_wm, edit)
                out.append(nu); done += 1
            except Exception:
                out.append(str(u))
        return out, done

    def _ai_images(self, p: dict, res) -> dict:
        """主图 / 详情图 / SKU 图 三条分支，按配置范围处理。"""
        from .image_index import pick

        # 主图
        main = p.get("images", [])
        main_ops = (self.cfg.white_bg_enable or self.cfg.watermark_enable
                    or getattr(self.cfg, "img_main_edit", False)
                    or (self.cfg.background_gen_rules or "").strip() not in ("", "[]"))
        if main and main_ops:
            # 白底范围
            wb_expr = ""
            if self.cfg.white_background_pic_1 or self.cfg.white_background_pic_2:
                wb_expr = f"{self.cfg.white_background_pic_1}-{self.cfg.white_background_pic_2}"
            # 去水印范围（独立）
            wm_expr = ""
            if getattr(self.cfg, "remove_watermark_pic_1", "") or getattr(self.cfg, "remove_watermark_pic_2", ""):
                wm_expr = f"{getattr(self.cfg, 'remove_watermark_pic_1', '1')}-{getattr(self.cfg, 'remove_watermark_pic_2', '5')}"
            edit_expr = getattr(self.cfg, "single_edit_main_pic_indices", "") or ""
            selected = pick(main, wb_expr or wm_expr or edit_expr) if (wb_expr or wm_expr or edit_expr) else main
            if selected:
                out, done = self._process_urls(selected,
                                               white_bg=self.cfg.white_bg_enable or bool(wb_expr),
                                               remove_wm=self.cfg.watermark_enable or bool(wm_expr),
                                               edit=getattr(self.cfg, "img_main_edit", False) or bool(edit_expr))
                idx = [i for i, u in enumerate(main) if u in selected]
                for i, u in zip(idx, out):
                    main[i] = u
                p["images"] = main
                res.steps.append(Step("主图处理", done > 0, f"{done}/{len(out)} 张"))

        # 详情图
        detail = p.get("detail_images") or []
        detail_ops = (getattr(self.cfg, "img_detail_white_bg", False)
                      or getattr(self.cfg, "img_detail_remove_wm", False)
                      or getattr(self.cfg, "img_detail_edit", False))
        if detail and detail_ops:
            selected = pick(detail, self.cfg.white_background_detail_pic_indices)
            if selected:
                out, done = self._process_urls(selected, limit=20,
                                               white_bg=getattr(self.cfg, "img_detail_white_bg", False),
                                               remove_wm=getattr(self.cfg, "img_detail_remove_wm", False),
                                               edit=getattr(self.cfg, "img_detail_edit", False))
                idx = [i for i, u in enumerate(detail) if u in selected]
                for i, u in zip(idx, out):
                    detail[i] = u
                p["detail_images"] = detail
                res.steps.append(Step("详情图处理", done > 0, f"{done}/{len(out)} 张"))

        # SKU 图
        if self.cfg.white_background_sku_pic:
            skus = p.get("skus") or []
            done = 0
            for sku in skus[:12]:
                img = sku.get("image") or sku.get("imageUrl")
                if not img:
                    continue
                try:
                    sku["processed_image"] = self._run_ops(
                        str(img),
                        white_bg=getattr(self.cfg, "img_sku_white_bg", True),
                        remove_wm=getattr(self.cfg, "img_sku_remove_wm", False),
                        edit=getattr(self.cfg, "img_sku_edit", False))
                    done += 1
                except Exception:
                    pass
            if done:
                res.steps.append(Step("SKU图处理", True, f"{done} 张"))

        # 背景生成（AI 换背景）
        rules_raw = (getattr(self.cfg, "background_gen_rules", "") or "").strip()
        if rules_raw not in ("", "[]"):
            try:
                import json as _j
                rules = _j.loads(rules_raw)
            except Exception:
                rules = []
            done_bg = 0
            for rule in (rules or [])[:6]:
                pos = str(rule.get("position", ""))
                prompt = rule.get("prompt", "") or "专业电商白底展示图"
                idx = 0
                if pos.startswith("main_"):
                    try:
                        idx = int(pos.split("_")[1]) - 1
                    except Exception:
                        idx = 0
                    if 0 <= idx < len(p.get("images") or []):
                        try:
                            p["images"][idx] = self._run_ops(p["images"][idx], False, False, True)
                            done_bg += 1
                        except Exception:
                            pass
            if done_bg:
                res.steps.append(Step("背景生成", True, f"{done_bg} 张"))
        return p

    def _publicize_images(self, p: dict, res) -> dict:
        """把私有图片下载后转存到我们的服务器，得到公网可访问 URL。"""
        import tempfile
        from pathlib import Path as _P
        from .hosting import upload as host_upload
        d = _P(tempfile.mkdtemp())
        cookies = None
        if self.dxm is not None:
            try:
                cookies = self.dxm.cookies_dict()
            except Exception:
                cookies = None
        out = []
        for i, u in enumerate(p.get("images", [])[:8]):
            try:
                r = __import__("requests").get(str(u), timeout=60, cookies=cookies)
                r.raise_for_status()
                fp = d / f"pub_{i}.png"; fp.write_bytes(r.content)
                out.append(host_upload(fp))
            except Exception:
                out.append(str(u))
        if out:
            p["images"] = out
            res.steps.append(Step("图片转存", True, f"{len(out)} 张已转公网"))
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
            res.steps.append(Step("标准化", True,
                                  f"{len(p['skus'])} 个 SKU / {len(p['images'])} 张图（上限 {self.cfg.max_sku_count}）"))
            if p["skus"]:
                sample = p["skus"][0]
                res.steps.append(Step("SKU 处理", True,
                                      f"{sample.get('sku')} · 库存 {sample.get('stock', '-')}"))

            p = self._apply_template(p, res)
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
                    pos = (self.cfg.detail_text_position or "head").lower()
                    if head and pos in ("head", "both"):
                        html = f"<p>{head}</p>" + html
                    if tail and pos in ("tail", "both"):
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

            _img_ops = (self.cfg.white_bg_enable or self.cfg.watermark_enable or self.cfg.edit_enable
                        or getattr(self.cfg, "img_main_edit", False)
                        or getattr(self.cfg, "img_detail_white_bg", False)
                        or getattr(self.cfg, "img_detail_remove_wm", False)
                        or getattr(self.cfg, "img_detail_edit", False)
                        or getattr(self.cfg, "img_sku_white_bg", False)
                        or getattr(self.cfg, "img_sku_remove_wm", False)
                        or getattr(self.cfg, "img_sku_edit", False)
                        or (getattr(self.cfg, "background_gen_rules", "") or "").strip() not in ("", "[]"))
            if p.get("images") and _img_ops:
                try:
                    p = self._ai_images(p, res)
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("图片AI处理", False, str(ex)[:100]))

            if p.get("images"):
                try:
                    p = self._publicize_images(p, res)
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("图片转存", False, str(ex)[:100]))

            if not dry_run and self.dxm and p.get("images"):
                uploaded, failed = 0, 0
                for u in p["images"][:20]:
                    try:
                        self.dxm.upload_image_from_url(str(u), full_cid=getattr(self.cfg, "upload_full_cid", "") or "")
                        uploaded += 1
                    except Exception as ex:  # noqa: BLE001
                        failed += 1
                        p.setdefault("upload_errors", []).append(str(ex)[:120])
                res.steps.append(Step("图片上传", uploaded > 0 or failed == 0,
                                      f"成功 {uploaded} / 失败 {failed}"))

            if self.cfg.review_text_enable and p.get("detail_html"):
                try:
                    cleaned = TextPipeline(self.cfg).review_text(p["detail_html"]).value
                    if cleaned:
                        p["detail_html"] = cleaned
                    res.steps.append(Step("文本审查", True, (cleaned or "")[:40].replace("\n", " ")))
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("文本审查", False, str(ex)[:80]))

            if self.cfg.check_banned_word and p.get("title_en"):
                from .text_pipeline import find_banned, BANNED_DEFAULT
                hits = find_banned(p["title_en"], BANNED_DEFAULT)
                res.steps.append(Step("违禁词检查", not hits, ("发现：" + ", ".join(hits)) if hits else "未发现"))

            if self.cfg.enable_ai_weight and p.get("images"):
                try:
                    rw = TextPipeline(self.cfg).estimate_weight(str(p["images"][0]), p.get("title_en") or p.get("title", ""))
                    p["ai_weight"] = rw.value[:200]
                    res.steps.append(Step("AI 估重", rw.ok, rw.value[:40].replace("\n", " ")))
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("AI 估重", False, str(ex)[:80]))

            if (self.cfg.review_main_img_enable or self.cfg.review_detail_img_enable) and p.get("images"):
                ip = ImagePipeline(self.cfg)
                try:
                    verdict = ip.review(str(p["images"][0]), "main")
                    p["image_review"] = verdict[:300]
                    bad = any(k in verdict for k in ("不符合", "违规", "建议删除", "不建议", "含厂家", "含联系方式"))
                    if bad and self.cfg.review_block_on_fail and len(p["images"]) > 1:
                        p["images"] = p["images"][1:]
                        res.steps.append(Step("图片审查", False, "不合规，已剔除该图"))
                    elif bad and self.cfg.review_block_on_fail:
                        res.steps.append(Step("图片审查", False, "不合规，已拦截：" + verdict[:50].replace("\n", " ")))
                        res.ok = False
                        res.error = "图片审查不合规"
                        return res
                    res.steps.append(Step("图片审查", True, ("⚠ " if bad else "") + verdict[:60].replace("\n", " ")))
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("图片审查", False, "跳过：" + str(ex)[:100]))

            p = self._price(p)
            r = p["price_result"]
            extra = ""
            if p.get("regional"):
                extra = " · 区域 " + " ".join(f"{k}{v}" for k, v in list(p['regional'].items())[:3])
            res.steps.append(Step("价格计算", True, f"¥{r.final}（{r.note}）{extra}"))

            if getattr(self.cfg, "generate_main_video", False) and p.get("images"):
                try:
                    import tempfile as _tf
                    from pathlib import Path as _P2
                    import requests as _rq2
                    from .video import build_slideshow, probe as _vp
                    if _vp()["available"]:
                        d2 = _P2(_tf.mkdtemp())
                        files = []
                        for i, u in enumerate(p["images"][:6]):
                            fp = d2 / f"v{i}.png"
                            try:
                                fp.write_bytes(_rq2.get(str(u), timeout=60).content); files.append(fp)
                            except Exception:
                                continue
                        if len(files) >= 2:
                            out = d2 / "main.mp4"
                            build_slideshow(files, out)
                            p["main_video"] = str(out)
                            res.steps.append(Step("主图视频", True, str(out)))
                except Exception as ex:  # noqa: BLE001
                    res.steps.append(Step("主图视频", False, str(ex)[:80]))

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
