"""自动类目与 SKU 不兼容时，纠正为模板类目。"""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from types import SimpleNamespace
from aeclone.services.publisher import Publisher, SaveResult

class _Dxm:
    def _get(self, ep, params):
        return {"data":{"product":{"id":params["id"],"categoryId":"100007204"}}}
    def category_attributes(self, cid):
        if str(cid)=="202216043":
            return [{"arrtNameId":"14","sku":1,"values":json.dumps([{"id":"10"},{"id":"29"}])}]
        if str(cid)=="100007204":
            return [{"arrtNameId":"14","sku":1,"values":json.dumps([{"id":"350686"}])}]
        return []

def main():
    pub=object.__new__(Publisher)
    pub.cfg=SimpleNamespace(auto_detect_category=True, template_product_id="tpl")
    pub.dxm=_Dxm()
    target={"id":"p","categoryId":"202216043","subject":"drilling rig",
            "aeopAeProductSKUs":json.dumps([{"aeopSKUProperty":[{"skuPropertyId":"14","propertyValueId":"350686"}]}])}
    res=SaveResult(ok=True,message="")
    out=pub._resolve_auto_category(target,res)
    assert out["categoryId"]=="100007204",out.get("categoryId")
    assert any("不兼容" in s.detail for s in res.steps),[(s.name,s.detail) for s in res.steps]
    print("PASS: 不兼容自动类目已纠正为模板类目")
if __name__=="__main__": main()
