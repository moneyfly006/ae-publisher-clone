import asyncio, json
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
            try:
                buf = request.post_data_buffer
                captured.append({"url": request.url, "headers": dict(request.headers),
                                 "body": buf.decode('utf-8','replace') if buf else (request.post_data or "")})
            except Exception as e:
                captured.append({"url":request.url,"error":str(e)})
            await route.abort()
        await page.route("**/api/amazonProductSp/add.json", handler)
        await page.goto(f"https://www.dianxiaomi.com/web/amazon/edit?id={PID}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        # kill notice modals
        await page.evaluate("""() => { document.querySelectorAll('.ant-modal-root,.ant-modal-wrap,.ant-modal-mask').forEach(e=>e.remove()); }""")
        await page.wait_for_timeout(500)
        # dump buttons
        btns = await page.evaluate("""() => Array.from(document.querySelectorAll('button')).map(b=>({t:(b.innerText||'').trim(), c:b.className})).filter(x=>x.t)""")
        print("BUTTONS:", json.dumps(btns[:60], ensure_ascii=False))
        await page.screenshot(path='.tmp-probe/editor_clean.png', full_page=False)
        # scroll through page and screenshot
        for i,frac in enumerate([0.0,0.2,0.4,0.6,0.8,1.0]):
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight*{frac})")
            await page.wait_for_timeout(700)
            await page.screenshot(path=f'.tmp-probe/editor_scroll_{i}.png', full_page=False)
        # find save button
        save = page.locator("button:has-text('保存')").first
        try:
            vis = await save.is_visible(timeout=1000)
            print("save visible:", vis)
        except Exception as e:
            print("save lookup err", e)
        if await save.count() > 0:
            await save.scroll_into_view_if_needed()
            await save.click()
            await page.wait_for_timeout(5000)
            print("captured add.json:", len(captured))
        try:
            await page.screenshot(path='.tmp-probe/editor_after_save.png')
        except Exception: pass
        if captured:
            open('.tmp-probe/save_capture.json','w').write(json.dumps(captured, ensure_ascii=False, indent=1))
            body=captured[0].get('body','')
            print("BODY LEN:", len(body))
            print(body[:3000])
        # dialogs text
        try:
            txt = await page.evaluate("() => document.body.innerText.slice(0,3000)")
            print("PAGE TEXT:", txt[:1500])
        except Exception: pass
        await b.close()
asyncio.run(main())
