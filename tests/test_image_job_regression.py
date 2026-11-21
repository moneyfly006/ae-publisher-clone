"""Exercise real image-job control flow with fake services and temporary files."""
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import unittest
from test_audit_safety import extract


class ImageJobTests(unittest.TestCase):
    def run_jobs(self, count, cache_hit):
        module = ModuleType("offline_jobs.image_jobs")
        job = dict(group="main", index=1, refs="", prompt="x" * 140,
                   mode="generate", count=count, insert="first")
        module.parse_rules = lambda raw: []
        module.parse_jobs = lambda raw: [job]
        module.refs_indices = lambda *a, **k: []
        module.migrate = lambda cfg: ([], [])
        module.rules_from_switches = lambda cfg: []
        module.merged_jobs = lambda cfg: ([], [job])
        module.GROUP_LABELS = {"main": "主图"}
        index_module = ModuleType("offline_jobs.image_index")
        index_module.parse_indices = extract("src/aeclone/services/image_index.py", "parse_indices")
        # _apply_image_jobs 依赖模块级 with_scale_rule（给含人物提示词补比例约束）；
        # 这里按原样 stub 掉，测试只关心缓存/多输出控制流，不校验提示词文本。
        run = extract("src/aeclone/services/publisher.py", "_apply_image_jobs", method=True,
                      namespace={"__package__": "offline_jobs", "add_step": lambda *a: None,
                                 "with_scale_rule": lambda prompt: prompt})
        calls, reads, writes = [], [], []
        def generate(*args, **kwargs):
            calls.append(1)
            return "result-" + str(len(calls))
        def get(*args):
            reads.append(args)
            return cache_hit
        fake = SimpleNamespace(
            cfg=SimpleNamespace(img_fine_main_enable=True), _img_service=lambda: SimpleNamespace(edit_many=generate),
            _aborted=lambda: False,
            _group_urls=lambda p, group: list(p["images"]),
            _set_group_list=lambda p, group, urls: p.update(images=urls),
            _set_group_url=lambda p, group, idx, url: p["images"].__setitem__(idx, url),
            _cache_get=get, _cache_put=lambda *args: writes.append(args),
            _prompt_model=lambda: "model", _fetch=lambda url, workdir: workdir / "fake")
        p = {"images": ["source"]}
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(sys.modules, {module.__name__: module, index_module.__name__: index_module}), patch("tempfile.mkdtemp", return_value=root):
                run(fake, p, None)
        return p, calls, reads, writes

    def test_multiple_outputs_never_collapse_to_one_cached_url(self):
        product, calls, reads, writes = self.run_jobs(3, "stale-single-result")
        self.assertEqual(product["images"], ["result-1", "result-2", "result-3", "source"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(reads, [])
        self.assertEqual(writes, [])

    def test_single_output_reuses_cache_without_paid_call(self):
        product, calls, reads, writes = self.run_jobs(1, "cached")
        self.assertEqual(product["images"], ["cached", "source"])
        self.assertEqual(calls, [])
        self.assertIn("x" * 140, reads[0][1])

    def test_single_output_cache_miss_generates_and_caches(self):
        product, calls, reads, writes = self.run_jobs(1, None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(writes[0][2], "result-1")


if __name__ == "__main__":
    unittest.main()
