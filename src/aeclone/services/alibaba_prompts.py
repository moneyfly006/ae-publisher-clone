"""Alibaba 国际站 AI 提示词预设（行业预设 + 功能预设）。

背景
----
``config.DEFAULTS`` 里的 title/detail/sku 提示词是按 AliExpress 零售场景写的
（标题按 125 字符控制、详情不强制 HTML/图片占位符），直接拿到 Alibaba 国际站
有三个问题：

1. 标题上限不一致：Alibaba subject ≤128 字符，旧提示词要求 ≤125；
2. 详情缺少 Alibaba 强约束：HTML 片段 + 原详情图 ``<img data-detail-img="N">``
   占位、禁止联系方式/促销/编造参数；
3. 行业不对口：集装箱泳池、电动 ATV 等品类缺行业词与卖点结构。

本模块只负责「提示词文本 + 元数据」：不联网、不读配置、不保存配置、不引入
第三方依赖。接入方式（Lead 在 app.py 接线）::

    from aeclone.services import alibaba_prompts as AP
    preset = AP.resolve_preset("container_pool")   # 也接受 "泳池"/"pool"/中文名
    AP.apply_to_cfg(cfg, preset)                   # 只改内存 cfg 的 prompt 键

预设结构（扁平 dict）
--------------------
每个预设都包含：

- ``id`` / ``name`` / ``kind``（industry|function）/ ``industry``
- ``applicable_categories``：适用品类（中文，UI 展示用）
- ``aliases``：resolve_preset 的容错别名（中/英文）
- ``title_prompt`` / ``detail_prompt`` / ``sku_prompt``：直接喂
  ``TextPipeline.make_title/make_detail/make_skus`` 的 system prompt
- ``keyword_prompt`` / ``keyword_strategy``：关键词生成提示词与策略说明
- ``review_prompt``：内容审查 system prompt（对应 cfg.review_text_prompt）
- ``compliance_notes``：``banned_words`` / ``notes`` / ``images``

``apply_to_cfg`` 只写 ``PROMPT_KEYS`` 里的键，不写其它配置、不调用 ``save()``。
"""
from __future__ import annotations

import re

from .alibaba_payload import MAX_KEYWORDS, MAX_SUBJECT, MIN_KEYWORDS

PRESET_VERSION = "2026-11-22.2"
DEFAULT_PRESET_ID = "container_pool"

# apply_to_cfg 允许写入的配置键（与 config.DEFAULTS / schema_ai / schema_review 对齐）
PROMPT_KEYS = ("title_prompt", "detail_prompt", "sku_prompt", "review_text_prompt")

# 配置键 → 预设字段名（审查提示词在 cfg 里叫 review_text_prompt，预设里叫 review_prompt）
_CFG_TO_PRESET = {
    "title_prompt": "title_prompt",
    "detail_prompt": "detail_prompt",
    "sku_prompt": "sku_prompt",
    "review_text_prompt": "review_prompt",
}

# 通用合规词表：既用于提示词，也用于本地输出体检（find_banned 之外的 Alibaba 补充）
BANNED_WORDS = [
    # 平台/广告法常见违规与促销词
    "cheap", "cheapest", "replica", "fake", "copy", "knockoff",
    "best price", "best seller", "free shipping", "hot sale",
    "discount", "promotion", "cashback", "lowest price",
    "no.1", "number one", "100%", "guaranteed", "miracle",
    # 联系方式/站外引导
    "whatsapp", "wechat", "weixin", "skype", "telegram",
    # 医疗/功效（未加限定即违规）
    "cure", "treat disease", "therapeutic", "medical grade",
]

_CONTACT_NOTE = (
    "禁止邮箱、电话、WhatsApp/WeChat/Skype/Telegram、网址、二维码、第三方平台名"
    "（Amazon/AliExpress/eBay 等）与站外交易引导。"
)
_NO_FABRICATION_NOTE = (
    "型号、尺寸、功率、容量、产地、质保、认证（CE/ISO/EPA/DOT/SGS 等）只能来自原文；"
    "原文没有的不写、不用“可定制”冒充已有参数。"
)
_ABSOLUTE_NOTE = (
    "禁止绝对化/夸大用语：best、No.1、100%、cheapest、guaranteed、world leading、"
    "FDA approved 等；禁止无依据的医疗/功效/安全承诺。"
)
_IMAGE_NOTE = (
    "主图 6 张（纯白或浅色纯背景、1:1、无文字/水印/二维码/拼接边框）；"
    "详情图保留原图，用占位符按章节排版，不把联系方式/促销图带进详情。"
)

_TITLE_TAIL = (
    "4) 禁止中文、引号、emoji、货币符号、绝对化与促销词，不得出现品牌词"
    "（原文品牌也只在必要时保留，不写 for 以外的品牌）；\n"
    "5) 只输出一行英文标题，不要解释、不要编号、不要 Markdown。"
)

