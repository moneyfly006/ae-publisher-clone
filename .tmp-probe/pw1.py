import asyncio, json, sys
from playwright.async_api import async_playwright

COOKIE = open('.tmp-probe/cookies.txt').read().strip()

def parse_cookies(raw):
    out=[]
    for part in raw.split(';'):
        if '=' not in part: continue
        k,v=part.strip().split('=',1)
        out.append({"name":k,"value":v,"domain":".dianxiaomi.com","path":"/"})
    return out

async def main():
    async with async_playwright() as p:
        for bt in (p.chromium, p.webkit):
            try:
                b = await bt.launch(headless=True)
                print("launched", bt.name)
                ctx = await b.new_context()
                await ctx.add_cookies(parse_cookies(COOKIE))
                page = await ctx.new_page()
                reqs=[]
                page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url else None)
                r = await page.goto("https://www.dianxiaomi.com/web/amazon/pageList/draft", wait_until="domcontentloaded", timeout=45000)
                print("status", r.status if r else None, "url", page.url)
                await page.wait_for_timeout(6000)
                print("title", await page.title())
                print("api reqs:", len(reqs))
                for u in reqs[:20]: print("  ", u)
                await page.screenshot(path='.tmp-probe/dxm_draft.png', full_page=False)
                print("shot ok")
                await b.close()
                return
            except Exception as e:
                print("fail", bt.name, str(e)[:300])

asyncio.run(main())
