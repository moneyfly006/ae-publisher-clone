#!/usr/bin/env python3
"""真实 AI 验证脚本（会调用真实接口，产生少量费用）。

  文字模型：¥0 / 次
  图片编辑：约 ¥0.20 / 张

只用**合成测试数据**（假的机械类目商品 + 本地生成的图），
不读取、不修改任何店铺商品。

用法：
    python tools/verify_real_ai.py            # 只跑文字（免费）
    python tools/verify_real_ai.py --image    # 加上图片（约 ¥0.6）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

PRODUCT = {
    "title": "70m 便携式水井钻机 液压 低噪音 履带式",
    "title_en": "70m Portable Hydraulic Water Well Drilling Rig, Crawler Type, Low Noise",
    "specs": {"Depth": "70m", "Power": "22kW", "Weight": "850kg", "Drill Pipe": "42mm"},
}


def line(ok, label, text=""):
    print(f"{'✓' if ok else '✗'} {label}" + (f"\n    {text}" if text else ""))


def main() -> int:
    do_image = "--image" in sys.argv
    from aeclone.config import AppConfig
    from aeclone.api import ApiClient
    from aeclone.services.text_pipeline import TextPipeline

    cfg = AppConfig.load()
    key = (cfg.api_key or "").strip()
    if not key:
        print("✗ 没有 API Key")
        return 1

    client = ApiClient(cfg.api_base, key, 60)
    tp = TextPipeline(cfg, client=client)
    fails = 0

    print("=" * 62)
    print("真实 AI 验证（合成数据，不碰店铺）")
    print("=" * 62)
    print(f"商品：{PRODUCT['title']}\n")

    # ---- 1. 标题 ----
    print("[1] AI 标题")
    r = tp.make_title(PRODUCT["title"], ["YourBrand"])
    if r.ok:
        line(True, "生成成功", r.value)
        if len(r.value) > 128:
            print(f"    ! 长度 {len(r.value)} 超过速卖通常见 128 字符上限")
    else:
        line(False, "失败", r.error); fails += 1

    # ---- 2. 详情 ----
    print("\n[2] AI 详情")
    r2 = tp.make_detail(PRODUCT["title_en"], PRODUCT["specs"])
    if r2.ok:
        body = r2.value
        line(True, f"生成成功（{len(body)} 字符）", body[:200].replace("\n", " ") + " …")
        for kw in ("70", "Hydraulic"):
            print(f"    {'✓' if kw.lower() in body.lower() else '✗'} 描述里包含 {kw}")
    else:
        line(False, "失败", r2.error); fails += 1

    # ---- 3. SKU ----
    print("\n[3] AI SKU 命名")
    r3 = tp.make_skus(["钻机-70米-液压", "钻机-100米-液压", "钻机-70米-电动"])
    if r3.ok:
        names = [x for x in r3.value.splitlines() if x.strip()]
        line(True, f"生成 {len(names)} 条", " | ".join(names))
        if len(names) != 3:
            print(f"    ! 期望 3 条，实际 {len(names)} 条")
    else:
        line(False, "失败", r3.error); fails += 1

    # ---- 4. 内容审查 ----
    print("\n[4] 内容审查")
    r4 = tp.review_text("70m Hydraulic Water Well Drilling Rig, Crawler Type, CE Certified")
    if r4.ok:
        verdict = r4.value.strip().splitlines()[0][:60]
        line(True, "有结论", verdict)
    else:
        line(False, "失败", r4.error); fails += 1

    # ---- 5. 图片 ----
    if do_image:
        from PIL import Image
        from aeclone.services import ImageService

        tmp = Path(tempfile.mkdtemp())
        src = tmp / "rig.png"
        # 合成一张「产品图」：灰底 + 几个几何块（避免用任何真实商品图）
        img = Image.new("RGB", (1024, 1024), (120, 120, 125))
        from PIL import ImageDraw
        dr = ImageDraw.Draw(img)
        dr.rectangle([220, 380, 800, 640], fill=(210, 170, 40))
        dr.rectangle([300, 300, 420, 380], fill=(60, 60, 65))
        dr.ellipse([600, 600, 700, 700], fill=(40, 40, 45))
        img.save(src)
        print(f"\n[5] 图片编辑（原图 1024x1024 合成图）")
        svc = ImageService(client, cfg)

        try:
            url = svc.white_background(src)
            line(True, "白底图返回", str(url)[:110])
            ok, info = _check_url(url)
            line(ok, "返回图尺寸", info)
            if not ok:
                fails += 1
        except Exception as ex:  # noqa: BLE001
            line(False, "白底失败", f"{type(ex).__name__}: {str(ex)[:120]}"); fails += 1

        print("\n[6] 图片编辑（机械类目提示词）")
        try:
            prompt = ("将这台工程机械设备处理为专业电商主图：纯白色背景、45度角、"
                      "强化金属质感与结构细节、设备占画面约70%、不添加任何文字与水印。")
            url2 = svc.edit(src, prompt)
            line(True, "编辑图返回", str(url2)[:110])
            ok2, info2 = _check_url(url2)
            line(ok2, "返回图尺寸", info2)
            if not ok2:
                fails += 1
        except Exception as ex:  # noqa: BLE001
            line(False, "编辑失败", f"{type(ex).__name__}: {str(ex)[:120]}"); fails += 1
    else:
        print("\n[5] 图片：未启用（加 --image 跑，约 ¥0.4）")

    print("\n" + "=" * 62)
    print("结论：全部通过" if fails == 0 else f"结论：{fails} 项未通过")
    print("=" * 62)
    return 0 if fails == 0 else 1


def _check_url(url) -> tuple[bool, str]:
    """下载返回图并检查尺寸（速卖通要求 ≥800）。"""
    try:
        import io

        import requests
        from PIL import Image
        r = requests.get(str(url), timeout=60)
        r.raise_for_status()
        with Image.open(io.BytesIO(r.content)) as im:
            w, h = im.size
        ok = w >= 800 and h >= 800
        return ok, f"{w}x{h}" + ("" if ok else "  <800，会被尺寸校验拒绝")
    except Exception as ex:  # noqa: BLE001
        return False, f"读不出尺寸：{str(ex)[:60]}"


if __name__ == "__main__":
    raise SystemExit(main())
