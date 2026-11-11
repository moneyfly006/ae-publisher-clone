"""配置读写：保存在用户目录，跨平台。"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "AuroraList"


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.sys.platform == "darwin":  # type: ignore[attr-defined]
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


@dataclass
class AppConfig:
    api_base: str = "https://api.moneyfly.club"
    api_key: str = ""
    license_server: str = "https://license.moneyfly.club"
    concurrency: int = 3
    timeout: int = 180
    title_prompt: str = (
        "你来扮演一位资深速卖通平台运营，请将以下这款产品的中文标题翻译为英文标题，"
        "不新增任何产品品牌，若原标题中包含品牌词，则在品牌词等前面加 for，英文标题字符控制在 125 字。"
    )
    detail_prompt: str = (
        "以上是一款速卖通产品标题，请你使用英文根据标题写出适当的产品描述，"
        "若原标题中包含品牌词，则在品牌词等前面加 for，长度控制在 100 词以上，500 词以下。"
        "绝对不要包含中文，直接给出回答，不要二次询问。"
    )
    sku_prompt: str = (
        "你是一个跨境电商 SKU 名称优化助手。用户会提供一组中文 SKU 名称，"
        "请将每个名称翻译/优化为简洁的纯英文名称，每个名称最长 20 个字符，"
        "只能使用英文字母、数字、空格、连字符、小数点和圆括号。"
    )
    route: list = field(default_factory=list)
    # ---- AI 智能填写 ----
    enable_ai_title: bool = False
    enable_ai_detail: bool = False
    enable_ai_sku: bool = False
    enable_ai_weight: bool = False
    weight_prompt: str = (
        "我是一名电商运营者，这个产品我只有图片、没有实物，请根据产品图片和名称，"
        "估算该产品重量（kg）和长宽高（cm）。不需要分析，只需要结果。"
    )
    # ---- 内容审查 ----
    review_text_enable: bool = False
    review_text_prompt: str = (
        "请审查内容，删除非商品本身的信息：厂家信息、联系方式、广告促销、引导关注、物流售后；"
        "保留商品名称、规格参数、材质工艺、功能特点、尺寸重量。"
    )
    review_detail_img_enable: bool = False
    review_detail_img_prompt: str = "请审查详情图，删除含厂家信息、联系方式、广告促销、二维码的图片。"
    review_main_img_enable: bool = False
    review_main_img_prompt: str = "请审查主图，删除含厂家信息、联系方式、广告促销、二维码的图片。"
    # ---- 图片处理 ----
    white_bg_enable: bool = True
    white_bg_range: str = "1-2"
    watermark_enable: bool = True
    watermark_range: str = "1-2"
    edit_enable: bool = False
    edit_prompt: str = "清理并本地化这张商品图：去除水印、logo、二维码和促销标签，保持商品主体不变。"


    # ---------- 持久化 ----------
    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


_lock = threading.Lock()
_cache: AppConfig | None = None


def get_config() -> AppConfig:
    global _cache
    with _lock:
        if _cache is None:
            _cache = AppConfig.load()
        return _cache


def save_config(cfg: AppConfig) -> None:
    global _cache
    with _lock:
        _cache = cfg
        cfg.save()
