"""AuroraList 配置模型：覆盖老软件全部设置项（136 项的子集，含浏览器/模板/库存/SKU/价格/图片/AI）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

APP_NAME = "AuroraList"

DEFAULTS: dict = {
    # ---- 基础 / API ----
    # 模型已固定（见 services/__init__.py FIXED_MODELS），这里不再提供 model_* 配置项
    "cost_warn_threshold": 5.0,
    "api_base": "https://api.moneyfly.club",
    "api_key": "",
    "license_server": "https://license.moneyfly.club",
    "license_enforce": True,                              # 未授权时禁止开始上架
    "license_identity": "",                               # 可选：从老软件复制过来的旧授权 ID
    "license_cache_ok": False,                            # 上次授权结果（避免每次启动阻塞网络）
    "license_cache_expiry": "",
    "license_cache_ts": 0,
    "license_public_key": "",                             # 可选：服务端换公钥时覆盖内置公钥
    "api_purchase_url": "https://api.moneyfly.club",      # API 购买/充值后台
    "log_auto_follow": True,                              # 日志总是跳到最新
    "ai_image_enable": True,                              # AI 图片处理总开关
    "main_as_detail": False,                              # 主图同时作为详情图
    "write_main_images_to_product": True,                 # AI 主图写回商品
    "write_detail_html_to_product": True,                 # AI 详情写回商品
    "write_price_to_product": True,                       # 价格写回商品
    "keep_main_images_local": False,                      # 保留主图/AI图到本地
    "custom_detail_text_enable": True,                    # 添加自定义详情文本/图片
    "delete_detail_text": False,                          # 删除详情原有文本
    "keep_custom_attributes": True,                       # 保留商品自己的自定义属性
    "split_product_price": False,                         # 产品信息/价格分离处理
    "original_box": False,                                # 是否原箱
    "prefer_collected_weight": True,                      # 优先采集重量尺寸
    "custom_sku_prefix_enable": True,                     # 启用自定义 SKU 前缀
    "image_concurrency": 4,                               # 单产品内图片并发数
    "image_cache_enable": True,                           # 相同图片+相同处理只调用一次（跨产品复用）
    "image_cache_days": 7,
    "concurrency": 4,
    "retry_max": 1,
    "retry_delay": 2.0,
    "timeout": 180,
    # ---- 店铺 / 浏览器 / 模板 ----
    "browser_type": "本机Chrome",
    "bit_browser_id": "",
    "bit_browser_name": "",
    "zhanfu_port": "9222",
    "store_name": "",
    "store_id": "",
    "product_template_id": "",
    "product_template_name": "",
    "product_id_title": "",
    # ---- 高级设置 ----
    "edit_sum": "10",
    "watermark": "",
    "title_prefix": "",
    "title_suffix": "",
    # ---- 库存 / SKU ----
    "max_sku_count": 256,
    # ---- AI 智能填写 ----
    "enable_ai_title": False,
    "enable_ai_detail": False,
    "enable_ai_sku": False,
    "enable_ai_weight": False,
    "title_prompt": (
        "你是资深速卖通（AliExpress）英文刊登运营。请把用户给出的中文商品标题改写为专业英文标题。\n"
        "硬性规则：\n"
        "1) 总长度不超过 125 个字符（含空格），超长必须删减，不得截断单词；\n"
        "2) 结构：核心产品词 + 关键规格/型号 + 主要卖点 + 用途/适用场景；\n"
        "3) 品牌词一律前置 for（例：for CAT），不得暗示官方授权；\n"
        "4) 禁止：best/cheapest/No.1/100% 等绝对化用语、促销词（free shipping、hot sale）、表情符号、全大写堆砌、联系方式；\n"
        "5) 数字与单位保留（如 3 Ton、20m、220V），单位用英文缩写；\n"
        "6) 只输出一行英文标题，不要引号、不要解释、不要中文。"
    ),
    "detail_prompt": (
        "你是跨境电商详情页文案专家。根据用户给出的英文标题，写一段可直接用于速卖通商品详情（PC+无线端）的英文 HTML。\n"
        "结构要求（依次输出，使用 <h3>、<p>、<ul><li>、<table>）：\n"
        "1) 一句话核心卖点（<h3>）；\n"
        "2) 产品亮点 4~6 条（<ul><li>，每条 12~20 词，含规格数字）；\n"
        "3) 关键参数表（<table>：Model / Capacity / Power / Size / Weight / Material / Application，缺失写 N/A）；\n"
        "4) 适用场景 2~3 句；\n"
        "5) 包装清单（<ul>）；\n"
        "6) 发货与售后说明 2~3 句（不含具体时效承诺）。\n"
        "硬性规则：全英文；禁止中文、邮箱、电话、网址、二维码、支付方式、第三方平台名；"
        "禁止绝对化/医疗功效/侵权表述；不要夸大（如 world best）；不要 markdown 代码块；总词数 300~500。"
        "只输出 HTML 片段本身，不要任何解释。"
    ),
    "sku_prompt": (
        "你是速卖通 SKU 命名规范专家。用户会给出一组中文 SKU 名称（每行一个），请逐个改写为规范英文 SKU。\n"
        "规则：\n"
        "1) 每个 ≤ 20 个字符，仅允许 A-Z a-z 0-9 空格 - _ . ( )；\n"
        "2) 统一格式：<核心属性>-<规格>，例如 3T-20M、220V-EU、Red-XL；\n"
        "3) 单位用缩写并紧跟数字（3T、20m、220V），不要写中文单位；\n"
        "4) 同一批内风格一致：颜色用英文首字母大写，尺寸用大写字母，容量/功率保留数字；\n"
        "5) 禁止中文、表情、/ \\ & % 等符号，禁止品牌词；\n"
        "6) 输出行数与输入一致，每行一个 SKU，顺序不变，不要编号、不要解释。"
    ),
    "weight_prompt": (
        "你是跨境电商物流估算专家。根据商品标题与主图，估算该商品「包装后」的毛重与包装箱尺寸。\n"
        "输出严格为一行 JSON，不要任何解释或代码块：\n"
        '{"weight_kg": 数字, "length_cm": 数字, "width_cm": 数字, "height_cm": 数字}\n'
        "要求：weight_kg 保留 1 位小数；尺寸取整数；数值须符合该品类常识"
        "（例：小型工具 2~10kg，中型机械 50~300kg，超大件按比例放大）；"
        "不确定时给出偏保守（略大）的估算，便于运费核算。"
    ),
    # ---- 内容审查 ----
    "review_block_on_fail": False,
    "auto_save_products": False,
    "publish_after_save": False,
    "publish_action": "save_only",   # save_only=保存到待发布 / publish=保存并发布
    "onboarded": False,
    "manual_cookies": {},
    "template_source": "draft",
    "template_product_id": "",
    "template_product_title": "",
    "template_snapshot": "",
    "template_values": "",
    "template_library": "{}",
    "apply_template_on_save": True,
    # AI 主图生成 / 指定图片编辑
    "main_image_generate_enable": False,
    "main_image_generate_prompt": "电商主图：{title}，让 {subject} 居中并占画面约 80%，纯白背景（#FFFFFF），柔和棚拍光，阴影自然，细节清晰，颜色准确，1:1 正方形，画册级质感。不要文字、不要水印、不要 Logo、不要多余道具、不要人物、不要边框。",
    "main_image_generate_count": "1",
    "designated_edit_enable": False,
    "designated_edit_indices": "",
    "designated_edit_prompt": "把这张商品图处理成可直接上架的电商图：去掉所有水印、Logo、二维码、促销角标和叠加文字；商品本体的形状、颜色、材质和比例完全不变；被遮挡的区域自然补全，不要模糊和瑕疵；效果像原始棚拍图一样干净。不要新增任何文字。",
    # 逐图编辑方案（旧版，保留用于迁移）
    "image_edit_plans": "[]",
    # 新版图片处理：简单规则（按范围）+ 精细任务（按单图提示词）
    "image_edit_mode": "fine",          # simple | fine（默认精细）
    "image_simple_rules": "[]",
    "image_ai_jobs": "[]",
    # 文生图时是否把现有主图当参考素材（图生图，而不是纯文生图）
    "main_image_generate_use_refs": True,
    # 四合一：append=追加为主图最后一张 / first=放到主图第一张
    "four_in_one_position": "append",

    # 库存 / SKU
    "modify_stock_999": False,
    "stock_quantity": "999",
    "custom_sku_prefix": "",
    "sku_split_char": "-",
    "auto_generate_sku_code": False,
    "use_number_replace_sku": False,
    # 图片范围扩展
    "remove_watermark_pic_1": "1",
    "remove_watermark_pic_2": "5",
    "single_edit_main_pic_indices": "",
    "single_edit_detail_pic_indices": "",
    # 背景生成
    "background_gen_rules": "[]",
    "background_gen_sku_prompt": "把背景换成干净的摄影棚背景（纯白或极浅的中性灰），商品本体的形状、颜色、材质和比例保持不变，加一层自然的接触阴影，光线均匀，不要文字、不要道具。",
    "background_gen_detail_replace": False,
    # 上传 / 视频
    "upload_full_cid": "",
    "video_slot_seconds": 2.5,
    "video_transition_seconds": 0.5,
    "video_size": "1080x1080",
    "video_cover": True,
    "video_fade_edges": True,
    "video_output_dir": "",
    "eu_qualification": "否",
    # 选「是」时必填的欧盟责任人资料（店小秘选是后要求填写，否则保存被拦）
    "eu_responsible_name": "",
    "eu_responsible_address": "",
    "eu_responsible_email": "",
    "eu_responsible_phone": "",
    "browser_hide": True,          # 连接后隐藏/最小化浏览器窗口（后台运行）
    "browser_headless_login": False, # 首次登录保持可见
    "cdp_port": 9333,
    "proxy_enabled": False,
    "proxy_url": "",
    "generate_main_video": False,
    "upload_main_video": True,
    "use_template_video": False,
    "template_video_path": "",
    "overwrite_existing_video": False,
    # 区域调价模板（本地）
    "adjust_price_template_detail": "{}",
    # 详情文字 / 属性
    "move_detail_text": False,
    "declared_value": "",
    "product_type": "普货",
    "amount": "",
    "spec_weight": "",
    "spec_length": "",
    "spec_width": "",
    "spec_height": "",
    # 详情图翻译
    "translate_detail_pic_enable": False,
    "translate_detail_pic_indices": "",
    "translate_prompt": "把这张图里所有可见文字翻译成自然的英文（适合电商），保持原有排版、字体、颜色和位置不变；不要翻译商品本身；不要新增文字；如果图里没有文字，就原样返回。",
    # 详情图审查范围
    "detail_review_first_count": 0,
    "detail_review_last_count": 0,
    # 翻译扩展
    "translate_main_pic_enable": False,
    "translate_main_pic_indices": "",
    "translate_sku_pic": False,
    "translate_image_understanding": False,
    # SKU 增强
    "only_sku_prefix_checkbox": False,
    "copy_sku_to_detail": False,
    "pack_weight": "",
    # 素材补齐
    "pad_main_images_from_extra": False,
    "main_pic_checkbox": False,
    "main_pic_count": "6",
    # 定时发布
    "schedule_publish": False,
    "schedule_time": "",
    # 套装 / 水印叠加 / SKU 图替换
    "combo_enable": False,
    "combo_prefix": "套装",
    "combo_multiplier": "2",
    "overlay_watermark_enable": False,
    "watermark_upload_album": True,
    "overlay_position": "bottom-right",
    "overlay_opacity": "0.7",
    "sku_image_replace": "{}",




    "review_text_enable": False,
    "review_detail_img_enable": False,
    "review_main_img_enable": False,
    "review_text_prompt": (
        "你是速卖通文案合规审查员。只输出一行：PASS 或 FAIL|原因关键词。\n"
        "FAIL 条件：联系方式（邮箱/电话/网址/微信/WhatsApp）、促销诱导（好评返现、加微信优惠）、"
        "绝对化用语（best/No.1/100%）、医疗功效宣称、侵权品牌（未加 for）、中文残留。\n"
        "其余情况一律 PASS。"
    ),
    "review_detail_img_prompt": "你是速卖通详情图合规审查员。只输出一行结论：PASS 或 FAIL|原因关键词。\nFAIL 条件：厂家名称/地址/联系方式、二维码、其它平台水印、促销与返现引导、侵权品牌、医疗功效宣称、暴露内容。正常产品细节图、尺寸图、场景图应判 PASS。",
    "review_main_img_prompt": "你是速卖通主图合规审查员。只输出一行结论：PASS 或 FAIL|原因关键词。\nFAIL 条件：出现品牌 logo/商标、厂家名称、联系方式（电话/邮箱/网址/微信）、二维码、促销文字（折扣/包邮/热卖）、中文文字、水印、拼接边框、非白底主图、暴露人体。\n判断从严，但仅针对上述明确项。",
    # ---- 图片处理 ----
    "white_bg_enable": True,
    "watermark_enable": True,
    "edit_enable": False,
    "edit_prompt": "清理并本地化这张商品图：去掉水印、Logo、二维码和促销贴纸；保持商品本体颜色、形状、材质不变；输出干净可直接上架的电商图，画面里不要出现任何文字。",
    "four_in_one": False,
    # ---- 价格 ----
    "price_mode": "multiplier",          # multiplier | free_shipping
    "price_multiplier": "1.2",
    "price_fixed_add": "0",
    "regional_pricing": False,
    "platform_commission": "0",
    "sales_profit": "0",
    "goods_cost": "0",
    "default_express_price": "0",
    "adjust_price_template_id": "",
    "adjust_price_template_name": "",
    "marketing_image_enable": False,
    "marketing_image_count": 1,
    "marketing_image_prompt": (
        "Create a marketing lifestyle image for this product: place the product in a clean modern "
        "usage scene matching its purpose, natural lighting, shallow depth of field, product remains "
        "the focus and unchanged in shape/color; no text, no logo, no watermark, 1:1 composition."
    ),
    "wholesale_enable": False,
    "wholesale_tiers": "[]",
    "price_template_library": [],
    "prompt_sets": [],
    "active_prompt_set": "",
    "prompt_set_by_category": True,
    "custom_attributes": [],
    "attribute_template_id": "",
    # ---- 变种 ----
    "variant_mappings": "[]",
    # ---- 视频 / 发布 ----
    "check_banned_word": False,
    # ---- 通用 ----
    "endpoint_overrides": {},
    # ---- 详情文字 ----
    "detail_text_info": "",
    "detail_text_info_tail": "",
    "detail_text_position": "head",
    # ---- 主图/图片位置 ----
    "white_background_pic_1": "1",
    "white_background_pic_2": "3",
    "white_background_detail_pic_indices": "",
    "white_background_sku_pic": True,
    # 分类型处理开关：简单=白底/去水印，精细=AI编辑
    "img_main_edit": False,
    "img_detail_white_bg": False,
    "img_detail_remove_wm": False,
    "img_detail_edit": False,
    "img_sku_white_bg": True,
    "img_sku_remove_wm": False,
    "img_sku_edit": False,
    "img_single_mode": "simple",
    # ---- 规格 / 商品属性 ----
    # ---- 审查数量 ----
    # ---- 价格扩展 ----
    "tuoguan_price_tiered_enabled": False,
    "tuoguan_price_tiers": "[]",
    "product_id": "",
    # 启动时默认打开的页面（0=概览 … 12=预览）
    "start_page": 0,

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


ALIASES = {
    "ai_prompt": "title_prompt",
    "detail_ai_prompt": "detail_prompt",
    "ai_sku_optimize_prompt": "sku_prompt",
    "ai_weight_size_prompt": "weight_prompt",
    "enable_ai_sku_optimize": "enable_ai_sku",
    "enable_ai_weight_size": "enable_ai_weight",
    "enable_detail_text_review": "review_text_enable",
    "enable_detail_image_review": "review_detail_img_enable",
    "enable_main_image_review": "review_main_img_enable",
    "detail_review_prompt": "review_text_prompt",
    "main_image_review_prompt": "review_main_img_prompt",
    "white_background_enable": "white_bg_enable",
    "remove_watermark_enable": "watermark_enable",
    "single_edit_enable": "edit_enable",
    "single_edit_prompt": "edit_prompt",
}


class AppConfig:
    """字典式配置对象：cfg.xxx 读写，自动持久化。"""

    DEFAULTS = DEFAULTS

    def __init__(self, data: dict | None = None):
        src = dict(data or {})
        for old, new in ALIASES.items():
            if old in src and new not in src:
                src[new] = src[old]
        object.__setattr__(self, "_d", {**DEFAULTS, **src})

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

    def set(self, key, value):
        self._d[key] = value

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
            # 主配置损坏时用备份，避免“重启后配置全丢”
            try:
                bak = p.with_suffix(".bak")
                if bak.exists():
                    return cls(json.loads(bak.read_text(encoding="utf-8")))
            except Exception:
                pass
            return cls()

    def save(self) -> None:
        """原子写：先写临时文件，再替换；保留一份 .bak 防止断电损坏。"""
        import os as _os
        import shutil as _sh
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self._d, ensure_ascii=False, indent=2)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        try:
            if p.exists():
                _sh.copy2(p, p.with_suffix(".bak"))
        except Exception:
            pass
        _os.replace(tmp, p)


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
