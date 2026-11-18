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
    "write_sku_codes_to_product": True,                   # SKU 编码写回商品
    "keep_main_images_local": False,                      # 保留主图/AI图到本地
    "custom_detail_text_enable": True,
    "delete_detail_text": False,   # 加自定义详情时，删掉原有详情文字                    # 添加自定义详情文本/图片
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
        "你是一名资深AliExpress速卖通SEO运营专家。请对我提供的商品标题进行专业优化。\n"
        "要求：\n"
        "1) 保留原标题的核心产品名称、产品属性和主要卖点，不改变产品实际用途；\n"
        "2) 优化关键词布局，将核心关键词、长尾关键词、属性词合理组合；\n"
        "3) 提升搜索匹配度，优先使用海外买家常用的搜索表达；\n"
        "4) 删除重复、无效、堆砌的词语，避免语法错误；\n"
        "5) 在原有标题基础上优化，不要完全推翻重写；\n"
        "6) 英文标题字符控制在 125 字以内；\n"
        "7) 只输出一行英文标题，不要引号、不要解释、不要中文。"
    ),
    "detail_prompt": (
        "你是一名专业工业设备外贸营销专家。在保持原有文字基础之上，请优化以下商品描述，面向海外工程承包商、设备经销商、工程公司及终端采购商。\n\n"
        "重点突出：\n"
        "1) 设备用途及适用施工场景；\n"
        "2) 核心技术参数和产品性能；\n"
        "3) 设备结构、操作特点及实际优势；\n"
        "4) 适用行业和目标客户；\n"
        "5) 产品质量、生产制造及服务优势（仅限原文有依据的信息）。\n\n"
        "排版要求：逻辑清晰，章节段落规范，使用 <h3>、<p>、<ul><li>、<table> 等 HTML 标签。\n"
        "硬性规则：全英文；禁止中文、邮箱、电话、网址、二维码、支付方式、第三方平台名；"
        "禁止绝对化/医疗功效/侵权表述；只输出 HTML 片段本身，不要任何解释。"
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
    "image_edit_mode": "fine",          # general（通用编辑：一条提示词）| fine（精细化编辑：逐图设置）
    "image_simple_rules": "[]",
    "image_ai_jobs": "[]",
    # ---- 图片通用编辑（对齐老软件：一个总开关 + 通用编辑/精细化编辑二选一）----
    "img_general_prompt": (
        "清理并本地化这张商品图：1）彻底去除画面中的水印、Logo、二维码、促销标签等多余叠加元素，"
        "并用周围背景自然填补；2）把图中的中文文字（卖点、说明、标注等）翻译并替换为准确的英文，"
        "保持原有字体风格、颜色、大小与位置，排版协调；3）除上述处理外，不得改变商品的形状、颜色、"
        "材质、数量与构图，保持原图比例与清晰度，画面自然，看不出处理痕迹。不要新增任何文字。"
    ),
    "img_general_main_from": "1",       # 通用编辑：主图从第几张开始
    "img_general_main_to": "8",         # 通用编辑：主图编辑到第几张（不选则不处理主图）
    # 精细化编辑：每组独立启用（老软件：主图/详情图/SKU 图各自开关）
    "img_fine_main_enable": True,
    "img_fine_detail_enable": False,
    "img_fine_detail_replace": False,   # 删除原有详情图，仅保留编辑结果
    "img_fine_sku_enable": False,
    # ---- 图片尺寸校验（速卖通要求 ≥800×800）----
    "img_min_size_enable": True,        # AI 产出的图小于下限时不采用（保留原图）
    "img_min_size": 800,                # 最小边长（px）
    "img_min_size_drop_invalid": False, # 连原图里本来就不合规的也去掉（默认关，避免误删）
    # 编辑器主图上限（速卖通一般是 6；改大前请确认类目允许）
    "editor_main_images_max": 6,
    # 保存时删除「与系统属性重名」的自定义属性行。
    # 默认关：这是唯一会删用户数据的属性操作，而且实测该商品 22 条自定义属性里
    # 有 7 条名字与系统属性重名（Brand Name / Type / Model Number / Origin …），
    # 一开就会静默删掉它们。要清理再手动打开。
    "auto_clean_dup_attributes": True,
    # 主图写回方式：auto=先试「网络图片」URL 粘贴，失败再用文件上传 | url | file
    # 实测新版编辑器没有「网络图片」入口，所以 auto 的兜底（文件上传）才是实际生效的那条。
    # 服务模板名称（草稿商品的必填项之一，留空则跳过不填）
    "service_template": "",
    # 运费模板名称（草稿必填项，留空则跳过不填）
    "freight_template": "",
    # 点店小秘「营销图 → 一键生成」（生成 1:1 + 3:4 两张）
    "marketing_auto_generate": False,   # 营销图：可选增强，单张 40~90s，默认关
    # 类目属性默认值（模板产品读不到时用；速卖通默认都选「否」）
    "high_concerned_chemical": "否",
    "charge_status": "否",
    # ---- 图床（AI 产出的图先放这里，编辑器/速卖通按 URL 取图）----
    # 店小秘编辑器只认「网络图片 URL」，而 AI 返回的是约 1 天过期的临时地址，
    # 所以需要一个稳定的公网地址做中转。
    "image_host_url": "https://image.moneyfly.club",
    "image_host_key": "120423mm",
    # 图床也会满：删掉 N 天前上传的图（按本地台账里的 delToken）。
    # 注意不要设太小——商品审核期间可能还会去抓图，建议 ≥7 天。
    "host_keep_days": 7,
    # 出图后直接传进店小秘「图片空间」，用店小秘自己的地址（不再依赖图床）。
    # 好处：图在店小秘里，空间/引用都由它管；坏处：受相册空间限制，每张多一次上传。
    "prefer_dianxiaomi_album": False,
    # 传完相册后回读一次，确认地址真的能公开访问（否则保留原地址，避免商品图裂）
    "album_verify_url": True,
    # 刊登完成后自动创建「删除未引用图片」任务（店小秘侧异步跑，已被商品引用的不会删）
    "album_auto_clean_enable": False,
    "album_auto_clean_days": 7,
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
    "browser_headless_login": False, # 首次登录保持可见
    "proxy_enabled": False,
    "proxy_url": "",
    # 视频来源：collected=用采集的产品视频（默认）｜template=模板视频｜generated=主图轮播生成｜none=不要视频
    "video_source": "collected",
    "generate_main_video": False,
    "upload_main_video": True,
    "use_template_video": False,
    "template_video_path": "",
    "overwrite_existing_video": False,
    # 区域调价模板（本地）
    "adjust_price_template_detail": "{}",
    # 详情文字 / 属性
    "move_detail_text": False,
    "declared_value": "50",
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
    "marketing_source": "local",   # local=本地裁切(店小秘同款,免费) | ai=AI生成场景图(花钱)
    "marketing_timeout": "45",
    "marketing_image_prompt": (
        "生成一张专业的产品营销场景图，用于速卖通营销位：\n"
        "1. 把商品放在符合其用途的真实作业场景中（工程机械类：施工现场/工地/矿山/道路工程）\n"
        "2. 商品本体形状、颜色、结构、标识完全不变，只替换背景与光影\n"
        "3. 自然光或柔和环境光，画面干净、有质感、有纵深感\n"
        "4. 商品清晰为视觉主体，占画面 55~70%\n"
        "5. 不要添加任何文字、水印、Logo、促销标签\n"
        "6. 不要出现人脸特写、不要卡通化、不要过度特效\n"
        "7. 1:1 正方形构图，适合营销位展示"
    ),
    # ---- 批发 / 区域调价（店小秘风格，直接对应平台字段）----
    "wholesale_enable": False,           # 启用批发（wholeSale）
    "bulk_order": "2",                   # 购买 N 件以上（2~100000）
    "bulk_discount": "15",               # 减免 X%（1~99）
    "dropship_discount": "0",            # 一件代发折扣 Y%（0~99）
    "regional_quote_enable": False,      # 启用区域调价
    "regional_quotes": "[]",             # [{"country":"US","price":"12.99"}]
    "wholesale_tiers": "[]",             # 旧数据，保留兼容
    "price_template_library": [],
    "custom_attributes": [],
    "attribute_template_id": "",
    # ---- 变种 ----
    # ---- 视频 / 发布 ----
    "check_banned_word": False,
    # ---- 通用 ----
    "endpoint_overrides": {},
    # ---- 详情文字 ----
    "detail_text_info": "",
    "detail_text_info_tail": "",
    # 本地详情图：用户从电脑选的图片，上传图床后插入详情头/尾
    "detail_local_images": "[]",          # JSON: ["/path/a.png", ...]
    "detail_local_image_position": "tail",  # head | tail | both
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
    "white_background_sku_pic_indices": "1-20",   # SKU 图序号（白底/去水印/编辑共用）
    "img_sku_edit_prompt": "",                    # SKU 图精细编辑提示词
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
        d = {**DEFAULTS, **src}
        # 旧版英文营销图提示词 → 迁移成机械类目中文提示词
        _mp = str(d.get("marketing_image_prompt") or "")
        if _mp.strip().lower().startswith("create a marketing lifestyle image"):
            d["marketing_image_prompt"] = DEFAULTS["marketing_image_prompt"]
        # 营销图来源：老配置没有该键 → 默认本地（免费）
        if "marketing_source" not in src:
            d["marketing_source"] = "local"
        object.__setattr__(self, "_d", d)

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

    # 上次 load() 是否因为主配置损坏而回退（给界面提示用）
    loaded_from: str = "file"      # file | bak | defaults | new

    @classmethod
    def load(cls) -> "AppConfig":
        p = cls.path()
        if not p.exists():
            cls.loaded_from = "new"
            return cls()
        try:
            cfg = cls(json.loads(p.read_text(encoding="utf-8")))
            cls.loaded_from = "file"
            object.__setattr__(cfg, "_loaded_from", "file")
            return cfg
        except Exception:
            # 主配置读不出来：先把损坏文件另存一份，再退回备份。
            # 否则下一步 save() 一写，原来的内容（含 API Key）就永久没了。
            try:
                import time as _t
                bad = p.with_name(f"config.corrupt-{_t.strftime('%Y%m%d-%H%M%S')}.json")
                bad.write_bytes(p.read_bytes())
            except Exception:
                pass
            try:
                bak = p.with_suffix(".bak")
                if bak.exists():
                    cfg = cls(json.loads(bak.read_text(encoding="utf-8")))
                    cls.loaded_from = "bak"
                    object.__setattr__(cfg, "_loaded_from", "bak")
                    return cfg
            except Exception:
                pass
            cls.loaded_from = "defaults"
            cfg = cls()
            object.__setattr__(cfg, "_loaded_from", "defaults")
            return cfg

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
