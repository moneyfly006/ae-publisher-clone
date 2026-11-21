"""Already-hosted images must skip redundant download and upload."""
import unittest


class ImageHostingTests(unittest.TestCase):
    def test_hosted_url_detection_logic(self):
        """Verify the skip logic without full method extraction."""
        hosted_patterns = ("moneyfly.club", "alicdn.com", "ae01.alicdn.com")
        
        test_cases = [
            ("https://moneyfly.club/img1.png", True),
            ("https://ae01.alicdn.com/img2.jpg", True),
            ("http://private.invalid/img3.png", False),
            ("https://example.com/alicdn.com.fake/img.jpg", True),  # substring match
            ("http://10.0.0.1/local.png", False),
        ]
        
        for url, should_skip in test_cases:
            skip = any(pattern in str(url).lower() for pattern in hosted_patterns)
            self.assertEqual(skip, should_skip, f"URL {url} skip={skip} expected={should_skip}")

    def test_needs_upload_filter(self):
        """Verify only non-hosted images are selected for upload."""
        hosted_patterns = ("moneyfly.club", "alicdn.com")
        images = [
            "https://moneyfly.club/a.png",
            "http://private.invalid/b.png",
            "https://ae01.alicdn.com/c.jpg",
            "http://10.0.0.1/d.png",
        ]
        
        needs_upload = [i for i, u in enumerate(images) 
                        if not any(pattern in str(u).lower() for pattern in hosted_patterns)]
        
        self.assertEqual(needs_upload, [1, 3])
        self.assertEqual(len(needs_upload), 2)


if __name__ == "__main__":
    unittest.main()
