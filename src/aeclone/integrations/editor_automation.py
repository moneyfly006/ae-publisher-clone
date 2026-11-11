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


def update_and_save(ws_endpoint: str, product_id: str, new_title: str | None = None,
                    timeout: int = 60000) -> SaveResult:
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    seen = {"save": None}
    def _on_resp(resp):
        try:
            if "/api/smtProduct/add.json" in resp.url:
                seen["save"] = resp.status
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

        page.get_by_role("button", name="保存", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(6000)

        text = ""
        try:
            text = page.get_by_text("保存成功", exact=False).first.inner_text(timeout=3000)
        except Exception:
            pass
        if seen["save"]:
            return SaveResult(ok=(seen["save"] < 400),
                              message=f"保存接口 HTTP {seen['save']}", title=target)
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
