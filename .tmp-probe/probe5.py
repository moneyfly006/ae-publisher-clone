import requests, re, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "Referer": "https://www.dianxiaomi.com/web/amazon/amazonProductList/draft"})
# try editor pages for a draft id
pid = "220114595363182310"
for url in [f"/web/amazon/amazonProduct/edit?id={pid}",
            f"/web/amazon/amazonProductEdit?id={pid}",
            f"/web/amazon/edit?id={pid}",
            f"/web/amazon/amazonProductList/draft"]:
    r = s.get("https://www.dianxiaomi.com"+url, timeout=25)
    print(url, r.status_code, len(r.text), r.url)
    if r.status_code == 200:
        open('.tmp-probe/page_'+re.sub(r'[^a-zA-Z0-9]+','_',url)[-60:]+'.html','w').write(r.text)
        print("  scripts:", re.findall(r'<script[^>]+src="([^"]+)"', r.text)[:8])
        print("  title:", re.findall(r'<title>(.*?)</title>', r.text)[:1])
