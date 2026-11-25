"""Amazon 专业 AI 提示词预设库（标题 / 五点 / 描述 / Search Terms / SKU / 图片）。

设计目标
--------
- 六类预设，可在设置页/刊登页下拉选择（``preset_options``），套用后仍可编辑；
- 预设只是「上游指令模板」：文本类预设会被 ``amazon_prompts`` 的 prompt builder
  包上站点信息与商品原文，再交给 AI；图片类预设由 UI 直接 ``render`` 成最终提示词；
- 所有预设都写成 Amazon 合规约束（长度上限、同词频次、禁止促销/联系方式/编造）；
- 支持占位符（仅下列）：``{country}{code}{language}{lang}{title_limit}
  {bullet_limit}{bullet_suggest}{count}{desc_bytes}{st_bytes}{title}{subject}``，
  ``render`` 只替换已知占位符，未知占位符原样保留（防止用户 prompt 里其它花括号被误替换）。

配置映射
--------
``title→amazon_title_prompt``、``bullets→amazon_bullet_prompt``、
``description→amazon_description_prompt``、``search_terms→amazon_search_terms_prompt``、
``sku→amazon_sku_prompt``、``image→amazon_image_prompt``；
选择记录在 ``amazon_<kind>_preset``（bullets 用 ``amazon_bullet_preset``）。

本模块不联网、不读写文件、不依赖 UI。
"""
from __future__ import annotations

PRESET_VERSION = "2026-11-22.1"

PRESET_KINDS: tuple[str, ...] = (
    "title", "highlight", "bullets", "description", "search_terms", "sku", "image",
)

# kind → 写入 cfg 的 prompt 键
PROMPT_CFG_KEYS: dict[str, str] = {
    "title": "amazon_title_prompt",
    "highlight": "amazon_highlight_prompt",
    "bullets": "amazon_bullet_prompt",
    "description": "amazon_description_prompt",
    "search_terms": "amazon_search_terms_prompt",
    "sku": "amazon_sku_prompt",
    "image": "amazon_image_prompt",
}

# kind → 记录当前选择 key 的 cfg 键
SELECTED_CFG_KEYS: dict[str, str] = {
    "title": "amazon_title_preset",
    "highlight": "amazon_highlight_preset",
    "bullets": "amazon_bullet_preset",
    "description": "amazon_description_preset",
    "search_terms": "amazon_search_terms_preset",
    "sku": "amazon_sku_preset",
    "image": "amazon_image_preset",
}

PLACEHOLDERS: tuple[str, ...] = (
    "country", "code", "language", "lang", "title_limit", "bullet_limit",
    "bullet_suggest", "count", "desc_bytes", "st_bytes", "highlight_limit",
    "title", "subject",
)

KIND_LABELS: dict[str, str] = {
    "title": "标题", "highlight": "商品亮点", "bullets": "五点描述", "description": "产品描述",
    "search_terms": "Search Terms", "sku": "SKU", "image": "图片",
}


# ---------------------------------------------------------------------------
# 共用合规约束（避免每条预设重复）
# ---------------------------------------------------------------------------
_TITLE_BASE = (
    "为 {country} 站点（Amazon {code}）写商品标题，使用{language}。\n"
    "硬性要求：\n"
    "1) 长度 ≤ {title_limit} 字符；超过按词边界截断；\n"
    "2) 同一实词最多出现 2 次，不堆砌关键词；\n"
    "3) 保留原文真实型号/规格（功率、容量、尺寸、材质、数量），不编造认证、参数、产地、识别码；\n"
    "4) 禁止 emoji、HTML、引号、全角符号、联系方式、促销与绝对化表述、竞品品牌；\n"
    "5) 只输出一行标题，不要解释、不要编号、不要 Markdown。"
)

_BULLET_BASE = (
    "为 {country} 站点（Amazon {code}）商品写五点描述，使用{language}。\n"
    "硬性要求：\n"
    "1) 恰好 {count} 条，JSON 字符串数组；每条 ≤ {bullet_suggest} 字符（硬上限 {bullet_limit}）；\n"
    "2) 每条以 2~5 个词的卖点短语开头，后接冒号，例如 75 KW DIESEL ENGINE: ...；\n"
    "3) 不得包含 ±、●、emoji、HTML 标签、Markdown、联系方式；禁止促销与绝对化表述；\n"
    "4) 型号、尺寸、功率、材质、认证只能来自原文，不得编造；\n"
    "5) 只输出 JSON 数组，不要代码块围栏与解释。"
)

_DESC_BASE = (
    "为 {country} 站点（Amazon {code}）商品写 HTML 产品描述，使用{language}。\n"
    "硬性要求：\n"
    "1) 只允许 <p> <ul> <li> <b> <br> 标签；禁止脚本/样式/框架/图片标签；\n"
    "2) 总长度 ≤ {desc_bytes} 字节；3~5 段 + 一个参数列表；\n"
    "3) 参数与认证只能来自原文，不编造；禁止联系方式、二维码、促销与医疗/绝对化表述；\n"
    "4) 只输出 HTML 片段，不要 Markdown 代码块围栏与解释。"
)

