import requests, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1); s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/web/amazon/edit?id=220114595363182310"})
BASE="https://www.dianxiaomi.com"
MP="A2EUQ1WTGCTBG2"  # CA marketplace
def post(name, path, data):
    r = s.post(BASE+path, data=data, timeout=40)
    try: d=r.json()
    except Exception: d={"raw":r.text[:200]}
    print(f"\n### {name} {data} -> code={d.get('code')} msg={d.get('msg')}")
    dd=d.get("data")
    out=json.dumps(dd, ensure_ascii=False)
    print(out[:700])
    return d
post("logistics","/api/amazon/logistics/getLogisticsTemplates.json",{"shopId":"9408005","site":MP})
post("brands","/api/amazon/brand/getBrands.json",{"shopId":"9408005","site":MP,"name":"heng"})
post("brands2","/api/amazon/brand/getBrands.json",{"shopId":"9408005","site":MP,"keyword":"heng"})
post("currency","/api/amazon/currency/list.json",{"shopId":"9408005","site":MP})
post("attr by id","/api/amazonProductSp/getProductAttrById.json",{"id":"220114595363182310","site":MP})
post("attr by id2","/api/amazonProductSp/getProductAttrById.json",{"productId":"220114595363182310","site":MP})
post("attr js","/api/amazonProductSp/getAttrJsUrl.json",{"site":MP,"categoryId":"13553005011"})
post("attr js2","/api/amazonProductSp/getAttrJsUrl.json",{"site":MP,"productType":"OUTDOOR_LIVING"})
post("cat path","/api/amazonProductSp/getCategoryBySiteNodePath.json",{"site":MP,"categoryBrowsePath":"6299024011/6256963011/6256964011/13553005011"})
post("cat path2","/api/amazonProductSp/getCategoryBySiteNodePath.json",{"site":MP,"nodePath":"6299024011/6256963011/6256964011/13553005011"})
post("predict by item","/api/amazonProductSp/productTypePredictByItemName.json",{"shopId":"9408005","site":MP,"itemName":"backhoe loader"})
post("predict cates","/api/amazonProductSp/productTypePredictCatesByItemName.json",{"shopId":"9408005","site":MP,"itemName":"backhoe loader"})
post("types MP","/api/amazonProductSp/gerProductTypes.json",{"site":MP})
post("checkSku","/api/amazonProduct/checkSkuBatch.json",{"shopId":"9408005","site":MP,"skuList":"WY-QHCA-LTM-1"})
post("product by sku","/api/amazonProduct/getProductBySku.json",{"shopId":"9408005","site":MP,"sku":"WY-QHCA-LTM-1"})
