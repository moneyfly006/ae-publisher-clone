"""Selected image switches execute once and never need an unrelated master toggle."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher, PublishResult

class ImageSwitchTests(unittest.TestCase):
    def publisher(self, **values):
        data=dict(white_bg_enable=False,watermark_enable=False,
            img_detail_white_bg=False,img_detail_remove_wm=False,white_background_sku_pic=False,
            img_sku_white_bg=False,img_sku_remove_wm=False,img_sku_edit=False,
            translate_main_pic_enable=False,translate_detail_pic_enable=False,translate_sku_pic=False,
            background_gen_rules="[]",image_ai_jobs="[]",image_edit_plans="[]")
        data.update(values)
        cfg=AppConfig(data)
        pub=Publisher.__new__(Publisher);pub.cfg=cfg
        pub._pmap=lambda fn, rows: [fn(row) for row in rows]
        pub._run_ops=Mock(return_value="edited")
        return pub

    def test_new_engine_owns_simple_operations_legacy_path_does_not_repeat(self):
        pub=self.publisher(translate_main_pic_enable=True,translate_detail_pic_enable=True,
            translate_sku_pic=True,white_background_sku_pic=True,img_sku_white_bg=True)
        p={"images":["main"],"detail_images":["detail"],"skus":[{"sku":"a","image":"sku"}]}
        pub._ai_images(p,PublishResult("qa",True),handled_simple=True)
        pub._run_ops.assert_not_called()

    def test_sku_custom_edit_does_not_require_legacy_white_background_toggle(self):
        pub=self.publisher(img_sku_edit=True,img_sku_edit_prompt="Keep the product, change background")
        p={"images":[],"skus":[{"sku":"a","image":"sku"}]}
        pub._ai_images(p,PublishResult("qa",True),handled_simple=True)
        self.assertEqual(p["skus"][0]["processed_image"],"edited")
        self.assertEqual(pub._run_ops.call_count,1)
        self.assertEqual(pub._run_ops.call_args.kwargs['edit'],True)
        self.assertFalse(pub._run_ops.call_args.kwargs['white_bg'])

    def test_translation_detection_skips_textless_images_without_edit_call(self):
        pub=self.publisher(translate_image_understanding=True)
        pub.image_pipeline=Mock();pub.image_pipeline.has_text.return_value=False
        service=Mock();pub._img_service=lambda:service
        self.assertEqual(pub._apply_simple_ops("source",["translate"]),"source")
        service.edit.assert_not_called()
        pub.image_pipeline.has_text.assert_called_once_with("source")

if __name__=='__main__':unittest.main(verbosity=2)
