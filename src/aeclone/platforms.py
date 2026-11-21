"""平台注册表：每个平台的导航、功能与授权点。

UI 只读这里，不写平台分支；新增平台 = 新增一个 Platform 定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# 搜索别名：平台页面的 label/group 是页面文案，用户习惯搜的是任务词
# （库存、水印、阶梯价…）。不保留这些词，导航搜索会搜不到，这在改造成
# 平台化导航时丢失过（回归项）。
_KEYWORDS = {
    0: "首页 工作台 概览 状态 搜索",
    1: "cookie 登录 浏览器 店铺 模板",
    2: "类目 属性 重量 尺寸 责任人 包装 合规 欧盟",
    3: "ai 文案 标题 详情 提示词 自定义",
    4: "审查 违禁词 合规 拦截 审核",
    5: "ai 图片 白底 水印 翻译 精细 提示词 视频 图床",
    6: "价格 定价 成本 利润 运费 佣金 阶梯 区域 批发",
    7: "库存 变种 sku 前缀 套装 编码",
    8: "草稿 待发布 发布 队列 批量 重发 数量 去向",
    9: "图片 工作台 相册 本地 调试 拼图 视频",
    10: "发布 审核 状态 日志 诊断 导出 报错 定时",
    11: "设置 连接 图床 余额 更新 诊断 授权 多开 界面",
    12: "alibaba 刊登 发布 保存 草稿 提交 审核 预演 类目 moq 包装 重量 库存 fob 报价 贸易 商品",
    13: "amazon 亚马逊 采集箱 站点 五点 卖点 bullet 类目 价格 库存 保存 发布 ai 翻译 预演 草稿",
}


@dataclass(frozen=True)
class PageItem:
    index: int          # 复用现有页面实现（navigation.Destination.index）
    label: str
    group: str
    icon: str
    feature: str        # 需要的功能包
    keywords: str = ""  # 导航搜索别名（见 _KEYWORDS）

    def matches(self, query: str) -> bool:
        text = f"{self.group} {self.label} {self.keywords} {_KEYWORDS.get(self.index, '')}".casefold()
        return all(word in text for word in str(query or "").casefold().split())


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    short: str
    color: str
    pages: tuple[PageItem, ...]
    implemented: bool = True

    def groups(self) -> list[str]:
        return list(dict.fromkeys(p.group for p in self.pages))

    def page(self, index: int) -> PageItem | None:
        return next((p for p in self.pages if p.index == int(index)), None)


ALIBABA = Platform(
    id="alibaba", name="Alibaba 国际站", short="阿", color="#F5A623",
    pages=(
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(1, "Alibaba 连接", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(12, "Alibaba 刊登", "刊登", "PUBLISH", "listing"),
        PageItem(8, "发布队列", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(3, "AI 文案", "内容", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容", "AUTO_AWESOME", "ai_image"),
        PageItem(4, "内容审查", "内容", "FACT_CHECK_OUTLINED", "audit"),
        PageItem(10, "任务与日志", "刊登", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(9, "图片工作台", "工具", "GRID_VIEW", "ai_image"),
        PageItem(11, "设置", "工具", "SETTINGS_OUTLINED", "listing"),
    ),
)

ALIEXPRESS = Platform(
    id="aliexpress", name="速卖通 AliExpress", short="速", color="#E62E04",
    pages=(
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(1, "店铺与模板", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(6, "价格", "商品准备", "SELL_OUTLINED", "pricing"),
        PageItem(7, "变种与库存", "商品准备", "ACCOUNT_TREE_OUTLINED", "variants"),
        PageItem(3, "AI 文案", "内容处理", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容处理", "AUTO_AWESOME", "ai_image"),
        PageItem(4, "内容审查", "内容处理", "FACT_CHECK_OUTLINED", "audit"),
        PageItem(8, "发布队列", "发布与运行", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(10, "任务与日志", "发布与运行", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(9, "图片工作台", "工具与设置", "GRID_VIEW", "ai_image"),
        PageItem(11, "设置", "工具与设置", "SETTINGS_OUTLINED", "listing"),
    ),
)


AMAZON = Platform(
    id="amazon", name="Amazon 亚马逊", short="A", color="#FF9900",
    pages=(
        # 契约 §4.1：Amazon 平台只保留 4 个入口。其余页面实现保留给其它平台复用，
        # 但不在 Amazon 导航里露出（1/2/3/4/5/8/9 在 Amazon 平台不可达）。
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(13, "Amazon 刊登", "刊登", "PUBLISH", "listing"),
        PageItem(10, "任务与日志", "运行", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(11, "设置", "工具", "SETTINGS_OUTLINED", "listing"),
    ),
)

TIKTOK = Platform(
    id="tiktok", name="TikTok Shop", short="T", color="#111111",
    implemented=False,
    pages=(
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(1, "店铺与登录", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(8, "草稿与待发布", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(3, "AI 文案", "内容", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容", "AUTO_AWESOME", "ai_image"),
        PageItem(10, "任务与日志", "运行", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(11, "设置", "工具", "SETTINGS_OUTLINED", "listing"),
    ),
)

SHOPEE = Platform(
    id="shopee", name="Shopee", short="S", color="#EE4D2D", implemented=False,
    pages=(
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(1, "店铺与登录", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(8, "草稿与待发布", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(3, "AI 文案", "内容", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容", "AUTO_AWESOME", "ai_image"),
        PageItem(10, "任务与日志", "运行", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(11, "设置", "工具", "SETTINGS_OUTLINED", "listing"),
    ),
)

PLATFORMS: dict[str, Platform] = {p.id: p for p in (ALIBABA, ALIEXPRESS, AMAZON, TIKTOK, SHOPEE)}
DEFAULT_PLATFORM = "alibaba"

# 平台能力开关：只对「共享页面里的某块 UI」做裁剪，未声明的能力默认保留。
# 语义：True=保留/展示，False=该平台不展示（且不应影响其他平台）。
PLATFORM_CAPS: dict[str, dict[str, bool]] = {
    "alibaba": {
        # 店小秘 SMT（速卖通）专属：Alibaba 直连 ICBU 不用这些
        "smt_shop_template": False,      # 店铺选择 + 模板商品
        "smt_category_source": False,    # 模板类目 / 自动分类
        "smt_product_settings": False,   # 原箱 / 申报货值 / 化学品 / 规格默认值
        "smt_attr_templates": False,     # 属性模板与自定义属性清理
        "eu_qualification": False,       # 欧盟资质
        "legacy_price_page": False,      # SMT 价格页（Alibaba 报价在 12 号页）
        "smt_sku_page": False,           # SKU 编码 / 套装 / 库存页
        "marketing_images": False,       # 营销图 1:1 / 3:4
        "smt_queue": False,              # 图片工作台 SMT 产品入队/队列
        "scheduled_publish": False,      # SMT 定时发布
        "smt_detail_assembly": False,    # AI 文案-详情内容增强（SMT 详情组装）
        "smt_extra_ai": False,           # SKU 优化 / 读图估重（SMT 专用 AI 能力）
        "smt_prompt_library": False,     # 速卖通/通用提示词预设库（Alibaba 用行业预设）
        "smt_resume": False,             # 上次未完成任务的续跑卡
        "smt_template_ready": False,     # 概览准备状态里的「模板商品」
        "icbu_listing": True,            # Alibaba 直连刊登页
    },
    "aliexpress": {"icbu_listing": False},
    "amazon": {"icbu_listing": False},
    "tiktok": {"icbu_listing": False},
    "shopee": {"icbu_listing": False},
}


def cap(platform_id: str, name: str, default: bool = True) -> bool:
    """判断某平台是否保留某项共享 UI/能力。"""
    return bool(PLATFORM_CAPS.get(str(platform_id or "").strip(), {}).get(name, default))


def get_platform(pid: str | None) -> Platform:
    return PLATFORMS.get(str(pid or "").strip() or DEFAULT_PLATFORM, ALIBABA)
