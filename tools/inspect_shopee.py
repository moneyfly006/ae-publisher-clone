"""只读采集店小秘 Shopee 页面、静态资源和接口名；不输出登录凭据。"""
import asyncio
import argparse
import json
from pathlib import Path
import re
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "shopee-reference"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="/web/shopeeGlobal/draft")
    parser.add_argument("--output", default="shopee-reference")
    parser.add_argument("--probe", action="store_true", help="使用项目客户端进行只读接口验证")
    parser.add_argument("--categories", action="store_true", help="只读打开类目选择器并展开首级类目")
    args = parser.parse_args()
    global OUTPUT
    OUTPUT = ROOT / "build" / args.output
    if not OUTPUT.resolve().is_relative_to((ROOT / "build").resolve()):
        parser.error("output must be under build/")
    if not args.path.startswith("/web/"):
        parser.error("only /web/ page paths are allowed")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "build/reference-cookie.txt"
    raw = (source if source.exists() else ROOT / ".tmp-probe/cookies.txt").read_text().strip()
    if args.probe:
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from aeclone.integrations.shopee import ShopeeClient
        report = []
        for mode in ("global", "site"):
            client = ShopeeClient(raw, mode=mode)
            for label, action in (("shops", client.shops),
                                  ("list", lambda: client.list_products(page_size=1)),
                                  ("limits", lambda: client.limits("global" if mode == "global" else "SG")),
                                  ("categories", client.categories)):
                try:
                    data = action()
                    path = OUTPUT / f"probe-{mode}-{label}.json"
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    path.chmod(0o600)
                    report.append(dict(mode=mode, action=label, ok=True,
                        count=len(data) if isinstance(data, list) else None))
                except Exception as error:
                    report.append(dict(mode=mode, action=label, ok=False, error=str(error)))
            if mode == "global":
                try:
                    children = client.categories("100535")
                    (OUTPUT / "probe-global-children.json").write_text(json.dumps(children, ensure_ascii=False, indent=2))
                    report.append(dict(mode=mode, action="children", ok=True, count=len(children),
                        parent_ids=sorted({str(row.get("categoryParentId")) for row in children})))
                    leaf = next((row for row in children if row.get("isLeaf")), None)
                    if leaf:
                        attrs = client.attributes(str(leaf["categoryId"]))
                        (OUTPUT / "probe-global-attributes.json").write_text(json.dumps(attrs, ensure_ascii=False, indent=2))
                        report.append(dict(mode=mode, action="attributes", ok=True,
                            count=len(attrs) if isinstance(attrs, list) else None))
                except Exception as error:
                    report.append(dict(mode=mode, action="children/attributes", ok=False, error=str(error)))
        (OUTPUT / "probe-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
        return
    cookies = []
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            cookies.append(dict(name=key, value=value, domain=".dianxiaomi.com", path="/"))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.add_cookies(cookies)
            requests, resources, blocked, response_tasks = [], [], [], []

            async def protect(route):
                request = route.request
                path = request.url.split("?", 1)[0]
                # 编辑器 GET add.json 只返回新建表单默认值；POST 同路径才是保存。
                if request.method == "GET" and re.search(r"/(?:shopeeGlobalProduct|alibabaIcbuProduct)/add\.json$", path):
                    await route.continue_()
                    return
                if "/api/" in path and re.search(
                    r"/(?:[^/]*(?:save|delete|remove|publish|update|create|sync|add|set)[^/]*)\.", path, re.I
                ):
                    blocked.append(path)
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", protect)
            page = await context.new_page()
            page.on("request", lambda req: requests.append({
                "method": req.method, "path": req.url.split("?", 1)[0]
            } | ({"params": req.post_data} if req.post_data and
                 any(x in req.url for x in ("pageList.json", "getOfflineCounts.json", "globalList.json", "globalAttributeList.json")) else {}))
                if "/api/" in req.url else None)

            async def resource(response):
                url = response.url.split("?", 1)[0]
                if "/api/" in url and any(x in url for x in (
                    "shopeeGlobalProduct/add.json", "alibabaIcbuProduct/add.json",
                    "Product/edit.json", "ShopInfoSync/list.json", "userIn.json",
                    "shopeeCategory/getLimitBySite.json", "shopeeCategory/globalList.json",
                    "shopeeGlobalProduct/pageList.json"
                )):
                    try:
                        name = "api_" + url.split("/api/", 1)[1].replace("/", "_")
                        path = OUTPUT / name
                        path.write_bytes(await response.body())
                        path.chmod(0o600)
                    except Exception:
                        pass
                if url.endswith(".js") and "dianxiaomi" in url:
                    try:
                        name = url.rsplit("/", 1)[-1]
                        (OUTPUT / name).write_bytes(await response.body())
                        resources.append(name)
                    except Exception:
                        pass
            page.on("response", lambda response: response_tasks.append(asyncio.create_task(resource(response))))
            await page.goto("https://www.dianxiaomi.com" + args.path, wait_until="networkidle", timeout=60000)
            if args.categories:
                notice = page.locator(".notice-list-modal .ant-modal-close")
                if await notice.count() and await notice.first.is_visible():
                    await notice.first.click()
                await page.get_by_text("选择分类", exact=True).first.click()
                await page.locator(".ant-modal:visible").wait_for()
                modal = page.locator(".ant-modal:visible")
                print("CATEGORY MODAL", (await modal.inner_text())[:1500])
                await modal.get_by_text(re.compile("影音娱乐")).first.click()
                await page.wait_for_load_state("networkidle")
                print("CATEGORY CHILDREN", (await modal.inner_text())[:1500])
            await page.locator("body").wait_for()
            await page.screenshot(path=str(OUTPUT / "draft.png"), full_page=False)
            (OUTPUT / "draft.html").write_text(await page.content())
            body = await page.locator("body").inner_text()
            (OUTPUT / "draft.txt").write_text(body)
            await asyncio.gather(*response_tasks, return_exceptions=True)
            report = dict(url=page.url, title=await page.title(),
                          body_excerpt=body[:4500], requests=requests,
                          resources=resources, blocked=blocked)
            (OUTPUT / "capture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
