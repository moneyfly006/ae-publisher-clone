"""Offline regression checks: no application imports, network or customer config."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract(path, name, method=False, namespace=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = ast.walk(tree) if method else tree.body
    node = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


class SafetyTests(unittest.TestCase):
    def test_edited_records_never_expire_or_evict(self):
        prune = extract("src/aeclone/services/edited.py", "_prune")
        records = {str(i): {"ts": 1, "status": "saved"} for i in range(5002)}
        self.assertEqual(prune(records), records)
        self.assertIsNot(prune(records), records)

    def test_preview_never_touches_services(self):
        """预演可以算计划，但绝不能调 AI / 下载 / 上传 / 保存。

        第 24 轮后预演会做「标准化 + 价格 + 图片计划」，所以这里给一个最小的
        cfg 桩，并给所有会花钱/联网的方法装上计数器，断言一次都没被调用。
        """
        class Result:
            def __init__(self, product_id="", ok=True, **kwargs):
                self.product_id, self.ok, self.steps = product_id, ok, []

        class Cfg:
            def __getattr__(self, name):
                # 任何配置项都返回一个温和的默认值
                return "" if not name.endswith("_enable") else True

        calls = {"n": 0}

        class FakeSelf:
            cfg = Cfg()

            def _normalize(self, p):
                return p

            def _price(self, p):
                return p

            def _apply_image_jobs(self, p, res, dry_run=False):
                return p

            def _apply_sku_image_replace(self, p, res, dry_run=False):
                return p

            def _img_service(self):
                calls["n"] += 1

            def _fetch(self, *a, **k):
                calls["n"] += 1

            def upload_image_from_url(self, *a, **k):
                calls["n"] += 1

        run = extract("src/aeclone/services/publisher.py", "run", method=True,
                      namespace={"__package__": "aeclone.services", "PublishResult": Result,
                                 "Step": lambda *a, **k: type("S", (), {
                                     "name": a[0] if a else "",
                                     "ok": a[1] if len(a) > 1 else True,
                                     "detail": a[2] if len(a) > 2 else "",
                                 })()})
        result = run(FakeSelf(), {"id": "offline-only", "images": []}, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 0, "预演不该调用任何服务")
        last = result.steps[-1]
        self.assertIn("未写入", getattr(last, "detail", ""))


if __name__ == "__main__":
    unittest.main()
