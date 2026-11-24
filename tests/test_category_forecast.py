"""自动分类：没有商品类目时使用平台预测类目 forecastCategoryId。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.config import AppConfig
from aeclone.services.api_save import build_save_payload

def main():
    product={"id":"p","subject":"Hydraulic Mini Excavator","categoryId":"",
             "forecastCategoryId":"100007204;100007205",
             "aeopAeProductSKUs":'[{"skuCode":"S1","originalBox":"1"}]'}
    template={"categoryId":"999999","aeopAeProductSKUs":'[{"skuCode":"T"}]'}
    cfg=AppConfig({"auto_detect_category":True})
    p=build_save_payload(product,template,action="save_only",cfg=cfg)
    assert p["categoryId"]=="100007204",p.get("categoryId")
    print("PASS: 平台预测类目已自动采用")
if __name__=="__main__": main()
