"""Repair failed listings from their latest details, with explicit bounded writes."""
from copy import deepcopy
from dataclasses import asdict
import re

from .listing_failures import classify
from .progress import emit
from .collect_store import exclusive


def amazon_plan(product, cfg, overrides=None, error=""):
    from .amazon_payload import attribute_present, parse_product_attr, split_bullets, attribute_value_text, language_tag_for, remove_contact_numbers
    from .amazon_pipeline import autofill_attributes
    from .amazon_prompts import clean_title, clean_bullet, sanitize_description
    from .amazon_auto import _PROMO_RE, _EMAIL_RE, _URL_RE, fix_title_repetition
    ov = deepcopy(overrides or {})
    actions, blockers = [], []
    attrs = parse_product_attr(product.get("productAttr"))
    findings = classify("amazon", error) if error else []
    if findings and all(f.kind == "reviewing" for f in findings):
        return dict(overrides=ov, actions=[], blockers=[], status="reviewing", findings=[asdict(f) for f in findings])
    # Formatting fixes preserve product numbers, specifications and existing facts.
    def clean(value):
        return re.sub(r"[ \t]{2,}", " ", _URL_RE.sub(" ", _EMAIL_RE.sub(" ", _PROMO_RE.sub(" ", remove_contact_numbers(str(value or "")))))).strip()
    title = str(ov.get("title") or product.get("title") or "")
    fixed_title = clean_title(clean(title))
    duplicate = next((f for f in findings if f.kind == "title_duplicate"), None)
    if duplicate or any(len(re.findall(r"\b" + re.escape(w) + r"\b", fixed_title, re.I)) > 2
                        for w in set(re.findall(r"[A-Za-z]{3,}", fixed_title))):
        fixed_title = fix_title_repetition(fixed_title, max_repeat=2)
    if fixed_title and fixed_title != title:
        ov["title"] = fixed_title
        actions.append("清理标题促销词、重复词和格式")
    effective_title = str(ov.get("title") or title)
    schema_title = attribute_value_text(attrs.get("item_name"), limit=20000)
    if effective_title and schema_title and schema_title != effective_title:
        ov.setdefault("attributes", {})["item_name"] = effective_title
        actions.append("同步标题与 item_name，清除旧标题残留")
    source_bullets = ov.get("bullets") or split_bullets(product.get("bulletPoint") or "")
    if isinstance(source_bullets, str):
        source_bullets = split_bullets(source_bullets)
    if not source_bullets and attrs.get("bullet_point"):
        source_bullets = [str(x.get("value") or "") for x in attrs["bullet_point"] if isinstance(x, dict) and x.get("value")]
    bullets = [clean_bullet(clean(value)) for value in source_bullets]
    bullets = [value for value in bullets if value]
    if bullets and (bullets != split_bullets(product.get("bulletPoint") or "") or "bullets" in ov):
        ov["bullets"] = bullets
        actions.append("整理现有卖点，保留全部真实条目")
    desc = str(ov.get("description") or product.get("description") or "")
    if not desc:
        desc = attribute_value_text(attrs.get("product_description"), limit=20000)
    cleaned_desc = sanitize_description(clean(desc)) if desc else ""
    if cleaned_desc and cleaned_desc != product.get("description"):
        ov["description"] = cleaned_desc
        actions.append("清理现有描述格式并同步内容")
    required = list(dict.fromkeys(k for f in findings for k in f.required))
    recoverable = [name for name in required if name != "externally_assigned_product_identifier"]
    supplied = ov.get("attributes") or {}
    missing = [name for name in recoverable if not attribute_present(attrs, name)
               and not attribute_present(supplied, name)]
    inferred = autofill_attributes(product, cfg, product.get("marketPlaceId") or "", ov,
                                   required=missing) if missing else {}
    mappings = {"item_name": effective_title, "bullet_point": bullets,
                "product_description": cleaned_desc, "variation_theme": product.get("variationTheme") or
                attribute_value_text(attrs.get("variation_theme"))}
    for name in recoverable:
        if attribute_present(supplied, name):
            actions.append("使用已设置的批量属性 " + name)
            continue
        value = mappings.get(name)
        if value:
            if name == "bullet_point":
                value = [{"value": b, "language_tag": language_tag_for(product.get("marketPlaceId", "")), "marketplace_id": product.get("marketPlaceId", "")} for b in value]
            ov.setdefault("attributes", {})[name] = value
            actions.append("由本商品资料回填 " + name)
        elif name in inferred:
            ov.setdefault("attributes", {})[name] = inferred[name]
            actions.append("由本商品资料回填 " + name)
        elif not attribute_present(attrs, name):
            blockers.append("缺少真实字段 " + name + "；请在相应类目/变种设置补充，不自动猜测")
    for finding in findings:
        # 13013 often accompanies field errors. Repair those fields for a draft,
        # but a catalog/identifier issue must not trigger blind publication loops.
        if not finding.repairable and finding.kind != "reviewing":
            blockers.append(finding.message + "：" + finding.action)
    return dict(overrides=ov, actions=list(dict.fromkeys(actions)), blockers=list(dict.fromkeys(blockers)),
                status="blocked" if blockers else "ready", findings=[asdict(f) for f in findings])


