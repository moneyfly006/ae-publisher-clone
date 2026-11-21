import asyncio, json, base64
from playwright.async_api import async_playwright
COOKIE = open('.tmp-probe/cookies.txt').read().strip()
def parse(raw):
    return [{"name":k.strip(),"value":v.strip(),"domain":".dianxiaomi.com","path":"/"}
            for k,v in (p.split('=',1) for p in raw.split(';') if '=' in p)]
PID="220114595363182310"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await b.new_context(viewport={"width":1680,"height":1050})
        await ctx.add_cookies(parse(COOKIE))
        page = await ctx.new_page()
        captured=[]
        async def handler(route, request):
            buf = request.post_data_buffer
            captured.append({"url": request.url,
                             "headers": dict(request.headers),
                             "b64": base64.b64encode(buf or b"").decode(),
                             "post": request.post_data or ""})
            await route.abort()
        await page.route("**/api/amazonProductSp/add.json", handler)
        await page.goto(f"https://www.dianxiaomi.com/web/amazon/edit?id={PID}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(9000)
        await page.evaluate("""() => { document.querySelectorAll('.ant-modal-root,.ant-modal-wrap,.ant-modal-mask').forEach(e=>e.remove()); }""")
        await page.wait_for_timeout(400)
        btns = await page.evaluate("""() => Array.from(document.querySelectorAll('button')).map(b=>(b.innerText||'').trim()).filter(Boolean)""")
        print("BUTTONS:", json.dumps(btns, ensure_ascii=False)[:1200])
        save = page.locator("button:has-text('保存')").first
        n = await save.count()
        print("save count:", n)
        if n:
            await save.scroll_into_view_if_needed()
            await save.click()
            await page.wait_for_timeout(6000)
        open('.tmp-probe/save_capture2.json','w').write(json.dumps(captured, ensure_ascii=False))
        print("captured:", len(captured))
        if captured:
            print("POST field:", captured[0].get("post")[:200])
            print("CT:", captured[0]["headers"].get("content-type"))
        # capture any dialog text
        try:
            txt = await page.evaluate("() => Array.from(document.querySelectorAll('.ant-modal, .ant-message, .ant-notification')).map(e=>e.innerText).join(' | ')")
            print("DIALOG:", txt[:800])
        except Exception: pass
        await b.close()
asyncio.run(main())
