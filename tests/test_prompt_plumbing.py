#!/usr/bin/env python3
"""「提示词是否真的发给模型」验证（不联网，用本地假图片 API）。

用户的核心诉求：开了 AI、写了提示词，就要按提示词改图。
这里用假 /v1/images/edits 服务把收到的请求录下来，逐项断言：

1. 通用编辑：发出去的就是配置里那条提示词
2. 精细化编辑：每张图发各自的提示词（不会串）
3. 参考图顺序：本地参考图在前，产品原图在后
4. 机械类目预设：选预设后，预设正文真的在 payload 里
5. 模型名来自配置
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

from aeclone.api import ApiClient  # noqa: E402
from aeclone.services import ImageService  # noqa: E402

CAPTURED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        rec = {"path": self.path, "raw": raw, "ctype": ctype}
        if "multipart/form-data" in ctype:
            body = raw.decode("utf-8", "replace")
            m = re.search(r'name="prompt"\r\n\r\n(.*?)\r\n--', body, re.S)
            rec["prompt"] = m.group(1) if m else ""
            m = re.search(r'name="model"\r\n\r\n(.*?)\r\n--', body, re.S)
            rec["model"] = m.group(1) if m else ""
            rec["files"] = len(re.findall(r'name="image"; filename=', body))
            rec["filenames"] = re.findall(r'name="image"; filename="([^"]+)"', body)
        CAPTURED.append(rec)
        data = json.dumps({"data": [{"url": "https://cdn.ex.com/out.png"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class Cfg:
    def __init__(self, **kw):
        d = dict(api_base="", api_key="sk-test", timeout=10,
                 image_model_edit="qwen-image-3.0", image_model_gen="qwen-image-3.0",
                 model_image_edit="qwen-image-3.0", model_image_gen="qwen-image-3.0",
                 ai_image_enable=True, image_edit_mode="general",
                 img_general_prompt="", img_general_main_from="1", img_general_main_to="3",
                 img_min_size_enable=False, img_min_size=800, img_min_size_drop_invalid=False,
                 image_simple_rules="[]", image_ai_jobs="[]", image_cache_enable=False,
                 image_concurrency=1, img_fine_main_enable=True, img_fine_detail_enable=False,
                 img_fine_sku_enable=False, img_fine_detail_replace=False,
                 four_in_one=False, watermark=False, white_bg_enable=False, watermark_enable=False,
                 image_edit_plans="[]", designated_edit_enable=False, designated_edit_indices="",
                 designated_edit_prompt="", editor_main_images_max=6)
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def _mk(d: Path, name: str, w=1024, h=1024) -> Path:
    p = d / name
    Image.new("RGB", (w, h), "white").save(p)
    return p


def _pub(cfg, files_by_url):
    from aeclone.services.publisher import Publisher
    inst = Publisher.__new__(Publisher)
    inst.cfg = cfg
    inst._size_rejects = []
    inst._size_tmp = None
    inst.dxm = None
    inst._img_svc = None
    inst._pmap = lambda fn, items, workers=None: [fn(x) for x in items]

    def fetch(u, workdir=None):
        lp = Path(str(u))
        if lp.exists():
            return lp
        return files_by_url[str(u)]

    inst._fetch = fetch
    return inst


def t1_general_prompt_sent():
    print("\n[测试1] 通用编辑：配置里那条提示词必须原样发给模型")
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            big = _mk(d, "big.png")
            prompt = "清理并本地化这张商品图：去除水印、翻译成英文，商品本体不得改变。"
            cfg = Cfg(api_base=base, img_general_prompt=prompt)
            pub = _pub(cfg, {f"https://ex.com/{i}.jpg": big for i in range(1, 11)})
            CAPTURED.clear()
            class R:
                steps = []
            res = R()
            p = {"images": [f"https://ex.com/{i}.jpg" for i in range(1, 11)]}
            out = __import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher._apply_image_jobs(pub, p, res)
            assert len(CAPTURED) == 3, f"1-3 应调用 3 次，实际 {len(CAPTURED)}"
            for i, rec in enumerate(CAPTURED, 1):
                assert prompt in rec["prompt"], f"第{i}次提示词不对：{rec['prompt'][:60]}"
            assert out["images"][3:] == [f"https://ex.com/{i}.jpg" for i in range(4, 11)]
            print(f"  ✓ 3 次调用，提示词一致（{len(prompt)} 字）")
            print(f"  ✓ 第 4-10 张未发送：共 {len(CAPTURED)} 次调用")
    finally:
        srv.shutdown()


def t2_fine_each_prompt():
    print("\n[测试2] 精细化编辑：每张图发各自的提示词，不会串")
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            big = _mk(d, "big.png")
            jobs = [
                {"group": "main", "index": 1, "prompt": "主图第1张：白底棚拍，45度角，突出金属质感",
                 "mode": "edit", "insert": "replace"},
                {"group": "main", "index": 3, "prompt": "主图第3张：换成工地作业场景，可见操作人员",
                 "mode": "edit", "insert": "replace"},
            ]
            cfg = Cfg(api_base=base, image_edit_mode="fine",
                      image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
            pub = _pub(cfg, {f"https://ex.com/{i}.jpg": big for i in range(1, 11)})
            CAPTURED.clear()
            class R:
                steps = []
            p = {"images": [f"https://ex.com/{i}.jpg" for i in range(1, 11)]}
            P = __import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher
            P._apply_image_jobs(pub, p, R())
            assert len(CAPTURED) == 2, len(CAPTURED)
            assert "45度角" in CAPTURED[0]["prompt"], CAPTURED[0]["prompt"][:50]
            assert "工地作业场景" in CAPTURED[1]["prompt"], CAPTURED[1]["prompt"][:50]
            assert "工地" not in CAPTURED[0]["prompt"], "第1条串到了第2条的提示词"
            print("  ✓ 第1次:", CAPTURED[0]["prompt"][:36])
            print("  ✓ 第2次:", CAPTURED[1]["prompt"][:36])
    finally:
        srv.shutdown()


def t3_ref_order():
    print("\n[测试3] 参考图顺序：本地参考图在前，产品原图在后")
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            target = _mk(d, "product.png")
            ref1 = _mk(d, "ref_a.png")
            ref2 = _mk(d, "ref_b.png")
            jobs = [{"group": "main", "index": 1, "prompt": "参考图1、图2的风格，融合生成专业主图",
                     "mode": "edit", "insert": "first",
                     "ref_paths": [str(ref1), str(ref2)]}]
            cfg = Cfg(api_base=base, image_edit_mode="fine",
                      image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
            pub = _pub(cfg, {"https://ex.com/1.jpg": target})
            CAPTURED.clear()
            class R:
                steps = []
            p = {"images": ["https://ex.com/1.jpg"]}
            P = __import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher
            P._apply_image_jobs(pub, p, R())
            assert len(CAPTURED) == 1, len(CAPTURED)
            names = CAPTURED[0]["filenames"]
            assert len(names) == 3, names
            assert "ref_a" in names[0] and "ref_b" in names[1], names
            assert "product" in names[2], names
            print("  ✓ 上传顺序:", [n.split('_', 1)[-1] for n in names])
    finally:
        srv.shutdown()


def t4_machinery_preset_reaches_api():
    print("\n[测试4] 机械类目预设：选预设后正文真的进 payload")
    from aeclone.services.image_presets import get_preset
    preset = get_preset("main", "machinery_generate")
    assert preset, "预设 machinery_generate 不存在"
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            big = _mk(d, "big.png")
            jobs = [{"group": "main", "index": 1, "prompt": preset["en"],
                     "mode": "edit", "insert": "first", "preset": preset["key"]}]
            cfg = Cfg(api_base=base, image_edit_mode="fine",
                      image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
            pub = _pub(cfg, {"https://ex.com/1.jpg": big})
            CAPTURED.clear()
            class R:
                steps = []
            P = __import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher
            P._apply_image_jobs(pub, {"images": ["https://ex.com/1.jpg"]}, R())
            assert len(CAPTURED) == 1
            sent = CAPTURED[0]["prompt"]
            assert "industrial machinery" in sent, sent[:80]
            assert "1200x1200" in sent, sent[:80]
            print(f"  ✓ 预设「{preset['zh']}」已发出（{len(sent)} 字）")
    finally:
        srv.shutdown()


def t5_model_is_fixed():
    print("\n[测试5] 模型名固定（不随配置变，避免客户选到不可用模型）")
    from aeclone.services import FIXED_MODELS
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            big = _mk(d, "big.png")
            cfg = Cfg(api_base=base, img_general_prompt="测试",
                      image_model_edit="someone-elses-model")
            cfg.model_image_edit = "someone-elses-model"
            pub = _pub(cfg, {"https://ex.com/1.jpg": big})
            CAPTURED.clear()
            class R:
                steps = []
            P = __import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher
            P._apply_image_jobs(pub, {"images": ["https://ex.com/1.jpg"]}, R())
            assert CAPTURED, "没有调用"
            sent = CAPTURED[0].get("model")
            assert sent == FIXED_MODELS["image_edit"], (sent, FIXED_MODELS["image_edit"])
            print(f"  ✓ 实际发送 model = {sent}（= FIXED_MODELS['image_edit']）")
    finally:
        srv.shutdown()


def t6_image_service_direct():
    print("\n[测试6] 直接调 ImageService.edit_many 也带提示词")
    srv, base = _serve()
    try:
        with __import__("tempfile").TemporaryDirectory() as td:
            d = Path(td)
            f1, f2 = _mk(d, "a.png"), _mk(d, "b.png")
            svc = ImageService(ApiClient(base, "sk-test", 10), Cfg(api_base=base))
            CAPTURED.clear()
            url = svc.edit_many([f1, f2], "把这四张图合成一张专业电商主图")
            assert url.startswith("http"), url
            assert len(CAPTURED) == 1
            assert "合成一张专业电商主图" in CAPTURED[0]["prompt"]
            assert CAPTURED[0]["files"] == 2, CAPTURED[0]["files"]
            print("  ✓ prompt 与 2 张素材都到了模型")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    tests = [t1_general_prompt_sent, t2_fine_each_prompt, t3_ref_order,
             t4_machinery_preset_reaches_api, t5_model_is_fixed, t6_image_service_direct]
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
