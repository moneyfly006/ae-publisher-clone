"""UI state regressions: execute selected AST nodes, never import the real App.

All controls, configuration writes and timers are in-memory fakes. No customer
configuration, Flet runtime, browser or network is accessed.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src/aeclone/ui/app.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
APP = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "App")


def method(name):
    return next(node for node in APP.body if isinstance(node, ast.FunctionDef) and node.name == name)


def execute(nodes, namespace):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)


class Config(SimpleNamespace):
    def set(self, key, value):
        setattr(self, key, value)


class UIStateRegressionTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.ns = {
            "save_config": lambda cfg: self.saved.append(vars(cfg).copy()),
            "ft": SimpleNamespace(dropdown=SimpleNamespace(
                Option=lambda key, text: SimpleNamespace(key=key, text=text))),
        }
        execute([method(name) for name in (
            "_wz_template_picked", "_wz_finish", "_save_api_fields")], self.ns)
        self.cfg = Config(template_product_id="", template_product_title="",
                          template_source="draft", store_name="Store", api_key="old",
                          concurrency=3, timeout=180)
        self.app = SimpleNamespace(
            cfg=self.cfg,
            wz_tpl=SimpleNamespace(value="A", options=[SimpleNamespace(key="A", text="Template A")]),
            wz_store=SimpleNamespace(value="store", options=[]),
            wz_source=SimpleNamespace(value="draft"),
            wz_browser=SimpleNamespace(value="Chrome"),
            wz_dlg=SimpleNamespace(open=True),
            _set_tpl_preview=lambda images: None,
            _log=lambda message: None,
            _safe_update=lambda: None,
            _switch=lambda page: None,
        )
        self.app._save_api_fields = lambda: self.ns["_save_api_fields"](self.app)

    def test_wizard_button_uses_selection_callback(self):
        callbacks = [kw.value for node in ast.walk(method("_maybe_wizard"))
                     if isinstance(node, ast.Call) for kw in node.keywords
                     if kw.arg == "on_pick"]
        self.assertTrue(any(isinstance(callback, ast.Attribute)
                            and callback.attr == "_wz_template_picked" for callback in callbacks))

    def test_picker_b_survives_finish_and_sync_reset_to_a(self):
        # Real picker saves cfg before invoking its callback.
        self.cfg.template_product_id = "B"
        self.cfg.template_product_title = "Full template B title"
        self.cfg.template_source = "online"
        self.ns["_wz_template_picked"](self.app, "B", "Full template B title", [])
        self.assertEqual(self.app.wz_tpl.value, "B")
        self.assertEqual(self.app.wz_source.value, "online")
        # A later store sync can reset the hidden dropdown; explicit choice wins.
        self.app.wz_tpl.value = "A"
        self.app.wz_tpl.options = [SimpleNamespace(key="A", text="Template A")]
        self.ns["_wz_finish"](self.app, None)
        self.assertEqual(self.saved[-1]["template_product_id"], "B")
        self.assertEqual(self.saved[-1]["template_product_title"], "Full template B title")
        self.assertEqual(self.saved[-1]["template_source"], "online")
        self.assertTrue(self.cfg.onboarded)

    def test_wizard_without_explicit_pick_keeps_first_candidate(self):
        self.ns["_wz_finish"](self.app, None)
        self.assertEqual(self.saved[-1]["template_product_id"], "A")
        self.assertEqual(self.saved[-1]["template_product_title"], "Template A")

    def test_api_change_is_saved_before_immediate_page_rebuild(self):
        nodes = method("_p_settings").body
        start = next(i for i, node in enumerate(nodes)
                     if isinstance(node, ast.FunctionDef) and node.name == "_api_autosave")
        timers = []

        class Timer:
            def __init__(self, delay, callback):
                self.callback = callback
                timers.append(self)
            def start(self):
                pass
            def cancel(self):
                pass

        self.app.api_key_field = SimpleNamespace(value="new-key")
        self.app.cfg_concurrency = SimpleNamespace(value="7")
        self.app.cfg_timeout = SimpleNamespace(value="240")
        ns = dict(self.ns, self=self.app, threading=SimpleNamespace(Timer=Timer))
        # Execute the actual handler definition and the actual binding loop.
        execute(nodes[start:start + 2], ns)
        for field in (self.app.api_key_field, self.app.cfg_concurrency, self.app.cfg_timeout):
            field.on_change(SimpleNamespace(control=field))
        # Navigation reconstructs fields using the current cfg immediately.
        self.app.api_key_field = SimpleNamespace(value=self.cfg.api_key)
        self.app.cfg_concurrency = SimpleNamespace(value=str(self.cfg.concurrency))
        self.app.cfg_timeout = SimpleNamespace(value=str(self.cfg.timeout))
        for timer in timers:
            timer.callback()
        self.assertEqual(self.cfg.api_key, "new-key")
        self.assertEqual(self.cfg.concurrency, 7)
        self.assertEqual(self.cfg.timeout, 240)
        self.assertEqual(self.saved[-1]["api_key"], "new-key")

    def test_api_numeric_validation_is_preserved(self):
        self.app.api_key_field = SimpleNamespace(value="  trimmed  ")
        self.app.cfg_concurrency = SimpleNamespace(value="99")
        self.app.cfg_timeout = SimpleNamespace(value="1")
        self.app._save_api_fields()
        self.assertEqual((self.cfg.api_key, self.cfg.concurrency, self.cfg.timeout), ("trimmed", 8, 30))
        self.app.cfg_concurrency.value = "invalid"
        self.app.cfg_timeout.value = "invalid"
        self.app._save_api_fields()
        self.assertEqual((self.cfg.concurrency, self.cfg.timeout), (8, 30))


if __name__ == "__main__":
    unittest.main()
