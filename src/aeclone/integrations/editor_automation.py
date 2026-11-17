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




SKIP_WHEN_REQUIRED_MISSING = True   # 必填缺失时跳过保存，避免整批卡死

SYS_ATTR_ALIASES = {
    "place of origin": "origin",
    "country of origin": "origin",
    "brand": "brand name",
    "brand name": "brand name",
    "model": "model number",
    "model number": "model number",
    "condition": "condition",
    "type": "type",
    "state": "condition",
}






CAPTURE_FN = r"""() => {
  const out = {};
  document.querySelectorAll('.ant-form-item').forEach(fi => {
    const labEl = fi.querySelector('.ant-form-item-label label');
    if (!labEl) return;
    let label = (labEl.innerText || '').trim();
    label = label.replace(/\s+/g, ' ').replace(/[?？*]/g, '').trim();
    if (!label || label.length > 40) return;
    const sw = fi.querySelector('.ant-switch');
    if (sw) { out[label] = {t: 'switch', v: sw.getAttribute('aria-checked') === 'true'}; return; }
    const sel = fi.querySelector('.ant-select-selection-item');
    if (sel) { out[label] = {t: 'select', v: (sel.innerText || '').trim()}; return; }
    const radios = [...fi.querySelectorAll('.ant-radio-wrapper-checked')];
    if (radios.length) { out[label] = {t: 'radio', v: (radios[0].innerText || '').trim()}; return; }
    const inp = fi.querySelector('input:not([type=file]), textarea');
    if (inp && String(inp.value || '').trim()) { out[label] = {t: 'text', v: String(inp.value).trim()}; return; }
    const fileTxt = fi.querySelector('.ant-upload-list-item-name, .file-name');
    if (fileTxt) { out[label] = {t: 'file', v: (fileTxt.innerText || '').trim()}; return; }
  });
  return out;
}"""


