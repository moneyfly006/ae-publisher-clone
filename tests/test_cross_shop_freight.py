"""模板商品属于另一店铺时，不能套用它的运费模板 id。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.api_save import build_save_payload

def main():
    product={"id":"p","shopId":"9351129","categoryId":"100007204","subject":"T",
             "aeopAeProductSKUs":'[{"skuCode":"S1","freightTemplateId":"50202566651","originalBox":"1"}]'}
    template={"id":"tpl","shopId":"9190736","categoryId":"100007204","freightTemplateId":"50192654715",
              "aeopAeProductSKUs":'[{"skuCode":"T","freightTemplateId":"50192654715"}]'}
    p=build_save_payload(product,template,action="save_only")
    assert p.get("freightTemplateId") != "50192654715", p.get("freightTemplateId")
    rows=p.get("aeopAeProductSKUs") or "[]"
    import json
    assert all(x.get("freightTemplateId") != "50192654715" for x in json.loads(rows))
    print("PASS: 跨店铺模板运费未被套用")
if __name__=="__main__": main()
