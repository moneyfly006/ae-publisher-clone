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
    12: "alibaba 刊登 发布 保存 草稿 提交 审核 预演 类目 moq 包装 重量 fob 报价 贸易 商品",
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
        PageItem(1, "店铺与模板", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(6, "价格 / 阶梯 / 批发", "商品准备", "SELL_OUTLINED", "trade"),
        PageItem(7, "SKU · 库存 · 套装", "商品准备", "ACCOUNT_TREE_OUTLINED", "trade"),
        PageItem(8, "发布队列", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(12, "Alibaba 刊登", "刊登", "PUBLISH", "listing"),
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
        PageItem(0, "概览", "工作空间", "DASHBOARD_OUTLINED", "listing"),
        PageItem(1, "店铺与登录", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(8, "草稿与待发布", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(3, "AI 文案", "内容", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容", "AUTO_AWESOME", "ai_image"),
        PageItem(4, "内容审查", "内容", "FACT_CHECK_OUTLINED", "audit"),
        PageItem(10, "任务与日志", "运行", "RECEIPT_LONG_OUTLINED", "listing"),
        PageItem(9, "图片工作台", "工具", "GRID_VIEW", "ai_image"),
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


def get_platform(pid: str | None) -> Platform:
    return PLATFORMS.get(str(pid or "").strip() or DEFAULT_PLATFORM, ALIBABA)
