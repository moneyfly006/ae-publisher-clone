"""任务与日志「日志明细」：行高必须随内容自动变化，不能用固定行高。

回归背景（用户反馈）：日志明细原来用 DataTable + 固定行高，
短日志也被撑出很大的上下空隙；需要像右上角「实时日志」一样紧凑、按内容自动换行。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402


def _texts(controls):
    return "\n".join(str(getattr(c, "value", "") or "") for c in walk(controls)
                     if isinstance(c, ft.Text))


class LogsCompactLayoutTests(unittest.TestCase):
    def test_rows_auto_height_and_filters(self):
        with sandbox():
            app = build_app(eager_editors=False)
            app._switch(10)

            app._log("短日志：开始处理", "INFO")
            app._log("长日志：" + "这是一段很长很长的日志内容，用来验证行高会随内容自动变高，"
                     "而不是固定行高。" * 3, "ERROR")
            app._log_refresh_ts = 0
            app._refresh_log_table()

            controls = list(walk(app.content.controls))
            self.assertFalse(
                any(isinstance(c, ft.DataTable) for c in controls),
                "日志页不应再使用固定行高的 DataTable")

            rows = [c for c in app.log_scroll.controls if isinstance(c, ft.Container)]
            self.assertGreaterEqual(len(rows), 2, "日志明细应为逐行容器")
            self.assertTrue(all(getattr(r, "height", None) is None for r in rows),
                            "日志行不应写死高度，应由内容决定")
            self.assertLessEqual(int(getattr(app.log_scroll, "spacing", 99) or 0), 3,
                                 "行间距应紧凑")
            content_texts = [t for t in walk(rows)
                             if isinstance(t, ft.Text) and bool(getattr(t, "expand", False))]
            self.assertTrue(content_texts, "内容列应自适应宽度并自动换行")
            self.assertIn("用来验证行高会随内容自动变高", _texts(rows), "长日志应完整显示")
            self.assertTrue(any("INFO" in _texts([r]) for r in rows))
            self.assertTrue(any("ERROR" in _texts([r]) for r in rows))

            # 级别筛选仍然有效
            app.log_level.value = "ERROR"
            app._log_refresh_ts = 0
            app._refresh_log_table()
            rows_err = [c for c in app.log_scroll.controls if isinstance(c, ft.Container)]
            expect = sum(1 for _t, lv, _m in app.log_rows if str(lv).upper() == "ERROR")
            self.assertEqual(len(rows_err), expect, "级别筛选应只显示对应级别")

            # 无匹配时给空状态，而不是一片空白
            app.log_level.value = "全部"
            app.log_filter.value = "不可能匹配的关键词-zzz"
            app._log_refresh_ts = 0
            app._refresh_log_table()
            self.assertIn("没有符合筛选条件的日志", _texts(app.log_scroll.controls))

            # 新日志写入后自动刷新（不再依赖已删除的 log_table 标记）
            app.log_filter.value = ""
            app._log_refresh_ts = 0
            app._refresh_log_table()
            with patch.object(app, "_refresh_log_table") as refresh:
                app._log("自动刷新验证", "INFO")
                self.assertTrue(refresh.called, "_log 应自动触发明细刷新")


if __name__ == "__main__":
    unittest.main()
