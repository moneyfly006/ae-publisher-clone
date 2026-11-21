import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/pageList/draft"})
B="https://www.dianxiaomi.com"
def post(name, path, data):
    r=s.post(B+path, data=data, timeout=40); d=r.json()
    dd=d.get("data")
    print(f"### {name}: code={d.get('code')} msg={d.get('msg')}")
    if isinstance(dd,dict):
        print(" keys:", list(dd.keys()))
        page=dd.get("page") or {}
        if page: print(" list:", len(page.get("list") or []), "total:", page.get("totalSize"), "pageNo:", page.get("pageNo"))
        for k in ("draftNum","onlineNum","marketPids","leftStat","ecomType"):
            if k in dd: print(f"  {k}:", json.dumps(dd[k], ensure_ascii=False)[:300])
    return d
post("draft", "/api/amazonProduct/pageList.json", {"pageNo":1,"pageSize":5,"shopId":"-1","dxmState":"draft"})
post("offline", "/api/amazonProduct/pageList.json", {"pageNo":1,"pageSize":5,"shopId":"-1","dxmState":"offline"})
post("online", "/api/amazonProduct/pageList.json", {"pageNo":1,"pageSize":5,"shopId":"-1","dxmState":"online"})
post("predict", "/api/amazonProductSp/productTypePredictCatesByItemName.json", {"shopId":"9408005","site":"CA","itemName":"Backhoe Loader"})
post("banned", "/api/bannedWord/amazonCheckBannedWord.json", {"title":"Backhoe Loader 5 Ton","description":"<p>Best product 100%</p>","bulletPoint":"A±B","searchTerms":"backhoe"})
post("banned2", "/api/bannedWord/amazonCheckBannedWord.json", {"title":"Backhoe loader for farm","description":"<p>Heavy duty machine</p>","bulletPoint":"Heavy duty±Diesel","searchTerms":"backhoe loader"})
