"""SKU 的开关、行身份、库存与输入隔离必须实际体现在保存结果中。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aeclone.services.api_save import build_ai_overrides, match_processed_skus


def product():
    return {'aeopAeProductSKUs': [
        {'id':'red', 'skuCode':'RED', 'ipmSkuStock':'3', 'skuPrice':'10',
         'aeopSKUProperty':[{'skuPropertyId':1,'propertyValueId':1,'imageUrlList':'red-image'}]},
        {'id':'blue', 'skuCode':'BLUE', 'ipmSkuStock':'7', 'skuPrice':'20',
         'aeopSKUProperty':[{'skuPropertyId':1,'propertyValueId':2,'imageUrlList':'blue-image'}]}]}


class SKUWriteTests(unittest.TestCase):
    def cfg(self, **kw):
        return NS(stock_quantity='', write_sku_codes_to_product=True,
                  write_price_to_product=False, **kw)

    def test_reordered_rows_match_identity_instead_of_position(self):
        edited = product()
        result = build_ai_overrides(edited, {'skus':[
            {'id':'blue','original_sku':'BLUE','sku':'NEW-B','processed_image':'blue-new'},
            {'id':'red','original_sku':'RED','sku':'NEW-R','processed_image':'red-new'}]}, self.cfg())
        rows = json.loads(result['aeopAeProductSKUs'])
        self.assertEqual([r['skuCode'] for r in rows], ['NEW-R','NEW-B'])
        self.assertEqual([r['mobileModelSkuImage'] for r in rows], ['red-new','blue-new'])

    def test_write_switch_off_keeps_codes_and_individual_inventory(self):
        cfg=self.cfg(); cfg.write_sku_codes_to_product=False
        result=build_ai_overrides(product(), {'skus':[{'id':'red','sku':'NEW-R'},{'id':'blue','sku':'NEW-B'}]}, cfg)
        rows=json.loads(result['aeopAeProductSKUs'])
        self.assertEqual([r['skuCode'] for r in rows], ['RED','BLUE'])
        self.assertEqual([r['ipmSkuStock'] for r in rows], ['3','7'])

    def test_explicit_zero_inventory_applies_to_every_variant(self):
        cfg=self.cfg();cfg.stock_quantity=0
        rows=json.loads(build_ai_overrides(product(),{},cfg)['aeopAeProductSKUs'])
        self.assertEqual([r['ipmSkuStock'] for r in rows],['0','0'])

    def test_nested_image_changes_do_not_mutate_original_or_template(self):
        edited=product(); template=product(); before=deepcopy((edited,template))
        build_ai_overrides(edited, {'skus':[{'id':'red','image':'changed'}]},self.cfg(),template=template)
        self.assertEqual((edited,template), before)

    def test_unknown_and_duplicate_identities_refuse_writeback(self):
        rows=product()['aeopAeProductSKUs']
        for processed in ([{'sku':'unknown'}],[{'id':'red'},{'id':'red'}]):
            with self.assertRaises(ValueError): match_processed_skus(rows,processed)


    def test_sku_limit_writes_only_validated_retained_rows(self):
        from aeclone.services.publisher import Publisher
        from aeclone.config import AppConfig
        from aeclone.services.product_data import hydrate_product
        source = [dict(id=str(i),skuCode=f"SKU-{i}",skuPrice=str(i+10),
                       attributes={"Color": str(i)},ipmSkuStock=i+1) for i in range(4)]
        raw = dict(id="qa", aeopAeProductSKUs=json.dumps(source))
        cfg = AppConfig(dict(max_sku_count=2,stock_quantity="",write_sku_codes_to_product=False))
        pub = Publisher.__new__(Publisher);pub.cfg = cfg
        product = hydrate_product({"id":"qa"},raw)
        product = pub._map_variants(product)
        self.assertTrue(product["_sku_limit_applied"])
        result = build_ai_overrides(raw,product,cfg)
        rows = json.loads(result["aeopAeProductSKUs"])
        self.assertEqual([row["skuCode"] for row in rows],["SKU-0","SKU-1"])
        self.assertEqual([row["ipmSkuStock"] for row in rows],[1,2])
        self.assertEqual(len(json.loads(raw["aeopAeProductSKUs"])),4)

    def test_numeric_zero_inventory_is_preserved_in_normalization(self):
        from aeclone.services.publisher import Publisher
        from aeclone.config import AppConfig
        pub = Publisher.__new__(Publisher);pub.cfg = AppConfig(dict(stock_quantity=0,combo_enable=False))
        result = pub._normalize({"skus":[{"sku":"A","stock":9}]})
        self.assertEqual(result["skus"][0]["stock"],"0")

    def test_original_box_switch_updates_product_and_every_sku(self):
        from aeclone.config import AppConfig
        from aeclone.services.api_save import build_save_payload
        for enabled, expected in ((True,"1"),(False,"0")):
            with self.subTest(enabled=enabled):
                source=product()
                source.update(id="qa",categoryId="100")
                cfg=AppConfig(dict(original_box=enabled,stock_quantity=""))
                overrides=build_ai_overrides(source,{},cfg)
                payload=build_save_payload(source,overrides=overrides,cfg=cfg)
                self.assertEqual(payload["originalBox"],expected)
                self.assertEqual({row["originalBox"] for row in json.loads(payload["aeopAeProductSKUs"])},{expected})

    def test_platform_attribute_id_shared_by_eight_distinct_sku_codes(self):
        from aeclone.config import AppConfig
        from aeclone.services.product_data import hydrate_product
        source=[dict(id="400079803:1983475005",skuCode=str(109111075028+i),
                     skuPrice="10",ipmSkuStock=i+1) for i in range(8)]
        raw=dict(aeopAeProductSKUs=source)
        product=hydrate_product({"id":"qa"},raw)
        product["skus"].reverse()
        for row in product["skus"]:row["optimized_name"]="NEW-"+row["original_sku"]
        matched=match_processed_skus(source,product["skus"])
        self.assertEqual([row["original_sku"] for row in matched],[row["skuCode"] for row in source])
        out=build_ai_overrides(raw,product,AppConfig(dict(stock_quantity="")))
        rows=json.loads(out["aeopAeProductSKUs"])
        self.assertEqual([row["skuCode"] for row in rows],["NEW-"+row["skuCode"] for row in source])
        self.assertEqual([row["ipmSkuStock"] for row in rows],list(range(1,9)))

    def test_same_platform_id_and_same_original_code_remain_blocked(self):
        source=[dict(id="shared",skuCode="same"),dict(id="shared",skuCode="same")]
        with self.assertRaisesRegex(ValueError,"身份不唯一"):
            match_processed_skus(source,[dict(_source_id="shared",original_sku="same"),
                                         dict(_source_id="shared",original_sku="same")])


    def test_template_plug_properties_merge_without_duplicating_models(self):
        """模板补 Voltage/plug 时不能覆盖商品型号，否则所有 SKU 会变成同一组合。"""
        from aeclone.services.api_save import merge_skus, validate_sku_combinations
        source = {'aeopAeProductSKUs': [
            {'skuCode': 'A', 'aeopSKUProperty': [
                {'skuPropertyId': '200007763', 'propertyValueId': 5580,
                 'propertyValueDefinitionName': 'YG-GS50'}]},
            {'skuCode': 'B', 'aeopSKUProperty': [
                {'skuPropertyId': '200007763', 'propertyValueId': 100014969,
                 'propertyValueDefinitionName': 'YG-GS130'}]}]}
        template = {'aeopAeProductSKUs': [
            {'skuCode': 'T', 'aeopSKUProperty': [
                {'skuPropertyId': '200007763', 'propertyValueId': 1,
                 'propertyValueDefinitionName': 'T-MODEL'},
                {'skuPropertyId': 400079803, 'propertyValueId': 1983474624,
                 'plug_parent_property_id': 400081003,
                 'plug_parent_value_id': 24681606136}]}]}
        out = merge_skus(source, template)
        validate_sku_combinations(out)
        self.assertEqual([r['skuCode'] for r in out], ['A', 'B'])
        self.assertEqual(len(out[0]['aeopSKUProperty']), 2)
        self.assertNotEqual(out[0]['aeopSKUProperty'][0]['propertyValueId'],
                            out[1]['aeopSKUProperty'][0]['propertyValueId'])
        self.assertEqual(out[0]['aeopSKUProperty'][1]['plug_parent_property_id'], 400081003)

    def test_duplicate_variant_properties_never_silently_delete_distinct_skus(self):
        from aeclone.services.api_save import merge_skus
        source=dict(aeopAeProductSKUs=[dict(skuCode=f"SKU-{i}",aeopSKUProperty=[
            dict(skuPropertyId=14,propertyValueId=123)]) for i in range(8)])
        snapshot=deepcopy(source)
        with self.assertRaisesRegex(ValueError,"规格属性组合重复"):
            merge_skus(source,None)
        with self.assertRaisesRegex(ValueError,"规格属性组合重复"):
            build_ai_overrides(source,{},self.cfg())
        self.assertEqual(source,snapshot)

if __name__=='__main__': unittest.main(verbosity=2)
