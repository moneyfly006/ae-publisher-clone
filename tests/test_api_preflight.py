#!/usr/bin/env python3
"""API 预检测试：用本地假 HTTP 服务模拟各种返回。

目标：Key 无效/额度不足时在批次启动前就停下，
而不是让 10 个商品每个都失败一遍 "Invalid token"。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aeclone.api import ApiClient, ApiError, classify_api_error  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = Handler.mode
        if self.path.startswith("/v1/models"):
            if m == "ok":
                body = {"data": [{"id": "qwen-plus"}, {"id": "qwen-image-3.0"}]}
                self._json(200, body)
            elif m == "401":
                self._json(401, {"error": {"message": "Invalid token (request id: abc123)"}})
            elif m == "403":
                self._json(403, {"error": {"message": "Forbidden"}})
            elif m == "402":
                self._json(402, {"error": {"message": "Insufficient quota, please recharge"}})
            elif m == "500":
                self._json(500, {"error": {"message": "internal error"}})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve(mode: str):
    Handler.mode = mode
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def classify(ex: Exception) -> str:
    return classify_api_error(ex)


def t1_ok():
    print("\n[测试1] 正常 Key → 拿到模型列表")
    srv, base = _serve("ok")
    try:
        models = ApiClient(base, "sk-good", 10).models()
        assert "qwen-plus" in models, models
        print(f"  ✓ {len(models)} 个模型：{models}")
    finally:
        srv.shutdown()


def t2_invalid_token():
    print("\n[测试2] Invalid token → 判为 auth，应阻断")
    srv, base = _serve("401")
    try:
        try:
            ApiClient(base, "sk-bad", 10).models()
            raise AssertionError("应当抛错")
        except ApiError as ex:
            kind = classify(ex)
            assert kind == "auth", (kind, str(ex))
            assert "Invalid token" in str(ex), str(ex)
            print(f"  ✓ kind={kind} · {str(ex)[:50]}")
    finally:
        srv.shutdown()


def t3_forbidden():
    print("\n[测试3] 403 Forbidden → 同样判为 auth")
    srv, base = _serve("403")
    try:
        try:
            ApiClient(base, "sk-x", 10).models()
            raise AssertionError("应当抛错")
        except ApiError as ex:
            assert classify(ex) == "auth", str(ex)
            print("  ✓ kind=auth")
    finally:
        srv.shutdown()


def t4_quota():
    print("\n[测试4] 402 额度不足 → 判为 quota，提示充值")
    srv, base = _serve("402")
    try:
        try:
            ApiClient(base, "sk-x", 10).models()
            raise AssertionError("应当抛错")
        except ApiError as ex:
            assert classify(ex) == "quota", (classify(ex), str(ex))
            print(f"  ✓ kind=quota · {str(ex)[:46]}")
    finally:
        srv.shutdown()


def t5_server_error_not_blocking():
    print("\n[测试5] 500 服务端错误 → 判为 other（只告警，不阻断）")
    srv, base = _serve("500")
    try:
        try:
            ApiClient(base, "sk-x", 10).models()
            raise AssertionError("应当抛错")
        except ApiError as ex:
            assert classify(ex) == "other", (classify(ex), str(ex))
            print("  ✓ kind=other，不阻断批次")
    finally:
        srv.shutdown()


def t6_network_error_not_blocking():
    print("\n[测试6] 连不上（端口关闭）→ 网络错误，判为 other")
    try:
        ApiClient("http://127.0.0.1:9", "sk-x", 2).models()
        raise AssertionError("应当抛错")
    except ApiError:
        raise
    except Exception as ex:
        assert classify(ex) == "other", (classify(ex), str(ex))
        print(f"  ✓ 网络异常不阻断：{type(ex).__name__}")


def t7_empty_key_raises_clear_error():
    print("\n[测试7] 空 Key → 立刻报「未配置 API Key」，不再发 Bearer 换回 Invalid token")
    c = ApiClient("http://127.0.0.1:9", "   ", 2)
    assert c.key == "", repr(c.key)
    try:
        c._headers()
        raise AssertionError("空 Key 应当抛错")
    except ApiError as ex:
        assert "未配置 API Key" in str(ex), str(ex)
        assert classify_api_error(ex) == "auth", classify_api_error(ex)
        print("  ✓", str(ex))


GATES = ("ai_title_enable", "ai_desc_enable", "enable_ai_sku", "review_enable",
         "estimate_weight_enable", "ai_image_enable", "translate_main_pic_enable",
         "translate_detail_pic_enable", "translate_sku_pic")


class _FakeApp:
    """只带 _preflight_api 需要的最小属性。"""
    def __init__(self, cfg):
        self.cfg = cfg
        self.logs = []

    def _log(self, msg, level="INFO"):
        self.logs.append((level, msg))


def _cfg(**kw):
    from aeclone.config import AppConfig
    c = AppConfig()
    for k in GATES:
        c.set(k, False)
    for k, v in kw.items():
        c.set(k, v)
    return c


def t8_gate_off_skips_probe():
    print("\n[测试8] 所有 AI 开关关闭 → 不预检、不阻断")
    from aeclone.ui.app import App
    app = _FakeApp(_cfg(api_key=""))
    assert App._preflight_api(app) == "", "关掉 AI 就不该拦"
    print("  ✓ 返回空串（放行）")


def t9_empty_key_blocks():
    print("\n[测试9] 开了 AI 但 Key 为空 → 阻断并提示")
    from aeclone.ui.app import App
    app = _FakeApp(_cfg(ai_image_enable=True, api_key="   "))
    why = App._preflight_api(app)
    assert "未填写 API Key" in why, why
    print("  ✓", why)


def t10_invalid_token_blocks():
    print("\n[测试10] 开了 AI + Key 无效(401) → 阻断并提示重新填写")
    from aeclone.ui.app import App
    srv, base = _serve("401")
    try:
        app = _FakeApp(_cfg(ai_image_enable=True, api_key="sk-bad",
                            api_base=base, timeout=10))
        why = App._preflight_api(app)
        assert "无效或已过期" in why, why
        assert "Invalid token" in why, why
        print("  ✓", why[:80])
    finally:
        srv.shutdown()


def t11_quota_blocks():
    print("\n[测试11] 额度不足(402) → 阻断并提示充值")
    from aeclone.ui.app import App
    srv, base = _serve("402")
    try:
        app = _FakeApp(_cfg(ai_image_enable=True, api_key="sk-x",
                            api_base=base, timeout=10))
        why = App._preflight_api(app)
        assert "额度不足" in why, why
        print("  ✓", why[:80])
    finally:
        srv.shutdown()


def t12_network_does_not_block():
    print("\n[测试12] 网络异常 → 只 WARN，不阻断")
    from aeclone.ui.app import App
    app = _FakeApp(_cfg(ai_image_enable=True, api_key="sk-x",
                        api_base="http://127.0.0.1:9", timeout=2))
    why = App._preflight_api(app)
    assert why == "", why
    assert app.logs and any(lv == "WARN" for lv, _ in app.logs), app.logs
    print("  ✓ 放行，并记录 WARN")


def t13_ok_passes():
    print("\n[测试13] Key 正常 → 放行并记录模型数")
    from aeclone.ui.app import App
    srv, base = _serve("ok")
    try:
        app = _FakeApp(_cfg(ai_image_enable=True, api_key="sk-good",
                            api_base=base, timeout=10))
        why = App._preflight_api(app)
        assert why == "", why
        assert any("预检通过" in m for _lv, m in app.logs), app.logs
        print("  ✓", app.logs[-1][1])
    finally:
        srv.shutdown()


if __name__ == "__main__":
    tests = [t1_ok, t2_invalid_token, t3_forbidden, t4_quota,
             t5_server_error_not_blocking, t6_network_error_not_blocking, t7_empty_key_raises_clear_error,
             t8_gate_off_skips_probe, t9_empty_key_blocks, t10_invalid_token_blocks,
             t11_quota_blocks, t12_network_does_not_block, t13_ok_passes]
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
