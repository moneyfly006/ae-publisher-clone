"""Platform-specific collectors with a fixed, deduplicated scope before any write."""
from __future__ import annotations

from copy import deepcopy
import json
import math

from ..config import AppConfig
from .collect_runtime import ReadClient, RunPolicy, RunStopped, product_scope_issue, transient_error
from .progress import emit
from .platform_presets import ALIBABA_PROMPTS, resolve_config_prompts
from .collect_store import exclusive

PLATFORMS = ("alibaba", "amazon", "tiktok", "shopee")


def client_for(platform, cfg):
    if platform not in PLATFORMS:
        raise ValueError("不支持的自动处理平台")
    if platform == "shopee":
        from ..integrations.shopee import make_client
    else:
        from importlib import import_module
        make_client = import_module(f"aeclone.services.{platform}_publish").make_client
    return make_client(cfg)


def identity(row):
    return str(row.get("idStr") or row.get("id") or "").strip()


def snapshot(client, platform, *, keyword="", limit=10, stop=None, progress=None,
             page_size=100, state="draft", shop_id="", site_id=""):
    """Resolve all selected IDs before saving (saving can remove rows from the source).

    Never treat a repeated page or a partial failed scan as the complete scope.
    Alibaba's native search differs; use the same literal title/SKU/ID filter locally.
    """
    if platform not in PLATFORMS:
        raise ValueError("不支持的自动处理平台")
    limit = int(limit)
    if limit < 0:
        raise ValueError("数量不能小于 0")
    seen, selected, signatures = set(), [], set()
    pages, total = 0, 0
    for page in range(1, 10001):
        if stop and stop():
            raise RunStopped("读取范围时停止")
        if platform == "alibaba":
            args = (page, page_size, state, shop_id) if shop_id else (page, page_size, state)
            raw = client.list_products(*args)
            info = ((raw.get("data") or {}).get("page") or {})
            rows = info.get("list") or []
            returned_pages = info.get("totalPage")
            returned_total = info.get("totalSize")
        else:
            filters = {"site": site_id} if platform == "tiktok" and site_id else {}
            raw = client.list_products(state, page, page_size, shop_id or "-1", "", **filters)
            rows = raw.get("items") or []
            returned_pages = raw.get("total_page", raw.get("pages"))
            returned_total = raw.get("total")
        if stop and stop():
            raise RunStopped("读取范围时停止")
        if not isinstance(rows, list):
            raise ValueError("采集箱接口没有返回有效列表")
        pages = int(returned_pages or 0) or pages
        total = int(returned_total or 0) or total
        signature = tuple(identity(r) for r in rows if isinstance(r, dict))
        if signature and signature in signatures:
            raise ValueError("采集箱接口重复返回同一页，已停止，未执行写入")
        signatures.add(signature)
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = identity(row)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if product_scope_issue(platform, row, shop_id, site_id):
                continue
            text = " ".join(str(row.get(k) or "") for k in
                            ("subject", "title", "productName", "itemName", "name", "sku", "sellerSku"))
            if keyword.strip().casefold() not in (pid + " " + text).casefold():
                continue
            selected.append(deepcopy(row))
            if limit and len(selected) >= limit:
                return selected
        if progress:
            progress(page, len(selected))
        if not rows:
            if (total and len(seen) < total) or page < pages:
                raise ValueError("采集箱分页提前返回空页，请重试；未执行写入")
            return selected
        if pages:
            if page < pages:
                continue
            if total and len(seen) < total:
                raise ValueError("采集箱分页未返回全部商品，请重试；未执行写入")
            return selected
        if total and len(seen) >= total:
            return selected
        if not total and len(rows) < page_size:
            return selected
    raise ValueError("采集箱页数超出上限，未执行写入")


def settings(cfg, platform):
    raw = getattr(cfg, platform + "_collect_overrides", "{}") or "{}"
    data = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
    if not isinstance(data, dict):
        raise ValueError("批量预设必须是对象")
    if platform == "amazon" and "price" in data:
        if "standard_price" in data and str(data["standard_price"]) != str(data["price"]):
            raise ValueError("旧版与当前的统一售价设置冲突")
        data["standard_price"] = data.pop("price")
    validate_overrides(data)
    attribute_key = "product_attributes" if platform == "tiktok" else "attributes"
    if attribute_key in data:
        expected = list if platform in ("tiktok", "shopee") else dict
        if not isinstance(data[attribute_key], expected):
            raise ValueError("批量属性的格式无效")
    return data


