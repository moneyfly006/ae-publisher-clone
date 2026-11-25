"""Alibaba 自动类目/模板属性继承（离线桩）。"""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.alibaba_payload import auto_alibaba_category, merge_attributes

class Fake:
 def search_categories(self,kw,max_nodes=0):
  return [
   {"categoryId":"P1","nameEn":"Pool Lights","nodePath":"Lights & Lighting/Professional Lighting/Pool Lights","nodePathId":"39/40/P1","isleaf":1},
   {"categoryId":"P2","nameEn":"Pool","nodePath":"Sports & Entertainment/Water Sports/Pool & Accessories/Pool","nodePathId":"18/100005657/100005663/P2","isleaf":1},
   {"categoryId":"P3","nameEn":"Bathtubs & Whirlpools","nodePath":"Construction/Bathroom/Bathtubs","nodePathId":"13/14/P3","isleaf":1},
  ]

class AutoCategoryTests(unittest.TestCase):
 def test_pool_prefers_pool_category(self):
  cid,full=auto_alibaba_category(Fake(),{"subject":"Above Ground Swimming Pool"})
  self.assertEqual(cid,"P2"); self.assertEqual(full,"18,100005657,100005663,P2")
 def test_template_attributes_only_fill_missing(self):
  out=merge_attributes({"attributes":[{"attribute_name":"Material","value_name":"Steel"}]},None,
                       {"template_attributes":[{"attribute_name":"Material","value_name":"Wood"},
                                                {"attribute_name":"Warranty","value_name":"1 Year"}]})
  by={a["attribute_name"]:a["value_name"] for a in out}
  self.assertEqual(by["Material"],"Steel")
  self.assertEqual(by["Warranty"],"1 Year")

if __name__=="__main__": unittest.main()
