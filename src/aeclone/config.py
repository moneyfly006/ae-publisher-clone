"""AuroraList 配置模型：统一商品处理与发布设置（136 项的子集，含浏览器/模板/库存/SKU/价格/图片/AI）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .alibaba_company import COMPANY_PROFILE, company_desc, company_faq

APP_NAME = "AuroraList"
_SAVE_LOCK = threading.RLock()

DEFAULTS: dict = {
    # ---- 基础 / API ----
    # 模型已固定（见 services/__init__.py FIXED_MODELS），这里不再提供 model_* 配置项
    "api_base": "https://api.moneyfly.club",
    "api_key": "",
    "api_balance": -1.0,                                  # 最近一次已知余额（-1=未知）
    # ---- 账号制授权（契约 §10：登录拿 token，只认 token）----
    "license_server": "https://license.moneyfly.club",
    "license_enforce": True,                              # 未授权平台禁止开始上架
    "license_token": "",                                   # software_login 下发的签名授权 token
    "license_token_ts": 0,                                 # token 最近刷新时间（离线宽限 7 天）
    "license_platforms": "",                              # 仅测试/调试：平台权限覆盖（隐藏，正常登录不使用）
    "license_account_email": "",                           # 登录邮箱
    "license_account_save_password": False,                # 记住密码
    "license_account_password": "",                        # 加密后的密码，不是明文（account_store）
    "license_auto_login": True,                            # 本地 token 有效时启动静默登录
    # ---- 每平台今日刊登成功/失败计数（按天持久化，心跳上报；服务端同日取 MAX）----
    "publish_counts_day": "",                              # 计数所属日期 YYYY-MM-DD
    "publish_counts": "{}",                                # JSON: {pid:{"success":n,"fail":m}}
    "publish_seen": "{}",                                  # JSON: {pid:{product_id:"success"|"fail"}}
    "publish_total": "{}",                                 # JSON: {"success":n,"fail":m}（累计，跨天不清）
    "heartbeat_interval": 120,                               # 开发/过渡：手动覆盖（alibaba:listing+ai_text）
    "active_platform": "alibaba",
    "api_purchase_url": "https://api.moneyfly.club",      # API 购买/充值后台
    "log_auto_follow": True,                              # 日志总是跳到最新
    "auto_report_errors": True,                            # 出现 ERROR 日志立即上报授权后台
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
    # ---- 高级设置 ----
    "edit_sum": "3",
    "watermark": "",
    "title_prefix": "",
    "title_suffix": "",
    # ---- 库存 / SKU ----
    "max_sku_count": 256,
    # ---- AI 智能填写 ----
    "enable_ai_title": True,
    "enable_ai_detail": False,
    # 详情图合规过滤（默认关）：打开后用视觉模型识别含联系方式/二维码/促销的图片并剔除
    "detail_image_compliance": False,
    "enable_ai_sku": False,
    "enable_ai_weight": False,
    "title_prompt": (
        "你是资深 AliExpress 机械/工业设备类目 SEO 运营专家。请优化用户给出的商品标题。\n"
        "要求：\n"
        "1) 保留原产品英文名、型号、核心规格（功率/容量/尺寸/材质）与真实用途；\n"
        "2) 核心关键词 + 长尾关键词 + 属性词合理组合；\n"
        "3) 删除重复、无效、堆砌词，避免语法错误与绝对化用语；\n"
        "4) 不得编造原文没有的型号、参数、认证（原文有 CE/ISO 才写）；\n"
        "5) 英文标题 ≤125 字符；\n"
        "6) 只输出一行英文标题。"
    ),
    "detail_prompt": (
        "你是专业工业设备外贸营销专家。基于原始商品信息重写英文详情。\n\n"
        "结构：用途与场景；核心参数（只用原文数据，禁止编造型号/参数/认证）；结构与操作；适用行业；质量与服务。\n\n"
        "图片规则：原文图片改用占位符 <img data-detail-img=\"序号\"> 按章节插入，至少 2 张，不要堆在开头/结尾。\n\n"
        "排版：<h3>/<p>/<ul><li>/<table>，1500~3500 字符。\n"
        "合规：全英文；禁止中文、邮箱、电话、网址、二维码、支付方式、第三方平台名、促销返现、绝对化与医疗表述；"
        "不要 style/script/iframe；只输出 HTML 片段。"
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
    "publish_action": "save_only",   # save_only=留在待发布 / save_to_waiting=移入待发布 / publish=直接发布
    "onboarded": True,
    "manual_cookies": {},
    "template_source": "online",
    "template_product_id": "",
    "template_product_title": "",
    "template_snapshot": "",
    "template_image_url": "",        # 模板商品首图（预览用，选中模板时保存）
    "template_values": "",
    "template_library": "{}",
    "auto_detect_category": True,                        # 自动识别类目：优先用商品自带 categoryId
    "apply_template_on_save": True,
    "smt_attr_autofill": True,
    "smt_attr_ai_autofill": True,                    # 本地补不出的速卖通必填属性，AI 按类目枚举补一次                           # 保存/发布前补齐速卖通类目必填属性（只补缺失，不覆盖已有）
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
    # ---- 图片通用编辑（通用编辑与精细化编辑二选一）----
    "img_general_prompt": (
        "清理并本地化这张商品图：1）彻底去除画面中的水印、Logo、二维码、促销标签等多余叠加元素，"
        "并用周围背景自然填补；2）把图中的中文文字（卖点、说明、标注等）翻译并替换为准确的英文，"
        "保持原有字体风格、颜色、大小与位置，排版协调；3）除上述处理外，不得改变商品的形状、颜色、"
        "材质、数量与构图，保持原图比例与清晰度，画面自然，看不出处理痕迹。不要新增任何文字。"
    ),
    "img_general_main_from": "1",       # 通用编辑：主图从第几张开始
    "img_general_main_to": "8",         # 通用编辑：主图编辑到第几张（不选则不处理主图）
    # 精细化编辑：每组独立启用（主图、详情图、SKU 图分别启用）
    "img_fine_main_enable": True,
    "img_fine_detail_enable": False,
    "img_fine_detail_replace": False,   # 删除原有详情图，仅保留编辑结果
    "img_fine_sku_enable": False,
    # ---- 图片尺寸校验（速卖通要求 ≥800×800）----
    "img_min_size_enable": False,       # AI 产出的图小于下限时不采用（保留原图）
    "img_min_size": 800,                # 最小边长（px）
    "img_min_size_drop_invalid": False, # 连原图里本来就不合规的也去掉（默认关，避免误删）
    # 编辑器主图上限（速卖通一般是 6；改大前请确认类目允许）
    "editor_main_images_max": 6,
    # 保存时删除「与系统属性重名」的自定义属性行。
    # 默认关：这是唯一会删用户数据的属性操作，而且实测该商品 22 条自定义属性里
    # 有 7 条名字与系统属性重名（Brand Name / Type / Model Number / Origin …），
    # 一开就会静默删掉它们。要清理再手动打开。
    # 主图写回方式：auto=先试「网络图片」URL 粘贴，失败再用文件上传 | url | file
    # 实测新版编辑器没有「网络图片」入口，所以 auto 的兜底（文件上传）才是实际生效的那条。
    # 服务模板名称（草稿商品的必填项之一，留空则跳过不填）
    "service_template": "",
    # 运费模板名称（草稿必填项，留空则跳过不填）
    "freight_template": "",
    # 点店小秘「营销图 → 一键生成」（生成 1:1 + 3:4 两张）
    "marketing_auto_generate": True,    # 营销图：发布必填，本地裁切免费秒出，默认开
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
    "stock_quantity": "999",
    "custom_sku_prefix": "",
    "sku_split_char": "-",
    "auto_generate_sku_code": False,
    # 图片范围扩展
    "remove_watermark_pic_1": "1",
    "remove_watermark_pic_2": "5",
    "single_edit_main_pic_indices": "",
    # 背景生成
    "background_gen_rules": "[]",
    "background_gen_sku_prompt": "把背景换成干净的摄影棚背景（纯白或极浅的中性灰），商品本体的形状、颜色、材质和比例保持不变，加一层自然的接触阴影，光线均匀，不要文字、不要道具。",
    # 上传 / 视频
    "upload_full_cid": "",
    "video_slot_seconds": 2.5,
    "video_transition_seconds": 0.5,
    "video_size": "1080x1080",
    "video_cover": True,
    "video_fade_edges": True,
    "video_output_dir": "",
    "video_upload_timeout": 180,   # 店小秘视频 PUT 超时秒数；网络慢可调大
    "eu_qualification": "否",
    # 选「是」时必填的欧盟责任人资料（店小秘选是后要求填写，否则保存被拦）
    "eu_responsible_name": "",
    "eu_responsible_address": "",
    "eu_responsible_email": "",
    "eu_responsible_phone": "",
    "proxy_enabled": False,
    "proxy_url": "",
    # 视频来源：collected=用采集的产品视频（默认）｜template=模板视频｜generated=主图轮播生成｜none=不要视频
    "video_source": "collected",
    "generate_main_video": False,
    "use_template_video": False,
    "template_video_path": "",   # 旧本地路径，已弃用；模板视频自动取链接
    "overwrite_existing_video": False,   # 旧开关，已弃用；视频来源下拉决定覆盖
    # 区域调价模板（本地）
    "adjust_price_template_detail": "{}",
    # 详情文字 / 属性
    "move_detail_text": False,
    "declared_value": "50",
    "product_type": "普货",
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
    "main_pic_count": "6",
    # 套装 / 水印叠加 / SKU 图替换
    # 变种属性组合完全重复时：恢复真实规格失败后，自动合并重复 SKU（保留首条、库存累加）
    "auto_merge_duplicate_skus": True,
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
    "white_bg_enable": False,
    "watermark_enable": False,
    "edit_enable": False,
    "edit_prompt": "清理并本地化这张商品图：去掉水印、Logo、二维码和促销贴纸；保持商品本体颜色、形状、材质不变；输出干净可直接上架的电商图，画面里不要出现任何文字。",
    "four_in_one": False,
    # ---- 价格 ----
    "price_mode": "multiplier",          # multiplier | free_shipping | commission | fixed
    "price_fixed": "0",                  # 固定价格模式：所有商品统一售价（元）
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
    # 强制删除的自定义属性名（逗号分隔，大小写/空格不敏感）：清掉与系统属性冲突、
    # 软件自动判断删不掉的那些（如 brand / type / model）
    "custom_attr_drop": "",
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
    "workbench_image_overrides": "{}",    # JSON: {商品ID: {"main": [...], "detail": [...]}}
    "detail_local_image_position": "tail",  # head | tail | both
    "detail_text_position": "head",
    # ---- 主图/图片位置 ----
    "white_background_pic_1": "1",
    "white_background_pic_2": "3",
    "white_background_detail_pic_indices": "",
    "white_background_sku_pic": False,
    # 分类型处理开关：简单=白底/去水印，精细=AI编辑
    "img_main_edit": False,
    "img_detail_white_bg": False,
    "img_detail_remove_wm": False,
    "img_detail_edit": False,
    "img_sku_white_bg": False,
    "img_sku_remove_wm": False,
    "img_sku_edit": False,
    "white_background_sku_pic_indices": "1-20",   # SKU 图序号（白底/去水印/编辑共用）
    "img_sku_edit_prompt": "",                    # SKU 图精细编辑提示词
    # ---- 规格 / 商品属性 ----
    # ---- 审查数量 ----
    # ---- 价格扩展 ----
    "tuoguan_price_tiered_enabled": False,
    "tuoguan_price_tiers": "[]",
    "product_id": "",
    # 启动时默认打开的页面（0=概览 … 12=预览）
    "start_page": 0,
    # ---------------- Alibaba（国际站）刊登预设 ----------------
    "alibaba_cookies": "",
    "aliexpress_cookies": "",
    "alibaba_shop_id": "9466975",   # 预置：ICBU 店铺（可在「Alibaba 连接」页切换）
    "alibaba_shop_name": "zzyugong",
    "alibaba_template_product_id": "",
    "alibaba_template_product_name": "",
    "alibaba_template_source": "all",
    "alibaba_category_source": "auto",
    "alibaba_template_snapshot": "{}",
    "alibaba_auto_keywords": True,
    "alibaba_default_weight": "100",
    "alibaba_default_package_size": "10X10X10",
    "alibaba_default_deliver_periods": '[{"quantity":1,"process_period":5},{"quantity":3,"process_period":8},{"quantity":10,"process_period":12}]',
    "alibaba_default_freight_name": "买卖双方协商物流",
    "alibaba_default_logistics_property": "general_cargo_0",
    "alibaba_default_sample_service": "0",
    "alibaba_recommended_seeded": False,
    "alibaba_test_product_id": "",
    "alibaba_last_category_required": "",
    "alibaba_ai_preset": "machinery_industrial",
    "prompt_presets_data": "{}",   # 用户自定义提示词预设 + 上次选择（全平台共用）   # 上次使用的 Alibaba 行业提示词预设
    "alibaba_keyword_prompt": "",            # 空=用 TextPipeline 内置英文关键词提示词
    "alibaba_group_id": "",
    "alibaba_group_name": "",
    "alibaba_category_id": "127726092",
    "alibaba_brand": "Generic",
    "alibaba_ensure_min_images": True,
    "alibaba_force_ladder_price": False,  # 批量发布强制单档阶梯价，避免与 SKU 价并存  # 发布前不足4张主图时用原图补足
    "alibaba_batch_category_id": "127726092",   # 批量处理目标类目（与单个商品选择分开）
    "alibaba_category_name": "其他混凝土机械 / Other Concrete Machinery",
    "alibaba_full_cid": "201943703,1326,100010658,127726092",
    "alibaba_keywords": "",
    "alibaba_company_name": COMPANY_PROFILE["company_name"],
    "alibaba_company_desc": company_desc(),
    "alibaba_company_faq": company_faq(),
    "alibaba_company_website": COMPANY_PROFILE["website"],
    "alibaba_company_address": COMPANY_PROFILE["location"],
    "alibaba_company_license": COMPANY_PROFILE["business_license"],
    "alibaba_collect_company_desc": company_desc(),
    "alibaba_collect_company_faq": company_faq(),
    "alibaba_video_source": "auto",   # auto=采集视频优先，缺失用主图轮播生成 | collected | generated | none
    "alibaba_video_size": "1080x1080",
    "alibaba_video_slot_seconds": "2.5",
    "alibaba_video_transition_seconds": "0.5",
    "alibaba_min_order_quantity": 1,
    "alibaba_unit_type": "Piece",
    "alibaba_price_tiers": "",
    "alibaba_price_type": "",
    # 用户在 UI 显式选过定价方式才置 1；否则保存时保留商品原有 priceType
    # （避免默认 ladder_price 把 sku_price/fob_price 商品静默改成阶梯价）。
    "alibaba_price_type_explicit": "",
    "alibaba_sale_type": "normal",
    "alibaba_batch_number": "",
    "alibaba_fob_currency": "USD",
    "alibaba_fob_min_price": "",
    "alibaba_fob_max_price": "",
    "alibaba_freight_template_id": "2055583029",   # 运费模板 ID（发布时与重量/尺寸一起校验）
    "alibaba_freight_template_override": False,    # 用户在软件里显式选择运费模板后置 true
    "alibaba_logistics_property": "general_cargo_0",  # 物流属性；实测 online 商品用 general_cargo_0
    "alibaba_op": 1,
    "alibaba_package_size": "10X10X10",
    "alibaba_package_length": "10",
    "alibaba_package_width": "10",
    "alibaba_package_height": "10",
    "alibaba_weight": "100",
    "alibaba_hs_code": "84791000",
    "alibaba_stock": "5",
    "alibaba_payment_terms": "T/T",
    "alibaba_trade_terms": "FOB",
    "alibaba_port": "Shanghai",
    "alibaba_lead_time": 30,          # 发货期天数（写入 deliver_periods.process_period）
    "alibaba_deliver_quantity": "1",  # 发货期起订量（deliver_periods.quantity）
    "alibaba_process_period": "15",
    "alibaba_attr_presets": "{\"Core Components\":\"Motor\",\"Warranty\":\"1 Year\",\"Place of Origin\":\"China\",\"Condition\":\"New\",\"Model Number\":\"OEM\",\"Weight (kg)\":\"600\",\"Unique Selling Point\":\"Fully hydraulic system\"}",
    # ---------------- Amazon（亚马逊）刊登预设 ----------------
    "amazon_cookies": "",
    "amazon_shop_id": "",
    "amazon_shop_name": "",
    "amazon_site": "",              # marketPlaceId（如 A2EUQ1WTGCTBG2）
    "amazon_site_code": "",         # 站点代码（US/CA/MX/BR…）
    "amazon_site_name": "",
    "amazon_currency": "",          # 可选：覆盖站点币种（如 CA 选 CAD）
    "amazon_test_product_id": "",
    "amazon_list_state": "draft",
    "amazon_category_id": "",
    "amazon_category_path": "",     # nodePath a/b/c
    "amazon_category_name": "",
    "amazon_last_category_required": "",
    "amazon_price": "",
    "amazon_sale_price": "",
    "amazon_quantity": 0,
    "amazon_fulfillment": "MFN",    # MFN/FBA
    "amazon_handling_time": 2,
    "amazon_shipping_template": "",
    "amazon_brand": "",
    "amazon_manufacturer": "",
    "amazon_sku": "",
    "amazon_standard_product_type": "",
    "amazon_sku_prefix": "",
    "amazon_sku_suffix": "",
    "amazon_sku_sequence": False,  # 旧规则，保留兼容但不再使用
    "amazon_sku_random": True,     # 前缀 + 9 位随机字母数字
    "amazon_bullet_count": 5,       # AI 生成条数（5）；加载已有商品最多 10 条，保存不得截断
    "amazon_op": 1,   # 默认保存到采集箱，不直接提交线上
    "amazon_auto_op": "1",  # 自动流程默认同样只保存，禁止默认发线上
    "amazon_use_ai": False,
    "amazon_ai_preset": "generic",
    "amazon_title_prompt": "",
    "amazon_highlight_prompt": "",        # 空=内置
    "amazon_bullet_prompt": "",
    "amazon_description_prompt": "",
    "amazon_search_terms_prompt": "",
    "amazon_sku_prompt": "",
    "amazon_image_prompt": "",
    "amazon_title_preset": "",
    "amazon_bullet_preset": "",
    "amazon_description_preset": "",
    "amazon_search_terms_preset": "",
    "amazon_sku_preset": "",
    "amazon_image_preset": "",
    "amazon_presets_version": "",
    "amazon_last_status": "",
    "amazon_manufacturer_contact": "",   # 发布必填；从商品或配置读取真实联系方式
    "amazon_smart_attributes": False,    # 智能补全常见发布必填属性（不再依赖 schema 返回）
    "amazon_gtin_exemption": False,       # 已获批 GTIN/UPC 豁免：不填 UPC，提交豁免声明
    "amazon_auto_product_id": False,      # 有 UPC 前缀时自动生成 UPC
    "amazon_auto_merchant_asin": True,    # 缺失 Merchant Suggested ASIN 时自动生成
    "amazon_standard_product_type": "",   # UPC/EAN/GTIN
    "amazon_standard_product_id": "",
    "amazon_merchant_suggested_asin": "",
    "amazon_upc_prefix": "",
    "amazon_gs1_prefix": "",
    "amazon_template_product_id": "",
    "amazon_default_country_of_origin": "CN",
    "amazon_default_included_components": "1",
    "amazon_default_unit_count": "1",
    "amazon_default_power_source_type": "diesel",
    "amazon_default_voltage": "220",
    "amazon_default_dg_regulation": "not_applicable",
    "amazon_default_dimensions": "10x10x10",
    "amazon_default_length": "10",
    "amazon_default_width": "10",
    "amazon_default_height": "10",
    "amazon_publish_wait": 60,           # op=2 发布后轮询发布状态的最长秒数（0=只查一次）
    "amazon_autofill_attrs": True,       # 保存/发布前自动补齐缺失的必填属性（只补缺失，不覆盖已有）
    # ---- 真实发布自动修复闭环（services/amazon_auto.py） ----
    "amazon_auto_max_rounds": 3,         # 发布失败自动修复重发的最多轮次
    "amazon_auto_wait": 90,              # 每轮发布后轮询发布状态的最长秒数
    "amazon_auto_fix_attrs": True,       # 自动补齐缺失必填属性后重发
    "amazon_auto_fix_title": True,       # 标题重复/超长/违规自动修复
    "amazon_auto_fix_description": True, # 描述/卖点/关键词违规自动清洗
    "amazon_auto_ai_fix": False,         # 内容违规时用 AI 重写描述（付费，默认关）
    # ---------------- TikTok Shop 刊登预设（契约 §3.7） ----------------
    "tiktok_cookies": "",
    "tiktok_shop_id": "",
    "tiktok_shop_name": "",
    "tiktok_site": "",               # GB/US/…（L7 站点代码）
    "tiktok_site_name": "",
    "tiktok_list_state": "draft",
    "tiktok_test_product_id": "",
    "tiktok_category_id": "",
    "tiktok_category_name": "",
    "tiktok_category_path": "",      # nodePathId a/b/c
    "tiktok_category_list": "[]",    # JSON
    "tiktok_title": "",
    "tiktok_description": "",
    "tiktok_key_features": "[]",     # JSON（US/4Seller）
    "tiktok_search_terms": "[]",     # JSON（US/4Seller）
    "tiktok_images": "[]",           # JSON：主图 URL 列表（第 1 张=主图）
    "tiktok_video_id": "",
    "tiktok_source_url": "",
    "tiktok_product_type": 1,        # 0 单品 / 1 多变种
    "tiktok_price": "",
    "tiktok_stock": 0,
    "tiktok_sku": "",
    "tiktok_weight": "",
    "tiktok_length": "",
    "tiktok_width": "",
    "tiktok_height": "",
    "tiktok_dimension_unit": "CENTIMETER",
    "tiktok_warehouse_id": "",
    "tiktok_brand_id": "",
    "tiktok_manufacturer_id": "",
    "tiktok_responsible_person_id": "",
    "tiktok_product_attributes": "",
    "tiktok_op": 1,
    "tiktok_save_mode": "",
    "tiktok_use_ai": False,
    "tiktok_key_feature_count": 5,
    "tiktok_title_prompt": "",
    "tiktok_description_prompt": "",
    "tiktok_key_features_prompt": "",
    "tiktok_search_terms_prompt": "",
    "tiktok_last_status": "",
    # Shopee 使用独立配置与保存端点；不得复用速卖通店铺/模板。
    "shopee_mode": "global",
    "shopee_cookies": "",
    "shopee_shop_id": "",
    "shopee_site": "global",
    "shopee_test_product_id": "",
    "shopee_title_prompt": "",
    "shopee_description_prompt": "",
    # Alibaba 提示词单独存储，保护速卖通已有文案配置。
    "alibaba_title_prompt": "",
    "alibaba_detail_prompt": "",
    "alibaba_sku_prompt": "",
    "alibaba_review_text_prompt": "",
    "window_size": "1280x800",
    "ui_theme": "dark",             # dark | light
    "image_retry_times": 1,         # 图片接口 5xx 自动重试次数（0=不重试）      # 启动窗口大小（小 1100x760 / 中 1280x800 / 大 1512x945）

}



# Defaults for automatic source collection; never borrow single-item editor values.
for _platform in ("alibaba", "amazon", "tiktok", "shopee"):
    from .collect_defaults import PARAMETERS as _RUNTIME_PARAMETERS
    DEFAULTS.update({
        _platform + "_collect_keyword": "",
        _platform + "_collect_limit": 10,
        _platform + "_collect_selected_ids": "[]",
        _platform + "_collect_op": "1",
        _platform + "_collect_ai": False,
        _platform + "_collect_ai_mode": "missing",
        _platform + "_collect_ai_fields": json.dumps({
            "alibaba": ["title", "description", "keywords"],
            "amazon": ["title", "bullets", "description", "search_terms"],
            "tiktok": ["title", "description", "key_features", "search_terms"],
            "shopee": ["title", "description"],
        }[_platform]),
        _platform + "_collect_ai_sku": False,
        _platform + "_collect_ai_attributes": False,
        _platform + "_collect_sku_prompt": "",
        _platform + "_collect_attribute_prompt": "",
        _platform + "_collect_auto_repair": True,
        _platform + "_collect_max_attempts": 2,
        _platform + "_collect_images": False,
        _platform + "_collect_image_indices": "1",
        _platform + "_collect_image_rules": "",
        _platform + "_collect_image_prompt": "将商品图处理为纯白背景棚拍图。保持商品形状、结构、颜色、材质和比例，不新增文字或物件。",
        _platform + "_collect_overrides": "{}",
        _platform + "_collect_ai_max_calls": 100,
        _platform + "_collect_ai_budget": 0.0,
        _platform + "_collect_text_call_price": 0.0,
        _platform + "_collect_image_call_price": 0.0,
        _platform + "_collect_ai_cache": True,
        **{_platform + "_collect_" + suffix: spec[1] for suffix, spec in _RUNTIME_PARAMETERS.items()},
    })


def config_dir() -> Path:
    override = os.environ.get("AURORA_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
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

# 旧版「设备授权码 / ECDSA voucher / 迁移引导」配置键：契约 §10 已删除。
# 老 config.json 里出现这些键时直接忽略（不报错、不参与逻辑），下次保存即清掉。
LEGACY_LICENSE_KEYS = (
    "license_identity",
    "license_cache_ok",
    "license_cache_legacy",
    "license_cache_expiry",
    "license_cache_ts",
    "license_public_key",
    "license_voucher",
    "license_signature",
    "license_expiry",
    "license_voucher_ts",
    "license_client_type",
    "license_account_bound",
    "license_legacy_all",
    "license_migration_prompted_at",
)


class AppConfig:
    """字典式配置对象：cfg.xxx 读写，自动持久化。"""

    DEFAULTS = DEFAULTS

    def __init__(self, data: dict | None = None):
        src = dict(data or {})
        for old, new in ALIASES.items():
            if old in src and new not in src:
                src[new] = src[old]
        # 旧版设备授权相关键已废弃（契约 §10）：升级时静默忽略，不留进新配置。
        for legacy_key in LEGACY_LICENSE_KEYS:
            src.pop(legacy_key, None)
        d = {**DEFAULTS, **src}
        # 升级不覆盖用户自定义提示词：只补缺失的新键，不再按前缀自动改值。
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
            try:
                if _restore_missing_config():
                    cls.loaded_from = "migrated"
                else:
                    cls.loaded_from = "new"
                    return cls()
            except Exception:
                cls.loaded_from = "new"
                return cls()
        cfg = cls._load_file(p)
        # 升级/启动不得改写用户已保存的提示词。旧默认值迁移已移除，
        # 用户自定义提示词、Cookie、店铺和任务设置全部以 config.json 为准。
        return cfg

    @classmethod
    def _load_file(cls, p) -> "AppConfig":
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
        """Serialize concurrent autosaves before replacing the shared temporary file."""
        with _SAVE_LOCK:
            self._save_atomic()

    def _save_atomic(self) -> None:
        """原子写：先写临时文件，再替换；保留一份 .bak 防止断电损坏。"""
        import os as _os
        import shutil as _sh
        p = self.path()
        data = json.dumps(self._d, ensure_ascii=False, indent=2)
        previous = getattr(self, "_last_saved_state", None)
        if previous and previous[:2] == (str(p), data):
            try:
                if p.stat().st_mtime_ns == previous[2]:
                    return
            except OSError:
                pass
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        try:
            if p.exists():
                _sh.copy2(p, p.with_suffix(".bak"))
        except Exception:
            pass
        _os.replace(tmp, p)
        object.__setattr__(self, "_last_saved_state", (str(p), data, p.stat().st_mtime_ns))


def _legacy_config_candidates() -> list[Path]:
    """旧版本/便携版/更新前备份的配置位置，用于升级后找回登录与 API。"""
    out = []
    try:
        base = config_dir()
        out += [base / "config.json", base / "config.pre-update.json"]
        out += sorted(base.glob("config.pre-update-*.json"), reverse=True)
        out += [base / "config.bak"]
    except Exception:
        pass
    try:
        import sys as _sys
        exe = Path(_sys.executable).parent
        out += [exe / "config.json", exe / "config" / "config.json",
                exe.parent / "config.json", exe.parent / "config" / "config.json"]
        out += sorted(exe.glob("config.backup-*.json"), reverse=True)
        out += sorted(exe.glob("config.pre-update-*.json"), reverse=True)
    except Exception:
        pass
    try:
        import os as _os
        appdata = Path(_os.environ.get("APPDATA", "") or "")
        if appdata:
            for name in ("AuroraList", "AePublisherClone", "ae-publisher-clone",
                         "AuroraList-portable"):
                out += [appdata / name / "config.json"]
        home = Path.home()
        for name in ("AuroraList", "AePublisherClone", "ae-publisher-clone"):
            out += [home / name / "config.json", home / ".config" / name / "config.json"]
    except Exception:
        pass
    seen, uniq = set(), []
    for p in out:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key); uniq.append(p)
    return uniq


def backup_config(reason: str = "update") -> Path | None:
    """备份当前配置；更新安装/重置前调用，避免 API Key 与登录丢失。"""
    import shutil
    import time as _time
    try:
        p = AppConfig.path()
        if not p.exists():
            return None
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        dst = p.with_name(f"config.{reason}-{stamp}.json")
        shutil.copy2(p, dst)
        return dst
    except Exception:
        return None


def _restore_missing_config() -> bool:
    """当前 config.json 不存在时，从旧路径/更新备份迁移一份。"""
    import shutil
    p = AppConfig.path()
    if p.exists():
        return False
    for cand in _legacy_config_candidates():
        try:
            if not cand.is_file() or cand.resolve() == p.resolve():
                continue
            data = json.loads(cand.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cand, p)
            return True
        except Exception:
            continue
    return False


def ensure_alibaba_defaults(cfg: "AppConfig") -> None:
    """给 Alibaba 补安全默认值；只补空值，不覆盖用户已保存内容。"""
    import json as _json
    try:
        raw = str(getattr(cfg, "alibaba_attr_presets", "") or "").strip()
        try:
            presets = _json.loads(raw) if raw else {}
        except Exception:
            presets = {}
        if not isinstance(presets, dict):
            presets = {}
        for k, v in (("Place of Origin", "China"), ("Condition", "New"),
                     ("Warranty", "1 Year"), ("Core Components", "Motor"),
                     ("Model Number", "OEM")):
            presets.setdefault(k, v)
        cfg.set("alibaba_attr_presets", _json.dumps(presets, ensure_ascii=False))
        try:
            if int(float(getattr(cfg, "alibaba_min_order_quantity", 1) or 0)) <= 0:
                cfg.set("alibaba_min_order_quantity", 1)
        except Exception:
            cfg.set("alibaba_min_order_quantity", 1)
        if not str(getattr(cfg, "alibaba_weight", "") or "").strip():
            cfg.set("alibaba_weight", "100")
        if not str(getattr(cfg, "alibaba_package_size", "") or "").strip():
            cfg.set("alibaba_package_size", "10X10X10")
        if not str(getattr(cfg, "alibaba_hs_code", "") or "").strip():
            cfg.set("alibaba_hs_code", "84791000")
        from .alibaba_company import COMPANY_PROFILE
        for key, val in (("alibaba_company_name", COMPANY_PROFILE["company_name"]),
                         ("alibaba_company_website", COMPANY_PROFILE["website"]),
                         ("alibaba_company_address", COMPANY_PROFILE["location"]),
                         ("alibaba_company_license", COMPANY_PROFILE["business_license"])):
            if not str(getattr(cfg, key, "") or "").strip():
                cfg.set(key, val)
    except Exception:
        pass


_lock = threading.Lock()
_cache: AppConfig | None = None


def _preset_path() -> Path | None:
    """随包发布的默认配置（assets/preset_defaults.json）。"""
    import sys
    cands = []
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        cands.append(Path(mei) / "assets" / "preset_defaults.json")
    cands.append(Path(__file__).resolve().parents[2] / "assets" / "preset_defaults.json")
    exe = getattr(sys, "executable", "")
    if exe:
        cands.append(Path(exe).parent / "assets" / "preset_defaults.json")
        cands.append(Path(exe).parent.parent / "assets" / "preset_defaults.json")
    for c in cands:
        try:
            if c.is_file():
                return c
        except Exception:
            continue
    return None


def _apply_preset_defaults() -> None:
    """把打包默认值合并进 DEFAULTS；用户自己的 config.json 仍然优先。"""
    p = _preset_path()
    if not p:
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULTS:
                DEFAULTS[k] = v


_apply_preset_defaults()


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
