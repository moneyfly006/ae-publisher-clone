"""An existing estimate response must not trigger another billed request."""
from types import SimpleNamespace as NS
import unittest
from test_audit_safety import extract


class WeightReuseTests(unittest.TestCase):
    def setUp(self):
        self.parse = extract("src/aeclone/services/text_pipeline.py", "estimate_weight_json", method=True)

    def test_existing_response_needs_no_service(self):
        result = NS(ok=True, value='{"weight_kg": 2, "length_cm": 10, "width_cm": 20, "height_cm": 30}')
        self.assertEqual(self.parse(object(), "unused", result=result),
                         {"weight_kg": 2, "length_cm": 10, "width_cm": 20, "height_cm": 30})

    def test_existing_failure_never_retries(self):
        self.assertEqual(self.parse(object(), "unused", result=NS(ok=False)), {})

    def test_legacy_call_requests_once(self):
        calls = []
        def estimate(*args):
            calls.append(args)
            return NS(ok=True, value="2 kg, 10 cm x 20 cm x 30 cm")
        result = self.parse(NS(estimate_weight=estimate), "image", "product")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["weight_kg"], 2)


if __name__ == "__main__":
    unittest.main()
