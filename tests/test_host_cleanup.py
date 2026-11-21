#!/usr/bin/env python3
"""图床清理测试（本地假服务，不联网）。

图床也会满，必须能删。删除接口要的是上传时返回的 delToken（不是文件名），
所以上传时要把 token 记到本地台账，之后按时间清理。
"""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

import aeclone.services.hosting as H  # noqa: E402

DELETED: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path.startswith("/rest/upload"):
            body = raw.decode("utf-8", "replace")
            has_files = 'name="files"' in body
            if not has_files or not (self.headers.get("Authorization") or ""):
                self._json(400, {"code": 400, "msg": "bad"})
                return
            self._json(200, {"code": 200, "data": [{
                "key": "a.png", "url": "https://h.example.com/rest/a.png",
                "filename": "a.png", "delToken": "tok-123", "size": 10}]})
            return
        if self.path.startswith("/rest/delImage/"):
            tok = self.path.rsplit("/", 1)[-1]
            if not (self.headers.get("Authorization") or ""):
                self._json(401, {"code": 401, "msg": "no auth"})
                return
            DELETED.append(tok)
            self._json(200, {"code": 200, "msg": "ok", "data": "Delete success"})
            return
        if self.path.startswith("/rest/list"):
            self._json(200, {"code": 200, "data": {"list": []}})
            return
        self._json(404, {"error": "not found"})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def _img() -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "x.png"
    Image.new("RGB", (1200, 1200), (30, 190, 60)).save(p)
    return p


def _isolate_ledger(tmp: Path):
    H._ledger_path = lambda: tmp / "host_ledger.json"


def t1_upload_records_token():
    print("\n[测试1] 上传后把 delToken 记进台账")
    srv, base = _serve()
    try:
        with tempfile.TemporaryDirectory() as td:
            _isolate_ledger(Path(td))
            H._CACHE.clear()
            url = H.upload(_img(), base=base, key="k")
            assert url == "https://h.example.com/rest/a.png", url
            led = H._ledger_load()
            assert led, "台账应为空"
            meta = list(led.values())[0]
            assert meta.get("token") == "tok-123", meta
            assert meta.get("ts", 0) > 0, meta
            print("  ✓ 记录 token=del-123，时间戳已写")
    finally:
        srv.shutdown()


def t2_clean_deletes_old_keeps_new():
    print("\n[测试2] 清理：删旧图、留新图")
    srv, base = _serve()
    try:
        with tempfile.TemporaryDirectory() as td:
            _isolate_ledger(Path(td))
            H._CACHE.clear()
            H.upload(_img(), base=base, key="k")
            led = H._ledger_load()
            led["https://h.example.com/rest/old.png"] = {
                "token": "tok-old", "ts": int(time.time()) - 10 * 86400, "name": "old.png"}
            H._ledger_save(led)
            DELETED.clear()
            r = H.clean_old(7, base=base, key="k")
            assert r["deleted"] == 1, r
            assert r["kept"] == 1, r
            assert "tok-old" in DELETED, DELETED
            assert "tok-123" not in DELETED, DELETED
            print("  ✓", r["message"])
    finally:
        srv.shutdown()


def t3_clean_days_zero_removes_all():
    print("\n[测试3] 保留天数=0 → 全删")
    srv, base = _serve()
    try:
        with tempfile.TemporaryDirectory() as td:
            _isolate_ledger(Path(td))
            H._CACHE.clear()
            H.upload(_img(), base=base, key="k")
            DELETED.clear()
            r = H.clean_old(0, base=base, key="k")
            assert r["deleted"] == 1, r
            assert H._ledger_load() == {}, "台账应清空"
            print("  ✓", r["message"])
    finally:
        srv.shutdown()


def t4_delete_failure_counts():
    print("\n[测试4] 删除接口失败 → 计入 failed，台账保留")
    with tempfile.TemporaryDirectory() as td:
        _isolate_ledger(Path(td))
        H._ledger_save({"https://h.example.com/rest/x.png": {
            "token": "tok-x", "ts": int(time.time()) - 30 * 86400}})
        r = H.clean_old(7, base="http://127.0.0.1:9", key="k")   # 连不上
        assert r["failed"] == 1 and r["deleted"] == 0, r
        assert H._ledger_load(), "失败时不应从台账删掉"
        print("  ✓", r["message"])


def t5_entry_without_token_dropped():
    print("\n[测试5] 没有 token 的旧条目被丢弃，不会误删")
    with tempfile.TemporaryDirectory() as td:
        _isolate_ledger(Path(td))
        H._ledger_save({"https://h.example.com/rest/y.png": {"ts": 0}})
        r = H.clean_old(0, base="http://127.0.0.1:9", key="k")
        assert r["deleted"] == 0 and r["failed"] == 0, r
        assert H._ledger_load() == {}
        print("  ✓ 无 token 条目被清理，未调用删除")


if __name__ == "__main__":
    tests = [t1_upload_records_token, t2_clean_deletes_old_keeps_new,
             t3_clean_days_zero_removes_all, t4_delete_failure_counts,
             t5_entry_without_token_dropped]
    ok = fail = 0
    for t in tests:
        try:
            t()
            ok += 1
        except AssertionError as ex:
            fail += 1
            print(f"  ✗ 失败: {ex}")
        except Exception as ex:
            fail += 1
            import traceback
            print(f"  ✗ 异常: {ex}")
            traceback.print_exc()
    print("\n" + "=" * 56)
    print(f"结果: {ok} 通过, {fail} 失败")
    print("=" * 56)
    sys.exit(0 if fail == 0 else 1)
