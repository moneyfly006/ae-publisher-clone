"""Searchable durable execution results, without a collection selection list."""
import csv
import io
from pathlib import Path
import threading

import flet as ft

from ..services import collect_store as store


def repair_page(platform, record):
    error = str(record.get("error") or "").lower()
    from .collect_workflow import SECTIONS, CONNECTION_PAGE, RUN_PAGE
    if any(word in error for word in ("登录", "cookie", "店铺")):
        return CONNECTION_PAGE[platform]
    section = "category" if any(word in error for word in ("属性", "类目", "identifier", "gtin", "upc", "variation_theme")) else (
        "media" if any(word in error for word in ("图片", "图床", "image")) else
        "stock" if any(word in error for word in ("sku", "库存", "品牌")) else
        "price" if any(word in error for word in ("价格", "运费", "物流", "重量", "尺寸", "交期")) else
        "text" if any(word in error for word in ("标题", "描述", "五点", "keyword", "bullet", "description")) else "")
    return next((page for page, name in SECTIONS[platform].items() if name == section), RUN_PAGE[platform])


def csv_text(records):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    has_scores = any(row.get("initial_score") is not None for row in records)
    writer.writerow(("商品ID", "状态", "平台状态", "结果说明", "修改记录",
                     *(("初评分", "最终评分", "扣分原因", "剩余问题") if has_scores else ())))
    def safe(value):
        value = str(value or "")
        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
    for record in records:
        values = [safe(record.get("id")), store.STATUS_LABELS.get(record.get("outcome"), "需补资料"),
                  safe(record.get("state")), safe(record.get("error")), safe("；".join(record.get("actions", [])))]
        if has_scores:
            values.extend([record.get("initial_score"), record.get("score"),
                           safe("；".join(record.get("initial_score_problems", []))),
                           safe("；".join(record.get("score_problems", [])))])
        writer.writerow(values)
    return "\ufeff" + stream.getvalue()


