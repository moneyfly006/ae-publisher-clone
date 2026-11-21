import asyncio, json, re
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
        calls=[]
        async def on_resp(resp):
            u=resp.url
            if "/api/" in u:
                try:
                    body=await resp.text()
                except Exception:
                    body=""
                calls.append((resp.request.method,u,resp.request.post_data or "", resp.status, body[:4000]))
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))
        await page.goto(f"https://www.dianxiaomi.com/web/amazon/edit?id={PID}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)
        print("title:", await page.title())
        # close any modal
        for sel in ["button:has-text('关闭')", ".ant-modal-close", "button:has-text('知道了')"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=800):
                    await el.click(); await page.wait_for_timeout(500)
            except Exception: pass
        await page.screenshot(path='.tmp-probe/dxm_editor.png', full_page=False)
        print("screenshot saved; calls:", len(calls))
        seen=set()
        for m,u,pd,st,body in calls:
            key=u.split('?')[0]
            if key in seen: continue
            seen.add(key)
            print(f"\n=== {m} {u} [{st}]")
            if pd: print("POST:", str(pd)[:300])
            print("RESP:", body[:500].replace('\n',' '))
        json.dump([{"method":m,"url":u,"post":pd,"status":st,"body":body} for m,u,pd,st,body in calls],
                  open('.tmp-probe/editor_calls.json','w'), ensure_ascii=False, indent=1)
        await b.close()
asyncio.run(main())
