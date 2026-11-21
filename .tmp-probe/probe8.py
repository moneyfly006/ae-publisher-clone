import requests, json, itertools
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/edit?id=220114595363182310"})
BASE="https://www.dianxiaomi.com"
def post(name, path, data):
    r = s.post(BASE+path, data=data, timeout=40)
    try: d=r.json()
    except Exception: d={"raw":r.text[:200]}
    print(f"\n### {name} {data} -> code={d.get('code')} msg={d.get('msg')}")
    dd=d.get("data")
    print(json.dumps(dd, ensure_ascii=False)[:900] if dd is not None else "")
    return d
post("types US","/api/amazonProductSp/gerProductTypes.json",{"site":"US"})
post("types CA","/api/amazonProductSp/gerProductTypes.json",{"site":"CA"})
post("cat attr","/api/amazonProductSp/getProductAttrById.json",{"site":"CA","categoryId":"13553005011"})
post("cat attr2","/api/amazonProductSp/getProductAttrById.json",{"site":"CA","categoryId":"13553005011","productType":"OUTDOOR_LIVING"})
post("attr js","/api/amazonProductSp/getAttrJsUrl.json",{"site":"CA","categoryId":"13553005011"})
post("logistics","/api/amazon/logistics/getLogisticsTemplates.json",{"shopId":"9408005","site":"CA"})
post("brands","/api/amazon/brand/getBrands.json",{"shopId":"9408005","site":"CA","name":"heng"})
post("currency","/api/amazon/currency/list.json",{"shopId":"9408005","site":"CA"})
post("currency2","/api/amazon/currency/list.json",{"shopId":"9408005"})
post("cat path","/api/amazonProductSp/getCategoryBySiteNodePath.json",{"site":"CA","categoryBrowsePath":"6299024011/6256963011/6256964011/13553005011"})
post("predict by item","/api/amazonProductSp/productTypePredictByItemName.json",{"site":"CA","itemName":"backhoe loader"})
post("predict cates","/api/amazonProductSp/productTypePredictCatesByItemName.json",{"site":"CA","itemName":"backhoe loader"})
post("history","/api/amazonProductSp/listHistory.json",{"shopId":"9408005","site":"CA"})
