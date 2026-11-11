"""AuroraList 配置模型：覆盖老软件全部设置项（136 项的子集，含浏览器/模板/库存/SKU/价格/图片/AI）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

APP_NAME = "AuroraList"

DEFAULTS: dict = {
    # ---- 基础 / API ----
    "api_base": "https://api.moneyfly.club",
    "api_key": "",
    "license_server": "https://license.moneyfly.club",
    "concurrency": 3,
    "timeout": 180,
    # ---- 店铺 / 浏览器 / 模板 ----
    "browser_type": "本机Edge",
    "bit_browser_id": "",
    "bit_browser_name": "",
    "zhanfu_port": "9222",
    "instance_name": "默认",
    "store_name": "",
    "store_id": "",
    "product_template_id": "",
    "product_template_name": "",
    "product_id_title": "",
    "product_id_image": "",
    "use_template_category": True,
    # ---- 高级设置 ----
    "edit_sum": "10",
    "watermark": "",
    "declared_value": "0",
    "title_prefix": "",
    "title_suffix": "",
    "move_detail_text": False,
    "original_box": False,
    # ---- 库存 / SKU ----
    "modify_stock_999": False,
    "stock_quantity": "999",
    "max_sku_count": 256,
    "custom_sku_prefix": "",
    "auto_generate_sku_code": False,
    "use_number_replace_sku": False,
    "only_sku_prefix_checkbox": False,
    "copy_sku_to_detail": False,
    "sku_split_char": "-",
    # ---- AI 智能填写 ----
    "enable_ai_title": False,
    "enable_ai_detail": False,
    "enable_ai_sku": False,
    "enable_ai_weight": False,
    "title_prompt": (
        "你来扮演一位资深速卖通平台运营，请将以下这款产品的中文标题翻译为英文标题，"
        "不新增任何产品品牌，若原标题中包含品牌词，则在品牌词等前面加 for，英文标题字符控制在 125 字。"
    ),
    "detail_prompt": (
        "以上是一款速卖通产品标题，请你使用英文根据标题写出适当的产品描述，"
        "若原标题中包含品牌词，则在品牌词等前面加 for，长度控制在 100 词以上，500 词以下。"
        "绝对不要包含中文，直接给出回答，不要二次询问。"
    ),
    "sku_prompt": (
        "你是一个跨境电商 SKU 名称优化助手。用户会提供一组中文 SKU 名称，"
        "请将每个名称翻译/优化为简洁的纯英文名称，每个名称最长 20 个字符，"
        "只能使用英文字母、数字、空格、连字符、小数点和圆括号。"
    ),
    "weight_prompt": (
        "我是一名电商运营者，这个产品我只有图片、没有实物，请根据产品图片和名称，"
        "估算该产品重量（kg）和长宽高（cm）。不需要分析，只需要结果。"
    ),
    "weight_keep_existing": False,
    "pack_weight": "0",
    "pack_weight_tiers": "[]",
    # ---- 内容审查 ----
    "review_text_enable": False,
    "review_detail_img_enable": False,
    "review_main_img_enable": False,
    "review_text_prompt": (
        "请审查内容，删除非商品本身的信息：厂家信息、联系方式、广告促销、引导关注、物流售后；"
        "保留商品名称、规格参数、材质工艺、功能特点、尺寸重量。"
    ),
    "review_detail_img_prompt": "请审查详情图，删除含厂家信息、联系方式、广告促销、二维码的图片。",
    "review_main_img_prompt": "请审查主图，删除含厂家信息、联系方式、广告促销、二维码的图片。",
    "detail_review_first_count": 0,
    "detail_review_last_count": 0,
    # ---- 图片处理 ----
    "white_bg_enable": True,
    "white_bg_range": "1-2",
    "watermark_enable": True,
    "watermark_range": "1-2",
    "edit_enable": False,
    "edit_prompt": "清理并本地化这张商品图：去除水印、logo、二维码和促销标签，保持商品主体不变。",
    "background_gen_main_enable": False,
    "background_gen_detail_enable": False,
    "background_gen_model": "qwen-image-2.1-turbo",
    "single_edit_model": "qwen-image-2.1-turbo",
    "translate_enable": False,
    "translate_pic_1": "",
    "translate_pic_2": "",
    "translate_sku_pic": False,
    "four_in_one": False,
    "keep_temp_images": True,
    # ---- 价格 ----
    "price_mode": "multiplier",          # multiplier | free_shipping
    "price_multiplier": "1.2",
    "price_fixed_add": "0",
    "regional_pricing": False,
    "currency": "USD",
    "exchange_rate": "7.2",
    "platform_commission": "0",
    "sales_profit": "0",
    "single_item_discount_rate": "0",
    "affiliate_commission": "0",
    "goods_cost": "0",
    "goods_cost_unit": "CNY",
    "default_express_price": "0",
    "tuoguan_price_multiplier": "1",
    "tuoguan_price_fixed_addition": "0",
    "adjust_price_template_id": "",
    "adjust_price_template_name": "",
    # ---- 变种 ----
    "variant_mode": "auto",              # auto | manual
    "variant_mappings": "[]",
    "keep_custom_attributes": True,
    # ---- 视频 / 发布 ----
    "generate_main_video": False,
    "use_template_video": False,
    "overwrite_existing_video": False,
    "publish_destination": "",
    "folder_path": "",
    "check_banned_word": False,
    # ---- 通用 ----
    "image_upload_category": "",
    "market_white_bg_pic_no": "2",
    "market_scene_pic_no": "",
    "verify_mode": "local",
}


def config_dir() -> Path:
    inst = os.environ.get("AURORA_INSTANCE", "").strip()
    base_dir = _base_dir()
    if inst and inst != "默认":
        safe = "".join(c for c in inst if c.isalnum() or c in "-_") or "instance"
        return base_dir.parent / "AuroraListInstances" / safe / "config"
    return base_dir


def _base_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.sys.platform == "darwin":  # type: ignore[attr-defined]
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


class AppConfig:
    """字典式配置对象：cfg.xxx 读写，自动持久化。"""

    DEFAULTS = DEFAULTS

    def __init__(self, data: dict | None = None):
        object.__setattr__(self, "_d", {**DEFAULTS, **(data or {})})

    def __getattr__(self, name):
        d = object.__getattribute__(self, "_d")
        if name in d:
            return d[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._d[name] = value

    def get(self, key, default=None):
        return self._d.get(key, default)

    def as_dict(self):
        return dict(self._d)

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return cls()

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._d, ensure_ascii=False, indent=2), encoding="utf-8")


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
