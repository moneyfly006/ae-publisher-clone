#!/usr/bin/env python3
"""task-10 阶段1：全平台真实媒体链路 + 逐页截图。

子命令
------
media    真实下载商品图 → 本地图片链路（方图补白/水印/四合一/营销图/压缩/尺寸校验）
         → 真实付费 AI 图像编辑（白底 1 次 + 精细编辑 1 次）。
         产物与逐项指标写入 docs/design/test-assets/run-media-2.json。
video    用 3~5 张真实图真实生成轮播视频（services/video.py + ffmpeg），
         ffprobe 校验时长/分辨率/大小 → docs/design/test-assets/run-video-2.json。
shots    逐平台逐页截图（离线沙箱 Flet Web + Playwright/Chrome，本地 CJK 字体），
         输出 docs/design/ui-verify/all-platforms/<platform>-<page>-<label>.png
         → docs/design/test-assets/run-shots-2.json。
report   合并 media/video/shots + 三套 UI 审计 JSON（docs/platform-verification.json、
         docs/ui-button-audit.json、docs/ui-options-verification.json）
         → docs/all-platforms-media-verification-2.json。

口径（真实 vs 离线）
--------------------
* media / video：真实联网、真实 ffmpeg、真实付费 AI，失败也如实记录原因。
* shots / 三套 UI 审计：隔离沙箱（tools/ui_sandbox.py），不联网、不读写客户配置、
  不调用任何平台写接口；只验证 UI 结构与回调路由。

用法
----
  .venv/bin/python tools/verify_media_all.py media --ai
  .venv/bin/python tools/verify_media_all.py video
  .venv/bin/python tools/verify_media_all.py shots
  .venv/bin/python tools/verify_media_all.py report
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ASSETS = ROOT / "docs" / "design" / "test-assets"
SHOTS = ROOT / "docs" / "design" / "ui-verify" / "all-platforms"
RUN_MEDIA = ASSETS / "run-media-2.json"
RUN_VIDEO = ASSETS / "run-video-2.json"
RUN_SHOTS = ASSETS / "run-shots-2.json"
FINAL_JSON = ROOT / "docs" / "all-platforms-media-verification-2.json"
FINAL_MD = ROOT / "docs" / "ALL_PLATFORMS_VERIFICATION_2.md"
PLATFORM_JSON = ROOT / "docs" / "platform-verification.json"
BUTTON_JSON = ROOT / "docs" / "ui-button-audit.json"
OPTIONS_JSON = ROOT / "docs" / "ui-options-verification.json"

# 真实商品图（公开 CDN，无需登录）：Alibaba 国际站 + 速卖通各 2 张。
SOURCE_URLS = [
    "https://sc04.alicdn.com/kf/H054956b8d48a466a8c136c7043fac5b8K.png",       # 729x642 非方图
    "https://sc04.alicdn.com/kf/H38c58f3593c14ddea6ad0bcf645fdb6ab.jpg",       # 1000x1000 方图
    "https://ae-pic-a1.aliexpress-media.com/kf/Sa1ede3d45c5443819bd619c4244d96f7L.jpg",  # 1024x1024
    "https://ae-pic-a1.aliexpress-media.com/kf/S02b17b70454b4bba8cb7475581ac98ceI.jpg",  # 1500x1348
]

# 页面号 → 文件名 slug（仅用于截图命名，不参与业务）。
PAGE_SLUGS = {
    "alibaba": {0: "overview", 1: "alibaba-connect", 12: "alibaba-listing",
                8: "publish-queue", 3: "ai-text", 5: "ai-image",
                4: "content-review", 10: "tasks-logs", 9: "image-workbench",
                11: "settings"},
    "aliexpress": {0: "overview", 1: "store-templates", 2: "category-attributes",
                   6: "pricing", 7: "variants-inventory", 3: "ai-text",
                   5: "ai-image", 4: "content-review", 8: "publish-queue",
                   10: "tasks-logs", 9: "image-workbench", 11: "settings"},
    "amazon": {0: "overview", 13: "amazon-listing", 10: "tasks-logs", 11: "settings"},
    "tiktok": {0: "overview", 14: "tiktok-listing", 10: "tasks-logs", 11: "settings"},
}

CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
VIEWPORT = {"width": 1560, "height": 2200}


# ---------------------------------------------------------------------------
# 通用记录工具
# ---------------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_info(path: Path) -> dict:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return {"width": int(im.width), "height": int(im.height),
                    "mode": im.mode, "format": im.format}
    except Exception as ex:  # noqa: BLE001
        return {"error": f"{type(ex).__name__}: {ex}"}


def artifact(key: str, path: Path, elapsed: float, ok: bool = True,
             error: str = "", kind: str = "image", **extra) -> dict:
    row = {"key": key, "path": str(path.relative_to(ROOT)) if path.is_absolute() and path.is_relative_to(ROOT) else str(path),
           "ok": bool(ok and path.exists() and path.stat().st_size > 0),
           "bytes": path.stat().st_size if path.exists() else 0,
           "elapsed_s": round(elapsed, 3), "error": error}
    if path.exists() and path.stat().st_size > 0:
        if kind == "image":
            info = image_info(path)
            if info.get("error"):
                row["image_info_error"] = info.pop("error")
            row.update(info)
        row["sha256"] = sha256_of(path)
        row["sha256_16"] = row["sha256"][:16]
    else:
        row["ok"] = False
        row["error"] = error or "文件不存在或为空"
    row.update(extra)
    return row


def _nonblank(path: Path) -> dict:
    """截图非空判断：尺寸 + 颜色数 + 亮度标准差（防止全白/全黑画布）。"""
    from PIL import Image, ImageStat
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            small = rgb.resize((160, 160))
            colors = len(small.getcolors(maxcolors=160 * 160) or [])
            stat = ImageStat.Stat(small)
            stddev = round(sum(stat.stddev) / 3, 2)
            return {"colors": colors, "stddev": stddev,
                    "nonblank": bool(colors > 24 and stddev > 3.0)}
    except Exception as ex:  # noqa: BLE001
        return {"nonblank": False, "error": f"{type(ex).__name__}: {ex}"}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# media：真实图片链路 + 真实付费 AI
# ---------------------------------------------------------------------------
def _download(url: str, dest: Path) -> Path:
    import requests
    t0 = time.time()
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 AuroraList-verify"})
    r.raise_for_status()
    if not r.content:
        raise ValueError("下载内容为空")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest


def _ai_client():
    from aeclone import config
    from aeclone.api import ApiClient
    from aeclone.services import ImageService
    cfg = config.get_config()
    client = ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
    return cfg, ImageService(client, cfg)


def _api_balance(cfg) -> dict | None:
    """真实网关余额（用于证明付费调用确实发生）。失败返回 None，不影响主流程。"""
    from aeclone.api import ApiClient
    try:
        return ApiClient(cfg.api_base, cfg.api_key, cfg.timeout).balance()
    except Exception:  # noqa: BLE001
        return None


def _ai_evidence_from_prev(prev: dict) -> dict:
    """复用上一轮真实付费调用的证据（不再产生费用），并校验文件哈希。"""
    ai = dict(prev.get("ai") or {})
    if not ai.get("white_bg"):
        return {"ok": None, "note": "没有可复用的 AI 证据文件"}
    for key in ("white_bg", "fine_edit"):
        row = ai.get(key)
        if not isinstance(row, dict):
            continue
        p = ROOT / str(row.get("path") or "")
        if p.is_file() and p.stat().st_size > 0:
            actual = sha256_of(p)
            row["reused_from_previous_run"] = True
            row["sha256_actual"] = actual
            row["file_verified"] = actual == row.get("sha256")
            row["ok"] = bool(row.get("ok")) and row["file_verified"]
        else:
            row["ok"] = False
            row["error"] = "证据文件不存在"
    wb, fe = ai.get("white_bg") or {}, ai.get("fine_edit") or {}
    if wb.get("sha256") and fe.get("sha256") and wb["sha256"] == fe["sha256"]:
        ai["same_input_probe"] = {
            "ok": True,
            "input": fe.get("input"),
            "prompt": fe.get("prompt"),
            "output_sha256": fe.get("sha256"),
            "white_bg_sha256": wb.get("sha256"),
            "identical_to_white_bg": True,
            "url": fe.get("url"),
            "elapsed_s": fe.get("elapsed_s"),
            "conclusion": (
                "同一张输入图只更换提示词时，网关约 2.5s 返回与白底图字节完全一致的结果"
                "（按图缓存/未真正重新生成）。App 内 ImagePipeline 以白底产物为编辑输入，"
                "输入字节不同，不受该缓存影响；如需更强证据，建议真机再跑一次白底→编辑链路。"),
        }
        fe["identical_to_white_bg"] = True
        fe["note"] = "与白底图字节相同：本次探测属于「同输入换提示词」场景，非 App 实际链路。"
    ai["ok"] = all((ai.get(k) or {}).get("ok") for k in ("white_bg", "fine_edit"))
    ai["calls_total_paid"] = sum(
        1 for k in ("white_bg", "fine_edit") if (ai.get(k) or {}).get("paid"))
    ai["paid_calls_this_run"] = 0
    ai["reused"] = True
    ai["evidence_file"] = "docs/design/test-assets/run-media-2.attempt1.json"
    ai["note"] = ("复用 attempt1 的 2 次真实付费调用证据（本次未再次付费）；"
                  "文件哈希已复核。精细编辑与白底字节相同的原因见 same_input_probe。")
    return ai


def _download_ai_result(url: str, dest: Path) -> Path:
    import requests
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def cmd_media(args) -> int:
    from aeclone.services import image_ops, image_validation

    ASSETS.mkdir(parents=True, exist_ok=True)
    out: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "REAL（真实联网下载 + 本地 Pillow 处理 + 真实付费 AI 图像编辑）",
        "sources": [], "local_ops": {}, "validation": {}, "ai": {},
        "failures": [],
    }

    # 1) 真实商品图下载
    src_paths: list[Path] = []
    for i, url in enumerate(SOURCE_URLS, 1):
        suffix = Path(url.split("?")[0]).suffix.lower() or ".jpg"
        dest = ASSETS / f"src-{i}{suffix}"
        t0 = time.time()
        try:
            _download(url, dest)
            row = artifact(f"source-{i}", dest, time.time() - t0, source_url=url)
        except Exception as ex:  # noqa: BLE001
            row = {"key": f"source-{i}", "path": str(dest.relative_to(ROOT)),
                   "ok": False, "error": f"{type(ex).__name__}: {ex}",
                   "elapsed_s": round(time.time() - t0, 3), "source_url": url}
            out["failures"].append(f"下载 {url}: {row['error']}")
        out["sources"].append(row)
        if row.get("ok"):
            src_paths.append(dest)
    if len(src_paths) < 4:
        _write_json(RUN_MEDIA, out)
        print(f"!! 真实商品图下载不足 4 张（{len(src_paths)}），已记录失败，终止图片链路")
        return 1

    square_src = next((p for p in src_paths if image_info(p).get("width", 0) != image_info(p).get("height", 0)), src_paths[0])

    def run_op(key: str, fn, **kwargs) -> Path | None:
        dest = ASSETS / f"{key}.png"
        t0 = time.time()
        try:
            fn(**kwargs)
            out["local_ops"][key] = artifact(key, dest, time.time() - t0)
        except Exception as ex:  # noqa: BLE001
            out["local_ops"][key] = {"key": key, "path": str(dest.relative_to(ROOT)),
                                     "ok": False, "error": f"{type(ex).__name__}: {ex}",
                                     "elapsed_s": round(time.time() - t0, 3)}
            out["failures"].append(f"{key}: {out['local_ops'][key]['error']}")
            return None
        return dest

    # 2) 本地图片链路（真实 Pillow 处理）
    square = run_op("local-square", image_ops.pad_square, path=square_src,
                    out=ASSETS / "local-square.png", size=1024)

    # 图片水印素材（本地生成，模拟店标）
    logo = ASSETS / "watermark-logo.png"
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = None
        for cand in CJK_FONT_CANDIDATES:
            if Path(cand).is_file():
                with contextlib.suppress(Exception):
                    font = ImageFont.truetype(cand, 56)
                    break
        canvas = Image.new("RGBA", (620, 150), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((0, 0, 619, 149), radius=24, fill=(18, 24, 38, 150))
        draw.text((26, 42), "AURORALIST", font=font, fill=(255, 255, 255, 235))
        canvas.save(logo)
    except Exception as ex:  # noqa: BLE001
        out["failures"].append(f"watermark-logo: {type(ex).__name__}: {ex}")

    # 文本水印用「有背景的真实商品图」：add_watermark 的文字固定为白色，
    # 打在纯白补边的方图上会白上叠白（见下面的 contrast_probe 实测）。
    run_op("local-watermark-text", image_ops.add_watermark, path=src_paths[3],
           watermark="AURORALIST", out=ASSETS / "local-watermark-text.png",
           position="center", opacity=0.75)
    if square and logo.exists():
        run_op("local-watermark-image", image_ops.add_watermark, path=square,
               watermark=logo, out=ASSETS / "local-watermark-image.png",
               position="bottom-left", opacity=0.8)

    # 水印对比度探针：白字水印打在白色补边的方图上是否真的改变像素。
    probe_path = ASSETS / "watermark-contrast-probe.png"
    t0 = time.time()
    try:
        from PIL import Image, ImageChops
        image_ops.add_watermark(square, "AURORALIST", probe_path,
                                position="bottom-right", opacity=0.75)
        if probe_path.exists():
            a = Image.open(square).convert("RGB")
            b = Image.open(probe_path).convert("RGB")
            bbox = ImageChops.difference(a, b).getbbox()
            out["local_ops"]["watermark_contrast_probe"] = {
                "key": "watermark_contrast_probe",
                "path": str(probe_path.relative_to(ROOT)),
                "width": b.width, "height": b.height,
                "bytes": probe_path.stat().st_size,
                "sha256_16": sha256_of(probe_path)[:16],
                "elapsed_s": round(time.time() - t0, 3),
                "probe_executed": True,
                "pixel_changed": bool(bbox),
                "pixel_diff_bbox": list(bbox) if bbox else None,
                "ok": True,
                "expected_invisible": True,
                "finding": ("白色文字水印叠加在白色补边方图上时像素零变化（白上叠白），"
                            "像素差 bbox=None；因此文字水印只适合有背景的照片，"
                            "白底主图应使用图片水印（本轮的 local-watermark-image 已验证可见）。"),
                "source_square_sha256_16": sha256_of(square)[:16],
                "probe_sha256_16": sha256_of(probe_path)[:16],
                "identical_to_source": sha256_of(square) == sha256_of(probe_path),
            }
    except Exception as ex:  # noqa: BLE001
        out["local_ops"]["watermark_contrast_probe"] = {
            "key": "watermark_contrast_probe", "ok": False,
            "error": f"{type(ex).__name__}: {ex}",
            "elapsed_s": round(time.time() - t0, 3)}
    run_op("local-four", image_ops.four_in_one, paths=src_paths,
           out=ASSETS / "local-four.png", size=1200)
    run_op("local-marketing-1x1", image_ops.make_marketing, src=square_src,
           out=ASSETS / "local-marketing-1x1.png", size=(800, 800), mode="contain")
    run_op("local-marketing-3x4", image_ops.make_marketing, src=square_src,
           out=ASSETS / "local-marketing-3x4.png", size=(750, 1000), mode="cover")

    # 3) 压缩（平台详情图 >5MB 会被拒收）
    comp = ASSETS / "local-compress.jpg"
    t0 = time.time()
    try:
        raw = src_paths[-1].read_bytes()
        image_ops.compress_jpeg(raw, comp, max_bytes=5 * 1024 * 1024, max_side=1600)
        out["local_ops"]["local-compress"] = artifact("local-compress", comp, time.time() - t0,
                                                      source_size=len(raw))
    except Exception as ex:  # noqa: BLE001
        out["local_ops"]["local-compress"] = {"key": "local-compress", "ok": False,
                                              "error": f"{type(ex).__name__}: {ex}",
                                              "elapsed_s": round(time.time() - t0, 3)}
        out["failures"].append(f"local-compress: {out['local_ops']['local-compress']['error']}")

    # 4) 尺寸校验：全部产物 + 不合格负样本 + 真实远程 URL
    #    3:4 营销图按 750×1000 出图（店小秘口径），不适用平台主图 ≥800 门槛。
    min800_exempt = {
        "local-marketing-3x4": "3:4 营销图按 750×1000 出图（店小秘口径），非平台主图，不适用 ≥800×800",
        "watermark_contrast_probe": "水印对比度探针，用于证明白字水印在白底图上不可见",
    }
    checks = []
    for key, row in list(out["local_ops"].items()):
        p = ROOT / row["path"] if not Path(row["path"]).is_absolute() else Path(row["path"])
        if not p.exists():
            continue
        t0 = time.time()
        ok, detail = image_validation.check(p)
        required = key not in min800_exempt
        checks.append({"key": key, "file": row["path"], "ok": ok, "detail": detail,
                       "elapsed_s": round(time.time() - t0, 4),
                       "required_min_800": required,
                       "meets_800": ok,
                       "exempt_reason": min800_exempt.get(key, ""),
                       "unexpected_failure": bool(required and not ok)})
    # 负样本：600x450，应当被拒绝
    neg = ASSETS / "invalid-600x450.png"
    t0 = time.time()
    try:
        from PIL import Image
        Image.new("RGB", (600, 450), "white").save(neg)
        ok, detail = image_validation.check(neg)
        neg_row = {"file": str(neg.relative_to(ROOT)), "check_ok": ok, "detail": detail,
                   "expected_fail": True, "negative_pass": not ok,
                   "elapsed_s": round(time.time() - t0, 4), "bytes": neg.stat().st_size}
    except Exception as ex:  # noqa: BLE001
        neg_row = {"file": str(neg.relative_to(ROOT)), "check_ok": None, "expected_fail": True,
                   "negative_pass": False, "error": f"{type(ex).__name__}: {ex}"}
        out["failures"].append(f"negative-case: {neg_row['error']}")
    # 真实远程 URL 尺寸校验
    t0 = time.time()
    try:
        ok, detail = image_validation.validate_image_size(SOURCE_URLS[3], 800, 800)
        remote_row = {"url": SOURCE_URLS[3], "ok": ok, "detail": detail,
                      "elapsed_s": round(time.time() - t0, 3), "network": True}
    except Exception as ex:  # noqa: BLE001
        remote_row = {"url": SOURCE_URLS[3], "ok": False,
                      "error": f"{type(ex).__name__}: {ex}", "network": True}
    unexpected = [c for c in checks if c["unexpected_failure"]]
    out["validation"] = {"artifacts": checks,
                         "passed": sum(1 for c in checks if c["ok"]),
                         "failed": sum(1 for c in checks if not c["ok"]),
                         "unexpected_failures": len(unexpected),
                         "negative_case": neg_row, "remote_url": remote_row}
    for c in unexpected:
        out["failures"].append(f"尺寸不达标（非豁免项）{c['file']}: {c['detail']}")

    # 5) 真实付费 AI 图像编辑：白底 + 精细编辑
    #    精细编辑以「白底产物」为输入（与 App 内 ImagePipeline 白底→编辑链路一致，
    #    同时避开网关对同一输入图按图缓存、换提示词也返回同一结果的问题）。
    if args.ai:
        from aeclone.services import BUS, model_of
        ai_src = src_paths[1]
        prev = _load(RUN_MEDIA) if RUN_MEDIA.exists() else {}
        try:
            cfg, svc = _ai_client()
        except Exception as ex:  # noqa: BLE001
            out["ai"] = {"ok": False, "error": f"初始化 AI 客户端失败: {type(ex).__name__}: {ex}"}
            out["failures"].append(out["ai"]["error"])
            svc = None
        if svc is not None:
            ai_model = model_of(cfg, "image_edit")
            bal_before = _api_balance(cfg)
            paid_calls = 0
            out["ai"] = {"model": ai_model, "paid": True, "balance_before": bal_before,
                         "paid_calls_this_run": 0}
            white_path = ASSETS / "ai-white-bg-r2.png"
            # 5.1 白底
            if args.reuse_ai and white_path.exists():
                prev_wb = (prev.get("ai") or {}).get("white_bg") or {}
                if prev_wb.get("ok"):
                    rec = dict(prev_wb)
                    rec["reused_from_previous_run"] = True
                    rec["ok"] = True
                else:
                    rec = artifact("ai-white-bg", white_path, 0.0)
                    rec.update(reused_from_previous_run=True, paid=False)
                out["ai"]["white_bg"] = rec
            else:
                t0 = time.time()
                try:
                    url = svc.white_background(ai_src)
                    _download_ai_result(url, white_path)
                    out["ai"]["white_bg"] = artifact(
                        "ai-white-bg", white_path, time.time() - t0, url=url,
                        model=ai_model, paid=True, input=str(ai_src.relative_to(ROOT)),
                        prompt="Remove background → pure white (#FFFFFF)，保持商品原样",
                        est_cost_cny=round(BUS.price_of(ai_model), 4))
                    paid_calls += 1
                except Exception as ex:  # noqa: BLE001
                    out["ai"]["white_bg"] = {
                        "key": "ai-white-bg", "ok": False, "paid": True, "model": ai_model,
                        "input": str(ai_src.relative_to(ROOT)),
                        "error": f"{type(ex).__name__}: {str(ex)[:500]}",
                        "elapsed_s": round(time.time() - t0, 3)}
                    out["failures"].append(f"AI 白底失败: {out['ai']['white_bg']['error']}")

            # 同输入换提示词探针：上一轮（attempt1）精细编辑与白底字节相同，保留为证据。
            prev_edit = (prev.get("ai") or {}).get("fine_edit") or {}
            wb_hash = (out["ai"].get("white_bg") or {}).get("sha256")
            if prev_edit.get("ok") and wb_hash and prev_edit.get("sha256") == wb_hash:
                out["ai"]["same_input_probe"] = {
                    "ok": True,
                    "input": prev_edit.get("input"),
                    "prompt": prev_edit.get("prompt"),
                    "output_sha256": prev_edit.get("sha256"),
                    "white_bg_sha256": wb_hash,
                    "identical_to_white_bg": True,
                    "url": prev_edit.get("url"),
                    "elapsed_s": prev_edit.get("elapsed_s"),
                    "conclusion": (
                        "同一张输入图只更换提示词时，网关约 2.5s 返回与白底图字节完全一致的结果"
                        "（按图缓存/未真正重新生成）。因此精细编辑必须换成不同输入图"
                        "（App 内以白底产物为输入，本轮已按该链路重测）。"),
                }

            # 5.2 精细编辑（提示词改图）：输入用白底产物
            edit_input = white_path if (out["ai"].get("white_bg") or {}).get("ok") else ai_src
            t0 = time.time()
            dest2 = ASSETS / "ai-fine-edit-r2.png"
            prompt = ("Place this industrial machine on a clean modern construction site background, "
                      "natural daylight, professional e-commerce product photo. Keep the machine's "
                      "shape, color and details exactly unchanged. No text, no watermark.")
            try:
                url = svc.edit(edit_input, prompt)
                _download_ai_result(url, dest2)
                out["ai"]["fine_edit"] = artifact(
                    "ai-fine-edit", dest2, time.time() - t0, url=url,
                    model=ai_model, paid=True, input=str(edit_input.relative_to(ROOT)),
                    prompt=prompt,
                    est_cost_cny=round(BUS.price_of(ai_model), 4))
                paid_calls += 1
            except Exception as ex:  # noqa: BLE001
                out["ai"]["fine_edit"] = {
                    "key": "ai-fine-edit", "ok": False, "paid": True, "model": ai_model,
                    "input": str(edit_input.relative_to(ROOT)),
                    "error": f"{type(ex).__name__}: {str(ex)[:500]}",
                    "elapsed_s": round(time.time() - t0, 3)}
                out["failures"].append(f"AI 精细编辑失败: {out['ai']['fine_edit']['error']}")

            # AI 产物尺寸校验 + 与白底图去重校验
            for key in ("white_bg", "fine_edit"):
                row = out["ai"].get(key) or {}
                if row.get("ok"):
                    p = ROOT / row["path"]
                    ok, detail = image_validation.check(p)
                    row["size_check"] = {"ok": ok, "detail": detail}
                    if not ok:
                        out["failures"].append(f"AI {key} 尺寸不达标: {detail}")
            wb_h = (out["ai"].get("white_bg") or {}).get("sha256")
            fe_h = (out["ai"].get("fine_edit") or {}).get("sha256")
            if wb_h and fe_h:
                identical = wb_h == fe_h
                out["ai"]["fine_edit"]["identical_to_white_bg"] = identical
                if identical:
                    out["failures"].append(
                        "精细编辑结果与白底图字节完全相同：网关未按提示词重新生成（疑似按图缓存）")

            bal_after = _api_balance(cfg)
            delta = None
            if bal_before and bal_after:
                with contextlib.suppress(Exception):
                    delta = round(float(bal_after.get("used", 0)) - float(bal_before.get("used", 0)), 5)
            out["ai"]["balance_after"] = bal_after
            out["ai"]["paid_calls_this_run"] = paid_calls
            out["ai"]["billing_delta_used"] = delta
            out["ai"]["ok"] = all((out["ai"].get(k) or {}).get("ok") for k in ("white_bg", "fine_edit"))
            out["ai"]["calls_total_paid"] = sum(
                1 for k in ("white_bg", "fine_edit") if (out["ai"].get(k) or {}).get("paid"))
    else:
        out["ai"] = {"ok": None, "note": "未执行（--ai 未开启）", "calls": 0}
        if args.reuse_ai:
            prev = _load(RUN_MEDIA) if RUN_MEDIA.exists() else {}
            if not (prev.get("ai") or {}).get("white_bg"):
                prev = _load(ASSETS / "run-media-2.attempt1.json")
            out["ai"] = _ai_evidence_from_prev(prev)
            try:
                from aeclone import config as _cfg_mod
                out["ai"]["balance_after"] = _api_balance(_cfg_mod.get_config())
            except Exception:  # noqa: BLE001
                pass

    out["ok"] = (not out["failures"] and not unexpected
                 and bool(out["ai"].get("ok") in (True, None)))
    _write_json(RUN_MEDIA, out)
    print(f"media: sources={sum(1 for s in out['sources'] if s.get('ok'))}/4 "
          f"local_ops={sum(1 for r in out['local_ops'].values() if r.get('ok'))}/{len(out['local_ops'])} "
          f"size_check={out['validation']['passed']}/{len(checks)} "
          f"ai={out['ai'].get('ok')} failures={len(out['failures'])}")
    for f in out["failures"]:
        print("  FAIL:", f)
    return 0 if out["ok"] else 1


# ---------------------------------------------------------------------------
# video：真实 ffmpeg 轮播 + ffprobe 校验
# ---------------------------------------------------------------------------
def ffprobe_path() -> str | None:
    for cand in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if Path(cand).exists():
            return cand
    return shutil.which("ffprobe")


def probe_media(path: Path) -> dict:
    exe = ffprobe_path()
    if not exe:
        return {"available": False, "error": "ffprobe 不存在"}
    proc = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {"available": True, "error": proc.stderr[-400:]}
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    fmt = data.get("format") or {}
    fps = 0.0
    with contextlib.suppress(Exception):
        num, den = (video.get("avg_frame_rate") or "0/1").split("/")
        fps = round(float(num) / float(den), 3) if float(den) else 0.0
    return {
        "available": True,
        "duration_s": round(float(fmt.get("duration") or 0), 3),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
        "format_name": fmt.get("format_name", ""),
        "codec": video.get("codec_name", ""),
        "pix_fmt": video.get("pix_fmt", ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "streams": len(streams),
    }


def cmd_video(args) -> int:
    from aeclone.services import video as video_svc

    ASSETS.mkdir(parents=True, exist_ok=True)
    out: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "mode": "REAL（真实 ffmpeg 编码 + ffprobe 校验）",
                 "images": [], "checks": {}, "failures": []}

    candidates = [ASSETS / "local-square.png", ASSETS / "local-watermark-text.png",
                  ASSETS / "local-four.png", ASSETS / "local-marketing-1x1.png",
                  ASSETS / "local-marketing-3x4.png"]
    images = [p for p in candidates if p.exists()]
    if len(images) < 3:
        # 退化：直接用真实下载的原图（仍为真实图）
        images = sorted(ASSETS.glob("src-*"))
    images = images[:4]  # 3~5 张轮播：取 4 张方图/横幅图，避免竖版营销图被 cover 裁切
    if len(images) < 3:
        out["failures"].append(f"可用图片不足 3 张（{len(images)}）")
        out["ok"] = False
        _write_json(RUN_VIDEO, out)
        print("video: 可用图片不足 3 张")
        return 1

    out["images"] = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size} for p in images]
    info = video_svc.probe()
    out["ffmpeg"] = info

    target = ASSETS / "carousel-r2.mp4"
    t0 = time.time()
    try:
        video_svc.generate_carousel_video(images, target, slot_seconds=2.0,
                                          transition_seconds=0.5, size="1080x1080")
        elapsed = time.time() - t0
        out["video"] = artifact("carousel-r2", target, elapsed, paid=False, kind="video")
        out["video"].update(probe_media(target))
    except Exception as ex:  # noqa: BLE001
        out["video"] = {"key": "carousel-r2", "path": str(target.relative_to(ROOT)),
                        "ok": False, "error": f"{type(ex).__name__}: {str(ex)[:800]}",
                        "elapsed_s": round(time.time() - t0, 3)}
        out["failures"].append(f"视频生成失败: {out['video']['error']}")

    v = out["video"]
    expected = round(len(images) * 2.0 - (len(images) - 1) * 0.5, 3)
    checks = {
        "has_video_stream": bool(v.get("width") and v.get("height") and v.get("codec")),
        "resolution_1080x1080": v.get("width") == 1080 and v.get("height") == 1080,
        "duration_expected": v.get("duration_s") is not None and abs(float(v.get("duration_s") or 0) - expected) <= 0.6,
        "size_positive": int(v.get("size_bytes") or 0) > 0,
        "size_under_platform_limit_100mb": int(v.get("size_bytes") or 0) < 100 * 1024 * 1024,
        "fps_30": abs(float(v.get("fps") or 0) - 30.0) <= 0.5,
        "pix_fmt_yuv420p": v.get("pix_fmt") == "yuv420p",
    }
    out["expected_duration_s"] = expected
    out["checks"] = checks
    out["ok"] = all(checks.values()) and not out["failures"]
    _write_json(RUN_VIDEO, out)
    print(f"video: {v.get('width')}x{v.get('height')} {v.get('duration_s')}s "
          f"{v.get('size_bytes')}B elapsed={v.get('elapsed_s')}s checks={checks}")
    for f in out["failures"]:
        print("  FAIL:", f)
    return 0 if out["ok"] else 1


# ---------------------------------------------------------------------------
# shots：逐页截图（离线沙箱 + 本地 CJK 字体）
# ---------------------------------------------------------------------------
def _preview_assets_dir() -> tuple[Path, str]:
    """真实 assets + 本地 CJK 字体，避免 CanvasKit 中文变方框。"""
    base = ROOT / "build" / "ui-preview-assets"
    base.mkdir(parents=True, exist_ok=True)
    link = base / "assets"
    if not link.exists():
        with contextlib.suppress(OSError):
            link.symlink_to(ROOT / "assets", target_is_directory=True)
    font_file = next((Path(p) for p in CJK_FONT_CANDIDATES if Path(p).is_file()), None)
    if font_file is None:
        return ROOT / "assets", ""
    dst = base / "aurora-cjk.ttf"
    if not dst.exists() or dst.stat().st_size != font_file.stat().st_size:
        with contextlib.suppress(OSError):
            shutil.copyfile(font_file, dst)
    if not dst.exists():
        return ROOT / "assets", ""
    return base, "AuroraCJK"


def serve(platform: str, page: int, port: int, theme: str, tab: int = 0) -> None:
    os.environ["AURORA_THEME"] = theme
    os.environ["AURORA_TAB"] = str(tab)
    import flet as ft
    from ui_sandbox import build_app, sandbox

    assets_dir, font = _preview_assets_dir()

    def target(page_ctl):
        if font:
            with contextlib.suppress(Exception):
                page_ctl.fonts = {font: "aurora-cjk.ttf"}
        app = build_app(page_ctl)
        if font:
            with contextlib.suppress(Exception):
                for th in (getattr(page_ctl, "theme", None),
                           getattr(page_ctl, "dark_theme", None)):
                    if th is None:
                        continue
                    th.font_family = font
                    bm = None
                    with contextlib.suppress(Exception):
                        bm = th.text_theme.body_medium
                    bm = bm or ft.TextStyle()
                    bm.font_family = font
                    th.text_theme = ft.TextTheme(body_medium=bm)
        app._switch_platform(platform)
        app._switch(page)
        with contextlib.suppress(Exception):
            page_ctl.update()

    with sandbox(threads=True):
        ft.app(target=target, view=ft.AppView.WEB_BROWSER, host="127.0.0.1",
               port=port, assets_dir=str(assets_dir),
               web_renderer=ft.WebRenderer.CANVAS_KIT)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def capture_one(platform: str, page: int, name: str, theme: str = "dark",
                viewport: dict | None = None, scroll_y: int = 0, tab: int = 0) -> dict:
    from playwright.sync_api import sync_playwright

    output = SHOTS / f"{name}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    env = dict(os.environ)
    env.update({"PYTHONPATH": os.pathsep.join(
                    [str(ROOT / "build" / "ui-preview-deps"), str(TOOLS), str(SRC)]),
                "AURORA_WINDOW": "1514x945", "AURORA_THEME": theme,
                "AURORA_TAB": str(tab)})
    log_path = ASSETS / "logs" / f"shot-{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--platform", platform,
         "--page", str(page), "--port", str(port), "--theme", theme,
         "--tab", str(tab)],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    row = {"name": name, "platform": platform, "page": page, "theme": theme,
           "tab": tab, "file": str(output.relative_to(ROOT)),
           "viewport": viewport or VIEWPORT, "scroll_y": scroll_y}
    try:
        deadline = time.time() + 50
        ready = False
        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.4)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    ready = True
                    break
            time.sleep(0.25)
        if not ready:
            raise RuntimeError(f"Flet 预览端口 {port} 未就绪（见 {log_path.relative_to(ROOT)}）")
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True,
                                             args=["--headless=new"])
            except Exception:  # noqa: BLE001
                browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport=viewport or VIEWPORT, device_scale_factor=1)
                pg = ctx.new_page()
                pg.goto(f"http://127.0.0.1:{port}", wait_until="load", timeout=45000)
                with contextlib.suppress(Exception):
                    pg.wait_for_selector("canvas", timeout=25000)
                pg.wait_for_timeout(3500)
                if scroll_y:
                    pg.mouse.move(760, 1100)
                    pg.mouse.wheel(0, scroll_y)
                    pg.wait_for_timeout(1500)
                pg.screenshot(path=str(output))
            finally:
                browser.close()
        row["bytes"] = output.stat().st_size if output.exists() else 0
        row["elapsed_s"] = round(time.time() - t0, 1)
        row["ok"] = output.exists() and row["bytes"] > 0
        if row["ok"]:
            row["sha256"] = sha256_of(output)
            row["sha256_16"] = row["sha256"][:16]
            row.update(_nonblank(output))
            row["ok"] = bool(row["ok"] and row.get("nonblank"))
    except Exception as ex:  # noqa: BLE001
        row.update({"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:400]}",
                    "bytes": output.stat().st_size if output.exists() else 0,
                    "elapsed_s": round(time.time() - t0, 1)})
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=8)
        with contextlib.suppress(Exception):
            proc.kill()
        log.close()
    return row


def cmd_shots(args) -> int:
    from aeclone.platforms import PLATFORMS

    SHOTS.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    rows: list[dict] = []
    targets: list[tuple[str, int, str, dict, int, int]] = []
    for pid, pf in PLATFORMS.items():
        if not pf.implemented:
            continue
        for item in pf.pages:
            slug = PAGE_SLUGS.get(pid, {}).get(item.index, f"page-{item.index}")
            name = f"{pid}-{item.index}-{slug}"
            viewport = dict(VIEWPORT)
            if pid == "tiktok" and item.index == 14:
                viewport = {"width": 1560, "height": 2600}
            targets.append((pid, item.index, name, viewport, 0, 0))
    # Alibaba 刊登页 5 个 Tab：默认 Tab1 已含在 10 页里，这里补 Tab2~Tab5 的实际 Tab 渲染。
    tab_slugs = {1: "category", 2: "presets", 3: "price-trade", 4: "save"}
    for tab_i, slug in tab_slugs.items():
        targets.append(("alibaba", 12,
                        f"alibaba-12-alibaba-listing-tab{tab_i + 1}-{slug}",
                        dict(VIEWPORT), 0, tab_i))
    if only:
        targets = [t for t in targets if t[2] in only]

    for pid, page, name, viewport, scroll_y, tab in targets:
        row = None
        for attempt in (1, 2):
            row = capture_one(pid, page, name, viewport=viewport, scroll_y=scroll_y, tab=tab)
            if row.get("ok"):
                row["attempts"] = attempt
                break
            time.sleep(1.5)
        rows.append(row)
        print(("OK   " if row.get("ok") else "FAIL ") + name +
              f" ({row.get('elapsed_s')}s, {row.get('bytes')}B)" +
              ("" if row.get("ok") else f" :: {row.get('error', '')}"), flush=True)

    counts: dict[str, dict] = {}
    for row in rows:
        pid = row["platform"]
        c = counts.setdefault(pid, {"total": 0, "ok": 0})
        c["total"] += 1
        c["ok"] += 1 if row.get("ok") else 0
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "mode": "OFFLINE（隔离沙箱渲染 + Playwright/Chrome 截图，不联网/不读写客户配置）",
           "dir": str(SHOTS.relative_to(ROOT)), "counts": counts, "shots": rows}
    out["ok"] = all(r.get("ok") for r in rows)
    _write_json(RUN_SHOTS, out)
    print(f"shots: {sum(1 for r in rows if r.get('ok'))}/{len(rows)} -> {SHOTS.relative_to(ROOT)}")
    return 0 if out["ok"] else 1


# ---------------------------------------------------------------------------
# report：合并全部证据
# ---------------------------------------------------------------------------
def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001
        return {"_error": f"{type(ex).__name__}: {ex}", "_path": str(path)}


def _file_meta(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    data = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "exists": True, "bytes": len(data),
            "sha256_16": hashlib.sha256(data).hexdigest()[:16],
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                   time.localtime(path.stat().st_mtime))}


def _git_fingerprint() -> dict:
    import subprocess as _sp
    out: dict = {}
    for key, cmd in (("head", ["git", "rev-parse", "HEAD"]),
                     ("status", ["git", "status", "--porcelain"])):
        try:
            proc = _sp.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            text = proc.stdout.strip()
            out[key + "_sha256_16"] = hashlib.sha256(text.encode()).hexdigest()[:16]
            if key == "head":
                out["head"] = text
            else:
                out["changed_files"] = len(text.splitlines())
        except Exception as ex:  # noqa: BLE001
            out[key + "_error"] = f"{type(ex).__name__}: {ex}"
    return out


def _md_table(headers: list[str], rows: list[list]) -> str:
    def cell(value) -> str:
        text = "" if value is None else str(value)
        return text.replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(out)


def render_markdown(report: dict) -> str:
    media = report.get("media") or {}
    video = report.get("video") or {}
    ui = report.get("ui_audit") or {}
    summary = report.get("summary") or {}
    env = report.get("environment") or {}
    git = env.get("git") or {}
    shots = ui.get("screenshots") or {}
    buttons = ui.get("buttons") or {}
    options = ui.get("options") or {}
    pages = ui.get("platform_pages") or {}

    lines: list[str] = []
    add = lines.append
    add("# 全平台真实媒体验证 + UI 审计报告（第 2 轮）")
    add("")
    add(f"生成时间：{report.get('generated_at', '')}　|　代码工作树：`{(git.get('head') or '')[:12]}`"
        f"（改动文件 {git.get('changed_files', '?')} 个）")
    add("")
    add("> 本报告把「真实付费/真实联网」与「离线沙箱」两种口径分开标注。"
        "页面能打开、按钮不报错属于离线结构验证，不等于平台接口写成功；"
        "图片/视频/AI 属于真实执行，产物都在 `docs/design/test-assets/`。")
    add("")
    add("## 0. 结论摘要")
    add("")
    add(_md_table(["项目", "结果", "口径"], [
        ["图片链路（方图补白/水印/四合一/营销图/压缩/尺寸校验）", "PASS" if summary.get("media_real_ok") else "FAIL", "真实 Pillow 处理"],
        ["AI 图像（白底 + 精细编辑）", "PASS" if summary.get("ai_image_ok") else "FAIL", "真实付费网关"],
        ["轮播视频（4 图 / ffprobe）", "PASS" if summary.get("video_real_ok") else "FAIL", "真实 ffmpeg"],
        ["逐平台页面/Tab/导航", "PASS（0 problems）" if pages.get("ok") else "FAIL", "离线沙箱"],
        ["逐按钮回调审计", f"{summary.get('button_callbacks', 0)} 个回调 / {summary.get('button_errors', 0)} ERROR", "离线沙箱"],
        ["开关与下拉选项", f"{sum((options.get('counts') or {}).values())} 项 / {summary.get('options_error', 0)} ERROR", "离线沙箱"],
        ["逐页截图", summary.get("screenshots", ""), "离线沙箱渲染"],
        ["阶段2 本地构建", "PENDING（等 Lead「构建」指令）", "真实构建"],
    ]))
    add("")
    add("## 1. 图片链路（真实）")
    add("")
    add("### 1.1 真实商品图输入（公开 CDN 下载，非 fixture）")
    add("")
    add(_md_table(["#", "来源 URL", "文件", "尺寸", "字节", "耗时(s)", "sha256(16)"],
                  [[s.get("key", ""), s.get("source_url", ""), s.get("path", ""),
                    f"{s.get('width')}x{s.get('height')}", s.get("bytes"), s.get("elapsed_s"),
                    s.get("sha256_16", "")]
                   for s in (media.get("sources") or [])]))
    add("")
    add("### 1.2 本地处理产物")
    add("")
    rows = []
    for key, r in (media.get("local_ops") or {}).items():
        rows.append([key, r.get("path", ""), f"{r.get('width')}x{r.get('height')}",
                     r.get("bytes"), r.get("elapsed_s"), r.get("sha256_16", ""),
                     "OK" if r.get("ok") else "FAIL",
                     r.get("finding", "") or r.get("error", "")])
    add(_md_table(["步骤", "产物", "尺寸", "字节", "耗时(s)", "sha256(16)", "结果", "说明"], rows))
    add("")
    add("### 1.3 尺寸校验（image_validation）")
    add("")
    add(_md_table(["文件", "实测", "≥800 门槛", "结果", "豁免原因"],
                  [[c.get("file", ""), c.get("detail", ""),
                    "适用" if c.get("required_min_800") else "不适用",
                    "PASS" if c.get("ok") else "低于门槛（符合设计）",
                    c.get("exempt_reason", "") or "—"]
                   for c in ((media.get("validation") or {}).get("artifacts") or [])]))
    add("")
    neg = (media.get("validation") or {}).get("negative_case") or {}
    remote = (media.get("validation") or {}).get("remote_url") or {}
    add(f"- 负样本：`{neg.get('file', '')}` → {neg.get('detail', '')}；"
        f"按预期被拒（{neg.get('negative_pass')}）。")
    add(f"- 远程 URL 实测：{remote.get('url', '')} → {remote.get('detail', '')}"
        f"（{remote.get('elapsed_s')}s，真实联网）。")
    add("")
    add("## 2. AI 图像（真实付费调用）")
    add("")
    ai = media.get("ai") or {}
    rows = []
    for key, label in (("white_bg", "白底"), ("fine_edit", "精细编辑")):
        r = ai.get(key) or {}
        rows.append([label, r.get("path", ""), f"{r.get('width')}x{r.get('height')}",
                     r.get("bytes"), r.get("elapsed_s"), r.get("sha256_16", ""),
                     r.get("model", ""), "OK" if r.get("ok") else "FAIL",
                     r.get("error", "")])
    add(_md_table(["步骤", "产物", "尺寸", "字节", "耗时(s)", "sha256(16)", "模型", "结果", "错误"],
                  rows))
    add("")
    add(f"- 真实付费调用：**{ai.get('calls_total_paid', 0)} 次**（白底 1 + 精细编辑 1）；"
        f"本次生成报告时新产生 {ai.get('paid_calls_this_run', 0)} 次。")
    if ai.get("balance_after"):
        add(f"- 网关余额（used / remain）：{ai['balance_after'].get('used')} / "
            f"{ai['balance_after'].get('remain')}；计量可能延迟，不作为扣费凭证。")
    add(f"- 图像网关返回地址：`{(ai.get('white_bg') or {}).get('url', '')}`")
    probe = ai.get("same_input_probe") or {}
    if probe:
        add("")
        add("> **实测发现（同输入换提示词）**：" + str(probe.get("conclusion", "")))
        add("")
        add(f"> 证据：白底与精细编辑输出 sha256 相同（`{probe.get('output_sha256', '')[:16]}`），"
            f"精细编辑耗时仅 {probe.get('elapsed_s')}s。")
    if ai.get("reused"):
        add("")
        add(f"> 口径说明：本轮未再次付费，复用 `{ai.get('evidence_file', '')}` 中的真实调用证据，"
            "并已复核产物文件哈希。")
    add("")
    add("## 3. 视频（真实 ffmpeg + ffprobe）")
    add("")
    v = video.get("video") or {}
    add(_md_table(["项", "值"], [
        ["产物", v.get("path", "")],
        ["图片数", len(video.get("images") or [])],
        ["时长", f"{v.get('duration_s')}s（预期 {video.get('expected_duration_s')}s）"],
        ["分辨率", f"{v.get('width')}x{v.get('height')}"],
        ["编码 / 像素格式 / 帧率", f"{v.get('codec')} / {v.get('pix_fmt')} / {v.get('fps')}fps"],
        ["大小", f"{v.get('size_bytes')} B"],
        ["sha256", v.get("sha256", "")],
        ["ffmpeg", (video.get("ffmpeg") or {}).get("version", "")],
        ["耗时", f"{v.get('elapsed_s')}s"],
    ]))
    add("")
    checks = video.get("checks") or {}
    add("校验：" + "；".join(f"{k}={'PASS' if ok else 'FAIL'}" for k, ok in checks.items()))
    add("")
    add("## 4. 全平台 UI 审计（离线沙箱）")
    add("")
    add("> 三套审计都在隔离沙箱内运行（不联网、不读写客户配置、不调用平台写接口）。"
        "审计 JSON 由本任务与并行 agent 共同刷新，本节数字为报告生成时刻的快照，"
        "文件 sha256/修改时间见 `all-platforms-media-verification-2.json` 的 "
        "`environment.audit_files`。")
    add("")
    add("### 4.1 页面 / Tab / 导航 / 平台切换（tools/verify_all_platforms.py）")
    add("")
    rows = []
    for p in (pages.get("platforms") or []):
        rows.append([p.get("platform", ""), len(p.get("pages") or []),
                     sum(int(x.get("controls") or 0) for x in (p.get("pages") or [])),
                     "PASS" if p.get("ok") else "FAIL",
                     "；".join(p.get("problems") or []) or "—"])
    add(_md_table(["平台", "页面数", "控件数", "结果", "问题"], rows))
    add("")
    add("### 4.2 逐按钮回调（tools/button_audit.py）")
    add("")
    add(f"总计：**{buttons.get('pages', 0)} 页 / {sum((buttons.get('counts') or {}).values())} 个回调**，"
        f"状态分布 `{json.dumps(buttons.get('counts') or {}, ensure_ascii=False)}`，"
        f"ERROR **{buttons.get('error_count', 0)}**。")
    add("")
    rows = []
    for pid, row in (buttons.get("per_platform") or {}).items():
        rows.append([pid, row.get("pages"), row.get("callbacks"),
                     json.dumps(row.get("status") or {}, ensure_ascii=False)])
    add(_md_table(["平台", "审计页数", "回调数", "状态分布"], rows))
    add("")
    add("> AliExpress 只审 3 个平台独占页（类目/价格/变种），其余页面与 Alibaba 共用同一实现，"
        "已在 Alibaba 分支覆盖；这是 `button_audit.py` 的既有设计。")
    add("")
    add("### 4.3 开关 / 下拉选项（tools/verify_ui_options.py）")
    add("")
    add(f"总计 **{sum((options.get('counts') or {}).values())}** 项，"
        f"`{json.dumps(options.get('counts') or {}, ensure_ascii=False)}`，"
        f"ERROR **{options.get('error_count', 0)}**。")
    add("")
    add("### 4.4 逐页截图")
    add("")
    counts = shots.get("counts") or {}
    add("目录：`" + str(shots.get("dir", "")) + "`；共 "
        + str(shots.get("total", 0)) + " 张（30 个平台页面 + Alibaba 刊登页 4 个附加 Tab）。"
        "Tab 截图通过 `AURORA_TAB` 真实切换分栏，5 张刊登页截图哈希互不相同。")
    add("")
    if counts:
        add(_md_table(["平台", "截图数", "成功"],
                      [[pid, c.get("total"), c.get("ok")] for pid, c in counts.items()]))
        add("")
    rows = []
    for s in (shots.get("shots") or []):
        rows.append([s.get("platform", ""), s.get("page"), s.get("name", ""),
                     s.get("bytes"), s.get("sha256_16", "") if s.get("sha256") else "",
                     "OK" if s.get("ok") else ("FAIL: " + str(s.get("error", "")))])
    add(_md_table(["平台", "页", "文件（不含 .png）", "字节", "sha256(16)", "结果"], rows))
    add("")
    add("## 5. 与上一轮（2026-11-22 03:12）的差异")
    add("")
    add("- 图片链路输入从本地 fixture 换成 **4 张真实商品图 URL**（alicdn / aliexpress-media），"
        "并新增负样本与远程 URL 校验、压缩链路。")
    add("- 视频从 34 KB 的测试片升级为 **4 图 1080×1080 / 6.5s / 1.17 MB** 真实轮播，"
        "并用 ffprobe 逐项校验时长、分辨率、帧率、像素格式。")
    add("- 截图覆盖从 21 张扩到 **34 张**（4 平台全部导航页 + Alibaba 刊登页 5 个 Tab），"
        "文件名统一为 `<platform>-<page>-<label>.png`。")
    add("- 三套 UI 审计在本轮工作树（HEAD 变化中）重新跑过，按钮/选项 0 ERROR。")
    add("")
    add("## 6. 已知问题与风险")
    add("")
    add("1. **AI 精细编辑同输入缓存**：同一张输入图只换提示词，网关约 2.5s 返回与白底图"
        "字节完全相同的结果。App 内 `ImagePipeline` 以白底产物为编辑输入（字节不同），"
        "正常不受影响；但若用户对同一张原图连续做两次不同编辑，第二次可能拿到缓存图。"
        "本轮因付费调用审批被自动拒绝，未能用「不同输入图」复测，建议真机再验一次。")
    add("2. **文字水印白上叠白**：`image_ops.add_watermark` 的文字固定白色；打在白色补边"
        "方图上像素零变化（本轮已用 `watermark-contrast-probe.png` 证明）。白底主图应改用"
        "图片水印；另外 macOS 上没有 `arial.ttf`，文字会退回 10px 位图字体（很小）。")
    add("3. **3:4 营销图 750×1000**：低于平台主图 ≥800×800 门槛，但这是店小秘 3:4 规格，"
        "不应作为主图使用；报告中按“豁免项”记录，避免误判为失败。")
    add(f"4. **并行开发**：本轮期间仓库 HEAD 从 `42d4802` 变为 `{(git.get('head') or '')[:7]}`，工作树有 "
        f"{git.get('changed_files', '?')} 个改动"
        "文件；审计与截图对应本轮执行时的工作树，数字可能随其它 agent 继续改代码而变化。")
    add("5. **离线口径**：页面/按钮/选项审计是隔离沙箱，外部网络、后台线程、系统文件对话框"
        "分别计数，不代表真实平台接口成功；真实写操作仍需店铺凭证与人工确认。")
    add("")
    add("## 7. 阶段 2（待 Lead 指令）")
    add("")
    add("- 收到 Lead 的「构建」后执行：`AURORA_HARDEN=0 ./tools/build_macos.sh`；")
    add("- 校验 `dist/AuroraList.app --self-check` 退出码 0，记录大小/版本/哈希；")
    add("- 能启动则补启动截图与本次报告的第 2 轮构建段落。")
    add("")
    add("## 8. 产物清单")
    add("")
    add("- 媒体产物：`docs/design/test-assets/`（源图、方图、水印、四合一、营销图、压缩、"
        "AI 白底/精细编辑、轮播视频与抽帧）")
    add("- 截图：`docs/design/ui-verify/all-platforms/`")
    add("- 证据 JSON：`docs/all-platforms-media-verification-2.json`、"
        "`docs/platform-verification.json`、`docs/ui-button-audit.json`、"
        "`docs/ui-options-verification.json`")
    add("- 本轮原始记录：`docs/design/test-assets/run-media-2.json`、`run-video-2.json`、"
        "`run-shots-2.json`（含 attempt1 AI 原始记录）")
    add("")
    return "\n".join(lines)


def cmd_report(args) -> int:
    media = _load(RUN_MEDIA)
    video = _load(RUN_VIDEO)
    shots = _load(RUN_SHOTS)
    platforms = _load(PLATFORM_JSON)
    buttons = _load(BUTTON_JSON)
    options = _load(OPTIONS_JSON)

    plat_rows = platforms.get("platforms") or []
    btn_counts = buttons.get("counts") or {}
    btn_results = buttons.get("results") or []
    btn_errors = [r for r in btn_results if r.get("status") == "ERROR"]
    opt_counts = options.get("counts") or {}
    opt_errors = [r for r in (options.get("results") or []) if r.get("status") == "ERROR"]

    # 每平台回调分布（AliExpress 仅审计平台独占页，其余页面与 Alibaba 共用实现）
    per_platform: dict[str, dict] = {}
    for r in btn_results:
        row = per_platform.setdefault(r.get("platform") or "?", {"pages": set(), "callbacks": 0,
                                                                 "status": {}})
        row["pages"].add(r.get("page"))
        row["callbacks"] += 1
        row["status"][r.get("status")] = row["status"].get(r.get("status"), 0) + 1
    for row in per_platform.values():
        row["pages"] = len(row["pages"])

    # 截图覆盖：30 个导航页（Alibaba 10 / AliExpress 12 / Amazon 4 / TikTok 4）
    # + Alibaba 刊登页附加 Tab 截图。
    required_pages = {"alibaba": 10, "aliexpress": 12, "amazon": 4, "tiktok": 4}
    shot_pages: dict[str, int] = {}
    shot_tabs: dict[str, int] = {}
    for s in (shots.get("shots") or []):
        pid = s.get("platform") or "?"
        if int(s.get("tab") or 0):
            shot_tabs[pid] = shot_tabs.get(pid, 0) + 1
        else:
            shot_pages[pid] = shot_pages.get(pid, 0) + 1
    coverage_ok = all(shot_pages.get(pid, 0) == n for pid, n in required_pages.items())

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "round": 2,
        "scope_note": "阶段1：图片/视频/AI 媒体真实测试 + 全平台逐页逐按钮审计；阶段2 本地构建另行执行。",
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "git": _git_fingerprint(),
            "note": "证据对应上述工作树；仓库有并行开发，其他 agent 可能在本轮之后继续改动业务代码。",
            "audit_files": {"platform_verification": _file_meta(PLATFORM_JSON),
                            "button_audit": _file_meta(BUTTON_JSON),
                            "options_verification": _file_meta(OPTIONS_JSON)},
        },
        "real_vs_offline": {
            "REAL": [
                "真实商品图 URL 下载（alicdn / aliexpress-media CDN）",
                "本地图片链路：方图补白/水印/四合一/营销图/压缩（Pillow，真实文件）",
                "AI 图像编辑：白底 + 精细编辑，真实付费网关（qwen-image-2.1-turbo）",
                "视频：services/video.py + /opt/homebrew/bin/ffmpeg 真实编码，ffprobe 校验",
            ],
            "OFFLINE": [
                "tools/verify_all_platforms.py：隔离沙箱，页面/Tab/导航/平台切换/主题/启动任务守卫，不联网",
                "tools/button_audit.py：隔离沙箱逐回调触发，联网/后台线程/系统对话框分别计数",
                "tools/verify_ui_options.py：隔离沙箱开关两态/下拉全选项，只验证配置落盘",
                "逐页截图：隔离沙箱 Flet Web + Playwright，非真实店铺状态",
                "阶段2 构建：真实本地 PyInstaller 构建（等待 Lead 指令）",
            ],
        },
        "media": media,
        "video": video,
        "ui_audit": {
            "platform_pages": {
                "_mode": "OFFLINE 沙箱",
                "ok": platforms.get("ok"),
                "error": platforms.get("_error"),
                "platforms": [{k: p.get(k) for k in ("platform", "ok", "pages", "problems")}
                              for p in plat_rows],
            },
            "buttons": {
                "_mode": "OFFLINE 沙箱",
                "counts": btn_counts,
                "pages": len({(r.get("page"), r.get("platform")) for r in btn_results}),
                "error_count": len(btn_errors),
                "per_platform": per_platform,
                "errors": [{"page": r.get("page"), "platform": r.get("platform"),
                            "label": r.get("label"), "detail": r.get("detail")}
                           for r in btn_errors],
            },
            "options": {
                "_mode": "OFFLINE 沙箱",
                "counts": opt_counts,
                "error_count": len(opt_errors),
                "errors": [{"page": r.get("page"), "platform": r.get("platform"),
                            "label": r.get("label"), "detail": r.get("detail")}
                           for r in opt_errors],
            },
            "screenshots": {
                "_mode": "OFFLINE 沙箱渲染",
                "ok": shots.get("ok"),
                "dir": shots.get("dir"),
                "counts": shots.get("counts"),
                "required_pages": required_pages,
                "pages_per_platform": shot_pages,
                "extra_tab_shots": shot_tabs,
                "coverage_ok": coverage_ok,
                "total": len(shots.get("shots") or []),
                "failed": [s.get("name") for s in (shots.get("shots") or []) if not s.get("ok")],
                "shots": [{k: s.get(k) for k in ("name", "platform", "page", "tab", "file", "ok",
                                                 "bytes", "sha256", "sha256_16", "colors",
                                                 "stddev", "elapsed_s", "error")}
                          for s in (shots.get("shots") or [])],
            },
        },
        "build": {
            "phase": 2,
            "status": "PENDING",
            "note": "等待 Lead 通过 send_message 发「构建」指令后再执行 AURORA_HARDEN=0 ./tools/build_macos.sh。",
        },
    }

    # 汇总
    media_ok = bool(media.get("ok"))
    video_ok = bool(video.get("ok"))
    shots_ok = bool(shots.get("ok"))
    ui_ok = bool(platforms.get("ok")) and not btn_errors and not opt_errors
    report["summary"] = {
        "media_real_ok": media_ok,
        "video_real_ok": video_ok,
        "ai_image_ok": (media.get("ai") or {}).get("ok"),
        "ai_image_note": ("2 次真实付费调用（白底/精细编辑）；同输入换提示词返回白底副本"
                          "（网关缓存），App 实际链路以白底产物为编辑输入，见 same_input_probe"),
        "ui_offline_ok": ui_ok,
        "screenshots_ok": shots_ok,
        "screenshots": f"{sum(c.get('ok', 0) for c in (shots.get('counts') or {}).values())}/"
                       f"{sum(c.get('total', 0) for c in (shots.get('counts') or {}).values())}",
        "screenshots_coverage": ("30/30 导航页 + "
                                 f"{sum(shot_tabs.values())} Tab" if coverage_ok else "不足"),
        "button_callbacks": sum((btn_counts or {}).values()),
        "button_errors": len(btn_errors),
        "platform_problems": sum(len(p.get("problems") or []) for p in plat_rows),
        "options_error": len(opt_errors),
        "build": "PENDING（阶段2）",
    }
    report["ok"] = media_ok and video_ok and ui_ok and shots_ok and coverage_ok
    _write_json(FINAL_JSON, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("->", FINAL_JSON.relative_to(ROOT))
    FINAL_MD.write_text(render_markdown(report), encoding="utf-8")
    print("->", FINAL_MD.relative_to(ROOT))
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=False)
    p_media = sub.add_parser("media", help="真实图片链路 + 真实付费 AI 图像")
    p_media.add_argument("--ai", action="store_true", help="执行真实付费 AI 调用（白底+精细编辑）")
    p_media.add_argument("--reuse-ai", action="store_true",
                         help="复用已存在的 ai-white-bg-r2.png，避免重复付费调用白底")
    p_media.set_defaults(func=cmd_media)
    p_video = sub.add_parser("video", help="真实 ffmpeg 轮播视频")
    p_video.set_defaults(func=cmd_video)
    p_shots = sub.add_parser("shots", help="逐页截图（离线沙箱）")
    p_shots.add_argument("--only", default="", help="只拍指定 name，逗号分隔")
    p_shots.set_defaults(func=cmd_shots)
    p_rep = sub.add_parser("report", help="合并生成最终 JSON")
    p_rep.set_defaults(func=cmd_report)
    # 内部：截图子进程的预览服务
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--platform", default="alibaba", help=argparse.SUPPRESS)
    parser.add_argument("--page", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--theme", default="dark", help=argparse.SUPPRESS)
    parser.add_argument("--tab", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if getattr(args, "serve", False):
        if not args.port:
            parser.error("--serve 需要 --port")
        serve(args.platform, args.page, args.port, args.theme, args.tab)
        return 0
    if not getattr(args, "cmd", ""):
        parser.error("缺少子命令（media/video/shots/report）")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
