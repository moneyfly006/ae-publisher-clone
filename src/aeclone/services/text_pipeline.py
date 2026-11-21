"""AI 文本流水线：标题 / 描述 / SKU / 审查 / 估重。

文本处理流程：
- 标题：翻译 + 长度控制（默认 125 字符）+ 品牌词前加 for + 违禁词检查
- 描述：英文详情，含 HTML 包装
- SKU：批量优化，20 字符上限、仅 ASCII、去重
- 审查：按规则删除非商品信息
- 估重：看图输出重量/尺寸
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import TextService, model_of
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
            cut = v[:limit]
            if " " in cut:
                v = cut.rsplit(" ", 1)[0].strip()
            else:
                v = cut.strip()
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
            prefix = (getattr(self.cfg, "title_prefix", "") or "").strip()
            suffix = (getattr(self.cfg, "title_suffix", "") or "").strip()
            if prefix:
                en = f"{prefix} {en}"
            if suffix:
                en = f"{en} {suffix}"
            en = enforce_title_length(self.client, en, 125)
            notes = []
            banned = find_banned(en, BANNED_DEFAULT)
            if banned:
                notes.append("违禁词：" + ", ".join(banned))
            return TextResult(True, en, notes)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 描述 ----------
    def make_detail(self, title: str, attributes: dict | None = None,
                    source_text: str = "", images: list[str] | None = None) -> TextResult:
        """生成详情：原文信息 + 原详情图（AI 决定图片放哪，再按合规尺寸输出）。

        images 是「原详情里的图片」，不是商品主图；保留它们，只做排版。
        """
        try:
            imgs = [str(u) for u in (images or []) if str(u).startswith("http")][:12]
            src = (str(source_text or "").strip())[:6000]
            user = f"Product title: {title}\n"
            if src:
                user += f"\nOriginal description (facts only, do not copy verbatim):\n{src}\n"
            if imgs:
                user += ("\nAvailable detail images (use placeholder <img data-detail-img=\"N\"> "
                         "where N is the 1-based index):\n")
                for i, _u in enumerate(imgs, 1):
                    user += f"{i}. image\n"
            user += "\n请重写为专业英文详情，并按上面的图片规则插入占位符。"
            base = self.client.chat(model_of(getattr(self, "cfg", None), "text"), [
                {"role": "system", "content": self.cfg.detail_prompt},
                {"role": "user", "content": user},
            ], max_tokens=2000)

            # 清理模型可能带出的代码块/样板块
            base = __import__("re").sub(r"```[a-zA-Z]*|```", "", base or "").strip()
            base = __import__("re").sub(r"<(style|script|iframe)[\s\S]*?</\1>", "", base, flags=__import__("re").I)
            if not base:
                raise RuntimeError("模型返回空内容")

            # 占位符 → 合规图片标签（宽 750 上限、自适应、居中、懒加载）
            def _tag(u: str) -> str:
                return ('<p style="text-align:center;margin:12px 0;">'
                        f'<img src="{u}" loading="lazy" '
                        'style="max-width:750px;width:100%;height:auto;display:block;margin:0 auto;" />'
                        '</p>')
            used = set()
            def _rep(m):
                try:
                    idx = int(m.group(1))
                except Exception:
                    return ""
                if 1 <= idx <= len(imgs):
                    used.add(idx)
                    return _tag(imgs[idx - 1])
                return ""
            html = __import__("re").sub(r'<img[^>]*data-detail-img=["\']?(\d+)["\']?[^>]*>', _rep, base)

            # 模型没按占位符放图：按段落自动均分插入（仍然保留原图）
            if imgs and not used:
                blocks = [_tag(u) for u in imgs]
                parts = [p for p in html.split("</p>") if p.strip()]
                step = max(1, len(parts) // max(1, len(blocks) + 1))
                out, bi = [], 0
                for i, seg in enumerate(parts, 1):
                    out.append(seg + "</p>")
                    if bi < len(blocks) and i % step == 0:
                        out.append(blocks[bi]); bi += 1
                out.extend(blocks[bi:])
                html = "".join(out)
            elif imgs and len(used) < len(imgs):
                # 有占位符但没用全：把剩下的均匀插到段落之间（不要全堆到末尾）
                rest = [u for i, u in enumerate(imgs, 1) if i not in used]
                parts = html.split("</p>")
                n = max(1, len(rest))
                step = max(1, len(parts) // (n + 1))
                for k, u in enumerate(rest):
                    idx = min(len(parts) - 1, step * (k + 1))
                    parts[idx] = parts[idx] + _tag(u)
                html = "</p>".join(parts)

            html = f'<div style="max-width:750px;margin:0 auto;font-size:14px;line-height:1.7;">{html}</div>'
            return TextResult(True, html, [f"图文详情，保留原图 {len(imgs)} 张"])
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    def filter_detail_images(self, urls: list[str]) -> set[int]:
        """一次视觉调用，返回「不合规」的图片序号（1 基）。失败则返回空集合。"""
        urls = [u for u in (urls or []) if str(u).startswith("http")][:12]
        if not urls:
            return set()
        try:
            from . import model_of as _mo
            content = [{"type": "text", "text": (
                "逐张检查这些商品详情图是否含有：联系方式（电话/邮箱/WhatsApp/微信）、"
                "二维码、促销返现、其他平台水印/Logo。"
                "只输出一行：不合法图片的序号，用逗号分隔，如 3,5；全部合规输出 NONE。")}]
            for u in urls:
                content.append({"type": "image_url", "image_url": {"url": u}})
            out = self.client.chat(_mo(self.cfg, "vision"),
                                   [{"role": "user", "content": content}], max_tokens=60)
            nums = __import__("re").findall(r"\d+", out or "")
            return {int(n) for n in nums if 1 <= int(n) <= len(urls)}
        except Exception:
            return set()

    # ---------- SKU ----------
    def make_skus(self, names: list[str]) -> TextResult:
        try:
            got = self.text.optimize_skus(names)
            cleaned = optimize_sku_names(got or names)
            return TextResult(True, "\n".join(cleaned), [f"{len(cleaned)} 条"])
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 关键词（Alibaba 3~5 个英文搜索词） ----------
    def make_keywords(self, title: str, detail_text: str = "", limit: int = 5) -> TextResult:
        """从标题/详情生成 Alibaba 用英文关键词（ASCII，短语优先）。

        商品原 keywords 常常是采集来的小语种（如葡语），直接提交会被平台判为
        非英文；开启 AI 时用这个方法生成英文搜索词。
        """
        prompt = str(getattr(self.cfg, "alibaba_keyword_prompt", "") or "").strip() or (
            "You are an Alibaba.com SEO expert. From the product title and details, "
            "output ONLY 3-5 English search keywords as a comma-separated list. "
            "Lowercase phrases, ASCII only, no brand names, no prices, no claims, "
            "no explanations.")
        try:
            from . import model_of
            detail_flat = re.sub(r"\s+", " ", str(detail_text or ""))[:1200]
            user = f"Title: {title}\nDetails: {detail_flat}"
            out = self.client.chat(model_of(self.cfg, "text"), [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ], max_tokens=140)
            parts = [p.strip() for p in re.split(r"[,;\n]", str(out or "")) if p.strip()]
            phrases: list[str] = []
            for p in parts:
                p = re.sub(r"[^A-Za-z0-9 \-]", " ", p)
                p = re.sub(r"\s+", " ", p).strip().lower()
                if p and p not in phrases:
                    phrases.append(p)
            phrases = phrases[:limit]
            if len(phrases) < 3:
                return TextResult(False, error=f"仅生成 {len(phrases)} 个英文关键词")
            return TextResult(True, ", ".join(phrases), [f"{len(phrases)} 个"])
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

    # ---------- Alibaba 必填属性建议（人工复核后写入预设） ----------
    def suggest_attributes(self, attr_defs: list[dict], title: str = "",
                           detail: str = "") -> TextResult:
        """为类目必填属性建议取值，返回 JSON 文本（不是最终配置）。

        只允许模型从候选值里选，避免编造规格；调用方必须让用户复核后再保存。
        """
        lines: list[str] = []
        for a in attr_defs or []:
            name = str(a.get("nameEn") or a.get("nameZh") or "").strip()
            if not name:
                continue
            vals = a.get("values") or []
            if isinstance(vals, str):
                # 店小秘 attributeList 的 values 是 JSON 字符串
                try:
                    import json as _json
                    vals = _json.loads(vals)
                except Exception:
                    vals = []
            names = []
            for v in vals if isinstance(vals, list) else []:
                if isinstance(v, dict):
                    nm = str(v.get("name_en") or v.get("name") or "").strip()
                else:
                    nm = str(v or "").strip()
                if nm:
                    names.append(nm)
            lines.append(f'- {name}: allowed = {", ".join(names[:15]) or "(free text)"}')
        if not lines:
            return TextResult(False, error="没有可建议的必填属性")
        prompt = (
            "You are an Alibaba.com product data assistant. Given the product title, "
            "details and the allowed values of each required attribute, return ONLY a "
            "JSON object that maps each attribute name to ONE allowed value. Rules: "
            "use only values from the allowed list; if the product evidence is "
            "insufficient use \"Other\" when it is allowed, otherwise leave it out; "
            "never invent specs; ASCII output only; no explanations."
        )
        detail_flat = re.sub(r"\s+", " ", str(detail or ""))[:900]
        user = (f"Product title: {title}\n"
                f"Product details: {detail_flat}\n"
                f"Required attributes:\n" + "\n".join(lines))
        try:
            from . import model_of
            out = self.client.chat(model_of(self.cfg, "text"), [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ], max_tokens=500)
            return TextResult(True, out)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    # ---------- 估重 ----------
    def estimate_weight(self, image_url: str, product_name: str = "") -> TextResult:
        try:
            from . import model_of
            out = self.client.chat(model_of(self.cfg, "vision"), [
                {"role": "system", "content": self.cfg.weight_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": product_name or "请估算该产品重量与尺寸"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]},
            ], max_tokens=300)
            return TextResult(True, out)
        except Exception as e:  # noqa: BLE001
            return TextResult(False, error=str(e))

    def estimate_weight_json(self, image_url: str, product_name: str = "", *, result=None) -> dict:
        """解析已有估重响应；未提供 result 时才请求模型。"""
        import json as _json
        import re as _re
        r = result if result is not None else self.estimate_weight(image_url, product_name)
        if not r.ok:
            return {}
        txt = r.value or ""
        m = _re.search(r"\{.*\}", txt, _re.S)
        if m:
            try:
                d = _json.loads(m.group(0))
                return {k: float(d[k]) for k in ("weight_kg", "length_cm", "width_cm", "height_cm") if k in d}
            except Exception:
                pass
        # 兜底：从自然语言里抓数字
        out = {}
        w = _re.search(r"(\d+(?:\.\d+)?)\s*kg", txt, _re.I)
        if w:
            out["weight_kg"] = float(w.group(1))
        dims = _re.findall(r"(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)", txt)
        if len(dims) >= 3:
            out.update({"length_cm": float(dims[0]), "width_cm": float(dims[1]), "height_cm": float(dims[2])})
        return out
