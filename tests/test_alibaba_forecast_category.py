"""Alibaba 自动类目：没有 categoryId 时使用平台预测类目，再进入属性补全。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.config import AppConfig
from aeclone.services.alibaba_payload import resolve_category

def main():
    product={"id":"p","subject":"Hydraulic Excavator","forecastCategoryId":"202216043;202216035"}
    _cfg=AppConfig({"auto_detect_category":True}); _cfg.set("alibaba_category_id","")
    cid,full=resolve_category(product,_cfg)
    assert cid=="202216043",(cid,full)
    _cfg2=AppConfig({"auto_detect_category":False}); _cfg2.set("alibaba_category_id",""); _cfg2.set("alibaba_full_cid","")
    cid2,full2=resolve_category(product,_cfg2)
    assert cid2=="",(cid2,full2)
    print("PASS: Alibaba 平台预测类目可自动采用；关闭自动时不使用")
if __name__=="__main__": main()
