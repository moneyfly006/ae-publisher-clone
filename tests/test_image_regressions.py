"""图片处理回归：开关不勾选不能调用 AI；指定编辑必须进任务表。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services.image_jobs import merged_jobs  # noqa: E402
from aeclone.services.publisher import Publisher  # noqa: E402

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name} {extra}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update({
            "ai_image_enable": True, "white_bg_enable": False, "watermark_enable": False,
            "img_main_edit": False, "edit_prompt": "", "background_gen_rules": "",
            "white_background_pic_1": "1", "white_background_pic_2": "3",
            "remove_watermark_pic_1": "1", "remove_watermark_pic_2": "5",
            "single_edit_main_pic_indices": "",
            "img_detail_white_bg": False, "img_detail_remove_wm": False, "img_detail_edit": False,
            "translate_detail_pic_enable": False, "translate_main_pic_enable": False,
            "white_background_sku_pic": False, "watermark": "", "four_in_one": False,
            "image_simple_rules": "[]", "image_ai_jobs": "[]",
            "designated_edit_enable": False, "designated_edit_indices": "",
            "designated_edit_prompt": "", "img_fine_main_enable": True,
            "img_fine_detail_enable": False, "img_fine_sku_enable": False,
        })
        self.__dict__.update(kw)


class Res:
    def __init__(self):
        self.steps = []


print("=" * 60)
print("图片开关回归")

# 1) 全关：不调用任何 AI
cfg = Cfg()
pub = Publisher.__new__(Publisher)
pub.cfg = cfg
calls = []
pub._process_urls = lambda urls, **kw: (calls.append(kw), (urls, 0))[1]
Publisher._ai_images(pub, {"images": ["u1", "u2"]}, Res())
chk("未勾选白底/去水印时不调用 AI", not calls, str(calls))

# 2) 只开白底：remove_wm 必须为 False
cfg = Cfg(white_bg_enable=True)
pub = Publisher.__new__(Publisher)
pub.cfg = cfg
calls = []
pub._process_urls = lambda urls, **kw: (calls.append(kw), (urls, 1))[1]
Publisher._ai_images(pub, {"images": ["u1", "u2"]}, Res())
chk("只开白底时 remove_wm=False", calls and calls[0].get("remove_wm") is False,
    str(calls[0] if calls else "无调用"))
chk("只开白底时 white_bg=True", calls and calls[0].get("white_bg") is True)

# 3) 只开去水印：white_bg 必须为 False
cfg = Cfg(watermark_enable=True)
pub = Publisher.__new__(Publisher)
pub.cfg = cfg
calls = []
pub._process_urls = lambda urls, **kw: (calls.append(kw), (urls, 1))[1]
Publisher._ai_images(pub, {"images": ["u1"]}, Res())
chk("只开去水印时 white_bg=False", calls and calls[0].get("white_bg") is False)

# 4) 主图精细编辑没提示词：不应调用（会白花钱且图片不变）
cfg = Cfg(img_main_edit=True, edit_prompt="")
pub = Publisher.__new__(Publisher)
pub.cfg = cfg
calls = []
pub._process_urls = lambda urls, **kw: (calls.append(kw), (urls, 0))[1]
Publisher._ai_images(pub, {"images": ["u1"]}, Res())
chk("精细编辑无提示词时不调用", not calls, str(calls))

# 5) 指定编辑必须进任务表（即使已有简单规则）
cfg = Cfg(image_simple_rules='[{"group":"main","range":"1-2","ops":{"white":true}}]',
          designated_edit_enable=True, designated_edit_indices="1",
          designated_edit_prompt="换成木头背景")
rules, jobs = merged_jobs(cfg)
chk("简单规则仍在", len(rules) == 1, f"rules={len(rules)}")
chk("指定编辑生成任务", any(j["group"] == "main" and j["index"] == 1 and j["mode"] == "edit"
                            for j in jobs), f"jobs={len(jobs)}")
chk("任务带着提示词", jobs and jobs[0]["prompt"] == "换成木头背景")

# 6) 模板缩略图：没有传入图片时，用保存过的 template_image_url
from unittest.mock import MagicMock  # noqa: E402

import flet as ft  # noqa: E402

from aeclone.ui.app import App  # noqa: E402


class AStub(App):
    def __getattr__(self, n):
        return MagicMock()


a = object.__new__(AStub)
a.cfg = Cfg(template_image_url="https://x/t.jpg", template_product_title="模板产品",
            template_product_id="123", template_snapshot="{}", template_values="{}")
seen = []
a._zoom_img = lambda src, *x, **k: (seen.append(src) or ft.Text("img"))
App._template_preview_row(a)
chk("模板缩略图使用持久化的首图", any("t.jpg" in str(x) for x in seen), str(seen))

# 7) 模型选择：提示词类编辑必须用 qwen-image-3.0，简单处理仍用 turbo
from aeclone.services import model_of  # noqa: E402

_m = Cfg()
chk("白底/去水印仍用 turbo", model_of(_m, "image_edit") == "qwen-image-2.1-turbo",
    model_of(_m, "image_edit"))
chk("提示词改图用 3.0", model_of(_m, "image_prompt") == "qwen-image-3.0",
    model_of(_m, "image_prompt"))

pub2 = Publisher.__new__(Publisher)
pub2.cfg = _m
chk("publisher._prompt_model = 3.0", Publisher._prompt_model(pub2) == "qwen-image-3.0")

print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)
