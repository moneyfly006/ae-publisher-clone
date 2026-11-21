import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/amazonProductList/draft"})
r = s.post("https://www.dianxiaomi.com/api/amazonProduct/pageList.json", data={"pageNo":1,"pageSize":20,"shopId":"-1","dxmState":"draft"}, timeout=30)
d = r.json()["data"]
open('.tmp-probe/amazon_draft.json','w').write(json.dumps(r.json(), ensure_ascii=False, indent=1))
print("keys:", list(d.keys()))
print("stat:", d.get("stat"))
print("page keys:", list((d.get("page") or {}).keys()))
rows = (d.get("page") or {}).get("list") or d.get("list") or []
print("rows:", len(rows))
if rows:
    print("row keys:", list(rows[0].keys()))
    print(json.dumps(rows[0], ensure_ascii=False, indent=1)[:3000])
# online
r2 = s.post("https://www.dianxiaomi.com/api/amazonProduct/list.json", data={"pageNo":1,"pageSize":20}, timeout=30)
d2 = r2.json()["data"]
open('.tmp-probe/amazon_online.json','w').write(json.dumps(r2.json(), ensure_ascii=False, indent=1))
print("\n=== list.json keys:", list(d2.keys()))
r3 = (d2.get("page") or {}).get("list") or d2.get("list") or []
print("online rows:", len(r3), "stat", d2.get("stat"))
if r3: print("row keys:", list(r3[0].keys()))