_ST_BASE = (
    "为 {country} 站点（Amazon {code}）商品生成后台 Search Terms，使用{language}。\n"
    "硬性要求：\n"
    "1) 只输出一行：小写单词/短语，空格分隔，≤ {st_bytes} 字节；\n"
    "2) 去重、不重复单复数、不含停用词，不使用标点/编号/引号；\n"
    "3) 不含品牌词/他人品牌/竞品名，不含促销与联系方式；\n"
    "4) 只输出关键词本身，不要解释。"
)

_SKU_BASE = (
    "为商品生成一个 Amazon 卖家 SKU（Seller SKU），用于后台库存与订单识别。\n"
    "硬性要求：\n"
    "1) 只输出一行 SKU，≤ 40 字符；\n"
    "2) 只允许英文字母、数字、连字符 - 与下划线 _；不得含空格、中文、标点、emoji；\n"
    "3) 大写为主，结构清晰可读，不编造品牌与认证；\n"
    "4) 同一商品 SKU 必须唯一（必要时追加站点/序号后缀）；\n"
    "5) 只输出 SKU 本身，不要解释、不要引号、不要代码块围栏。"
)

_IMAGE_BASE = (
    "用于 Amazon {country} 站点（{language}）的 {subject} 图片处理，商品标题：{title}。\n"
    "硬性要求：\n"
    "1) 不得改变产品本体的形状、颜色、材质、结构、比例与数量；不得新增或删除产品部件；\n"
    "2) 主图不新增文字、水印、Logo、边框、促销角标；副图信息图文字使用{language}且必须来自原文；\n"
    "3) 画面干净、光线自然、细节清晰，符合 Amazon 图片规范（主图纯白或浅色背景）；\n"
    "4) 不出现人物特写、二维码、联系方式、其它平台标识；\n"
    "5) 只输出最终图片，不要解释。"
)


def _title_prompt(intro: str, structure: str, points, example: str) -> str:
    """标题预设统一骨架：基础合规 + 行业结构 + 2 条行业要点 + 英文示例模式。"""
    p1, p2 = points
    return (
        intro + _TITLE_BASE
        + f"\n结构：{structure}"
        + "\n行业要点：\n"
        + f"- {p1}\n"
        + f"- {p2}\n"
        + "英文示例模式（示例仅示意结构，不得照抄内容/参数）："
        + f"`{example}`"
    )