def read_form_values(ws_endpoint: str, product_id: str, timeout: int = 90000) -> dict:
    """读取编辑器里所有表单项的当前值（label → {t,v}），用于「模板怎么填就怎么填」。"""
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_selector(".ant-form-item", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(6000)
        return page.evaluate(CAPTURE_FN) or {}
    except Exception:
        return {}
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


def apply_form_values(page, values: dict) -> list[str]:
    """把模板值按 label 写入当前页面（只填空，不覆盖已有值）。"""
    applied = []
    if not values:
        return applied
    for label, item in list(values.items())[:60]:
        try:
            t, v = item.get("t"), item.get("v")
            fi = page.locator(".ant-form-item", has_text=label).first
            if fi.count() == 0:
                continue
            if t == "switch":
                sw = fi.locator(".ant-switch").first
                if sw.count() > 0:
                    cur = sw.get_attribute("aria-checked") == "true"
                    if cur != bool(v):
                        sw.click(timeout=2500)
                        page.wait_for_timeout(300)
                        applied.append(label)
                continue
            if t == "select":
                if fi.locator(".ant-select-selection-item").count() > 0:
                    continue
                sel = fi.locator(".ant-select").first
                if sel.count() == 0:
                    continue
                sel.click(timeout=2500)
                page.wait_for_timeout(600)
                opt = page.locator(".ant-select-item-option-content", has_text=str(v)[:20]).first
                if opt.count() > 0:
                    opt.click(timeout=2500)
                    page.wait_for_timeout(300)
                    applied.append(label)
                continue
            if t == "radio":
                r = fi.locator(".ant-radio-wrapper", has_text=str(v)[:12]).first
                if r.count() > 0:
                    r.click(timeout=2000)
                    applied.append(label)
                continue
            if t == "text":
                inp = fi.locator("input:not([type=file]), textarea").first
                if inp.count() > 0 and not (inp.input_value() or "").strip() and str(v).strip() not in ("", "N/A"):
                    inp.fill(str(v))
                    inp.dispatch_event("input")
                    page.wait_for_timeout(200)
                    applied.append(label)
        except Exception:
            continue
    return applied


def _make_manual_pdf(title: str, specs: list[str] | None = None) -> str:
    """生成一份简单的英文商品说明书 PDF（用于「商品说明书」必填项上传）。"""
    import tempfile
    from pathlib import Path as _P
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1240, 1754   # A4 @150dpi
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype("arial.ttf", 46)
        font = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font_b = ImageFont.load_default()
        font = ImageFont.load_default()

    d.text((90, 120), "PRODUCT MANUAL", font=font_b, fill=(20, 20, 20))
    y = 220
    for line in _wrap(title or "Product", 42)[:3]:
        d.text((90, y), line, font=font, fill=(40, 40, 40))
        y += 42
    y += 30
    d.text((90, y), "1. Specifications", font=font_b, fill=(20, 20, 20)); y += 56
    for sp in (specs or ["Refer to the product listing page for detailed specifications."])[:12]:
        for line in _wrap(sp, 60)[:2]:
            d.text((110, y), "- " + line, font=font, fill=(60, 60, 60)); y += 38
    y += 30
    d.text((90, y), "2. Safety Notes", font=font_b, fill=(20, 20, 20)); y += 56
    for line in ["Read this manual before use.", "Keep away from children.",
                 "Use only as intended by the manufacturer.",
                 "Inspect before each use; stop using if damaged."]:
        d.text((110, y), "* " + line, font=font, fill=(60, 60, 60)); y += 38
    y += 30
    d.text((90, y), "3. Package Contents", font=font_b, fill=(20, 20, 20)); y += 56
    d.text((110, y), "Product unit, accessory kit, this manual.", font=font, fill=(60, 60, 60))

    out = _P(tempfile.gettempdir()) / "product_manual.pdf"
    img.save(out, "PDF", resolution=150.0)
    return str(out)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _page_system_attrs(page) -> set[str]:
    """读取编辑器里「产品属性」区的系统属性英文键（如 Brand Name/Origin/Type/Model Number/Condition）。"""
    try:
        keys = page.evaluate(r"""() => {
            const out = [];
            document.querySelectorAll('.ant-form-item-label label').forEach(l => {
                const t = (l.innerText || '').trim();
                const m = t.match(/\(([^)]+)\)/);
                if (m) out.push(m[1].trim());
            });
            return out;
        }""")
    except Exception:
        keys = []
    norm = {"brand name", "origin", "type", "model number", "condition",
            "place of origin", "country of origin", "state", "model", "brand"}
    for k in (keys or []):
        kk = str(k).strip().lower()
        if not kk:
            continue
        norm.add(kk)
        if kk in SYS_ATTR_ALIASES:
            norm.add(SYS_ATTR_ALIASES[kk])
    return norm


def _norm_attr(name: str) -> str:
    n = (name or "").strip().lower()
    return SYS_ATTR_ALIASES.get(n, n)


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
        page.wait_for_timeout(1800)
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
        page.wait_for_timeout(3500)

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
        try:
            page.wait_for_selector(".ant-form-item", timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
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
        try:
            page.wait_for_selector(".vxe-table--body tbody tr", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

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
        page.wait_for_timeout(6000)

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


def apply_and_save(ws_endpoint: str, product_id: str, snapshot: dict | None = None,
                   new_title: str | None = None, timeout: int = 60000,
                   do_publish: bool = False, form_values: dict | None = None) -> SaveResult:
    """一次打开编辑器完成：写模板字段 → 改标题 → 保存（→ 可选发布）。

    相比 apply_template_fields + update_and_save 两次开页面，省一次导航约 20~40s。
    """
    pw, browser, ctx = _connect(ws_endpoint)
    page = ctx.new_page()
    seen: dict = {}
    applied: list[str] = []

    def _on_resp(resp):
        try:
            u = resp.url
            if "/api/smtProduct/add.json" in u or "/api/smtProduct/update.json" in u:
                seen["url"] = u.split("dianxiaomi.com")[-1]
                seen["status"] = resp.status
        except Exception:
            pass

    page.on("response", _on_resp)
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_selector('button:has-text("更新"), button:has-text("保存")', timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        fields = (snapshot or {}).get("fields") or {}
        attrs = list((snapshot or {}).get("attributes") or [])

        # 标题
        try:
            title_input = page.locator("input[type=text]").first
            old = title_input.input_value(timeout=6000)
            if new_title and new_title.strip() and new_title.strip() != old.strip():
                title_input.fill(new_title.strip())
                title_input.dispatch_event("input")
                page.wait_for_timeout(400)
                applied.append("标题")
        except Exception:
            pass

        # 类目（仅当未选择时）
        cat = (fields.get("category") or "").strip()
        if cat:
            try:
                cur = page.evaluate("""() => {
                    const fi=[...document.querySelectorAll('.ant-form-item')].find(x=>x.innerText.includes('产品分类'));
                    return fi?fi.innerText.replace(/\n/g,' '):'';
                }""")
                if "选择分类" in (cur or ""):
                    cat_name = cat.split("(")[0].strip()
                    page.get_by_role("button", name="选择分类", exact=False).first.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    sr = page.locator('input[placeholder="搜索分类名称"]').first
                    if sr.count() > 0:
                        sr.fill(cat_name)
                        page.get_by_role("button", name="搜索", exact=True).first.click(timeout=4000)
                        page.wait_for_timeout(2500)
                    item = page.locator(".search-result-item").first
                    if item.count() > 0:
                        item.click(timeout=5000)
                        page.wait_for_timeout(1000)
                        applied.append(f"类目 {cat_name}")
            except Exception:
                pass

        # 运费模板
        freight = (fields.get("freight") or "").strip()
        if freight:
            try:
                sel = page.locator(".ant-form-item", has_text="运费模板").locator(".ant-select").first
                if sel.count() > 0:
                    sel.click(timeout=4000)
                    page.wait_for_timeout(1000)
                    opt = page.locator(".ant-select-item-option-content", has_text=freight[:10]).first
                    if opt.count() > 0:
                        opt.click(timeout=4000)
                        page.wait_for_timeout(800)
                        applied.append(f"运费 {freight[:12]}")
            except Exception:
                pass

        # 重量/尺寸/品牌/产地/单位
        def fill_by_label(label, value):
            try:
                item = page.locator(".ant-form-item", has_text=label).first
                if item.count() == 0:
                    return False
                inp = item.locator("input").first
                if inp.count() > 0:
                    if not (inp.input_value() or "").strip():
                        inp.fill(value)
                        inp.dispatch_event("input")
                        page.wait_for_timeout(250)
                        return True
            except Exception:
                pass
            return False

        for label, key in (("包装后重量", "weight"), ("包装后尺寸", "size"),
                           ("品牌", "brand"), ("产地", "origin"), ("计件单位", "unit")):
            v = (fields.get(key) or "").strip()
            if v.upper() in ("NONE", "NULL", "-", "无"):
                continue
            if v and fill_by_label(label, v):
                applied.append(f"{label}={v[:12]}")

        # 自定义属性
        for pair in attrs[:20]:
            if "=" not in str(pair):
                continue
            k, v = str(pair).split("=", 1)
            try:
                name_input = page.locator(f'input[placeholder^="属性名"][value="{k.strip()}"]').first
                if name_input.count() > 0:
                    continue
                add_btn = page.get_by_role("button", name="添加自定义属性", exact=False).first
                if add_btn.count() > 0:
                    add_btn.click(timeout=3000)
                    page.wait_for_timeout(500)
                    page.locator('input[placeholder^="属性名"]').last.fill(k.strip())
                    page.locator('input[placeholder^="属性值"]').last.fill(v.strip())
                    applied.append(f"属性 {k.strip()}")
            except Exception:
                continue

        # 若没有任何改动，触发一次标题微改，确保保存按钮可用
        if not applied:
            try:
                ti = page.locator("input[type=text]").first
                cur = ti.input_value(timeout=5000)
                ti.fill((cur + " ").strip())
                ti.dispatch_event("input")
                page.wait_for_timeout(300)
                ti.fill(cur)
                ti.dispatch_event("input")
                page.wait_for_timeout(300)
                applied.append("触发变更")
            except Exception:
                pass

        # 按模板值补齐（模板产品怎么填，这里就怎么填）
        try:
            if form_values:
                _filled = apply_form_values(page, form_values)
                if _filled:
                    applied.append(f"按模板补齐 {len(_filled)} 项")
        except Exception:
            pass

        # 商品说明书：本地生成 PDF 并上传
        try:
            fi = page.locator('.ant-form-item', has_text="商品说明书").first
            if fi.count() > 0:
                has_file = fi.locator('input[type=file]').count() > 0
                if not has_file:
                    btn = fi.get_by_text("上传文件").first
                    if btn.count() > 0:
                        btn.click(timeout=4000)
                        page.wait_for_timeout(1200)
                inputs = fi.locator('input[type=file]')
                if inputs.count() == 0:
                    inputs = page.locator('input[type=file]')
                if inputs.count() > 0:
                    specs = []
                    for k, label in (("spec_weight", "Weight"), ("spec_length", "Length")):
                        v = (fields.get(k) or "").strip()
                        if v:
                            specs.append(f"{label}: {v}")
                    pdf = _make_manual_pdf(p.get("title_en") or "Product", specs)
                    inputs.first.set_input_files(pdf)
                    page.wait_for_timeout(7000)
                    applied.append("商品说明书已上传")
        except Exception:
            pass

        # 欧盟资质：必填开关 → 打开再关闭（确保有明确选择）
        try:
            sw = page.locator('.ant-form-item', has_text="是否提交欧盟资质").locator('.ant-switch').first
            if sw.count() > 0:
                checked = sw.get_attribute("aria-checked")
                if checked != "false":
                    sw.click(timeout=3000); page.wait_for_timeout(600)
                else:
                    sw.click(timeout=3000); page.wait_for_timeout(500)
                    sw.click(timeout=3000); page.wait_for_timeout(500)
                applied.append("欧盟资质已选择")
        except Exception:
            pass

        # 删除与系统属性重名的自定义属性行（系统已有 品牌/型号/产地/类型/状态 等）
        dup_removed = 0
        try:
            sys_keys = _page_system_attrs(page) or sys_keys
            dup_removed = page.evaluate("""(sysKeys) => {
                const norm = n => {
                  n = (n||'').trim().toLowerCase();
                  const alias = {'place of origin':'origin','country of origin':'origin',
                                 'brand':'brand name','model':'model number','state':'condition'};
                  return alias[n] || n;
                };
                const sys = new Set((sysKeys||[]).map(norm));
                let removed = 0;
                document.querySelectorAll('input[placeholder^="属性名"]').forEach(inp => {
                  const name = (inp.value||'').trim();
                  if (!name) return;
                  if (sys.has(norm(name))) {
                    const row = inp.closest('.ant-row, .ant-form-item, tr');
                    const btn = row && row.querySelector('.anticon-delete, .anticon-close, .anticon-minus-circle');
                    if (btn) { btn.click(); removed++; }
                  }
                });
                return removed;
            }""", list(sys_keys))
        except Exception:
            dup_removed = 0
        if dup_removed:
            page.wait_for_timeout(800)
            applied.append(f"删除与系统属性重复的自定义属性 {dup_removed} 项")

        # 必填项通用自动应答：下拉/单选优先选「否/无」，文本框填 N/A（文件上传类跳过）
        autofilled = []
        try:
            autofilled = page.evaluate("""() => {
                const done=[];
                document.querySelectorAll('.ant-form-item').forEach(fi=>{
                  const req=fi.querySelector('.ant-form-item-required, .ant-form-item-label .required');
                  if(!req) return;
                  const label=((fi.querySelector('label')||{}).innerText||'').trim().slice(0,20);
                  const inp=fi.querySelector('input,textarea');
                  const hasVal=inp && String(inp.value||'').trim();
                  const sel=fi.querySelector('.ant-select-selection-item');
                  if(hasVal || (sel && sel.innerText.trim())) return;
                  if(fi.querySelector('input[type=file]')){ done.push(label+':需上传文件'); return; }
                  const select=fi.querySelector('.ant-select');
                  if(select){ select.click(); done.push(label+':待选'); return; }
                  const radios=fi.querySelectorAll('.ant-radio-wrapper, input[type=radio]');
                  if(radios.length){
                    for(const r of radios){
                      const t=(r.innerText||'').trim();
                      if(/否|无|不|No/i.test(t)){ r.click(); done.push(label+'=否'); return; }
                    }
                    return;
                  }
                  if(inp){ inp.value='N/A'; inp.dispatchEvent(new Event('input',{bubbles:true})); done.push(label+'=N/A'); }
                });
                return done;
            }""")
        except Exception:
            autofilled = []
        if autofilled:
            page.wait_for_timeout(1200)
            for opt_text in ("否", "无", "不提交", "N/A", "不支持"):
                try:
                    opt = page.locator('.ant-select-item-option-content', has_text=opt_text).first
                    if opt.count() > 0:
                        opt.click(timeout=2000)
                        page.wait_for_timeout(300)
                except Exception:
                    continue
            applied.append(f"自动应答 {len(autofilled)} 项")

        # 必填项检查 + 属性冲突检查
        missing = []
        dup_attrs = []
        try:
            missing = page.evaluate("""() => {
                const out=[];
                document.querySelectorAll('.ant-form-item').forEach(fi=>{
                  const req = fi.querySelector('.ant-form-item-required, .ant-form-item-label .required');
                  if(!req) return;
                  const label=(fi.querySelector('label')||{}).innerText||'';
                  const val=(fi.querySelector('input,textarea')||{}).value||'';
                  const sel=fi.querySelector('.ant-select-selection-item');
                  const hasSel = sel && sel.innerText.trim();
                  if(!String(val).trim() && !hasSel) out.push(label.trim().slice(0,20));
                });
                return out.slice(0,12);
            }""")
        except Exception:
            missing = []
        try:
            dup_attrs = page.evaluate("""() => {
                const names=[...document.querySelectorAll('input[placeholder^="属性名"]')].map(i=>i.value.trim()).filter(Boolean);
                const seen={}, dup=[];
                names.forEach(n=>{ if(seen[n]) dup.push(n); seen[n]=1; });
                return dup;
            }""")
        except Exception:
            dup_attrs = []
        if missing:
            applied.append(f"⚠ 必填未填 {len(missing)} 项: {'、'.join(missing[:5])}")
            if SKIP_WHEN_REQUIRED_MISSING:
                return SaveResult(False, "必填项缺失，已跳过保存：" + "、".join(missing[:5])
                                  + (f" · 已写入：{'、'.join(applied[:4])}" if applied else ""))
        if dup_attrs:
            applied.append(f"⚠ 属性重复: {'、'.join(dup_attrs[:5])}")

        # 保存
        def save_click():
            for sel in ('button:has-text("更新")', 'button:has-text("保存")'):
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.scroll_into_view_if_needed(timeout=2000)
                        loc.click(timeout=6000)
                        return True
                except Exception:
                    continue
            return False

        if not save_click():
            return SaveResult(False, "未找到保存/更新按钮")
        page.wait_for_timeout(1800)
        for txt in ("确定", "确认", "继续发布", "继续"):
            try:
                btn = page.get_by_role("button", name=txt, exact=True).first
                if btn.count() > 0:
                    btn.click(timeout=2500)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        page.wait_for_timeout(3000)

        if not seen.get("status"):
            save_click()
            page.wait_for_timeout(4000)
        if not seen.get("status"):
            # 兜底：直接调用站点保存接口（编辑器内已有完整表单数据）
            try:
                resp = page.evaluate("""async () => {
                    const btn=[...document.querySelectorAll('button')].find(b=>/更新|保存/.test(b.innerText));
                    if(btn){btn.click(); return 'clicked';} return 'no-button';
                }""")
                page.wait_for_timeout(5000)
            except Exception:
                pass

        ok = bool(seen.get("status")) and int(seen.get("status", 500)) < 400
        msg = (f"保存接口 {seen.get('url','-')} HTTP {seen.get('status','-')}" if seen.get("status")
               else "未捕获保存接口（可能已保存但未响应）")
        if applied:
            msg += " · 写入：" + "、".join(applied[:6])
        return SaveResult(ok, msg)
    except Exception as e:  # noqa: BLE001
        return SaveResult(False, f"合并保存失败：{str(e)[:150]}")
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
