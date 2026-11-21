import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/amazonProductList/draft"})
def show(name, method, path, **kw):
    try:
        r = s.request(method, "https://www.dianxiaomi.com"+path, timeout=25, **kw)
        txt = r.text
        print(f"\n=== {name} {path} [{r.status_code}] len={len(txt)}")
        print(txt[:700].replace('\n',' '))
        return txt
    except Exception as e:
        print(f"\n=== {name} {path} ERR {e}")
        return ""
show("pageList POST", "POST", "/api/amazonProduct/pageList.json", data={"pageNo":1,"pageSize":20,"shopId":"-1","dxmState":"draft","searchValue":""})
show("pageList GET", "GET", "/api/amazonProduct/pageList.json", params={"pageNo":1,"pageSize":20,"shopId":"-1","dxmState":"draft"})
show("smt list", "POST", "/api/amazonProduct/list.json", data={"pageNo":1,"pageSize":20})
show("list page html", "GET", "/web/amazon/amazonProductList/draft")