def validate_overrides(data):
    for key in ("price", "standard_price", "originalPrice", "weight", "length", "width", "height",
                "packageLength", "packageWidth", "packageHeight", "quantity", "stock", "normalStock", "daysToShip"):
        if key not in data:
            continue
        if isinstance(data[key], bool):
            raise ValueError(f"{key} 必须填写有效数字，不能使用开关值")
        try:
            number = float(data[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须填写有效数字") from None
        integer = key in ("quantity", "stock", "normalStock", "daysToShip")
        zero_allowed = key in ("quantity", "stock", "normalStock")
        if not math.isfinite(number) or number < 0 or (number == 0 and not zero_allowed) or (integer and int(number) != number):
            raise ValueError(f"{key} 必须是{'大于等于0的整数' if zero_allowed else '正数' + ('整数' if integer else '')}")
    for key, expected in (("price_tiers", list), ("wholesale_trade", dict)):
        if key in data and (not isinstance(data[key], expected) or
                            (expected is list and any(not isinstance(row, dict) for row in data[key]))):
            raise ValueError(("阶梯报价" if key == "price_tiers" else "包装、库存与交期") + "的格式无效")
    def positive(value, label, integer=False, zero=False):
        try:
            number = float(value)
            if isinstance(value, bool) or not math.isfinite(number) or number < 0 or (not zero and number == 0) or (integer and int(number) != number):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError(label + "必须为" + ("非负" if zero else "正") + ("整数" if integer else "数")) from None
    tiers = data.get("price_tiers", [])
    if len(tiers) > 4:
        raise ValueError("阶梯报价最多4档")
    seen = set()
    for row in tiers:
        positive(row.get("start_quantity", row.get("qty")), "起订数量", True)
        positive(row.get("price"), "阶梯单价")
        quantity = int(float(row.get("start_quantity", row.get("qty"))))
        if quantity in seen:
            raise ValueError("阶梯报价起订数量不能重复")
        seen.add(quantity)
    trade = data.get("wholesale_trade", {})
    if trade.get("weight") not in (None, ""):
        positive(trade["weight"], "包装重量")
    for key, label, limit, columns in (
            ("deliver_periods", "交期档位", 3, (("quantity", "数量上限", False), ("process_period", "发货天数", False))),
            ("inventory", "仓库库存", 30, (("value", "仓库库存", True),))):
        rows = trade.get(key, [])
        if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row, dict) for row in rows):
            raise ValueError(label + "的格式或数量不正确")
        for row in rows:
            for column, title, zero in columns:
                positive(row.get(column), title, True, zero)
            if key == "inventory" and not str(row.get("warehouseCode") or "").strip():
                raise ValueError("仓库编码不能为空")


def isolated_config(cfg, platform):
    """Single-item editors and old installation presets must never leak into a batch."""
    data = deepcopy(cfg.as_dict() if hasattr(cfg, "as_dict") else dict(cfg))
    # One budget reservation must map to at most one paid image request.
    # Existing manual/AliExpress image retry preferences remain unchanged.
    data["image_retry_times"] = 0
    keep = ("cookies", "shop_id", "shop_name", "site", "site_code", "site_name", "mode")
    if platform == "alibaba":
        keep += ("min_order_quantity", "unit_type", "weight", "package_size",
                 "package_length", "package_width", "package_height", "freight_template_id",
                 "logistics_property", "default_sample_service", "default_deliver_periods",
                 "hs_code", "company_desc", "company_faq", "company_name", "video_source",
                 "video_size", "video_slot_seconds", "video_transition_seconds")
    for key in list(data):
        if key.startswith(platform + "_"):
            suffix = key[len(platform) + 1:]
            if suffix not in keep and not any(word in suffix for word in
                    ("prompt", "preset", "auto_", "collect_", "publish_wait")):
                data[key] = ""
    # Alibaba 用户保存的必填属性/公司资料必须带进批量；只清掉单品的临时覆盖。
    if platform == "alibaba":
        data["alibaba_attr_presets"] = getattr(cfg, "alibaba_attr_presets", "") or ""
        for key in ("company_desc", "company_faq", "ai_preset"):
            data["alibaba_" + key] = ""
        data["price_fixed"] = ""
        # 用户设置的 MOQ/重量/包装/运费/发货期必须生效；商品已有真实值仍由
        # build_alibaba_save 内部按“只补缺失”处理。
        data["alibaba_preserve_trade"] = True
    if platform == "alibaba":
        for suffix in ("company_desc", "company_faq"):
            data["alibaba_" + suffix] = data.get("alibaba_collect_" + suffix, "")
        for kind, prompt in ALIBABA_PROMPTS.items():
            data["alibaba_" + kind + "_prompt"] = data.get("alibaba_" + kind + "_prompt") or prompt
    if platform == "amazon":
        data["amazon_bullet_count"] = 5
        data["amazon_autofill_attrs"] = True
        data["amazon_last_category_required"] = ""
    return AppConfig(resolve_config_prompts(data, platform))


