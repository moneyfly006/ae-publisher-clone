import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/edit?id=220114595363182310"})
BASE="https://www.dianxiaomi.com"
r = s.get(BASE+"/api/amazonProductSp/editSp.json", params={"id":"220114595363182310"}, timeout=30)
d = r.json()
open('.tmp-probe/amazon_edit.json','w').write(json.dumps(d, ensure_ascii=False, indent=1))
data = d["data"]
p = data["product"]
print("top keys:", list(data.keys()))
print("site:", data.get("site"), "productType:", data.get("productType"), "categoryName:", data.get("categoryName"))
print("product keys:", len(p.keys()))
print(json.dumps(list(p.keys()), ensure_ascii=False))
print("\ncategoryTree:", json.dumps(data.get("categoryTree"), ensure_ascii=False)[:600])
print("itemTypeKeyword:", data.get("itemTypeKeyword"))
print("\ndesc/bullet fields:")
for k in p:
    if any(x in k.lower() for x in ("desc","bullet","search","keyword","image","variation","price","quantity","fulfill","ship")):
        v=p[k]
        print(" ", k, "=", json.dumps(v, ensure_ascii=False)[:200])
