from pathlib import Path
from types import SimpleNamespace as NS
import datetime
import tempfile
import unittest
from unittest.mock import Mock
from test_run_end_to_end import _run, _cfg, _product, _mk

class ScheduleTests(unittest.TestCase):
    def test_schedule_saves_to_waiting_without_immediate_publish(self):
        future=(datetime.datetime.now()+datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        for action in ("publish","save_only","save_to_waiting"):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as root:
                image=_mk(Path(root),"source.png")
                scheduler=NS(add=Mock())
                result,captured,pub=_run(_cfg(schedule_publish=True,schedule_time=future,
                                             publish_action=action),_product(1),image,image,scheduler=scheduler)
                self.assertTrue(result.ok,result.error)
                self.assertEqual(captured['action'],'save_to_waiting')
                scheduler.add.assert_called_once()
                self.assertEqual(scheduler.add.call_args.args[0],"P1001")

    def test_save_only_reports_actual_destination(self):
        with tempfile.TemporaryDirectory() as root:
            image=_mk(Path(root),"source.png")
            result,captured,pub=_run(_cfg(publish_action="save_only"),_product(1),image,image)
            self.assertEqual(captured['action'],'save_only')
            detail=next(step.detail for step in result.steps if step.name=="保存结果")
            self.assertIn("采集箱",detail)

if __name__=='__main__':unittest.main(verbosity=2)
