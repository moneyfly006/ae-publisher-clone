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
    # 12/13 号：平台专属刊登工作台（仅对应平台导航露出；其余平台走通用页）。
    Destination(12, "刊登", "Alibaba 刊登", "PUBLISH",
                "Alibaba 商品选择、类目必填、价格贸易、保存与发布",
                "alibaba 阿里 国际站 刊登 发布 保存 草稿 提交 审核 类目 moq 包装 重量 fob 报价 贸易"),
    # 13 号：Amazon 刊登工作台（平台导航 pages 里只在 Amazon 平台露出）。
    Destination(13, "刊登", "Amazon 刊登", "PUBLISH",
                "Amazon 采集箱编辑、站点、五点描述、AI、保存",
                "amazon 亚马逊 采集箱 站点 五点 卖点 bullet 类目 价格 库存 保存 发布 ai 翻译"),
    # 14 号：TikTok Shop 完整刊登工作台（内部隐藏路由；导航已拆成 15–21 功能页）。
    Destination(14, "刊登", "TikTok 刊登", "PUBLISH",
                "TikTok 采集箱编辑、店铺/站点、类目、AI、保存与发布",
                "tiktok tiktokshop 抖音 采集箱 店铺 站点 类目 属性 价格 库存 保存 发布 "
                "ai 文案 标题 描述 五点 关键词 图片 视频 物流 诊断"),
    # 15–21：Amazon 平台左侧导航按功能拆分（13 号完整工作台保留为内部隐藏路由）。
    Destination(15, "商品准备", "连接与店铺", "STORE_OUTLINED",
                "填写/读取店小秘 Cookie，检测连接并选择 Amazon 店铺与站点",
                "amazon 亚马逊 连接 店铺 cookie 登录 站点 检测连接"),
    Destination(16, "商品准备", "采集箱商品", "INVENTORY_2_OUTLINED",
                "按状态浏览/搜索采集箱商品，点「编辑」载入到后续页面",
                "amazon 亚马逊 采集箱 商品 列表 搜索 草稿 待发布 在线 发布中 发布失败"),
    Destination(17, "商品准备", "站点与类目", "CATEGORY_OUTLINED",
                "选择站点并逐级下钻到末级类目，支持最近使用与 nodePath 校验",
                "amazon 亚马逊 站点 类目 节点 path 最近使用 产品类型 属性"),
    Destination(18, "商品准备", "商品信息", "SELL_OUTLINED",
                "填写价格/库存/物流/品牌/SKU 与发布必填属性",
                "amazon 亚马逊 价格 库存 物流 运费 品牌 制造商 sku 必填属性"),
    Destination(19, "内容处理", "文案与 AI", "EDIT_NOTE",
                "编辑标题/五点/描述/Search Terms，可用 AI 生成与提示词预设",
                "amazon 亚马逊 文案 标题 五点 描述 search terms 关键词 ai 预设"),
    Destination(20, "内容处理", "AI 图片", "AUTO_AWESOME",
                "查看当前图片（只读）并设置图片提示词预设",
                "amazon 亚马逊 ai 图片 主图 缩略图 提示词 预设 白底 去水印"),
    Destination(21, "发布与运行", "发布与结果", "CLOUD_UPLOAD_OUTLINED",
                "保存/发布、查询发布结果、自动修复重发与按轮结果",
                "amazon 亚马逊 发布 保存 结果 失败 自动发布 自动修复 asin op"),
    # 22–28：TikTok 平台左侧导航按功能拆分（14 号完整工作台保留为内部隐藏路由）。
    Destination(22, "商品准备", "连接与店铺", "STORE_OUTLINED",
                "填写/读取店小秘 Cookie，测试连接并选择 TikTok Shop 店铺与 L7 站点",
                "tiktok 连接 店铺 cookie 登录 站点 测试连接 限额"),
    Destination(23, "商品准备", "采集箱商品", "INVENTORY_2_OUTLINED",
                "按状态浏览/搜索 TikTok 采集箱商品，点「编辑」载入后续页面",
                "tiktok 采集箱 商品 列表 搜索 草稿 待发布 在线 编辑"),
    Destination(24, "商品准备", "类目与属性", "CATEGORY_OUTLINED",
                "选择站点并逐级下钻到末级类目，加载必填属性并用规则/AI 补全",
                "tiktok 类目 属性 站点 补全 最近使用 必填 资质"),
    Destination(25, "商品准备", "价格 / 库存 / 物流", "SELL_OUTLINED",
                "填写价格/库存/重量尺寸/SKU 与仓库，价格库存为空禁止保存",
                "tiktok 价格 库存 物流 仓库 重量 尺寸 sku 币种"),
    Destination(26, "内容处理", "文案与 AI", "EDIT_NOTE",
                "编辑标题/五点/描述/关键词，可用 AI 生成、翻译与提示词预设",
                "tiktok 文案 标题 五点 描述 关键词 search terms ai 翻译 预设"),
    Destination(27, "内容处理", "图片与视频", "AUTO_AWESOME",
                "管理主图/详情图/视频 URL，应用图片方案并查看平台规则校验",
                "tiktok 图片 视频 主图 详情图 应用图片 恢复 视频 id"),
    Destination(28, "发布与运行", "保存与发布", "CLOUD_UPLOAD_OUTLINED",
                "发布前检查、二次确认保存、查看只读保存包与运行接入诊断",
                "tiktok 保存 发布 诊断 保存包 回读 预检 二次确认"),
)

