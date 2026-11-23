"""Alibaba AI 规则：标题 ≤128、提示词用 Alibaba 专用、关键词至少 3 个。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import AppConfig  # noqa: E402
from aeclone.services import alibaba_prompts as AP  # noqa: E402
from aeclone.services.alibaba_payload import build_alibaba_save, validate_alibaba_save  # noqa: E402
from aeclone.services.text_pipeline import TextPipeline  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


class FakeApi:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, model, messages, max_tokens=0):
        # 校验 system prompt 是 Alibaba 专用（含 128 / Alibaba）
        sys_text = str(messages[0]["content"])
        assert "Alibaba" in sys_text and "128" in sys_text, sys_text[:300]
        return self.reply


def test_title_limit_and_prompt():
    cfg = AppConfig()
    tp = TextPipeline(cfg)
    tp.client = FakeApi("A" * 300)   # 模型故意返回超长标题
    preset = AP.resolve_preset("machinery_industrial")
    r = tp.make_alibaba_title("drilling machine", prompt=preset["title_prompt"], limit=128)
    check("标题生成成功", bool(r.ok), getattr(r, "error", ""))
    check("标题 ≤128 字符", len(str(r.value or "")) <= 128, len(str(r.value or "")))
    check("提示词是 Alibaba 专用", "Alibaba" in preset["title_prompt"])


def test_keywords_need_three():
    cfg = AppConfig()
    save = {
        "categoryId": "127726092", "fullCid": "201943703,1326,100010658,127726092",
        "subject": "DTH drilling rig for mining", "keywords": "drilling rig",
        "mainImage": "https://x/a.jpg", "priceType": "fob_price",
        "sourcingTrade": '{"fob_min_price": 1000, "fob_max_price": 2000, "fob_currency": "USD"}',
    }
    issues = validate_alibaba_save({}, save, cfg, [], [])
    check("关键词不足 3 个被拦截", any("至少 3 个" in x for x in issues), issues)


def main():
    print("=" * 60)
    test_title_limit_and_prompt()
    test_keywords_need_three()
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
