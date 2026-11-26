"""AI 模型槽位：默认值、逐任务选择、选择后不回退。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig  # noqa: E402
from aeclone.services import (FIXED_MODELS, AI_MODEL_KEYS, model_of,  # noqa: E402
                              ImageService, FALLBACK_IMAGE_MODEL)

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


class FakeImages:
    def __init__(self):
        self.calls = []

    def images_edit(self, model, prompt, files, size="1024x1024", allow_fallback=True):
        self.calls.append((model, allow_fallback))
        raise RuntimeError("timeout")


def main():
    print("=" * 60)
    cfg = AppConfig()
    check("默认文本模型", model_of(cfg, "text") == FIXED_MODELS["text"], model_of(cfg, "text"))
    check("默认视觉模型", model_of(cfg, "vision") == FIXED_MODELS["vision"])
    cfg.set("ai_model_text", "qwen-turbo")
    check("文本模型可覆盖", model_of(cfg, "text") == "qwen-turbo", model_of(cfg, "text"))
    cfg = AppConfig()
    cfg.set("ai_model_image_edit", "qwen-image-3.0")
    cfg.set("ai_model_image_prompt", "")
    cfg.set("ai_model_marketing", "")
    check("图片编辑选择生效", model_of(cfg, "image_edit") == "qwen-image-3.0")
    check("提示词改图跟随图片编辑", model_of(cfg, "image_prompt") == "qwen-image-3.0")
    check("营销图跟随图片编辑", model_of(cfg, "marketing") == "qwen-image-3.0")
    cfg.set("ai_model_image_prompt", "qwen-image-2.1-turbo")
    check("提示词改图可独立覆盖", model_of(cfg, "image_prompt") == "qwen-image-2.1-turbo")
    check("营销图仍跟随提示词改图", model_of(cfg, "marketing") == "qwen-image-2.1-turbo")
    # 默认图片模型失败允许兜底；客户自选模型失败必须报错，不能静默换模型
    fake = FakeImages()
    svc = ImageService(fake, AppConfig())
    try:
        svc._img_edit("x", [Path("/tmp/x.png")])
    except RuntimeError:
        pass
    check("默认图片模型允许降级", len(fake.calls) >= 1 and fake.calls[0][0] == FIXED_MODELS["image_edit"])
    fake = FakeImages()
    cfg = AppConfig(); cfg.set("ai_model_image_edit", "qwen-image-3.0")
    svc = ImageService(fake, cfg)
    try:
        svc._img_edit("x", [Path("/tmp/x.png")])
    except RuntimeError:
        pass
    check("自选图片模型不静默降级", len(fake.calls) == 1 and fake.calls[0] == ("qwen-image-3.0", False), fake.calls)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
