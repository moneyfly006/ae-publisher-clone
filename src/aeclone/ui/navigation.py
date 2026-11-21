"""工作流导航。页面 ID 保持稳定，兼容旧配置和命令行启动参数。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Destination:
    index: int
    group: str
    label: str
    icon: str
    description: str
    keywords: str = ""

    def matches(self, query: str) -> bool:
        text = f"{self.group} {self.label} {self.description} {self.keywords}".casefold()
        return all(word in text for word in query.casefold().split())


DESTINATIONS = (
    Destination(0, "工作空间", "概览", "DASHBOARD_OUTLINED", "查看商品状态、检查准备情况，开始本次任务", "首页 工作台 搜索"),
    Destination(1, "商品准备", "店铺与模板", "STORE_OUTLINED", "连接店铺，选择商品模板和类目来源", "cookie 登录 浏览器"),
    Destination(2, "商品准备", "类目与属性", "CATEGORY_OUTLINED", "管理类目、包装规格、属性默认值与欧盟资质", "重量 尺寸 责任人"),
    Destination(6, "商品准备", "价格", "SELL_OUTLINED", "配置定价算法、区域调价与批发价，实时试算", "成本 利润 运费 佣金 阶梯"),
    Destination(7, "商品准备", "变种与库存", "ACCOUNT_TREE_OUTLINED", "统一 SKU 编码、库存、图片替换与套装组合", "前缀 套装 sku"),
    Destination(3, "内容处理", "AI 文案", "EDIT_NOTE", "配置标题、详情、SKU 优化与读图估重", "提示词 自定义 详情"),
    Destination(5, "内容处理", "AI 图片", "AUTO_AWESOME", "设置图片编辑、营销图、商品视频与输出规则", "白底 水印 翻译 精细 提示词 视频"),
    Destination(4, "内容处理", "内容审查", "FACT_CHECK_OUTLINED", "设置文本与图片审查规则，发布前自动检查", "违禁词 合规 拦截"),
    Destination(8, "发布与运行", "发布队列", "CLOUD_UPLOAD_OUTLINED", "设置本次处理数量与发布去向，查看商品队列和失败重试", "数量 去向 批量 重发"),
    Destination(10, "发布与运行", "任务与日志", "RECEIPT_LONG_OUTLINED", "跟踪任务进度、定时发布与运行日志", "诊断 导出 报错"),
    Destination(9, "工具与设置", "图片工作台", "GRID_VIEW", "手动取图、处理、拼图、生成视频和上传结果", "本地 调试 相册"),
    Destination(11, "工具与设置", "设置", "SETTINGS_OUTLINED", "管理 API、授权、图片服务、界面与多开实例", "连接 图床 余额 更新 诊断"),
    # 13 号：Amazon 刊登工作台（平台导航 pages 里只在 Amazon 平台露出）。
    Destination(13, "刊登", "Amazon 刊登", "PUBLISH",
                "Amazon 采集箱编辑、站点、五点描述、AI、保存",
                "amazon 亚马逊 采集箱 站点 五点 卖点 bullet 类目 价格 库存 保存 发布 ai 翻译 预演"),
)

BY_INDEX = {item.index: item for item in DESTINATIONS}


def destination(index: int) -> Destination:
    # 13 号加入后 DESTINATIONS 长度=13，不能再用纯 % len（13%13=0 会取到概览）。
    i = int(index)
    if i in BY_INDEX:
        return BY_INDEX[i]
    return BY_INDEX[i % len(DESTINATIONS)]
