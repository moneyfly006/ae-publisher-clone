from pathlib import Path
from types import SimpleNamespace as NS
import sys
import json
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aeclone.services.customs_metadata import refreshed_customs, validate_customs_repair
from aeclone.services.api_save import build_ai_overrides

class CustomsTests(unittest.TestCase):
    def product(self,version='old',code='123'):
        return dict(categoryId='100',errMsg='isv.7CHK_GLOBAL_PROPERTY_VERSION_ERROR',
                    hsCode=json.dumps(dict(hsCode=code,version=version)))

    def test_current_template_with_same_stale_metadata_is_not_a_fix(self):
        with self.assertRaisesRegex(ValueError,'同一份过期'):
            refreshed_customs(self.product(),self.product())

    def test_other_category_and_other_classification_are_refused(self):
        template=self.product('new');template['categoryId']='200'
        with self.assertRaisesRegex(ValueError,'同类目'):refreshed_customs(self.product(),template)
        with self.assertRaisesRegex(ValueError,'海关编码与商品不同'):
            refreshed_customs(self.product(),self.product('new','456'))

    def test_refreshed_metadata_is_actually_included_in_save_overrides(self):
        value=refreshed_customs(self.product(),self.product('new'))
        output=build_ai_overrides({'aeopAeProductSKUs':[]},{'hs_code_override':value},NS(stock_quantity=''))
        self.assertEqual(json.loads(output['hsCode'])['version'],'new')

    def test_normal_product_does_not_replace_customs_data(self):
        product=self.product();product['errMsg']=''
        self.assertIsNone(refreshed_customs(product,self.product('new')))

    def selected_product(self):
        product = self.product()
        product.update(id='p',shopId='s')
        metadata = json.loads(product['hsCode'])
        metadata['pvList'] = [{'propertyName':'Material','valueName':'Steel'}]
        product['hsCode'] = json.dumps(metadata)
        return product

    def response(self, product, **kw):
        new = json.loads(product['hsCode']);new['version']='V2'
        response = dict(success=True, completed=True, hscode=json.dumps(new), possible_pv_list=[])
        response.update(kw)
        return response

    def test_platform_current_metadata_is_used_even_when_template_is_stale(self):
        product=self.selected_product(); calls=[]
        def query(pid, shop, selected):
            calls.append((pid,shop,selected))
            return self.response(product)
        output=refreshed_customs(product,product,query=query)
        self.assertEqual(json.loads(output)['version'],'V2')
        self.assertEqual(calls,[('p','s',[{'property_name':'Material','value_list':[{'value_name':'Steel'}]}])])
        self.assertEqual(json.loads(product['hsCode'])['version'],'old')

    def test_actual_platform_classification_change_is_not_accepted_as_version_only(self):
        product=self.selected_product()
        new=json.loads(self.response(product)['hscode']);new['hsCode']='456'
        with self.assertRaisesRegex(ValueError,'归类变化'):
            refreshed_customs(product,query=lambda *a:self.response(product,hscode=json.dumps(new)))

    def test_new_visible_property_is_not_guessed_even_if_platform_says_completed(self):
        product=self.selected_product()
        prop=dict(property_name='Type',display_name='种类',ui_type='SINGLE_SELECT',value_list=[{'value_name':'Other'}])
        with self.assertRaisesRegex(ValueError,'需要确认'):
            refreshed_customs(product,query=lambda *a:self.response(product,possible_pv_list=[prop]))

    def test_changed_existing_selection_is_rejected(self):
        product=self.selected_product()
        new=json.loads(self.response(product)['hscode']);new['pvList'][0]['valueName']='Plastic'
        with self.assertRaisesRegex(ValueError,'原始选择不一致'):
            refreshed_customs(product,query=lambda *a:self.response(product,hscode=json.dumps(new)))

    def test_stalled_query_stops_without_repeating_calls(self):
        product=self.selected_product(); calls=[]
        prop=dict(property_name='Material',ui_type='SINGLE_SELECT',value_list=[{'value_name':'Steel'}])
        def query(*args):
            calls.append(args)
            return self.response(product,completed=False,possible_pv_list=[prop])
        with self.assertRaisesRegex(ValueError,'停止重复查询'):
            refreshed_customs(product,query=query)
        self.assertEqual(len(calls),1)

    def test_save_refetch_rejects_concurrent_customs_changes(self):
        product=self.selected_product()
        processed={'raw':product,'hs_code_override':refreshed_customs(product,query=lambda *a:self.response(product))}
        validate_customs_repair(product,processed)
        changed=dict(product,hsCode=json.dumps({'hsCode':'456'}))
        with self.assertRaisesRegex(ValueError,'已被更改'):validate_customs_repair(changed,processed)

    def test_query_transport_is_read_only_and_uses_json_selections(self):
        from aeclone.integrations.dianxiaomi import DianxiaomiClient
        from unittest.mock import Mock
        client=DianxiaomiClient({})
        client._request=Mock(return_value=Mock(status_code=200,json=lambda:{'code':0,'data':{'completed':True}}))
        self.assertEqual(client.query_customs_metadata('p','s',[]),{'completed':True})
        args,kwargs=client._request.call_args
        self.assertEqual(args,('post',client.base+'/api/smtCommProduct/getGlobalHsCodeById.json'))
        self.assertEqual(kwargs['params'],{'shopId':'s','id':'p'})
        self.assertEqual(kwargs['json'],{'selected_pv_list':[]})

if __name__=='__main__':unittest.main(verbosity=2)
