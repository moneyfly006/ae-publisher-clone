"""系统 Origin 与 SKU 发货地都必须统一为中国大陆。"""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.publisher import Publisher, SaveResult
from types import SimpleNamespace

class _Cat:
 def category_attributes(self,cid):
  return [{"arrtNameId":"219","values":json.dumps([
   {"id":"9441593375","names":{"en":"AE(Origin)"}},
   {"id":"9441741844","names":{"en":"Mainland China","zh":"中国大陆"}}])}]

def main():
 p=object.__new__(Publisher);p.dxm=_Cat()
 d={"categoryId":"100007204",
    "aeopAeProductPropertys":json.dumps([
      {"attrNameId":"219","attrValueId":"9441593375","attrName":"Origin"},
      {"attrName":"Place of Origin","attrValue":"Japan"}]),
    "aeopAeProductSKUs":json.dumps([{"skuCode":"S1","aeopSKUProperty":[
      {"skuPropertyId":"200007763","propertyValueId":"5580","attrVal":"JP"}]}])}
 r=SaveResult(ok=True,message="")
 o=p._repair_origin_attribute(d,r);o=p._repair_ships_from(o,r)
 attrs=json.loads(o["aeopAeProductPropertys"])
 assert [a for a in attrs if str(a.get("attrNameId"))=="219"][0]["attrValueId"]=="9441741844"
 assert not any(a.get("attrName")=="Place of Origin" for a in attrs)
 sku=json.loads(o["aeopAeProductSKUs"])[0]
 assert not any(str(x.get("skuPropertyId"))=="200007763" for x in sku.get("aeopSKUProperty") or [])
 print("PASS: SKU 发货地已清空；系统 Origin 已统一为中国大陆")
if __name__=="__main__": main()
