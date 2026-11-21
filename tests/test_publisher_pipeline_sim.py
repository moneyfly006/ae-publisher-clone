#!/usr/bin/env python3
"""Publisher 端到端模拟（不联网、不碰店铺）。

用真实的 `_apply_image_jobs` / `_publicize_images` 跑完整图片链路，
只把 AI 接口和下载换成桩，验证最终主图列表完全符合预期：

- 主图数量不变
- 尺寸不合格的 AI 产出被拒、原图保留
- 范围外的图一张不动
- 转存后第 9+ 张仍然保留
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

from aeclone.services.publisher import Publisher  # noqa: E402


def _mk(d: Path, name: str, w: int, h: int) -> Path:
    p = d / name
    Image.new("RGB", (w, h), "white").save(p)
    return p


class Cfg:
    def __init__(self, **kw):
        d = dict(
            ai_image_enable=True, image_edit_mode="general",
            img_general_prompt="清理商品图", img_general_main_from="1", img_general_main_to="3",
            img_min_size_enable=True, img_min_size=800, img_min_size_drop_invalid=False,
            image_simple_rules="[]", image_ai_jobs="[]", image_cache_enable=False,
            image_concurrency=1, img_fine_main_enable=True, img_fine_detail_enable=False,
            img_fine_sku_enable=False, img_fine_detail_replace=False,
            four_in_one=False, watermark=False, white_bg_enable=False, watermark_enable=False,
            image_edit_plans="[]", designated_edit_enable=False, designated_edit_indices="",
            designated_edit_prompt="", editor_main_images_max=6,
        )
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


class Res:
    def __init__(self):
        self.steps = []


def _pub(cfg, big: Path, small: Path):
    inst = Publisher.__new__(Publisher)
    inst.cfg = cfg
    inst._img_svc = object()
    inst._size_rejects = []
    inst._size_tmp = None
    inst.dxm = None
    inst._pmap = lambda fn, items, workers=None: [fn(x) for x in items]

    def fetch(u, workdir=None):
        lp = Path(str(u))
        if lp.exists():
            return lp
        return small if str(u).endswith("2.jpg") else big

    inst._fetch = fetch
    inst._edit_with_prompt = lambda u, p: str(small) if str(u).endswith("2.jpg") else str(big)

    class _Svc:
        def edit_many(self, files, prompt, tag=""):
            return str(big)

        def white_background(self, fp):
            return str(big)

        def remove_watermark(self, fp):
            return str(big)

    inst._img_service = lambda: _Svc()
    return inst


def _prod(n=10):
    return {"id": "T1", "images": [f"https://ex.com/{i}.jpg" for i in range(1, n + 1)]}


def t1_general_keeps_10():
    print("\n[测试1] 通用编辑 1-3：10 张进 10 张出，只有 1/3 换新，2 因尺寸被拒")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png", 1200, 1200), _mk(d, "small.png", 500, 500)
        pub = _pub(Cfg(), big, small)
        res = Res()
        p = _prod(10)
        before = list(p["images"])
        out = Publisher._apply_image_jobs(pub, p, res)
        assert len(out["images"]) == 10, out["images"]
        assert Path(out["images"][0]).name == "big.png"
        assert out["images"][1] == "https://ex.com/2.jpg", "第2张应保留原图"
        assert Path(out["images"][2]).name == "big.png"
        assert out["images"][3:] == before[3:], "第4-10张必须原样"
        print("  ✓", res.steps[-1].detail)


def t2_general_off_keeps_all():
    print("\n[测试2] 关掉 800 校验：不合格产出也被采纳（对照）")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png", 1200, 1200), _mk(d, "small.png", 500, 500)
        pub = _pub(Cfg(img_min_size_enable=False), big, small)
        res = Res()
        p = _prod(10)
        out = Publisher._apply_image_jobs(pub, p, res)
        assert Path(out["images"][1]).name == "small.png", out["images"][1]
        print("  ✓ 校验关闭时第2张采用 500x500")


def t3_publicize_preserves_9_10():
    print("\n[测试3] 图片转存：8 张上限之外的第 9、10 张必须保留")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png", 1200, 1200), _mk(d, "small.png", 1200, 1200)
        pub = _pub(Cfg(), big, small)
        # 让上传桩直接返回原地址
        import aeclone.services.hosting as hosting
        orig = getattr(hosting, "upload", None)
        hosting.upload = lambda fp: "https://moneyfly.club/up.png"
        try:
            res = Res()
            p = _prod(10)
            out = Publisher._publicize_images(pub, p, res)
            assert len(out["images"]) == 10, out["images"]
            assert out["images"][8] == "https://ex.com/9.jpg", out["images"][8]
            assert out["images"][9] == "https://ex.com/10.jpg", out["images"][9]
            print("  ✓ 10 张全在，第9/10张保留：", out["images"][8], out["images"][9])
        finally:
            if orig is not None:
                hosting.upload = orig


def t4_fine_job_insert_first_keeps_all():
    print("\n[测试4] 精细化：四合一插到第1位 → 原 6 张顺延，总数 7")
    import json
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png", 1200, 1200), _mk(d, "small.png", 500, 500)
        jobs = [{"group": "main", "index": 1, "refs": "1,2,3,4", "prompt": "四合一",
                 "mode": "edit", "insert": "first", "count": 1, "preset": "四合一主图"}]
        cfg = Cfg(image_edit_mode="fine", image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
        pub = _pub(cfg, big, small)
        res = Res()
        p = _prod(6)
        before = list(p["images"])
        out = Publisher._apply_image_jobs(pub, p, res)
        assert len(out["images"]) == 7, out["images"]
        assert out["images"][1:] == before, "原 6 张必须顺延保留"
        print("  ✓ 7 张：第1张为 AI 产出，其余 6 张原样")


if __name__ == "__main__":
    tests = [t1_general_keeps_10, t2_general_off_keeps_all,
             t3_publicize_preserves_9_10, t4_fine_job_insert_first_keeps_all]
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
