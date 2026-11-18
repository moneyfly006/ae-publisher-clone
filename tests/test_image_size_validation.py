#!/usr/bin/env python3
"""图片尺寸校验（速卖通 ≥800×800）专项测试。

覆盖：
1. 底层 check()：合格 / 不合格 / 读不出
2. Publisher._size_ok()：本地文件、URL（file:// 与本机 http 都跳过）
3. 通用编辑：AI 产出不合格 → 保留原图，不覆盖
4. 精细任务：产出不合格 → 该条不落位
5. _publicize_images：告警模式保留 / 移除模式删除
6. 阈值可配置（≥1000 时 900×900 应被拒）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

from aeclone.services import image_validation as iv  # noqa: E402


def _mk(d: Path, name: str, w: int, h: int) -> Path:
    p = d / name
    Image.new("RGB", (w, h), "white").save(p)
    return p


def t1_basic_check():
    print("\n[测试1] 底层 check()")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ok_f = _mk(d, "ok.png", 1024, 1024)
        edge_f = _mk(d, "edge.png", 800, 800)
        bad_f = _mk(d, "bad.png", 799, 900)
        ok, info = iv.check(ok_f)
        assert ok and info == "1024x1024", (ok, info)
        ok, info = iv.check(edge_f)
        assert ok, f"800x800 应合格：{info}"
        ok, info = iv.check(bad_f)
        assert not ok and "799x900" in info, (ok, info)
        ok, info = iv.check(d / "nope.png")
        assert not ok and "无法读取" in info
        print("  ✓ 1024 / 800 / 799 / 不存在 全部判断正确")


def t2_threshold_configurable():
    print("\n[测试2] 阈值可配置")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        f = _mk(d, "900.png", 900, 900)
        assert iv.check(f, 800)[0] is True
        assert iv.check(f, 1000)[0] is False
        assert iv.check(f, 900)[0] is True
        print("  ✓ 900x900：min=800 通过 / min=1000 拒绝 / min=900 通过")


def t3_size_cache():
    print("\n[测试3] 尺寸缓存（同一文件只解析一次）")
    with tempfile.TemporaryDirectory() as td:
        f = _mk(Path(td), "c.png", 1024, 1024)
        iv._SIZE_CACHE.clear()
        a = iv.size_of(f)
        assert a == (1024, 1024)
        assert len(iv._SIZE_CACHE) == 1
        b = iv.size_of(f)
        assert a == b and len(iv._SIZE_CACHE) == 1
        print("  ✓ 缓存命中，未重复解析")


class MiniCfg:
    def __init__(self, **kw):
        d = {"img_min_size_enable": True, "img_min_size": 800,
             "img_min_size_drop_invalid": False}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def _make_publisher(cfg):
    from aeclone.services.publisher import Publisher
    inst = Publisher.__new__(Publisher)
    inst.cfg = cfg
    inst._img_svc = None
    inst._size_rejects = []
    inst._size_tmp = None
    return inst


def t4_publisher_size_ok_local():
    print("\n[测试4] Publisher._size_ok 读本地文件")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ok_f = _mk(d, "a.png", 1000, 1000)
        bad_f = _mk(d, "b.png", 500, 500)
        pub = _make_publisher(MiniCfg())
        assert pub._size_ok(str(ok_f))[0] is True
        assert pub._size_ok(str(bad_f))[0] is False
        print("  ✓ 本地 1000 通过 / 500 拒绝")


def t5_vet_keeps_original():
    print("\n[测试5] _vet 不合格时退回原图，并记录拒绝项")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        bad = _mk(d, "bad.png", 400, 400)
        orig = "https://ex.com/orig.jpg"
        pub = _make_publisher(MiniCfg())
        got = pub._vet(str(bad), orig)
        assert got == orig, f"应退回原图，实际 {got}"
        assert pub._size_rejects and "400x400" in pub._size_rejects[0][1]
        print("  ✓ 退回原图：", got, "| 记录：", pub._size_rejects[0][1])


def t6_vet_disabled():
    print("\n[测试6] 关掉校验 → 原样放行")
    with tempfile.TemporaryDirectory() as td:
        bad = _mk(Path(td), "bad.png", 400, 400)
        pub = _make_publisher(MiniCfg(img_min_size_enable=False))
        got = pub._vet(str(bad), "https://ex.com/orig.jpg")
        assert got == str(bad)
        print("  ✓ 已关闭校验，直接采用新图")


def t7_general_edit_rejects_small():
    print("\n[测试7] 通用编辑：AI 产出 500×500 → 保留原图")
    from aeclone.services.publisher import Publisher
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        small = _mk(d, "small.png", 500, 500)
        big = _mk(d, "big.png", 1200, 1200)

        cfg = MiniCfg()
        cfg.image_edit_mode = "general"
        cfg.img_general_prompt = "清理商品图"
        cfg.img_general_main_from = "1"
        cfg.img_general_main_to = "2"

        pub = _make_publisher(cfg)
        # 第1张产出小图（应被拒），第2张产出大图（应采纳），第3张不变
        pub._edit_with_prompt = lambda u, p: str(small) if u.endswith("1.jpg") else str(big)
        pub._pmap = lambda fn, items, workers=None: [fn(x) for x in items]
        pub._fetch = lambda u, workdir=None: Path(u)
        pub._set_group_url = Publisher._set_group_url

        class R:
            steps = []
        res = R()

        p = {"images": ["https://ex.com/1.jpg", "https://ex.com/2.jpg", "https://ex.com/3.jpg"]}
        # 让 _fetch 返回真实文件
        def fake_fetch(u, workdir=None):
            lp = Path(str(u))
            if lp.exists():
                return lp
            return small if str(u).endswith("1.jpg") else big
        pub._fetch = fake_fetch

        out = pub._apply_general_edit(p, res)
        assert out["images"][0] == "https://ex.com/1.jpg", f"第1张应保留原图，实际 {out['images'][0]}"
        assert out["images"][1] == str(big), f"第2张应采用大图，实际 {out['images'][1]}"
        assert out["images"][2] == "https://ex.com/3.jpg", "第3张不应变"
        print("  ✓ 第1张保留原图 / 第2张采纳 / 第3张不动")
        print("  ✓ 步骤:", [s.detail for s in res.steps])


def _offline_env(small: Path, big: Path):
    """把下载与上传都换成桩，保证测试零网络、零上传。"""
    import contextlib
    import aeclone.services.hosting as hosting
    import requests

    class _Resp:
        def __init__(self, data):
            self.content = data

        def raise_for_status(self):
            return None

    orig_upload = getattr(hosting, "upload", None)
    orig_get = requests.get

    counter = {"n": 0}

    def fake_upload(fp):
        # 顺序上传（image_concurrency=1），第 1 张对应 images[0]
        counter["n"] += 1
        return ("https://moneyfly.club/up_small.png" if counter["n"] == 1
                else "https://moneyfly.club/up_big.png")

    def fake_get(url, *a, **k):
        return _Resp((small if "small" in str(url) else big).read_bytes())

    hosting.upload = fake_upload
    requests.get = fake_get

    @contextlib.contextmanager
    def _ctx():
        try:
            yield
        finally:
            if orig_upload is not None:
                hosting.upload = orig_upload
            requests.get = orig_get
    return _ctx()


def t8_publicize_drop_mode():
    print("\n[测试8] _publicize_images：移除模式删除不合格图")
    from aeclone.services.publisher import Publisher
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        small = _mk(d, "s.png", 400, 400)
        big = _mk(d, "b.png", 1000, 1000)

        cfg = MiniCfg(img_min_size_drop_invalid=True)
        pub = _make_publisher(cfg)
        pub.dxm = None
        pub._fetch = lambda u, workdir=None: (
            small if ("small" in str(u) or "up_small" in str(u)) else big)
        pub._pmap = lambda fn, items, workers=None: [fn(x) for x in items]

        class R:
            steps = []
        res = R()
        p = {"images": ["https://ex.com/small.jpg", "https://ex.com/big.jpg"]}
        with _offline_env(small, big):
            out = Publisher._publicize_images(pub, p, res)
        print("  结果:", out["images"])
        assert len(out["images"]) == 1, out["images"]
        print("  ✓ 已移除 1 张")


def t9_publicize_warn_mode():
    print("\n[测试9] _publicize_images：告警模式保留（不删主图）")
    from aeclone.services.publisher import Publisher
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        small = _mk(d, "s.png", 400, 400)
        big = _mk(d, "b.png", 1000, 1000)

        cfg = MiniCfg(img_min_size_drop_invalid=False)
        pub = _make_publisher(cfg)
        pub.dxm = None
        pub._fetch = lambda u, workdir=None: (
            small if ("small" in str(u) or "up_small" in str(u)) else big)
        pub._pmap = lambda fn, items, workers=None: [fn(x) for x in items]

        class R:
            steps = []
        res = R()
        p = {"images": ["https://ex.com/small.jpg", "https://ex.com/big.jpg"]}
        with _offline_env(small, big):
            out = Publisher._publicize_images(pub, p, res)
        assert len(out["images"]) == 2, f"告警模式不应删除：{out['images']}"
        assert pub._size_rejects, "应记录拒绝项用于告警"
        print("  ✓ 2 张都在，已告警：", res.steps[-2].detail if len(res.steps) > 1 else "")


def t10_product_summary():
    print("\n[测试10] validate_product_images / summarize 汇总")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ok_f = _mk(d, "ok.png", 900, 900)
        bad_f = _mk(d, "bad.png", 300, 300)
        prod = {"images": [str(ok_f), str(bad_f)], "detail_images": [str(ok_f)], "skus": []}
        results = iv.validate_product_images(prod, 800)
        passed, failed, problems = iv.summarize(results)
        assert passed == 2 and failed == 1, (passed, failed)
        assert "主图[2]" in problems[0], problems
        print(f"  ✓ 合格 {passed} / 不合格 {failed} / 问题 {problems}")


if __name__ == "__main__":
    tests = [t1_basic_check, t2_threshold_configurable, t3_size_cache,
             t4_publisher_size_ok_local, t5_vet_keeps_original, t6_vet_disabled,
             t7_general_edit_rejects_small, t8_publicize_drop_mode,
             t9_publicize_warn_mode, t10_product_summary]
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