_DETAIL_TAIL = (
    "图片规则：原文详情图改用占位符 <img data-detail-img=\"N\">（N 为 1 基序号）"
    "按章节插入，至少 2 张，不要全部堆在开头或结尾；\n"
    "排版：<h3>/<p>/<ul><li>/<table>，1800~3500 字符，短段落便于移动端阅读；\n"
    "合规：全英文；" + _CONTACT_NOTE + _ABSOLUTE_NOTE + _NO_FABRICATION_NOTE +
    "不要 <style>/<script>/<iframe>；只输出 HTML 片段，不要代码块围栏。"
)

_SKU_RULES = (
    "1) 每个 ≤20 个字符，仅允许 A-Z a-z 0-9 空格 - _ . ( )；\n"
    "2) 统一格式：[关键属性]-[规格]，例如 3T-20M、220V-EU、15FT-BLUE、2PCS-SET；\n"
    "3) 单位用缩写并紧跟数字（ft/m/V/Ah/W/kg），颜色用英文首字母大写；\n"
    "4) 去掉中文、emoji、括号注释、品牌词与促销词；同一批风格一致；\n"
    "5) 输出行数与输入一致、顺序不变，每行一个，不编号、不解释；无法识别的保留"
    "最核心英文词（如型号/规格）。"
)


# ---------------- 编造风险体检（AI 输出后置检查，离线） ----------------
_FAB_UNIT = re.compile(
    r"\b(\d+(?:\.\d+)?)[\s-]*(ft|feet|foot|m|mm|cm|kg|kw|w|v|ah|inch|in|years?|months?)\b",
    re.I,
)
_FAB_MATERIALS = (
    "fiberglass", "stainless steel", "carbon steel", "steel frame", "galvanized",
    "aluminum", "acrylic", "tempered glass",
)
_FAB_CERTS = ("ce", "iso", "sgs", "fda", "epa", "dot", "ul", "rohs", "tuv")
_FAB_ABSOLUTE = ("best", "cheapest", "no.1", "100%", "guaranteed", "world class", "world-class")