def panel(app, platform):
    from . import app as ui
    from .collect_workflow import launch
    from ..services.collect_profiles import label
    history = store.history(app.cfg, platform)
    if not hasattr(app, "_collect_view_run"):
        app._collect_view_run = {}
    selected = app._collect_view_run.get(platform)
    run = store.get(selected) if selected and any(row["id"] == selected for row in history) else store.latest(app.cfg, platform)
    records = [item["result"] for item in run["items"] if item["result"]] if run else (
        [] if platform == "amazon" else getattr(app, "_collect_results", {}).get(platform, []))
    for record in records:
        record.setdefault("outcome", store.status(record, run["op"] if run else "1"))
    search = ft.TextField(label="搜索本次任务结果", hint_text="商品ID、状态或错误原因", width=360, color=ui.TEXT, bgcolor=ui.CARD_2)
    status = ft.Dropdown(label="结果状态", width=200, value="all", color=ui.TEXT, bgcolor=ui.CARD_2,
        options=[ft.dropdown.Option("all", "全部结果")] + [ft.dropdown.Option(key, value) for key, value in store.STATUS_LABELS.items()])
    box, page_text = ft.Column(spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), ft.Text("", color=ui.MUTED)
    position = [0]
    def filtered():
        keyword = str(search.value or "").casefold()
        return [row for row in records if (status.value == "all" or row["outcome"] == status.value)
                and keyword in (str(row.get("id")) + " " + str(row.get("error", "")) + " " + store.STATUS_LABELS.get(row["outcome"], "")).casefold()]
    def draw(reset=False):
        if reset:
            position[0] = 0
        rows = filtered()
        pages = max(1, (len(rows) + 19) // 20)
        position[0] = min(max(0, position[0]), pages - 1)
        box.controls = []
        for row in rows[position[0] * 20:(position[0] + 1) * 20]:
            controls = [ft.Text(str(row["id"]) + " · " + store.STATUS_LABELS[row["outcome"]], color=ui.TEXT, weight=ft.FontWeight.W_600, selectable=True),
                        ft.Text(str(row.get("error") or "已完成对应阶段"), color=ui.MUTED, selectable=True)]
            if row.get("initial_score") is not None:
                initial, final = row["initial_score"], row["score"]
                controls.append(ft.Text(f"刊登评分 {initial:.2f} → {final:.2f}/5", selectable=True,
                    color=ui.SUCCESS_C if row.get("score_passed") else ui.WARN_C))
                problems = row.get("initial_score_problems", [])
                remaining = row.get("score_problems", [])
                if problems or remaining:
                    details = [ui.muted("初评 · " + problem) for problem in problems]
                    if remaining != problems:
                        details.extend(ui.muted("复评 · " + problem) for problem in remaining)
                    controls.append(ft.ExpansionTile(title=ft.Text("查看扣分原因"), controls=details))
            if row.get("actions"):
                controls.append(ui.muted("修改：" + "；".join(row["actions"])))
            if row["outcome"] in ("needs_info", "uncertain"):
                controls.append(ft.TextButton("前往修正设置", on_click=lambda e, r=row: app._switch(repair_page(platform, r))))
            box.controls.append(ft.Container(ft.Column(controls, spacing=6, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=12, bgcolor=ui.CARD_2, border_radius=8))
        page_text.value = f"第{position[0] + 1}/{pages}页 · {len(rows)}条"
        prev.disabled, following.disabled = position[0] == 0, position[0] >= pages - 1
        app._safe_update()
    def move(delta):
        position[0] += delta
        draw()
    prev = ft.OutlinedButton("结果上一页", on_click=lambda e: move(-1))
    following = ft.OutlinedButton("结果下一页", on_click=lambda e: move(1))
    search.on_change = search.on_submit = lambda e: draw(True)
    status.on_change = lambda e: draw(True)
    def saved(e):
        if e.path:
            try:
                Path(e.path).write_text(getattr(app, "_collect_export_content", ""), encoding="utf-8")
                app.toast("任务结果已导出")
            except OSError as exc:
                app.toast("导出失败：" + str(exc), False)
    picker = getattr(app, "_collect_export_picker", None)
    if picker is None:
        picker = ft.FilePicker(on_result=saved)
        app._collect_export_picker = picker
        app.page.overlay.append(picker)
    else:
        picker.on_result = saved
    def export(e):
        app._collect_export_content = csv_text(filtered())
        picker.save_file(dialog_title="导出任务结果", file_name=platform + "-task-results.csv", allowed_extensions=["csv"])
    def query(e):
        if getattr(app, "_running", False) or getattr(app, "_collect_status_reading", False):
            app.toast("请等待当前任务结束后回读", False)
            return
        from ..config import AppConfig
        cfg_snapshot = AppConfig(deepcopy_config(app.cfg))
        app._collect_status_reading = True
        def work():
            try:
                from ..services.collect_automation import client_for
                from ..services.collect_runtime import ReadClient
                client = ReadClient(client_for(platform, cfg_snapshot), cfg_snapshot, platform)
                for row in records:
                    if row["outcome"] not in ("uncertain", "reviewing", "processing"):
                        continue
                    detail = client.product_detail(row["id"])
                    p = detail.get("product", detail)
                    state = str(p.get("dxmOfflineState") or p.get("dxmState") or "")
                    # A draft after an uncertain write doesn't prove its edits
                    # were accepted. Keep uncertain until explicitly resolved.
                    if state in ("online", "onSelling", "publishing", "waitPublish"):
                        row.update(ok=True, state=state, outcome="published" if state in ("online", "onSelling") else "processing", error="已回读最新平台状态，未重复提交")
                    elif p.get("errMsg"):
                        row["error"] = str(p["errMsg"])
                        from ..services.listing_failures import review_only
                        review = review_only(platform, row["error"])
                        row.update(outcome="reviewing" if review else "needs_info", ok=review)
                    if run:
                        store.item_update(run["id"], row["id"], "done", result=row)
                draw()
                app.toast("结果状态已回读，未重新提交商品")
            except Exception as exc:
                app.toast("状态回读失败：" + str(exc), False)
            finally:
                app._collect_status_reading = False
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as exc:
            app._collect_status_reading = False
            app.toast("状态回读未启动：" + str(exc), False)
    controls = [ui.h1("任务结果", ui.FS_MD), ui.muted("适用范围：" + label(app.cfg, platform))]
    if history:
        from datetime import datetime
        tasks = ft.Dropdown(label="历史处理任务", width=430, value=run["id"], color=ui.TEXT, bgcolor=ui.CARD_2,
            options=[ft.dropdown.Option(row["id"], datetime.fromtimestamp(row["started"]).strftime("%m-%d %H:%M:%S") + " · " +
                ("检查" if row["dry"] else "失败修复" if row["kind"] == "recovery" else "自动处理") + " · " +
                {"running": "进行中或待恢复", "complete": "已结束", "stopped": "已停止"}.get(row["status"], row["status"])) for row in history])
        def select(e):
            app._collect_view_run[platform] = e.control.value
            from .collect_workflow import RUN_PAGE
            app._switch(RUN_PAGE[platform])
        tasks.on_change = select
        controls.append(tasks)
    if run:
        cfg = run["config"]
        prefix = platform + "_collect_"
        money = "金额未估算" if run["calls"] and run["cost"] == 0 else f"预算金额{run['cost']:g}元（非账单）"
        controls.append(ui.muted(f"任务 {run['id'][:8]} · 已记录{len(run['items'])}件 · AI已预约{run['calls']}次 · {money}"))
        controls.append(ft.ExpansionTile(title=ft.Text("本次任务参数快照", color=ui.TEXT), controls=[
            ui.muted(f"请求{cfg.get(prefix + 'request_timeout', 30)}秒 · 重试{cfg.get(prefix + 'read_retries', 2)}次 · 间隔{cfg.get(prefix + 'interval', 1)}秒 · 去向{run['op']}")]))
        unfinished = sum(item["phase"] != "done" for item in run["items"])
        if unfinished:
            controls += [ui.muted(f"尚有{unfinished}件未完成。提交中断的商品先回读并跳过重发，其他商品按原参数继续。"),
                ft.ElevatedButton("继续未完成任务", on_click=lambda e: launch(app, platform, resume_id=run["id"]))]
    controls += [ft.Row([search, status], wrap=True), ft.Row([
        ft.OutlinedButton("导出筛选结果", icon=ft.Icons.DOWNLOAD, on_click=export),
        ft.OutlinedButton("回读平台状态", icon=ft.Icons.REFRESH, on_click=query)], wrap=True),
        box, ft.Row([prev, page_text, following], wrap=True), ui.muted("本地任务逐件保存；此处展示处理结果，不用于手动选择采集箱商品。")]
    draw()
    panel = ui.card(ft.Column(controls, spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
    panel.key = "collect-results"
    return panel


def deepcopy_config(cfg):
    from copy import deepcopy
    return deepcopy(cfg.as_dict())
