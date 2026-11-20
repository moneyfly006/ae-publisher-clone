"""AST-isolated UI safety tests: never import the app or customer configuration."""
import ast
import datetime
from pathlib import Path
import threading
import time
import tempfile
import os
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "src/aeclone/ui/app.py"


def method(name, **namespace):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    scope = {"Path": Path, "threading": threading, "datetime": datetime,
             "time": time, "tempfile": tempfile, "__package__": "aeclone.ui",
             "ACCENT_2": "green", "DANGER_C": "red", **namespace}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), scope)
    return scope[name]


def forbidden(*args, **kwargs):
    raise AssertionError("external service or customer state accessed")


class Control:
    def __init__(self, controls=None, **kwargs):
        self.controls = controls or []
        self.__dict__.update(kwargs)


class WorkbenchSafetyTests(unittest.TestCase):
    def selection(self, count):
        paths = [Path("one.png"), Path("two.png"), Path("three.png")]
        app = NS(picked=paths, wb_sel_paths={str(p) for p in paths[:count]},
                 _safe_update=Mock(), toast=Mock(), _log=Mock())
        app._wb_selected_paths = MethodType(method("_wb_selected_paths"), app)
        app._wb_pick_one = MethodType(method("_wb_pick_one"), app)
        return app

    def test_zero_one_many_selection_preserves_order_and_ignores_stale_paths(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                app = self.selection(count)
                app.wb_sel_paths.add("stale.png")
                self.assertEqual(app._wb_selected_paths(), app.picked[:count])
                for path in app.picked:
                    app._wb_pick_one(str(path), False)
                self.assertEqual(app._wb_selected_paths(), [])

    def test_redraw_never_selects_unchecked_images(self):
        ft = NS(Row=Control, Column=Control, Checkbox=Control, Image=Control,
                ImageFit=NS(COVER="cover"), CrossAxisAlignment=NS(CENTER="center"))
        render = method("_wb_render_wall", ft=ft)
        for count in (0, 1, 3):
            with self.subTest(count=count):
                app = self.selection(count)
                original = set(app.wb_sel_paths)
                render(app)
                render(app)
                self.assertEqual(app.wb_sel_paths, original)
                self.assertEqual([t.controls[1].value for t in app.wb_wall.controls],
                                 [i < count for i in range(3)])
                app.wb_wall.controls[0].controls[1].on_change(NS(control=NS(value=False)))
                render(app)
                self.assertNotIn(str(app.picked[0]), app.wb_sel_paths)

    def test_empty_selection_stops_before_image_services(self):
        app = self.selection(0)
        app.wb_busy = NS(visible=True)
        process = method("_wb_process", ImageService=forbidden, ApiClient=forbidden,
                         BatchRunner=forbidden)
        process(app, "white")
        self.assertFalse(app.wb_busy.visible)
        app.toast.assert_called_once()

    def test_image_processing_uses_only_selected_snapshot(self):
        for count in (1, 3):
            with self.subTest(count=count):
                app = self.selection(count)
                expected = app.picked[:count]
                app.cfg = NS(api_base="fake", api_key="fake", timeout=1, concurrency=1,
                             retry_max=1, retry_delay=1, edit_prompt="test")
                app.wb_results = NS(controls=[])
                app.wb_busy = NS(visible=True)
                app.wb_status = NS(value="")
                service = NS(white_background=Mock(return_value="result"))
                def run(targets, job, done):
                    self.assertEqual(targets, expected)
                    app.wb_sel_paths.clear()
                    return [NS(ok=True, value=job(i, p)) for i, p in enumerate(targets)]
                method("_wb_process", ImageService=lambda *a: service,
                       ApiClient=lambda *a: None, BatchRunner=lambda *a, **kw: NS(run=run),
                       history=NS(record=Mock()))(app, "white")
                self.assertEqual([c.args[0] for c in service.white_background.call_args_list], expected)
                self.assertFalse(app.wb_busy.visible)

    def test_video_never_falls_back_to_all_images(self):
        paths = ["one.png", "two.png", "three.png"]
        class FakePath:
            def __init__(self, value):
                self.value = value
            def exists(self):
                return self.value != "missing.png"
            def mkdir(self, **kwargs):
                pass
            def __truediv__(self, name):
                return self
        for count in (0, 1, 3):
            with self.subTest(count=count):
                app = self.selection(count)
                app.cfg = NS(video_output_dir="fake-output", video_slot_seconds=2.5,
                             video_transition_seconds=.5, video_size="1080x1080",
                             video_cover=True, video_fade_edges=True)
                app._wb_selected_paths = lambda: paths[:count] + ["missing.png"]
                generate = Mock()
                probe = Mock(return_value={"available": True})
                method("_wb_make_video", Path=FakePath, video_probe=probe,
                       generate_carousel_video=generate)(app)
                if count:
                    self.assertEqual(generate.call_args.args[0], paths[:count])
                else:
                    probe.assert_not_called()
                    generate.assert_not_called()

    def test_queue_preview_ignores_auto_save_and_external_connections(self):
        for auto_save in (False, True):
            for size in (0, 1, 3):
                for success in (False, True):
                    with self.subTest(auto_save=auto_save, size=size, success=success):
                        items = [NS(product_id=str(i), title="Product") for i in range(size)]
                        publisher = Mock(return_value=NS(run=Mock(return_value=NS(ok=success, error="bad"))))
                        history = NS(record=Mock())
                        app = NS(cfg=NS(auto_save_products=auto_save, concurrency=3),
                                 products=[], _queue_products={},
                                 q=NS(pending=lambda: items, mark=Mock()),
                                 _cookies=forbidden, _ensure_browser_session=forbidden,
                                 _refresh_queue=Mock(), _log=Mock(), toast=Mock())
                        with patch.dict(os.environ, {"AURORA_NO_SAVE": "1"}):
                            method("_process_queue", Publisher=publisher, history=history)(app)
                        self.assertFalse(app._running)
                        self.assertEqual(publisher.call_count, size)
                        for call in publisher.call_args_list:
                            self.assertEqual(call.args, (app.cfg,))
                            self.assertEqual(call.kwargs, {"dxm": None, "scheduler": None})
                        for call in publisher.return_value.run.call_args_list:
                            self.assertEqual(call.kwargs, {"dry_run": True, "manage_progress": False})
                        if size:
                            history.record.assert_called_once_with("queue:preview", size if success else 0,
                                                                   0 if success else size)
                            terminal = [c for c in app.q.mark.call_args_list if c.args[1] != "running"]
                            self.assertEqual(len(terminal), size)
                            for call in terminal:
                                self.assertEqual(call.args[1], "done" if success else "failed")
                                if success:
                                    self.assertIn("未标记已编辑", call.args[2])
                            self.assertIn("未保存商品", app.toast.call_args.args[0])
                        else:
                            history.record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
