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
from .progress import BUS, emit


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


def add_step(res, name, ok, detail=""):
    """记录步骤结果，同时实时上报到进度总线。"""
    res.steps.append(Step(name, bool(ok), str(detail)))
    emit(str(name), "", str(detail)[:140], "OK" if ok else "ERROR")
    if ok:
        BUS.done_stage()


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
        only_prefix = getattr(self.cfg, "only_sku_prefix_checkbox", False)
        split_ch = getattr(self.cfg, "sku_split_char", "-") or "-"
        stock = getattr(self.cfg, "stock_quantity", "") or ""
        for i, sku in enumerate(p.get("skus") or [], 1):
            name = str(sku.get("sku") or sku.get("name") or "").strip()
            if prefix and not name.startswith(prefix) and not only_prefix:
                name = f"{prefix}{split_ch}{name}" if name else f"{prefix}{split_ch}{i}"
            if getattr(self.cfg, "auto_generate_sku_code", False):
                name = f"{prefix}{split_ch}{i:03d}" if prefix else f"SKU{split_ch}{i:03d}"
            sku["sku"] = name
            if getattr(self.cfg, "modify_stock_999", False):
                sku["stock"] = 999
            elif stock:
                sku["stock"] = stock
        if getattr(self.cfg, "combo_enable", False):
            try:
                factor = float(getattr(self.cfg, "combo_multiplier", "2") or 2)
            except Exception:
                factor = 2.0
            pref = getattr(self.cfg, "combo_prefix", "套装") or "套装"
            base_skus = list(p.get("skus") or [])
            combo = []
            for sku in base_skus:
                c = dict(sku)
                name = str(sku.get("sku") or sku.get("name") or "")
                c["sku"] = f"{pref}-{name}" if name else f"{pref}-{len(combo)+1}"
                try:
                    if sku.get("price") is not None:
                        c["price"] = round(float(sku["price"]) * factor, 2)
                except Exception:
                    pass
                combo.append(c)
            p["skus"] = base_skus + combo
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
            add_step(res, "模板参数", True, "未设置模板，跳过")
            return p
        p["template_fields"] = fields
        p["template_attributes"] = snap.get("attributes") or []
        specs = getattr(self, "_pending_specs", {}) or {}
        for key, label in (("spec_weight", "Weight"), ("spec_length", "Length"),
                           ("spec_width", "Width"), ("spec_height", "Height")):
            v = (getattr(self.cfg, key, "") or "").strip()
            if v:
                specs[label] = v
        if getattr(self.cfg, "product_type", ""):
            specs["Product Type"] = self.cfg.product_type
        if getattr(self.cfg, "declared_value", ""):
            specs["Declared Value"] = self.cfg.declared_value
        if specs:
            p["specs"] = specs
            amap = dict(p.get("attributes_map") or {})
            amap.update(specs)
            p["attributes_map"] = amap
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
        # 白底 + 去水印 同时开启时，合并成一次调用
        if white_bg and remove_wm and not edit:
            try:
                import tempfile as _tf, requests as _rq
                from pathlib import Path as _P
                from ..api import ApiClient
                from . import ImageService
                d = _P(_tf.mkdtemp()); fp = d / "in.png"
                fp.write_bytes(_rq.get(str(url), timeout=90).content)
                svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
                return svc.combined(fp) or str(url)
            except Exception:
                pass
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
                add_step(res, "主图处理", done > 0, f"{done}/{len(out)} 张")

        # 详情图
        detail = p.get("detail_images") or []
        detail_ops = (getattr(self.cfg, "img_detail_white_bg", False)
                      or getattr(self.cfg, "img_detail_remove_wm", False)
                      or getattr(self.cfg, "img_detail_edit", False)
                      or getattr(self.cfg, "translate_detail_pic_enable", False))
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
                add_step(res, "详情图处理", done > 0, f"{done}/{len(out)} 张")

        # 主图文字翻译
        if getattr(self.cfg, "translate_main_pic_enable", False) and main and getattr(self.cfg, "translate_image_understanding", False):
            kept = []
            for it in main:
                _u = it.get("url") if isinstance(it, dict) else str(it)
                try:
                    _has = ip.has_text(str(_u))
                except Exception:
                    _has = True
                if _has:
                    kept.append(it)
                else:
                    add_step(res, "翻译预检", True, f"无文字，跳过 {str(_u)[-20:]}")
            main = kept or main

        if getattr(self.cfg, "translate_main_pic_enable", False) and main:
            from .image_index import pick as _pk
            expr = getattr(self.cfg, "translate_main_pic_indices", "") or ""
            targets = _pk(main, expr) if expr else main[:2]
            n = 0
            for i, u in enumerate(main):
                if u in targets:
                    try:
                        ip = self.image_pipeline
                        old_p = ip.cfg.edit_prompt
                        ip.cfg.edit_prompt = getattr(self.cfg, "translate_prompt", "") or old_p
                        main[i] = self._run_ops(u, False, False, True)
                        ip.cfg.edit_prompt = old_p
                        n += 1
                    except Exception:
                        pass
            if n:
                p["images"] = main
                add_step(res, "主图翻译", True, f"{n} 张")

        # 详情图文字翻译
        if getattr(self.cfg, "translate_detail_pic_enable", False) and detail:
            from .image_index import pick as _pick
            idx_expr = getattr(self.cfg, "translate_detail_pic_indices", "") or ""
            targets = _pick(detail, idx_expr) if idx_expr else detail[:3]
            tr_done = 0
            for i, u in enumerate(detail):
                if u in targets:
                    try:
                        ip = self.image_pipeline
                        old = ip.cfg.edit_prompt
                        ip.cfg.edit_prompt = getattr(self.cfg, "translate_prompt", "") or old
                        detail[i] = self._run_ops(u, False, False, True)
                        ip.cfg.edit_prompt = old
                        tr_done += 1
                    except Exception:
                        pass
            if tr_done:
                p["detail_images"] = detail
                add_step(res, "详情图翻译", True, f"{tr_done} 张")

        # SKU 图
        if self.cfg.white_background_sku_pic:
            skus = p.get("skus") or []
            done = 0
            try:
                import json as _j3
                repl = _j3.loads(getattr(self.cfg, "sku_image_replace", "") or "{}")
            except Exception:
                repl = {}
            for sku in skus[:12]:
                img = sku.get("image") or sku.get("imageUrl")
                name = str(sku.get("sku") or "")
                if name in repl and repl[name]:
                    sku["image"] = repl[name]
                    img = repl[name]
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
                add_step(res, "SKU图处理", True, f"{done} 张")

        # SKU 图文字翻译
        if getattr(self.cfg, "translate_sku_pic", False):
            n = 0
            for sku in (p.get("skus") or [])[:6]:
                img = sku.get("processed_image") or sku.get("image")
                if not img:
                    continue
                try:
                    ip = self.image_pipeline
                    old_p = ip.cfg.edit_prompt
                    ip.cfg.edit_prompt = getattr(self.cfg, "translate_prompt", "") or old_p
                    sku["processed_image"] = self._run_ops(str(img), False, False, True)
                    ip.cfg.edit_prompt = old_p
                    n += 1
                except Exception:
                    pass
            if n:
                add_step(res, "SKU图翻译", True, f"{n} 张")

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
                add_step(res, "背景生成", True, f"{done_bg} 张")
        return p

    def _publicize_images(self, p: dict, res, dry_run: bool = False) -> dict:
        """把私有图片下载后转存到我们的服务器，得到公网可访问 URL。

        dry-run 时跳过下载/上传（预演只需计划，不需要真实公网 URL），速度快 20 倍以上。
        """
        if dry_run:
            add_step(res, "图片转存", True, f"dry-run 跳过（{len(p.get('images') or [])} 张）")
            return p
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
            add_step(res, "图片转存", True, f"{len(out)} 张已转公网")
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

        # 回传店小秘相册：水印图先进图片库，商品再用相册 URL（老软件同款做法）
        album_ok = 0
        if urls and self.dxm is not None and getattr(self.cfg, "watermark_upload_album", True) and self.cfg.watermark:
            full_cid = getattr(self.cfg, "upload_full_cid", "") or ""
            for u in urls:
                try:
                    self.dxm.upload_image_from_url(str(u), full_cid=full_cid)
                    album_ok += 1
                except Exception:
                    continue
            if album_ok:
                add_step(res, "水印回传相册", True, f"{album_ok} 张已入店小秘图片库")

        if urls:
            p["images"] = urls
        add_step(res, "本地图片处理", bool(urls),
                 f"{len(urls)} 张已托管" + (f"（相册 {album_ok}）" if album_ok else ""))
        return p

    # ---------- 主流程 ----------
    def run(self, product: dict, dry_run: bool = True) -> PublishResult:
        res = PublishResult(product_id=str(product.get("id", "")), ok=True)
        try:
            BUS.begin(9)
            emit("开始", f"产品 {product.get('id','')}", (product.get("title") or "")[:60], "INFO")
            # 提示词方案：按品类自动套用
            try:
                from .prompt_sets import apply_for_product
                _cat = ""
                try:
                    _snap = __import__("json").loads(getattr(self.cfg, "template_snapshot", "") or "{}")
                    _cat = str((_snap.get("fields") or {}).get("category") or "")
                except Exception:
                    _cat = ""
                _set_name = apply_for_product(self.cfg, _cat + " " + str(product.get("title") or ""))
                if _set_name:
                    emit("提示词方案", _set_name, f"品类={_cat or '-'}", "INFO")
            except Exception:
                pass
            if not BUS.price_of(self.cfg.model_image_edit):
                try:
                    from ..api import ApiClient as _AC
                    _rows = _AC(self.cfg.api_base, self.cfg.api_key, 20).pricing()
                    BUS.set_prices({r["model"]: r["per_call"] for r in _rows if r.get("per_call")})
                except Exception:
                    pass
            p = self._normalize(product)
            add_step(res, "标准化", True,
                     f"{len(p['skus'])} 个 SKU / {len(p['images'])} 张图（上限 {self.cfg.max_sku_count}）")
            if p["skus"]:
                sample = p["skus"][0]
                add_step(res, "SKU 处理", True,
                         f"{sample.get('sku')} · 库存 {sample.get('stock', '-')}")

            specs = {}
            for key, label in (("spec_weight", "Weight"), ("spec_length", "Length"),
                               ("spec_width", "Width"), ("spec_height", "Height")):
                v = (getattr(self.cfg, key, "") or "").strip()
                if v:
                    specs[label] = v
            if getattr(self.cfg, "product_type", ""):
                specs["Product Type"] = self.cfg.product_type
            if getattr(self.cfg, "declared_value", ""):
                specs["Declared Value"] = self.cfg.declared_value
            if specs:
                p["specs"] = specs
                add_step(res, "规格属性", True, ", ".join(f"{k}={v}" for k, v in specs.items())[:80])

            try:
                from .image_task_plan import build_plan
                plan = build_plan(self.cfg, p)
                if plan.steps:
                    add_step(res, "图片计划", not plan.blockers, "；".join(plan.steps[:4]))
                if plan.blockers:
                    add_step(res, "计划阻断", False, "；".join(plan.blockers[:3]))
            except Exception:
                pass

            p = self._apply_template(p, res)
            p = self._map_variants(p)
            res.steps.append(Step("变种映射", True, f"{len(p.get('attr_mapping') or {})} 项映射"
                                                    + (f"，{len(p['sku_issues'])} 个问题" if p.get("sku_issues") else "")))

            tp = TextPipeline(self.cfg)
            if self.cfg.enable_ai_title and p["title"]:
                r = tp.make_title(p["title"], p.get("brands"))
                if r.ok:
                    p["title_en"] = r.value
                    add_step(res, "AI 标题", True, r.value[:70])
                else:
                    add_step(res, "AI 标题", False, r.error)
            else:
                add_step(res, "AI 标题", True, "未启用，跳过")

            if self.cfg.enable_ai_detail and p["title"]:
                r = tp.make_detail(p.get("title_en") or p["title"], p.get("attributes_map"))
                add_step(res, "AI 详情", r.ok, (r.value[:60] if r.ok else r.error))
                if r.ok:
                    html = r.value
                    head = (self.cfg.detail_text_info or "").strip()
                    tail = (self.cfg.detail_text_info_tail or "").strip()
                    pos = (self.cfg.detail_text_position or "head").lower()
                    if getattr(self.cfg, "move_detail_text", False) and head:
                        pos = "head"
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
                add_step(res, "AI SKU", r.ok, (r.value[:50].replace("\n", " ") if r.ok else r.error))

            if self.cfg.four_in_one or self.cfg.watermark:
                try:
                    p = self._local_image_ops(p, res)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "本地图片处理", False, str(ex)[:120])

            _img_ops = (self.cfg.white_bg_enable or self.cfg.watermark_enable or self.cfg.edit_enable
                        or getattr(self.cfg, "img_main_edit", False)
                        or getattr(self.cfg, "img_detail_white_bg", False)
                        or getattr(self.cfg, "img_detail_remove_wm", False)
                        or getattr(self.cfg, "img_detail_edit", False)
                        or getattr(self.cfg, "img_sku_white_bg", False)
                        or getattr(self.cfg, "img_sku_remove_wm", False)
                        or getattr(self.cfg, "img_sku_edit", False)
                        or (getattr(self.cfg, "background_gen_rules", "") or "").strip() not in ("", "[]")
                        or getattr(self.cfg, "translate_detail_pic_enable", False)
                        or getattr(self.cfg, "translate_main_pic_enable", False)
                        or getattr(self.cfg, "translate_sku_pic", False))
            # AI 主图生成（文生图）
            if getattr(self.cfg, "main_image_generate_enable", False):
                try:
                    from ..api import ApiClient
                    model = getattr(self.cfg, "image_gen_model", "qwen-image-3.0") or "qwen-image-3.0"
                    prompt = (getattr(self.cfg, "main_image_generate_prompt", "") or "").strip()
                    prompt = prompt or (p.get("title_en") or p.get("title", "") or "商品主图")
                    n = int(getattr(self.cfg, "main_image_generate_count", "1") or 1)
                    client = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)
                    gen = []
                    for _ in range(min(n, 3)):
                        try:
                            u = client.images_generate(model, prompt)
                            if u:
                                gen.append(u)
                        except Exception:
                            break
                    if gen:
                        p["images"] = gen + (p.get("images") or [])
                        add_step(res, "AI主图生成", True, f"{len(gen)} 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "AI主图生成", False, str(ex)[:80])

            # 指定图片编辑
            if getattr(self.cfg, "designated_edit_enable", False):
                try:
                    from .image_index import pick as _pk2
                    idx = getattr(self.cfg, "designated_edit_indices", "") or ""
                    prompt = (getattr(self.cfg, "designated_edit_prompt", "") or "").strip()
                    if idx and prompt and p.get("images"):
                        targets = _pk2(p["images"], idx)
                        n2 = 0
                        for i, u in enumerate(p["images"]):
                            if u in targets:
                                try:
                                    ip = self.image_pipeline
                                    old_p = ip.cfg.edit_prompt
                                    ip.cfg.edit_prompt = prompt
                                    p["images"][i] = self._run_ops(u, False, False, True)
                                    ip.cfg.edit_prompt = old_p
                                    n2 += 1
                                except Exception:
                                    pass
                        if n2:
                            add_step(res, "指定图片编辑", True, f"{n2} 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "指定图片编辑", False, str(ex)[:80])

            if getattr(self.cfg, "pad_main_images_from_extra", False):
                try:
                    need = int(getattr(self.cfg, "main_pic_count", "6") or 6)
                    imgs = p.get("images") or []
                    extras = [u for u in (p.get("detail_images") or []) if u not in imgs]
                    while len(imgs) < need and extras:
                        imgs.append(extras.pop(0))
                    if imgs:
                        p["images"] = imgs
                        add_step(res, "主图补齐", True, f"{len(imgs)} 张")
                except Exception:
                    pass

            if (p.get("images") or p.get("detail_images") or p.get("skus")) and _img_ops:
                try:
                    p = self._ai_images(p, res)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片AI处理", False, str(ex)[:100])

            if getattr(self.cfg, "overlay_watermark_enable", False) and (self.cfg.watermark or "").strip():
                try:
                    import tempfile as _tf3
                    from pathlib import Path as _P3
                    import requests as _rq3
                    from .image_ops import add_watermark
                    from .hosting import upload as _host3
                    d3 = _P3(_tf3.mkdtemp()); n3 = 0
                    for i, u in enumerate((p.get("images") or [])[:6]):
                        try:
                            raw = _rq3.get(str(u), timeout=60).content
                            src = d3 / f"w{i}.png"; src.write_bytes(raw)
                            out = d3 / f"wo{i}.png"
                            add_watermark(src, self.cfg.watermark, out,
                                          position=getattr(self.cfg, "overlay_position", "bottom-right"),
                                          opacity=float(getattr(self.cfg, "overlay_opacity", "0.7") or 0.7))
                            p["images"][i] = _host3(out); n3 += 1
                        except Exception:
                            continue
                    if n3:
                        add_step(res, "水印叠加", True, f"{n3} 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "水印叠加", False, str(ex)[:80])

            # 营销场景图（基于主图生成，追加到详情图）
            if getattr(self.cfg, "marketing_image_enable", False) and p.get("images") and not dry_run:
                try:
                    import tempfile as _tf, requests as _rq
                    from pathlib import Path as _Pm
                    from . import ImageService
                    from ..api import ApiClient as _ACm
                    d_m = _Pm(_tf.mkdtemp())
                    src = d_m / "m0.png"
                    src.write_bytes(_rq.get(str(p["images"][0]), timeout=60).content)
                    svc = ImageService(_ACm(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
                    out = svc.marketing(src, getattr(self.cfg, "marketing_image_prompt", ""),
                                        int(getattr(self.cfg, "marketing_image_count", 1) or 1))
                    if out:
                        p.setdefault("detail_images", [])
                        p["detail_images"] = list(p["detail_images"]) + [u for u in out if u]
                        add_step(res, "营销图", True, f"{len(out)} 张已生成")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "营销图", False, str(ex)[:80])

            if p.get("images"):
                try:
                    p = self._publicize_images(p, res, dry_run=dry_run)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片转存", False, str(ex)[:100])

            if not dry_run and self.dxm and p.get("images"):
                uploaded, failed, space_full = 0, 0, False
                for u in p["images"][:20]:
                    try:
                        self.dxm.upload_image_from_url(str(u), full_cid=getattr(self.cfg, "upload_full_cid", "") or "")
                        uploaded += 1
                    except Exception as ex:  # noqa: BLE001
                        _msg = str(ex)
                        if "空间不足" in _msg or "空间已满" in _msg or "no space" in _msg.lower():
                            space_full = True
                            break
                        failed += 1
                        p.setdefault("upload_errors", []).append(_msg[:120])
                if space_full:
                    add_step(res, "图片上传", True,
                             f"店小秘图片空间不足，已跳过上传（改用外链 URL，不影响刊登）")
                    emit("图片上传", "图片空间不足", "已跳过，外链 URL 可正常使用", "WARN")
                else:
                    add_step(res, "图片上传", uploaded > 0 or failed == 0,
                             f"成功 {uploaded} / 失败 {failed}")

            if (self.cfg.review_detail_img_enable and p.get("detail_images")):
                try:
                    ip2 = ImagePipeline(self.cfg)
                    n_first = int(getattr(self.cfg, "detail_review_first_count", 0) or 0)
                    n_last = int(getattr(self.cfg, "detail_review_last_count", 0) or 0)
                    imgs = p["detail_images"]
                    subset = []
                    if n_first:
                        subset += imgs[:n_first]
                    if n_last:
                        subset += imgs[-n_last:]
                    if not subset:
                        subset = imgs[:2]
                    verdict = ip2.review(str(subset[0]), "detail")
                    add_step(res, "详情图审查", True, verdict[:50].replace("\n", " "))
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "详情图审查", False, str(ex)[:80])

            if getattr(self.cfg, "copy_sku_to_detail", False) and p.get("skus") and p.get("detail_html"):
                names = " / ".join(str(s.get("sku") or "") for s in p["skus"][:20])
                p["detail_html"] = p["detail_html"] + f"<p>SKU: {names}</p>"
                add_step(res, "SKU写入详情", True, f"{len(p['skus'])} 个")

            if getattr(self.cfg, "pack_weight", ""):
                p.setdefault("specs", {})["Pack Weight"] = self.cfg.pack_weight

            if self.cfg.review_text_enable and p.get("detail_html"):
                try:
                    cleaned = TextPipeline(self.cfg).review_text(p["detail_html"]).value
                    if cleaned:
                        p["detail_html"] = cleaned
                    add_step(res, "文本审查", True, (cleaned or "")[:40].replace("\n", " "))
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "文本审查", False, str(ex)[:80])

            if self.cfg.check_banned_word and p.get("title_en"):
                from .text_pipeline import find_banned, BANNED_DEFAULT
                hits = find_banned(p["title_en"], BANNED_DEFAULT)
                add_step(res, "违禁词检查", not hits, ("发现：" + ", ".join(hits)) if hits else "未发现")

            if self.cfg.enable_ai_weight and p.get("images"):
                try:
                    tp_w = TextPipeline(self.cfg)
                    rw = tp_w.estimate_weight(str(p["images"][0]), p.get("title_en") or p.get("title", ""))
                    p["ai_weight"] = rw.value[:200]
                    # 解析成结构化数值，自动填入规格（缺失才填，不覆盖手工值）
                    est = tp_w.estimate_weight_json(str(p["images"][0]), p.get("title_en") or p.get("title", ""))
                    if est:
                        specs = p.setdefault("specs", {})
                        if est.get("weight_kg") and not specs.get("Weight"):
                            specs["Weight"] = f"{est['weight_kg']:g} kg"
                        if all(est.get(k) for k in ("length_cm", "width_cm", "height_cm")) and not specs.get("Size"):
                            specs["Size"] = (f"{est['length_cm']:g} x {est['width_cm']:g} x {est['height_cm']:g} cm")
                        add_step(res, "AI 估重数值", True,
                                 f"{specs.get('Weight','-')} / {specs.get('Size','-')}")
                    add_step(res, "AI 估重", rw.ok, rw.value[:40].replace("\n", " "))
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "AI 估重", False, str(ex)[:80])

            if (self.cfg.review_main_img_enable or self.cfg.review_detail_img_enable) and p.get("images"):
                ip = ImagePipeline(self.cfg)
                try:
                    verdict = ip.review(str(p["images"][0]), "main")
                    p["image_review"] = verdict[:300]
                    _v = (verdict or "").upper()
                    bad = ("FAIL" in _v) or any(k in verdict for k in ("不符合", "违规", "建议删除", "不建议", "含厂家", "含联系方式"))
                    if bad and self.cfg.review_block_on_fail and len(p["images"]) > 1:
                        p["images"] = p["images"][1:]
                        add_step(res, "图片审查", False, "不合规，已剔除该图")
                    elif bad and self.cfg.review_block_on_fail:
                        add_step(res, "图片审查", False, "不合规，已拦截：" + verdict[:50].replace("\n", " "))
                        res.ok = False
                        res.error = "图片审查不合规"
                        return res
                    add_step(res, "图片审查", True, ("⚠ " if bad else "") + verdict[:60].replace("\n", " "))
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片审查", False, "跳过：" + str(ex)[:100])

            p = self._price(p)
            r = p["price_result"]
            extra = ""
            if p.get("regional"):
                extra = " · 区域 " + " ".join(f"{k}{v}" for k, v in list(p['regional'].items())[:3])
            add_step(res, "价格计算", True, f"¥{r.final}（{r.note}）{extra}")

            if getattr(self.cfg, "generate_main_video", False) and p.get("images"):
                try:
                    import tempfile as _tf
                    from pathlib import Path as _P2
                    import requests as _rq2
                    from .video import build_slideshow, generate_carousel_video, probe as _vp

                    # 已存在视频且未允许覆盖 → 跳过
                    if p.get("main_video") and not getattr(self.cfg, "overwrite_existing_video", False):
                        add_step(res, "主图视频", True, "已存在视频，跳过")
                    elif getattr(self.cfg, "use_template_video", False) and getattr(self.cfg, "template_video_path", ""):
                        p["main_video"] = str(self.cfg.template_video_path)
                        add_step(res, "主图视频", True, "使用模板视频")
                    elif _vp()["available"]:
                        d2 = _P2(_tf.mkdtemp())
                        files = []
                        for i, u in enumerate(p["images"][:6]):
                            fp = d2 / f"v{i}.png"
                            try:
                                fp.write_bytes(_rq2.get(str(u), timeout=60).content); files.append(fp)
                            except Exception:
                                continue
                        if len(files) >= 2:
                            # 视频落到本地目录（老软件也是先存本地再上传）
                            try:
                                from pathlib import Path as _PV
                                vdir = _PV(getattr(self.cfg, "video_output_dir", "") or (_PV.home() / "Movies" / "AuroraList"))
                                vdir.mkdir(parents=True, exist_ok=True)
                                out = vdir / f"{p.get('id','product')}-{int(__import__('time').time())}.mp4"
                            except Exception:
                                out = d2 / "main.mp4"
                            generate_carousel_video(
                                files, out,
                                slot_seconds=float(getattr(self.cfg, "video_slot_seconds", 2.5) or 2.5),
                                transition_seconds=float(getattr(self.cfg, "video_transition_seconds", 0.5) or 0.5),
                                size=str(getattr(self.cfg, "video_size", "1080x1080") or "1080x1080"),
                                cover=bool(getattr(self.cfg, "video_cover", True)),
                                fade_edges=bool(getattr(self.cfg, "video_fade_edges", True)),
                            )
                            p["main_video"] = str(out)
                            add_step(res, "主图视频", True, str(out))
                        else:
                            add_step(res, "主图视频", False, "可用图片不足 2 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "主图视频", False, str(ex)[:80])

            if getattr(self.cfg, "schedule_publish", False) and (getattr(self.cfg, "schedule_time", "") or ""):
                p["scheduled_at"] = self.cfg.schedule_time
                add_step(res, "定时发布", True, f"已排定 {self.cfg.schedule_time}")
                add_step(res, "保存到店小秘", True, "定时任务已登记")
                return res

            if dry_run:
                add_step(res, "保存到店小秘", True, "dry-run：未写入")
            else:
                if not self.ws_endpoint:
                    raise RuntimeError("未连接浏览器（缺少 CDP 端点）")
                from ..integrations.editor_automation import apply_and_save, update_and_save, apply_template_fields
                snap_json = (getattr(self.cfg, "template_snapshot", "") or "").strip()
                _snap_obj = None
                if snap_json and getattr(self.cfg, "apply_template_on_save", True):
                    try:
                        import json as _jt
                        _snap_obj = _jt.loads(snap_json)
                        _extra = getattr(self.cfg, "custom_attributes", None) or []
                        if _extra:
                            _attrs = []
                            _seen = set()
                            for _x in list(_snap_obj.get("attributes") or []) + list(_extra):
                                if not isinstance(_x, str) or "=" not in _x:
                                    continue
                                _name = _x.split("=", 1)[0].strip().lower()
                                if not _name or _name in _seen:
                                    continue
                                _seen.add(_name)
                                _attrs.append(_x)
                            _snap_obj["attributes"] = _attrs
                    except Exception:
                        _snap_obj = None
                # 主图视频：先上传视频再保存
                if p.get("main_video") and getattr(self.cfg, "upload_main_video", True):
                    try:
                        from ..integrations.editor_automation import attach_video
                        rv = attach_video(self.ws_endpoint, str(p.get("id", "")), str(p["main_video"]))
                        add_step(res, "视频上传", rv.ok, rv.message[:80])
                    except Exception as ex:  # noqa: BLE001
                        add_step(res, "视频上传", False, str(ex)[:80])

                # 合并流程：一次开页面完成 写模板 + 改标题 + 保存（省一次导航）
                _fv = {}
                try:
                    import json as _jf
                    _fv = _jf.loads(getattr(self.cfg, "template_values", "") or "{}")
                except Exception:
                    _fv = {}
                r = apply_and_save(self.ws_endpoint, str(p.get("id", "")), _snap_obj,
                                   p.get("title_en") or None, form_values=_fv)
                if not r.ok:
                    raise RuntimeError(r.message)
                add_step(res, "保存到店小秘", True, r.message)

                # 发布动作：save_only=留在待发布 / publish=保存并发布
                _action = (getattr(self.cfg, "publish_action", "") or
                           ("publish" if getattr(self.cfg, "publish_after_save", False) else "save_only"))
                if _action == "publish":
                    try:
                        from ..integrations.editor_automation import publish_from_list
                        rp = publish_from_list(self.ws_endpoint, str(p.get("id", "")),
                                               confirm=True, dry_run=False,
                                               title_hint=p.get("title_en") or p.get("title", ""))
                        add_step(res, "发布到速卖通", rp.ok, rp.message[:100])
                        if not rp.ok:
                            res.error = (res.error + " " if res.error else "") + f"发布失败：{rp.message[:120]}"
                    except Exception as ex:  # noqa: BLE001
                        add_step(res, "发布到速卖通", False, str(ex)[:100])
            return res
        except Exception as e:  # noqa: BLE001
            res.ok = False
            res.error = str(e)
            emit("异常", "", str(e)[:160], "ERROR")
            add_step(res, "异常", False, str(e))
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


def run_many(pub: "Publisher", products: list[dict], workers: int = 4,
             dry_run: bool = True, on_done=None) -> list:
    """多线程并行处理多个产品（每个产品独立 publisher 实例，避免共享状态）。"""
    import concurrent.futures as _cf
    import copy as _copy

    results = []
    lock = __import__("threading").Lock()

    def work(prod):
        local_cfg = _copy.deepcopy(pub.cfg)
        local_pub = Publisher(local_cfg, dxm=pub.dxm, ws_endpoint=pub.ws_endpoint)
        try:
            r = local_pub.run(prod, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            r = PublishResult(product_id=str(prod.get("id", "")), ok=False, error=str(e))
        with lock:
            results.append(r)
        if on_done:
            try:
                on_done(r)
            except Exception:
                pass
        return r

    with _cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        list(ex.map(work, products))
    return results
