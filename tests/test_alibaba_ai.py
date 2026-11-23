import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.alibaba_ai import (AI_TOUCHPOINTS, map_attribute_suggestion,
                                         build_attribute_prompt, NO_FABRICATION)


class AlibabaAITests(unittest.TestCase):
    def test_touchpoint_map_covers_required_fields(self):
        for k in ("categoryId", "subject", "keywords", "textDesc", "attributes"):
            self.assertIn(k, AI_TOUCHPOINTS)
        self.assertIn("Certification", NO_FABRICATION)

    def test_map_attribute_suggestion_only_enum_values(self):
        defs = [{"nameEn": "Color", "values": '[{"id":"1","name_en":"White"},{"id":"2","name_en":"Blue"}]'},
                {"nameEn": "Material", "values": '[{"id":"9","name_en":"Metal"}]'}]
        got = map_attribute_suggestion({"Color": "blue", "Material": "Plastic"}, defs)
        self.assertEqual(got["Color"], {"value_id": "2", "value_name": "Blue"})
        self.assertNotIn("Material", got)

    def test_attribute_prompt_lists_names(self):
        p = build_attribute_prompt([{"nameEn": "Voltage"}, {"nameEn": "Power"}])
        self.assertIn("Voltage", p); self.assertIn("Power", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
