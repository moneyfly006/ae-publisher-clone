#!/usr/bin/env python3
"""图床上传测试（不联网，用本地假服务模拟两种协议）。

为什么需要图床：店小秘编辑器只认「网络图片 URL」，
而 AI 返回的是约 1 天过期的临时地址，所以需要一个稳定地址中转。

覆盖：
1. Cloudflare-ImgBed 协议（POST /rest/upload，字段 files，Header Authorization）
2. 老自建协议（POST /upload，字段 file）
3. 自动识别：前者不适用时退回后者
4. 同内容重复上传命中缓存（不重复上传）
5. 两种都失败 → 抛 HostError
6. 配置能覆盖默认地址/密钥
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

import aeclone.services.hosting as H  # noqa: E402

SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    mode = "cf"          # cf | legacy | none
    auth_ok = True

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"),
                     "ctype": self.headers.get("Content-Type") or "",
                     "body": raw[:400]})
        if Handler.mode == "none":
            self._json(500, {"error": "down"})
            return
        if self.path.startswith("/rest/upload"):
            if Handler.mode == "legacy":
                self._json(404, {"error": "not found"})   # 模拟：这台机器没有 CF 图床协议
                return
            body = raw.decode("utf-8", "replace")
            has_files = 'name="files"' in body
            if not has_files or not Handler.auth_ok:
                self._json(400, {"code": 400, "msg": "bad"})
                return
            self._json(200, {"code": 200, "msg": "", "data": [
                {"key": "abc.png", "url": "https://image.moneyfly.club/rest/abc.png"}]})
            return
        if self.path.startswith("/upload"):
            body = raw.decode("utf-8", "replace")
            if 'name="file"' not in body:
                self._json(400, {"error": "no file"})
                return
            self._json(200, {"url": "https://vps.example.com/abc.png"})
            return
        self._json(404, {"error": "not found"})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve(mode="cf"):
    Handler.mode = mode
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def _img() -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "x.png"
    Image.new("RGB", (1200, 1200), (30, 190, 60)).save(p)
    return p


def t1_cf_protocol():
    print("\n[测试1] Cloudflare-ImgBed 协议：字段 files + Authorization")
    srv, base = _serve("cf")
    try:
        SEEN.clear()
        H._CACHE.clear()
        url = H.upload(_img(), base=base, key="test-key")
        assert url == "https://image.moneyfly.club/rest/abc.png", url
        rec = [r for r in SEEN if r["path"].startswith("/rest/upload")][0]
        assert rec["auth"] == "test-key", rec["auth"]
        assert 'name="files"' in rec["body"].decode("utf-8", "replace")
        print("  ✓ 走 /rest/upload，Authorization 正确，字段是 files")
    finally:
        srv.shutdown()


def t2_legacy_protocol():
    print("\n[测试2] 老自建协议：字段 file → 自动退回")
    srv, base = _serve("legacy")
    try:
        SEEN.clear()
        H._CACHE.clear()
        url = H.upload(_img(), base=base, key="")
        assert url == "https://vps.example.com/abc.png", url
        paths = [r["path"] for r in SEEN]
        assert any(p.startswith("/rest/upload") for p in paths), "应先试 CF 协议"
        assert any(p.startswith("/upload") for p in paths), "再退回老协议"
        print("  ✓ 先试 /rest/upload，失败后退回 /upload")
    finally:
        srv.shutdown()


def t3_cache_no_double_upload():
    print("\n[测试3] 同一文件重复上传 → 命中缓存，只传一次")
    srv, base = _serve("cf")
    try:
        SEEN.clear()
        H._CACHE.clear()
        p = _img()
        u1 = H.upload(p, base=base, key="k")
        n1 = len(SEEN)
        u2 = H.upload(p, base=base, key="k")
        assert u1 == u2 and len(SEEN) == n1, (u1, u2, n1, len(SEEN))
        print(f"  ✓ 两次返回同一地址，请求数仍为 {n1}")
    finally:
        srv.shutdown()


def t4_bad_key_raises():
    print("\n[测试4] 两种协议都失败 → 抛 HostError")
    srv, base = _serve("none")
    try:
        H._CACHE.clear()
        try:
            H.upload(_img(), base=base, key="k")
            raise AssertionError("应当抛 HostError")
        except H.HostError as ex:
            assert "上传失败" in str(ex)
            print("  ✓", str(ex)[:60])
    finally:
        srv.shutdown()


def t5_default_from_config():
    print("\n[测试5] 默认地址/密钥来自配置")
    b, k = H._resolve(None, None)
    assert b.startswith("http"), b
    assert k, "应能取到默认密钥"
    print(f"  ✓ 地址 {b} · 密钥长度 {len(k)}")


def t6_explicit_overrides_config():
    print("\n[测试6] 显式传参优先于配置")
    b, k = H._resolve("http://127.0.0.1:9999", "my-key")
    assert b == "http://127.0.0.1:9999" and k == "my-key"
    print("  ✓ 显式参数生效")


if __name__ == "__main__":
    tests = [t1_cf_protocol, t2_legacy_protocol, t3_cache_no_double_upload,
             t4_bad_key_raises, t5_default_from_config, t6_explicit_overrides_config]
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
