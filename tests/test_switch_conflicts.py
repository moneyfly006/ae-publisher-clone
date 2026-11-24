"""开关联动：被忽略/可能清空内容的组合必须给出提示。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.config import AppConfig
from aeclone.services.conflicts import check
from aeclone.services.detail_content import compose_detail
from types import SimpleNamespace

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def has(cfg, text):
 return any(text in x.title for x in check(cfg))

def main():
 print("="*60)
 c=AppConfig(); c.set("title_prefix","PRE"); c.set("enable_ai_title",False)
 chk("标题前后缀无AI标题会提示", has(c,"标题前后缀"))
 c=AppConfig(); c.set("custom_detail_text_enable",False); c.set("detail_text_info","X")
 chk("详情内容总开关关闭会提示", has(c,"详情文字/本地图片"))
 c=AppConfig(); c.set("write_detail_html_to_product",False); c.set("custom_detail_text_enable",True); c.set("detail_text_info","X")
 chk("详情写回关闭会提示", has(c,"详情写回商品关闭"))
 c=AppConfig(); c.set("delete_detail_text",True); c.set("enable_ai_detail",False); c.set("custom_detail_text_enable",False)
 chk("删原文无替代内容会提示", has(c,"删除原详情文字"))
 c=AppConfig(); c.set("write_sku_codes_to_product",False); c.set("custom_sku_prefix","MY"); c.set("custom_sku_prefix_enable",True)
 chk("SKU前缀不写回会提示", has(c,"SKU 编码规则"))
 out=compose_detail({"detail":"<p>Original kept</p>"},
   SimpleNamespace(delete_detail_text=True,custom_detail_text_enable=False,
                   detail_text_info="",detail_text_info_tail="",copy_sku_to_detail=False,
                   move_detail_text=False,detail_local_image_position="tail"))
 chk("删原文开关未配自定义内容时保留原描述", "Original kept" in out, out)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
