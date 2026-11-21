import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/"})
r = s.get("https://www.dianxiaomi.com/api/userIn.json", timeout=20).json()
d = r["data"]
print("id", d.get("id"), "account", d.get("account"))
sm = d.get("shopMap") or {}
print("shops:", len(sm))
for sid, sh in sm.items():
    print(sid, "|", sh.get("name"), "| platform?", sh.get("platform") or sh.get("platformName"), "| mp", sh.get("marketPlaceId"), "| merchant", sh.get("merchantId"))
open('.tmp-probe/userIn.json','w').write(json.dumps(r, ensure_ascii=False, indent=1))
