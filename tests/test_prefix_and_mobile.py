"""前后缀不清空原内容；本地详情图同步到 mobileDetail。"""
import json, sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.api_save import build_ai_overrides, build_save_payload
from aeclone.services.text_pipeline import TextPipeline
from aeclone.services.publisher import Publisher

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 # 标题前后缀
 tp=object.__new__(TextPipeline)
 tp.cfg=SimpleNamespace(title_prefix="PRE", title_suffix="POST")
 tp.text=SimpleNamespace(translate_title=lambda t:"Original Product Title")
 tp.client=object()
 r=tp.make_title("原始标题")
 chk("标题保留原文并加前后缀", r.ok and "Original Product Title" in r.value and r.value.startswith("PRE ") and r.value.endswith(" POST"), r.value)
 # SKU 前缀：默认不丢原编码
 pub=object.__new__(Publisher)
 pub.cfg=SimpleNamespace(custom_sku_prefix="MY",custom_sku_prefix_enable=True,
                         only_sku_prefix_checkbox=False,sku_split_char="-",
                         stock_quantity="",auto_generate_sku_code=False)
 p=pub._normalize({"skus":[{"sku":"RED-M"}],"images":[]})
 chk("SKU 前缀保留原编码", p["skus"][0]["sku"]=="MY-RED-M", p["skus"][0]["sku"])
 # 本地详情图 + 原描述 + mobileDetail
 _prod={"id":"p","shopId":"s","categoryId":"100007204","detail":"<p>Original collected text</p>",
        "aeopAeProductSKUs":json.dumps([{"skuCode":"S1","originalBox":"1"}])}
 cfg=SimpleNamespace(write_detail_html_to_product=True,delete_detail_text=False,
                     custom_detail_text_enable=True,detail_local_image_position="head",
                     detail_text_info="",detail_text_info_tail="",copy_sku_to_detail=False)
 p2={"detail_local_images_html":'<p><img src="https://local/x.jpg" /></p>'}
 ov=build_ai_overrides(_prod,p2,cfg)
 chk("本地图只插入且保留原描述", "Original collected text" in ov.get("detail","") and "https://local/x.jpg" in ov.get("detail",""), ov.get("detail",""))
 payload=build_save_payload(_prod,_prod,action="save_only",overrides=ov,cfg=cfg)
 chk("手机详情同步本地图", "https://local/x.jpg" in str(payload.get("mobileDetail")), str(payload.get("mobileDetail"))[:200])
 chk("手机详情保留原描述", "Original collected text" in str(payload.get("mobileDetail")), str(payload.get("mobileDetail"))[:200])

 # AI 描述 + 本地图片前置
 from aeclone.services.detail_content import compose_detail
 composed=compose_detail({"detail_html":"<p>AI generated description</p>",
                          "detail_local_images_html":'<p><img src="https://local/ai-prefix.jpg" /></p>'},
                         SimpleNamespace(custom_detail_text_enable=True,delete_detail_text=False,
                                         detail_text_info="",detail_text_info_tail="",
                                         detail_text_position="head",detail_local_image_position="head",
                                         copy_sku_to_detail=False,move_detail_text=False))
 chk("AI描述前成功插入本地图", composed.startswith('<p><img src="https://local/ai-prefix.jpg"') and "AI generated description" in composed, composed[:120])
 ai_ov=build_ai_overrides({"detail":"<p>old</p>"},{"detail_html":composed},cfg)
 chk("AI描述+本地图写入detail", "AI generated description" in ai_ov.get("detail","") and "https://local/ai-prefix.jpg" in ai_ov.get("detail",""), ai_ov.get("detail","")[:160])
 chk("AI描述+本地图同步mobileDetail", "AI generated description" in str(ai_ov.get("mobileDetail")) and "https://local/ai-prefix.jpg" in str(ai_ov.get("mobileDetail")), str(ai_ov.get("mobileDetail"))[:160])
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
