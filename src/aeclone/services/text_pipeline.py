"""AI 文本流水线：标题 / 描述 / SKU / 审查 / 估重。

对齐老软件行为：
- 标题：翻译 + 长度控制（默认 125 字符）+ 品牌词前加 for + 违禁词检查
- 描述：英文详情，含 HTML 包装
- SKU：批量优化，20 字符上限、仅 ASCII、去重
- 审查：按规则删除非商品信息
- 估重：看图输出重量/尺寸
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import TextService
from ..api import ApiClient
from ..config import AppConfig

BANNED_DEFAULT = ["cheap", "replica", "fake", "copy", "knockoff", "best price", "free shipping"]


@dataclass
class TextResult:
    ok: bool
    value: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def enforce_title_length(client: ApiClient, title: str, limit: int = 125) -> str:
    t = _clean(title).strip('"')
    if len(t) <= limit:
        return t
    try:
        short = client.chat("qwen-plus", [
            {"role": "system", "content": f"把下面的英文标题压缩到 {limit} 个字符以内，保留核心关键词和型号，只输出标题。"},
            {"role": "user", "content": t},
        ], max_tokens=200)
        short = _clean(short).strip('"')
        if 0 < len(short) <= limit:
            return short
    except Exception:
        pass
    cut = t[:limit]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def add_brand_for(title: str, brands: list[str]) -> str:
    for b in brands:
        if not b:
            continue
        if re.search(rf"\b{re.escape(b)}\b", title, re.I) and not re.search(rf"\bfor\s+{re.escape(b)}\b", title, re.I):
            title = re.sub(rf"\b{re.escape(b)}\b", f"for {b}", title, count=1, flags=re.I)
    return title


def find_banned(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if w and w.lower() in low]


def optimize_sku_names(raw: list[str], limit: int = 20) -> list[str]:
    out = []
    for s in raw:
        v = _clean(s)
        v = re.sub(r"[^\x20-\x7E]", "", v)            # 仅 ASCII
        v = re.sub(r"[^A-Za-z0-9 \-.()]", "", v)
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) > limit:
            v = v[:limit].rsplit(" ", 1)[0].strip()
        if v and v not in out:
            out.append(v)
    return out


class TextPipeline:
    def __init__(self, cfg: AppConfig, client: ApiClient | None = None):
        self.cfg = cfg
        self.client = client or ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
        self.text = TextService(self.client, cfg)

    # ---------- 标题 ----------
    def make_title(self, title: str, brands: list[str] | None = None) -> TextResult:
        try:
            en = self.text.translate_title(title)
            en = add_brand_for(_clean(en), brands or [])
            en = enforce_title_length(self.client, en, 125)
            notes = []
            banned = find_banned(en, BANNED_DEFAULT)
            if banned:
                notes.append("违禁词：" + ", ".join(banned))
            return TextResult(True, en, notes)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 描述 ----------
    def make_detail(self, title: str, attributes: dict | None = None) -> TextResult:
        try:
            base = self.text.write_detail(title)
            if attributes:
                bullets = "\n".join(f"- {k}: {v}" for k, v in list(attributes.items())[:20])
                base = f"{base}\n\nSpecifications:\n{bullets}"
            html = "<p>" + base.replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>"
            return TextResult(True, html, ["已包装为 HTML"])
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- SKU ----------
    def make_skus(self, names: list[str]) -> TextResult:
        try:
            got = self.text.optimize_skus(names)
            cleaned = optimize_sku_names(got or names)
            return TextResult(True, "\n".join(cleaned), [f"{len(cleaned)} 条"])
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 审查 ----------
    def review_text(self, content: str) -> TextResult:
        try:
            out = self.client.chat("qwen-plus", [
                {"role": "system", "content": self.cfg.review_text_prompt},
                {"role": "user", "content": content},
            ], max_tokens=1500)
            return TextResult(True, out)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 估重 ----------
    def estimate_weight(self, image_url: str, product_name: str = "") -> TextResult:
        try:
            out = self.client.chat("qwen3-vl-plus", [
                {"role": "system", "content": self.cfg.weight_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": product_name or "请估算该产品重量与尺寸"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]},
            ], max_tokens=300)
            return TextResult(True, out)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))
