"""Conflict diagnostics use active options and actual configuration keys."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from aeclone.config import AppConfig
from aeclone.services.conflicts import check

class ConflictTests(unittest.TestCase):
    def config(self, **values):
        base=dict(enable_ai_title=False, enable_ai_detail=False, enable_ai_sku=False,
                  enable_ai_weight=False, eu_qualification="否", review_block_on_fail=False,
                  custom_detail_text_enable=False, overlay_watermark_enable=False)
        base.update(values)
        return AppConfig(base)

    def test_removing_watermark_needs_no_overlay_reference(self):
        items=check(self.config(watermark_enable=True, watermark=""))
        self.assertFalse(any("水印文件" in c.title for c in items))

    def test_overlay_requires_content_but_accepts_text(self):
        self.assertTrue(any(c.level=="error" and "水印" in c.title for c in
                            check(self.config(overlay_watermark_enable=True,watermark=""))))
        self.assertFalse(any("水印" in c.title for c in
                             check(self.config(overlay_watermark_enable=True,watermark="@mybrand"))))

    def test_replacing_detail_then_adding_custom_content_is_valid(self):
        items=check(self.config(custom_detail_text_enable=True,delete_detail_text=True,detail_text_info="New details"))
        self.assertFalse(any("同时开启" in c.title for c in items))

    def test_explicit_video_source_overrides_legacy_flags(self):
        for source in ("collected","template","generated","none"):
            items=check(self.config(video_source=source,use_template_video=True,generate_main_video=True))
            self.assertFalse(any("视频同时" in c.title for c in items))

    def test_fixed_price_ignores_unused_multiplier_and_commission(self):
        items=check(self.config(write_price_to_product=True,price_mode="fixed",fixed_price="10",
                                price_multiplier="-1",platform_commission="100"))
        self.assertFalse(any("倍率" in c.title or "佣金" in c.title for c in items))

    def test_eu_qualification_reads_the_fields_the_ui_saves(self):
        values=dict(eu_qualification="是",eu_responsible_name="Name",eu_responsible_address="Address",
                    eu_responsible_email="qa@example.invalid",eu_responsible_phone="123")
        self.assertFalse(any(c.level=="error" for c in check(self.config(**values))))
        values['eu_responsible_email']=""
        self.assertTrue(any("eu_responsible_email" in c.keys for c in check(self.config(**values))))

    def test_unsupported_combo_is_explicitly_blocked_before_running(self):
        from aeclone.services.preflight import blocking
        self.assertTrue(any("套装" in error for error in blocking(self.config(combo_enable=True))))

if __name__=='__main__': unittest.main(verbosity=2)
