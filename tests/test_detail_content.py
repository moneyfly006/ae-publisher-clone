from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aeclone.config import AppConfig
from aeclone.services.detail_content import compose_detail

class DetailTests(unittest.TestCase):
    def config(self, **values):
        base=dict(custom_detail_text_enable=False,delete_detail_text=False,copy_sku_to_detail=False)
        base.update(values)
        return AppConfig(base)

    def test_image_edit_preserves_original_product_text_without_ai_detail(self):
        product=dict(detail='<p>Capacity 100kg and warranty</p><img src="https://old/image">',
                     detail_images=['https://new/image'])
        result=compose_detail(product,self.config())
        self.assertIn('Capacity 100kg and warranty',result)
        self.assertIn('https://new/image',result)
        self.assertNotIn('https://old/image',result)
        self.assertEqual(result.count('<img'),1)

    def test_custom_text_and_sku_do_not_require_ai_detail(self):
        result=compose_detail(dict(detail='<p>Original</p>',skus=[dict(sku='A&B')]),
            self.config(custom_detail_text_enable=True,detail_text_info='Head',detail_text_info_tail='Tail',
                        detail_text_position='both',copy_sku_to_detail=True))
        for text in ('Head','Original','Tail','SKU: A&amp;B'): self.assertIn(text,result)

    def test_delete_original_text_preserves_images_and_new_custom_content(self):
        result=compose_detail(dict(detail='<p>Old description</p><img src="https://old/image">'),
            self.config(delete_detail_text=True,custom_detail_text_enable=True,detail_text_info='New description'))
        self.assertNotIn('Old description',result)
        self.assertIn('New description',result)
        self.assertIn('https://old/image',result)

    def test_local_images_keep_original_collected_detail(self):
        result=compose_detail(dict(detail='<p>Original collected description</p>',
                                   detail_local_images_html='<img src="https://local/image">'),
            self.config(custom_detail_text_enable=True,detail_local_image_position='head'))
        self.assertIn('Original collected description',result)
        self.assertIn('https://local/image',result)
        self.assertTrue(result.startswith('<img'))

    def test_local_images_both_and_generated_text_are_not_duplicated(self):
        result=compose_detail(dict(detail_html='<p>AI text</p>',detail_local_images_html='<img src="https://local/image">'),
            self.config(custom_detail_text_enable=True,detail_local_image_position='both',delete_detail_text=True))
        self.assertEqual(result.count('AI text'),1)
        self.assertEqual(result.count('https://local/image'),2)

if __name__=='__main__':unittest.main(verbosity=2)