DESTINATIONS += (
    Destination(29, "商品准备", "采集箱商品", "INVENTORY_2_OUTLINED", "浏览、搜索或按 ID 选择 Alibaba 商品"),
    Destination(30, "商品准备", "类目与属性", "CATEGORY_OUTLINED", "选择 Alibaba 类目，填写必填属性并核对经营范围"),
    Destination(31, "商品准备", "公司与商品资料", "BUSINESS_OUTLINED", "维护公司信息、商品资料和行业预设"),
    Destination(32, "商品准备", "价格与贸易", "SELL_OUTLINED", "配置阶梯价、FOB、起订量、包装与物流"),
    Destination(33, "发布与运行", "保存与发布", "CLOUD_UPLOAD_OUTLINED", "单个或批量检查、保存、发布并查看结果"),
    Destination(34, "商品准备", "SKU 与库存", "ACCOUNT_TREE_OUTLINED", "维护 Amazon SKU、库存、品牌与制造商"),
    Destination(35, "商品准备", "SKU 与库存", "ACCOUNT_TREE_OUTLINED", "维护 TikTok SKU 与库存"),
)

# Shopee 使用独立的全局/站点编辑器，避免复用速卖通字段与端点。
from .shopee import SECTIONS as SHOPEE_SECTIONS
DESTINATIONS += tuple(Destination(36 + i,
    "商品准备" if i < 5 else "内容处理" if i < 7 else "发布与运行",
    title, "STORE_OUTLINED" if i == 0 else "INVENTORY_2_OUTLINED", description)
    for i, (title, description) in enumerate(SHOPEE_SECTIONS))
DESTINATIONS += (Destination(44, "自动处理", "处理规则", "TUNE", "设置 Alibaba 文案、图片和商品资料的批量规则"),)
DESTINATIONS += (Destination(45, "自动处理", "处理规则", "TUNE", "设置 Amazon 文案、图片和商品资料的批量规则"),)
DESTINATIONS += (Destination(46, "发布与运行", "任务队列", "QUEUE",
                             "保存多份配置快照，单独执行或全部执行，日志按任务记录",
                             "队列 配置 快照 批量 执行 日志"),)

BY_INDEX = {item.index: item for item in DESTINATIONS}


def destination(index: int) -> Destination:
    # 13/14 号加入后 DESTINATIONS 长度=15，不能再用纯 % len（14%15=14 尚可，
    # 但 15%15=0 会取到概览）；先查 BY_INDEX，越界再回退取模。
    i = int(index)
    if i in BY_INDEX:
        return BY_INDEX[i]
    return BY_INDEX[i % len(DESTINATIONS)]