# ---------------------------------------------------------------------------
# 预设内容（有序 dict）
# ---------------------------------------------------------------------------
PRESETS: dict[str, dict[str, dict]] = {
    # ---------------- 商品亮点 ----------------
    "highlight": {
        "north_america_default": {
            "name": "北美商品亮点默认",
            "desc": "≤125 字符、单行、逗号短语，补充标题未覆盖的关键词。",
            "tags": ["北美", "亮点", "Item Highlights"],
            "prompt": (
                "你是 Amazon 商品亮点（Item Highlights）文案专家。请为 {country} 站点"
                "（Amazon {code}）商品写商品亮点。\n"
                "硬性要求：\n"
                "1) Item Highlights 不超过 {highlight_limit} 个字符（含空格），单行；首字母大写，所有介词小写；"
                "该字段具备独立搜索索引权重，标题中无法容纳的关键词可在此补充承接；\n"
                "2) 使用{language}（站点语言），非中文站点禁止中文；\n"
                "3) 用英文逗号分隔的短语，例如 `Stainless Steel, Ages 2-4, Tool-Free Installation`；\n"
                "4) 不要写成完整句子；不要重复标题已有的核心词；重点补充材质、适用型号、独特功能、使用场景；\n"
                "5) 禁止主观评价（如销量第一、绝对化宣传）、促销语、特殊符号、emoji、HTML、"
                "联系方式；不得编造原文没有的参数；\n"
                "6) 只输出一行亮点本身，不要解释、不要引号、不要编号。"
            ),
        },
        "industrial_default": {
            "name": "工业机械亮点",
            "desc": "工业设备亮点：材质、型号、工况和操作优势。",
            "tags": ["工业", "亮点"],
            "prompt": "你是 Amazon 商品亮点文案专家。为 {country} 站点（Amazon {code}）写工业设备亮点，使用{language}；≤ {highlight_limit} 字符、单行、英文逗号分隔；补充材质、适用型号、功能、工况；不重复标题；禁止主观评价、促销词、特殊符号、emoji、HTML、联系方式；保持商品形状与水印合规；只输出亮点。",
        },
        "home_default": {
            "name": "家居亮点",
            "desc": "家居商品亮点：材质、尺寸、安装和使用场景。",
            "tags": ["家居", "亮点"],
            "prompt": "你是 Amazon 商品亮点文案专家。为 {country} 站点（Amazon {code}）写家居商品亮点，使用{language}；≤ {highlight_limit} 字符、单行、英文逗号分隔；补充材质、适用尺寸、安装方式、使用场景；不重复标题；禁止主观评价、促销词、特殊符号、emoji、HTML、联系方式；保持商品形状与水印合规；只输出亮点。",
        },
        "outdoor_default": {
            "name": "户外亮点",
            "desc": "户外商品亮点：耐用材质、便携和适用环境。",
            "tags": ["户外", "亮点"],
            "prompt": "你是 Amazon 商品亮点文案专家。为 {country} 站点（Amazon {code}）写户外商品亮点，使用{language}；≤ {highlight_limit} 字符、单行、英文逗号分隔；补充材质、便携性、独特功能和适用环境；不重复标题；禁止主观评价、促销词、特殊符号、emoji、HTML、联系方式；保持商品形状与水印合规；只输出亮点。",
        },
        "compact_default": {
            "name": "紧凑型亮点",
            "desc": "紧凑型商品亮点：体积、安装、收纳和多场景。",
            "tags": ["紧凑", "亮点"],
            "prompt": "你是 Amazon 商品亮点文案专家。为 {country} 站点（Amazon {code}）写紧凑型商品亮点，使用{language}；≤ {highlight_limit} 字符、单行、英文逗号分隔；补充体积、安装、收纳、使用场景；不重复标题；禁止主观评价、促销词、特殊符号、emoji、HTML、联系方式；保持商品形状与水印合规；只输出亮点。",
        },
    },
    # ---------------- 标题（8） ----------------
    "title": {
        "industrial_b2b": {
            "name": "工业机械 B2B 参数型",
            "desc": "核心品类词 + 关键参数 + 型号 + B2B 采购场景，适合机械/设备类。",
            "tags": ["工业", "机械", "B2B", "参数"],
            "prompt": _title_prompt(
                "你是工业机械类 B2B 跨境 listing 专家。",
                "核心品类词 + 关键参数（功率/容量/尺寸） + 型号 + 采购场景",
                ("吨位/功率/驱动等可量化参数放在场景词之前，采购买家先看参数。",
                 "避免同一型号词与用途词重复堆叠，保持标题可读。"),
                "Diesel Backhoe Loader HW20-28 75 kW 5 Ton for Road Construction",
            ),
        },
        "construction_machinery": {
            "name": "工程机械高转化",
            "desc": "面向挖掘/装载/起重等工程机械，突出工况与核心规格。",
            "tags": ["工程机械", "施工", "高转化"],
            "prompt": _title_prompt(
                "你是工程机械行业 Amazon 运营专家。",
                "产品词 + 吨位/功率 + 驱动形式 + 作业场景（construction/road/farm）",
                ("先写设备类型与吨位/功率，再写驱动形式与作业场景。",
                 "场景词只用真实适用工况，不省略关键限定词也不堆砌。"),
                "4x4 Wheeled Backhoe Loader 5 Ton 75 kW for Heavy Construction",
            ),
        },
        "agri_garden": {
            "name": "农机 / 园林",
            "desc": "面向农机、园林与户外机械，突出适用地形与作业效率。",
            "tags": ["农机", "园林", "户外"],
            "prompt": _title_prompt(
                "你是农机与园林机械类目专家。",
                "产品词 + 排量/功率 + 作业宽度或容量 + 适用地形",
                ("优先展示马力/排量/作业幅宽等硬指标。",
                 "地形与作物场景只写真实适用项，不夸大适用范围。"),
                "Compact Tractor 25 HP 4WD for Farm and Orchard Work",
            ),
        },
        "packaging_food": {
            "name": "包装 / 食品机械",
            "desc": "包装、灌装、食品加工设备，突出产能与材质合规。",
            "tags": ["包装", "食品机械", "产能"],
            "prompt": _title_prompt(
                "你是包装与食品机械类目专家。",
                "设备类型 + 产能/规格 + 材质（原文有才写） + 适用行业",
                ("优先写产能、规格与材质，参数顺序按采购关注度排列。",
                 "行业合规与材质表述只在原文有依据时出现，不新增认证。"),
                "Automatic Filling Machine 60 BPM Stainless Steel for Food Packaging",
            ),
        },
        "cnc_metal": {
            "name": "数控 / 金属加工",
            "desc": "CNC、车床、加工中心，突出轴数、行程与精度。",
            "tags": ["数控", "金属加工", "精度"],
            "prompt": _title_prompt(
                "你是数控与金属加工设备类目专家。",
                "设备类型 + 轴数/行程 + 精度或功率 + 加工对象",
                ("优先写轴数/行程/定位精度等可检索硬参数。",
                 "加工对象与适用材料必须与原文一致，不扩展未声明的材料。"),
                "CNC Machining Center 3 Axis 800 mm Travel for Metal Parts",
            ),
        },
        "laser_welding": {
            "name": "激光 / 焊接",
            "desc": "激光切割、焊接设备，突出功率、幅面与材料。",
            "tags": ["激光", "焊接", "功率"],
            "prompt": _title_prompt(
                "你是激光与焊接设备类目专家。",
                "设备类型 + 功率/幅面 + 适用材料 + 使用场景",
                ("优先写功率、幅面与可切/可焊厚度。",
                 "安全与认证相关表述必须来自原文，不做未验证声明。"),
                "Fiber Laser Welding Machine 1500 W for Stainless Steel Sheet",
            ),
        },
        "seo_high_conversion": {
            "name": "通用工业品高转化 SEO",
            "desc": "不限定行业：核心词 + 长尾词 + 属性词 + 场景词。",
            "tags": ["通用", "SEO", "转化"],
            "prompt": _title_prompt(
                "你是 Amazon 工业品 SEO 标题专家。",
                "核心产品词 + 2~3 个长尾修饰词 + 关键属性 + 适用场景",
                ("核心词后紧跟 2~3 个高意图属性词，再补场景。",
                 "用同义替换避免重复，优先买家常用搜索语序。"),
                "Heavy Duty Industrial Backhoe Loader 5 Ton Diesel 4x4 Machine",
            ),
        },
        "brand_model_spec": {
            "name": "品牌 + 型号 + 规格 + 场景",
            "desc": "品牌背书型结构，适合已有品牌与明确型号的商品。",
            "tags": ["品牌", "型号", "规格"],
            "prompt": _title_prompt(
                "你是品牌型 listing 标题专家。",
                "品牌（原文有才写） + 型号 + 核心规格 + 应用场景",
                ("品牌与型号紧随产品词，便于品牌词与型号检索。",
                 "规格与场景按买家搜索顺序排列，不虚构未提供的品牌。"),
                "Hengwang HW20-28 5 Ton Diesel Backhoe Loader Construction Machine",
            ),
        },
        "short_title": {
            "name": "移动端短标题（≤80 字符）",
            "desc": "移动端展示优先：核心词 + 型号 + 1 个关键规格，≤80 字符。",
            "tags": ["移动端", "短标题", "型号"],
            "prompt": (
                "你是 Amazon 移动端短标题专家。为 {country} 站点（Amazon {code}）"
                "写一个移动端短标题，使用{language}。\n"
                "硬性要求：\n"
                "1) 长度 ≤80 字符，超过按词边界截断；\n"
                "2) 只保留：核心品类词 + 型号 + 1 个关键规格，不放长尾场景与修饰词；\n"
                "3) 同一实词最多出现 1 次，不重复堆词；\n"
                "4) 保留原文真实型号/规格，不编造认证、参数、产地、识别码；\n"
                "5) 禁止 emoji、HTML、引号、全角符号、联系方式、促销与绝对化表述、竞品品牌；\n"
                "6) 只输出一行标题，不要解释、不要编号、不要 Markdown。\n"
                "行业要点：\n"
                "- 型号紧跟在核心品类词之后，规格选买家最关注的一项。\n"
                "- 移动端首屏可见，避免任何可省略的形容词。\n"
                "英文示例模式（示例仅示意结构，不得照抄内容/参数）："
                "`HW20-28 5 Ton Diesel Backhoe Loader 4x4`"
            ),
        },
        "spec_model_first": {
            "name": "型号 / 规格优先",
            "desc": "采购型买家：型号/关键规格前置，产品词随后。",
            "tags": ["型号", "规格", "采购"],
            "prompt": _title_prompt(
                "你是采购型 listing 标题专家。",
                "型号 + 关键规格（功率/吨位/尺寸） + 产品词 + 适用场景",
                ("型号与关键规格前置，便于按型号与参数精确检索。",
                 "产品词紧随其后保证类目相关性，场景只保留 1 个。"),
                "HW20-28 75 kW 5 Ton 4x4 Wheeled Backhoe Loader",
            ),
        },
    },
    # ---------------- 五点（6） ----------------
    "bullets": {
        "benefit_first": {
            "name": "Benefit-first（默认）",
            "desc": "每条先给买家利益点，再补参数支撑。",
            "tags": ["默认", "利益点", "转化"],
            "prompt": (
                "你是 Amazon 卖点文案专家。" + _BULLET_BASE + "\n"
                "写法：每条以买家利益点短语开头，后接冒号，再用 1~2 句说明参数/证据；"
                "利益点必须能被原文参数支撑。"
            ),
        },
        "spec_first": {
            "name": "参数规格优先",
            "desc": "适合专业采购：先给关键规格，再解释用途。",
            "tags": ["参数", "规格", "专业采购"],
            "prompt": (
                "你是工业品参数型文案专家。" + _BULLET_BASE + "\n"
                "写法：每条以参数类别短语开头（如 ENGINE POWER:），后接数值与单位，"
                "再说明带来的作业能力；数值只来自原文。"
            ),
        },
        "scenario": {
            "name": "场景化（施工 / 农场 / 仓储）",
            "desc": "用真实作业场景讲卖点，适合工程、农机、仓储设备。",
            "tags": ["场景", "施工", "农场", "仓储"],
            "prompt": (
                "你是场景化卖点专家。" + _BULLET_BASE + "\n"
                "写法：每条围绕一个真实作业场景（施工、农场、仓储、道路维护）展开，"
                "说明该场景下的效率与可靠性；场景必须符合商品用途。"
            ),
        },
        "b2b_procurement": {
            "name": "B2B 采购关注（耐用 / 维护 / 配件）",
            "desc": "面向批发买家：耐用性、维护成本、配件与售后。",
            "tags": ["B2B", "耐用", "维护", "配件"],
            "prompt": (
                "你是 B2B 采购视角文案专家。" + _BULLET_BASE + "\n"
                "写法：围绕耐用性、维护便利、配件可得性与售后支持展开；"
                "质保/配件/售后信息只能来自原文，不编造承诺。"
            ),
        },
        "differentiation": {
            "name": "差异化对比（不贬低竞品）",
            "desc": "突出与普通方案的结构/配置差异，禁止贬低他人。",
            "tags": ["差异化", "对比", "合规"],
            "prompt": (
                "你是差异化卖点专家。" + _BULLET_BASE + "\n"
                "写法：用中性表述突出结构、配置或工艺上的差异，只讲自身事实；"
                "不得提及或贬低具体竞品品牌，不做无依据对比。"
            ),
        },
        "compliance_safe": {
            "name": "合规安全型",
            "desc": "只写原文可验证事实，不含绝对化、医疗、比较级与未验证声明。",
            "tags": ["合规", "安全", "事实"],
            "prompt": (
                "你是 Amazon 合规卖点文案专家。" + _BULLET_BASE + "\n"
                "写法：每条只陈述原文可验证的事实与参数；不使用绝对化、医疗、"
                "比较级或未验证声明；不暗示效果承诺；参数缺依据时宁可少写。"
            ),
        },
        "faq": {
            "name": "FAQ 式卖点",
            "desc": "用买家常见问题结构写卖点，适合高客单设备。",
            "tags": ["FAQ", "答疑", "高客单"],
            "prompt": (
                "你是 FAQ 型卖点专家。" + _BULLET_BASE + "\n"
                "写法：每条对应一个买家常见疑问（安装、油耗、维护、适配、运输），"
                "先给简短回答再补参数；回答必须来自原文。"
            ),
        },
    },
    # ---------------- 描述（6） ----------------
    "description": {
        "standard_industrial": {
            "name": "标准工业结构（用途→参数→结构→服务）",
            "desc": "通用工业品四段式，信息完整、便于审核。",
            "tags": ["默认", "工业", "四段式"],
            "prompt": (
                "你是工业品描述文案专家。" + _DESC_BASE + "\n"
                "结构：产品用途/适用场景 → 核心参数列表 → 结构与操作/维护 → 包装与服务；"
                "每段 2~4 句，短句优先。"
            ),
        },
        "spec_table": {
            "name": "参数表导向",
            "desc": "以规格表为主体，适合专业采购与比价。",
            "tags": ["参数", "规格表", "采购"],
            "prompt": (
                "你是参数型描述专家。" + _DESC_BASE + "\n"
                "结构：一段总述 + 一个 <ul><li> 参数列表（至少 6 项，全部来自原文） + "
                "一段使用说明；参数不足时宁可少写也不编造。"
            ),
        },
        "scenario": {
            "name": "场景应用型",
            "desc": "按施工/农场/仓储场景讲用途与收益。",
            "tags": ["场景", "应用", "收益"],
            "prompt": (
                "你是场景化描述专家。" + _DESC_BASE + "\n"
                "结构：3~4 个真实应用场景分段，每段说明该场景下的作业方式与收益，"
                "结尾给参数列表；场景必须符合商品实际用途。"
            ),
        },
        "b2b_trust": {
            "name": "B2B 信任型（质保 / 配件 / 售后）",
            "desc": "面向批发客户，强调可靠性与长期使用成本。",
            "tags": ["B2B", "信任", "质保", "售后"],
            "prompt": (
                "你是 B2B 信任型描述专家。" + _DESC_BASE + "\n"
                "结构：产品价值 → 结构与耐用设计 → 维护与配件 → 交付与服务；"
                "质保、配件、交期信息只能来自原文，不编造承诺。"
            ),
        },
        "mobile_short": {
            "name": "移动端短描述",
            "desc": "短段落 + 短列表，适合移动端快速阅读。",
            "tags": ["移动端", "短描述", "易读"],
            "prompt": (
                "你是移动端描述专家。" + _DESC_BASE + "\n"
                "结构：2~3 段短段落（每段 ≤2 句） + 5~8 项短参数列表；"
                "优先关键差异信息，避免长句与重复。"
            ),
        },
        "comparison": {
            "name": "中性方案对比",
            "desc": "与常见传统方案做中性对比，不点名/不贬低竞品，不编造数据。",
            "tags": ["对比", "中性", "合规"],
            "prompt": (
                "你是中性对比型描述专家。" + _DESC_BASE + "\n"
                "结构：先说明常见传统方案的使用痛点，再说明本商品在结构/效率/维护上的"
                "客观差异；只做中性表述，不点名或不贬低任何品牌，不编造对比数据。"
            ),
        },
        "aplus_sections": {
            "name": "A+ 风格分段",
            "desc": "按 A+ 页面逻辑分段，主题清晰、便于图文配合。",
            "tags": ["A+", "分段", "品牌"],
            "prompt": (
                "你是 A+ 页面文案专家。" + _DESC_BASE + "\n"
                "结构：用 <b> 小标题把内容分成 4 个主题段（核心价值、关键参数、使用场景、"
                "服务保障），每段后用 <ul><li> 列 2~3 个要点；不编造参数。"
            ),
        },
    },
    # ---------------- Search Terms（6） ----------------
    "search_terms": {
        "core_attr_scene": {
            "name": "核心词 + 属性 + 场景",
            "desc": "最常用的三层组合，覆盖大词与精准词。",
            "tags": ["默认", "核心词", "属性", "场景"],
            "prompt": (
                "你是 Amazon 后台关键词专家。" + _ST_BASE + "\n"
                "优先级：产品核心词 > 关键属性词（功率/尺寸/材质） > 使用场景词。"
            ),
        },
        "longtail_combo": {
            "name": "长尾组合",
            "desc": "核心词 + 修饰词组合，覆盖低竞争长尾搜索。",
            "tags": ["长尾", "组合", "低竞争"],
            "prompt": (
                "你是长尾关键词专家。" + _ST_BASE + "\n"
                "写法：以核心词为骨架，补充 2~3 个修饰词组成长尾短语；"
                "避免无意义堆砌，保持买家真实搜索语序。"
            ),
        },
        "synonyms_spelling": {
            "name": "同义词 / 拼写变体",
            "desc": "覆盖同义词、常见拼写差异与地区用词。",
            "tags": ["同义词", "拼写", "地区用词"],
            "prompt": (
                "你是关键词变体专家。" + _ST_BASE + "\n"
                "写法：给出核心词的同义词、地区用词与常见拼写变体；"
                "只使用通用词汇，不添加他人品牌名。"
            ),
        },
        "purchase_intent": {
            "name": "采购意图词",
            "desc": "面向商用/工业买家：heavy duty、industrial、commercial。",
            "tags": ["采购意图", "商用", "工业"],
            "prompt": (
                "你是 B2B 采购意图关键词专家。" + _ST_BASE + "\n"
                "写法：加入商用/工业采购常用限定词（heavy duty、industrial、commercial "
                "等通用词），与产品词组合；不得使用他人品牌。"
            ),
        },
        "model_series": {
            "name": "型号系列词",
            "desc": "覆盖型号系列、规格后缀与配件兼容搜索。",
            "tags": ["型号", "系列", "配件"],
            "prompt": (
                "你是型号系列关键词专家。" + _ST_BASE + "\n"
                "写法：以商品真实型号为基础，补充同系列规格后缀与常见配件词；"
                "型号必须来自原文，不编造不存在的系列。"
            ),
        },
        "application_scene": {
            "name": "应用场景词",
            "desc": "覆盖施工、农场、仓储、园林等真实使用场景。",
            "tags": ["场景", "施工", "农场", "仓储"],
            "prompt": (
                "你是场景关键词专家。" + _ST_BASE + "\n"
                "写法：围绕商品真实用途补充场景词（construction、farm、warehouse、"
                "landscaping 等通用场景词），不写与商品无关的场景。"
            ),
        },
    },
    # ---------------- SKU（6） ----------------
    "sku": {
        "brand_model_spec": {
            "name": "品牌-型号-规格",
            "desc": "最常见结构：品牌缩写 + 型号 + 关键规格。",
            "tags": ["默认", "品牌", "型号", "规格"],
            "prompt": "你是 Amazon 卖家 SKU 命名专家。" + _SKU_BASE + "\n"
                      "结构：品牌缩写-型号-关键规格（如 GEN-HW20-28-4X4）。",
        },
        "model_variant": {
            "name": "型号-变体属性",
            "desc": "适合多变体：型号 + 颜色/尺寸/功率变体。",
            "tags": ["型号", "变体", "属性"],
            "prompt": "你是多变体 SKU 命名专家。" + _SKU_BASE + "\n"
                      "结构：型号-变体属性-序号（如 HW20-28-RED-01）；变体属性只来自原文。",
        },
        "site_category_seq": {
            "name": "站点-类目-序号",
            "desc": "适合批量刊登：站点代码 + 类目缩写 + 流水号。",
            "tags": ["站点", "类目", "序号", "批量"],
            "prompt": "你是批量刊登 SKU 命名专家。" + _SKU_BASE + "\n"
                      "结构：站点代码-类目缩写-三位序号（如 CA-BACKHOE-001）；"
                      "站点代码用 {code}。",
        },
        "code_size_power": {
            "name": "简码-尺寸-功率",
            "desc": "紧凑结构：品类简码 + 尺寸 + 功率。",
            "tags": ["简码", "尺寸", "功率"],
            "prompt": "你是工业品 SKU 命名专家。" + _SKU_BASE + "\n"
                      "结构：品类简码-尺寸-功率（如 BH-6200-75KW）；尺寸与功率只来自原文。",
        },
        "short_alnum": {
            "name": "纯字母数字短码",
            "desc": "短码风格，适合系统内部识别。",
            "tags": ["短码", "字母数字", "系统"],
            "prompt": "你是 SKU 编码专家。" + _SKU_BASE + "\n"
                      "结构：6~12 位字母数字短码（可含连字符），可读且唯一；"
                      "不使用无意义随机串。",
        },
        "model_color_qty": {
            "name": "型号-颜色-数量",
            "desc": "适合颜色/套装变体：型号 + 颜色 + 数量。",
            "tags": ["型号", "颜色", "数量", "套装"],
            "prompt": "你是套装/颜色变体 SKU 命名专家。" + _SKU_BASE + "\n"
                      "结构：型号-颜色代码-数量（如 HW20-28-BLK-2PCS）；"
                      "颜色与数量只来自原文。",
        },
    },
    # ---------------- 图片（12） ----------------
    "image": {
        "aliexpress_pro_hero": {
            "name": "专业电商主图（速卖通同款）",
            "desc": "速卖通同款主图提示词：白底/浅灰、自然光、去文字水印，欧洲面孔美女操作机器，两名工人辅助。",
            "tags": ["主图", "速卖通", "人物", "作业场景"],
            "prompt": """基于上传的图片融合生成专业电商主图。
生成要求：
1. 主体清晰突出，构图饱满平衡
2. 纯白或浅灰背景，突出商品
3. 柔和自然光，无突兀阴影
4. 不添加任何文字，去除logo、水印
5. 一个欧洲面孔的美女正在操作机器，旁边有两个工人辅助
6. 保持商品形状、结构、颜色、材质和比例不变；不新增水印；商品：{title}（{subject}）；图片文字使用{language}""",
        },
        "white_bg_main": {
            "name": "主图纯白底",
            "desc": "Amazon 主图标准：纯白背景、产品占主体、无文字水印。",
            "tags": ["主图", "纯白底", "标准"],
            "prompt": "把商品主体完整抠出并放到纯白背景（#FFFFFF），主体居中、占画面 80% 左右，"
                      "柔和棚拍光与自然接触阴影，1:1 正方形。" + _IMAGE_BASE,
        },
        "gray_studio_main": {
            "name": "主图浅灰棚拍",
            "desc": "浅灰无缝背景棚拍质感，适合金属/机械设备的副主图。",
            "tags": ["主图", "浅灰", "棚拍"],
            "prompt": "把商品主体放到浅灰无缝背景的摄影棚场景，均匀柔光、真实金属质感与细节，"
                      "主体居中、比例不变，1:1 正方形。" + _IMAGE_BASE,
        },
        "industrial_scene": {
            "name": "工业场景图",
            "desc": "施工/工厂/道路等真实作业场景，商品为视觉主体。",
            "tags": ["场景图", "施工", "工厂"],
            "prompt": "把商品放入真实工业作业场景（施工现场、矿山、道路或厂房），"
                      "商品占画面 55~70%，自然光与合理阴影，背景虚化突出主体。"
                      + _IMAGE_BASE,
        },
        "detail_closeup": {
            "name": "细节特写",
            "desc": "关键部件/材质/焊缝等特写，突出做工。",
            "tags": ["细节", "特写", "做工"],
            "prompt": "拍摄商品关键部位的特写（发动机、液压件、焊缝、材质纹理），"
                      "微距质感、光线均匀、细节锐利，不改变部件结构与颜色。" + _IMAGE_BASE,
        },
        "dimension_infographic": {
            "name": "尺寸 / 规格信息图（仅副图）",
            "desc": "在副图上标注尺寸与关键规格，文字用站点语言且来自原文。",
            "tags": ["信息图", "尺寸", "副图"],
            "prompt": "制作尺寸/规格信息图：在商品图上用引线标注长宽高与关键规格，"
                      "文字使用{language}且数值必须来自原文；仅用于副图，主图不得使用。"
                      + _IMAGE_BASE,
        },
        "accessories_package": {
            "name": "配件 / 包装清单图",
            "desc": "展示随箱配件与包装内容，减少售后疑问。",
            "tags": ["配件", "包装", "清单"],
            "prompt": "制作配件/包装清单图：把原文列出的随箱配件整齐平铺在白底上，"
                      "用{language}标注名称与数量；不得添加原文没有的配件。" + _IMAGE_BASE,
        },
        "watermark_cleanup": {
            "name": "去水印 / Logo / 二维码",
            "desc": "清理原图水印、Logo、二维码与促销角标。",
            "tags": ["去水印", "清理", "合规"],
            "prompt": "清理原图中的水印、Logo、二维码与促销角标，用周围背景自然修补；"
                      "商品本体形状、颜色、材质与比例完全不变；主图不新增文字与水印。"
                      + _IMAGE_BASE,
        },
        "text_translate": {
            "name": "图片文字翻译（站点语言）",
            "desc": "把图中的中文卖点翻译替换为站点语言，保持排版。",
            "tags": ["翻译", "本地化", "站点语言"],
            "prompt": "把图片中可见的中文卖点翻译为{language}并替换到原位置，"
                      "保持字体风格、颜色、大小与排版；只翻译原文已有的信息，不新增文字。"
                      + _IMAGE_BASE,
        },
        "upscale_restore": {
            "name": "老图修复清晰化",
            "desc": "低清/压缩图修复：去噪、锐化、还原细节。",
            "tags": ["修复", "清晰化", "老图"],
            "prompt": "修复低清图片：去噪、去压缩伪影、适度锐化并还原细节，"
                      "保持商品原始外观与颜色，不改变结构与比例，不新增内容。" + _IMAGE_BASE,
        },
        "lifestyle_aplus": {
            "name": "A+ 生活方式图",
            "desc": "A+ 页面的生活/作业场景氛围图，可含环境人物远景。",
            "tags": ["A+", "生活方式", "氛围"],
            "prompt": "生成 A+ 页面用的生活方式图：商品处于真实使用环境，"
                      "人物仅作远景陪衬、不遮挡商品；光线自然、构图舒展，商品为主体。"
                      + _IMAGE_BASE,
        },
        "hero_action": {
            "name": "作业动作主视觉",
            "desc": "展示设备作业动作（挖掘/装载/切割），用于详情页头图。",
            "tags": ["动作", "主视觉", "详情页"],
            "prompt": "生成设备作业动作主视觉：展示商品正在完成其典型作业动作，"
                      "动作自然、结构准确，商品占画面 60% 左右，背景符合真实工况。"
                      + _IMAGE_BASE,
        },
        "packaging_pallet": {
            "name": "包装箱 / 托盘展示（仅副图）",
            "desc": "展示出口包装箱/托盘摆放与固定，仅用于副图。",
            "tags": ["包装", "托盘", "副图"],
            "prompt": "制作包装箱/托盘展示图：展示商品在出口包装箱或托盘上的摆放、"
                      "固定与防护方式，可标注原文提供的包装尺寸、数量与重量，文字使用"
                      "{language}；仅用于副图，主图不得使用，且不新增原文没有的包装信息。"
                      + _IMAGE_BASE,
        },
        "detail_collage": {
            "name": "多张细节拼合（仅副图）",
            "desc": "把 2~4 张关键细节特写拼合成一张信息图，仅用于副图。",
            "tags": ["细节", "拼合", "副图"],
            "prompt": "把 2~4 张关键细节特写（材质、焊缝、接口、控制面板）拼合成一张"
                      "整齐的信息图，统一光线与背景，细节保持原始颜色与结构；"
                      "文字使用{language}且只标注原文已有信息；仅用于副图，不改变产品本体。"
                      + _IMAGE_BASE,
        },
        "multi_angle": {
            "name": "多角度展示",
            "desc": "同一商品正/侧/后多角度组合展示结构差异。",
            "tags": ["多角度", "结构", "展示"],
            "prompt": "生成商品多角度展示图：正面、侧面、后方三个视角并排，"
                      "保持三张图中商品的结构、颜色、材质与比例一致，背景统一干净。"
                      + _IMAGE_BASE,
        },
    },
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _norm_kind(kind) -> str:
    return str(kind or "").strip().lower()


def _cfg_get(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _cfg_set(cfg, key: str, value) -> None:
    if cfg is None:
        raise ValueError("cfg 不能为空")
    if isinstance(cfg, dict):
        cfg[key] = value
    elif hasattr(cfg, "set"):
        cfg.set(key, value)
    else:
        setattr(cfg, key, value)


def render(template, mapping: dict | None = None) -> str:
    """只替换已知占位符，未知占位符/花括号原样保留。"""
    out = str(template or "")
    data = mapping if isinstance(mapping, dict) else {}
    for key in PLACEHOLDERS:
        if key in data and data[key] is not None:
            out = out.replace("{" + key + "}", str(data[key]))
    return out


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------
def list_kinds() -> list[str]:
    return list(PRESET_KINDS)


def list_presets(kind) -> list[dict]:
    """返回某类全部预设（含 key），保持内置顺序；未知类返回 []。"""
    k = _norm_kind(kind)
    presets = PRESETS.get(k) or {}
    rows: list[dict] = []
    for key, item in presets.items():
        rows.append({
            "key": key,
            "kind": k,
            "name": str(item.get("name") or ""),
            "desc": str(item.get("desc") or ""),
            "tags": list(item.get("tags") or []),
            "prompt": str(item.get("prompt") or ""),
        })
    return rows


def preset_options(kind) -> list[tuple[str, str]]:
    """下拉选项：[(key, name)]；未知类返回 []。"""
    return [(row["key"], row["name"]) for row in list_presets(kind)]


def get_preset(kind, key) -> dict | None:
    """返回单个预设副本；未知类/未知 key 返回 None。"""
    k = _norm_kind(kind)
    presets = PRESETS.get(k) or {}
    item = presets.get(str(key or "").strip())
    if not item:
        return None
    return {
        "key": str(key).strip(),
        "kind": k,
        "name": str(item.get("name") or ""),
        "desc": str(item.get("desc") or ""),
        "tags": list(item.get("tags") or []),
        "prompt": str(item.get("prompt") or ""),
    }


def default_preset(kind) -> str:
    """该类第一个内置预设 key（默认项）；未知类返回 ""。"""
    presets = PRESETS.get(_norm_kind(kind)) or {}
    return next(iter(presets), "")


def resolve_selected(cfg, kind) -> str:
    """当前选择的预设 key：cfg 里合法则用之，否则回落到默认项。"""
    k = _norm_kind(kind)
    if k not in PRESETS:
        return ""
    selected_key = SELECTED_CFG_KEYS.get(k, "")
    selected = str(_cfg_get(cfg, selected_key, "") or "").strip() if selected_key else ""
    if selected and selected in PRESETS[k]:
        return selected
    return default_preset(k)


def apply_to_cfg(cfg, kind, key) -> str:
    """把预设 prompt 写入 cfg 对应键，并记录选择；返回写入的 prompt 模板。"""
    k = _norm_kind(kind)
    preset = get_preset(k, key)
    if preset is None:
        raise ValueError(f"未知预设：kind={kind!r} key={key!r}")
    prompt_key = PROMPT_CFG_KEYS[k]
    marker_key = SELECTED_CFG_KEYS[k]
    prompt = preset["prompt"]
    _cfg_set(cfg, prompt_key, prompt)
    _cfg_set(cfg, marker_key, preset["key"])
    _cfg_set(cfg, "amazon_presets_version", PRESET_VERSION)
    return prompt


def reset_to_default(cfg, kind) -> str:
    """恢复该类默认预设（写回默认 prompt 并记录默认 key）；返回默认 key。"""
    k = _norm_kind(kind)
    key = default_preset(k)
    if not key:
        return ""
    apply_to_cfg(cfg, k, key)
    return key


__all__ = [
    "PRESET_VERSION", "PRESET_KINDS", "PRESETS", "PLACEHOLDERS",
    "PROMPT_CFG_KEYS", "SELECTED_CFG_KEYS", "KIND_LABELS",
    "list_kinds", "list_presets", "preset_options", "get_preset",
    "default_preset", "apply_to_cfg", "resolve_selected", "reset_to_default",
    "render",
]
