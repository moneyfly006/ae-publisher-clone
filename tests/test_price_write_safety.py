"""价格、主图与详情写回开关的实际输出验证。"""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aeclone.services.api_save import build_ai_overrides

class WriteSwitchTests(unittest.TestCase):
    def test_disabled_price_preserves_each_variant_price(self):
        product={'aeopAeProductSKUs':[{'id':'a','skuCode':'A','skuPrice':'10'},
                                      {'id':'b','skuCode':'B','skuPrice':'20'}]}
        cfg=NS(write_price_to_product=False,stock_quantity='')
        out=build_ai_overrides(product,{'price_result':NS(final=99)},cfg)
        self.assertEqual(out['productMinPrice'],'10.00')
        self.assertEqual(out['productMaxPrice'],'20.00')
        self.assertEqual([r['skuPrice'] for r in json.loads(out['aeopAeProductSKUs'])],['10','20'])

    def test_enabled_price_updates_sku_and_product_range_together(self):
        cfg=NS(write_price_to_product=True,stock_quantity='')
        out=build_ai_overrides({'aeopAeProductSKUs':[{'skuCode':'A','skuPrice':'10'}]},
                               {'price_result':NS(final=99)},cfg)
        self.assertEqual(out['productMinPrice'], '99.00')
        self.assertEqual(json.loads(out['aeopAeProductSKUs'])[0]['skuPrice'],'99.00')

    def test_disabled_image_and_detail_writeback_do_not_override(self):
        cfg=NS(write_main_images_to_product=False,write_detail_html_to_product=False,stock_quantity='')
        out=build_ai_overrides({'detail':'original'},{'detail_html':'new'},cfg,images=['new-image'])
        self.assertNotIn('detail',out);self.assertNotIn('imageURLs',out)
        out=build_ai_overrides({'detail':'original'},{'detail_local_images_html':'local'},cfg)
        self.assertNotIn('detail',out)

if __name__=='__main__': unittest.main(verbosity=2)
