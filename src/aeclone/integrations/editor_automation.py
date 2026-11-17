"""店小秘编辑器自动化：通过页面操作完成保存（API 保存为 multipart，难以直接复放）。"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import sync_playwright

EDITOR_URL = "https://www.dianxiaomi.com/web/smt/edit?id={pid}"


@dataclass
class SaveResult:
    ok: bool
    message: str
    title: str = ""


def _connect(ws: str):
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(ws, timeout=30000)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return pw, browser, ctx


def read_title(ws_endpoint: str, product_id: str, timeout: int = 60000) -> str:
    """读取编辑器里的当前标题（用于保存后校验）。"""
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(9000)
        return page.locator("input[type=text]").first.input_value(timeout=8000)
    except Exception:
        return ""
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


def update_and_save(ws_endpoint: str, product_id: str, new_title: str | None = None,
                    timeout: int = 60000) -> SaveResult:
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    seen = {"save": None}
    def _on_resp(resp):
        try:
            u = resp.url
            if "/api/smtProduct/add.json" in u or "/api/smtProduct/update.json" in u:
                seen["save"] = resp.status
                seen["url"] = u.split("dianxiaomi.com")[-1]
        except Exception:
            pass
    page.on("response", _on_resp)
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        # 编辑器较重，等按钮出现再继续
        try:
            page.wait_for_selector('button:has-text("更新"), button:has-text("保存")', timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        title_input = page.locator("input[type=text]").first
        old = title_input.input_value(timeout=8000)
        target = new_title if new_title else old
        if new_title:
            title_input.fill(new_title)
            title_input.dispatch_event("input")
            page.wait_for_timeout(500)
        else:
            # 触发一次变更，让保存按钮可用
            title_input.fill(old + " ")
            title_input.dispatch_event("input")
            title_input.fill(old)
            title_input.dispatch_event("input")
        page.wait_for_timeout(800)

        def try_click() -> bool:
            selectors = [
                'button:has-text("更新")', 'button:has-text("保存")',
                'a.btn:has-text("更新")', 'a.btn:has-text("保存")',
                '.btn:has-text("更新")',
                'button:has-text("保存并发布")', 'button:has-text("提交")',
            ]
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        try:
                            loc.scroll_into_view_if_needed(timeout=3000)
                        except Exception:
                            pass
                        loc.click(timeout=8000)
                        return True
                except Exception:
                    continue
            return False

        if not try_click():
            raise RuntimeError("未找到保存/更新按钮")
        page.wait_for_timeout(4000)
        # 处理确认弹窗（欧盟合规 / 发布确认等）
        for txt in ("继续发布", "确定", "确认", "继续", "知道了"):
            try:
                btn = page.get_by_role("button", name=txt, exact=True).first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    break
            except Exception:
                continue
        page.wait_for_timeout(9000)

        if not seen.get("save"):
            if try_click():
                page.wait_for_timeout(3000)
                for txt in ("继续发布", "确定", "确认"):
                    try:
                        btn = page.get_by_role("button", name=txt, exact=True).first
                        if btn.count() > 0:
                            btn.click(timeout=3000); page.wait_for_timeout(6000); break
                    except Exception:
                        continue

        text = ""
        try:
            text = page.get_by_text("保存成功", exact=False).first.inner_text(timeout=3000)
        except Exception:
            pass
        if seen["save"]:
            # 回读校验
            verified = ""
            try:
                page.wait_for_timeout(1500)
                now = page.locator("input[type=text]").first.input_value(timeout=5000)
                verified = "，回读一致" if now.strip() == target.strip() else f"，回读不一致（{now[:20]}）"
            except Exception:
                verified = "，回读失败"
            return SaveResult(ok=(seen["save"] < 400),
                              message=f"保存接口 {seen.get('url','')} HTTP {seen['save']}{verified}", title=target)
        ok = "成功" in text or "保存" in text
        return SaveResult(ok=ok, message=text or "已点击保存（未捕获到保存接口）", title=target)
    except Exception as e:  # noqa: BLE001
        return SaveResult(ok=False, message=f"自动化失败：{e}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


def publish_product(ws_endpoint: str, product_id: str, confirm: bool = False,
                    dry_run: bool = True, timeout: int = 90000) -> SaveResult:
    """点击编辑器「发布」。confirm=False 时拒绝执行（防止误发）。"""
    if not confirm:
        return SaveResult(ok=False, message="未确认：发布操作被拒绝（需显式 confirm=True）")
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    captured = {}
    if dry_run:
        def _route(route):
            req = route.request
            if req.method in ("POST", "PUT") and "/api/" in req.url and \
               any(k in req.url.lower() for k in ("publish", "submit", "online", "add")):
                captured["url"] = req.url.split("dianxiaomi.com")[-1]
                captured["len"] = len(req.post_data or "")
                route.abort()
            else:
                route.continue_()
        page.route("**/api/**", _route)
    seen = {}
    def _on_resp(resp):
        try:
            u = resp.url
            if "/api/" in u and any(k in u.lower() for k in ("publish", "submit", "online")):
                seen["url"] = u.replace("https://www.dianxiaomi.com", "")
                seen["status"] = resp.status
        except Exception:
            pass
    page.on("response", _on_resp)
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(10000)
        page.get_by_role("button", name="发布", exact=True).first.click(timeout=12000)
        page.wait_for_timeout(6000)
        for txt in ("继续发布", "确定", "确认"):
            try:
                btn = page.get_by_role("button", name=txt, exact=True).first
                if btn.count() > 0:
                    btn.click(timeout=3000); page.wait_for_timeout(3000); break
            except Exception:
                continue
        page.wait_for_timeout(9000)
        if dry_run and captured.get("url"):
            return SaveResult(ok=True, message=f"演练：已捕获发布接口 {captured['url']}（{captured['len']} 字节，未提交）")
        if seen:
            return SaveResult(ok=seen.get("status", 500) < 400,
                              message=f"发布接口 {seen['url']} HTTP {seen.get('status')}")
        text = ""
        try:
            text = page.get_by_text("发布成功", exact=False).first.inner_text(timeout=3000)
        except Exception:
            pass
        return SaveResult(ok=bool(text), message=text or "已点击发布（未捕获接口）")
    except Exception as e:  # noqa: BLE001
        return SaveResult(ok=False, message=f"发布自动化失败：{e}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


def apply_template_fields(ws_endpoint: str, product_id: str, snapshot: dict,
                          timeout: int = 60000) -> SaveResult:
    """把模板字段按表单标签写入编辑器：类目/运费/重量/尺寸/品牌/产地/单位/属性。"""
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    applied = []
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(10000)
        fields = (snapshot or {}).get("fields") or {}
        attrs = (snapshot or {}).get("attributes") or []

        def fill_by_label(label, value):
            item = page.locator(".ant-form-item", has_text=label).first
            if item.count() == 0:
                return False
            inp = item.locator("input").first
            if inp.count() > 0:
                cur = inp.input_value() or ""
                if not cur.strip():
                    inp.fill(value); inp.dispatch_event("input")
                    page.wait_for_timeout(300)
                    return True
            return False

        # 类目：点选择分类 → 搜索分类名称 → 点搜索结果项
        cat = (fields.get("category") or "").strip()
        if cat:
            try:
                cur_cat = page.evaluate("""() => {
                    const fi = Array.from(document.querySelectorAll('.ant-form-item')).find(x=>x.innerText.includes('产品分类'));
                    return fi ? fi.innerText.replace(/\n/g,' ') : '';
                }""")
                if "选择分类" in (cur_cat or "") and cur_cat.replace("产品分类", "").replace("选择分类", "").replace("自动识别分类", "").strip():
                    applied.append("类目已存在(跳过)")
                    cat = ""
            except Exception:
                pass
        if cat:
            try:
                cat_name = cat.split("(")[0].strip()
                page.get_by_role("button", name="选择分类", exact=False).first.click(timeout=5000)
                page.wait_for_timeout(2000)
                search = page.locator('input[placeholder="搜索分类名称"]').first
                if search.count() > 0:
                    search.fill(cat_name)
                    page.get_by_role("button", name="搜索", exact=True).first.click(timeout=4000)
                    page.wait_for_timeout(3500)
                item = page.locator(".search-result-item").first
                if item.count() > 0:
                    item.click(timeout=5000); page.wait_for_timeout(1500)
                    applied.append(f"类目 {cat_name}")
                # 关闭弹层
                for lbl in ("选择", "确定", "关闭"):
                    try:
                        b2 = page.get_by_role("button", name=lbl, exact=True).first
                        if b2.count() > 0:
                            b2.click(timeout=2500); page.wait_for_timeout(800); break
                    except Exception:
                        continue
            except Exception:
                pass

        # 运费模板：点击运费下拉选择
        freight = (fields.get("freight") or "").strip()
        if freight:
            try:
                sel = page.locator(".ant-form-item", has_text="运费模板").locator(".ant-select").first
                if sel.count() > 0:
                    sel.click(timeout=4000); page.wait_for_timeout(1500)
                    opt = page.locator(".ant-select-item-option-content", has_text=freight[:10]).first
                    if opt.count() > 0:
                        opt.click(timeout=4000); page.wait_for_timeout(1200)
                        applied.append(f"运费 {freight[:16]}")
            except Exception:
                pass

        label_map = [("包装后重量", "weight"),
                     ("包装后尺寸", "size"), ("品牌", "brand"), ("产地", "origin"), ("计件单位", "unit")]
        for label, key in label_map:
            v = (fields.get(key) or "").strip()
            if v.upper() in ("NONE", "NULL", "-", "无"):
                continue
            if v and fill_by_label(label, v):
                applied.append(f"{label}={v[:16]}")

        # 自定义属性：按「属性名=值」匹配
        for pair in attrs[:24]:
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            try:
                name_input = page.locator(f'input[placeholder^="属性名"][value="{k.strip()}"]').first
                if name_input.count() == 0:
                    continue
                row = name_input.locator("xpath=../..")
                val_input = row.locator('input[placeholder^="属性值"]').first
                if val_input.count() > 0 and not (val_input.input_value() or "").strip():
                    val_input.fill(v.strip()); val_input.dispatch_event("input")
                    applied.append(f"属性 {k.strip()}")
            except Exception:
                continue

        # 保存
        if applied:
            for label in ("保存", "更新"):
                try:
                    btn = page.get_by_role("button", name=label, exact=True).first
                    if btn.count() > 0:
                        btn.click(timeout=8000); page.wait_for_timeout(4000); break
                except Exception:
                    continue
        if applied:
            return SaveResult(ok=True, message="已写入：" + ", ".join(applied[:8]))
        return SaveResult(ok=True, message="模板字段已存在，无需修改（已跳过）")
    except Exception as e:  # noqa: BLE001
        return SaveResult(ok=False, message=f"模板写回失败：{e}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


def attach_video(ws_endpoint: str, product_id: str, video_path: str, timeout: int = 180000) -> SaveResult:
    """在编辑器里上传主图视频：点「添加视频」→ 选择本地文件 → 等待上传完成 → 保存。"""
    from pathlib import Path as _P
    vp = _P(video_path)
    if not vp.exists():
        return SaveResult(False, f"视频不存在：{video_path}")
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    saved = {"ok": False, "msg": ""}
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_selector('button:has-text("添加视频")', timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # 点开视频上传区
        clicked = False
        for sel in ('button:has-text("添加视频")', '.btn:has-text("添加视频")', 'text=添加视频'):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3000)
                    loc.click(timeout=6000)
                    clicked = True
                    break
            except Exception:
                continue
        page.wait_for_timeout(2000)

        # 找到文件输入并上传
        inputs = page.locator('input[type="file"]')
        n = inputs.count()
        if n == 0:
            return SaveResult(False, "未找到视频文件输入框（可能改版）")
        target = None
        for i in range(n):
            try:
                acc = (inputs.nth(i).get_attribute("accept") or "").lower()
                if "video" in acc or "mp4" in acc:
                    target = inputs.nth(i)
                    break
            except Exception:
                continue
        target = target or inputs.nth(0)
        target.set_input_files(str(vp), timeout=30000)

        # 等上传完成（出现进度/成功提示，或按钮变为可保存）
        deadline = __import__("time").time() + 120
        while __import__("time").time() < deadline:
            page.wait_for_timeout(3000)
            body = page.inner_text("body")
            if any(k in body for k in ("上传成功", "视频已添加", "100%", "处理完成")):
                break
        page.wait_for_timeout(3000)

        # 保存
        for sel in ('button:has-text("更新")', 'button:has-text("保存")'):
            try:
                b = page.locator(sel).first
                if b.count() > 0:
                    b.click(timeout=8000)
                    saved["ok"] = True
                    saved["msg"] = f"视频已上传并保存（{vp.name}）"
                    break
            except Exception:
                continue
        if not saved["ok"]:
            saved["msg"] = "视频已选择，但未找到保存按钮"
        page.wait_for_timeout(4000)
        return SaveResult(saved["ok"], saved["msg"])
    except Exception as e:  # noqa: BLE001
        return SaveResult(False, f"视频上传失败：{str(e)[:160]}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()


LIST_URL = "https://www.dianxiaomi.com/web/smt/smtProductList/offline"


def publish_from_list(ws_endpoint: str, product_id: str, confirm: bool = False,
                      dry_run: bool = True, timeout: int = 150000,
                      title_hint: str = "") -> SaveResult:
    """在「待发布」列表里勾选产品 → 批量操作 → 批量发布（真实提交到速卖通）。"""
    if not confirm:
        return SaveResult(False, "未确认：发布操作被拒绝（需显式 confirm=True）")
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    seen: dict = {}
    captured: dict = {}

    def _on_req(req):
        try:
            u = req.url
            if "/api/" in u and any(k in u.lower() for k in ("publish", "submit", "online")):
                captured["url"] = u.split("dianxiaomi.com")[-1]
                captured["len"] = len(req.post_data or "")
                if dry_run:
                    req.abort()
        except Exception:
            pass

    def _on_resp(resp):
        try:
            u = resp.url
            if "/api/" in u and any(k in u.lower() for k in ("publish", "submit", "online")):
                seen["url"] = u.split("dianxiaomi.com")[-1]
                seen["status"] = resp.status
        except Exception:
            pass

    page.on("request", _on_req)
    page.on("response", _on_resp)
    try:
        page.goto(LIST_URL, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(12000)

        # 在列表里按标题找到目标行（列表不显示产品 ID，只能按标题匹配）
        rows = page.locator(".vxe-table--body tbody tr")
        if rows.count() == 0:
            return SaveResult(False, "待发布列表为空")
        target = None
        hint = (title_hint or "").strip()
        if hint:
            try:
                for i in range(rows.count()):
                    try:
                        txt = rows.nth(i).inner_text()
                    except Exception:
                        continue
                    if hint[:18].lower() in (txt or "").lower():
                        target = rows.nth(i)
                        break
            except Exception:
                target = None
        if target is None:
            target = rows.first
        first = target
        box = first.locator('input[type="checkbox"]').first
        if box.count() == 0:
            try:
                first.locator(".vxe-checkbox--icon").first.click(timeout=4000)
            except Exception:
                return SaveResult(False, "找不到勾选框")
        else:
            try:
                box.check(timeout=4000)
            except Exception:
                try:
                    box.click(timeout=4000)
                except Exception:
                    return SaveResult(False, "勾选失败")
        page.wait_for_timeout(1500)

        # 批量操作 → 批量发布
        try:
            page.get_by_role("button", name="批量操作").first.click(timeout=8000)
        except Exception:
            return SaveResult(False, "找不到「批量操作」")
        page.wait_for_timeout(1500)
        clicked = False
        for sel in ('text=批量发布', '.vxe-dropdown--panel >> text=批量发布'):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=6000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return SaveResult(False, "找不到「批量发布」")
        page.wait_for_timeout(4000)

        # 确认弹窗
        for txt in ("确定", "确认", "立即发布", "发布", "继续"):
            try:
                btn = page.get_by_role("button", name=txt, exact=True).first
                if btn.count() > 0:
                    btn.click(timeout=4000)
                    page.wait_for_timeout(3000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(12000)

        if dry_run:
            if captured.get("url"):
                return SaveResult(True, f"演练：捕获发布接口 {captured['url']}（{captured['len']} 字节，未提交）")
            return SaveResult(False, "演练未捕获到发布接口（可能弹窗未确认）")
        if seen:
            return SaveResult(seen.get("status", 500) < 400,
                              f"发布接口 {seen['url']} HTTP {seen.get('status')}")
        body = ""
        try:
            body = page.inner_text("body")[-400:]
        except Exception:
            pass
        ok = ("发布成功" in body) or ("已提交" in body)
        return SaveResult(ok, "发布已提交（未捕获接口）" if ok else "已点击发布但未确认结果")
    except Exception as e:  # noqa: BLE001
        return SaveResult(False, f"列表发布失败：{str(e)[:160]}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()
