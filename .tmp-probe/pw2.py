import asyncio
from playwright.async_api import async_playwright
COOKIE = open('.tmp-probe/cookies.txt').read().strip()
def parse(raw):
    return [{"name":k.strip(),"value":v.strip(),"domain":".dianxiaomi.com","path":"/"}
            for k,v in (p.split('=',1) for p in raw.split(';') if '=' in p)]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await b.new_context(viewport={"width":1600,"height":1000})
        await ctx.add_cookies(parse(COOKIE))
        page = await ctx.new_page()
        reqs=[]
        page.on("request", lambda r: reqs.append((r.method, r.url, r.post_data)) if "/api/" in r.url else None)
        r = await page.goto("https://www.dianxiaomi.com/web/amazon/pageList/draft", wait_until="domcontentloaded", timeout=60000)
        print("status", r.status, "url", page.url)
        await page.wait_for_timeout(8000)
        print("title:", await page.title())
        print("api calls:", len(reqs))
        for m,u,pd in reqs[:30]: print(" ", m, u, (str(pd)[:200] if pd else ""))
        await page.screenshot(path='.tmp-probe/dxm_draft.png', full_page=False)
        print("saved shot")
        await b.close()
asyncio.run(main())