def _plain(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()


def fabrication_warnings(text: str, source_text: str = "") -> list[str]:
    """本地「编造风险」体检：生成文本出现原文没有的规格数字/材质/认证时给出中文提醒。

    - 返回 ``[]`` 表示未发现明显编造；不修改文本，供 UI 在 AI 生成后展示 warning；
    - ``source_text`` 应传「商品原标题 + 原详情纯文本」，覆盖越全误报越少；
    - 只能发现「原文完全没有」的情况，不能判断参数与原文是否一致（仍需人工复核）。
    """
    gen = _plain(text).lower()
    src = _plain(source_text).lower()
    out: list[str] = []
    for m in _FAB_UNIT.finditer(gen):
        token = m.group(0).lower()
        num, unit = m.group(1), m.group(2).lower()
        grounded = bool(
            re.search(rf"\b{re.escape(num)}[\s-]*{re.escape(unit)}\b", src)
            or (unit in src and re.search(rf"\b{re.escape(num)}\b", src))
        )
        if not grounded:
            out.append(f"规格数字疑似编造：{token}（原文无对应数据）")
    for mat in _FAB_MATERIALS:
        if mat in gen and mat not in src:
            out.append(f"材质词疑似编造：{mat}（原文未出现）")
    for cert in _FAB_CERTS:
        if re.search(rf"\b{cert}\b", gen) and not re.search(rf"\b{cert}\b", src):
            out.append(f"认证词疑似编造：{cert.upper()}（原文未出现）")
    for w in _FAB_ABSOLUTE:
        if w in gen and w not in src:
            out.append(f"绝对化/夸大用语：{w}")
    return list(dict.fromkeys(out))


def _review_prompt(industry_extra: str) -> str:
    """内容审查提示词：统一基线 + 行业补充。"""
    return (
        "你是 Alibaba.com 商品内容合规审查员。输入是商品标题/关键词/详情 HTML，"
        "只输出一行结论：PASS 或 FAIL|原因关键词（多个用英文逗号分隔），不要解释。\n"
        "FAIL 条件：\n"
        "A) 联系方式：邮箱、电话、WhatsApp/WeChat/Skype/Telegram、网址、二维码、QQ；\n"
        "B) 促销与站外引导：好评返现、折扣、优惠券、免费样品换评论、引导站外交易；\n"
        "C) 绝对化/夸大：best、No.1、100%、cheapest、guaranteed、world leading、"
        "无依据的 FDA/CE 等认证宣称；\n"
        "D) 医疗/功效/安全承诺（治疗、治愈、永久、零风险）；\n"
        "E) 侵权品牌词（Nike/Honda/Yamaha 等未加 for 的第三方品牌）；\n"
        "F) 中文残留、乱码、HTML 标签残缺、代码块围栏、Markdown 标记；\n"
        "G) 与原文明显不符的编造型号/参数/认证；\n"
        "H) 违禁品或危险品描述（毒品、武器、易燃易爆、成人内容）。\n"
        "以上之外一律 PASS；只输出一行。\n"
        "行业补充：" + industry_extra
    )


PRESETS: dict[str, dict] = {
    # ======================= 1. 集装箱泳池 / 泳池设备 =======================
    "container_pool": {
        "id": "container_pool",
        "kind": "industry",
        "name": "集装箱泳池 / 泳池设备（主推）",
        "industry": "集装箱泳池、玻璃钢泳池、地上泳池、泳池设备与配件",
        "applicable_categories": [
            "集装箱泳池 Container Pool",
            "玻璃钢/FRP 泳池 Fiberglass Pool",
            "地上泳池 Above Ground Pool",
            "泳池设备/配件（爬梯、过滤泵、沙缸）",
        ],
        "aliases": [
            "泳池", "集装箱泳池", "泳池设备", "玻璃钢泳池", "地上泳池", "泳池配件",
            "pool", "swimming pool", "container pool", "fiberglass pool",
            "above ground pool", "pool equipment", "pool ladder",
        ],
        "title_prompt": (
            "你是 Alibaba.com 集装箱泳池 / 泳池设备类目的资深英文运营。"
            "基于商品原始信息生成一条可直接上架的英文标题。\n"
            "要求：\n"
            "1) 保留真实产品名与结构形式（container pool / prefabricated pool / "
            "above ground pool / pool ladder）；材质与尺寸只能写原文明确出现的"
            "（原文有 fiberglass / steel frame / acrylic window 才写，原文有具体 ft/m 尺寸才写）；"
            "原文没有具体尺寸或材质时，标题中禁止出现任何尺寸数字（如 40ft）和材质词"
            "（如 fiberglass），用 Customized / Custom Size 代替；保留真实卖点"
            "（ready made / customized / outdoor）；" + _NO_FABRICATION_NOTE + "\n"
            "2) 结构建议：定制/规格词 + 核心产品词 + 材质或结构 + 尺寸 + 场景词，"
            "自然融入 2~4 个搜索词，不堆砌重复词；\n"
            "3) 全英文 ≤128 字符（尽量控制在 120 以内，避免被截断）；\n" + _TITLE_TAIL
        ),
        "detail_prompt": (
            "你是 Alibaba.com 集装箱泳池 / 泳池设备类目的英文详情页文案专家。"
            "基于商品原始信息重写英文 HTML 详情片段。\n"
            "结构（按顺序）：\n"
            "1) 产品概述：是什么、适用场景（villa/hotel/resort/backyard）；"
            "尺寸与颜色定制只在原文提到 customization 时才写；\n"
            "2) 核心规格表 <table>：尺寸/容量/材质/结构/安装方式，只使用原文有依据的数据；"
            "原文没有具体尺寸时禁止写任何 ft/m 数字（如 40ft），写 Custom Size 或省略；"
            "原文没有的材质词（如 fiberglass）不得出现；禁止编造；\n"
            "3) 结构与工艺：池体、钢架、亚克力玻璃窗、循环过滤系统等，只描述原文提到的部分；\n"
            "4) 安装与交付：可提供安装图纸/视频指导、集装箱海运包装，不承诺原文没有的交期；\n"
            "5) 定制与服务：颜色、尺寸、logo、包装定制与售后说明，不承诺原文没有的质保年限/认证。\n"
            + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 泳池类目 SKU 命名专家。输入：每行一个原始 SKU 名称"
            "（可能含中文/颜色/尺寸/容量/套装）。输出：每行一个规范英文 SKU。\n"
            "规则：\n" + _SKU_RULES +
            "\n6) 泳池类示例：15FT-BLUE、20FT-WHITE、40FT-CUSTOM、LADDER-3STEP、POOL-EQUIP。"
        ),
        "keyword_prompt": (
            "你是 Alibaba 集装箱泳池类目关键词专家。根据产品标题与属性生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）：\n"
            "1 个核心产品词（container swimming pool / fiberglass pool / pool ladder）；\n"
            "+ 1~2 个结构/材质词（steel frame、acrylic window、above ground）；\n"
            "+ 1~2 个场景/采购词（villa pool、outdoor pool、pool equipment）。\n"
            "不得重复、不得使用品牌词、不得堆砌；每个关键词 1~3 个单词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个（Alibaba 后台上限 {MAX_KEYWORDS}）",
            "composition": "1 核心产品词 + 1~2 属性/材质词 + 1~2 场景/采购词",
            "examples": ["container pool", "fiberglass swimming pool",
                         "above ground pool", "pool ladder", "outdoor pool"],
            "rules": [
                "优先保留商品原有英文关键词短语（不把 fiberglass pool 拆成 fiberglass/pool）",
                "不重复标题里已堆砌的词，不写品牌词",
                "关键词可小写；后台会自动去重，保持 3 个以上即可",
            ],
        },
        "review_prompt": _review_prompt(
            "泳池类重点检查“医疗/治疗/康复”功效宣称、无依据的 CE/SGS 认证、"
            "带 WhatsApp/邮箱/Skype 的报价引导（原文详情里常见，必须判 FAIL），"
            "以及超出原文的尺寸/质保承诺。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                _CONTACT_NOTE,
                _NO_FABRICATION_NOTE,
                _ABSOLUTE_NOTE,
                "原详情里常见 “contact us for price / please leave message / WhatsApp” 段落，"
                "AI 重写时必须删除，不能原样带出。",
                "标题 ≤128 字符；关键词 3~5 个；详情为 HTML 片段（textDesc）。",
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 2. 机械 / 工业设备 =======================
    "machinery_industrial": {
        "id": "machinery_industrial",
        "kind": "industry",
        "name": "机械 / 工业设备",
        "industry": "工程机械、包装机械、机床、激光设备、泵阀、输送设备等",
        "applicable_categories": [
            "工程机械（挖掘/装载/起重）",
            "包装/食品加工机械",
            "数控机床/激光/金属加工",
            "泵阀/压缩机/输送/液压",
        ],
        "aliases": [
            "机械", "工业设备", "机械设备", "工程机械", "机床", "包装机械",
            "machine", "machinery", "industrial equipment", "industrial machine",
        ],
        "title_prompt": (
            "你是 Alibaba.com 机械/工业设备类目的资深英文运营。基于商品原始信息生成"
            "一条可直接上架的英文标题。\n"
            "要求：\n"
            "1) 保留真实设备类型（如 wheel loader / filling machine / CNC lathe / "
            "fiber laser cutting machine）、型号、核心规格（功率 kW、产能、加工尺寸、"
            "载重、电压）与真实用途；" + _NO_FABRICATION_NOTE + "\n"
            "2) 结构建议：设备类型 + 关键规格 + 适用工况/行业，自然融入 2~4 个搜索词，"
            "不堆砌、不写促销词；\n"
            "3) 全英文 ≤128 字符（尽量 ≤120）；\n" + _TITLE_TAIL
        ),
        "detail_prompt": (
            "你是 Alibaba.com 机械/工业设备类目的英文详情页文案专家。基于原始信息重写"
            "英文 HTML 详情片段。\n"
            "结构（按顺序）：\n"
            "1) 设备定位与适用场景（行业/工况）；\n"
            "2) 核心参数表 <table>：功率、产能、尺寸、重量、电压、控制系统等，只写原文有"
            "依据的数据，缺失项省略或写 Customizable；\n"
            "3) 结构与工作原理：关键部件、材质、操作流程；\n"
            "4) 适用行业与典型客户场景（不编造客户案例）；\n"
            "5) 定制/服务：OEM/ODM、电压/插头、包装与交付、质保说明（原文没有就不写年限）。\n"
            + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 机械类目 SKU 命名专家。输入：每行一个原始 SKU 名称"
            "（可能含中文/型号/电压/功率/规格）。输出：每行一个规范英文 SKU。\n"
            "规则：\n" + _SKU_RULES +
            "\n6) 机械类示例：1.5KW-220V、3T-20M、MODEL-A-380V、SS304-10MM。"
        ),
        "keyword_prompt": (
            "你是 Alibaba 机械类目关键词专家。根据产品标题与属性生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）：\n"
            "1 个核心设备词（如 wheel loader / filling machine / laser cutter）；\n"
            "+ 1~2 个规格/型号词（1.5kw、3t、20m）；\n"
            "+ 1~2 个应用/行业词（construction、food packing、metal cutting）。\n"
            "不得重复、不得使用品牌词、不得堆砌；每个关键词 1~3 个单词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个（Alibaba 后台上限 {MAX_KEYWORDS}）",
            "composition": "1 核心设备词 + 1~2 规格/型号词 + 1~2 应用/行业词",
            "examples": ["wheel loader", "filling machine", "fiber laser cutter",
                         "cnc lathe", "conveyor belt"],
            "rules": [
                "型号词只有在原文出现时才能写，不能为了关键词编造型号",
                "避免只写泛词（machine、equipment），必须有 1 个具体设备词",
                "不写促销词与绝对化词",
            ],
        },
        "review_prompt": _review_prompt(
            "机械类重点检查：无依据的 CE/ISO/EPA 认证、编造型号与功率/产能、"
            "“100% 安全/零故障”等绝对化承诺、以及报价联系方式（邮箱/电话/WhatsApp）。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                _CONTACT_NOTE,
                _NO_FABRICATION_NOTE,
                _ABSOLUTE_NOTE,
                "重工业设备不得写“medical grade / food grade”除非原文明确且与类目相符。",
                "参数单位统一（kW/kg/mm/m），不混用中英单位。",
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 3. 电动 ATV / 代步 =======================
    "electric_atv": {
        "id": "electric_atv",
        "kind": "industry",
        "name": "电动 ATV / 代步车",
        "industry": "电动 ATV、四轮越野车、UTV、电动代步/滑板车",
        "applicable_categories": [
            "电动 ATV Electric ATV",
            "四轮沙滩车/UTV Quad Bike / UTV",
            "电动代步车/滑板车 E-scooter",
        ],
        "aliases": [
            "电动atv", "atv", "电动越野车", "沙滩车", "四轮车", "代步车", "滑板车",
            "electric atv", "quad bike", "utv", "e-scooter", "electric scooter",
        ],
        "title_prompt": (
            "你是 Alibaba.com 电动 ATV / 四轮代步车类目的资深英文运营。基于商品原始信息"
            "生成一条可直接上架的英文标题。\n"
            "要求：\n"
            "1) 保留真实车型（electric ATV / quad bike / UTV / e-scooter）、电机功率（W）、"
            "电池规格（V/Ah）、驱动形式（2WD/4WD）、轮胎尺寸、载重/速度（仅原文有）；"
            + _NO_FABRICATION_NOTE + "\n"
            "2) 结构建议：动力/车型词 + 核心规格 + 场景（farm/off-road/beach/patrol），"
            "自然融入 2~4 个搜索词，不堆砌；\n"
            "3) 全英文 ≤128 字符（尽量 ≤120）；\n" + _TITLE_TAIL
        ),
        "detail_prompt": (
            "你是 Alibaba.com 电动 ATV / 四轮代步车类目的英文详情页文案专家。基于原始信息"
            "重写英文 HTML 详情片段。\n"
            "结构（按顺序）：\n"
            "1) 产品概述与适用场景（farm/ranch、off-road、beach、patrol、kids 等，"
            "只写原文支持的场景）；\n"
            "2) 核心参数表 <table>：电机功率、电池 V/Ah、续航、最高速度、载重、轮胎、"
            "制动、驱动形式——只写原文数据，缺失项省略，严禁编造续航与速度；\n"
            "3) 结构与配置：车架、悬挂、制动、灯光、仪表；\n"
            "4) 使用与安全：充电、电池保养、佩戴头盔、限速与场地要求；\n"
            "5) 包装与交付：木箱/纸箱包装、可定制颜色与 logo（原文有才写）。\n"
            + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 电动 ATV 类目 SKU 命名专家。输入：每行一个原始 SKU 名称"
            "（可能含中文/电压/颜色/电池容量）。输出：每行一个规范英文 SKU。\n"
            "规则：\n" + _SKU_RULES +
            "\n6) ATV 类示例：48V-20AH、60V-BLACK、1000W-4WD、12IN-RED。"
        ),
        "keyword_prompt": (
            "你是 Alibaba 电动 ATV 类目关键词专家。根据产品标题与属性生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）：\n"
            "1 个核心车型词（electric atv / quad bike / utv / electric scooter）；\n"
            "+ 1~2 个动力规格词（48v、1000w、4wd、20ah）；\n"
            "+ 1~2 个场景/人群词（off road、farm、adult、kids）。\n"
            "不得重复、不得使用品牌词、不得堆砌；每个关键词 1~3 个单词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个（Alibaba 后台上限 {MAX_KEYWORDS}）",
            "composition": "1 核心车型词 + 1~2 动力/规格词 + 1~2 场景/人群词",
            "examples": ["electric atv", "quad bike", "1000w atv", "4wd utv",
                         "electric scooter"],
            "rules": [
                "不写 street legal / road legal / DOT approved 除非原文明确且有依据",
                "不写品牌（Honda/Yamaha/Polaris 等）",
                "规格词必须来自原文，不能把常见配置当默认值",
            ],
        },
        "review_prompt": _review_prompt(
            "ATV 类重点检查：street legal / road legal / DOT/EPA 认证等无依据宣称、"
            "速度/续航/载重夸大、儿童可骑行的安全暗示、品牌侵权（Honda/Yamaha 等）、"
            "以及电池运输合规风险（不能承诺空运）。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS + [
                "street legal", "road legal", "dot approved", "epa approved",
            ],
            "notes": [
                _CONTACT_NOTE,
                _NO_FABRICATION_NOTE,
                "不得宣称 street legal / road legal / DOT / EPA，除非原文有明确依据；"
                "不能承诺可空运（锂电池运输限制）。",
                "速度、续航、载重只能写原文参数，禁止把行业常见值当默认值。",
                "儿童车型不得写成人可用的速度/载重暗示。",
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 4. 通用工业品 =======================
    "general_industrial": {
        "id": "general_industrial",
        "kind": "industry",
        "name": "通用工业品",
        "industry": "五金工具、配件、工业耗材、网袋等通用工业品",
        "applicable_categories": [
            "五金工具/配件",
            "工业耗材/网袋/包装材料",
            "通用机械配件",
        ],
        "aliases": [
            "通用工业品", "通用", "五金", "工具", "配件", "耗材", "网袋",
            "general", "general industrial", "hardware", "tools",
            "mesh bag", "industrial supplies",
        ],
        "title_prompt": (
            "你是 Alibaba.com 通用工业品类目的资深英文运营。基于商品原始信息生成一条"
            "可直接上架的英文标题。\n"
            "要求：\n"
            "1) 保留真实品名、材质、规格（尺寸/厚度/目数/承重/件数）与真实用途；"
            + _NO_FABRICATION_NOTE + "\n"
            "2) 结构建议：品名 + 材质/规格 + 用途/场景，自然融入 2~4 个搜索词，不堆砌；\n"
            "3) 全英文 ≤128 字符（尽量 ≤120）；\n" + _TITLE_TAIL
        ),
        "detail_prompt": (
            "你是 Alibaba.com 通用工业品类目的英文详情页文案专家。基于原始信息重写"
            "英文 HTML 详情片段。\n"
            "结构（按顺序）：\n"
            "1) 产品概述与用途；\n"
            "2) 规格表 <table>：材质/尺寸/厚度/目数/承重/件数等，只写原文数据；\n"
            "3) 材质与工艺：原料、加工方式、表面处理；\n"
            "4) 适用场景与配套建议；\n"
            "5) 包装、MOQ 与定制（OEM/ODM、logo、包装），原文没有的不写。\n"
            + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 通用工业品 SKU 命名专家。输入：每行一个原始 SKU 名称，输出"
            "每行一个规范英文 SKU。\n规则：\n" + _SKU_RULES +
            "\n6) 通用示例：SS304-10MM、100PCS-SET、40X60CM、BLACK-L。"
        ),
        "keyword_prompt": (
            "你是 Alibaba 通用工业品关键词专家。根据产品标题与属性生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）：\n"
            "1 个核心产品词 + 1~2 个材质/规格词 + 1~2 个用途/场景词。\n"
            "不得重复、不得使用品牌词、不得堆砌；每个关键词 1~3 个单词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个（Alibaba 后台上限 {MAX_KEYWORDS}）",
            "composition": "1 核心产品词 + 1~2 材质/规格词 + 1~2 用途/场景词",
            "examples": ["mesh bag", "stainless steel fitting", "hardware tools",
                         "protective net", "industrial supplies"],
            "rules": [
                "不写品牌词；不写“best quality”类形容",
                "规格词（目数/尺寸/厚度）必须来自原文",
                "中文习惯词（如“网袋”）要转成英文常用词（mesh bag / net bag）",
            ],
        },
        "review_prompt": _review_prompt(
            "通用工业品重点检查：绝对化质量宣称、无依据认证、联系方式、"
            "以及网袋/包装类目可能出现的“食品级/医用级”无依据宣称。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                _CONTACT_NOTE,
                _NO_FABRICATION_NOTE,
                _ABSOLUTE_NOTE,
                "不写无依据的食品级/医用级/环保认证。",
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 5. 功能预设：详情 HTML =======================
    "detail_html": {
        "id": "detail_html",
        "kind": "function",
        "name": "详情 HTML（通用强约束）",
        "industry": "任意品类：只强化详情 HTML 的结构与合规约束",
        "applicable_categories": ["任意 Alibaba 类目（详情页为主）"],
        "aliases": ["详情html", "详情", "html", "detail", "detail html", "详情页"],
        "title_prompt": (
            "你是 Alibaba.com 国际站英文标题运营。基于商品原始信息生成一条英文标题："
            "保留产品名、型号/材质、核心规格与真实用途，自然融入 2~4 个搜索词；"
            "全英文 ≤128 字符（尽量 ≤120）；禁止中文、引号、emoji、绝对化与促销词；"
            "不得编造原文没有的型号/参数/认证；只输出一行英文标题。"
        ),
        "detail_prompt": (
            "你是 Alibaba.com 国际站英文详情页文案专家。基于商品原始信息重写英文 HTML"
            "详情片段。\n"
            "结构：产品概述 → 核心规格表（<table>，只用原文数据）→ 结构与材质 → "
            "适用场景 → 定制/包装/售后（原文没有的不写）。\n"
            + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 国际站 SKU 命名专家。把输入的每行 SKU 改写成规范英文 SKU：\n"
            + _SKU_RULES
        ),
        "keyword_prompt": (
            "你是 Alibaba 国际站关键词专家。根据产品标题生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）："
            "1 个核心产品词 + 1~2 个属性词 + 1~2 个场景词；不重复、不含品牌词；"
            "只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个",
            "composition": "1 核心产品词 + 1~2 属性词 + 1~2 场景词",
            "examples": ["product keyword", "attribute keyword", "application keyword"],
            "rules": ["保留商品原有短语", "不写品牌词", "不堆砌"],
        },
        "review_prompt": _review_prompt(
            "通用：重点检查详情 HTML 里的联系方式、促销、绝对化、中文残留与编造参数。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                _CONTACT_NOTE,
                "详情必须是 HTML 片段（<h3>/<p>/<ul>/<table>），不要 Markdown、"
                "不要 ``` 代码块、不要整页 <html>/<body>。",
                _NO_FABRICATION_NOTE,
                "图片用 <img data-detail-img=\"N\"> 占位，由 TextPipeline 替换为合规图标签。",
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 6. 功能预设：SKU 命名 =======================
    "sku_naming": {
        "id": "sku_naming",
        "kind": "function",
        "name": "SKU 命名优化（通用）",
        "industry": "任意品类：只强化 SKU 命名规范",
        "applicable_categories": ["任意 Alibaba 类目（多规格/多颜色/多尺寸商品）"],
        "aliases": ["sku", "sku命名", "规格", "变体", "variant", "sku naming"],
        "title_prompt": (
            "你是 Alibaba.com 国际站英文标题运营。基于商品原始信息生成一条英文标题："
            "保留产品名、型号/材质、核心规格与真实用途，自然融入 2~4 个搜索词；"
            "全英文 ≤128 字符（尽量 ≤120）；禁止中文、引号、emoji、绝对化与促销词；"
            "不得编造原文没有的型号/参数/认证；只输出一行英文标题。"
        ),
        "detail_prompt": (
            "你是 Alibaba.com 国际站英文详情页文案专家。基于商品原始信息重写英文 HTML"
            "详情片段：产品概述 → 规格表 → 结构材质 → 场景 → 定制/售后；"
            "只写原文数据；图片用 <img data-detail-img=\"N\"> 占位；" + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 国际站 SKU 命名与变体整理专家。输入：每行一个原始 SKU"
            "（常含中文、颜色/尺寸/容量/数量/套装、括号注释）。\n"
            "目标：输出可直接上架的英文 SKU 列表。\n规则：\n" + _SKU_RULES + "\n"
            "6) 变体维度识别：同一商品的多 SKU 应保持“同前缀 + 差异后缀”，"
            "如 POOL-15FT-BLUE / POOL-15FT-GREY / POOL-20FT-BLUE；\n"
            "7) 没有可识别差异时，用 ITEM-01、ITEM-02 这类中性编号，不要编造材质/性能。"
        ),
        "keyword_prompt": (
            "你是 Alibaba 国际站关键词专家。根据产品标题与 SKU 差异生成 "
            f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个英文关键词（英文逗号分隔）："
            "1 个核心产品词 + 1~2 个变体维度词（颜色/尺寸/容量）+ 1~2 个场景词；"
            "不重复、不含品牌词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个",
            "composition": "1 核心产品词 + 1~2 变体维度词 + 1~2 场景词",
            "examples": ["container pool", "20ft pool", "blue pool", "outdoor pool"],
            "rules": [
                "变体维度词（颜色/尺寸）只在商品实际有该变体时使用",
                "不把 SKU 编号当关键词",
                "不写品牌词",
            ],
        },
        "review_prompt": _review_prompt(
            "重点检查 SKU 是否含中文/超长（>20 字符）/特殊符号、以及 SKU 与标题矛盾。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                "SKU ≤20 字符、仅 ASCII、字母数字与 - _ . ( )；不能有中文/emoji/斜杠。",
                "输出行数与输入一致，顺序不变，不能多出解释行。",
                "SKU 不能把编造的型号/认证写进去。",
                _CONTACT_NOTE,
            ],
            "images": _IMAGE_NOTE,
        },
    },

    # ======================= 7. 功能预设：内容审查 =======================
    "content_review": {
        "id": "content_review",
        "kind": "function",
        "name": "内容审查（Alibaba 合规）",
        "industry": "任意品类：文本/详情合规审查",
        "applicable_categories": ["任意 Alibaba 类目（上架前内容审查）"],
        "aliases": ["审查", "合规", "内容审查", "review", "compliance", "content review"],
        "title_prompt": (
            "你是 Alibaba.com 国际站英文标题运营。基于商品原始信息生成一条合规英文标题："
            "保留产品名、型号/材质、核心规格与真实用途；全英文 ≤128 字符（尽量 ≤120）；"
            "禁止中文、引号、emoji、联系方式、绝对化与促销词；不得编造型号/参数/认证；"
            "只输出一行英文标题。"
        ),
        "detail_prompt": (
            "你是 Alibaba.com 国际站英文详情页文案专家。基于商品原始信息重写英"
            "文 HTML 详情片段：产品概述 → 规格表 → 结构材质 → 场景 → 定制/售后；"
            "只写原文数据；" + _CONTACT_NOTE + _DETAIL_TAIL
        ),
        "sku_prompt": (
            "你是 Alibaba 国际站 SKU 命名专家。把输入的每行 SKU 改写成规范英文 SKU：\n"
            + _SKU_RULES
        ),
        "keyword_prompt": (
            f"你是 Alibaba 国际站关键词专家。生成 {MIN_KEYWORDS}~{MAX_KEYWORDS} 个"
            "英文关键词（英文逗号分隔）：1 个核心产品词 + 1~2 个属性词 + 1~2 个场景词；"
            "不重复、不含品牌词与违禁词；只输出关键词。"
        ),
        "keyword_strategy": {
            "count": f"{MIN_KEYWORDS}~{MAX_KEYWORDS} 个",
            "composition": "1 核心产品词 + 1~2 属性词 + 1~2 场景词",
            "examples": ["core product keyword", "attribute keyword", "application keyword"],
            "rules": ["不使用违禁/促销词", "不含品牌词", "不堆砌"],
        },
        "review_prompt": _review_prompt(
            "这是通用审查基线：逐项检查 A~H 条件；输入可能是标题、关键词或详情 HTML，"
            "任一命中即 FAIL 并输出原因关键词。"
        ),
        "compliance_notes": {
            "banned_words": BANNED_WORDS,
            "notes": [
                "审查输出必须是一行：PASS 或 FAIL|原因关键词；不要把整段原文复述出来。",
                _CONTACT_NOTE,
                _ABSOLUTE_NOTE,
                _NO_FABRICATION_NOTE,
            ],
            "images": _IMAGE_NOTE,
        },
    },
}

PRESET_IDS: list[str] = list(PRESETS)
INDUSTRY_PRESET_IDS: list[str] = [pid for pid, p in PRESETS.items() if p.get("kind") == "industry"]
FUNCTION_PRESET_IDS: list[str] = [pid for pid, p in PRESETS.items() if p.get("kind") == "function"]

_ALIAS_INDEX: dict[str, str] | None = None


def _alias_index() -> dict[str, str]:
    """构建别名 → 预设 id 索引（惰性，避免模块导入时做多余工作）。"""
    global _ALIAS_INDEX
    if _ALIAS_INDEX is not None:
        return _ALIAS_INDEX
    idx: dict[str, str] = {}
    for pid, p in PRESETS.items():
        keys = [pid, str(p.get("name") or ""), str(p.get("industry") or "")]
        keys += [str(a) for a in (p.get("aliases") or [])]
        for k in keys:
            k = k.strip().lower()
            if k and k not in idx:
                idx[k] = pid
    _ALIAS_INDEX = idx
    return idx


def list_presets(kind: str | None = None) -> list[dict]:
    """返回预设列表（浅拷贝，调用方可安全读取；不要原地改 PRESETS）。"""
    rows = [dict(p) for p in PRESETS.values()]
    if kind:
        rows = [p for p in rows if p.get("kind") == kind]
    return rows


def preset_options(kind: str | None = None) -> list[tuple[str, str]]:
    """给 UI 下拉用的 [(id, 名称)]，保持 PRESETS 定义顺序。"""
    return [(p["id"], p["name"]) for p in list_presets(kind)]


def resolve_preset(industry_or_id=None) -> dict:
    """按 id / 中文行业名 / 英文别名解析预设，找不到时回退默认预设。

    - ``None``/空串 → 默认（container_pool）
    - 完整中文名（如 ``"集装箱泳池 / 泳池设备（主推）"``）→ 命中
    - 片段（如 ``"泳池"``、``"pool"``、``"atv"``）→ 最长别名匹配
    - 传入 preset dict → 原样返回（便于调用方二次处理）
    - 未知值 → 默认预设（不抛异常，UI 不会因为脏值崩掉）
    """
    if isinstance(industry_or_id, dict):
        return industry_or_id
    key = str(industry_or_id or "").strip().lower()
    if not key:
        return PRESETS[DEFAULT_PRESET_ID]
    if key in PRESETS:
        return PRESETS[key]
    idx = _alias_index()
    if key in idx:
        return PRESETS[idx[key]]
    # 片段匹配：取最长命中的别名，避免 "pool" 抢先于 "container pool"
    best_pid, best_len = "", 0
    for alias, pid in idx.items():
        if not alias:
            continue
        if alias in key or key in alias:
            if len(alias) > best_len:
                best_len, best_pid = len(alias), pid
    if best_pid:
        return PRESETS[best_pid]
    return PRESETS[DEFAULT_PRESET_ID]


def get_prompt(key: str, industry_or_id=None) -> str:
    """取某个预设的提示词字段（key 如 ``title_prompt``）；不存在返回空串。"""
    return str(resolve_preset(industry_or_id).get(str(key or "")) or "")


def apply_to_cfg(cfg, preset=None) -> list[str]:
    """把预设写进 cfg 的 prompt 键（仅内存），返回实际写入的键名列表。

    - 只写 ``PROMPT_KEYS``（title_prompt/detail_prompt/sku_prompt/
      review_text_prompt），不动其它配置，不调用 ``cfg.save()``；
    - ``cfg`` 兼容 AppConfig 对象与普通 dict；
    - ``preset`` 兼容预设 dict / id / 行业名（走 resolve_preset）。
    """
    p = preset if isinstance(preset, dict) else resolve_preset(preset)
    written: list[str] = []
    for cfg_key in PROMPT_KEYS:
        val = str(p.get(_CFG_TO_PRESET[cfg_key]) or "").strip()
        if not val:
            continue
        if isinstance(cfg, dict):
            cfg[cfg_key] = val
        else:
            setter = getattr(cfg, "set", None)
            if callable(setter):
                setter(cfg_key, val)
            else:
                setattr(cfg, cfg_key, val)
        written.append(cfg_key)
    return written


def describe_preset(preset=None) -> str:
    """一行摘要（日志/UI 提示用）：``名称（id）· 适用品类``。"""
    p = preset if isinstance(preset, dict) else resolve_preset(preset)
    cats = "、".join(str(c) for c in (p.get("applicable_categories") or [])[:3])
    return f"{p.get('name', '')}（{p.get('id', '')}）· {cats}"


__all__ = [
    "PRESET_VERSION", "DEFAULT_PRESET_ID", "PROMPT_KEYS", "BANNED_WORDS",
    "PRESETS", "PRESET_IDS", "INDUSTRY_PRESET_IDS", "FUNCTION_PRESET_IDS",
    "resolve_preset", "apply_to_cfg", "list_presets", "preset_options",
    "get_prompt", "describe_preset", "fabrication_warnings",
]
