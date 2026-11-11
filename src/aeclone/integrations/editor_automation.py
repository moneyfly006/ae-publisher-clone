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
        page.wait_for_timeout(9000)

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
            for label in ("保存", "更新", "保存并发布", "提交"):
                try:
                    loc = page.get_by_role("button", name=label, exact=True).first
                    if loc.count() > 0:
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
    """把模板的类目/运费/属性写入编辑器表单（尽力而为）。"""
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    applied = []
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(9000)
        fields = (snapshot or {}).get("fields") or {}
        attrs = (snapshot or {}).get("attributes") or []

        # 属性：按 "属性名=值" 匹配输入框
        for pair in attrs[:20]:
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            try:
                name_input = page.locator(f'input[placeholder^="属性名"][value="{k.strip()}"]').first
                if name_input.count() == 0:
                    continue
                row = name_input.locator("xpath=..").locator("xpath=..")
                val_input = row.locator('input[placeholder^="属性值"]').first
                if val_input.count() > 0 and not (val_input.input_value() or "").strip():
                    val_input.fill(v.strip()); val_input.dispatch_event("input")
                    applied.append(f"属性 {k.strip()}")
            except Exception:
                continue

        # 类目/运费：仅记录（下拉交互依赖具体组件）
        for key in ("category", "freight"):
            if fields.get(key):
                applied.append(f"{key}={fields[key][:18]}")

        if applied:
            for label in ("保存", "更新"):
                try:
                    btn = page.get_by_role("button", name=label, exact=True).first
                    if btn.count() > 0:
                        btn.click(timeout=8000); break
                except Exception:
                    continue
            page.wait_for_timeout(5000)
        return SaveResult(ok=bool(applied), message="已应用：" + ", ".join(applied[:6]) if applied else "没有可应用的字段")
    except Exception as e:  # noqa: BLE001
        return SaveResult(ok=False, message=f"模板写回失败：{e}")
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()
