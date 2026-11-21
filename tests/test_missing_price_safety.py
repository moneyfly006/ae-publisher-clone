from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher
from aeclone.services.api_save import build_ai_overrides
import json
from types import SimpleNamespace as NS

class PriceSafetyTests(unittest.TestCase):
    def publisher(self, **settings):
        base=dict(price_mode='multiplier',goods_cost='0',price_multiplier='2',price_fixed_add='0',
                  write_price_to_product=True,tuoguan_price_tiered_enabled=False)
        base.update(settings)
        pub=Publisher.__new__(Publisher);pub.cfg=AppConfig(base)
        return pub

    def test_missing_source_price_cannot_silently_compute_zero(self):
        with self.assertRaisesRegex(ValueError,'未读取到采集价格'):
            self.publisher()._price({'price':None})

    def test_explicit_default_cost_is_used_when_source_missing(self):
        product=self.publisher(goods_cost='125')._price({'price':None})
        self.assertEqual(product['price_result'].final,250)

    def test_collected_price_precedes_default_cost(self):
        product=self.publisher(goods_cost='125')._price({'price':100})
        self.assertEqual(product['price_result'].final,200)

    def test_preserve_price_and_fixed_price_do_not_need_acquisition_cost(self):
        self.publisher(write_price_to_product=False)._price({'price':None})
        product=self.publisher(price_mode='fixed',price_fixed='50')._price({'price':None})
        self.assertEqual(product['price_result'].final,50)

    def test_missing_cost_preserves_distinct_prices_without_changing_global_settings(self):
        pub = self.publisher()
        rows = [{'skuCode':'A','skuPrice':'12.34'}, {'skuCode':'B','skuPrice':'56.78'}]
        product = pub._price({'price':0, '_source_sku_rows':rows})
        self.assertTrue(product['_preserve_existing_prices'])
        self.assertTrue(pub.cfg.write_price_to_product)
        self.assertEqual(product['price_result'].mode,'preserve')
        # Even a later computed result cannot accidentally overwrite these prices.
        product['price_result'] = NS(final=999)
        output = build_ai_overrides({'aeopAeProductSKUs':rows}, product, pub.cfg)
        self.assertEqual([r['skuPrice'] for r in json.loads(output['aeopAeProductSKUs'])],['12.34','56.78'])
        self.assertEqual((output['productMinPrice'],output['productMaxPrice']),('12.34','56.78'))

    def test_any_invalid_price_blocks_automatic_preservation(self):
        for value in [None, '', 'nan', 'inf', '0', '-1', '1000000']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.publisher()._price({'_source_sku_rows':[{'skuPrice':'10'},{'skuPrice':value}]})

    def test_prices_changed_to_invalid_before_save_are_rejected(self):
        product = self.publisher()._price({'_source_sku_rows':[{'skuPrice':'10'}]})
        with self.assertRaises(ValueError):
            build_ai_overrides({'aeopAeProductSKUs':[{'skuPrice':'0'}]}, product, self.publisher().cfg)

    def test_explicit_cost_still_recalculates_despite_previous_preservation(self):
        product = self.publisher(goods_cost='100')._price({'_preserve_existing_prices':True})
        self.assertNotIn('_preserve_existing_prices',product)
        self.assertEqual(product['price_result'].final,200)

if __name__=='__main__':unittest.main(verbosity=2)
