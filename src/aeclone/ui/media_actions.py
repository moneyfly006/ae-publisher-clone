"""Image selection, per-image jobs and draft updates for platform editors."""
from copy import deepcopy
import threading

import flet as ft

from ..services.platform_media import GROUPS, is_image_url
from ..services.platform_presets import IMAGE_STYLES

PROMPTS = {key: prompt for key, (_, prompt) in IMAGE_STYLES.items()}
PROMPTS["custom"] = "保持商品本体的形状、结构、颜色、材质与比例，只按用户描述调整背景和展示效果。"
OPERATIONS = {key: label for key, (label, _) in IMAGE_STYLES.items()} | {"custom": "自定义编辑"}


def controls(app, get_urls, apply_urls, *, platform, language="英语", groups=None, get_groups=None, apply_groups=None):
    from . import app as ui
    from ..services.collect_store import scope
    from ..services.progress import emit
    from .structured_fields import text_field

    # The legacy Python entry point still accepts one list of product images.
    rich = get_groups is not None
    use_shared_defaults = bool(getattr(app, "_collect_editor_only", False))
    groups = groups or {"main": GROUPS[platform]["main"]}
    source_groups = get_groups or (lambda: {"main": list(get_urls())})
    save_groups = apply_groups or (lambda values: apply_urls(values["main"]))
    group_picker = ft.Dropdown(label="图片分组", width=230, value=next(iter(groups)),
        options=[ft.dropdown.Option(k, group.label) for k, group in groups.items()],
        color=ui.TEXT, bgcolor=ui.CARD_2)
    image_grid = ft.Row(wrap=True, spacing=10, run_spacing=10)
    task_box = ft.Column(spacing=8)
    new_url = text_field("新增图片 URL", width=335)
    status = ft.Text("", color=ui.MUTED, size=12, selectable=True)
    busy = ft.ProgressBar(visible=False, color=ui.ACCENT)
    selected = set()
    tasks = []
    editor_key = None
    known = {}
    if not hasattr(app, "_media_jobs"):
        app._media_jobs = set()
    if not hasattr(app, "_media_jobs_lock"):
        app._media_jobs_lock = threading.Lock()
    if not hasattr(app, "_media_edit_plans"):
        app._media_edit_plans = {}

    def context():
        return (scope(app.cfg, platform), str(getattr(app.cfg, platform + "_test_product_id", "") or ""),
                getattr(getattr(app, "active_platform", None), "id", ""), getattr(app, "_page_idx", None))

    def notify(text, color=None):
        status.value, status.color = text, color or ui.MUTED
        app._safe_update()

    def change_urls(key, urls, *, clear_tasks=False):
        save_groups({key: urls})
        if clear_tasks:
            selected.difference_update(pair for pair in list(selected) if pair[0] == key)
            for task in list(tasks):
                if task["group"] != key:
                    continue
                if task["source"] not in urls:
                    tasks.remove(task)
                    continue
                task["index"] = urls.index(task["source"])
                task["references"] = [url for url in task["references"] if url in urls]
            known[key] = list(urls)
            render_tasks()
        render()

    def add_url(e):
        key = group_picker.value
        group = groups[key]
        if group.fixed_slots:
            return
        url = str(new_url.value or "").strip()
        if not is_image_url(url):
            notify("请填写有效的 http 或 https 图片地址", ui.DANGER_C)
            return
        urls = list(source_groups().get(key, ()))
        if url in urls or len(urls) >= group.limit:
            notify(f"{group.label}最多 {group.limit} 张，每个地址只添加一次。", ui.WARN_C)
            return
        change_urls(key, urls + [url])
        new_url.value = ""
        notify("图片已加入当前商品")

    def local_upload(e):
        key = group_picker.value
        urls = list(source_groups().get(key, ()))
        ctx = context()
        if groups[key].fixed_slots or len(urls) >= groups[key].limit:
            notify("当前分组没有可新增的位置", ui.WARN_C)
            return
        if not getattr(app, "_media_picker", None):
            app._media_picker = ft.FilePicker()
            app.page.overlay.append(app._media_picker)
            app._safe_update()

        def picked(event):
            from pathlib import Path
            from ..services.hosting import upload
            paths = [Path(f.path) for f in (event.files or []) if f.path]
            if not paths:
                return
            if len(paths) + len(urls) > groups[key].limit:
                notify("所选文件超出当前分组的图片上限", ui.WARN_C)
                return
            host, token = app.cfg.image_host_url, app.cfg.image_host_key

            def work():
                try:
                    if context() != ctx or list(source_groups().get(key, ())) != urls:
                        raise ValueError("商品或图片已切换")
                    hosted = [upload(path, base=host, key=token) for path in paths]
                    if not all(is_image_url(url) for url in hosted):
                        raise ValueError("图床未返回有效地址")
                    if context() != ctx or list(source_groups().get(key, ())) != urls:
                        raise ValueError("商品或图片已切换，上传结果未回填")
                    change_urls(key, list(dict.fromkeys(urls + hosted)))
                    notify(f"已上传 {len(hosted)} 张图片", ui.SUCCESS_C)
                except Exception as exc:
                    notify(f"上传图片失败：{exc}", ui.DANGER_C)

            threading.Thread(target=work, daemon=True).start()

        app._media_picker.on_result = picked
        try:
            app._media_picker.pick_files(allow_multiple=True, allowed_extensions=["png", "jpg", "jpeg", "webp"])
        except Exception as exc:
            notify(f"无法打开文件选择窗口：{exc}", ui.DANGER_C)

    def modify_url(index, action):
        key = group_picker.value
        if groups[key].fixed_slots:
            return
        urls = list(source_groups().get(key, ()))
        if index >= len(urls):
            render()
            return
        if action == "remove":
            urls.pop(index)
        elif index:
            urls.insert(0, urls.pop(index))
        else:
            return
        change_urls(key, urls, clear_tasks=True)
        notify("图片已移除" if action == "remove" else "已调整为首图")

    def render(e=None):
        nonlocal tasks, editor_key
        if rich:
            scope_key, pid, _, _ = context()
            next_key = (platform, scope_key, pid)
            if editor_key != next_key:
                tasks = app._media_edit_plans.setdefault(next_key, []) if pid else []
                selected.clear()
                editor_key = next_key
        values = source_groups()
        for key in groups:
            urls = list(values.get(key, ()))
            before = known.get(key, [])
            changed = [pair for pair in selected if pair[0] == key and
                       (pair[1] >= len(urls) or pair[1] >= len(before) or before[pair[1]] != urls[pair[1]])]
            selected.difference_update(changed)
            known[key] = urls
        key = group_picker.value
        urls = known[key]
        add_pictures.visible = not groups[key].fixed_slots
        image_grid.controls = []
        for index, url in enumerate(urls):
            pair = (key, index)

            def check(ev, target=pair):
                if ev.control.value:
                    selected.add(target)
                else:
                    selected.discard(target)

            checkbox = ft.Checkbox(label="主图" if key == "main" and index == 0 else f"第 {index + 1} 张",
                                   value=pair in selected, on_change=check)
            image_grid.controls.append(ft.Container(width=129, padding=6, border_radius=8, bgcolor=ui.CARD_2,
                content=ft.Column([
                    ft.Image(src=url, width=117, height=90, fit=ft.ImageFit.CONTAIN,
                             error_content=ft.Text("图片加载失败", size=11)),
                    checkbox,
                    ft.Row([ft.IconButton(ft.Icons.VERTICAL_ALIGN_TOP, tooltip="设为首图",
                              visible=groups[key].listed and index > 0, icon_size=16,
                              on_click=lambda ev, n=index: modify_url(n, "first")),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="移除商品图片",
                              icon_size=16, visible=not groups[key].fixed_slots,
                              on_click=lambda ev, n=index: modify_url(n, "remove"))], spacing=0),
                ], spacing=0)))
        if not urls:
            image_grid.controls = [ui.muted("商品没有可替换的规格图。" if groups[key].fixed_slots else "此分组暂无图片。")]
        app._safe_update()

    def render_tasks():
        task_box.controls = []
        for task in tasks:
            key = task["group"]
            preset = ft.Dropdown(label="处理方式", width=190, value=task["operation"],
                options=[ft.dropdown.Option(k, label) for k, label in OPERATIONS.items()], color=ui.TEXT, bgcolor=ui.CARD_2)
            prompt = text_field("本任务提示词", task["prompt"], width=420)
            prompt.multiline = True
            prompt.min_lines, prompt.max_lines = 2, 5
            prompt_preset = ft.Dropdown(label="提示词预设", width=220, bgcolor=ui.CARD_2,
                border_color=ui.CARD_2, color=ui.TEXT, options=[])
            try:
                from .prompt_presets import attach_dropdown
                from . import app as _ui
                attach_dropdown(app, prompt_preset,
                                key="image_task:" + platform,
                                field=prompt,
                                builtins=(getattr(_ui, "IMAGE_PROMPT_PRESETS", {}) or {}).get("main", {}),
                                labels={k: k for k in (getattr(_ui, "IMAGE_PROMPT_PRESETS", {}) or {}).get("main", {})},
                                apply_prompt=lambda text, row=task: row.update(prompt=text))
            except Exception:
                pass
            target = ft.Dropdown(label="指定商品图片", width=190, value=str(task["index"]),
                options=[ft.dropdown.Option(str(i), f"第 {i + 1} 张") for i, _ in enumerate(known[key])],
                color=ui.TEXT, bgcolor=ui.CARD_2)
            output = ft.Dropdown(label="回填方式", width=155, value=task["output"],
                options=[ft.dropdown.Option("replace", "替换原图")] + (
                    [ft.dropdown.Option("first", "新增到首位"), ft.dropdown.Option("last", "新增到末尾")]
                    if groups[key].limit > 1 and not groups[key].fixed_slots else []), color=ui.TEXT, bgcolor=ui.CARD_2)

            def set_preset(e, row=task, field=prompt):
                row["operation"] = e.control.value
                row["prompt"] = field.value = PROMPTS[e.control.value]
                app._safe_update()

            def set_target(e, row=task):
                row["index"] = int(e.control.value)
                row["source"] = known[row["group"]][row["index"]]
                row["references"] = []
                render_tasks()

            def remove(e, row=task):
                tasks.remove(row)
                render_tasks()

            preset.on_change = set_preset
            target.on_change = set_target
            output.on_change = lambda e, row=task: row.update(output=e.control.value)
            prompt.on_change = lambda e, row=task: row.update(prompt=e.control.value)
            reference_controls = []
            for i, url in enumerate(known[key]):
                if i == task["index"] or url == task["source"]:
                    continue

                def reference(e, row=task, url=url):
                    values = row["references"]
                    if e.control.value and url not in values:
                        if len(values) == 2:
                            e.control.value = False
                            notify("每条任务最多选择两张参考图", ui.WARN_C)
                            return
                        values.append(url)
                    elif not e.control.value and url in values:
                        values.remove(url)

                reference_controls.append(ft.Checkbox(label=f"第 {i + 1} 张", value=url in task["references"],
                                                     on_change=reference))
            task_box.controls.append(ft.Container(padding=10, border_radius=8, bgcolor=ui.CARD_2,
                content=ft.Column([
                    ft.Row([ui.h1(groups[key].label, 13), target, preset, prompt_preset, output,
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除图片任务", on_click=remove)], wrap=True),
                    prompt,
                    *([ft.ExpansionTile(title=ft.Text("选用同组图片作为参考"), controls=[
                        ft.Row(reference_controls, wrap=True)])] if reference_controls else []),
                ], spacing=4)))
        app._safe_update()

    def add_tasks(e):
        render()
        if not selected:
            notify("先勾选要处理的商品图片", ui.WARN_C)
            return
        for key, index in sorted(selected):
            preset = getattr(app.cfg, platform + "_collect_image_style", "white")
            preset = preset if preset in OPERATIONS else "white"
            prompt = str(getattr(app.cfg, platform + "_collect_image_prompt", "") or PROMPTS[preset])
            tasks.append(dict(group=key, index=index, source=known[key][index], operation=preset,
                              prompt=prompt, output="replace", references=[]))
        selected.clear()
        render()
        render_tasks()

    def start(e):
        from ..services.image_index import parse_indices

        render()
        current = {k: list(v) for k, v in source_groups().items() if k in groups}
        plan = deepcopy(tasks)
        if not rich and not plan and current.get("main"):
            shared = use_shared_defaults
            indices = parse_indices(getattr(app.cfg, platform + "_collect_image_indices", "1") if shared else "1",
                                    len(current["main"]))
            text = getattr(app.cfg, platform + "_collect_image_prompt", "") if shared else ""
            plan = [dict(group="main", index=i - 1, source=current["main"][i - 1],
                         prompt=text or PROMPTS["white"], output="replace", references=[]) for i in indices]
        if not plan:
            notify("请先添加图片处理任务", ui.WARN_C)
            return
        affected = {task["group"] for task in plan}
        replaced = set()
        for task in plan:
            key, index = task["group"], task["index"]
            if (index >= len(current.get(key, [])) or current[key][index] != task["source"]
                    or any(url not in current[key] for url in task["references"])):
                notify("商品图片已变化，请删除旧任务后重新选图", ui.WARN_C)
                return
            if not is_image_url(task["source"]) or not str(task["prompt"] or "").strip():
                notify("请为每条任务选择有效图片并填写提示词", ui.WARN_C)
                return
            if task["output"] == "replace" and (key, index) in replaced:
                notify("同一张原图只能有一条替换任务", ui.WARN_C)
                return
            if task["output"] == "replace":
                replaced.add((key, index))
        for key in affected:
            count = len(current[key]) + sum(task["group"] == key and task["output"] != "replace" for task in plan)
            if count > groups[key].limit:
                notify(f"{groups[key].label}最多 {groups[key].limit} 张，请减少新增任务", ui.WARN_C)
                return

        original_context = context()
        job_key = (platform, original_context[1] or tuple(tuple(urls) for urls in current.values()))

        def is_current():
            return context() == original_context and all(list(source_groups().get(k, ())) == current[k] for k in affected)

        def confirmed():
            from ..config import AppConfig, config_dir
            from ..services.image_pipeline import ImagePipeline
            from ..services.platform_media import edit_and_host

            if not is_current():
                notify("商品、页面或图片已切换，请重新确认任务", ui.WARN_C)
                return
            with app._media_jobs_lock:
                if getattr(app, "_running", False) or (platform == "amazon" and getattr(app, "_amz_saving", False)) or job_key in app._media_jobs:
                    app.toast("当前已有任务运行，请等待任务结束", False)
                    return
                app._media_jobs.add(job_key)
            cfg = AppConfig(deepcopy(app.cfg.as_dict()))
            lang = language() if callable(language) else language
            busy.visible = True
            notify("正在处理已选图片…")

            def work():
                output = {k: list(current[k]) for k in affected}
                front, back = {k: [] for k in affected}, {k: [] for k in affected}
                errors = []
                try:
                    pipeline = ImagePipeline(cfg, workdir=config_dir() / "cache" / "platform-media")
                    for task in plan:
                        if not is_current():
                            notify("商品或图片已切换，结果未回填", ui.WARN_C)
                            return
                        key, index = task["group"], task["index"]
                        label = f"{groups[key].label} · 第 {index + 1} 张"
                        emit(platform + " · 图片处理", "开始", label, product=original_context[1])
                        try:
                            url = edit_and_host(pipeline, task["source"], task["prompt"].strip() +
                                                "\n图片文字目标语言：" + str(lang), task["references"])
                        except Exception as error:
                            errors.append(f"{label}：{error}")
                            emit(platform + " · 图片处理", "失败", errors[-1], level="ERROR")
                            continue
                        if task["output"] == "replace":
                            output[key][index] = url
                        else:
                            (front if task["output"] == "first" else back)[key].append(url)
                    if not is_current():
                        notify("商品或图片已切换，结果未回填", ui.WARN_C)
                        return
                    success = len(plan) - len(errors)
                    if success:
                        save_groups({k: front[k] + output[k] + back[k] for k in affected if
                                     front[k] or back[k] or output[k] != current[k]})
                        if rich:
                            tasks.clear()
                            render()
                            render_tasks()
                    if errors:
                        notify(f"成功 {success}/{len(plan)} 张，失败项的原商品图片保留。{errors[0]}", ui.WARN_C)
                    else:
                        notify(f"已回填 {success} 张图片，请复核并保存商品。", ui.SUCCESS_C)
                except Exception as error:
                    notify(f"图片处理失败：{error}，请检查当前商品图片。", ui.DANGER_C)
                finally:
                    busy.visible = False
                    with app._media_jobs_lock:
                        app._media_jobs.discard(job_key)
                    app._safe_update()

            try:
                threading.Thread(target=work, daemon=True).start()
            except Exception as error:
                busy.visible = False
                with app._media_jobs_lock:
                    app._media_jobs.discard(job_key)
                notify(f"图片任务启动失败：{error}", ui.DANGER_C)

        app._confirm("确认图片处理（付费）",
                     f"将执行 {len(plan)} 条图片任务。成功结果会回填当前商品编辑区。", confirmed)

    add_pictures = ft.Row([new_url, ft.OutlinedButton("添加图片", icon=ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED,
                          on_click=add_url), ft.TextButton("上传本地图", icon=ft.Icons.UPLOAD_FILE, on_click=local_upload)], wrap=True)

    def refresh(e):
        render()
        render_tasks()

    group_picker.on_change = refresh
    render()
    render_tasks()
    return ft.Column([
        ft.Row([group_picker, ft.TextButton("刷新图片列表", icon=ft.Icons.REFRESH, on_click=refresh)], wrap=True),
        image_grid, add_pictures,
        ft.TextButton("为勾选图片添加任务", icon=ft.Icons.PLAYLIST_ADD, on_click=add_tasks),
        task_box,
        ft.ElevatedButton("处理并回填图片", icon=ft.Icons.AUTO_AWESOME, on_click=start), busy, status,
    ], spacing=10, data=refresh)