@exclusive
def run_failed_recovery(cfg, platform, *, dry_run=True, client=None, stop=None, progress=None, resume_id=None):
    from .collect_automation import client_for, isolated_config, snapshot, settings, identity, process_one
    if platform not in ("alibaba", "amazon"):
        raise ValueError("该平台暂未接入发布失败自动修复")
    from . import collect_store as store
    resumed = store.get(resume_id) if resume_id else None
    if resumed:
        if resumed["scope"] != store.scope(cfg, platform) or resumed["kind"] != "recovery":
            raise ValueError("任务所属店铺、站点或类型不匹配")
        from ..config import AppConfig
        data = cfg.as_dict()
        data.update(resumed["config"])
        cfg = AppConfig(data)
        dry_run = bool(resumed["dry"])
    from .collect_preflight import check
    problems = check(cfg, platform, paid=False)
    if problems:
        raise ValueError("；".join(issue.title + "：" + issue.detail for issue in problems))
    config = isolated_config(cfg, platform)
    from .collect_runtime import ReadClient, RunPolicy, RunStopped
    client = ReadClient(client or client_for(platform, config), config, platform, stop)
    policy = RunPolicy(config, platform, stop)
    op = str(getattr(cfg, platform + "_collect_op", "1") or "1")
    if not dry_run and op not in ("1", "2"):
        raise ValueError("失败商品请选择保存修复草稿或修复后发布；不能再次移入待发布")
    try:
        rows = [item["row"] for item in resumed["items"]] if resumed else snapshot(client, platform, state="publishFail",
            shop_id=str(getattr(config, platform + "_shop_id", "") or ""),
            site_id=str(getattr(config, "amazon_site", "") or "") if platform == "amazon" else "",
            keyword=str(getattr(cfg, platform + "_collect_keyword", "") or ""),
            limit=int(getattr(cfg, platform + "_collect_limit", 10)), stop=stop)
    except RunStopped:
        emit(platform + " · 失败修复", "已停止", "读取范围时停止，未开始写入")
        return []
    maximum = min(3, max(1, int(getattr(cfg, platform + "_collect_max_attempts", 2) or 2)))
    results = []
    from .progress import BUS
    BUS.begin(len(rows))
    run_id = resume_id or store.create(cfg, platform, rows, dry=dry_run, kind="recovery")
    previous_items = {item["id"]: item for item in resumed["items"]} if resumed else {}
    for index, row in enumerate(rows, 1):
        if not policy.before(index, dry_run=dry_run):
            break
        pid = identity(row)
        client.prepare_item()
        old = previous_items.get(pid, {})
        if old.get("phase") == "done":
            results.append(old["result"])
            BUS.done_stage()
            continue
        rec = dict(id=pid, ok=False, saved=False, checked=dry_run, actions=[], blockers=[], attempts=0, error="", status="blocked")
        did_submit = old.get("phase") == "submitting"
        try:
            if old.get("phase") == "submitting":
                client.fresh()
                latest = client.product_detail(pid)
                product = latest.get("product", latest)
                rec.update(outcome="uncertain", state=str(product.get("dxmOfflineState") or product.get("dxmState") or ""),
                           error="上次修复在提交期间中断；已回读状态，未重复提交")
                if rec["state"] in ("online", "onSelling", "publishing", "waitPublish"):
                    rec.update(ok=True, outcome="published" if rec["state"] in ("online", "onSelling") else "processing")
                raise InterruptedRecovery()
            store.item_update(run_id, pid, "preparing")
            previous = None
            for attempt in range(1, maximum + 1):
                detail = client.product_detail(pid)
                from .collect_runtime import verified_product
                product = verified_product(detail, pid)
                # A stale failure-list entry may already have become online.
                state = str(product.get("dxmOfflineState") or product.get("dxmState") or "")
                if state in ("online", "onSelling", "publishing", "waitPublish", "auditing", "reviewing"):
                    rec.update(ok=True, state=state, status="already_submitted", error="已上线或正在处理，无需重发")
                    break
                if platform == "amazon" and state not in ("", "offline", "publishFail"):
                    rec.update(ok=False, state=state, error="商品已离开发布失败列表，请核对当前状态")
                    break
                error = str(product.get("errMsg", product.get("err_msg", row.get("err_msg") or row.get("errMsg"))) or "").strip()
                if platform == "amazon":
                    plan = amazon_plan(product, config, settings(cfg, platform), error)
                    if not error:
                        plan["blockers"].append("平台未返回发布失败原因，请刷新失败列表并核对平台状态")
                        plan["status"] = "blocked"
                else:
                    findings = classify(platform, error)
                    plan = dict(overrides=settings(cfg, platform), actions=[],
                        blockers=[f.message + "：" + f.action for f in findings if f.kind != "reviewing"],
                        status="reviewing" if all(f.kind == "reviewing" for f in findings) else "blocked")
                rec.update(actions=plan["actions"], blockers=plan["blockers"], status=plan["status"],
                           error="；".join(plan["blockers"]))
                emit(platform + " · 失败诊断", plan["status"], error, product=pid)
                for action in plan["actions"]:
                    emit(platform + " · 自动修复", "已准备", action, product=pid)
                if plan["status"] == "reviewing":
                    rec.update(ok=True, error="平台审核中，已跳过重复提交")
                    break
                if dry_run or (plan["blockers"] and op == "2"):
                    rec["ok"] = not plan["blockers"]
                    break
                if not plan["actions"]:
                    rec.update(status="blocked", error=rec["error"] or "没有可自动修复的变更，请核对平台错误后手动处理")
                    break
                if stop and stop():
                    raise RunStopped("已停止，本件未继续提交")
                if plan["overrides"] == previous:
                    rec.update(error="同一修复方案再次失败，停止重复提交", status="blocked")
                    break
                previous = deepcopy(plan["overrides"])
                def before_submit():
                    nonlocal did_submit
                    if client.read_stopped:
                        raise RunStopped("读取已停止，本件尚未提交")
                    store.item_update(run_id, pid, "submitting", prepared=plan["overrides"])
                    did_submit = True

                did_submit = False
                client.fresh()
                result = process_one(platform, pid, config, client, plan["overrides"], dry_run=False,
                                     op=op, use_ai=False, before_submit=before_submit)
                if client.read_stopped and not did_submit and not result.saved:
                    raise RunStopped("读取已停止，本件尚未提交")
                rec.update(attempts=attempt, saved=rec["saved"] or bool(result.saved),
                           ok=bool(result.ok) and bool(result.saved) and not plan["blockers"],
                           error="；".join(plan["blockers"]) or str(getattr(result, "error", "") or ""))
                if did_submit and not result.saved:
                    from .collect_runtime import transient_error
                    if transient_error(rec["error"]):
                        rec["outcome"] = "uncertain"
                if result.saved:
                    verified_state = str(getattr(result, "publish_state", "") or "")
                    verdict = getattr(result, "publish_status", "")
                    if verdict == "reviewing":
                        rec["outcome"] = "reviewing"
                    elif verdict == "unknown":
                        rec["outcome"] = "uncertain"
                    if verified_state:
                        rec["state"] = verified_state
                    elif op != "2":
                        latest = client.product_detail(pid)
                        readback = verified_product(latest, pid)
                        rec["state"] = str(readback.get("dxmOfflineState") or readback.get("dxmState") or "")
                if rec["ok"] or op == "1" or not result.saved:
                    rec["status"] = "repaired" if rec["ok"] else "partial" if rec["saved"] else "blocked"
                    break
                # Only an explicit fresh platform failure may be retried; unknown
                # responses and in-flight saves are never resent automatically.
                if (str(getattr(result, "publish_state", "") or "") != "publishFail"
                        or getattr(result, "publish_status", "") != "failed"):
                    break
        except InterruptedRecovery:
            pass
        except RunStopped as exc:
            rec.update(ok=False, status="stopped", outcome="uncertain" if did_submit else "stopped",
                       error=str(exc), halt_reason=str(exc), resume_pending=not did_submit)
        except Exception as exc:
            rec.update(ok=False, error=str(exc), status="blocked")
            from .collect_runtime import transient_error
            if did_submit and transient_error(exc):
                rec["outcome"] = "uncertain"
        rec["run_id"] = run_id
        rec["outcome"] = store.status(rec, op)
        phase = "queued" if rec.get("resume_pending") else "done"
        store.item_update(run_id, pid, phase, result=rec)
        results.append(rec)
        BUS.done_stage()
        emit(platform + " · 失败处理结果", rec["status"], rec["error"] or "修复检查完成",
             product=pid, level="OK" if rec["ok"] else "WARN")
        if progress:
            progress(index, len(rows), rec)
        if rec.get("halt_reason"):
            break
        if not policy.after(rec, client):
            store.item_update(run_id, pid, phase, result=rec)
            break
    store.finish(run_id, "stopped" if len(results) < len(rows) or (results and results[-1].get("halt_reason")) else "complete")
    return results


class InterruptedRecovery(Exception):
    """Internal control flow: interrupted writes must never be automatically replayed."""