def process_one(platform, pid, cfg, client, overrides, *, dry_run, op, use_ai, before_submit=None):
    if platform == "alibaba":
        from .alibaba_pipeline import run_alibaba_product
        if overrides.get("price") or overrides.get("price_tiers"):
            overrides.setdefault("price_type", "ladder_price")
        if use_ai:
            product = client.product_detail(pid)
            # 补齐模式才把已有字段保留；重写模式必须交给 AI，不能因为字段已存在就关掉 AI。
            _rewrite = str(getattr(cfg, "alibaba_collect_ai_mode", "missing") or "missing") == "rewrite"
            if not _rewrite:
                for native, key in (("subject", "subject"), ("textDesc", "text_desc"), ("keywords", "keywords")):
                    if product.get(native):
                        overrides.setdefault(key, product[native])
                if all(product.get(k) for k in ("subject", "textDesc", "keywords")):
                    use_ai = False
        return run_alibaba_product(pid, cfg, client=client, overrides=overrides,
                                  dry_run=dry_run, op=op, use_ai=use_ai, before_submit=before_submit,
                                  repair_score=bool(getattr(cfg, "alibaba_collect_auto_repair", True)))
    if platform in ("amazon", "tiktok"):
        if platform == "amazon" and bool(getattr(cfg, "amazon_collect_auto_repair", True)):
            from .listing_recovery import amazon_plan
            detail = client.product_detail(pid)
            plan = amazon_plan(detail.get("product", detail), cfg, overrides)
            overrides = plan["overrides"]
            for action in plan["actions"]:
                emit("amazon · 自动修复", "已准备", action, product=pid)
        from importlib import import_module
        run = getattr(import_module(f"aeclone.services.{platform}_pipeline"), f"run_{platform}_product")
        submit_options = {"before_submit": before_submit} if before_submit else {}
        return run(pid, cfg, overrides, client=client, dry_run=dry_run,
                   confirm_write=not dry_run, use_ai=use_ai, op=op, **submit_options)
    from . import shopee
    if use_ai:
        product = client.product_detail(pid)["product"]
        title_key = "name" if getattr(cfg, "shopee_mode", "global") == "site" else "itemName"
        for kind, key in (("title", title_key), ("description", "description")):
            if not product.get(key) and not overrides.get(key):
                overrides[key] = shopee.generate_copy(product, cfg, kind,
                    site=getattr(cfg, "shopee_site", "global") or "global")
    if dry_run:
        from types import SimpleNamespace
        _, issues = shopee.prepare(pid, cfg, overrides, client=client)
        return SimpleNamespace(ok=not issues, saved=False, error="；".join(issues))
    submit_options = {"before_submit": before_submit} if before_submit else {}
    return shopee.save(pid, cfg, overrides, op=op, client=client, **submit_options)


def _transient_image_error(exc) -> bool:
    text = str(exc or "")
    return any(key in text for key in ("524", "520", "521", "522", "502", "503",
                                       "timeout", "Timeout", "timed out",
                                       "Read timed out", "Connection reset",
                                       "Connection aborted"))


