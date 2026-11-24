"""清空发货地后，只剩空属性组合的 SKU 必须合并，否则平台判重复规格。"""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.publisher import Publisher, SaveResult

class D:
 def category_attributes(self,cid): return []

def main():
 p=object.__new__(Publisher);p.dxm=D()
 target={"categoryId":"100007204","aeopAeProductSKUs":json.dumps([
  {"skuCode":"A","ipmSkuStock":"1","aeopSKUProperty":[{"skuPropertyId":"200007763","propertyValueId":"5580"}]},
  {"skuCode":"B","ipmSkuStock":"2","aeopSKUProperty":[{"skuPropertyId":"200007763","propertyValueId":"100014969"}]},
 ])}
 r=SaveResult(ok=True,message="")
 target=p._repair_ships_from(target,r)
 target=p._dedupe_product_skus(target,r)
 rows=json.loads(target["aeopAeProductSKUs"])
 assert len(rows)==1, rows
 assert rows[0]["ipmSkuStock"]=="3", rows
 print("PASS: 清空发货地后的空属性 SKU 已合并，库存累加")
if __name__=="__main__": main()
