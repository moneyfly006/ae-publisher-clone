import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest",
                  "Referer": "https://www.dianxiaomi.com/web/amazon/pageList/draft"})
BASE="https://www.dianxiaomi.com"
def call(name, method, path, **kw):
    try:
        r = s.request(method, BASE+path, timeout=30, **kw)
        try: d=r.json()
        except Exception: d={"_raw": r.text[:300]}
        print(f"\n=== {name} [{r.status_code}] code={d.get('code')} msg={d.get('msg')}")
        data=d.get("data")
        if isinstance(data,(dict,list)):
            print("data keys:", list(data.keys())[:20] if isinstance(data,dict) else f"list[{len(data)}]")
            print(json.dumps(data, ensure_ascii=False)[:1200])
        else:
            print(json.dumps(d, ensure_ascii=False)[:800])
        return d
    except Exception as e:
        print(f"\n=== {name} ERR {e}")
        return {}

# sites for shop
call("country by shop", "POST", "/api/amazonProduct/getCountryByShopId.json", data={"shopId":"9408005"})
call("currency list", "POST", "/api/amazon/currency/list.json", data={})
call("logistics templates", "POST", "/api/amazon/logistics/getLogisticsTemplates.json", data={"shopId":"9408005"})
call("brands", "POST", "/api/amazon/brand/getBrands.json", data={"shopId":"9408005"})
call("addNew", "GET", "/api/amazonProductSp/addNew.json")
call("editSp draft", "GET", "/api/amazonProductSp/editSp.json", params={"id":"220114595363182310"})
