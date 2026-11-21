"""平台注册表：每个平台的导航、功能与授权点。

UI 只读这里，不写平台分支；新增平台 = 新增一个 Platform 定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PageItem:
    index: int          # 复用现有页面实现（navigation.Destination.index）
    label: str
    group: str
    icon: str
    feature: str        # 需要的功能包


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
        PageItem(1, "店铺与登录", "商品准备", "STORE_OUTLINED", "listing"),
        PageItem(2, "类目与属性", "商品准备", "CATEGORY_OUTLINED", "category"),
        PageItem(6, "阶梯价 / FOB 报价", "价格与贸易", "SELL_OUTLINED", "trade"),
        PageItem(7, "MOQ · 包装 · 重量", "价格与贸易", "ACCOUNT_TREE_OUTLINED", "trade"),
        PageItem(8, "草稿与待发布", "刊登", "CLOUD_UPLOAD_OUTLINED", "listing"),
        PageItem(3, "AI 文案", "内容", "EDIT_NOTE", "ai_text"),
        PageItem(5, "AI 图片", "内容", "AUTO_AWESOME", "ai_image"),
        PageItem(4, "内容审查", "内容", "FACT_CHECK_OUTLINED", "audit"),
        PageItem(10, "发布 / 审核状态", "刊登", "RECEIPT_LONG_OUTLINED", "listing"),
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