def _image_source_unavailable(exc: Exception) -> bool:
    """源图 403/404（CDN 防盗链/已删除）不能拖垮整件；保留原图继续。"""
    text = str(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (403, 404) or "403" in text or "404" in text or "Forbidden" in text or "Not Found" in text


def process_images(platform, pid, cfg, client, overrides):
    """Apply configured rules to the product's native media fields."""
    from ..config import config_dir
    from .collect_store import BudgetExceeded
    from .collect_budget import paid
    from .collect_runtime import verified_product
    from .image_pipeline import ImagePipeline
    from .platform_media import GROUPS, batch_rules, edit_and_host, read_groups, write_group

    product = verified_product(client.product_detail(pid), pid)
    mode = getattr(cfg, "shopee_mode", "global")
    groups = read_groups(platform, product, overrides, mode)
    rules = batch_rules(cfg, platform)
    old_rules = getattr(cfg, platform + "_collect_image_rules", "") in ("", None)
    if old_rules and not str(getattr(cfg, platform + "_collect_image_prompt", "") or "").strip():
        raise ValueError("请先在图片页填写自动处理提示词")
    language = "英语"
    if platform == "amazon":
        from .amazon_prompts import site_meta
        language = site_meta(product.get("marketPlaceId") or "").get("lang_name", "英语")
    elif platform == "tiktok":
        from .tiktok_prompts import site_meta
        meta = site_meta(product.get("site") or getattr(cfg, "tiktok_site", ""))
        language = meta.get("language_name") or meta.get("lang_name") or "英语"
    elif platform == "shopee":
        from .shopee import LANGUAGES
        language = LANGUAGES.get(getattr(cfg, "shopee_site", "global"), "英语")
    plan = {}
    for rule in rules:
        group = rule["group"]
        positions = range(1, len(groups[group]) + 1) if rule["position"] == "all" else [int(rule["position"])]
        for pos in positions:
            if pos > len(groups[group]):
                continue
            if (group, pos) in plan:
                raise ValueError("同一张图片匹配了多条自动图片规则")
            plan[group, pos] = rule["prompt"] + "\n图片文字目标语言：" + language
    if not plan:
        if old_rules:
            raise ValueError("图片处理范围无效或商品没有图片")
        return

    pipeline = ImagePipeline(cfg, workdir=config_dir() / "cache" / "platform-media")
    output = {group: list(urls) for group, urls in groups.items()}
    changed = set()
    for (group, pos), prompt in plan.items():
        label = f"{GROUPS[platform][group].label} · 第 {pos} 张"
        source = groups[group][pos - 1]
        emit(platform + " · 自动图片", "开始", label, product=pid)
        try:
            hosted = paid(cfg, platform, "image", product, [source, prompt],
                          lambda: edit_and_host(pipeline, source, prompt))
        except BudgetExceeded:
            raise
        except Exception as exc:
            # 图片网关瞬时故障 / 源图 403 防盗链：保留原图继续保存，不能整件失败。
            if _transient_image_error(exc) or _image_source_unavailable(exc):
                emit(platform + " · 自动图片", "已跳过",
                     f"{label} 源图/图片接口不可用，保留原图继续保存：{str(exc)[:120]}", "WARN")
                continue
            raise ValueError(f"{label}处理失败，原图保留，本件未提交：{exc}") from exc
        if not hosted:
            raise ValueError(f"{label}转存失败，原图保留，本件未提交")
        output[group][pos - 1] = hosted
        changed.add(group)
        emit(platform + " · 自动图片", "完成", label, product=pid)
    for group in changed:
        write_group(platform, product, overrides, group, output[group], mode)


@exclusive
def run_collect(cfg, platform, *, dry_run=True, client=None, stop=None, progress=None,
                rows=None, resume_id=None):
    """One run, one config snapshot, one source snapshot; per-item failure continues."""
    if platform not in PLATFORMS:
        raise ValueError("不支持的自动处理平台")
    from .collect_preflight import check
    from . import collect_store as store
    resumed = store.get(resume_id) if resume_id else None
    if resumed:
        if resumed["scope"] != store.scope(cfg, platform) or resumed["platform"] != platform or resumed["kind"] != "collect":
            raise ValueError("任务所属店铺、站点或类型不匹配，不能继续")
        data = cfg.as_dict()
        data.update(resumed["config"])
        approved_limits = getattr(cfg, "_resume_limits", {})
        for key, value in approved_limits.items():
            if key in (platform + "_collect_ai_max_calls", platform + "_collect_ai_budget"):
                data[key] = value
        cfg = AppConfig(data)
        dry_run = bool(resumed["dry"])
    issues = check(cfg, platform, paid=not dry_run)
    if issues:
        raise ValueError("；".join(item.title + "：" + item.detail for item in issues))
    if resumed and approved_limits:
        store.update_limits(resume_id, approved_limits)
    config = isolated_config(cfg, platform)
    overrides = settings(cfg, platform)
    op = str(getattr(cfg, platform + "_collect_op", "1") or "1")
    if op not in ("1", "2", "3"):
        raise ValueError("无效的保存去向")
    client = ReadClient(client or client_for(platform, config), config, platform, stop)
    policy = RunPolicy(config, platform, stop)
    emit(platform + " · 自动采集箱", "开始", "读取处理范围，准备后逐个执行")
    # 已勾选商品优先：读取全量源数据再按 ID 过滤，不能被关键词/数量限制提前截断。
    try:
        _picked_raw = str(getattr(cfg, platform + "_collect_selected_ids", "") or "[]")
        _picked = json.loads(_picked_raw) if _picked_raw.strip() else []
        _picked = [str(x) for x in _picked if str(x).strip()] if isinstance(_picked, list) else []
    except (ValueError, TypeError):
        _picked = []
    try:
        if _picked and rows is None and not resumed:
            selected = snapshot(client, platform, keyword="", limit=0,
                                shop_id=client.expected_shop, site_id=client.expected_site,
                                stop=stop,
                                progress=lambda p, n: emit(platform + " · 读取范围", "进行中",
                                                           f"读取全量第 {p} 页 · 已读 {n} 件"))
        else:
            selected = [item["row"] for item in resumed["items"]] if resumed else snapshot(client, platform,
                keyword=str(getattr(cfg, platform + "_collect_keyword", "") or ""),
                shop_id=client.expected_shop, site_id=client.expected_site,
                limit=int(getattr(cfg, platform + "_collect_limit", 10)), stop=stop,
                progress=lambda p, n: emit(platform + " · 读取范围", "进行中", f"第 {p} 页 · 匹配 {n} 件")) if rows is None else deepcopy(rows)
    except RunStopped:
        emit(platform + " · 自动采集箱", "已停止", "读取范围时停止，未开始写入")
        return []
    # 用户在「选择商品」里勾选的范围：只处理这些商品。
    if _picked:
        _want = set(_picked)
        selected = [r for r in selected if identity(r) in _want]
        _miss = _want - {identity(r) for r in selected}
        emit(platform + " · 选择范围", "完成", f"仅处理已选 {len(selected)} 件")
        if _miss:
            emit(platform + " · 选择范围", "提醒",
                 f"有 {len(_miss)} 个已选商品不在当前店铺/站点/数据源中，已跳过")

    emit(platform + " · 范围已确定", "完成", f"共 {len(selected)} 件，去向 {op}")
    from .progress import BUS
    BUS.begin(len(selected))
    run_id = resume_id or store.create(cfg, platform, selected, dry=dry_run)
    from .collect_budget import Budget
    object.__setattr__(config, "_collect_budget", Budget(config, platform, run_id))
    previous = {item["id"]: item for item in resumed["items"]} if resumed else {}
    results = []
    for i, row in enumerate(selected, 1):
        if not policy.before(i, dry_run=dry_run):
            break
        pid = identity(row)
        old = previous.get(pid, {})
        if old.get("phase") == "done":
            results.append(old["result"])
            BUS.done_stage()
            continue
        client.prepare_item()
        if old.get("phase") == "submitting":
            # Never infer that an interrupted network call did not write.
            rec = dict(id=pid, ok=False, saved=False, checked=dry_run, outcome="uncertain", run_id=run_id,
                       error="上次在提交期间中断；已跳过重发，请回读平台状态确认")
            try:
                client.fresh()
                detail = client.product_detail(pid)
                p = detail.get("product", detail)
                rec["state"] = str(p.get("dxmOfflineState") or p.get("dxmState") or "")
                if rec["state"] in ("online", "onSelling", "publishing", "waitPublish"):
                    rec.update(ok=True, outcome="published" if rec["state"] in ("online", "onSelling") else "processing", error="已回读平台状态，未重复提交")
            except Exception as exc:
                rec["error"] += "：" + str(exc)
            store.item_update(run_id, pid, "done", result=rec)
            results.append(rec)
            BUS.done_stage()
            continue
        store.item_update(run_id, pid, "preparing")
        emit(platform + " · 商品处理", "开始", f"{i}/{len(selected)}", product=pid)
        did_submit = False

        def before_submit():
            nonlocal did_submit
            if client.read_stopped:
                raise RunStopped("读取已停止，本件尚未提交")
            store.item_update(run_id, pid, "submitting")
            did_submit = True

        try:
            item_overrides = deepcopy(overrides)
            if not dry_run:
                from .collect_ai import prepare
                item_overrides = prepare(platform, pid, config, client, item_overrides)
            if not dry_run and bool(getattr(config, platform + "_collect_images", False)):
                process_images(platform, pid, config, client, item_overrides)
            store.item_update(run_id, pid, "prepared", prepared=item_overrides)
            client.fresh()
            submit_options = {"before_submit": before_submit} if not dry_run else {}
            result = process_one(platform, pid, config, client, item_overrides,
                                 dry_run=dry_run, op=op, use_ai=False, **submit_options)
            if client.read_stopped and not did_submit and not result.saved:
                raise RunStopped("读取已停止，本件尚未提交")
            rec = {"id": pid, "ok": bool(result.ok), "saved": bool(result.saved),
                   "error": str(getattr(result, "error", "") or getattr(result, "message", "") or ""),
                   "checked": dry_run, "run_id": run_id,
                   "state": str(getattr(result, "publish_state", "") or getattr(result, "state", "") or "")}
            if platform == "amazon" and op == "2":
                status = getattr(result, "publish_status", "")
                if status == "reviewing":
                    rec["outcome"] = "reviewing"
                elif status == "unknown" and rec["saved"]:
                    rec["outcome"] = "uncertain"
            if platform == "alibaba" and getattr(result, "initial_score", None) is not None:
                rec.update(initial_score=result.initial_score, score=result.score, score_passed=result.score_passed,
                           initial_score_problems=result.initial_score_problems, score_problems=result.score_problems,
                           actions=result.score_actions)
            if not dry_run and rec["ok"] and not rec["saved"]:
                rec.update(ok=False, error=rec["error"] or "流程未执行保存，请检查测试模式或授权状态")
            if did_submit and not rec["saved"] and transient_error(rec["error"]):
                rec["outcome"] = "uncertain"
            if rec["saved"] and op in ("1", "3"):
                try:
                    latest = client.product_detail(pid)
                    product = latest.get("product", latest)
                    rec["state"] = str(product.get("dxmOfflineState") or product.get("dxmState") or "")
                    if platform == "amazon" and op == "3" and rec["state"].casefold() == "draft":
                        rec.update(ok=False, outcome="uncertain",
                                   error="已收到保存响应，但商品回读仍在采集箱，待发布状态尚未确认")
                    elif platform == "amazon" and op == "3" and rec["state"].casefold() == "waitpublish":
                        rec["outcome"] = "pending"
                    emit(platform + " · 保存结果回读", "完成", rec["state"] or "已重新读取商品详情", product=pid)
                except Exception as exc:
                    rec.update(ok=False, error=f"商品已保存，结果回读失败：{exc}")
        except Exception as exc:
            rec = {"id": pid, "ok": False, "saved": False, "error": str(exc), "checked": dry_run, "run_id": run_id}
            if did_submit and (transient_error(exc) or isinstance(exc, RunStopped)):
                rec["outcome"] = "uncertain"
            if isinstance(exc, RunStopped) and not did_submit:
                rec.update(outcome="stopped", halt_reason=str(exc), resume_pending=True)
            elif isinstance(exc, store.BudgetExceeded):
                rec["halt_reason"] = str(exc)
                rec["resume_pending"] = not isinstance(exc, store.UnknownAIRequest)
        rec["outcome"] = store.status(rec, op)
        phase = "queued" if rec.get("resume_pending") else "done"
        store.item_update(run_id, pid, phase, result=rec)
        results.append(rec)
        BUS.done_stage()
        emit(platform + " · 商品结果", "已停止" if rec["outcome"] == "stopped" else "完成" if rec["ok"] else "失败",
             rec["error"] or ("检查通过" if dry_run else "已保存"),
             product=pid, level="WARN" if rec["outcome"] == "stopped" else "OK" if rec["ok"] else "ERROR")
        if progress:
            progress(i, len(selected), rec)
        if rec.get("halt_reason"):
            break
        if not policy.after(rec, client):
            store.item_update(run_id, pid, phase, result=rec)
            break
    stopped = bool(stop and stop())
    halted = bool(results and results[-1].get("halt_reason"))
    store.finish(run_id, "stopped" if stopped or halted or len(results) < len(selected) else "complete")
    emit(platform + " · 自动采集箱", "已停止" if stopped or halted else "结束",
         f"范围 {len(selected)} · 完成 {len(results)} · 成功 {sum(r['ok'] for r in results)} · 失败 {sum(not r['ok'] for r in results)}")
    return results
