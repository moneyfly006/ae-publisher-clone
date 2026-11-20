from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher

class PromptCacheTests(unittest.TestCase):
    def publisher(self):
        pub=Publisher.__new__(Publisher)
        pub.cfg=AppConfig(dict(translate_image_understanding=False))
        cache={}
        pub._cache_get=lambda url,action,model:cache.get((url,action,model))
        pub._cache_put=lambda url,action,value,model:cache.update({(url,action,model):value})
        pub._img_model=lambda:"test"
        pub._fetch=lambda url:url
        return pub

    def test_translation_prompts_differing_after_40_characters_do_not_reuse_outputs(self):
        pub=self.publisher()
        service=NS(edit=Mock(side_effect=["English output","French output"]))
        pub._img_service=lambda:service
        pub.cfg.translate_prompt="x"*100+"Translate to English"
        self.assertEqual(pub._apply_simple_ops("source",["translate"]),"English output")
        pub.cfg.translate_prompt="x"*100+"Translate to French"
        self.assertEqual(pub._apply_simple_ops("source",["translate"]),"French output")
        self.assertEqual(pub._apply_simple_ops("source",["translate"]),"French output")
        self.assertEqual(service.edit.call_count,2)

    def test_sku_edit_prompt_changes_after_60_characters_regenerate(self):
        pub=self.publisher()
        service=NS(edit=Mock(side_effect=["Red background","Blue background"]))
        pub._img_svc=service
        first="x"*100+"Red background";second="x"*100+"Blue background"
        self.assertEqual(pub._run_ops("source",False,False,True,first),"Red background")
        self.assertEqual(pub._run_ops("source",False,False,True,second),"Blue background")
        self.assertEqual(pub._run_ops("source",False,False,True,first),"Red background")
        self.assertEqual(service.edit.call_count,2)

if __name__=='__main__': unittest.main(verbosity=2)
