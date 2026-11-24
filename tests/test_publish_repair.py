"""User-triggered publication retry repairs data without repeating AI work."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher, PublishResult


def detail():
    return dict(id="p", shopId="s", categoryId="123", hsCode=json.dumps(dict(hsCode="123456",version="old",
        pvList=[dict(propertyName="Material",valueName="Steel")])),
        errMsg="CHK_GLOBAL_PROPERTY_VERSION_ERROR", aeopAeProductSKUs=[
            dict(skuCode="A",id="1:1",skuPrice="10",ipmSkuStock=8,
                 aeopSKUProperty=[dict(skuPropertyId=1,propertyValueId=1)])])


class PublishRepairTests(unittest.TestCase):
    def publisher(self, raw, response):
        pub = Publisher.__new__(Publisher)
        pub.cfg = AppConfig(dict(template_product_id="", auto_detect_category=True))
        pub.dxm = NS(_get=Mock(return_value={"data":{"product":raw}}),
                     query_customs_metadata=Mock(return_value=response),
                     save_product_via_api=Mock(return_value={"code":0,"data":{}}))
        pub._fix_market_images=Mock(return_value=[])
        pub._await_publish_outcome=Mock(return_value=None)
        pub._shrink_detail_images=lambda html, res:html
        pub.text=Mock(side_effect=AssertionError("Retry must not use AI"))
        return pub

    def response(self, raw):
        new=json.loads(raw["hsCode"]);new["version"]="V2"
        return dict(success=True,completed=True,hscode=json.dumps(new),possible_pv_list=[])

    def run_retry(self, pub):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ",{"AURORA_CONFIG_DIR":folder}):
            return pub.publish_only("p",PublishResult(product_id="p",ok=True))

    def test_retry_includes_current_schema_and_keeps_variant_price_and_stock(self):
        raw=detail();pub=self.publisher(raw,self.response(raw))
        result=self.run_retry(pub)
        self.assertTrue(result.ok,result.message)
        pub.dxm.save_product_via_api.assert_called_once()
        payload=pub.dxm.save_product_via_api.call_args.args[0]
        self.assertEqual(json.loads(payload["hsCode"])["version"],"V2")
        row=json.loads(payload["aeopAeProductSKUs"])[0]
        self.assertEqual(row["skuPrice"],"10")
        self.assertEqual(row["ipmSkuStock"],8)
        pub.text.assert_not_called()

    def test_classification_change_never_submits(self):
        raw=detail();response=self.response(raw)
        new=json.loads(response["hscode"]);new["hsCode"]="999999";response["hscode"]=json.dumps(new)
        pub=self.publisher(raw,response);result=self.run_retry(pub)
        self.assertFalse(result.ok)
        self.assertIn("归类变化",result.message)
        pub.dxm.save_product_via_api.assert_not_called()

    def test_unrecoverable_duplicate_specs_merge_and_submit(self):
        from copy import deepcopy
        raw=detail();row=deepcopy(raw["aeopAeProductSKUs"][0]);row["skuCode"]="B"
        raw["aeopAeProductSKUs"].append(row)
        pub=self.publisher(raw,self.response(raw));result=self.run_retry(pub)
        # 新策略：找不到真实规格时自动合并重复变种（保留首条、库存累加）后提交
        self.assertTrue(result.ok, result.message)
        pub.dxm.save_product_via_api.assert_called_once()


if __name__ == "__main__":unittest.main(verbosity=2)
