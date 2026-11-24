"""其他平台发布前预检：图片/重复变体/仓库。"""
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services import alibaba_payload, tiktok_payload, shopee, image_preflight

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 # Alibaba: 图片必须是 http(s)，重复 SKU 阻断
 save={"categoryId":"100007204","fullCid":"1,2,100007204","subject":"T","keywords":"a,b,c",
       "mainImage":"ftp://bad/x.jpg","priceType":"ladder_price",
       "bulkDiscountPrices":[{"start_quantity":10,"price":1}],
       "wholesaleTrade":json.dumps({"min_order_quantity":1,"package_size":"10X10X10","weight":1}),
       "attributes":[],"productSku":json.dumps({"attributes":["Color"],"special_skus":[
           {"price":1,"sku_code":"A","attributes":[{"attribute_name":"Color","value_name":"Red"}]},
           {"price":2,"sku_code":"B","attributes":[{"attribute_name":"Color","value_name":"Red"}]}]})}
 issues=alibaba_payload.validate_alibaba_save({"id":"p"},save)
 chk("Alibaba 非 HTTP 主图被拦截", any("HTTP/HTTPS" in x for x in issues), issues)
 chk("Alibaba 重复 SKU 组合被拦截", any("SKU 属性组合重复" in x for x in issues), issues)
 # TikTok 发布前缺仓库
 p={"shopId":"s","site":"US","categoryId":"1","isLeaf":1,"title":"T"*20,
    "description":"<p>d</p>","mainImage":"https://img.example.com/1.jpg",
    "price":10,"quantity":1,"packageWeight":1,"warehouseId":"","sellerSku":"S"}
 iss=tiktok_payload.validate_tiktok_payload(p,site="US",for_publish=True)
 chk("TikTok 发布前缺仓库被拦截", any("仓库" in x for x in iss), iss)
 # Shopee 重复变体 ID
 sp={"shopId":"1","categoryId":"1","itemName":"T"*30,"description":"d"*60,
     "mainImage":"https://img.example.com/1.jpg","weight":1,
     "variationList":[{"id":"1","originalPrice":10,"normalStock":1},
                      {"id":"1","originalPrice":11,"normalStock":2}]}
 iss=shopee.validate(sp,mode="global")
 chk("Shopee 重复变体 ID 被拦截", any("变种 ID 重复" in x for x in iss), iss)
 # 测试域/假客户端不做联网探活
 class FakeClient: pass
 chk("测试桩跳过图片探活", image_preflight.preflight_field(
     {"mainImage":"https://img.example.com/1.jpg"}, "mainImage", ";", client=FakeClient()) == [])
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
