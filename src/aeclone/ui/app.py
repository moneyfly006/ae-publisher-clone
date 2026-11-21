"""AuroraList · 现代化界面（schema 驱动，统一商品处理与发布设置）。"""
from __future__ import annotations

from .. import __version__ as VERSION

import datetime
import sys
import tempfile
import os
import threading
import time
from pathlib import Path

import flet as ft

from ..api import ApiClient, ApiError
from ..config import AppConfig, config_dir, get_config, save_config
from ..license import verify as verify_license
from ..services import BatchRunner, ImageService, model_of
from ..services.publisher import Publisher
from ..integrations.dianxiaomi import DianxiaomiClient
from ..services.video import generate_carousel_video, probe as video_probe
from ..core import updater
from ..core.instances import list_instances, create_instance, launch_instance
from ..services import history
from ..services.queue import TaskQueue
from ..services.scheduler import Scheduler
from .navigation import DESTINATIONS, destination
from ..licensing import load_entitlements
from ..platforms import PLATFORMS, get_platform

ACCENT, ACCENT_2 = "#58C9B7", "#58C9B7"
INFO_C, DANGER_C, WARN_C, SUCCESS_C = "#80BCF4", "#FF9298", "#EAC078", "#58C9B7"
BORDER = "#2C3C45"
BG, CARD, CARD_2, SIDEBAR = "#10191F", "#19262E", "#21323C", "#131F26"
TEXT, MUTED = "#EEF4F5", "#A2B4BF"
ON_ACCENT = "#102B2B"
DANGER_BG = "#BA343E"

THEMES = {
    "dark": {"BG": "#10191F", "CARD": "#19262E", "CARD_2": "#21323C", "SIDEBAR": "#131F26",
             "TEXT": "#EEF4F5", "MUTED": "#A2B4BF",
             "ACCENT": "#58C9B7", "ACCENT_2": "#58C9B7",
             "INFO": "#80BCF4", "DANGER": "#FF9298", "WARN": "#EAC078", "SUCCESS": "#58C9B7",
             "BORDER": "#2C3C45"},
    "light": {"BG": "#F3F6F7", "CARD": "#FFFFFF", "CARD_2": "#EDF3F4", "SIDEBAR": "#E9F0F1",
              "TEXT": "#172E38", "MUTED": "#526975",
              "ACCENT": "#0F766E", "ACCENT_2": "#0F766E",
              "INFO": "#2366A6", "DANGER": "#BA343E", "WARN": "#895B15", "SUCCESS": "#0F766E",
              "BORDER": "#CDDADD"},
}


def apply_theme(name: str) -> str:
    """切换全局配色（重绘界面后生效）。返回真正生效的主题名。"""
    global BG, CARD, CARD_2, SIDEBAR, TEXT, MUTED, ACCENT, ACCENT_2
    global INFO_C, DANGER_C, WARN_C, SUCCESS_C, BORDER
    global ON_ACCENT
    key = "light" if str(name or "").lower() == "light" else "dark"
    t = THEMES[key]
    BG, CARD, CARD_2, SIDEBAR = t["BG"], t["CARD"], t["CARD_2"], t["SIDEBAR"]
    TEXT, MUTED = t["TEXT"], t["MUTED"]
    ACCENT, ACCENT_2 = t["ACCENT"], t["ACCENT_2"]
    INFO_C, DANGER_C, WARN_C, SUCCESS_C = t["INFO"], t["DANGER"], t["WARN"], t["SUCCESS"]
    BORDER = t["BORDER"]
    ON_ACCENT = "#FFFFFF" if key == "light" else "#102B2B"
    return key


def log_level_color(level: str) -> str:
    """日志级别语义色：OK/INFO=成功青绿，WARN=琥珀，ERROR=红。"""
    lv = str(level or "").upper()
    if lv in ("OK", "INFO", "SUCCESS"):
        return SUCCESS_C
    if lv == "WARN":
        return WARN_C
    return DANGER_C

CURRENCIES = ["USD", "EUR", "GBP", "CNY", "RUB", "BRL"]
# 区域调价常用国家/地区（代码 → 名称）
REGIONS = [("US", "美国"), ("RU", "俄罗斯"), ("BR", "巴西"), ("ES", "西班牙"),
           ("FR", "法国"), ("DE", "德国"), ("GB", "英国"), ("IT", "意大利"),
           ("PL", "波兰"), ("TR", "土耳其"), ("SA", "沙特"), ("AE", "阿联酋"),
           ("IL", "以色列"), ("KR", "韩国"), ("JP", "日本"), ("MX", "墨西哥"),
           ("CA", "加拿大"), ("AU", "澳大利亚"), ("NL", "荷兰"), ("PT", "葡萄牙")]

# 文案提示词预设库（标题 / 详情 / SKU）
TEXT_PROMPT_PRESETS = {
    "title": {
        "机械/工业设备（默认 SEO）": "你是资深 AliExpress 机械类目 SEO 运营。优化标题：保留产品英文名、型号、核心规格（功率/容量/尺寸/材质）与主要用途；核心词+长尾词+属性词组合；删除重复与堆砌词；英文 ≤125 字符；只输出一行标题，不要中文、引号或解释。",
        "工程机械（挖掘/装载/起重）": "你是工程机械外贸运营。标题突出：设备类型（excavator/wheel loader/crane）、吨位或载重、发动机功率、适用工况；英文 ≤125 字符；避免绝对化；只输出一行。",
        "包装机械（灌装/封口/贴标）": "你是包装机械运营。标题突出：机型（filling/capping/labeling/wrapping）、产能（bottles/min）、适用包装规格、自动化程度；英文 ≤125 字符；只输出一行。",
        "食品加工机械": "你是食品机械运营。标题突出：设备用途（清洗/切片/烘焙/杀菌）、产能、材质（304 stainless steel）、适用食品类型；英文 ≤125 字符；不写医疗或功效声称；只输出一行。",
        "数控机床/金属加工": "你是机床类目运营。标题突出：机型（CNC lathe/milling machine）、加工尺寸、主轴功率/转速、控制系统、精度；英文 ≤125 字符；只输出一行。",
        "激光设备（切割/焊接/打标）": "你是激光设备运营。标题突出：激光功率（W）、类型（fiber/CO2）、加工幅面、适用材料；英文 ≤125 字符；禁止夸大；只输出一行。",
        "注塑/挤出/吹塑": "你是橡塑机械运营。标题突出：锁模力/螺杆直径、产量、适用原料、自动化配置；英文 ≤125 字符；只输出一行。",
        "真空吸盘/搬运设备": "你是真空搬运设备运营。标题突出：吸盘类型、载重（kg）、电池/电压、适用板材（glass/marble/metal）；英文 ≤125 字符；只输出一行。",
        "输送/提升/流水线": "你是输送设备运营。标题突出：输送类型（belt/roller/screw）、长度、载重、适用行业；英文 ≤125 字符；只输出一行。",
        "泵阀/压缩机/液压": "你是流体设备运营。标题突出：类型（pump/valve/compressor）、流量/压力/功率、材质、接口规格；英文 ≤125 字符；只输出一行。",
        "农业机械": "你是农机运营。标题突出：设备类型、配套动力、作业幅宽/容量、适用作物；英文 ≤125 字符；只输出一行。",
        "木工机械": "你是木工机械运营。标题突出：机型（edge banding/panel saw/CNC router）、加工尺寸、主轴功率、适用板材；英文 ≤125 字符；只输出一行。",
        "焊接设备": "你是焊接设备运营。标题突出：焊接方式（MIG/TIG/arc）、电流范围、适用材料与厚度；英文 ≤125 字符；只输出一行。",
        "检测/测量仪器": "你是检测仪器运营。标题突出：测量项目、量程、精度、适用行业；英文 ≤125 字符；禁止医疗声称；只输出一行。",
        "B2B 批发/OEM 风格": "你是 B2B 外贸运营。标题突出：产品名+规格+OEM/ODM 支持+批量供货，弱化零售词；英文 ≤125 字符；只输出一行。",
        "机械配件/耗材": "你是机械配件运营。标题突出：配件名称、适配机型/OEM 号、材质、件数；英文 ≤125 字符；只输出一行。",
        "高端欧美市场": "你是欧美高端市场运营。标题用词专业克制：品牌调性、精度/耐用性、认证（CE/ISO，原文有才写）；英文 ≤125 字符；只输出一行。",
        "性价比热销": "你是性价比路线运营。标题突出：核心功能+关键规格+适用人群/场景，避免夸大与最低价表述；英文 ≤125 字符；只输出一行。",
    },
    "detail": {
        "工业设备通用（默认）": "你是专业工业设备外贸营销专家。基于原始信息重写英文详情：1) 用途与适用场景 2) 核心参数（只用原文有依据的数据）3) 结构与操作 4) 适用行业与客户 5) 质量与服务。使用 h3/p/ul/table；图片用占位符 <img data-detail-img=\"N\"> 按逻辑插入；禁止联系方式、二维码、促销、绝对化与医疗表述；只输出 HTML。",
        "工程机械（重型）": "你是工程机械外贸文案专家。结构：设备定位 → 工况与产能 → 发动机/液压/底盘参数（仅原文数据）→ 操作与维护 → 安全注意事项 → 适用工程类型；英文 HTML，图片占位符同上；不夸大吨位与性能。",
        "包装机械（产线）": "你是包装机械文案专家。结构：适用包装形式 → 产能与规格范围 → 电气/气动配置 → 与上下游设备联动 → 操作与换型 → 售后与备件；英文 HTML，参数只写原文。",
        "食品加工机械": "你是食品机械文案专家。结构：适用食品 → 工艺流程图说明 → 产能/材质（食品级 304）→ 清洗与维护 → 认证与安全 → 包装清单；英文 HTML；禁止功效与医疗表述。",
        "数控机床/金属加工": "你是机床文案专家。结构：加工能力 → 行程/工作台/主轴参数 → 控制系统与精度 → 刀具与夹具 → 安装调试 → 维护保养；英文 HTML，只用原文参数。",
        "激光设备": "你是激光设备文案专家。结构：激光源与功率 → 加工幅面与速度 → 适用材料与厚度 → 冷却与安全防护 → 软件与操作 → 售后；英文 HTML；禁止夸大切割厚度。",
        "注塑/挤出/吹塑": "你是橡塑机械文案专家。结构：适用原料与产品 → 锁模/螺杆参数 → 产量与能耗 → 控制系统 → 安装与调试 → 维护；英文 HTML。",
        "真空吸盘/搬运": "你是真空搬运设备文案专家。结构：适用板材与载重 → 吸盘与泵配置 → 电池/续航（如为电动）→ 操作安全 → 维护与备件；英文 HTML。",
        "输送/流水线": "你是输送设备文案专家。结构：输送形式 → 尺寸/载重/速度 → 材质与电机 → 布局与联动 → 安装与维护；英文 HTML。",
        "泵阀/压缩机": "你是流体设备文案专家。结构：工作原理 → 流量/扬程/压力/功率 → 材质与密封 → 安装条件 → 维护与备件；英文 HTML。",
        "农业机械": "你是农机文案专家。结构：适用作物与地块 → 配套动力 → 作业幅宽/容量 → 操作与调整 → 维护保养；英文 HTML。",
        "木工机械": "你是木工机械文案专家。结构：适用板材与工艺 → 加工尺寸/主轴 → 精度与效率 → 操作与安全 → 维护；英文 HTML。",
        "焊接设备": "你是焊接设备文案专家。结构：焊接方式与材料 → 电流/电压范围 → 占空比与保护 → 操作步骤 → 安全注意事项；英文 HTML。",
        "检测/测量仪器": "你是仪器文案专家。结构：测量项目 → 量程/精度/分辨率 → 使用环境 → 校准与维护 → 包装清单；英文 HTML；禁止医疗声称。",
        "B2B 批发/OEM": "你是 B2B 文案专家。结构：供货能力与 MOQ → OEM/ODM 流程 → 交期与包装运输 → 质保与备件 → 合作方式；英文 HTML；不写零售促销。",
        "机械配件/耗材": "你是配件文案专家。结构：适配机型/OEM 号 → 材质与工艺 → 安装位置与步骤 → 更换周期 → 包装清单；英文 HTML。",
        "短详情（移动端友好）": "用 300~500 字符写英文详情：3~5 个短段落 + 一个规格列表，只保留最核心卖点；图片占位符 <img data-detail-img=\"N\">；只输出 HTML。",
        "参数表为主（工程采购）": "以参数表为核心的英文详情：先一段 2~3 句概述，再输出 8~15 行规格表（只用原文数据），最后一段售后与备件说明；图片占位符同上；只输出 HTML。",
    },
    "sku": {
        "简短规范（默认）": "把每个 SKU 名称优化为简短英文：≤20 字符，只保留颜色/尺寸/容量/数量等关键差异，去重、不含中文与符号，每行一个，顺序与输入一致。",
        "颜色+尺寸": "SKU 命名规则：[颜色]-[尺寸]，英文，≤20 字符；颜色用常见英文名，尺寸用数字+单位；每行一个。",
        "容量/功率": "SKU 命名规则：[容量或功率]，英文，≤20 字符，如 2L / 1500W / 12V；每行一个。",
        "电压/插头": "SKU 命名规则：[电压]+[插头类型]，英文，≤20 字符，如 220V-EU / 110V-US；每行一个。",
        "型号+规格": "SKU 命名规则：[型号]-[关键规格]，英文，≤20 字符；型号保留原文，规格用数字；每行一个。",
        "材质": "SKU 命名规则：[材质]-[规格]，英文，≤20 字符，如 SS304-10mm；每行一个。",
        "长度/直径": "SKU 命名规则：[长度]x[直径]，英文，≤20 字符，单位统一；每行一个。",
        "压力/流量": "SKU 命名规则：[压力]/[流量]，英文，≤20 字符，如 10bar-50L；每行一个。",
        "数量/套装": "SKU 命名规则：[数量]pcs 或 [数量]pack，英文，≤20 字符；每行一个。",
        "配件型号": "SKU 命名规则：[适配机型]-[配件名]，英文，≤20 字符；每行一个。",
    },
    "weight": {
        "机械估重（默认）": "根据商品主图估算跨境包裹重量与尺寸（cm）：只输出 JSON {\"weight\":数字kg,\"length\":数字,\"width\":数字,\"height\":数字}；以同类机械设备的常见规格推断，不编造极端值。",
        "小型配件": "按小型机械配件估算：重量 0.1~2kg，单边 10~40cm；只输出 JSON，字段同上。",
        "整机设备": "按整机设备估算：重量 50~500kg，单边 80~200cm；只输出 JSON，字段同上。",
        "包装后估重": "按已包装木箱估算：在原机重量上加 10~20%，尺寸含托盘与木箱；只输出 JSON。",
        "超重设备": "按超重设备估算：重量 500~3000kg，单边 150~400cm；只输出 JSON，并给出是否需要海运的提示字段。",
        "体积重参考": "估算体积重：只输出 JSON {\"weight\":实重kg,\"volume_weight\":体积重kg,\"length\":..,\"width\":..,\"height\":..}。",
    },
}

# AI图片编辑提示词预设
IMAGE_PROMPT_PRESETS = {
    "marketing": {
        "工地作业场景": """生成专业的产品营销场景图（工程机械类）：\n1. 商品置于真实施工现场：土方工地/矿山/道路工程（如出现人物，人物身高必须与设备真实比例一致，不得高于设备）\n2. 商品结构、颜色、标识完全不变，只换背景与光影\n3. 自然日光，画面干净有纵深感\n4. 商品占画面 55~70%，为视觉主体\n5. 不要文字、水印、Logo、促销标签\n6. 1:1 构图""",
        "白底棚拍": """生成专业白底棚拍营销图：\n1. 纯白背景（#FFFFFF），无渐变\n2. 商品居中，占画面 60~70%\n3. 柔和棚拍光 + 自然接触阴影\n4. 保持商品原有颜色与细节\n5. 不要文字、水印、Logo\n6. 1:1 构图""",
        "展会/交付场景": """生成商务展示场景图：\n1. 商品置于展会现场或客户交付场景\n2. 环境整洁、专业、有商务感\n3. 商品为视觉主体，比例 55~70%\n4. 光线柔和自然，无过曝\n5. 不要文字、水印、Logo\n6. 1:1 构图""",
        "3:4 竖版场景": """生成 3:4 竖版营销场景图：
1. 产品置于真实使用场景，竖构图
2. 上方留出干净空间，方便后续加标题（但本次不要加字）
3. 产品占画面 55~65%，细节清晰
4. 不加文字、水印、Logo""",
        "高端暗调": """生成高端暗调营销图：
1. 深灰/黑色背景，局部打光突出产品
2. 金属与机械质感明显，画面干净
3. 产品占 60%，不加文字水印
4. 3:4 或 1:1 构图""",
        "多场景合并": """把产品合成到 2~3 个使用场景的组合图中：
1. 场景之间自然过渡，无拼接痕迹
2. 产品在每个场景中比例一致、结构不变
3. 不加文字、水印、Logo
4. 1:1 构图""",
        "户外实拍": """生成户外实拍风格营销图：\n1. 商品置于开阔户外环境（山地/工地/道路）\n2. 天空、地面层次自然，有空间感\n3. 商品结构比例不变，仅换环境\n4. 不要人物正脸，不要文字水印\n5. 1:1 构图""",
    },
    "detail": {
        "详情图拼接（参数表）": "把这张详情图整理成专业参数说明图：保留原始参数文字，排版为标题+表格+产品小图；白底，字号清晰，英文；不要添加原文没有的数据。",
        "详情图场景化": "把这张详情图改成场景化说明图：产品置于真实使用场景，保留原有文字信息，构图整洁，英文标注，不留联系方式。",
        "详情图卖点拆解": "把这张详情图改成卖点拆解图：3~5 个图标+短句（英文），围绕用途/材质/尺寸/服务；不要编造参数。",
    },
    "sku": {
        "SKU 白底图": "把这张 SKU 图处理成纯白背景：产品居中、比例 70%，去除杂物与水印，保留颜色细节，1:1。",
        "SKU 场景小图": "把 SKU 图处理成简洁场景小图：浅色背景+产品主体，保留颜色与结构，不加文字，1:1。",
    },
    "main": {
        "专业电商主图": """基于上传的图片融合生成专业电商主图。
生成要求：
1. 主体清晰突出，构图饱满平衡
2. 纯白或浅灰背景，突出商品
3. 柔和自然光，无突兀阴影
4. 不添加任何文字，去除logo、水印
5. 如出现人物：人物身高必须与设备真实比例一致（不得高于设备主体，操作姿势合理），严禁人比机器还高
6. 一个欧洲面孔的美女正在操作机器，""",
        "高端质感主图": """生成高端质感电商主图：
1. 深色或渐变背景，突出金属/机械质感
2. 侧逆光勾勒轮廓，保留产品细节
3. 产品占画面 65%，构图稳重大气
4. 不加文字、水印、Logo
5. 1:1 构图""",
        "轻奢浅色主图": """生成轻奢浅色电商主图：
1. 米白/浅灰背景，柔和自然光
2. 产品居中，配少量同色系道具
3. 保留产品原色与结构，不改变比例
4. 不加文字与水印，1:1 构图""",
        "多角度拼合主图": """把同一产品的多个角度拼合为一张主图：
1. 纯白背景，2~4 个角度均匀排布
2. 每个角度清晰、不变形
3. 不加文字、水印，1:1 构图""",
        "白底棚拍": """将产品图片转换为专业白底棚拍效果：
1. 纯白背景（#FFFFFF），无渐变无阴影
2. 产品居中，占画面60-70%
3. 保持产品原有颜色和细节
4. 清除水印、logo、背景杂物
5. 专业摄影棚光照效果""",
        "使用场景图": """生成产品的真实使用场景图：
1. 将产品置于符合其用途的真实环境中
2. 环境自然真实，光线柔和
3. 突出产品在使用中的状态
4. 可添加人物互动（欧美面孔）
5. 场景干净整洁，无杂乱元素""",
        "多角度展示": """基于参考图生成产品多角度展示图：
1. 正面、侧面、背面、细节等多个角度
2. 统一的白底或场景背景
3. 每个角度突出不同特点
4. 保持产品一致性
5. 专业拍摄质感""",
    },
    "detail": {
        "细节特写": """生成产品细节特写图：
1. 聚焦产品关键部位（接口、按钮、材质、工艺等）
2. 清晰展示细节特征
3. 适当景深，突出焦点
4. 柔和补光，无过曝
5. 可添加标注线和说明文字""",
        "尺寸规格图": """生成产品尺寸规格展示图：
1. 清晰的尺寸标注（长×宽×高）
2. 标注线规范，字体清晰
3. 可添加比例参照物
4. 白底或浅灰底，便于阅读
5. 多角度尺寸标注""",
        "功能展示": """生成产品功能特性展示图：
1. 展示产品的核心功能和特点
2. 分步骤或分区域说明
3. 配合图标和文字说明
4. 对比效果展示（使用前后）
5. 清晰直观，易于理解""",
        "包装配件": """生成产品包装和配件展示图：
1. 完整展示产品包装外观
2. 列出所有配件和附件
3. 整齐排列，布局美观
4. 白底或场景拍摄
5. 可添加清单文字""",
    },
    "sku": {
        "SKU白底图": """生成SKU规格白底图：
1. 纯白背景（#FFFFFF）
2. 产品居中，占比适中
3. 清晰展示该SKU的特征（颜色/尺寸/款式）
4. 无水印、无logo、无文字
5. 保持系列一致性""",
        "颜色对比": """生成不同颜色SKU的对比图：
1. 多个颜色并排展示
2. 统一角度和光照
3. 准确还原颜色差异
4. 白底或浅灰底
5. 便于买家选择""",
        "规格差异": """生成不同规格SKU的对比图：
1. 展示尺寸、容量、型号等差异
2. 添加清晰的规格标注
3. 并排或层叠对比
4. 参照物体现大小差异
5. 帮助买家理解规格""",
    }
}


def _tpl_lib_load(raw) -> dict:
    """模板库统一为 {name: record}；兼容旧版 list。"""
    import json as _j
    try:
        data = _j.loads(raw or "{}")
    except Exception:
        data = {}
    if isinstance(data, list):
        out = {}
        for i, x in enumerate(data):
            if isinstance(x, dict):
                out[str(x.get("name") or f"模板{i+1}")] = x
        return out
    return data if isinstance(data, dict) else {}


def _tpl_lib_dump(lib: dict) -> str:
    import json as _j
    return _j.dumps(lib or {}, ensure_ascii=False)


# ---------- 设计令牌（字体 / 间距 / 圆角）----------
FS_XS, FS_SM, FS_BASE, FS_MD, FS_LG, FS_XL = 12, 13, 14.5, 16, 19, 26
SP_XS, SP_SM, SP_MD, SP_LG = 6, 10, 16, 24
RADIUS_CARD, RADIUS_CTRL = 12, 8
ROW_MIN_H = 44          # 列表行最小高度（可点击区域）


def card(content, padding=SP_MD + 4, expand=False):
    return ft.Container(content=content, bgcolor=CARD, border_radius=RADIUS_CARD, padding=padding,
                        expand=expand, border=ft.border.all(1, BORDER))


def h1i(icon, text, size=FS_MD, color=None):
    """带小图标的卡片标题。"""
    return ft.Row([ft.Icon(icon, size=size + 2, color=color or ACCENT),
                   ft.Text(text, size=size, weight=ft.FontWeight.BOLD, color=TEXT)],
                  spacing=SP_SM)


def empty_state(text, icon=ft.Icons.INBOX_OUTLINED):
    """统一空状态样式。"""
    return ft.Row([ft.Icon(icon, size=FS_LG, color=MUTED), muted(text, FS_SM)],
                  spacing=SP_SM)


def h1(t, s=FS_XL):
    return ft.Text(t, size=s, weight=ft.FontWeight.BOLD, color=TEXT)


def muted(t, s=FS_SM):
    return ft.Text(t, size=s, color=MUTED)


def page_header(title: str, subtitle: str = "") -> ft.Column:
    """所有页面统一页头：主标题 + 一行说明，间距一致。"""
    row = [h1(title)]
    if subtitle:
        row.append(muted(subtitle, FS_BASE))
    return ft.Column(row, spacing=6)


def page_header(title: str, subtitle: str = "", actions=None):
    """页面顶栏：大标题 + 一句说明，统一各页层级。"""
    cols = [h1(title)]
    if subtitle:
        cols.append(muted(subtitle, FS_SM))
    heading = ft.Column(cols, spacing=6, expand=bool(actions))
    content = (ft.Row([heading, ft.Row(actions, spacing=10)], spacing=20,
                      vertical_alignment=ft.CrossAxisAlignment.START) if actions else heading)
    return ft.Container(content=content,
                        padding=ft.padding.only(top=4, bottom=8))


def workspace_tabs(items):
    """同页按任务分栏；控件只创建一次，切换时保留输入与回调。"""
    try:
        _sel = int(str(os.environ.get("AURORA_TAB", "0") or "0"))
    except Exception:
        _sel = 0
    _sel = max(0, min(_sel, max(0, len(items) - 1)))
    return ft.Tabs(
        selected_index=_sel, animation_duration=0, scrollable=True,
        indicator_color=ACCENT, label_color=ACCENT, unselected_label_color=MUTED,
        tabs=[ft.Tab(text=title, content=ft.Container(
            content=ft.Column(controls, spacing=SP_MD,
                              horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            padding=ft.padding.only(top=SP_MD))) for title, controls in items])


def capability_card(title: str, hint: str, enable_field: "Field", advanced_fields: list,
                    cfg, on_bind, preset_control=None):
    """AI 能力卡：主开关置顶，提示词/细节折叠进「高级」。功能键全部保留。"""
    refs = []
    enable_ctrl = enable_field.build(cfg)
    refs.append(enable_field)
    if on_bind:
        on_bind(enable_field)
    adv = []
    for f in advanced_fields:
        ctl = f.build(cfg)
        # 多行提示词不用浮动 label（折叠面板里会被边框遮住半行字），
        # 改成字段上方独立标题 + 输入框 hint，更清楚也不遮挡。
        if f.kind == "multiline":
            try:
                ctl.label = None
                ctl.hint_text = f.label
            except Exception:
                pass
            adv.append(ft.Column([ft.Text(f.label, size=FS_SM, color=MUTED),
                                  ctl], spacing=4))
        else:
            adv.append(ctl)
        refs.append(f)
        if on_bind:
            on_bind(f)
    if preset_control is not None:
        adv.insert(0, preset_control)
    body = ft.Column([
        ft.Row([
            ft.Column([
                ft.Text(title, size=FS_MD, weight=ft.FontWeight.BOLD, color=TEXT),
                muted(hint, FS_XS) if hint else ft.Container(height=0),
            ], spacing=2, expand=True),
            enable_ctrl,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=SP_SM),
    ], spacing=SP_SM)
    if adv:
        body.controls.append(ft.ExpansionTile(
            title=ft.Text("提示词与高级选项", size=FS_SM, color=MUTED),
            controls=adv, initially_expanded=False,
            icon_color=ACCENT, collapsed_icon_color=MUTED))
    return card(body), refs


def kpi_tile(label, value_ctrl, hint, color):
    """发布队列/概览用的指标块：大数字 + 说明，统一高度与对齐。"""
    try:
        value_ctrl.size = FS_XL
        value_ctrl.weight = ft.FontWeight.BOLD
        value_ctrl.color = color
    except Exception:
        pass
    return ft.Container(
        content=ft.Column([
            muted(label, FS_SM),
            ft.Container(content=value_ctrl, height=34,
                         alignment=ft.alignment.center_left),
            muted(hint, FS_XS),
        ], spacing=2),
        padding=ft.padding.symmetric(horizontal=16, vertical=12),
        bgcolor=CARD_2, border_radius=RADIUS_CTRL, width=240, height=104)


# 下拉选项的中文显示（配置里仍存英文 key，旧配置不受影响）
OPTION_LABELS = {
    "multiplier": "倍率：成本×倍率+固定加价",
    "fixed": "固定价格：所有商品统一售价",
    "free_shipping": "包邮：成本+运费，再乘利润",
    "commission": "含佣金：倒扣佣金+利润",
    "append": "追加到末尾",
    "first": "插入到开头",
}

# 价格模式说明（界面上鼠标悬停/阅读用）
PRICE_MODE_HINTS = {
    "multiplier": "售价 = 成本 × 倍率 + 固定加价",
    "free_shipping": "售价 = (成本 + 默认运费) × (1 + 期望利润%)",
    "commission": "售价 = (成本+加价) × 倍率 × (1+利润%) ÷ (1-佣金%)",
}


class Field:
    """把配置项渲染成控件。"""

    def __init__(self, key, label, kind="text", options=None, hint=""):
        self.key, self.label, self.kind, self.options, self.hint = key, label, kind, options or [], hint
        self.ctrl = None

    def build(self, cfg):
        val = cfg.get(self.key, "")
        if self.kind == "bool":
            self.ctrl = ft.Switch(label=self.label, value=bool(val), active_color=ACCENT)
        elif self.kind == "select":
            # 存英文 key，界面显示中文，避免「multiplier 是什么」的困惑
            self.ctrl = ft.Dropdown(label=self.label, value=str(val) if val not in ("", None) else None,
                                    options=[ft.dropdown.Option(o, OPTION_LABELS.get(o, o))
                                             for o in self.options], width=280,
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        elif self.kind == "multiline":
            self.ctrl = ft.TextField(label=self.label, value=str(val), multiline=True, min_lines=2, max_lines=6,
                                     bgcolor=CARD_2, border_color=CARD_2, color=TEXT, helper_text=self.hint or None)
        else:
            self.ctrl = ft.TextField(label=self.label, value=str(val), bgcolor=CARD_2, border_color=CARD_2,
                                     color=TEXT, helper_text=self.hint or None)
        return self.ctrl

    def read(self):
        v = self.ctrl.value
        if self.kind == "bool":
            cfg_val = bool(v)
        else:
            cfg_val = v
        return self.key, cfg_val


def section(title, fields, cfg, cols=2):
    rows, controls = [], []
    for f in fields:
        c = f.build(cfg)
        controls.append(f)
        span = 12 if f.kind in ("bool", "multiline") else {"xs": 12, "md": 12 // cols}
        rows.append(ft.Container(content=c, col=span))
    return card(ft.Column([
        h1(title, 15), ft.ResponsiveRow(rows, spacing=12, run_spacing=12),
    ], spacing=12)), controls


# ---------------- 页面 schema ----------------
def schema_store():
    return [
        ("店铺", [Field("store_name", "店铺名称"), Field("store_id", "店铺 ID")]),
        ("模板（API 直存必需）", [
            Field("product_template_name", "模板名称", hint="从店小秘选择或粘贴模板商品，属性/品牌/说明书等将从模板补齐"),
            Field("product_template_id", "模板商品 ID", hint="必填：API 直存会从该模板 edit.json 合并缺失字段"),
        ]),
    ]


def schema_ai():
    return [
        ("标题", [Field("enable_ai_title", "启用 AI 标题", "bool"),
                  Field("title_prompt", "标题提示词", "multiline"),
                  Field("title_prefix", "标题前缀"), Field("title_suffix", "标题后缀")]),
        ("详情", [Field("enable_ai_detail", "启用 AI 详情", "bool"),
                  Field("detail_prompt", "详情提示词", "multiline")]),
        ("SKU", [Field("enable_ai_sku", "启用 AI 优化 SKU", "bool"),
                 Field("sku_prompt", "SKU 提示词", "multiline")]),
        ("读图估重", [Field("enable_ai_weight", "启用 AI 估重", "bool"),
                      Field("weight_prompt", "估重提示词", "multiline")]),
    ]


def schema_review():
    return [
        ("文本审查", [Field("review_text_enable", "启用文本审查", "bool"),
                      Field("review_text_prompt", "文本审查提示词", "multiline"),
                      Field("check_banned_word", "违禁词检查", "bool")]),
        ("图片审查", [Field("review_main_img_enable", "主图审查", "bool"),
                      Field("review_main_img_prompt", "主图审查提示词", "multiline"),
                      Field("review_detail_img_enable", "详情图审查", "bool"),
                      Field("review_detail_img_prompt", "详情图审查提示词", "multiline"),
                      Field("detail_review_first_count", "详情图审查前 N 张"),
                      Field("detail_review_last_count", "详情图审查后 N 张"),
                      Field("review_block_on_fail", "不合规时拦截发布", "bool")]),
    ]


def schema_image():
    return [
        ("并发", [
            Field("image_concurrency", "单产品图片并发数（1-8，越大越快）"),
        ]),
        ("主图", [
            Field("main_image_generate_enable", "AI 生成主图（文生图）", "bool"),
            Field("main_image_generate_prompt", "生成提示词", "multiline"),
            Field("main_image_generate_count", "生成数量"),
            Field("main_image_generate_use_refs", "文生图用现有主图当素材（图生图）", "bool"),
            Field("white_bg_enable", "主图白底", "bool"),
            Field("white_background_pic_1", "白底起始序号"),
            Field("white_background_pic_2", "白底结束序号"),
            Field("watermark_enable", "主图去水印/去Logo", "bool"),
            Field("remove_watermark_pic_1", "去水印起始序号"),
            Field("remove_watermark_pic_2", "去水印结束序号"),
            Field("translate_main_pic_enable", "主图文字翻译", "bool"),
            Field("translate_main_pic_indices", "翻译序号"),
            Field("img_main_edit", "主图精细编辑（用通用编辑提示词）", "bool"),
            Field("designated_edit_enable", "主图指定编辑", "bool"),
            Field("designated_edit_indices", "编辑序号"),
            Field("designated_edit_prompt", "编辑提示词", "multiline"),
        ]),
        ("描述图", [
            Field("white_background_detail_pic_indices", "描述图序号（白底/去水印/编辑共用）"),
            Field("img_detail_white_bg", "描述图白底", "bool"),
            Field("img_detail_remove_wm", "描述图去水印", "bool"),
            Field("img_detail_edit", "描述图精细编辑", "bool"),
            Field("translate_detail_pic_enable", "描述图文字翻译", "bool"),
            Field("translate_detail_pic_indices", "翻译序号"),
        ]),
        ("SKU 图", [
            Field("white_background_sku_pic_indices", "SKU 图序号（白底/去水印/编辑共用）",
                  hint="支持 1-3,5 这种写法；留空=全部"),
            Field("white_background_sku_pic", "处理 SKU 图", "bool"),
            Field("img_sku_white_bg", "SKU 图白底", "bool"),
            Field("img_sku_remove_wm", "SKU 图去水印", "bool"),
            Field("img_sku_edit", "SKU 图精细编辑", "bool"),
            Field("img_sku_edit_prompt", "SKU 图编辑提示词", "multiline"),
            Field("translate_sku_pic", "SKU 图文字翻译", "bool"),
        ]),
        ("营销", [
            Field("marketing_auto_generate", "生成营销图（写进营销图片 1:1 / 3:4）", "bool"),
            Field("marketing_image_prompt", "营销图提示词", "multiline"),
        ]),
        ("通用", [
            Field("overlay_watermark_enable", "主图叠加水印（发布时）", "bool"),
            Field("four_in_one", "四合一拼图", "bool"),
            Field("four_in_one_position", "四合一位置", "select", ["append", "first"]),
            Field("pad_main_images_from_extra", "主图不足用描述图补", "bool"),
            Field("main_pic_count", "主图目标数量"),
        ]),
        ("主图视频", [
            Field("generate_main_video", "发布时生成主图视频", "bool",
                  hint="生成轮播视频并上传（需 ffmpeg）"),
            Field("use_template_video", "使用模板视频（需填路径）", "bool"),
            Field("template_video_path", "模板视频路径"),
            Field("overwrite_existing_video", "覆盖产品已有视频", "bool"),
            Field("video_slot_seconds", "每张停留秒数"),
            Field("video_transition_seconds", "转场秒数"),
            Field("video_size", "视频尺寸（如 1080x1080）"),
            Field("video_cover", "铺满裁剪（否则留白）", "bool"),
            Field("video_fade_edges", "首尾淡入淡出", "bool"),
            Field("video_output_dir", "视频输出目录（留空用默认）"),
        ]),
        ("精细编辑提示词", [
            Field("edit_enable", "启用通用编辑", "bool"),
            Field("edit_prompt", "通用编辑提示词", "multiline"),
            Field("translate_prompt", "翻译提示词", "multiline"),
            Field("background_gen_sku_prompt", "SKU 背景提示词", "multiline"),
        ]),
    ]
def schema_price():
    return [
        ("模式", [Field("price_mode", "价格模式", "select", ["multiplier", "free_shipping", "commission"]),
                  Field("price_multiplier", "价格倍率（如 1.35）"),
                  Field("price_fixed_add", "固定加价（元）"),
                  Field("regional_pricing", "启用区域倍率", "bool")]),
        ("成本", [Field("goods_cost", "商品成本（元）"),
                  Field("platform_commission", "平台佣金 %"),
                  Field("sales_profit", "期望利润 %"),
                  Field("default_express_price", "默认运费（包邮模式用）")]),
        ("阶梯", [Field("tuoguan_price_tiered_enabled", "启用阶梯倍率（按成本区间）", "bool")]),
        ("批发", [Field("wholesale_enable", "启用批发价", "bool")]),
    ]


def schema_variant():
    return []


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg: AppConfig = get_config()
        try:
            page.on_keyboard_event = self._on_key
        except Exception:
            pass
        try:
            _ws = str(getattr(self.cfg, "window_size", "") or "1280x800")
            _w, _h = _ws.lower().split("x")
            page.window.width = int(_w)
            page.window.height = int(_h)
        except Exception:
            pass
        self._ui_lock = threading.RLock()
        self._building_page = False
        self._update_err_ts = 0.0
        self._log_file_lock = threading.Lock()
        self._browser_lock = threading.RLock()
        self.picked: list[Path] = []
        self.wb_sel_paths: set[str] = set()
        self.wb_pick_summary = ft.Text("", size=12.5, color=ACCENT_2)
        self._subscribe_progress()
        self.wb_wall = None
        self.logs: list[str] = []
        self.products: list[dict] = []
        self.ws_endpoint = ""
        self.field_refs: list[Field] = []
        self.file_picker = ft.FilePicker(on_result=self._on_files)
        self.wm_picker = ft.FilePicker(on_result=self._on_watermark_file)
        self.api_state: dict = {}
        self.license_state: dict = {}
        self.cookie_src = None
        self._cookie_ts = 0.0

        page.title = f"AuroraList · 极光上架 v{VERSION}"
        self._preview_mode = bool(os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"))
        if self._preview_mode:
            page.title += " · 本地验收（预演）"
        # 窗口/任务栏图标：Windows 用 .ico（任务栏识别更好），其它平台用 PNG
        try:
            _icon = "icon.ico" if sys.platform == "win32" else "icon_1024.png"
            page.window.icon = _icon
        except Exception:
            try:
                page.window.icon = "icon_1024.png"
            except Exception:
                pass
        _theme = apply_theme(os.environ.get("AURORA_THEME") or
                             str(getattr(self.cfg, "ui_theme", "dark") or "dark"))
        page.bgcolor = BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.LIGHT if _theme == "light" else ft.ThemeMode.DARK
        self._configure_theme()
        page.window.min_width, page.window.min_height = 1024, 640
        # 小屏 / 截图验证时可固定窗口尺寸：AURORA_WINDOW=1514x945
        try:
            win = (os.environ.get("AURORA_WINDOW") or "").strip().lower()
            if "x" in win:
                w, h = win.split("x", 1)
                page.window.width, page.window.height = int(w), int(h)
        except Exception:
            pass
        # 多开时窗口错开，避免完全叠在一起
        try:
            pos = (os.environ.get("AURORA_WINDOW_POS") or "").strip()
            if "," in pos:
                x, y = pos.split(",", 1)
                page.window.left, page.window.top = int(x), int(y)
        except Exception:
            pass
        page.overlay.append(self.file_picker)
        page.overlay.append(self.wm_picker)
        page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)
        self.entitlements = load_entitlements(self.cfg)
        self.active_platform = get_platform(getattr(self.cfg, "active_platform", "") or "alibaba")
        self._build_shell()
        self._switch(self._start_page_index())
        self._log("运行实例：" + ("本地验收预演，不保存商品" if self._preview_mode else "日常运行")
                  + f" · 配置目录：{config_dir()} · 程序：{sys.executable}")
        # 无论从哪一页启动，都拉一次各状态数量（概览/发布队列都要用）
        try:
            self._refresh_store_counts()
        except Exception:
            pass
        # 启动强制版本检查：有新版必须更新后才能继续使用
        try:
            self._startup_update_gate()
        except Exception:
            pass
        # 设置里用到 ffmpeg 却没装：启动即提示下载
        try:
            self._startup_ffmpeg_gate()
        except Exception:
            pass
        # 启动时后台探一次余额：充值后不会再显示旧的低余额提示
        try:
            threading.Thread(target=self._probe_balance, kwargs={"silent": True},
                             daemon=True).start()
        except Exception:
            pass
        page.update()
        # 启动即自动检测一次 API / 图片空间（ISSUES #9）
        self._probe_api_async()

    # ---------- 启动页（ISSUES #10：计划任务下环境变量可能不生效）----------
    def _start_page_index(self) -> int:
        """--page N > 页面文件 > AURORA_PAGE 环境变量 > 配置 start_page。"""
        argv = list(getattr(sys, "argv", []) or [])
        for i, a in enumerate(argv):
            if a == "--page" and i + 1 < len(argv):
                try:
                    return int(argv[i + 1])
                except ValueError:
                    pass
            if a.startswith("--page="):
                try:
                    return int(a.split("=", 1)[1])
                except ValueError:
                    pass
        candidates = [os.environ.get("AURORA_PAGE_FILE", "").strip()]
        try:
            candidates.append(str(config_dir() / "page.txt"))
        except Exception:
            pass
        for p in candidates:
            if not p:
                continue
            try:
                txt = Path(p).read_text(encoding="utf-8").strip()
                if txt:
                    return int(txt)
            except Exception:
                continue
        try:
            env = os.environ.get("AURORA_PAGE")
            if env is not None and str(env).strip() != "":
                return int(env)
        except ValueError:
            pass
        try:
            return int(getattr(self.cfg, "start_page", 0) or 0)
        except Exception:
            return 0

    # ---------- 壳 ----------
    def _configure_theme(self):
        theme = ft.Theme(
            font_family="Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
            color_scheme=ft.ColorScheme(
                primary=ACCENT, on_primary=ON_ACCENT,
                secondary=ACCENT_2, on_secondary=ON_ACCENT,
                surface=CARD, on_surface=TEXT, on_surface_variant=MUTED,
                outline=BORDER, error=DANGER_C),
            scaffold_bgcolor=BG, card_color=CARD, divider_color=BORDER,
            text_theme=ft.TextTheme(body_medium=ft.TextStyle(size=FS_BASE, color=TEXT)),
            scrollbar_theme=ft.ScrollbarTheme(thickness=5, radius=4))
        self.page.theme = theme
        self.page.dark_theme = theme

    def _build_shell(self):
        self.nav_buttons = []
        self._nav_groups = []
        nav = ft.Column(spacing=2)
        self.nav_column = nav
        self._rebuild_nav()
        self.nav_empty = ft.Container(content=muted("未找到功能，请换个关键词", 12),
                                      padding=12, visible=False)
        nav.controls.append(self.nav_empty)
        self.nav_search = ft.TextField(
            hint_text="查找功能…", prefix_icon=ft.Icons.SEARCH,
            dense=True, text_size=12, height=40, bgcolor=CARD,
            border_color=BORDER, color=TEXT, border_radius=8,
            tooltip="输入功能名称或关键词，如 水印、库存、视频",
            on_change=lambda e: self._filter_navigation(e.control.value),
            on_submit=lambda e: self._open_navigation_match())
        self.btn_run_icon = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=19, color=ON_ACCENT)
        self.btn_run_text = ft.Text("启动任务", size=13, weight=ft.FontWeight.BOLD, color=ON_ACCENT)
        self.btn_run = ft.ElevatedButton(
            content=ft.Row([self.btn_run_icon, self.btn_run_text], spacing=8,
                           alignment=ft.MainAxisAlignment.CENTER),
            height=44, bgcolor=ACCENT, color=ON_ACCENT,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
            tooltip="启动 / 停止本次任务（⌘R / Ctrl+R）", on_click=self._toggle_run)
        self.license_text = ft.Text("检测中…", color=MUTED, size=11)
        self.search_kw = ft.TextField(
            label="商品标题关键词（留空处理全部）", width=320, dense=True,
            bgcolor=CARD_2, border_color=BORDER, color=TEXT,
            value=str(getattr(self, "run_keyword", "") or ""),
            hint_text="如 Concrete / Drilling",
            on_change=lambda e: self._apply_search(notify=False),
            on_submit=lambda e: self._apply_search())
        self.search_hint = ft.Text("", size=12.5, color=MUTED)
        sidebar = ft.Container(
            width=232, bgcolor=SIDEBAR,
            padding=ft.padding.symmetric(horizontal=14, vertical=20),
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            content=ft.Column([
                ft.Row([
                    ft.Container(width=36, height=36, border_radius=10,
                                 clip_behavior=ft.ClipBehavior.HARD_EDGE,
                                 content=ft.Image(src="icon_1024.png", width=36, height=36,
                                                  fit=ft.ImageFit.COVER)),
                    ft.Column([ft.Text("AuroraList", size=17, color=TEXT,
                                       weight=ft.FontWeight.BOLD),
                               muted("极光上架 · 运营工作空间", 10)], spacing=2),
                ], spacing=10),
                ft.Container(height=22), self.nav_search,
                ft.Column([nav], spacing=0, expand=True, scroll=ft.ScrollMode.AUTO),
                ft.Divider(color=BORDER, height=20),
                self.btn_run,
                ft.TextButton("日志监控", icon=ft.Icons.TERMINAL, height=36,
                              style=ft.ButtonStyle(color=MUTED),
                              on_click=lambda e: self._open_log_panel()),
                ft.Row([ft.Icon(ft.Icons.VERIFIED_USER, size=13, color=SUCCESS_C),
                        self.license_text, ft.Container(expand=True), muted(f"v{VERSION}", 10)],
                       spacing=5),
            ], spacing=6, expand=True))
        self.breadcrumb = ft.Text("工作空间 / 概览", size=12, color=MUTED)
        self.store_shortcut = ft.TextButton(
            "选择店铺", icon=ft.Icons.STORE_OUTLINED,
            style=ft.ButtonStyle(color=TEXT), tooltip="打开店铺与模板",
            on_click=lambda e: self._switch(1))
        self.shell_status = ft.Text("任务空闲", size=11, color=MUTED)
        self.theme_btn = ft.IconButton(
            icon=ft.Icons.LIGHT_MODE_OUTLINED if self.page.theme_mode == ft.ThemeMode.DARK
                 else ft.Icons.DARK_MODE_OUTLINED,
            icon_size=19, icon_color=MUTED, tooltip="切换白天 / 夜间模式",
            on_click=lambda e: self._toggle_theme())
        topbar = ft.Container(
            bgcolor=SIDEBAR, padding=ft.padding.symmetric(horizontal=28, vertical=8),
            border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
            content=ft.Row([
                self.breadcrumb, ft.Container(expand=True), self.shell_status,
                ft.VerticalDivider(width=16, color=BORDER), self.store_shortcut,
                self.theme_btn,
                ft.IconButton(ft.Icons.RECEIPT_LONG_OUTLINED, icon_size=19, icon_color=MUTED,
                              tooltip="查看任务与日志", on_click=lambda e: self._switch(10)),
            ], spacing=8))
        self.content = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=16,
                                 horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.content_slot = ft.Container(
            expand=True, content=self.content,
            padding=ft.padding.symmetric(horizontal=28, vertical=22))
        self.platform_rail = self._build_platform_rail()
        self.page.add(ft.Row([
            self.platform_rail,
            sidebar,
            ft.Column([topbar, self.content_slot],
                spacing=0, expand=True),
        ], expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        self._sync_run_button()
        threading.Thread(target=self._check_license, daemon=True).start()
        try:
            if getattr(self, "scheduler", None) is None:
                self.scheduler = Scheduler()
                self.scheduler.start(self._run_scheduled)
        except Exception as ex:
            self._log(f"调度器启动失败：{ex}", "WARN")
        def _detect_cookie():
            try:
                from ..integrations.browser_cookies import describe, resolve
                src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None))
                self.cookie_src = src
                self._cookie_ts = time.time()
                self._log(f"Cookie 源：{describe(src)}" + ("" if src.ok else f"（{src.error[:60]}）"),
                          "INFO" if src.ok else "WARN")
            except Exception as ex:
                self._log(f"Cookie 检测异常：{ex}", "WARN")
        threading.Thread(target=_detect_cookie, daemon=True).start()

        if getattr(self, "tpl_preview_box", None) is None:
            self.tpl_preview_box = ft.Container(content=self._template_preview_row())
        if getattr(self, "tpl_snapshot_text", None) is None:
            self.tpl_snapshot_text = ft.Text("模板参数：未读取", size=13.5, color=MUTED)
        try:
            self._maybe_wizard()
        except Exception as ex:
            self._log(f"引导弹窗异常：{ex}")

    # ---------- 平台 / 授权 ----------
    def _page_allowed(self, item) -> bool:
        return self.entitlements.has_feature(self.active_platform.id, item.feature)

    def _rebuild_nav(self):
        """按当前平台重建左侧导航；未授权功能显示锁，不隐藏（可引导开通）。"""
        nav = getattr(self, "nav_column", None)
        if nav is None:
            return
        nav.controls = []
        self.nav_buttons = []
        self._nav_groups = []
        pf = self.active_platform
        for group in pf.groups():
            heading = ft.Container(
                content=ft.Text(group, size=11, color=MUTED, weight=ft.FontWeight.W_600),
                padding=ft.padding.only(left=12, top=10, bottom=2))
            indices = []
            nav.controls.append(heading)
            for item in (p for p in pf.pages if p.group == group):
                allowed = self._page_allowed(item)
                locks = []
                if not allowed:
                    locks.append(ft.Icon(ft.Icons.LOCK_OUTLINE, size=12, color=MUTED))
                button = ft.TextButton(
                    content=ft.Row([
                        ft.Icon(getattr(ft.Icons, item.icon), size=19, color=MUTED),
                        ft.Text(item.label, size=13, color=MUTED, weight=ft.FontWeight.W_500,
                                expand=True),
                        *locks,
                    ], spacing=8),
                    height=38, width=204,
                    style=self._nav_style(False),
                    tooltip=f"{item.label}（{'已开通' if allowed else '未开通 · 点击查看'}）",
                    on_click=lambda e, idx=item.index: self._switch(idx))
                self.nav_buttons.append((item.index, button))
                indices.append(item.index)
                nav.controls.append(button)
            self._nav_groups.append((heading, indices))
        if getattr(self, "nav_empty", None) is not None:
            nav.controls.append(self.nav_empty)

    def _build_platform_rail(self):
        col = ft.Column(spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        for pid, pf in PLATFORMS.items():
            licensed = self.entitlements.has_platform(pid)
            active = pid == self.active_platform.id
            badge = ft.Container(
                width=44, height=44, border_radius=12,
                bgcolor=CARD_2 if not active else ACCENT,
                border=ft.border.all(1, BORDER if not active else ACCENT),
                alignment=ft.alignment.center,
                tooltip=f"{pf.name} · {'已开通' if licensed else '未开通'}",
                content=ft.Text(pf.short, size=15, color=ON_ACCENT if active else TEXT,
                                weight=ft.FontWeight.BOLD),
                on_click=lambda e, pid=pid: self._switch_platform(pid))
            lock = ft.Icon(ft.Icons.LOCK_OUTLINE, size=12,
                           color=MUTED if licensed else WARN_C, visible=not licensed)
            col.controls.append(ft.Column([badge, lock], spacing=2,
                                          horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Container(
            width=66, bgcolor=SIDEBAR,
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            padding=ft.padding.symmetric(vertical=18, horizontal=8),
            content=ft.Column([col], spacing=0))

    def _switch_platform(self, pid: str):
        pf = get_platform(pid)
        self.active_platform = pf
        try:
            self.cfg.set("active_platform", pf.id)
            save_config(self.cfg)
        except Exception:
            pass
        self.entitlements = load_entitlements(self.cfg)
        self._rebuild_nav()
        # 平台栏重绘（锁状态/高亮）
        try:
            self.platform_rail.content = self._build_platform_rail().content
            self._safe_update()
        except Exception:
            pass
        first = pf.pages[0].index if pf.pages else 0
        self._switch(first)
        self._log(f"切换平台：{pf.name} · {'已开通' if self.entitlements.has_platform(pf.id) else '未开通'}",
                  "INFO")

    def _render_locked(self, item):
        pf = self.active_platform
        feats = sorted(self.entitlements.platforms.get(pf.id, set()))
        site = str(getattr(self.cfg, "license_server", "") or "https://license.moneyfly.club")
        need = f"{item.group} / {item.label}"
        desc = ("当前授权未包含该平台。" if not self.entitlements.has_platform(pf.id)
                else f"当前授权未包含「{item.feature}」功能包。")
        cta = ft.ElevatedButton(
            "去授权网站开通", icon=ft.Icons.OPEN_IN_NEW, bgcolor=ACCENT, color=ON_ACCENT,
            on_click=lambda e: self.page.launch_url(site))
        self.content.controls = [card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=26, color=WARN_C),
                    ft.Text(f"{pf.name} · 功能未开通", size=FS_LG, weight=ft.FontWeight.BOLD,
                            color=TEXT)], spacing=10),
            muted(desc),
            ft.Divider(color=BORDER, height=18),
            muted("已开通的平台：" + (", ".join(self.entitlements.platforms_list()) or "无")),
            muted("当前平台已授权功能：" + (", ".join(feats) or "无")),
            muted(f"需要：{need} · 功能包 {item.feature}"),
            ft.Container(height=6), cta,
        ], spacing=10))]
        self._safe_update()

    @staticmethod
    def _nav_style(selected):
        return ft.ButtonStyle(
            bgcolor=CARD_2 if selected else ft.Colors.TRANSPARENT,
            color=TEXT if selected else MUTED,
            padding=ft.padding.symmetric(horizontal=12),
            alignment=ft.alignment.center_left,
            shape=ft.RoundedRectangleBorder(radius=8),
            overlay_color=ft.Colors.with_opacity(0.10, ACCENT))

    def _filter_navigation(self, query):
        q = str(query or "")
        matches = {it.index for it in self.active_platform.pages
                   if all(w in f"{it.group} {it.label}".casefold() for w in q.casefold().split())}
        for index, button in self.nav_buttons:
            button.visible = index in matches
        for heading, indices in self._nav_groups:
            heading.visible = any(index in matches for index in indices)
        self.nav_empty.visible = not matches
        self._safe_update()

    def _open_navigation_match(self):
        q = str(self.nav_search.value or "")
        matches = [it for it in self.active_platform.pages
                   if all(w in f"{it.group} {it.label}".casefold() for w in q.casefold().split())]
        if matches:
            self._switch(matches[0].index)
            self.nav_search.value = ""
            self._filter_navigation("")

    def _switch(self, idx):
        item = self.active_platform.page(int(idx))
        if item is None:
            item = self.active_platform.pages[0]
        idx = item.index
        for page_idx, b in self.nav_buttons:
            on = page_idx == idx
            b.style = self._nav_style(on)
            b.content.controls[0].color = ACCENT if on else MUTED
            b.content.controls[1].color = TEXT if on else MUTED
            b.content.controls[1].weight = ft.FontWeight.W_600 if on else ft.FontWeight.W_500
        self.breadcrumb.value = f"{self.active_platform.name} / {item.group} / {item.label}"
        if not self._page_allowed(item):
            self._page_idx = idx
            self._render_locked(item)
            return
        self.store_shortcut.text = self.cfg.store_name or "选择店铺"
        self.field_refs = []
        pages = [self._p_overview, self._p_store, self._p_advanced, self._p_ai, self._p_review,
                 self._p_image, self._p_price, self._p_variant, self._p_publish,
                 self._p_workbench, self._p_logs, self._p_settings]
        idx = idx % len(pages)
        self._page_idx = idx
        self._building_page = True
        try:
            controls = pages[idx]()
        finally:
            self._building_page = False
        self.content.controls = controls
        self._safe_update()
        try:
            self.content.scroll_to(offset=0, duration=0)
        except (AssertionError, RuntimeError):
            # 初次构建时控件可能尚未挂载到原生窗口。
            pass
        if idx == 0:
            self._probe_api_async()      # 概览页用缓存或重测，展示真实状态（ISSUES #9）

    def _render(self, title, schemas, extra=None):
        out = [h1(title)]
        self._autosave_status = ft.Text("修改即自动保存", size=12.5, color=MUTED)
        for name, fields in schemas:
            c, refs = section(name, fields, self.cfg)
            self.field_refs.extend(refs)
            for f in refs:
                self._bind_autosave(f)
            out.append(c)
        if extra:
            out.append(extra)
        out.append(ft.Row([self._autosave_status,
                           ft.OutlinedButton("恢复默认", icon=ft.Icons.RESTART_ALT,
                                             on_click=lambda e: self._reset())], spacing=12))
        return out

    def _settings_header(self, title, subtitle):
        # 操作区固定在页头右侧，避免原生 Flet 在 Tabs 后回收按钮时
        # 将旧的胶囊形边框绘制到标题位置。
        return page_header(title, subtitle, actions=[
            self._autosave_status,
            ft.TextButton("恢复默认", icon=ft.Icons.RESTART_ALT,
                          on_click=lambda e: self._reset()),
        ])

    def _bind_autosave(self, f):
        if f.ctrl is None:
            return
        def handler(e, _f=f):
            try:
                self._autosave(_f)
            except Exception:
                pass
        try:
            f.ctrl.on_change = handler
        except Exception:
            pass

    def _autosave(self, f):
        """任意设置改动后 0.6 秒自动落盘（防抖，避免每键一写）。"""
        try:
            k, v = f.read()
            self.cfg.set(k, v)
        except Exception:
            return
        timer = getattr(self, "_autosave_timer", None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        def _flush():
            try:
                save_config(self.cfg)
                if getattr(self, "_autosave_status", None) is not None:
                    self._autosave_status.value = f"已自动保存 {time.strftime('%H:%M:%S')}"
                    self._safe_update()
            except Exception:
                pass
        t = threading.Timer(0.6, _flush)
        t.daemon = True
        self._autosave_timer = t
        t.start()

    # ---------- 欧盟资质（ISSUES #4）----------
    EU_KEYS = ("eu_responsible_name", "eu_responsible_address",
               "eu_responsible_email", "eu_responsible_phone")

    def _eu_missing(self) -> list[str]:
        labels = {"eu_responsible_name": "名称", "eu_responsible_address": "地址",
                  "eu_responsible_email": "邮箱", "eu_responsible_phone": "电话"}
        return [labels[k] for k in self.EU_KEYS if not str(getattr(self.cfg, k, "") or "").strip()]

    def _bind_eu_guard(self):
        """选「是」时先要欧盟责任人资料，否则保存必被站点拦截。"""
        for f in self.field_refs:
            if f.key != "eu_qualification" or f.ctrl is None:
                continue
            prev = getattr(f.ctrl, "on_change", None)

            def handler(e, _prev=prev):
                if _prev:
                    try:
                        _prev(e)
                    except Exception:
                        pass
                try:
                    if str(e.control.value) == "是" and self._eu_missing():
                        self._ensure_eu_responsible()
                except Exception:
                    pass

            f.ctrl.on_change = handler

    def _ensure_eu_responsible(self):
        specs = (("eu_responsible_name", "欧盟责任人名称（公司/个人）", "例如：EU Responsible GmbH"),
                 ("eu_responsible_address", "欧盟责任人地址", "国家/城市/街道/邮编"),
                 ("eu_responsible_email", "电子邮箱", "name@example.com"),
                 ("eu_responsible_phone", "联系电话", "+49 ..."))
        fields = {}
        for key, label, hint in specs:
            fields[key] = ft.TextField(label=label, value=str(getattr(self.cfg, key, "") or ""),
                                       hint_text=hint, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        status = ft.Text("选「是」后站点要求填写欧盟责任人，否则保存会被拦截", size=12.5, color=WARN_C)

        def save(e):
            missing = [f.label for f in fields.values() if not str(f.value or "").strip()]
            if missing:
                status.value = "还缺：" + "、".join(missing)
                status.color = DANGER_C
                self._safe_update()
                return
            for k, f in fields.items():
                self.cfg.set(k, str(f.value or "").strip())
            self.cfg.set("eu_qualification", "是")
            save_config(self.cfg)
            self._close_dlg(dlg)
            self.toast("欧盟责任人资料已保存（欧盟资质=是）")
            self._switch(2)

        def to_no(e):
            self.cfg.set("eu_qualification", "否")
            save_config(self.cfg)
            self._close_dlg(dlg)
            self.toast("已改为「否」，不提交欧盟资质")
            self._switch(2)

        dlg = ft.AlertDialog(
            title=ft.Text("提交欧盟资质 → 需要欧盟责任人资料"),
            content=ft.Column([muted("填一次长期复用；也可以在「类目与属性 → 欧盟资质」里随时修改。"),
                               *fields.values(), status], spacing=10, width=520),
            actions=[ft.TextButton("改为「否」", on_click=to_no),
                     ft.ElevatedButton("保存并提交", bgcolor=ACCENT, color=ON_ACCENT, on_click=save)])
        self._show_dlg(dlg)

    def _reset(self):
        # 恢复默认前先备份，避免误点丢配置（API Key / 店铺 / 模板）
        def do_reset(e):
            try:
                import shutil as _sh, time as _t
                src = AppConfig.path()
                if src.exists():
                    _sh.copy2(src, src.with_name(f"config.backup-{_t.strftime('%Y%m%d-%H%M%S')}.json"))
            except Exception:
                pass
            self.cfg = AppConfig()
            save_config(self.cfg)
            self._switch(self._current)
            self.toast("已恢复默认")
            self._close_dlg(self.dlg)
        
        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认恢复默认设置"),
            content=ft.Text("所有配置将恢复为默认值（API密钥、店铺、模板等将丢失）。\n\n当前配置会自动备份为 config.backup-*.json"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog()),
                ft.FilledButton("确认恢复", on_click=do_reset),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dlg(self.dlg)

    # ---------- 页面 ----------
    def _maybe_wizard(self):
        if getattr(self.cfg, "onboarded", False):
            return
        if getattr(self, "_dlg", None) is not None:
            self._log("已有弹窗（更新/授权等），首次引导稍后再弹", "INFO")
            return
        # 已有店铺/API 配置的老用户不再弹首次引导
        if getattr(self.cfg, "store_id", "") or getattr(self.cfg, "api_key", ""):
            self.cfg.onboarded = True
            save_config(self.cfg)
            return
        self._wizard_step = 1
        self.wz_store = ft.Dropdown(label="店铺", width=320, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_source = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                     value=self.cfg.template_source or "draft",
                                     options=[ft.dropdown.Option("draft", "草稿"), ft.dropdown.Option("online", "在线")],
                                     on_change=lambda e: self._wz_source_changed())
        self.wz_tpl = ft.Dropdown(label="模板产品", width=380, bgcolor=CARD_2, border_color=CARD_2, color=TEXT, options=[])
        self.wz_status = ft.Text("① 点「读取已登录 Chrome」自动读取登录态", size=13.5, color=MUTED)
        self.wz_btn = ft.ElevatedButton("① 读取已登录 Chrome", icon=ft.Icons.SYNC, bgcolor=ACCENT, color=ON_ACCENT, on_click=self._wz_sync)

        self.wz_dlg = ft.AlertDialog(
            title=ft.Text("欢迎使用 AuroraList · 首次设置"),
            content=ft.Column([
                ft.Text("第 1 步：直接读取你 Chrome 里已登录的店小秘（需要先在 Chrome 登录过）", size=13.5, color=TEXT),
                ft.Row([self.wz_btn], spacing=10),
                ft.ElevatedButton("读取店铺信息（我已登录）", icon=ft.Icons.SYNC, on_click=self._wz_sync),
                ft.Divider(color=CARD_2),
                ft.Row([self.wz_store, self.wz_source], spacing=10),
                ft.Row([ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT,
                                          on_click=lambda e: self._open_template_picker(self.wz_source.value,
                                                                                        on_pick=self._wz_template_picked)),
                        self.tpl_preview_box], spacing=10),
                self.wz_status,
            ], spacing=12, width=520),
            actions=[ft.TextButton("稍后设置", on_click=lambda e: self._wz_close(False)),
                     ft.ElevatedButton("完成", bgcolor=ACCENT_2, color=ON_ACCENT, on_click=self._wz_finish)],
        )
        self._show_dlg(self.wz_dlg)

    def _wz_source_changed(self):
        self.cfg.template_source = self.wz_source.value
        save_config(self.cfg)
        self.toast(f"模板来源已切换为{'草稿' if self.wz_source.value == 'draft' else '在线'}")

    def _wz_sync(self, e):
        self.wz_status.value = "正在读取 Cookie 与店铺…"
        self._safe_update()

        def go():
            try:
                ck = self._cookies()
                if not ck:
                    self.wz_status.value = "未读取到 Cookie，请确认已在浏览器登录店小秘"
                    self._safe_update(); return
                c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                shops = c.shops()
                self.wz_store.options = [ft.dropdown.Option(str(s["id"]), s["name"]) for s in shops]
                if shops:
                    self.wz_store.value = str(shops[0]["id"])
                    self.cfg.store_id, self.cfg.store_name = str(shops[0]["id"]), shops[0]["name"]
                prods = c.drafts(limit=50) if self.wz_source.value == "draft" else c.online(limit=50)
                self.wz_tpl.options = [ft.dropdown.Option(str(p["id"]), (p.get("title") or p["id"])[:44]) for p in prods]
                if prods:
                    self.wz_tpl.value = str(prods[0]["id"])
                self.cfg.template_source = self.wz_source.value
                save_config(self.cfg)
                self.wz_status.value = f"已同步：{len(shops)} 个店铺 / {len(prods)} 个模板候选"
            except Exception as ex:
                self.wz_status.value = f"读取失败：{ex}"
            self._safe_update()
        threading.Thread(target=go, daemon=True).start()

    def _wz_template_picked(self, pid, title, images):
        # picker 已保存真实选择（含来源）；同步向导，不能让隐藏下拉框的首项覆盖它。
        self.wz_tpl.value = str(pid)
        self.wz_tpl.options = [ft.dropdown.Option(str(pid), title)]
        self.wz_source.value = self.cfg.template_source
        self._set_tpl_preview(images)

    def _wz_finish(self, e):
        if self.wz_store.value:
            self.cfg.store_id = str(self.wz_store.value)
            name = next((o.text for o in self.wz_store.options if o.key == self.wz_store.value), "")
            self.cfg.store_name = name or self.cfg.store_name
        if self.wz_tpl.value and not self.cfg.template_product_id:
            self.cfg.template_product_id = str(self.wz_tpl.value)
            title = next((o.text for o in self.wz_tpl.options if o.key == self.wz_tpl.value), "")
            self.cfg.template_product_title = title or ""
        self.cfg.template_source = self.wz_source.value
        self.cfg.onboarded = True
        save_config(self.cfg)
        self._close_dlg(self.wz_dlg)
        self._log(f"首次设置完成：店铺 {self.cfg.store_name} / 模板 {self.cfg.template_product_title[:30]}")
        self._safe_update()
        self._switch(0)

    def _wz_close(self, mark):
        self._close_dlg(self.wz_dlg)

    def _p_overview(self):
        self.stat_api = ft.Text("检测中…", color=MUTED, size=13.5)
        self.stat_auth = ft.Text("检测中…", color=MUTED, size=13.5)
        self.stat_auth_detail = muted("", 10)
        self._current = 0

        self.ov_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        self.ov_progress_text = ft.Text("空闲", size=13.5, color=MUTED)
        self.ov_recent = ft.Column(spacing=2)

        def svc(label, ctrl, icon, color, sub="", action=None):
            """概览底部服务卡：三张卡等宽等高、结构一致（标题/值/说明/操作）。"""
            sub_ctrl = sub if isinstance(sub, ft.Control) else muted(sub, 11)
            head = ft.Row([ft.Icon(icon, size=17, color=color),
                           ft.Text(label, size=13.5, color=TEXT, weight=ft.FontWeight.BOLD),
                           ft.Container(expand=True),
                           action or ft.Container()], spacing=6)
            return card(ft.Container(height=112, content=ft.Column([
                head,
                ft.Container(content=ctrl, height=26, alignment=ft.alignment.center_left),
                ft.Container(content=sub_ctrl, height=18),
            ], spacing=8)), padding=20, expand=True)

        st = {"runs": 0, "ok": 0, "failed": 0}
        try:
            st = history.stats(days=1)
        except Exception:
            pass
        try:
            _today_cost = f"¥{history.cost_stats(days=1)['total']:.2f}"
        except Exception:
            _today_cost = "¥0.00"

        def recheck_auth(e):
            self.stat_auth.value = "检测中…"
            self.stat_auth.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._check_license, daemon=True).start()

        auth_card = svc("授权状态", self.stat_auth, ft.Icons.VERIFIED_OUTLINED, ACCENT_2,
                        sub=self.stat_auth_detail,
                        action=ft.TextButton("重新检测", on_click=recheck_auth))
        self._apply_license_state()
        self._apply_api_state()
        self._apply_balance_state()

        # 准备状态：4 项关键就绪度，自动检测
        self.ready_cookie = ft.Text("检测中…", size=13.5, color=MUTED)
        self.ready_tpl = ft.Text("检测中…", size=13.5, color=MUTED)
        self.ready_api = ft.Text("检测中…", size=13.5, color=MUTED)
        self.ready_auth = ft.Text("检测中…", size=13.5, color=MUTED)
        self.ready_conflict = ft.Text("", size=13.5, color=MUTED)

        def _ready_row(icon, label, ctrl):
            target = 1 if label in ("Cookie", "模板商品") else 11
            ctrl.expand = True
            return ft.Container(expand=True, content=ft.TextButton(
                content=ft.Row([ft.Icon(icon, size=17.5, color=MUTED),
                               ft.Text(label, size=13, color=TEXT), ctrl,
                               ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, color=MUTED)], spacing=8),
                style=ft.ButtonStyle(padding=8),
                tooltip="前往店铺与模板" if target == 1 else "前往账号与连接",
                on_click=lambda e: self._switch(target)))

        ready_card = card(ft.Column([
            h1i(ft.Icons.CHECKLIST, "准备状态"),
            muted("都变绿就能一键启动；红色项点击可直接去对应页修复"),
            ft.Row([_ready_row(ft.Icons.COOKIE_OUTLINED, "Cookie", self.ready_cookie),
                    _ready_row(ft.Icons.COPY_OUTLINED, "模板商品", self.ready_tpl)], spacing=16),
            ft.Row([_ready_row(ft.Icons.CLOUD_OUTLINED, "AI API", self.ready_api),
                    _ready_row(ft.Icons.VERIFIED_OUTLINED, "授权", self.ready_auth)], spacing=16),
            ft.Row([ft.Container(expand=True, content=ft.Row(
                        [ft.Icon(ft.Icons.RULE, size=17.5, color=MUTED),
                         ft.Text("配置冲突", size=14.5, color=TEXT, no_wrap=True),
                         self.ready_conflict], spacing=8)),
                    ft.TextButton("检查配置冲突", icon=ft.Icons.RULE,
                                  on_click=lambda e: self._check_conflicts())], spacing=16),
        ], spacing=8))

        def _refresh_ready():
            # Cookie
            try:
                from ..integrations.browser_cookies import describe, resolve
                src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None))
                self.cookie_src = src
                self._cookie_ts = time.time()
                self.ready_cookie.value = "✓ " + describe(src) if (src.ok and src.cookies) else "✗ 未读到（点这里去粘贴）"
                self.ready_cookie.color = ACCENT_2 if (src.ok and src.cookies) else DANGER_C
            except Exception as _e:
                self.ready_cookie.value = f"✗ {str(_e)[:40]}"
                self.ready_cookie.color = DANGER_C
            # 模板
            _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
            self.ready_tpl.value = ("✓ " + str(getattr(self.cfg, "template_product_title", "") or "已选")[:24]) if _tid else "✗ 未选模板（API 保存必需）"
            self.ready_tpl.color = ACCENT_2 if _tid else DANGER_C
            # API / 授权：先触发自动检测，再等结果（最多 12 秒），不要一上来就显示 ✗
            self.ready_api.value = "检测中…"
            self.ready_api.color = MUTED
            self.ready_auth.value = "检测中…"
            self.ready_auth.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            try:
                self._probe_api_async(force=False)
            except Exception:
                pass
            if not (getattr(self, "license_state", None) or {}):
                try:
                    threading.Thread(target=self._check_license, daemon=True).start()
                except Exception:
                    pass
            _deadline = time.time() + 12
            while time.time() < _deadline:
                _st = getattr(self, "api_state", None) or {}
                _ls = getattr(self, "license_state", None) or {}
                if _st and _ls:
                    break
                time.sleep(0.5)
            _st = getattr(self, "api_state", None) or {}
            if _st.get("ok"):
                self.ready_api.value = "✓ " + str(_st.get("detail", "正常"))[:30]
                self.ready_api.color = ACCENT_2
            else:
                self.ready_api.value = ("✗ " + str(_st.get("detail", "API 未连通"))[:34]) if _st else "✗ API 未检测"
                self.ready_api.color = DANGER_C
            _ls = getattr(self, "license_state", None) or {}
            if _ls.get("ok"):
                self.ready_auth.value = "✓ " + str(_ls.get("message", "已授权"))[:24]
                self.ready_auth.color = ACCENT_2
            else:
                self.ready_auth.value = ("✗ " + str(_ls.get("message", "未授权"))[:24]) if _ls else "✗ 未授权"
                self.ready_auth.color = DANGER_C
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=_refresh_ready, daemon=True).start()

        # 状态数字：5 个等宽块（采集箱 / 待发布 / 发布失败 / 在线 / 审核中）
        _sc = getattr(self, "store_counts", {}) or {}
        self.ov_tiles = {}
        def _tile(key, label, hint, color, target=None):
            num = ft.Text(str(_sc.get(key, "—")), size=23, weight=ft.FontWeight.BOLD, color=color)
            self.ov_tiles[key] = num
            c = card(ft.Column([muted(label, 10.5),
                                ft.Container(content=num, height=30,
                                             alignment=ft.alignment.center_left),
                                muted(hint, 10)], spacing=2), padding=14, expand=True)
            if target is not None:
                c.on_click = lambda e, _t=target: self._switch(_t)
                c.ink = True
                c.tooltip = "点一下去处理"
            return c
        tiles_row = ft.Row([
            _tile("draft", "采集箱", "待处理", TEXT),
            _tile("editingRequired", "待发布", "可直接发布", INFO_C, target=8),
            _tile("publishFail", "发布失败", "可重试", DANGER_C, target=8),
            _tile("onSelling", "在线", "已上架", ACCENT_2),
            _tile("auditing", "审核中", "等待平台", WARN_C),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.ov_tiles_ts = ft.Text("尚未刷新", size=12, color=MUTED)
        tiles_head = ft.Row([muted("状态数字 · 缓存 30 秒", 10.5),
                             ft.Container(expand=True),
                             self.ov_tiles_ts], spacing=8)

        filter_card = card(ft.Column([
            h1i(ft.Icons.PLAY_CIRCLE_OUTLINE, "开始一次任务"),
            muted("选范围 → 填关键词（可选）→ 启动；默认只处理采集箱商品"),
            ft.Row([
                ft.ElevatedButton("启动", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT, color=ON_ACCENT,
                                  on_click=lambda e: self._toggle_run()),
                self.search_kw,
                ft.ElevatedButton("选择商品", icon=ft.Icons.CHECKLIST, bgcolor=CARD_2, color=TEXT,
                                  on_click=lambda e: self._scope_dialog()),
                ft.TextButton("清空", icon=ft.Icons.CLEAR, on_click=lambda e: self._clear_search()),
                ft.OutlinedButton("搜索全部商品", icon=ft.Icons.SEARCH,
                                  on_click=lambda e: self._global_search_dialog()),
                ft.TextButton("刷新数量", icon=ft.Icons.REFRESH,
                              on_click=lambda e: self._refresh_store_counts()),
            ], spacing=10, wrap=True),
            self.search_hint,
        ], spacing=8))

        _extra = [c for c in (self._cost_card(), self._resume_card(), self._diff_card())
                  if c is not None]
        workflow = ft.ResponsiveRow([
            ft.Container(col={"xs": 6, "lg": 3}, content=ft.OutlinedButton(
                text, icon=icon, height=44,
                on_click=lambda e, target=target: self._switch(target)))
            for text, icon, target in (
                ("01  商品准备", ft.Icons.STORE_OUTLINED, 1),
                ("02  内容处理", ft.Icons.AUTO_AWESOME, 3),
                ("03  发布商品", ft.Icons.CLOUD_UPLOAD_OUTLINED, 8),
                ("04  运行记录", ft.Icons.RECEIPT_LONG_OUTLINED, 10))
        ], spacing=12, run_spacing=8)

        return [page_header("概览", "从商品准备到发布，在一个工作空间完成日常运营。"), workflow,
                tiles_head, tiles_row, filter_card, ready_card,
                card(ft.Row([
                    ft.Column([muted("今日任务", 11),
                               ft.Text(str(st["runs"]), size=19, color=TEXT,
                                       weight=ft.FontWeight.BOLD)], spacing=2, expand=True),
                    ft.Column([muted("成功", 11),
                               ft.Text(str(st["ok"]), size=19, color=ACCENT_2,
                                       weight=ft.FontWeight.BOLD)], spacing=2, expand=True),
                    ft.Column([muted("失败", 11),
                               ft.Text(str(st["failed"]), size=19, color=DANGER_C,
                                       weight=ft.FontWeight.BOLD)], spacing=2, expand=True),
                    ft.Column([muted("今日花费", 11),
                               ft.Text(_today_cost, size=19, color=TEXT,
                                       weight=ft.FontWeight.BOLD)], spacing=2, expand=True),
                ], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=20),
                *_extra,
                ft.Row([svc("API 服务", self.stat_api, ft.Icons.CLOUD_OUTLINED, ACCENT,
                            sub="自建站点 · 按量计费",
                            action=ft.TextButton("去设置", on_click=lambda e: self._switch(11))),
                        auth_card,
                        svc("图片服务", ft.Text("约 15 秒/张", color=WARN_C, size=13.5,
                                                weight=ft.FontWeight.BOLD),
                            ft.Icons.BOLT_OUTLINED, WARN_C,
                            sub=(str(getattr(self.cfg, "image_host_url", "") or "image.moneyfly.club")
                                 .replace("https://", "").replace("http://", "")))],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ]

    # ---------- 概览的状态刷新（ISSUES #8 / #9）----------
    def _zoom_img(self, src, w: int = 70, h: int = 70, label: str = "", radius: int = 6,
                  gallery=None):
        """缩略图 + 点击放大。没有图时显示占位图标。"""
        s0 = str(src or "").strip()
        if not s0:
            return ft.Container(
                width=w, height=h, border_radius=radius, bgcolor=CARD_2,
                alignment=ft.alignment.center,
                content=ft.Text(label or "无图", size=11, color=MUTED))
        img = ft.Image(src=s0, width=w, height=h, fit=ft.ImageFit.COVER,
                       error_content=ft.Text(label or "加载失败", size=11, color=MUTED))

        def open_big(e):
            imgs = [str(x) for x in (gallery or []) if str(x or "").strip()]
            if not imgs:
                imgs = [s0]
            idx0 = imgs.index(s0) if s0 in imgs else 0
            big = ft.Image(src=imgs[idx0], fit=ft.ImageFit.CONTAIN, width=760, height=560)
            pos = ft.Text(f"{idx0 + 1}/{len(imgs)}", size=12, color=MUTED)

            def shift(delta):
                nonlocal idx0
                idx0 = (idx0 + delta) % len(imgs)
                big.src = imgs[idx0]
                pos.value = f"{idx0 + 1}/{len(imgs)}"
                try:
                    self._safe_update()
                except Exception:
                    pass

            actions = []
            if len(imgs) > 1:
                actions += [ft.TextButton("◀ 上一张", on_click=lambda ev: shift(-1)),
                            ft.TextButton("下一张 ▶", on_click=lambda ev: shift(1))]
            actions += [ft.TextButton("打开原图", on_click=lambda ev: self._open_url(imgs[idx0])),
                        ft.TextButton("关闭", on_click=lambda ev: self._close_dlg(dlg))]
            dlg = ft.AlertDialog(
                title=ft.Row([ft.Text(label or "图片预览"), pos], spacing=10),
                content=ft.Container(content=big, width=780, height=580,
                                     alignment=ft.alignment.center),
                actions=actions,
            )
            self._show_dlg(dlg)

        return ft.Container(width=w, height=h, border_radius=radius, bgcolor=CARD_2,
                            clip_behavior=ft.ClipBehavior.HARD_EDGE, content=img,
                            on_click=open_big, ink=True,
                            tooltip="点击放大")

    def _open_url(self, url: str):
        try:
            import webbrowser
            webbrowser.open(str(url))
        except Exception:
            self.toast("无法打开链接", False)

    def _diff_card(self):
        """改前后对比预览（标题 / 主图 / 详情长度）。"""
        try:
            from ..services import preview as pv
            rows = pv.latest(200)
        except Exception:
            rows = []
        if not rows:
            return card(ft.Column([h1("改前后对比", 15),
                                   empty_state("还没有处理记录；跑一次后这里会显示改前/改后差异")],
                                  spacing=6))

        def _item(r):
            b = r.get("before") or {}
            a2 = r.get("after") or {}
            bt, at = str(b.get("title") or "-"), str(a2.get("title") or "-")
            bi = (b.get("images") or [""])[0]
            ai = (a2.get("images") or [""])[0]
            bd, ad = len(str(b.get("detail") or "")), len(str(a2.get("detail") or ""))
            _gal = [x for x in (bi, ai) if str(x or "").strip()]
            thumbs = ft.Row([self._zoom_img(bi, 52, 52, "改前", gallery=_gal),
                             ft.Icon(ft.Icons.ARROW_FORWARD, size=14.5, color=MUTED),
                             self._zoom_img(ai, 52, 52, "改后", gallery=_gal)], spacing=5)
            _title_now = str(r.get("title") or "")[:46]
            _new_title = at if at and at != "-" and at != bt else ""
            _st = str(r.get("status") or "")
            _badge = {
                "published": ("已发布", "#0F7A45", "#E8F6EE"),
                "saved": ("已保存", "#4A3B8C", "#EDE9FF"),
                "failed": ("失败", "#B3261E", "#FDECEB"),
            }.get(_st)
            _badge_ctrl = (ft.Container(
                content=ft.Text(_badge[0], size=12, color=_badge[1],
                                weight=ft.FontWeight.BOLD),
                bgcolor=_badge[2], border_radius=999,
                padding=ft.padding.symmetric(horizontal=8, vertical=2))
                if _badge else ft.Text("", size=1))
            return ft.Row([
                thumbs,
                ft.Column([
                    ft.Text(_title_now or str(r.get("id") or ""), size=14,
                            color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{r.get('id')} · 详情 {bd}→{ad} 字符"
                            + (f" · 新标题「{_new_title[:24]}」" if _new_title else ""),
                            size=12, color=MUTED, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, expand=True),
                _badge_ctrl,
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        PER = 5
        page = {"i": 0}
        list_col = ft.Column(spacing=10)
        info = ft.Text("", size=12.5, color=MUTED)
        prev_btn = ft.OutlinedButton("上一页", icon=ft.Icons.CHEVRON_LEFT)
        next_btn = ft.OutlinedButton("下一页", icon=ft.Icons.CHEVRON_RIGHT)
        only_today = ft.Checkbox(label="只看今天", value=False)
        only_failed = ft.Checkbox(label="只看失败", value=False)
        kw = ft.TextField(hint_text="搜产品 ID / 标题", width=200, dense=True,
                          bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def _filtered():
            import time as _t
            cur = list(rows)
            if only_today.value:
                start = _t.mktime(_t.localtime()[:3] + (0, 0, 0, 0, 0, -1))
                cur = [r for r in cur if int(r.get("ts") or 0) >= start]
            if only_failed.value:
                cur = [r for r in cur if str(r.get("status") or "") == "failed"]
            k = (kw.value or "").strip().lower()
            if k:
                cur = [r for r in cur
                       if k in str(r.get("id") or "").lower()
                       or k in str(r.get("title") or "").lower()]
            return cur

        def _render(ev=None):
            cur = _filtered()
            total = max(1, (len(cur) + PER - 1) // PER)
            page["i"] = min(max(0, page["i"]), total - 1)
            lo = page["i"] * PER
            list_col.controls = ([_item(r) for r in cur[lo:lo + PER]]
                                 or [empty_state("没有符合条件的记录")])
            info.value = f"第 {page['i'] + 1}/{total} 页 · {len(cur)} 条（共 {len(rows)}）"
            prev_btn.disabled = page["i"] <= 0
            next_btn.disabled = page["i"] >= total - 1
            try:
                self._safe_update()
            except Exception:
                pass

        def _go(delta):
            page["i"] += delta
            _render()

        def _reset(e=None):
            page["i"] = 0
            _render()

        only_today.on_change = _reset
        only_failed.on_change = _reset
        kw.on_change = _reset

        prev_btn.on_click = lambda e: _go(-1)
        next_btn.on_click = lambda e: _go(1)

        def _reset(e=None):
            page["i"] = 0
            _render()

        only_today.on_change = _reset
        only_failed.on_change = _reset
        kw.on_change = _reset
        _render()
        return card(ft.Column([h1("改前后对比", 15),
                               muted("最近处理的商品：标题 / 详情 / 主图变化", 11),
                               ft.Row([only_today, only_failed, kw], spacing=12,
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER),
                               list_col,
                               ft.Row([prev_btn, next_btn, info], spacing=10,
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                              spacing=10))

    def _resume_card(self):
        """断点续传：显示上次批量剩余/失败，提供「继续」。"""
        try:
            from ..services import batch_state as bs
            info = bs.load()
        except Exception:
            info = {}
        rem = len(info.get("remaining") or [])
        fail = len(info.get("failed") or [])
        if not rem and not fail:
            return None   # 没有断点就不占位置
        txt = (f"上次批量：完成 {info.get('done', 0)}/{info.get('total', 0)}"
               f"，剩余 {rem} 个" + (f"，失败 {fail} 个" if fail else ""))
        return card(ft.Column([
            h1("断点续传", 15),
            ft.Text(txt, size=13.5, color=TEXT),
            ft.Row([ft.ElevatedButton("继续处理剩余", icon=ft.Icons.PLAY_ARROW, bgcolor=ACCENT,
                                      color=ON_ACCENT, on_click=lambda e: self._start()),
                    ft.OutlinedButton("清空断点", icon=ft.Icons.CLEAR,
                                      on_click=lambda e: self._confirm(
                                          "清空断点记录？",
                                          "清空后下次启动会重新扫描全部待处理商品。",
                                          lambda: (bs.clear(), self._switch(0))))],
                   spacing=10, wrap=True),
            muted("「继续」会重新扫描待处理商品，已成功的自动跳过，只处理剩余的", 11),
        ], spacing=8))

    def _cost_card(self):
        """成本/用量统计卡片（今日 + 近 7 天 + 按功能拆分）。"""
        try:
            d1 = history.cost_stats(days=1)
            d7 = history.cost_stats(days=7)
        except Exception:
            d1, d7 = {"calls": 0, "total": 0.0, "by_stage": {}}, {"calls": 0, "total": 0.0, "by_stage": {}}
        by_stage = d1.get("by_stage") or {}
        rows = []
        for k, v in list(by_stage.items())[:5]:
            rows.append(ft.Row([ft.Text(f"{k}", size=13.5, color=TEXT, width=110),
                                ft.Text(f"{v['n']} 次", size=13.5, color=MUTED, width=70),
                                ft.Text(f"¥{v['cost']:.3f}", size=13.5, color=ACCENT_2)], spacing=8))
        if not rows:
            rows = [empty_state("今天还没有 AI 调用记录")]

        # 近 7 天按天趋势（纯容器柱状图，不依赖图表库）
        try:
            import time as _t
            day_cost = {}
            for r in history.recent(5000):
                if r.get("kind") != "cost":
                    continue
                ts = float(r.get("ts") or 0)
                if ts < _t.time() - 7 * 86400:
                    continue
                key = _t.strftime("%m-%d", _t.localtime(ts))
                day_cost[key] = day_cost.get(key, 0.0) + float(r.get("cost") or 0)
            days = []
            for i in range(6, -1, -1):
                ts = _t.time() - i * 86400
                days.append((_t.strftime("%m-%d", _t.localtime(ts)),
                             _t.strftime("%a", _t.localtime(ts))[:1]))
            mx = max([day_cost.get(k, 0.0) for k, _ in days] + [0.001])
            bars = []
            for k, wd in days:
                v = day_cost.get(k, 0.0)
                h = max(4, int(44 * v / mx))
                bars.append(ft.Column([
                    ft.Container(width=18, height=h, bgcolor=ACCENT if v else CARD_2,
                                 border_radius=4,
                                 tooltip=f"{k} ¥{v:.3f}"),
                    ft.Text(wd, size=11, color=MUTED),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER))
            trend = ft.Row(bars, spacing=8, vertical_alignment=ft.CrossAxisAlignment.END)
        except Exception:
            trend = ft.Container()

        return card(ft.Column([
            h1i(ft.Icons.INSIGHTS, "成本 / 用量"),
            ft.Row([ft.Text(f"今日 ¥{d1['total']:.3f}", size=14.5, color=ACCENT_2),
                    ft.Text(f"（{d1['calls']} 次调用）", size=12.5, color=MUTED),
                    ft.Text(f"近 7 天 ¥{d7['total']:.3f}", size=14.5, color=TEXT),
                    ft.Text(f"（{d7['calls']} 次）", size=12.5, color=MUTED)], spacing=18),
            ft.Row([trend, ft.Column(rows, spacing=3, expand=True)], spacing=18,
                   vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=8))

    def _apply_api_state(self):
        st = getattr(self, "api_state", None) or {}
        ctrl = getattr(self, "stat_api", None)
        if ctrl is None:
            return
        if not st:
            return
        try:
            if st.get("ok"):
                ctrl.value = f"正常 · {st.get('detail', '')} · {st.get('lat', '-')}s"
                ctrl.color = ACCENT_2
            else:
                ctrl.value = f"连接失败：{st.get('detail', '')}"
                ctrl.color = DANGER_C
            self._safe_update()
        except Exception:
            pass

    def _probe_api_async(self, force: bool = False):
        st = getattr(self, "api_state", None) or {}
        if not force and st and (time.time() - float(st.get("ts") or 0)) < 300:
            self._apply_api_state()
            return
        if getattr(self, "_api_probing", False):
            return
        self._api_probing = True

        def run():
            t0 = time.time()
            try:
                models = ApiClient(self.cfg.api_base, self.cfg.api_key, min(float(self.cfg.timeout or 60), 30)).models()
                self.api_state = {"ok": True, "detail": f"{len(models)} 个模型",
                                  "lat": round(time.time() - t0, 1), "ts": time.time()}
            except Exception as ex:  # noqa: BLE001
                self.api_state = {"ok": False, "detail": str(ex)[:70],
                                  "lat": round(time.time() - t0, 1), "ts": time.time()}
            finally:
                self._api_probing = False
            self._apply_api_state()

        threading.Thread(target=run, daemon=True).start()

    # 需要 AI 的开关（任意一个开就要预检）
    _AI_GATES = ("enable_ai_title", "enable_ai_detail", "enable_ai_sku", "review_enable",
                 "estimate_weight_enable", "translate_main_pic_enable",
                 "translate_detail_pic_enable", "translate_sku_pic")

    def _preflight_api(self) -> str:
        """启动前检查 API Key。返回非空字符串 = 需要中止并展示的原因。

        只拦「Key 无效 / 额度不足」这类一定跑不通的情况；
        网络抖动只记 WARN，不阻断（可能只是瞬时）。
        """
        try:
            from ..services.preflight import ai_active as _ai_active
            if not _ai_active(self.cfg):
                return ""
        except Exception:
            pass
        key = (getattr(self.cfg, "api_key", "") or "").strip()
        if not key:
            return "未填写 API Key（设置 → API 配置）"
        try:
            client = ApiClient(self.cfg.api_base, key, min(float(self.cfg.timeout or 60), 15))
            models = client.models()
            self._log(f"API 预检通过：{len(models)} 个模型可用", "INFO")
            # 余额：已知不足就提醒（不阻断，允许只跑免费步骤）
            try:
                _bal = self._fresh_balance()
                self._apply_balance_state()
                if 0 <= _bal < 0.2:
                    self._log(f"API 余额不足：剩 ¥{_bal:.3f}（AI 图片编辑约 ¥0.2/次），本次会跳过付费步骤；"
                              f"请到「设置 → API」充值", "WARN")
                    self.toast(f"API 余额不足（剩 ¥{_bal:.3f}），AI 图片编辑会被跳过，建议先充值", False)
            except Exception:
                pass
            return ""
        except ApiError as ex:
            from ..api import classify_api_error
            kind = classify_api_error(ex)
            status = int(getattr(ex, "status", 0) or 0)
            msg = str(ex)
            if kind == "auth":
                return f"API Key 无效或已过期（HTTP {status or 401}：{msg[:70]}）——请到设置页重新填写"
            if kind == "quota":
                return f"API 额度不足或请求受限（HTTP {status}：{msg[:70]}）——请充值后重试"
            self._log(f"API 预检异常（不阻断）：{msg[:80]}", "WARN")
            return ""
        except Exception as ex:  # noqa: BLE001
            self._log(f"API 预检网络异常（不阻断）：{str(ex)[:80]}", "WARN")
            return ""

    def _fresh_balance(self) -> float:
        """只信任 10 分钟内测到的余额；旧数据一律当“未知”（-1）。

        旧版把一次「余额不足」的报错永久写进配置，充值后仍会提示余额少，
        客户看到的就是「明明有钱还说没钱」。现在过期即作废。
        """
        try:
            import time as _t
            ts = float(getattr(self.cfg, "api_balance_ts", 0) or 0)
            bal = float(getattr(self.cfg, "api_balance", -1) or -1)
        except Exception:
            return -1.0
        if ts <= 0 or (_t.time() - ts) > 600:
            return -1.0
        return bal

    def _probe_balance(self, silent: bool = False):
        """用一次最小请求判断余额：成功=可用；失败若是额度错误，解析剩余金额。

        silent=True 用于启动时后台探测：不弹 toast，只写日志/刷新概览。
        """
        from ..api import ApiClient, parse_balance, classify_api_error
        if not str(getattr(self.cfg, "api_key", "") or "").strip():
            return
        try:
            cli = ApiClient(self.cfg.api_base, self.cfg.api_key, 20)
            cli.chat("qwen-plus", [{"role": "user", "content": "hi"}], max_tokens=1)
            self.cfg.set("api_balance", -1)
            self.cfg.set("api_balance_ts", 0)
            save_config(self.cfg)
            self._apply_balance_state()
            if silent:
                self._log("余额探测：API 可用（最小请求成功），已清除历史余额不足缓存", "INFO")
            else:
                self.toast("余额可用（最小请求调用成功）", True)
            return
        except Exception as ex:  # noqa: BLE001
            import time as _t
            bal = parse_balance(ex)
            if bal is not None:
                self.cfg.set("api_balance", bal)
                self.cfg.set("api_balance_ts", _t.time())
                save_config(self.cfg)
                self._apply_balance_state()
                self.toast(f"余额不足：剩 ¥{bal:.3f}", False)
            else:
                kind = classify_api_error(ex)
                self.toast("检测失败：" + ("Key 无效" if kind == "auth" else str(ex)[:60]), False)

    def _apply_balance_state(self):
        """把最近一次已知余额刷到概览页（-1=未知）。"""
        try:
            bal = self._fresh_balance()
        except Exception:
            bal = -1
        ctrl = getattr(self, "ready_balance", None)
        if ctrl is None:
            return
        try:
            if bal < 0:
                ctrl.value = "未知（跑一次后显示）"
                ctrl.color = MUTED
            elif bal < 0.2:
                ctrl.value = f"¥{bal:.3f} 不足（AI 图片编辑约 ¥0.2/次）→ 去「设置」充值"
                ctrl.color = DANGER_C
            else:
                ctrl.value = f"¥{bal:.3f}"
                ctrl.color = ACCENT_2
            self._safe_update()
        except Exception:
            pass

    def _apply_license_state(self):
        st = getattr(self, "license_state", None) or {}
        ctrl = getattr(self, "stat_auth", None)
        detail = getattr(self, "stat_auth_detail", None)
        if ctrl is None or not st:
            return
        try:
            if st.get("ok"):
                exp = str(st.get("expiry") or "")
                ctrl.value = "已授权"
                ctrl.color = ACCENT_2
                if detail is not None:
                    detail.value = f"到期：{exp}" if exp else "永久有效"
            else:
                ctrl.value = "未授权"
                ctrl.color = DANGER_C
                if detail is not None:
                    detail.value = str(st.get("message") or "点「重新检测」或检查网络")[:40]
            self._safe_update()
        except Exception:
            pass

    def _validate(self) -> list[str]:
        cfg = self.cfg
        errs = []
        # 会「静默失效」的配置（选了通用编辑却没提示词、三组都没启用……）
        try:
            from ..services.preflight import blocking
            for _b in blocking(cfg):
                if _b not in errs:
                    errs.append(_b)
        except Exception:
            pass
        from ..services.preflight import ai_active
        if ai_active(cfg) and not (cfg.api_key or "").strip():
            if not any("API Key" in error for error in errs):
                errs.append("未填写 API Key")
        if not (cfg.store_id or cfg.store_name):
            errs.append("未选择店铺（点「同步店铺/模板」）")
        if not any((cfg.product_template_id, cfg.product_template_name,
                    getattr(cfg, "template_product_id", ""), getattr(cfg, "template_product_title", ""),
                    (getattr(cfg, "template_snapshot", "") or "").strip() not in ("", "{}"))):
            errs.append("未选择产品模板（可在「店铺设置 → 选择模板」选一个）")
        try:
            if int(str(cfg.edit_sum or "0").strip() or "0") < 0:
                errs.append("处理产品数量不能为负数（0 或留空处理全部）")
        except Exception:
            errs.append("编辑产品数量不是有效数字")
        try:
            if int(cfg.max_sku_count) <= 0:
                errs.append("SKU 上限必须大于 0")
        except Exception:
            errs.append("SKU 上限不是有效数字")
        try:
            if not (1 <= int(cfg.concurrency) <= 10):
                errs.append("并发数应在 1~10")
        except Exception:
            errs.append("并发数不是有效数字")
        if str(getattr(cfg, "price_mode", "multiplier")) in ("multiplier", "commission"):
            try:
                if float(cfg.price_multiplier) <= 0:
                    errs.append("价格倍率必须大于 0")
            except Exception:
                errs.append("价格倍率不是有效数字")
        if getattr(cfg, "combo_enable", False):
            import math
            try:
                factor = float(cfg.combo_multiplier)
                if not math.isfinite(factor) or factor <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errs.append("套装价格倍率必须为大于 0 的有效数字（变种与库存 → 套装 SKU）")
        if (getattr(cfg, "overlay_watermark_enable", False) and cfg.watermark
                and ("/" in str(cfg.watermark) or str(cfg.watermark).lower().endswith((".png", ".jpg", ".webp")))):
            from pathlib import Path as _P
            if not _P(str(cfg.watermark)).exists():
                errs.append(f"水印文件不存在：{cfg.watermark}")
        # 定时发布：时间必须能解析且在未来（否则保存后永远不会发布）
        try:
            if getattr(cfg, "schedule_publish", False):
                from ..services.publisher import parse_run_at
                when = str(getattr(cfg, "schedule_time", "") or "").strip()
                if not when:
                    errs.append("已开启定时发布，但没填发布时间（例：2026-11-12 09:00）")
                else:
                    run_at = parse_run_at(when)
                    if run_at <= time.time():
                        errs.append(f"定时发布时间已过：{when}，请改成将来的时间")
        except Exception as ex:
            errs.append(f"定时发布时间格式不对：{str(ex)[:60]}")
        # 欧盟资质选「是」时必须有责任人资料，否则保存会被站点拦截（ISSUES #4）
        try:
            if str(getattr(cfg, "eu_qualification", "否")) == "是":
                miss = self._eu_missing()
                if miss:
                    errs.append("已选「提交欧盟资质」，还缺欧盟责任人：" + "、".join(miss)
                                + "（高级设置 → 欧盟资质）")
        except Exception:
            pass
        return errs

    def _sync_run_button(self):
        """根据运行状态切换「启动/停止」按钮外观。"""
        try:
            running = bool(getattr(self, "_running", False))
            stopping = bool(getattr(self, "_stop_requested", False))
            if running or stopping:
                self.btn_run_icon.name = ft.Icons.STOP_CIRCLE
                self.btn_run_icon.color = "white"
                self.btn_run_text.value = "停止中…" if stopping else "停止"
                self.btn_run_text.color = "white"
                self.btn_run.bgcolor = DANGER_BG
            else:
                self.btn_run_icon.name = ft.Icons.ROCKET_LAUNCH
                self.btn_run_icon.color = ON_ACCENT
                self.btn_run_text.value = "启动任务"
                self.btn_run_text.color = ON_ACCENT
                self.btn_run.bgcolor = ACCENT
            if getattr(self, "shell_status", None) is not None:
                self.shell_status.value = "正在停止…" if stopping else ("任务运行中" if running else "任务空闲")
                self.shell_status.color = WARN_C if stopping else (SUCCESS_C if running else MUTED)
            self._safe_update()
        except Exception:
            pass

    def _toggle_run(self, e=None):
        """一个按钮两种状态：运行中→停止，空闲→启动。"""
        if bool(getattr(self, "_running", False)) or bool(getattr(self, "_stop_requested", False)):
            self._request_stop()
        else:
            _ids = list(getattr(self, "run_scope_ids", []) or [])
            self._start(only_ids=_ids or None)
        (getattr(self, '_sync_run_button', None) or (lambda: None))()

    def _retry_publish_failed(self):
        """只重试「发布失败」的商品：不跑 AI、不处理图片，只重新提交发布。

        整个过程放到后台线程（扫描店铺可能要几十秒），点按钮立刻有反馈。
        """
        self._log("开始扫描「发布失败」商品…（可能需要 10~30 秒）", "INFO")
        try:
            self.toast("正在扫描发布失败商品…")
        except Exception:
            pass

        def work():
            try:
                from ..services import publish_retry as pr
                rows = pr.items()
            except Exception:
                rows = []
            try:
                from ..integrations.dianxiaomi import DianxiaomiClient
                ck0 = self._cookies()
                dxm0 = DianxiaomiClient(ck0, overrides=self.cfg.endpoint_overrides or {})
                store_rows = dxm0.publish_failed_products(self.cfg.store_id or None)
                have = {r["id"] for r in rows}
                for r in store_rows:
                    if r["id"] not in have:
                        rows.append(r)
                self._log(f"店铺里检测到 {len(store_rows)} 个发布失败商品", "INFO")
            except Exception as ex:  # noqa: BLE001
                self._log(f"扫描发布失败商品出错：{str(ex)[:100]}", "WARN")
            if not rows:
                self._log("没有发布失败的商品", "WARN")
                self.toast("没有发布失败的商品", False)
                return

            from collections import Counter
            _c = Counter((r.get("error") or "未知")[:26] for r in rows)
            self._log(f"待重试发布 {len(rows)} 个，失败原因分布：", "INFO")
            for _k, _v in _c.most_common(5):
                self._log(f"   {_v:4d} × {_k}", "INFO")

            # 每次点击最多处理 N 个（避免一次点下去跑十几分钟没反馈）
            try:
                _cap = int(self.cfg.edit_sum or 0)
            except Exception:
                _cap = 0
            _cap = _cap if _cap > 0 else 20
            if len(rows) > _cap:
                _left = len(rows) - _cap
                self._log(f"本次先处理 {_cap} 个，剩余 {_left} 个（再点一次继续）", "INFO")
                rows = rows[:_cap]

            from ..integrations.dianxiaomi import DianxiaomiClient
            from ..services.publisher import Publisher, PublishResult
            try:
                ck = self._cookies()
            except Exception as ex:  # noqa: BLE001
                self._log(f"读取 Cookie 失败：{ex}", "ERROR")
                return
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            pub = Publisher(self.cfg, dxm=dxm, ws_endpoint="")
            ok = fail = 0
            for i, r in enumerate(rows, 1):
                if getattr(self, "_abort_now", False):
                    self._log("已停止重试发布", "WARN")
                    break
                pid = r["id"]
                try:
                    out = pub.publish_only(pid, PublishResult(product_id=pid, ok=True))
                except Exception as ex:  # noqa: BLE001
                    out = None
                    self._log(f"[{i}/{len(rows)}] ✗ {pid} 异常：{str(ex)[:90]}", "ERROR")
                if out is not None and out.ok:
                    ok += 1
                    self._log(f"[{i}/{len(rows)}] ✓ {pid} {out.message[:90]}", "INFO")
                    try:
                        pr.remove(pid)
                    except Exception:
                        pass
                else:
                    fail += 1
                    _msg = (out.message if out is not None else "异常")
                    self._log(f"[{i}/{len(rows)}] ✗ {pid} {_msg[:110]}", "WARN")
                    try:
                        pr.record(pid, _msg, r.get("title") or "")
                    except Exception:
                        pass
            self._log(f"只重试发布完成：成功 {ok} / 失败 {fail}（未调用 AI，未产生费用）", "INFO")
            try:
                self.toast(f"重试发布：成功 {ok} / 失败 {fail}")
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _publish_failed_count(self) -> int:
        """发布失败列表里的商品数（用于「只重试发布失败」按钮上的数字）。"""
        try:
            from ..services import publish_retry as _prc
            return int(_prc.count())
        except Exception:
            return 0

    def _rerun_failed(self):
        """只重跑上一次批量里失败/未完成的商品（忽略已编辑记录）。"""
        try:
            from ..services import batch_state as bs
            info = bs.load()
        except Exception:
            info = {}
        ids = list(info.get("failed") or []) + list(info.get("remaining") or [])
        ids = [str(x) for x in dict.fromkeys(ids) if str(x).strip()]
        if not ids:
            self.toast("没有上次失败/未完成的商品", False)
            return
        self._log(f"重跑上次失败/未完成：{len(ids)} 个商品", "INFO")
        self.toast(f"开始重跑 {len(ids)} 个商品")
        self._start(ignore_conflicts=True, only_ids=ids)

    def _warn_conflicts_before_run(self) -> bool:
        """启动前检查开关冲突。返回 True 表示已拦截（用户选择去修改）。"""
        # 先做一次自动修复：开了「AI 生成主图」但提示词为空 = 误开，自动关掉
        try:
            if (getattr(self.cfg, "main_image_generate_enable", False)
                    and not (getattr(self.cfg, "main_image_generate_prompt", "") or "").strip()):
                self.cfg.set("main_image_generate_enable", False)
                save_config(self.cfg)
                self._log("自动修复：「AI 生成主图」开着但提示词为空，已关闭该功能", "WARN")
        except Exception:
            pass
        try:
            from ..services.image_task_plan import detect_conflicts
            cf = detect_conflicts(self.cfg)
        except Exception:
            return False
        hard = [c for c in cf if c.get("level") in ("high", "mid")]
        if not hard:
            # 没有冲突：明确告诉用户「没问题」
            self._log("开关检查：未发现冲突，配置正常", "INFO")
            self.toast("配置检查通过，没有冲突")
            return False
        lines = []
        for c in hard[:6]:
            lines.append(f"[{c['level'].upper()}] {c['title']}\n  {c['detail']}\n  → {c['fix']}")
        text = "\n\n".join(lines)
        self._log("启动前发现开关冲突：" + "；".join(c["title"] for c in hard[:4]), "WARN")

        def go_fix(e):
            self._close_dlg(dlg)
            self._switch(5)   # AI 图片页

        def go_anyway(e):
            self._close_dlg(dlg)
            self._log("用户选择忽略冲突继续启动", "WARN")
            self._start(ignore_conflicts=True)

        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("发现可能的开关冲突"),
            content=ft.Column([
                ft.Text("这些开关同时开启可能互相覆盖，建议先处理：", size=13.5, color=TEXT),
                ft.Text(text, size=12.5, color=MUTED, selectable=True),
            ], spacing=8, width=560, scroll=ft.ScrollMode.AUTO, height=320),
            actions=[ft.OutlinedButton("去修改（推荐）", on_click=go_fix),
                     ft.TextButton("仍然启动", on_click=go_anyway)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dlg(self.dlg)
        return True

    def _start(self, ignore_conflicts: bool = False, only_ids: list | None = None):
        # 启动前先把当前配置全部落盘（含未失焦的输入）
        self._save_api_fields()
        # 启动前检查图片开关冲突：有冲突先提醒，用户可强制继续
        if not ignore_conflicts:
            try:
                if self._warn_conflicts_before_run():
                    return
            except Exception:
                pass
        try:
            for f in self.field_refs:
                try:
                    k, v = f.read()
                    self.cfg.set(k, v)
                except Exception:
                    continue
            save_config(self.cfg)
        except Exception:
            pass
        # 立刻跳到「任务与日志」，让用户看到实时进度
        try:
            self._switch(10)
        except Exception:
            pass
        # 授权校验：未授权不允许开始；但不要在 UI 线程里阻塞等网络
        try:
            if getattr(self.cfg, "license_enforce", True) and not (self.license_state or {}).get("ok"):
                state = self.license_state or {}
                cached_ok = bool(getattr(self.cfg, "license_cache_ok", False)) and \
                    (time.time() - float(getattr(self.cfg, "license_cache_ts", 0) or 0) < 72 * 3600)
                exp = str(getattr(self.cfg, "license_cache_expiry", "") or "")
                if cached_ok and exp and exp < __import__("datetime").date.today().isoformat():
                    cached_ok = False
                if not state:
                    threading.Thread(target=self._check_license, daemon=True).start()
                    if not cached_ok:
                        self._log("授权检测中，本次启动已阻止（请 1~3 秒后再点启动）", "WARN")
                        self.toast("正在检测授权，请稍后再点启动", False)
                        return
                    self._log("使用上次授权缓存启动，后台复核中…", "INFO")
                else:
                    self._show_license_block(str(state.get("message") or "未授权"))
                    return
        except Exception as ex:
            self._log(f"授权检查异常：{ex}", "WARN")
        if getattr(self, "_running", False):
            self.toast("正在运行，请等当前任务结束或点「停止」", False)
            return
        self._stop_requested = False
        try:
            if getattr(self, "run_status", None) is not None:
                self.run_status.value = "状态：运行中…"
                self.run_status.color = ACCENT_2
        except Exception:
            pass
        errs = self._validate()
        if errs:
            dlg = ft.AlertDialog(title=ft.Text("启动前检查未通过"),
                                 content=ft.Column([ft.Text("• " + e, color=DANGER_C, size=13.5) for e in errs],
                                                   spacing=6, height=200, width=460),
                                 actions=[ft.TextButton("去修改", on_click=lambda e: (self._close_dlg(dlg), self._switch(1))),
                                          ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))])
            self._show_dlg(dlg)
            return
        self._log("启动：配置检查通过")
        self._switch(10)
        self._log("开始执行…")
        threading.Thread(target=self._run_pipeline, args=(only_ids,), daemon=True).start()

    def _run_scheduled(self, job):
        """到点执行「定时发布」：商品已保存，这里用 API op=2 直接发布（无浏览器）。"""
        if os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"):
            raise RuntimeError("本地验收预演：未执行真实定时发布")
        self._log(f"定时发布触发：{job.product_id}（{job.title[:30]}）", "INFO")
        try:
            from ..integrations.dianxiaomi import DianxiaomiClient
            from ..services.api_save import build_save_payload
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            pid = str(job.product_id)
            _tpl = str(getattr(self.cfg, "template_product_id", "") or "").strip()
            target = dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
            tpl = None
            if _tpl:
                tpl = dxm._get("product_detail", {"id": _tpl}).get("data", {}).get("product", {})
            if not target:
                raise RuntimeError("未取到商品详情")
            payload = build_save_payload(target, tpl, action="publish",
                                         custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None))
            resp = dxm.save_product_via_api(payload)
            _code = resp.get("code") if isinstance(resp, dict) else None
            _d = resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else {}
            _msg = str(_d.get("msg") or (resp.get("msg") if isinstance(resp, dict) else "") or "")
            if _code != 0:
                raise RuntimeError(_msg[:120] or "发布失败")
            # 店小秘可能返回 code=0 但 msg 是「操作失败，只有处在采集箱中的产品…」
            try:
                from ..services.publisher import is_failure_msg as _ifm
            except Exception:
                _ifm = lambda m: False  # noqa: E731
            if _ifm(_msg):
                raise RuntimeError(_msg[:120])
            self._log(f"定时发布成功：{pid}", "INFO")
            try:
                history.record("scheduled_publish", 1, 0, pid)
            except Exception:
                pass
        except Exception as ex:
            self._log(f"定时发布失败：{ex}", "ERROR")
            try:
                history.record("scheduled_publish", 0, 1, str(job.product_id))
            except Exception:
                pass
            raise

    def _save_api_fields(self):
        """把设置页 API 卡里的值落盘（输入即保存，启动前也会再存一次）。"""
        try:
            if getattr(self, "api_key_field", None) is not None:
                self.cfg.set("api_key", str(self.api_key_field.value or "").strip())
            if getattr(self, "cfg_concurrency", None) is not None:
                try:
                    self.cfg.set("concurrency", max(1, min(8, int(float(self.cfg_concurrency.value or 3)))))
                except Exception:
                    pass
            if getattr(self, "cfg_timeout", None) is not None:
                try:
                    self.cfg.set("timeout", max(30, int(float(self.cfg_timeout.value or 180))))
                except Exception:
                    pass
            save_config(self.cfg)
        except Exception:
            pass

    def _show_balance_low(self, bal: float):
        if getattr(self, "_dlg", None) is not None:
            return
        """余额不足弹窗：说明会被跳过的步骤，并给「去充值 / 继续」两个选择。"""
        dlg = ft.AlertDialog(
            title=ft.Text("API 余额不足"),
            content=ft.Column([
                ft.Text(f"当前余额：¥{bal:.3f}", size=14.5, color=DANGER_C,
                        weight=ft.FontWeight.BOLD),
                ft.Text("AI 图片编辑约 ¥0.2 / 次，余额不足时这一步会被自动跳过"
                        "（白底/去水印等本地免费步骤不受影响）。", size=13.5, color=TEXT),
                ft.Text("充值后重新运行即可恢复正常出图。", size=12.5, color=MUTED),
            ], spacing=8, width=440),
            actions=[
                ft.TextButton("继续（跳过 AI 出图）", on_click=lambda e: self._close_dlg(dlg)),
                ft.ElevatedButton("去充值", bgcolor="#F0A020", color="#1A1200",
                                  on_click=lambda e: self._open_recharge(dlg)),
            ],
        )
        self._show_dlg(dlg)

    def _open_recharge(self, dlg=None):
        try:
            if dlg is not None:
                self._close_dlg(dlg)
        except Exception:
            pass
        url = str(getattr(self.cfg, "api_purchase_url", "") or "https://api.moneyfly.club")
        try:
            self.page.launch_url(url)
        except Exception:
            self.toast(f"请在浏览器打开 {url}", False)

    def _category_map_dialog(self):
        from ..services.category_map import load as _cm_load
        m = _cm_load()
        rules = m.get("rules") or []
        learned = m.get("learned") or {}
        body = "\n".join(f"{r.get('kw')}={r.get('categoryId')}" for r in rules
                         if isinstance(r, dict))
        tf = ft.TextField(value=body, multiline=True, min_lines=6, max_lines=12, width=560,
                          label="关键词规则（每行一条：关键词=类目ID）",
                          hint_text="hydraulic=100007204")
        info = ft.Text(f"学习表：{len(learned)} 个关键词", size=12.5, color=MUTED)

        def do_save(e):
            from ..services.category_map import load as _l, save as _s
            cur = _l()
            new_rules = []
            for line in (tf.value or "").splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                kw, cid = line.split("=", 1)
                if kw.strip() and cid.strip():
                    new_rules.append({"kw": kw.strip(), "categoryId": cid.strip()})
            cur["rules"] = new_rules
            _s(cur)
            self._log(f"类目规则已保存：{len(new_rules)} 条", "INFO")
            self.toast(f"已保存 {len(new_rules)} 条关键词规则", True)
            self._close_dlg(dlg)

        def do_learn(e):
            self._close_dlg(dlg)
            threading.Thread(target=self._category_learn, daemon=True).start()

        def do_clear(e):
            from ..services.category_map import save as _s
            _s({"rules": rules, "learned": {}})
            info.value = "学习表：0 个关键词"
            self._safe_update()
            self.toast("已清空学习表", True)

        dlg = ft.AlertDialog(
            title=ft.Text("类目自动匹配"),
            content=ft.Column([
                ft.Text("匹配顺序：关键词规则 → 学习表 → 商品自带类目 → 模板类目", size=12.5, color=MUTED),
                tf, info,
            ], spacing=8, width=580),
            actions=[ft.TextButton("从历史商品学习", on_click=do_learn),
                     ft.TextButton("清空学习表", on_click=do_clear),
                     ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("保存规则", bgcolor=ACCENT, color=ON_ACCENT, on_click=do_save)],
        )
        self._show_dlg(dlg)

    def _category_learn(self):
        try:
            from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
            from ..services.category_map import learn_from_products, merge_learned
            dxm = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            sid = self.cfg.store_id or None
            # 列表接口不返回类目，必须逐个取详情；限制数量避免太慢
            cand = []
            for st, pages in (("editingRequired", 3), ("onSelling", 4)):
                for page in range(1, pages + 1):
                    got, meta = dxm.product_page(page, 100, sid, st)
                    if not got:
                        break
                    cand.extend(got)
                    if len(got) < 100:
                        break
            limit = 150
            cand = cand[:limit]
            self._log(f"类目学习：准备读取 {len(cand)} 个商品的详情…", "INFO")
            items = []
            for i, it in enumerate(cand, 1):
                try:
                    d0 = dxm._get("product_detail", {"id": it.get("id")}).get("data", {}).get("product", {})
                    items.append({"title": d0.get("subject") or "", "categoryId": d0.get("categoryId")})
                except Exception:
                    continue
                if i % 30 == 0:
                    self._log(f"类目学习：已读 {i}/{len(cand)}", "INFO")
                time.sleep(0.05)
            learned = learn_from_products(items)
            merge_learned(learned)
            self._log(f"类目学习：学到 {len(learned)} 个关键词", "INFO")
            self.toast(f"已学习 {len(learned)} 个关键词", True)
        except Exception as ex:  # noqa: BLE001
            self._log(f"类目学习失败：{str(ex)[:150]}", "ERROR")
            self.toast(f"类目学习失败：{str(ex)[:80]}", False)

    def _scope_dialog(self):
        """选择商品：搜索采集箱商品，多选后只处理这些商品（不分页，整表滚动）。"""
        kw = ft.TextField(hint_text="标题关键词（留空=全部采集箱）", width=320, dense=True,
                          bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        info = ft.Text("", size=12.5, color=MUTED)
        col = ft.Column(spacing=2, height=420, scroll=ft.ScrollMode.AUTO)
        picked: set[str] = set(str(x) for x in (getattr(self, "run_scope_ids", []) or []))
        state = {"rows": []}
        sel_text = ft.Text(f"已选 {len(picked)} 个", size=12.5, color=ACCENT_2)
        dlg_holder: dict = {}

        def _upd():
            try:
                self.page.update()
            except Exception:
                pass

        def render():
            rows = []
            for it in state["rows"]:
                pid = str(it.get("id") or "")
                imgs = it.get("images") or []
                cb = ft.Checkbox(value=pid in picked,
                                 on_change=lambda e, _p=pid: _toggle(_p, e.control.value))
                rows.append(ft.ListTile(
                    leading=ft.Row([cb, self._zoom_img(imgs[0] if imgs else "", 38, 38, "主图",
                                                       gallery=imgs)], spacing=2, tight=True),
                    title=ft.Text((it.get("title") or pid)[:66], size=13.5, color=TEXT, max_lines=2),
                    subtitle=ft.Text(pid, size=12, color=MUTED), dense=True))
            col.controls = rows or [empty_state("没有匹配的商品")]
            info.value = f"共 {len(state['rows'])} 个（上下滚动查看全部）"
            _upd()

        def _toggle(pid, on):
            (picked.add(pid) if on else picked.discard(pid))
            sel_text.value = f"已选 {len(picked)} 个"

        def _load_children(node):
            """非叶子类目：展开子类目供继续选择（机械类目常有两层）。"""
            cid = str(node.get("categoryId") or "")
            status.value = f"「{node.get('nameZh')}」不是末级类目，正在加载子类目…"
            status.color = MUTED
            try:self._safe_update()
            except Exception: pass

            def work():
                try:
                    ch = client().categories(cid)
                    cat_box.controls = [h1(f"{node.get('nameZh')} 的子类目", 13)]
                    for x in ch:
                        leaf = int(x.get("isleaf") or 0) == 1
                        def pick_child(ev, _x=x):
                            self.cfg.set("alibaba_category_id", str(_x.get("categoryId") or ""))
                            self.cfg.set("alibaba_category_name", f"{_x.get('nameZh')} / {_x.get('nameEn')}")
                            full = str(_x.get("nodePathId") or _x.get("categoryId") or "")
                            self.cfg.set("alibaba_full_cid", full.replace("/", ","))
                            save_config(self.cfg)
                            status.value = (f"已选类目：{_x.get('nameZh')}（{_x.get('categoryId')}）"
                                            + ("· 末级类目" if leaf else "· 还有子类目"))
                            status.color = SUCCESS_C
                            if not leaf:
                                _load_children(_x)
                            try:self._safe_update()
                            except Exception: pass
                        cat_box.controls.append(ft.Row([
                            ft.Text(f"{x.get('nameZh')} / {x.get('nameEn')} · {'末级' if leaf else 'L'+str(x.get('level'))} · {x.get('categoryId')}",
                                    size=12.5, color=TEXT, expand=True),
                            ft.OutlinedButton("选择", on_click=pick_child)], spacing=6))
                    status.value = f"子类目 {len(ch)} 个；末级类目才能发布"
                    status.color = SUCCESS_C
                except Exception as ex:  # noqa: BLE001
                    status.value = f"加载子类目失败：{ex}"
                    status.color = DANGER_C
                try:self._safe_update()
                except Exception: pass
            threading.Thread(target=work, daemon=True).start()

        def do_search(e=None):
            info.value = "读取中…"
            _upd()

            def work():
                try:
                    from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                    dxm = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    k = (kw.value or "").strip()
                    rows, _m = dxm.product_page(1, 500, self.cfg.store_id or None, "draft")
                    if k:
                        low = k.lower()
                        rows = [x for x in rows if low in str(x.get("title") or "").lower()]
                    state["rows"] = rows or []
                    info.color = ACCENT_2
                    render()
                except Exception as ex:  # noqa: BLE001
                    info.value = f"读取失败：{str(ex)[:100]}"
                    info.color = DANGER_C
                    _upd()

            threading.Thread(target=work, daemon=True).start()

        def apply_scope(e=None):
            self.run_scope_ids = sorted(picked)
            try:
                if self.search_hint is not None:
                    if self.run_scope_ids:
                        self.search_hint.value = f"当前：只处理已选 {len(self.run_scope_ids)} 个商品"
                        self.search_hint.color = ACCENT_2
                    else:
                        self.search_hint.value = "当前：处理全部采集箱商品"
                        self.search_hint.color = MUTED
            except Exception:
                pass
            self._log(f"范围已更新：{len(self.run_scope_ids)} 个指定商品", "INFO")
            self._close_dlg(dlg_holder.get("dlg"))
            self._safe_update()

        def _all(on):
            if on:
                for it in state["rows"]:
                    picked.add(str(it.get("id")))
            else:
                picked.clear()
            sel_text.value = f"已选 {len(picked)} 个"
            render()

        kw.on_submit = do_search
        dlg = ft.AlertDialog(
            title=ft.Text("选择要处理的商品"),
            content=ft.Column([ft.Row([kw,
                                       ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, bgcolor=ACCENT,
                                                         color=ON_ACCENT, on_click=do_search),
                                       ft.TextButton("全选", on_click=lambda e: _all(True)),
                                       ft.TextButton("清空", on_click=lambda e: _all(False))],
                                      spacing=8, wrap=True),
                               info, col,
                               ft.Row([ft.Container(expand=True), sel_text], spacing=10)],
                              spacing=8, width=720),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg_holder.get("dlg"))),
                     ft.ElevatedButton("只处理选中", bgcolor=ACCENT, color=ON_ACCENT, on_click=apply_scope)],
        )
        dlg_holder["dlg"] = dlg
        self._show_dlg(dlg)
        do_search()

    def _global_search_dialog(self):
        """全局搜商品：关键词 + 状态，结果整表滚动（不再分页，避免翻页关窗）。"""
        kw = ft.TextField(hint_text="标题关键词（留空=该状态全部）", width=320, dense=True,
                          bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        st = ft.Dropdown(width=170, dense=True, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                         value="draft",
                         options=[ft.dropdown.Option("draft", "采集箱"),
                                  ft.dropdown.Option("editingRequired", "待发布"),
                                  ft.dropdown.Option("publishFail", "发布失败"),
                                  ft.dropdown.Option("onSelling", "在线"),
                                  ft.dropdown.Option("auditing", "审核中")])
        info = ft.Text("输入关键词后点搜索", size=13.5, color=MUTED)
        col = ft.Column(spacing=2, height=430, scroll=ft.ScrollMode.AUTO)
        state = {"rows": []}
        picked: set[str] = set()
        sel_text = ft.Text("已选 0 个", size=13.5, color=ACCENT_2)
        dlg_holder: dict = {}

        def _upd():
            try:
                self.page.update()
            except Exception:
                pass

        def _toggle(pid, on):
            (picked.add(pid) if on else picked.discard(pid))
            sel_text.value = f"已选 {len(picked)} 个"

        def render():
            rows = []
            for it in state["rows"]:
                pid = str(it.get("id") or "")
                imgs = it.get("images") or []
                cb = ft.Checkbox(value=pid in picked,
                                 on_change=lambda e, _p=pid: _toggle(_p, e.control.value))
                rows.append(ft.ListTile(
                    leading=ft.Row([cb, self._zoom_img(imgs[0] if imgs else "", 38, 38, "主图",
                                                       gallery=imgs)], spacing=2, tight=True),
                    title=ft.Text((it.get("title") or pid)[:80], size=14.5, color=TEXT, max_lines=2),
                    subtitle=ft.Text(f"{pid} · {it.get('status_type') or ''}", size=12.5, color=MUTED),
                    dense=False))
            col.controls = rows or [empty_state("没有匹配的商品")]
            info.value = f"共 {len(state['rows'])} 个（上下滚动查看全部）"
            _upd()

        def do_search(e=None):
            info.value = "搜索中…"
            _upd()

            def work():
                try:
                    from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                    dxm = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    k = (kw.value or "").strip()
                    if k:
                        rows = dxm.search_products(k, self.cfg.store_id or None, st.value, 300)
                    else:
                        rows, _m = dxm.product_page(1, 300, self.cfg.store_id or None, st.value)
                    state["rows"] = rows or []
                    info.color = ACCENT_2
                    render()
                except Exception as ex:  # noqa: BLE001
                    info.value = f"搜索失败：{str(ex)[:100]}"
                    info.color = DANGER_C
                    _upd()

            threading.Thread(target=work, daemon=True).start()

        def _all(on):
            if on:
                for it in state["rows"]:
                    picked.add(str(it.get("id")))
            else:
                picked.clear()
            sel_text.value = f"已选 {len(picked)} 个"
            render()

        def _run_picked():
            ids = sorted(picked)
            if not ids:
                self.toast("先勾选要处理的商品", False)
                return
            self._close_dlg(dlg_holder.get("dlg"))
            self.run_scope_ids = ids
            self._start(only_ids=ids)

        kw.on_submit = do_search
        dlg = ft.AlertDialog(
            title=ft.Text("搜索商品"),
            content=ft.Column([ft.Row([kw, st,
                                       ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH,
                                                         bgcolor=ACCENT, color=ON_ACCENT,
                                                         on_click=do_search),
                                       ft.TextButton("全选", on_click=lambda e: _all(True)),
                                       ft.TextButton("清空", on_click=lambda e: _all(False))],
                                      spacing=8, wrap=True),
                               info, col,
                               ft.Row([ft.Container(expand=True), sel_text], spacing=10)],
                              spacing=8, width=740),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg_holder.get("dlg"))),
                     ft.ElevatedButton("处理选中商品", bgcolor=ACCENT, color=ON_ACCENT,
                                       on_click=lambda e: _run_picked())],
        )
        dlg_holder["dlg"] = dlg
        self._show_dlg(dlg)
        do_search()

    def _publish_one(self, pid: str, on_done=None):
        """单独发布 / 重试一个商品。"""
        def go():
            try:
                from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                c = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                r = c.batch_publish([str(pid)])
                ok = bool(r.get("success"))
                bad = (r.get("failed") or [{}])[0].get("msg", "") if not ok else ""
                self._log(("✓ 发布成功：" if ok else "✗ 发布失败：") + f"{pid} {bad}"[:120],
                          "INFO" if ok else "WARN")
                self.toast(("已提交发布：" if ok else f"发布失败：{bad[:60]}") + str(pid), ok)
            except Exception as ex:  # noqa: BLE001
                self._log(f"✗ 发布失败：{pid} {str(ex)[:100]}", "ERROR")
                self.toast(f"发布失败：{str(ex)[:60]}", False)
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
        threading.Thread(target=go, daemon=True).start()

    def _publish_list_dialog(self, offline_state: str, label: str):
        """查看「待发布 / 发布失败」的具体商品（前 100 条）。"""
        col = ft.Column(spacing=2, height=380, scroll=ft.ScrollMode.AUTO)
        status = ft.Text("读取中…", size=12.5, color=MUTED)
        picked: set[str] = set()
        sel_text = ft.Text("已选 0 个", size=12.5, color=MUTED)

        sort_dd = ft.Dropdown(
            label="排序", width=170, dense=True, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value="default",
            options=[ft.dropdown.Option("default", "默认（最新在前）"),
                     ft.dropdown.Option("img_desc", "图多 → 少"),
                     ft.dropdown.Option("img_asc", "图少 → 多")])
        state = {"items": []}

        def _toggle(pid, on):
            (picked.add(pid) if on else picked.discard(pid))
            sel_text.value = f"已选 {len(picked)} 个"

        def _img_cnt(it) -> str:
            u = str(it.get("imageURLs") or "")
            n = len([x for x in u.split(";") if x.strip()]) if u else 0
            return f"{n} 张图"

        def _upd_time(it) -> str:
            import time as _t
            for k in ("gmtModified", "updateTime", "modifyTime"):
                v = it.get(k)
                try:
                    if v:
                        ts = float(v) / 1000.0
                        return " · " + _t.strftime("%m-%d %H:%M", _t.localtime(ts))
                except Exception:
                    continue
            return ""

        def _after(pid):
            """单条发布成功后：从列表移除并刷新，状态栏给反馈。"""
            state["items"] = [x for x in state["items"] if str(x.get("id")) != str(pid)]
            picked.discard(str(pid))
            sel_text.value = f"已选 {len(picked)} 个"
            status.value = f"已提交发布：{pid}（已从列表移除）"
            status.color = ACCENT_2
            _render()

        def _render(e=None):
            items = list(state["items"])
            mode = str(getattr(sort_dd, "value", "default") or "default")
            if mode == "img_desc":
                items.sort(key=lambda x: len(str(x.get("imageURLs") or "").split(";")) if x.get("imageURLs") else 0, reverse=True)
            elif mode == "img_asc":
                items.sort(key=lambda x: len(str(x.get("imageURLs") or "").split(";")) if x.get("imageURLs") else 0)
            rows = []
            for it in items:
                pid = str(it.get("id") or "")
                title = str(it.get("subject") or it.get("productName") or "")[:70]
                rows.append(ft.ListTile(
                    leading=ft.Row([
                        ft.Checkbox(value=pid in picked,
                                    on_change=lambda e, _p=pid: _toggle(_p, e.control.value)),
                        self._zoom_img((it.get("imageURLs") or "").split(";")[0]
                                       if it.get("imageURLs") else "", 40, 40, "主图")],
                        spacing=2, tight=True),
                    title=ft.Text(title or pid, size=13.5, color=TEXT, max_lines=2),
                    subtitle=ft.Text(
                        pid + " · " + _img_cnt(it) + _upd_time(it),
                        size=12, color=MUTED),
                    trailing=ft.TextButton("发布", icon=ft.Icons.SEND,
                                           on_click=lambda e, _p=pid:
                                           self._publish_one(_p, on_done=lambda: _after(_p))),
                    dense=False))
            col.controls = rows or [empty_state("这一栏没有商品")]
            try:
                self._safe_update()
            except Exception:
                pass

        sort_dd.on_change = _render

        def _publish_picked(e=None):
            if not picked:
                self.toast("先勾选要发布的商品", False)
                return
            ids = list(picked)
            self.toast(f"提交发布 {len(ids)} 个…", True)

            def work():
                try:
                    from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                    c = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    r = c.batch_publish(ids)
                    ok, bad = len(r.get("success") or []), len(r.get("failed") or [])
                    self._log(f"批量发布（已选）：成功 {ok} / 失败 {bad}", "INFO" if not bad else "WARN")
                    self.toast(f"发布完成：成功 {ok} / 失败 {bad}", not bad)
                except Exception as ex:  # noqa: BLE001
                    self.toast(f"发布失败：{str(ex)[:60]}", False)
            threading.Thread(target=work, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text(label),
            content=ft.Column([ft.Row([status, ft.Container(expand=True), sort_dd],
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER), col,
                               ft.Row([sel_text,
                                       ft.ElevatedButton("发布所选的商品", icon=ft.Icons.SEND,
                                                         bgcolor=ACCENT, color=ON_ACCENT,
                                                         on_click=_publish_picked)],
                                      spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                              spacing=8, width=660),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self._show_dlg(dlg)

        def work():
            try:
                from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                c = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                raw = c._post("product_list", {
                    "pageNo": 1, "pageSize": 100, "total": 0, "searchType": 0,
                    "searchValue": "", "shopId": self.cfg.store_id or -1,
                    "dxmState": "offline", "dxmOfflineState": offline_state})
                po = ((raw.get("data") or {}).get("page") or {})
                items = po.get("list") or []
                state["items"] = items
                _render()
                status.value = f"共 {po.get('totalSize') or len(items)} 个（显示前 {len(items)} 个）"
                status.color = ACCENT_2
            except Exception as ex:  # noqa: BLE001
                status.value = f"读取失败：{str(ex)[:120]}"
                status.color = DANGER_C
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _confirm(self, title: str, body: str, on_yes):
        """通用二次确认（批量发布这类会影响线上商品的动作）。"""
        def yes(e):
            self._close_dlg(dlg)
            try:
                on_yes()
            except Exception as ex:  # noqa: BLE001
                self.toast(f"执行失败：{ex}", False)

        dlg = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(body, size=13.5, color=TEXT),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("确认执行", bgcolor=ACCENT, color=ON_ACCENT, on_click=yes)],
        )
        self._show_dlg(dlg)

    def _batch_publish(self, offline_state: str, label: str):
        """批量发布：按 dxmOfflineState 取商品 -> batchPublish -> 汇总。"""
        if os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"):
            self._log("本地验收预演：未提交" + label, "WARN")
            self.toast("本地验收预演：未提交真实批量发布", False)
            return
        try:
            ck = self._cookies()
            from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
            dxm = _DXC(ck, overrides=self.cfg.endpoint_overrides or {})
            sid = self.cfg.store_id or None
            raw = dxm._post("product_list", {
                "pageNo": 1, "pageSize": 500, "total": 0, "searchType": 0,
                "searchValue": "", "shopId": sid or -1,
                "dxmState": "offline", "dxmOfflineState": offline_state})
            po = ((raw.get("data") or {}).get("page") or {})
            ids = [str(x.get("id")) for x in (po.get("list") or []) if x.get("id")]
            if not ids:
                self._log(label + "：没有可发布的商品", "WARN")
                self.toast(label + "：没有商品", False)
                return
            self._log(label + f"：开始发布 {len(ids)} 个商品…", "INFO")
            r = dxm.batch_publish(ids)
            ok, bad = r.get("success") or [], r.get("failed") or []
            self._log(label + f"：成功 {len(ok)} / 失败 {len(bad)}", "INFO" if not bad else "WARN")
            for b in bad[:5]:
                self._log(f"  ✗ {b.get('id')} {str(b.get('msg'))[:80]}", "WARN")
            self.toast(label + f"：成功 {len(ok)} / 失败 {len(bad)}", not bad)
            try:
                self._refresh_store_counts()
            except Exception:
                pass
        except Exception as ex:  # noqa: BLE001
            self._log(label + " 失败：" + str(ex)[:150], "ERROR")
            self.toast(label + " 失败：" + str(ex)[:80], False)

    def _batch_publish_waiting(self):
        self._batch_publish("waitPublish", "批量发布待发布商品")

    def _batch_publish_failed(self):
        self._batch_publish("publishFail", "重试发布失败商品")

    def _check_conflicts(self, silent: bool = False):
        """检查所有开关的互斥/依赖/无效组合，弹窗列出并给出改法。"""
        try:
            from ..services.conflicts import check as _cc
            items = _cc(self.cfg)
        except Exception as ex:
            self.toast(f"冲突检查失败：{ex}", False)
            return
        errs = [c for c in items if c.level == "error"]
        warns = [c for c in items if c.level != "error"]
        try:
            self.ready_conflict.value = (f"✗ {len(errs)} 个阻断 / {len(warns)} 个提示"
                                         if errs else
                                         (f"⚠ {len(warns)} 个提示" if warns else "✓ 无冲突"))
            self.ready_conflict.color = DANGER_C if errs else (WARN_C if warns else ACCENT_2)
            self._safe_update()
        except Exception:
            pass
        for c in items:
            self._log("配置" + ("冲突" if c.level == "error" else "提示") + "：" + c.title + " -> " + c.fix,
                      "ERROR" if c.level == "error" else "WARN")
        if silent:
            return
        if not items:
            self.toast("没有发现配置冲突")
            return
        rows = []
        for c in items:
            color = DANGER_C if c.level == "error" else WARN_C
            head = ("[阻断] " if c.level == "error" else "[提示] ") + c.title
            rows.append(ft.Container(
                content=ft.Column([
                    ft.Text(head, size=13.5, weight=ft.FontWeight.BOLD, color=color),
                    ft.Text("原因：" + c.detail, size=12.5, color=MUTED),
                    ft.Text("怎么改：" + c.fix, size=12.5, color=TEXT, selectable=True),
                ], spacing=2),
                padding=8, bgcolor=CARD_2, border_radius=6, margin=ft.margin.only(bottom=6)))
        dlg = ft.AlertDialog(
            title=ft.Text(f"配置检查：{len(errs)} 个阻断 / {len(warns)} 个提示"),
            content=ft.Column(rows, spacing=2, width=620, height=420, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self._show_dlg(dlg)

    def _show_license_block(self, message: str = ""):
        from ..license import machine_code as _mc
        code = _mc()
        st = getattr(self, "license_text", None)
        if st is not None:
            st.value = "未授权"
            st.color = DANGER_C
        dlg = ft.AlertDialog(
            title=ft.Text("未授权，暂时不能开始"),
            content=ft.Column([
                ft.Text("把这台电脑的机器码发给管理员登记后，点「重新检测」即可：", size=13.5, color=TEXT),
                ft.Text(code, size=17.5, weight=ft.FontWeight.BOLD, color=ACCENT_2, selectable=True),
                ft.Text(str(message or "")[:120], size=12.5, color=WARN_C),
            ], spacing=8, width=460),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                     ft.TextButton("复制机器码", on_click=lambda e: self._copy_machine(code)),
                     ft.ElevatedButton("重新检测", bgcolor=ACCENT, color=ON_ACCENT,
                                       on_click=lambda e: (self._close_dlg(dlg),
                                                           threading.Thread(target=self._check_license,
                                                                            daemon=True).start()))])
        self._show_dlg(dlg)

    def _request_stop(self, e=None):
        self._stop_requested = True
        self._abort_now = True
        try:                       # 心跳线程也一起停，否则窗口关了它还在跑
            _hb = getattr(self, "_hb_stop", None)
            if _hb is not None:
                _hb.set()
        except Exception:
            pass
        try:
            if getattr(self, "run_status", None) is not None:
                self.run_status.value = "状态：停止中…（正在中断当前步骤，不会继续保存）"
                self.run_status.color = WARN_C
        except Exception:
            pass
        self._log("已请求停止：当前产品/任务结束后停止", "WARN")
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
        self.toast("已请求停止")
        self._safe_update()

    def _copy_machine(self, code: str = ""):
        from ..license import machine_code as _mc
        code = code or _mc()
        try:
            self.page.set_clipboard(code)
            self.toast("机器码已复制")
        except Exception:
            self.toast(f"请手动复制：{code}", False)

    def _publish_scheduled_now(self, job):
        """把「定时发布队列」里的任务立即执行（手动提前发布）。"""
        self._log(f"手动提前发布：{job.product_id}")
        try:
            self._run_scheduled(job)
            self.toast("定时任务已提前发布")
        except Exception as ex:
            self.toast(f"发布失败：{ex}", False)
        finally:
            try:
                self._switch(10)
            except Exception:
                pass

    def _run_pipeline(self, only_ids: list | None = None):
        """并发处理多个产品；带停止请求与心跳日志，避免看起来“卡死”。

        only_ids: 只跑这些商品 ID（「重跑上次失败」用），并忽略已编辑记录。
        """
        if getattr(self, "_running", False):
            self._log("已有任务在运行，忽略本次启动", "WARN")
            return
        self._running = True
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
        self._stop_requested = False
        # 余额不足：跑之前先提示一次（不阻断，免费步骤照常）
        try:
            _bal = self._fresh_balance()
            if 0 <= _bal < 0.2 and not getattr(self, "_balance_warned", False):
                self._balance_warned = True
                self._log(f"API 余额不足：剩 ¥{_bal:.3f}，AI 图片编辑会被跳过", "WARN")
                self._show_balance_low(_bal)
        except Exception:
            pass
        hb_stop = threading.Event()
        self._hb_stop = hb_stop
        self._abort_now = False
        state = {"done": 0, "current": "", "total": 0}

        def finish(msg: str = ""):
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
            hb_stop.set()
            try:
                if getattr(self, "run_status", None) is not None:
                    self.run_status.value = "状态：" + (msg or "空闲")
                    self.run_status.color = ACCENT_2 if "完成" in (msg or "") else MUTED
            except Exception:
                pass
            try:
                if getattr(self, "ov_progress", None) is not None:
                    self.ov_progress.value = 1
                    self.ov_progress_text.value = msg or "空闲"
                    self._safe_update()
            except Exception:
                pass
            try:
                self._apply_balance_state()
            except Exception:
                pass
            # 每轮结束回传日志尾部（后台，失败静默）
            try:
                from ..core import report as _rep
                _rep.report_async(str(getattr(self.cfg, "license_server", "") or ""),
                                  str(getattr(self, "_report_identity", "") or ""),
                                  version=__import__("aeclone").__version__)
            except Exception:
                pass

        try:
            # 先把「这次会做什么」写进日志，出错时能对照
            try:
                from ..services.preflight import summary as _pf_summary
                for _ln in _pf_summary(self.cfg).splitlines():
                    self._log(_ln, "INFO")
            except Exception:
                pass
            # 启动前冲突检查：把互斥/依赖/无效的组合找出来，ERROR 直接阻断
            try:
                from ..services.conflicts import check as _conf_check, blocking as _conf_block
                _cs = _conf_check(self.cfg)
                _errs = _conf_block(_cs)
                _warns = [c for c in _cs if c.level != "error"]
                for _c in _warns:
                    self._log(f"配置提示：{_c.title} — {_c.fix}", "WARN")
                if _errs:
                    _lines = "\n".join(f"• {c.title}\n  原因：{c.detail}\n  怎么改：{c.fix}" for c in _errs)
                    self._log("配置冲突，已停止启动：\n" + _lines, "ERROR")
                    finish("配置冲突，请先按提示修改")
                    return
            except Exception as _ce:
                self._log(f"冲突检查跳过（{_ce}）", "WARN")

            # 启动前 API 预检：Key 无效/额度不足时直接停，不要整批任务全崩在 "Invalid token"
            why = self._preflight_api()
            if why:
                self._log(f"API 预检未通过：{why}", "ERROR")
                finish("API 预检未通过，已停止")
                return
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            all_prods, _meta = dxm.product_page(1, 100, self.cfg.store_id or None, None)
            blocked = [p for p in all_prods if (p.get("status_type") or "") in ("auditing", "onSelling")]
            self._log(f"共 {len(all_prods)} 个产品，跳过审核中/已发布 {len(blocked)} 个", "INFO")
            limit = int(self.cfg.edit_sum or 0) or 0
            _fetch = (max(limit * 4, 40) if limit else 0)
            if only_ids:
                # 明确指定了商品：直接按 ID 取，避免它们不在 pending 前 N 条里
                _want = {str(x) for x in only_ids}
                _all, _m = dxm.product_page(1, 500, self.cfg.store_id or None, "draft")
                prods = [x for x in _all if str(x.get("id")) in _want]
                self._log(f"按指定商品读取：{len(prods)}/{len(_want)} 个", "INFO")
            else:
                prods = dxm.pending_products(limit=_fetch, shop_id=self.cfg.store_id or None)
            # 标题关键词筛选（留空=全部）
            _kw = str(getattr(self, "run_keyword", "") or "").strip()
            if _kw:
                _low = _kw.lower()
                _hit = [x for x in prods if _low in str(x.get("title") or "").lower()]
                self._log(f"按关键词「{_kw}」筛选：{len(_hit)}/{len(prods)} 个", "INFO")
                if not _hit:
                    try:
                        _hit = dxm.search_products(_kw, self.cfg.store_id or None, "draft", 200)
                        self._log(f"服务端搜索「{_kw}」命中 {len(_hit)} 个", "INFO")
                    except Exception:
                        pass
                prods = _hit
            prods = [x for x in prods if (x.get("status_type") or "") not in
                     ("auditing", "onSelling", "published", "online")]
            _tpl_ids = {str(getattr(self.cfg, "template_product_id", "") or ""),
                        str(getattr(self.cfg, "product_template_id", "") or "")}
            _tpl_ids.discard("")
            if _tpl_ids:
                prods = [x for x in prods if str(x.get("id")) not in _tpl_ids]
            if only_ids:
                _want = {str(x) for x in only_ids}
                prods = [x for x in prods if str(x.get("id")) in _want]
                self._log(f"只重跑指定商品：{len(prods)}/{len(_want)} 个（已忽略「已编辑记录」）", "INFO")
            _skip_edited = 0
            try:
                from ..services import edited as _edited
                if not only_ids:
                    prods, _skip_edited = _edited.filter_new(prods)
                if _skip_edited:
                    self._log(f"跳过已编辑过 {_skip_edited} 个产品", "INFO")
            except Exception:
                pass
            if limit:
                prods = prods[:limit]
            self.products = prods
            if not prods:
                self._log("没有可编辑的产品（采集箱 offline / 草稿 draft 都为空）", "WARN")
                finish("没有待处理产品")
                return
            state["total"] = len(prods)
            workers = max(1, min(int(getattr(self.cfg, "concurrency", 3) or 3), 8, len(prods)))
            self._log(f"本次待处理 {len(prods)} 个产品，并发 {workers}", "INFO")
            try:
                if getattr(self, "ov_progress", None) is not None:
                    self.ov_progress.value = None
                    self.ov_progress_text.value = f"并行处理中（{workers} 个线程）"
                    self._safe_update()
            except Exception:
                pass

            def heartbeat():
                while not hb_stop.wait(15):
                    self._log(f"运行中：已完成 {state['done']}/{state['total']} · 当前 {state['current'] or '-'}", "INFO")

            threading.Thread(target=heartbeat, daemon=True).start()

            ok_n = fail_n = skipped_n = warning_n = 0
            audit_streak = 0
            lock = threading.Lock()
            failed_ids: list[str] = []
            processed_ids: list[str] = []

            def work(item):
                i, prod = item
                nonlocal ok_n, fail_n, skipped_n, audit_streak, warning_n
                if self._stop_requested:
                    self._log(f"[{i}/{len(prods)}] 已停止，跳过 {prod.get('id')}", "WARN")
                    return
                state["current"] = f"{prod.get('title') or prod.get('id')}"[:40]
                self._log(f"[{i}/{len(prods)}] {prod['id']} {(prod.get('title') or '')[:40]}", "INFO")
                try:
                    local_ck = dict(ck)
                    local_dxm = DianxiaomiClient(local_ck, overrides=self.cfg.endpoint_overrides or {})
                    pub = Publisher(self.cfg, dxm=local_dxm, ws_endpoint=self.ws_endpoint,
                                    scheduler=getattr(self, "scheduler", None))
                    # 让「停止」能中断正在处理的图片，而不是等整批跑完
                    pub.should_stop = lambda: bool(getattr(self, "_abort_now", False))
                    if getattr(self, "_abort_now", False):
                        self._log(f"[{i}/{len(prods)}] 已停止，跳过 {prod.get('id')}", "WARN")
                        return
                    res = pub.run(prod, dry_run=False, manage_progress=False)
                    for line in res.log().splitlines():
                        self._log("   " + line, "INFO" if "✓" in line else "WARN")
                    if getattr(res, "stopped", False):
                        self._log(f"[{i}/{len(prods)}] 已停止，未继续保存 {prod.get('id')}", "WARN")
                        return
                    if getattr(res, "skipped_auditing", False):
                        # 审核中的商品不是失败，也不算成功；单独计数，下次再处理
                        with lock:
                            skipped_n += 1
                            audit_streak += 1
                        self._log("   → 商品审核中，已跳过（不计入失败）", "WARN")
                        # 连续 3 个都因「审核中」被拒，说明这家店整体处于审核锁，
                        # 继续跑只会白花时间（AI 已经调用过了），直接停。
                        if audit_streak >= 3 and not self._stop_requested:
                            self._log("   连续 3 个商品都因「站点审核中」被拒 —— "
                                      "这家店当前整体不能保存，已停止本批（避免继续消耗 AI）",
                                      "ERROR")
                            self._stop_requested = True
                    else:
                        with lock:
                            audit_streak = 0
                            ok_n += 1 if res.ok else 0
                            fail_n += 0 if res.ok else 1
                            if res.ok and any(not step.ok for step in res.steps):
                                warning_n += 1
                        if res.ok:
                            try:
                                # 能成功保存说明余额足够，清掉历史“余额不足”缓存
                                if float(getattr(self.cfg, "api_balance", -1) or -1) >= 0:
                                    self.cfg.set("api_balance", -1)
                                    self.cfg.set("api_balance_ts", 0)
                            except Exception:
                                pass
                            _preview = bool(getattr(res, "preview", False))
                            history.record("preview" if _preview else "publish", 1, 0, prod["id"])
                            _result_label = getattr(res, "completion_label", "预演完成（未保存商品）" if _preview else "编辑成功")
                            self._log(f"✓ {_result_label}：{prod['id']} "
                                      f"{(prod.get('title') or '')[:44]}", "WARN" if any(not s.ok for s in res.steps) else "INFO")
                        else:
                            with lock:
                                failed_ids.append(str(prod.get("id", "")))
                            self._log(f"✗ 编辑失败：{prod['id']} "
                                      f"{(prod.get('title') or '')[:44]} · "
                                      f"{str(res.error or '')[:80]}", "WARN")
                except Exception as ex:  # noqa: BLE001
                    with lock:
                        fail_n += 1
                        failed_ids.append(str(prod.get("id", "")))
                    self._log(f"   失败：{ex}", "ERROR")
                finally:
                    with lock:
                        state["done"] += 1
                        processed_ids.append(str(prod.get("id", "")))

            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, list(enumerate(prods, 1))))
            # 断点：记录剩余/失败，供「继续上次」使用
            try:
                from ..services import batch_state as _bs
                _all_ids = [str(x.get("id", "")) for x in prods]
                _left = [x for x in _all_ids if x not in set(processed_ids)]
                if _left or failed_ids:
                    _bs.save(total=len(prods), done=len(processed_ids),
                             remaining=_left, failed=failed_ids,
                             stopped=bool(self._stop_requested))
                    self._log(f"断点已记录：剩余 {len(_left)} 个，失败 {len(failed_ids)} 个", "WARN")
                else:
                    _bs.clear()
            except Exception:
                pass
            # 刊登完成后，按开关创建「删除未引用图片」任务（店小秘侧异步执行，
            # 已被商品引用的图片不会删，所以是安全的清理方式）
            if not self._stop_requested:
                try:
                    from ..services.album_cleanup import maybe_auto_clean
                    _ck2 = self._cookies()
                    _cli = DianxiaomiClient(_ck2, overrides=self.cfg.endpoint_overrides or {})
                    _r = maybe_auto_clean(self.cfg, _cli)
                    # 未开启/已跳过属于正常情况，不要打 WARN 吓人
                    _skipped = bool(_r.get("skipped")) or "未开启" in str(_r.get("message") or "")
                    self._log(f"相册清理：{_r.get('message', '')}",
                              "INFO" if (_r.get("ok") or _skipped) else "WARN")
                except Exception as _ex:  # noqa: BLE001
                    self._log(f"相册清理失败：{str(_ex)[:80]}", "WARN")

            _tail = f" / 审核中跳过 {skipped_n}" if skipped_n else ""
            if warning_n:
                _tail += f" / 已保存但部分步骤未完成 {warning_n}"
            _stop = "（已按请求停止）" if self._stop_requested else ""
            self._log(f"完成：成功 {ok_n} / 失败 {fail_n}{_tail}{_stop}", "INFO")
            try:
                history.record("pipeline", ok_n, fail_n)
            except Exception:
                pass
            finish(f"完成：成功 {ok_n} / 失败 {fail_n}{_tail}")
        except Exception as ex:
            self._log(f"运行异常：{ex}", "ERROR")
            finish("运行异常")
        finally:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
            hb_stop.set()

    def _p_store(self):
        self._current = 1
        ck_ok, ck_text = self._cookie_status_text()

        cnt = getattr(self, "store_counts", {}) or {}

        def status_chip(label, value, color=MUTED):
            # 数字用可变 Text 持有：同步店铺后直接改 value 并刷新，
            # 否则构建时没有数据就会永远显示「—」。
            return ft.Column([
                ft.Text(label, size=12, color=MUTED),
                ft.Text(str(value), size=17.5, color=color, weight=ft.FontWeight.BOLD),
            ], spacing=0)

        self.chip_online = ft.Text(str(cnt.get("onSelling", "—")), size=17.5, color=ACCENT_2,
                                   weight=ft.FontWeight.BOLD)
        self.chip_wait = ft.Text(str(cnt.get("editingRequired", "—")), size=17.5, color=INFO_C,
                                 weight=ft.FontWeight.BOLD)
        self.chip_audit = ft.Text(str(cnt.get("auditing", "—")), size=17.5, color=WARN_C,
                                  weight=ft.FontWeight.BOLD)

        def chip_col(label, ctrl):
            return ft.Column([ft.Text(label, size=12, color=MUTED), ctrl], spacing=0)

        self.shop_dd = ft.Dropdown(label="当前店铺", width=340, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                   options=[ft.dropdown.Option(key=self.cfg.store_id, text=self.cfg.store_name)] if self.cfg.store_id else [],
                                   value=self.cfg.store_id or None,
                                   on_change=lambda e: self._on_shop_change())
        if getattr(self, "tpl_preview_box", None) is None:
            self.tpl_preview_box = ft.Container(content=self._template_preview_row())
        else:
            self.tpl_preview_box.content = self._template_preview_row()
        if getattr(self, "tpl_snapshot_text", None) is None:
            self.tpl_snapshot_text = ft.Text("模板参数：未读取", size=13.5, color=MUTED)
        try:
            import json as _js
            _snap0 = _js.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            _f0 = _snap0.get("fields") or {}
            if _f0:
                self.tpl_snapshot_text.value = (
                    f"模板参数：类目 {_f0.get('category','-')} · 运费 {_f0.get('freight','-')}"
                    f" · 属性 {len(_snap0.get('attributes') or [])} 项 · 重量 {_f0.get('weight','-')}")
                self.tpl_snapshot_text.color = ACCENT_2
        except Exception:
            pass
        self.tpl_source_dd = ft.Dropdown(label="模板来源", width=140, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                         value=self.cfg.template_source or "draft",
                                         options=[ft.dropdown.Option("draft", "待发布"), ft.dropdown.Option("online", "在线")],
                                         on_change=lambda e: self._on_tpl_source())
        self.cookie_status = ft.Text(f"Cookie：{ck_text}",
                                     size=12.5, color=ACCENT_2 if ck_ok else WARN_C,
                                     selectable=True)
        self.store_status = ft.Text("点击「读取并同步」获取店铺与模板", size=13.5, color=MUTED)

        def refresh_cookie(e):
            def go():
                ok, text = self._cookie_status_text(refresh=True)
                self.cookie_status.value = f"Cookie：{text}"
                self.cookie_status.color = ACCENT_2 if ok else WARN_C
                self.toast(("Cookie 可用：" if ok else "Cookie 仍不可用：") + text, ok)
            threading.Thread(target=go, daemon=True).start()

        def sync(e):
            self.store_status.value = "正在读取…"; self._safe_update()
            threading.Thread(target=self._sync_shops, daemon=True).start()

        connect_card = card(ft.Column([
            h1("1 · 连接店小秘", 15),
            muted("自动读取本机 Chrome/Edge 已登录的店小秘；读不到就粘贴一次 Cookie"),
            self.cookie_status,
            ft.Row([ft.ElevatedButton("读取并同步", icon=ft.Icons.SYNC, bgcolor=ACCENT, color=ON_ACCENT, on_click=sync),
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=refresh_cookie),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie())],
                   spacing=10, wrap=True),
            self.store_status,
        ], spacing=10))

        shop_card = card(ft.Column([
            h1("2 · 选择店铺", 15),
            self.shop_dd,
            ft.Row([chip_col("在线", self.chip_online),
                    chip_col("待发布", self.chip_wait),
                    chip_col("审核中", self.chip_audit)], spacing=28),
        ], spacing=12))

        def pick(e):
            self._open_template_picker(self.tpl_source_dd.value,
                                       on_pick=lambda pid, t, imgs: self._set_tpl_preview(imgs))

        # 类目来源：使用模板类目 / 自动分类（用商品自带类目）
        self.cat_mode_dd = ft.Dropdown(
            label="类目来源", width=210, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value="auto" if bool(getattr(self.cfg, "auto_detect_category", True)) else "template",
            options=[ft.dropdown.Option("auto", "自动分类（用商品自带类目）"),
                     ft.dropdown.Option("template", "使用模板类目")],
        )
        def _on_cat_mode(e):
            self.cfg.set("auto_detect_category", e.control.value == "auto")
            save_config(self.cfg)
            self._log(f"类目来源：{'自动分类' if e.control.value == 'auto' else '使用模板类目'}", "INFO")
        self.cat_mode_dd.on_change = _on_cat_mode

        tpl_card = card(ft.Column([
            h1("3 · 选择模板", 15),
            muted("后续产品沿用模板的运费模板、属性、重量尺寸；标题/SKU/图片仍按新生成"),
            ft.Row([self.tpl_source_dd, self.cat_mode_dd,
                    ft.OutlinedButton("类目映射 / 学习", icon=ft.Icons.AUTO_AWESOME_MOTION,
                                      tooltip="配关键词→类目规则，或从你已有的商品里自动学习",
                                      on_click=lambda e: self._category_map_dialog()),
                    ft.ElevatedButton("选择模板产品", icon=ft.Icons.IMAGE_SEARCH, bgcolor=CARD_2, color=TEXT, on_click=pick),
                    ft.ElevatedButton("保存模板到本地", icon=ft.Icons.SAVE, bgcolor=ACCENT_2, color=ON_ACCENT,
                                      on_click=lambda e: threading.Thread(target=self._save_tpl_local_quick, daemon=True).start()),
                    ft.OutlinedButton("模板库", icon=ft.Icons.LIBRARY_BOOKS, on_click=lambda e: self._template_library_dialog())], spacing=10, wrap=True),
            self.tpl_preview_box,
            ft.Row([ft.ElevatedButton("读取模板参数", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=CARD_2, color=TEXT,
                                      on_click=lambda e: threading.Thread(target=self._read_template, daemon=True).start()),
                    self.tpl_snapshot_text], spacing=10),
        ], spacing=10))

        if not getattr(self, "_store_autosynced", False):
            self._store_autosynced = True
            threading.Thread(target=self._sync_shops, daemon=True).start()
        return [page_header("店铺与模板", destination(1).description), connect_card, shop_card, tpl_card]

    def _apply_search(self, notify=True):
        kw = str(getattr(self.search_kw, "value", "") or "").strip()
        self.run_keyword = kw
        if not kw:
            self.search_hint.value = "当前：处理全部采集箱商品"
        else:
            self.search_hint.value = f"当前：只处理标题包含「{kw}」的商品"
        self.search_hint.color = ACCENT_2 if kw else MUTED
        self._safe_update()
        if notify:
            self.toast(f"处理范围：{'全部' if not kw else '标题含「' + kw + '」'}", True)

    def _clear_search(self):
        try:
            self.search_kw.value = ""
        except Exception:
            pass
        self.run_keyword = ""
        self.run_scope_ids = []
        self.search_hint.value = "当前：处理全部采集箱商品"
        self.search_hint.color = MUTED
        self._safe_update()

    def _refresh_store_counts(self):
        """后台拉一次各状态数量并刷新店铺卡片上的三个数字。"""
        def go():
            try:
                c = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                sid = self.cfg.store_id or None
                cnt = c.counts(sid, ttl=30) if sid else {}
                if not cnt:
                    return
                self.store_counts = cnt
                try:
                    if getattr(self, "ov_tiles_ts", None) is not None:
                        self.ov_tiles_ts.value = "更新于 " + time.strftime("%H:%M:%S")
                except Exception:
                    pass
                for _k, _t in (getattr(self, "ov_tiles", {}) or {}).items():
                    try:
                        _t.value = str(cnt.get(_k, "—"))
                    except Exception:
                        pass
                try:
                    self.chip_online.value = str(cnt.get("onSelling", 0))
                    self.chip_wait.value = str(cnt.get("editingRequired", 0))
                    self.chip_audit.value = str(cnt.get("auditing", 0))
                except Exception:
                    pass
                self._safe_update()
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()

    def _on_shop_change(self):
        sid_now = str(getattr(self, "shop_dd", None).value if getattr(self, "shop_dd", None) else "")
        sid_saved = str(getattr(self.cfg, "store_id", "") or "")
        # 只有「有旧店铺」AND「换了不同店铺」才清模板，避免初始化时误清
        if not sid_now or not sid_saved or sid_now == sid_saved:
            # 店铺未变，仍需同步 store_id/name（避免漏存）
            if sid_now and sid_now != sid_saved:
                pass  # 走下面的清模板流程
            else:
                return
        self.cfg.template_product_id = ""
        self.cfg.template_product_title = ""
        self.cfg.template_snapshot = ""
        try:
            self._refresh_store_counts()
        except Exception:
            pass
        save_config(self.cfg)
        self.tpl_preview_box.content = self._template_preview_row()
        self.tpl_snapshot_text.value = "模板参数：未读取（已切换店铺，请重新选择模板）"
        self.toast("已切换店铺，请重新选择模板")
        sid = self.shop_dd.value
        name = next((o.text for o in self.shop_dd.options if o.key == sid), "")
        self.cfg.store_id = str(sid or "")
        self.cfg.store_name = name or self.cfg.store_name
        self.store_shortcut.text = self.cfg.store_name or "选择店铺"
        save_config(self.cfg)
        threading.Thread(target=self._sync_shops, daemon=True).start()

    def _on_tpl_source(self):
        self.cfg.template_source = self.tpl_source_dd.value
        save_config(self.cfg)
        self._open_template_picker(self.tpl_source_dd.value,
                                   on_pick=lambda pid, t, imgs: self._set_tpl_preview(imgs))

    # ---------- 对接动作 ----------
    def _template_library_dialog(self):
        import json as _j
        lib = _tpl_lib_load(self.cfg.template_library)
        list_col = ft.Column(spacing=4, height=280, scroll=ft.ScrollMode.AUTO)

        def persist():
            self.cfg.template_library = _tpl_lib_dump(lib)
            save_config(self.cfg)
            render()

        def save_template_local(name, item):
            """把当前模板（含抓取到的表单值）保存到本地模板库，读取在后台线程完成。"""
            try:
                vals = {}
                try:
                    import json as _jv
                    vals = _jv.loads(getattr(self.cfg, "template_values", "") or "{}")
                except Exception:
                    vals = {}
                rec = dict(item)
                rec["name"] = name or self.cfg.template_product_title or "未命名模板"
                rec["values"] = vals
                lib[rec["name"]] = rec
                self.cfg.template_library = _tpl_lib_dump(lib)
                save_config(self.cfg)
                render()
                self.toast(f"模板已保存到本地：{rec['name']}（{len(vals)} 个字段）")
            except Exception as ex:
                self.toast(f"保存失败：{ex}", False)

        def apply_item(name, item):
            # 先在本地立即生效（不阻塞界面），表单值抓取放后台
            self.cfg.template_snapshot = _j.dumps({"fields": item.get("fields") or {},
                                                   "attributes": item.get("attributes") or []},
                                                  ensure_ascii=False)
            self.cfg.template_product_title = name
            self.cfg.template_product_id = item.get("product_id") or ""
            save_config(self.cfg)
            self._set_tpl_preview(None)
            f = (item.get("fields") or {})
            if getattr(self, "tpl_snapshot_text", None) is not None:
                self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                                f" · 属性 {len(item.get('attributes') or [])} 项")
            self._close_dlg(dlg)
            self.toast(f"已应用模板：{name}")
            self._safe_update()
            try:
                threading.Thread(target=save_template_local, args=(name, item), daemon=True).start()
            except Exception:
                pass

            def fetch_values():
                try:
                    dxm = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    snap = dxm.template_snapshot(
                        str(item.get("product_id") or self.cfg.template_product_id))
                    vals = (snap or {}).get("values") or {}
                    if vals:
                        self.cfg.template_values = _j.dumps(vals, ensure_ascii=False)
                        save_config(self.cfg)
                        self.toast(f"已抓取模板 {len(vals)} 个字段值")
                except Exception as ex:  # noqa: BLE001
                    self._log(f"模板值抓取失败：{ex}", "WARN")

            threading.Thread(target=fetch_values, daemon=True).start()

        def rename_item(name, item):
            field = ft.TextField(label="新名称", value=name, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def do(e):
                nn = (field.value or "").strip()
                if nn and nn != name:
                    lib[nn] = lib.pop(name, item)
                    persist()
                    self.toast(f"已重命名为 {nn}")
                self._close_dlg(d2)

            d2 = ft.AlertDialog(title=ft.Text("重命名模板"), content=field,
                                actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(d2)),
                                         ft.ElevatedButton("确定", on_click=do)])
            self._show_dlg(d2)

        def delete_item(name):
            lib.pop(name, None)
            persist()
            self.toast(f"已删除 {name}")

        def render():
            rows = []
            for name, item in lib.items():
                rows.append(ft.Row([
                    ft.Text(name[:28], size=13.5, color=TEXT, expand=True),
                    ft.TextButton("应用", on_click=lambda e, n=name, i=item: apply_item(n, i)),
                    ft.TextButton("重命名", on_click=lambda e, n=name, i=item: rename_item(n, i)),
                    ft.TextButton("删除", on_click=lambda e, n=name: delete_item(n)),
                ], spacing=6))
            list_col.controls = rows or [ft.Text("模板库为空，先点「保存当前模板」", size=13.5, color=MUTED)]
            self._safe_update()

        def save_current(e):
            try:
                snap = _j.loads(self.cfg.template_snapshot or "{}")
            except Exception:
                snap = {}
            if not snap.get("fields"):
                self.toast("当前没有可保存的模板参数，请先读取模板", False); return
            name = (self.cfg.template_product_title or "未命名模板")[:40]
            lib[name] = {"fields": snap.get("fields"), "attributes": snap.get("attributes"),
                         "product_id": self.cfg.template_product_id}
            persist()
            self.toast(f"已保存模板：{name}")

        dlg = ft.AlertDialog(
            title=ft.Text("模板库"),
            content=ft.Column([ft.ElevatedButton("保存当前模板", icon=ft.Icons.SAVE, on_click=save_current),
                               ft.Divider(color=CARD_2), list_col], spacing=8, width=560),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self._show_dlg(dlg)
        render()

    def _sku_replace_dialog(self):
        """SKU 图替换：从模板商品读取真实 SKU 名 + 图片缩略图预览。"""
        import json as _j
        try:
            mapping = _j.loads(self.cfg.sku_image_replace or "{}")
        except Exception:
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}

        rows = ft.Column(spacing=6, height=320, scroll=ft.ScrollMode.AUTO)
        pairs: list[tuple] = []

        def add(sku="", url=""):
            a = ft.TextField(value=sku, hint_text="SKU 名（点「读取模板 SKU」自动填）",
                             width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            b = ft.TextField(value=url, hint_text="新图片 URL", expand=True,
                             bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            thumb = ft.Image(src=(url or "").strip(), width=40, height=40,
                             fit=ft.ImageFit.COVER, border_radius=6,
                             visible=bool((url or "").strip()))

            def on_url(e, _t=thumb):
                u = (e.control.value or "").strip()
                _t.src = u
                _t.visible = bool(u)
                try:
                    self._safe_update()
                except Exception:
                    pass

            b.on_change = on_url
            rows.controls.append(ft.Row([a, b, thumb], spacing=8,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER))
            pairs.append((a, b))
            try:
                self._safe_update()
            except Exception:
                pass

        for k, v in mapping.items():
            add(k, v)

        def load_from_template(e):
            """拉模板商品的真实 SKU 名，避免手打拼错导致替换不生效。"""
            try:
                ck = self._cookies()
                if not ck:
                    self.toast("请先在「店铺设置」读取 Cookie", False)
                    return
                from ..integrations.dianxiaomi import DianxiaomiClient
                dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                pid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                if not pid:
                    self.toast("请先在「店铺设置」选模板商品", False)
                    return
                d = dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
                skus = _j.loads(d.get("aeopAeProductSKUs") or "[]")
                have = {(x[0].value or "").strip() for x in pairs}
                added = 0
                for it in skus:
                    nm = str(it.get("skuCode") or it.get("sku") or "").strip()
                    if nm and nm not in have:
                        add(nm, "")
                        have.add(nm)
                        added += 1
                self.toast(f"已从模板读取 {added} 个 SKU" if added else "模板商品没有可用的 SKU 名",
                           bool(added))
            except Exception as ex:  # noqa: BLE001
                self.toast(f"读取失败：{str(ex)[:80]}", False)

        def clear_all(e):
            rows.controls = []
            pairs.clear()
            try:
                self._safe_update()
            except Exception:
                pass

        def save(e):
            out = {}
            for a, b in pairs:
                if (a.value or "").strip():
                    out[a.value.strip()] = (b.value or "").strip()
            self.cfg.sku_image_replace = _j.dumps(out, ensure_ascii=False)
            save_config(self.cfg)
            self.toast(f"已保存 {len(out)} 条 SKU 图替换")
            self._close_dlg(dlg)

        dlg = ft.AlertDialog(
            title=ft.Text("SKU 图替换"),
            content=ft.Column([
                ft.Text("这里是「按 SKU 名精确匹配」的覆盖表：只影响名字完全一致的 SKU。"
                        "建议先点「读取模板 SKU」再填图，避免拼错。",
                        size=12.5, color=MUTED),
                ft.Row([
                    ft.ElevatedButton("读取模板 SKU", icon=ft.Icons.DOWNLOAD,
                                      bgcolor=ACCENT_2, color=ON_ACCENT,
                                      on_click=load_from_template),
                    ft.ElevatedButton("新增一行", icon=ft.Icons.ADD,
                                      bgcolor=CARD_2, color=TEXT, on_click=lambda e: add()),
                    ft.TextButton("清空", icon=ft.Icons.CLEAR_ALL, on_click=clear_all),
                ], spacing=8, wrap=True),
                rows,
            ], spacing=10, width=680),
            actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("保存", bgcolor=ACCENT, color=ON_ACCENT, on_click=save)],
        )
        self._show_dlg(dlg)

    def _paste_cookie(self):
        field = ft.TextField(label="Cookie", multiline=True, min_lines=4, bgcolor=CARD_2,
                             border_color=CARD_2, color=TEXT,
                             hint_text="从 Chrome 开发者工具 Network → 任意请求 → Request Headers → Cookie 复制整行")

        def save(e):
            raw = (field.value or "").strip()
            ck = {}
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    ck[k.strip()] = v.strip()
            if not ck:
                self.toast("Cookie 解析为空", False); return
            self.cfg.manual_cookies = ck
            save_config(self.cfg)
            self._cookie_cache = None      # 刚粘贴的 Cookie 立刻生效
            self._cookie_ts = 0.0
            self.toast(f"已保存 {len(ck)} 个 Cookie")
            self._close_dlg(dlg)
            threading.Thread(target=self._sync_shops, daemon=True).start()

        dlg = ft.AlertDialog(title=ft.Text("手动粘贴 Cookie"),
                             content=ft.Column([field], width=560, height=200),
                             actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(dlg)),
                                      ft.ElevatedButton("保存并同步", bgcolor=ACCENT, color=ON_ACCENT, on_click=save)])
        self._show_dlg(dlg)

    def _set_tpl_preview(self, images=None):
        self.tpl_preview_box.content = self._template_preview_row(images)
        try:
            self._safe_update()
        except Exception:
            pass

    def _read_template(self):
        """读取模板参数：优先走店小秘 API（不用打开浏览器），失败才用已连接的浏览器。"""
        import json as _j
        pid = self.cfg.template_product_id or self.cfg.product_template_id
        if not pid:
            self.toast("请先选择模板产品", False); return
        self.tpl_snapshot_text.value = "正在读取模板参数…"
        self._safe_update()
        snap = None
        # 1) API 读取（不依赖浏览器）
        try:
            dxm = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
            snap = dxm.template_snapshot(str(pid))
            if snap and snap.get("fields"):
                self.cfg.template_product_id = str(pid)
                if snap.get("title"):
                    self.cfg.template_product_title = snap["title"]
                self.cfg.template_snapshot = _j.dumps({"fields": snap.get("fields") or {},
                                                       "attributes": snap.get("attributes") or []},
                                                      ensure_ascii=False)
                if snap.get("values"):
                    self.cfg.template_values = _j.dumps(snap["values"], ensure_ascii=False)
                # 预览首图：老版本没有存过，就从详情接口补一张
                try:
                    if not str(getattr(self.cfg, "template_image_url", "") or "").strip():
                        _d = dxm._get("product_detail", {"id": str(pid)}).get("data", {}).get("product", {})
                        _u = str(_d.get("imageURLs") or "").split(";")
                        _u = [x for x in _u if x.strip()]
                        if _u:
                            self.cfg.template_image_url = _u[0]
                except Exception:
                    pass
                save_config(self.cfg)
                f = snap["fields"]
                self.tpl_snapshot_text.value = (f"模板参数：类目 {f.get('category','-')} · 运费 {f.get('freight','-')}"
                                                f" · 属性 {len(snap.get('attributes') or [])} 项 · 重量 {f.get('weight','-')}")
                self.tpl_snapshot_text.color = ACCENT_2
                self._set_tpl_preview()
                self._log(f"模板参数已读取（API）：{pid}")
                self._safe_update()
                return
        except Exception as ex:  # noqa: BLE001
            self.tpl_snapshot_text.value = f"模板参数读取失败：{str(ex)[:80]}"
            self.tpl_snapshot_text.color = DANGER_C
            self._log(f"模板参数读取失败（API）：{str(ex)[:80]}", "WARN")

    def _sync_shops(self):
        try:
            ck = self._cookies()
            c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            shops = c.shops()
            self.shop_dd.options = [ft.dropdown.Option(key=str(s["id"]), text=s["name"]) for s in shops]
            if shops and not self.cfg.store_id:
                self.cfg.store_id = shops[0]["id"]; self.cfg.store_name = shops[0]["name"]
                save_config(self.cfg)
            if shops:
                self.shop_dd.value = str(self.cfg.store_id or shops[0]["id"])
            sid = self.cfg.store_id or (shops[0]["id"] if shops else None)
            # 名称以店小秘返回为准（本地配置可能残留旧店名）
            for _sh in shops:
                if str(_sh.get("id")) == str(sid) and _sh.get("name"):
                    self.cfg.store_name = str(_sh["name"])
                    break
            save_config(self.cfg)
            cnt = c.counts(sid, ttl=30) if sid else {}
            self.store_counts = cnt
            try:
                self.chip_online.value = str(cnt.get("onSelling", 0))
                self.chip_wait.value = str(cnt.get("editingRequired", 0))
                self.chip_audit.value = str(cnt.get("auditing", 0))
            except Exception:
                pass
            self.store_status.value = (f"店铺 {self.cfg.store_name}：在线 {cnt.get('onSelling',0)} · "
                                       f"待发布 {cnt.get('editingRequired',0)} · 审核中 {cnt.get('auditing',0)}")
            self.store_status.color = ACCENT_2
        except Exception as ex:
            self.store_status.value = f"同步失败：{ex}"
            self.store_status.color = DANGER_C
        self._safe_update()

    def _cookies(self, refresh: bool = False):
        """取 Cookie：手动配置 → 已开浏览器（CDP）→ 本机浏览器库（ISSUES #1）。"""
        from ..integrations.browser_cookies import resolve
        cache = getattr(self, "_cookie_cache", None)
        if (not refresh and cache and cache[0] == self.ws_endpoint
                and (time.time() - cache[1]) < 20):
            return dict(cache[2])
        src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None),
                      ws_endpoint=self.ws_endpoint)
        self.cookie_src = src
        self._cookie_ts = time.time()
        if src.ok and src.cookies:
            self._cookie_cache = (self.ws_endpoint, time.time(), dict(src.cookies))
            return dict(src.cookies)
        raise RuntimeError("未读取到店小秘 Cookie：请点「粘贴 Cookie」，或点「启动浏览器（兜底）」登录一次")

    def _cookie_status_text(self, refresh: bool = False, allow_cdp: bool = False) -> tuple[bool, str]:
        """给界面显示的 Cookie 状态。

        默认**不读本机 Cookie 库**（macOS 解密可能弹 Keychain，导致界面像卡死），
        只用手动配置/缓存；点「重新检测」时才在后台线程真正读取。
        """
        from ..integrations.browser_cookies import describe, resolve
        cached = getattr(self, "cookie_src", None)
        if not refresh:
            if cached is not None and (time.time() - getattr(self, "_cookie_ts", 0)) < 60:
                return bool(cached.ok and cached.cookies), describe(cached)
            manual = getattr(self.cfg, "manual_cookies", None)
            if isinstance(manual, dict) and manual:
                src = resolve("dianxiaomi.com", manual=manual, ws_endpoint="", use_local=False)
                self.cookie_src = src
                self._cookie_ts = time.time()
                return True, describe(src)
            return False, "未检测（点「重新检测 Cookie」）"
        try:
            src = resolve("dianxiaomi.com", manual=getattr(self.cfg, "manual_cookies", None),
                          ws_endpoint=self.ws_endpoint if allow_cdp else "")
        except Exception as ex:  # noqa: BLE001
            return False, f"读取异常：{str(ex)[:60]}"
        self.cookie_src = src
        self._cookie_ts = time.time()
        return bool(src.ok and src.cookies), describe(src)

    def _show_dlg(self, dlg) -> None:
        """统一弹窗入口：记录当前弹窗 + 交给 Flet 管理弹窗栈。

        - 记录 self._dlg 后，_safe_update() 会在 page.update 之后把弹窗重新置顶，
          避免后台线程/分页刷新时「弹窗一闪就没了」。
        - 注意：这里不能递归调自己（以前 show_dialog 失败会无限递归）。
        """
        self._dlg = dlg
        try:
            stack = getattr(self, "_dlg_stack", None)
            if stack is None:
                stack = []
                self._dlg_stack = stack
            if dlg not in stack:
                stack.append(dlg)
        except Exception:
            pass
        shown = False
        try:
            self.page.show_dialog(dlg)
            shown = True          # show_dialog 自己会推一次更新，别再 update 造成闪烁
        except Exception:
            try:
                self.page.dialog = dlg
                self.page.update()
                shown = True
            except Exception:
                pass
        if not shown:
            try:
                self._safe_update()
            except Exception:
                pass

    def _close_dlg(self, dlg):
        try:
            if dlg is None:
                return
            _stack = getattr(self, "_dlg_stack", None) or []
            if not bool(getattr(dlg, "open", False)) and dlg not in _stack:
                return
        except Exception:
            pass
        try:
            self.page.close_dialog()
        except Exception:
            try:
                dlg.open = False
            except Exception:
                pass
        try:
            stack = getattr(self, "_dlg_stack", None) or []
            if dlg in stack:
                stack.remove(dlg)
            self._dlg = stack[-1] if stack else None
        except Exception:
            pass
        self._safe_update()

    def _open_template_picker(self, source: str, on_pick=None):
        """先弹窗、后台拉数据，避免网络慢导致界面“卡死”。"""
        state = {"source": source if source in ("draft", "online") else "draft",
                 "kw": "", "data": {}, "totals": {}, "err": ""}
        list_col = ft.Column(spacing=2, height=380, scroll=ft.ScrollMode.AUTO)
        search = ft.TextField(hint_text="搜索标题或产品ID（在当前已加载的列表里过滤）", prefix_icon=ft.Icons.SEARCH, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        tab_row = ft.Row(spacing=8)
        status = ft.Text("正在读取模板产品…", size=12.5, color=MUTED)
        PAGE = 20
        state.update({"sort": "new", "page": 1, "filtered": []})
        dlg_holder: dict = {}
        sort_dd = ft.Dropdown(
            value="new", width=150, dense=True, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            options=[ft.dropdown.Option("new", "最新优先"),
                     ft.dropdown.Option("old", "最早优先"),
                     ft.dropdown.Option("az", "标题 A→Z"),
                     ft.dropdown.Option("za", "标题 Z→A")])
        page_info = ft.Text("", size=12.5, color=MUTED)
        prev_btn = ft.OutlinedButton("上一页", on_click=lambda e: _go(-1))
        next_btn = ft.OutlinedButton("下一页", on_click=lambda e: _go(1))

        def _go(delta):
            n = max(1, (len(state.get("filtered") or []) + PAGE - 1) // PAGE)
            state["page"] = min(max(1, state["page"] + delta), n)
            render()

        def _on_sort(e):
            state["sort"] = e.control.value or "new"
            state["page"] = 1
            render()

        def pick(pid, title, images):
            self.cfg.template_product_id = str(pid)
            self.cfg.template_product_title = title
            self.cfg.template_source = state["source"]
            try:
                _imgs0 = [str(x) for x in (images or []) if str(x or "").strip()]
                self.cfg.template_image_url = _imgs0[0] if _imgs0 else ""
            except Exception:
                self.cfg.template_image_url = ""
            save_config(self.cfg)
            self.toast(f"已选模板：{title[:24]}")
            self._close_dlg(dlg)
            if on_pick:
                on_pick(pid, title, images)
            threading.Thread(target=self._read_template, daemon=True).start()

        def render():
            items = list((state["data"] or {}).get(state["source"], []) or [])
            kw = (state["kw"] or "").strip().lower()
            if kw:
                items = [x for x in items
                         if kw in str(x.get("title") or "").lower() or kw in str(x.get("id"))]
            # 排序
            mode = state.get("sort") or "new"
            if mode in ("new", "old"):
                try:
                    items.sort(key=lambda x: int(str(x.get("id") or 0)), reverse=(mode == "new"))
                except Exception:
                    items.sort(key=lambda x: str(x.get("id") or ""), reverse=(mode == "new"))
            elif mode in ("az", "za"):
                items.sort(key=lambda x: str(x.get("title") or "").lower(), reverse=(mode == "za"))
            state["filtered"] = items
            total_pages = max(1, (len(items) + PAGE - 1) // PAGE)
            state["page"] = min(max(1, state["page"]), total_pages)
            start = (state["page"] - 1) * PAGE
            rows = []
            for it in items[start:start + PAGE]:
                imgs = it.get("images") or []
                thumb = imgs[0] if imgs else None
                rows.append(ft.ListTile(
                    leading=self._zoom_img(thumb, 44, 44, "模板主图", gallery=imgs),
                    title=ft.Text((it.get("title") or it["id"])[:60], size=13.5, color=TEXT, max_lines=2),
                    subtitle=ft.Text(f"{it['id']} · {it.get('status_type')}", size=12, color=MUTED),
                    dense=True, on_click=lambda e, x=it: pick(x["id"], x.get("title", ""), x.get("images") or []),
                ))
            list_col.controls = rows or [ft.Text("无匹配结果" if state["data"] else "读取中…", size=13.5, color=MUTED)]
            page_info.value = f"第 {state['page']}/{total_pages} 页 · 共 {len(items)} 个"
            prev_btn.disabled = state["page"] <= 1
            next_btn.disabled = state["page"] >= total_pages
            sort_dd.value = mode
            data = state["data"] or {}
            totals = state["totals"] or {}
            tab_row.controls = [
                ft.ElevatedButton(f"待发布 {totals.get('editingRequired', len(data.get('draft') or []))}",
                                  bgcolor=ACCENT_2 if state["source"] == "draft" else CARD_2,
                                  color=ON_ACCENT if state["source"] == "draft" else TEXT,
                                  on_click=lambda e: switch("draft")),
                ft.ElevatedButton(f"在线 {totals.get('onSelling', len(data.get('online') or []))}",
                                  bgcolor=ACCENT_2 if state["source"] == "online" else CARD_2,
                                  color=ON_ACCENT if state["source"] == "online" else TEXT,
                                  on_click=lambda e: switch("online")),
            ]
            try:
                dlg_holder["dlg"].update()
            except Exception:
                try:
                    self._safe_update()
                except Exception:
                    pass

        def switch(src):
            state["source"] = src
            self.cfg.template_source = src
            save_config(self.cfg)
            render()

        def on_search(e):
            state["kw"] = e.control.value or ""
            state["page"] = 1
            render()
            kw = (state["kw"] or "").strip()
            if len(kw) < 2:
                return
            # 服务端搜索：商品几千条时也不必先全部拉下来
            state["seq"] = int(state.get("seq") or 0) + 1
            seq = state["seq"]

            def work():
                try:
                    c = DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    st = "draft" if state["source"] == "draft" else "onSelling"
                    hits = c.search_products(kw, self.cfg.store_id or None, st, 200)
                    if seq != state.get("seq"):
                        return
                    state["data"][state["source"]] = hits
                    state["page"] = 1
                    status.value = f"服务端搜索「{kw}」：{len(hits)} 个"
                    status.color = ACCENT_2
                    render()
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()

        search.on_change = on_search
        sort_dd.on_change = _on_sort

        dlg = ft.AlertDialog(
            title=ft.Text("选择模板产品"),
            content=ft.Column([tab_row, search,
                               ft.Row([sort_dd, prev_btn, next_btn, page_info],
                                      spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                               status, list_col], spacing=10, width=660),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        dlg_holder["dlg"] = dlg
        self._show_dlg(dlg)
        render()

        def load():
            try:
                ck = self._cookies()
                c = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                sid = self.cfg.store_id or None
                state["data"] = {"draft": c.drafts(limit=800, shop_id=sid),
                                 "online": c.online(limit=800, shop_id=sid)}
                state["totals"] = c.counts(sid) if sid else {}
                _n1 = len(state["data"]["draft"]); _n2 = len(state["data"]["online"])
                status.value = f"读取完成：待发布 {_n1} 条 / 在线 {_n2} 条（超过 800 条的请用搜索过滤）"
                status.color = ACCENT_2
            except Exception as ex:  # noqa: BLE001
                state["data"] = {}
                status.value = f"读取失败：{str(ex)[:100]}"
                status.color = DANGER_C
            render()

        threading.Thread(target=load, daemon=True).start()

    def _template_preview_row(self, images=None):
        """模板预览：标题 + 类目/运费/品牌/属性数/抓取字段数（ISSUES #2）。"""
        import json as _j
        _imgs = [x for x in (images or []) if str(x or "").strip()]
        thumb = _imgs[0] if _imgs else (str(getattr(self.cfg, "template_image_url", "") or "") or None)
        title = self.cfg.template_product_title or "未选择模板"
        f: dict = {}
        attrs: list = []
        try:
            snap = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            if isinstance(snap, dict):
                f = snap.get("fields") or {}
                attrs = snap.get("attributes") or []
        except Exception:
            f, attrs = {}, []
        vals: dict = {}
        try:
            vals = _j.loads(getattr(self.cfg, "template_values", "") or "{}")
        except Exception:
            vals = {}
        if not isinstance(vals, dict):
            vals = {}

        def _pick(*keys):
            for k in keys:
                v = (f or {}).get(k)
                if v not in (None, "", "None", "NONE", "-"):
                    return str(v)
                item = vals.get(k)
                if isinstance(item, dict) and item.get("v"):
                    return str(item.get("v"))
                if isinstance(item, str) and item:
                    return item
            return ""

        brand = _pick("brand", "品牌")
        origin = _pick("origin", "产地")
        chips = [
            ("类目", _pick("category", "类目") or "-", INFO_C),
            ("运费", _pick("freight", "运费模板") or "-", INFO_C),
            ("品牌", brand or "-", ACCENT_2),
            ("产地", origin or "-", MUTED),
            ("属性", f"{len(attrs)} 项", WARN_C),
            ("抓取字段", f"{len(vals)} 个", ACCENT_2 if vals else MUTED),
        ]
        chip_row = ft.Row([
            ft.Container(
                content=ft.Row([ft.Text(f"{label} ", size=12, color=MUTED),
                                ft.Text(str(val)[:24], size=12, color=color, weight=ft.FontWeight.BOLD)],
                               spacing=2, tight=True),
                bgcolor=CARD_2, border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4))
            for label, val, color in chips], spacing=6, wrap=True)
        return ft.Column([
            ft.Row([
                self._zoom_img(thumb, 44, 44, "模板主图"),
                ft.Column([ft.Text(title, size=13.5, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                           muted(f"模板产品 ID：{self.cfg.template_product_id or '-'}", 10)], spacing=2),
            ], spacing=10),
            chip_row,
        ], spacing=8)

    def _search_attr_templates(self):
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            items = client.attribute_templates((getattr(self.cfg, 'shop_id', '') or self.cfg.store_id or None), (self.attr_kw.value or "").strip())
            rows = []
            for t in items[:40]:
                name = t.get("name") or t.get("templateName") or str(t.get("id", ""))
                tid = str(t.get("id", ""))
                attrs = t.get("attributes") or t.get("attrList") or t.get("data") or []
                pairs = []
                if isinstance(attrs, dict):
                    pairs = [f"{k}={v}" for k, v in attrs.items()]
                elif isinstance(attrs, list):
                    for a in attrs:
                        if isinstance(a, dict):
                            k = a.get("name") or a.get("attrName") or ""
                            v = a.get("value") or a.get("attrValue") or ""
                            if k:
                                pairs.append(f"{k}={v}")
                        elif isinstance(a, str) and "=" in a:
                            pairs.append(a)
                elif isinstance(attrs, str) and "=" in attrs:
                    pairs = [x for x in attrs.split("\n") if "=" in x]

                def apply(e, _tid=tid, _name=name, _pairs=pairs):
                    self.cfg.attribute_template_id = _tid
                    self.cfg.custom_attributes = _pairs
                    save_config(self.cfg)
                    self.attr_status.value = f"已应用 {_name}（{len(_pairs)} 条属性）"
                    self.toast(f"已应用属性模板：{_name}")

                rows.append(ft.ListTile(
                    title=ft.Text(name, size=14.5, color=TEXT),
                    subtitle=ft.Text(f"{tid} · {len(pairs)} 条属性", size=12, color=MUTED),
                    on_click=apply))
            self.attr_list.controls = rows or [ft.Text("没有属性模板（可先在店小秘创建）", size=13.5, color=WARN_C)]
            self.attr_status.value = f"共 {len(rows)} 个模板"
        except Exception as ex:
            self.attr_status.value = f"搜索失败：{ex}"
            self.attr_list.controls = []
        try:
            self._safe_update()
        except Exception:
            pass

    def _clear_attrs(self):
        self.cfg.custom_attributes = []
        self.cfg.attribute_template_id = ""
        save_config(self.cfg)
        self.attr_status.value = "已清空"
        self.toast("已清空自定义属性")

    def _save_tpl_local_quick(self):
        """把当前使用的模板（快照 + 表单值）存到本地模板库；读取全部在后台。"""
        try:
            import json as _j
            try:
                _snap_chk = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
            except Exception:
                _snap_chk = {}
            if not (_snap_chk.get("fields") or _snap_chk.get("attributes")):
                self.toast("当前没有模板参数可保存：请先选模板产品并读取参数", False)
                return
            name = self.cfg.template_product_title or "未命名模板"
            lib = _tpl_lib_load(self.cfg.template_library)
            rec = {"name": name, "product_id": self.cfg.template_product_id or "",
                   "fields": {}, "attributes": [], "values": {}}
            try:
                snap = _j.loads(self.cfg.template_snapshot or "{}")
                rec["fields"] = snap.get("fields") or {}
                rec["attributes"] = snap.get("attributes") or []
            except Exception:
                pass
            try:
                rec["values"] = _j.loads(self.cfg.template_values or "{}")
            except Exception:
                rec["values"] = {}
            lib[name] = rec
            self.cfg.template_library = _tpl_lib_dump(lib)
            save_config(self.cfg)
            self.toast(f"已保存到本地：{name}（{len(rec['values'])} 字段 / {len(rec['attributes'])} 属性）")
        except Exception as ex:
            self.toast(f"保存失败：{ex}", False)

    def _close_dialog(self):
        """关闭 self.dlg（兼容 overlay 与 show_dialog 两种打开方式）。"""
        dlg = getattr(self, "dlg", None)
        if dlg is None:
            return
        try:
            self._close_dlg(dlg)
        except Exception:
            try:
                self._close_dlg(dlg)
            except Exception:
                pass

    # ---------- 图片空间 / 清理旧图（ISSUES #5）----------
    def _album_client(self):
        return DianxiaomiClient(self._cookies(), overrides=self.cfg.endpoint_overrides or {})

    def _open_album_page(self):
        url = "https://www.dianxiaomi.com/web/service/album"
        try:
            self.page.launch_url(url)
        except Exception:
            self.toast(f"请在浏览器打开 {url}", False)

    def _open_cleanup_dialog(self):
        from ..services import album_cleanup

        def fmt_time(v) -> str:
            try:
                n = float(v)
                if n > 1e12:
                    n /= 1000.0
                return time.strftime("%Y-%m-%d", time.localtime(n)) if n else "-"
            except Exception:
                return str(v or "-")[:10]

        state = {"plan": None, "sel": set(), "timer": None}
        space_text = ft.Text("图片空间：读取中…", size=13.5, color=MUTED)
        days = ft.TextField(label="删除多少天前的图片（0=今天之前全部）", value="30", width=260,
                            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        status = ft.Text("先点「扫描」看看有哪些旧图", size=13.5, color=MUTED)
        result = ft.Column(spacing=2, height=280, scroll=ft.ScrollMode.AUTO)
        select_all = ft.Checkbox(label="全选可删", value=False)
        dlg = None

        def load_space():
            try:
                info = album_cleanup.space(self._album_client())
                space_text.value = "图片空间：" + info.brief
                space_text.color = ACCENT_2
            except Exception as ex:  # noqa: BLE001
                space_text.value = f"图片空间：读取失败（{str(ex)[:60]}）"
                space_text.color = DANGER_C
            self._safe_update()

        def render():
            state["rendering"] = True
            plan = state["plan"]
            if not plan:
                result.controls = []
                state["rendering"] = False
                return
            rows = []
            unlocked = [c for c in plan.candidates if not c.locked]
            for c in plan.candidates[:120]:
                cb = ft.Checkbox(value=(c.id in state["sel"]), disabled=c.locked, width=36)
                if not c.locked:
                    def on_cb(e, _c=c):
                        if e.control.value:
                            state["sel"].add(_c.id)
                        else:
                            state["sel"].discard(_c.id)
                        self._safe_update()
                    cb.on_change = on_cb
                rows.append(ft.Row([
                    cb,
                    ft.Text(fmt_time(c.created), size=12, color=MUTED, width=90),
                    ft.Text(album_cleanup.human_size(c.size), size=12, color=MUTED, width=60),
                    ft.Text("🔒" if c.locked else "", size=12, color=WARN_C, width=20),
                    ft.Text((c.name or c.id)[:52], size=12, color=TEXT, expand=True),
                ], spacing=6))
            result.controls = rows
            select_all.value = bool(unlocked) and all(c.id in state["sel"] for c in unlocked)
            state["rendering"] = False
            self._safe_update()

        def scan_now():
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                status.value = "天数要填数字"; status.color = DANGER_C; self._safe_update(); return
            status.value = "扫描中…"; status.color = MUTED; self._safe_update()
            try:
                plan = album_cleanup.scan(self._album_client(), d)
                state["plan"] = plan
                state["sel"] = set()
                status.value = plan.brief() if plan.scanned else "没有符合条件的图片（可把天数改成 0）"
                status.color = ACCENT_2 if plan.delete_count else WARN_C
                render()
            except Exception as ex:  # noqa: BLE001
                status.value = f"扫描失败：{str(ex)[:100]}"; status.color = DANGER_C; self._safe_update()

        def on_select_all(e):
            if state.get("rendering"):
                return
            plan = state["plan"]
            if not plan:
                return
            if e.control.value:
                state["sel"] = {c.id for c in plan.candidates if not c.locked and c.id}
            else:
                state["sel"] = set()
            render()

        select_all.on_change = on_select_all

        def confirm_then(tip: str, action):
            confirm = ft.AlertDialog(
                title=ft.Text("确认删除图片？"),
                content=ft.Text(tip, color=MUTED, size=13.5),
                actions=[ft.TextButton("取消", on_click=lambda e: self._close_dlg(confirm)),
                         ft.ElevatedButton("确认删除", bgcolor=DANGER_BG, color="white",
                                           on_click=lambda e: (self._close_dlg(confirm), action()))])
            self._show_dlg(confirm)

        def delete_selected():
            ids = list(state["sel"])
            if not ids:
                self.toast("请先勾选要删除的图片", False); return
            def go():
                status.value = "删除中…"; status.color = MUTED; self._safe_update()
                r = album_cleanup.delete_ids(self._album_client(), ids)
                msg = r.get("message") or (f"已删除 {r.get('deleted', 0)} 张"
                                           + (f"，失败 {r.get('failed', 0)} 张" if r.get("failed") else ""))
                if r.get("errors"):
                    msg += " · " + str(r["errors"][0])[:60]
                status.value = msg
                status.color = ACCENT_2 if r.get("ok") else DANGER_C
                self._log("图片清理（选中）：" + msg)
                load_space(); scan_now()
            threading.Thread(target=go, daemon=True).start()

        def delete_range():
            try:
                d = int((days.value or "30").strip())
            except ValueError:
                self.toast("天数要填数字", False); return
            tip = (f"将删除「{d} 天前」上传的所有图片（已锁定的会自动跳过）。\n"
                   f"注意：这个方式不判断图片是否正被商品使用，删除前请确认。\n"
                   f"想更安全就先用「扫描」勾选后再删。")
            def go():
                status.value = "删除中…"; status.color = MUTED; self._safe_update()
                r = album_cleanup.delete_older_than(self._album_client(), d)
                status.value = r.get("message") or ("完成" if r.get("ok") else "失败")
                status.color = ACCENT_2 if r.get("ok") else DANGER_C
                self._log("图片清理（按时间）：" + status.value)
                load_space(); scan_now()
            confirm_then(tip, lambda: threading.Thread(target=go, daemon=True).start())

        def open_album(e):
            self._open_album_page()

        dlg = ft.AlertDialog(
            title=ft.Text("图片空间清理"),
            content=ft.Column([
                space_text,
                muted("支持两种：① 扫描后勾选删除（推荐，可控）② 按时间删除（快，但不判断是否在用）"),
                ft.Row([days, ft.ElevatedButton("扫描", icon=ft.Icons.SEARCH, bgcolor=ACCENT, color=ON_ACCENT,
                                                on_click=lambda e: threading.Thread(target=scan_now, daemon=True).start()),
                        ft.ElevatedButton("删除选中", icon=ft.Icons.DELETE, bgcolor=DANGER_BG, color="white",
                                          on_click=lambda e: delete_selected()),
                        ft.OutlinedButton("按时间删除", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: delete_range())],
                       spacing=8, wrap=True),
                status, select_all, result,
                muted("说明：店小秘的「自动清理未引用图片」接口对当前账号未开放（返回：功能暂未开放），所以这里用勾选/按时间两种可用方式。"),
            ], spacing=8, width=720, height=560),
            actions=[ft.TextButton("打开相册页面", on_click=open_album),
                     ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self._show_dlg(dlg)
        threading.Thread(target=load_space, daemon=True).start()
        threading.Thread(target=scan_now, daemon=True).start()

    def _p_advanced(self):
        self._current = 2

        # ---------- 类目 ----------
        _auto_cat_on = bool(getattr(self.cfg, "auto_detect_category", True))
        cat_card = card(ft.Column([
            h1i(ft.Icons.CATEGORY_OUTLINED, "类目"),
            muted("类目来源在「店铺设置 → 选择模板」里切换，这里只显示当前生效方式"),
            ft.Row([
                ft.Text("当前：" + ("自动分类（关键词规则 → 商品自带类目 → 模板兜底）"
                                   if _auto_cat_on else "统一使用模板类目"),
                        size=13.5, color=ACCENT_2),
                ft.TextButton("去店铺设置切换", icon=ft.Icons.ARROW_FORWARD,
                              on_click=lambda e: self._switch(1)),
            ], spacing=10, wrap=True),
        ], spacing=8))

        # ---------- 基础设置 ----------
        base_card = card(ft.Column([
            h1i(ft.Icons.STRAIGHTEN, "规格与库存"),
            ft.Row([
                self._sw("original_box", "是否原箱",
                   tooltip_text="商品是否以原箱发货"),
            ], spacing=12, wrap=True),
            ft.Row([
                self._tf("spec_weight", "重量(kg)", width=120, tooltip_text="默认包装重量（优先使用采集到的重量，未采集时用此值）"),
                self._tf("spec_length", "长(cm)", width=100),
                self._tf("spec_width", "宽(cm)", width=100),
                self._tf("spec_height", "高(cm)", width=100),
            ], spacing=8, wrap=True),
            ft.Row([
                self._tf("declared_value", "货值默认值", width=160,
                   tooltip_text="申报货值（速卖通草稿必填，建议填实际货值），未填则默认 100"),
            ], spacing=12, wrap=True),
        ], spacing=10))

        # ---------- 类目属性默认值 ----------
        attr_default_card = card(ft.Column([
            h1("类目属性默认值", 15),
            muted("模板读不到时使用的兜底值"),
            ft.Row([
                self._dd("high_concerned_chemical", "高关注化学品",
                   [("否", "否"), ("是", "是")], width=160),
                self._dd("charge_status", "是否带电",
                   [("否", "否"), ("是", "是")], width=160),
            ], spacing=12, wrap=True),
        ], spacing=10))

        # ---------- 属性模板 / SKU替换 ----------
        self.attr_kw = ft.TextField(label="属性模板关键词", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.attr_status = ft.Text("", size=13.5, color=MUTED)
        self.attr_list = ft.Column(spacing=4, height=220, scroll=ft.ScrollMode.AUTO)

        def attr_search(e):
            self.attr_status.value = "搜索中…"
            self._safe_update()
            threading.Thread(target=self._search_attr_templates, daemon=True).start()

        # 属性模板 + SKU 图替换 + 自定义属性清理：合成一张卡，左右两栏对齐
        def _append_drop(name: str):
            cur = [x.strip() for x in str(self.attr_drop_tf.value or "").replace("，", ",").split(",") if x.strip()]
            if name.lower() not in [x.lower() for x in cur]:
                cur.append(name)
            self.attr_drop_tf.value = ", ".join(cur)
            self.cfg.set("custom_attr_drop", self.attr_drop_tf.value)
            save_config(self.cfg)
            self._safe_update()

        def _on_drop_change(e):
            self.cfg.set("custom_attr_drop", str(e.control.value or "").strip())
            save_config(self.cfg)

        self.attr_drop_tf = ft.TextField(
            label="强制删除的自定义属性（逗号分隔）", width=None, expand=True,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "custom_attr_drop", "") or ""),
            hint_text="如 brand, type, model —— 与系统属性冲突、软件删不掉的填这里",
            on_change=_on_drop_change)

        props_card = card(ft.Column([
            h1i(ft.Icons.RULE, "属性模板与清理"),
            muted("读取模板属性，清理与系统属性冲突的自定义属性"),
            ft.Row([
                ft.Column([
                    ft.Row([self.attr_kw,
                            ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, bgcolor=CARD_2, color=TEXT,
                                              on_click=attr_search),
                            ft.OutlinedButton("清空已应用", on_click=lambda e: self._clear_attrs())],
                           spacing=8, wrap=True),
                    self.attr_status, self.attr_list,
                ], expand=3, spacing=8),
                ft.Container(width=1, bgcolor=CARD_2, height=250),
                ft.Column([
                    ft.Text("自定义属性清理", size=14.5, weight=ft.FontWeight.BOLD, color=TEXT),
                    muted("勾选/填写后，保存时直接从属性里删掉，不再报冲突", 11),
                    ft.Row([self.attr_drop_tf], spacing=8),
                    ft.Row([ft.TextButton("+ brand", on_click=lambda e: _append_drop("brand")),
                            ft.TextButton("+ type", on_click=lambda e: _append_drop("type")),
                            ft.TextButton("+ model", on_click=lambda e: _append_drop("model")),
                            ft.TextButton("+ condition", on_click=lambda e: _append_drop("condition"))],
                           spacing=2, wrap=True),
                ], expand=2, spacing=8),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=10))

        # ---------- 欧盟资质 ----------
        eu_card = card(ft.Column([
            h1("欧盟资质（需要时才填）", 15),
            muted("选「是」时速卖通要求填写，否则保存会被拦截"),
            ft.Row([
                self._dd("eu_qualification", "是否提交欧盟资质",
                   [("否", "否"), ("是", "是")], width=180),
            ], spacing=12),
            ft.Row([
                self._tf("eu_responsible_name", "欧盟责任人名称", width=240),
                self._tf("eu_responsible_address", "责任人地址", width=240),
            ], spacing=12, wrap=True),
            ft.Row([
                self._tf("eu_responsible_email", "电子邮箱", width=240),
                self._tf("eu_responsible_phone", "联系电话", width=180),
            ], spacing=12, wrap=True),
        ], spacing=10))

        self._bind_eu_guard()

        return [page_header("类目与属性", destination(2).description),
                workspace_tabs([
                    ("类目与规格", [cat_card, base_card, attr_default_card]),
                    ("属性模板与清理", [props_card]),
                    ("欧盟资质", [eu_card]),
                ])]

    # ---------- 可复用设置卡（拆分自原「商品设置」）----------
    def _sw(self, key, label, tooltip_text=""):
        ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                        active_color=ACCENT, tooltip=tooltip_text or None)
        def on(e, _k=key):
            self.cfg.set(_k, bool(e.control.value))
            save_config(self.cfg)
        ctl.on_change = on
        return ctl

    def _tf(self, key, label, width=200, tooltip_text=""):
        ctl = ft.TextField(label=label, value=str(getattr(self.cfg, key, "") or ""),
                           width=width, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                           tooltip=tooltip_text or None)
        def on(e, _k=key):
            self.cfg.set(_k, str(e.control.value or ""))
            save_config(self.cfg)
        ctl.on_change = on
        return ctl

    def _dd(self, key, label, options, width=220, tooltip_text=""):
        ctl = ft.Dropdown(label=label, value=str(getattr(self.cfg, key, options[0][0]) or options[0][0]),
                          options=[ft.dropdown.Option(k, v) for k, v in options],
                          width=width, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                          tooltip=tooltip_text or None)
        def on(e, _k=key):
            self.cfg.set(_k, str(e.control.value or ""))
            save_config(self.cfg)
        ctl.on_change = on
        return ctl

    def _card_run(self):
        top_card = card(ft.Column([
            h1i(ft.Icons.TUNE, "运行设置"),
            ft.Row([
                self._tf("edit_sum", "处理产品数量", width=140,
                   tooltip_text="每次运行处理多少个产品（1=只跑1个，0或空=全部）"),
                self._dd("publish_action", "编辑完成后去向", [
                    ("save_only", "留在采集箱"),
                    ("save_to_waiting", "移入待发布"),
                    ("publish", "直接发布"),
                ], width=200, tooltip_text="产品保存后放在哪个位置"),
                self._sw("check_banned_word", "违禁词检查",
                   tooltip_text="发布前检查标题/详情是否有违禁词"),
            ], spacing=16, wrap=True),
        ], spacing=10))

        return top_card

    def _card_img(self):
        four_in_one_on = bool(getattr(self.cfg, "four_in_one", False))
        four_warn = ft.Container(
            content=ft.Text(
                "⚠ 四合一开启后会将主图1-4合成插入第一位，与「AI精细化编辑·主图1」冲突。\n"
                "建议：关闭「AI精细化编辑·主图1」，或把四合一插入位置改为「末尾」。",
                size=12, color=WARN_C),
            bgcolor=ft.Colors.with_opacity(0.12, WARN_C), border_radius=8, padding=8,
            visible=four_in_one_on)

        four_sw = ft.Switch(label="四合一主图（将主图1-4合成一张）",
                            value=four_in_one_on, active_color=ACCENT)

        def on_four(e):
            self.cfg.set("four_in_one", bool(four_sw.value))
            save_config(self.cfg)
            four_warn.visible = bool(four_sw.value)
            try:
                self._safe_update()
            except Exception:
                pass

        four_sw.on_change = on_four

        img_card = card(ft.Column([
            h1("图片设置", 15),
            ft.Row([four_sw,
                    self._dd("four_in_one_position", "四合一位置",
                       [("first", "插入第一位"), ("append", "追加末尾")], width=180,
                       tooltip_text="四合一图片插入的位置（建议「末尾」，避免与AI精细编辑冲突）")],
                   spacing=12, wrap=True),
            four_warn,
            ft.Row([
                self._sw("main_as_detail", "主图同时作为详情图",
                   tooltip_text="把所有主图也追加到详情图里"),
                self._sw("pad_main_images_from_extra", "主图不足时从详情图补齐",
                   tooltip_text="主图张数不足时，从详情图中取图补充到指定数量"),
                self._sw("keep_main_images_local", "保留主图到本地",
                   tooltip_text="将处理后的主图额外保存到本地文件夹"),
            ], spacing=12, wrap=True),
            ft.Divider(height=1, color=CARD_2),
            h1("水印设置", 13),
            self._watermark_card(),
        ], spacing=10))

        return img_card

    def _card_detail(self):
        detail_card = card(ft.Column([
            h1("详情设置", 15),
            ft.Row([
                self._sw("copy_sku_to_detail", "复制SKU到详情",
                   tooltip_text="把SKU名称列表追加到详情页"),
                self._sw("delete_detail_text", "删除详情原有文本",
                   tooltip_text="开启后，AI 详情/自定义文本会替换原有详情，而不是叠加"),
            ], spacing=12, wrap=True),
        ], spacing=10))

        return detail_card

    def _card_publish(self):
        # ---------------- 发布额外选项 ----------------
        try:
            from ..services import publish_retry as _prc
            _pf_count = _prc.count()
        except Exception:
            _pf_count = 0

        extra_card = card(ft.Column([
            h1i(ft.Icons.REPLAY, "记录与重试"),
            muted("管理已编辑记录，重跑处理失败或仅重试发布失败的商品"),
            ft.Row([self._edited_count_text(), ft.TextButton("清空已编辑记录", icon=ft.Icons.RESTART_ALT,
                                                              on_click=lambda e: self._clear_edited()),
                    ft.ElevatedButton("重跑上次失败的商品", icon=ft.Icons.REPLAY,
                                      bgcolor=ACCENT_2, color=ON_ACCENT,
                                      on_click=lambda e: self._rerun_failed()),
                    ft.ElevatedButton(f"只重试发布失败（{_pf_count} 个·不花钱）",
                                      icon=ft.Icons.CLOUD_UPLOAD,
                                      bgcolor=ACCENT, color=ON_ACCENT,
                                      tooltip="这些商品已经编辑成功，只是发布被拒；重试只调发布接口，不跑 AI、不处理图片",
                                      on_click=lambda e: self._retry_publish_failed())],
                   spacing=10, wrap=True),
        ], spacing=10))

        return extra_card

    def _card_video(self):
        # 视频来源：采集视频 / 模板视频 / 主图轮播生成 / 不要视频
        self.video_src_dd = ft.Dropdown(
            label="视频来源", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "video_source", "collected") or "collected"),
            options=[ft.dropdown.Option("collected", "采集的产品视频（默认）"),
                     ft.dropdown.Option("template", "模板产品视频（自动取模板链接）"),
                     ft.dropdown.Option("generated", "用主图合成轮播视频"),
                     ft.dropdown.Option("none", "不要视频")])

        def _on_video_src(e):
            self.cfg.set("video_source", str(self.video_src_dd.value or "collected"))
            save_config(self.cfg)
            self._log(f"视频来源：{self.video_src_dd.value}")

        self.video_src_dd.on_change = _on_video_src
        self.video_path_tf = ft.TextField(
            label="本地视频路径（可选；留空=用模板商品的视频）", width=380,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "template_video_path", "") or ""),
            hint_text="留空即可：自动使用模板商品视频；也可填本地 mp4 覆盖")

        def _on_video_path(e):
            self.cfg.set("template_video_path", str(e.control.value or "").strip())
            save_config(self.cfg)

        self.video_path_tf.on_change = _on_video_path

        return card(ft.Column([
            h1i(ft.Icons.VIDEOCAM_OUTLINED, "商品视频"),
            muted("选择商品视频来源；合成视频使用处理后的主图，不消耗 AI 图片额度"),
            ft.Row([self.video_src_dd, self.video_path_tf], spacing=12, wrap=True),
            h1("轮播合成参数", 14),
            ft.Row([
                self._tf("video_slot_seconds", "每张停留秒数", width=160),
                self._tf("video_transition_seconds", "转场秒数", width=140),
                self._tf("video_size", "视频尺寸", width=180),
            ], spacing=12, wrap=True),
            ft.Row([
                self._sw("video_cover", "铺满裁剪（关闭则留白）"),
                self._sw("video_fade_edges", "首尾淡入淡出"),
                self._sw("overwrite_existing_video", "覆盖商品已有视频"),
            ], spacing=12, wrap=True),
            self._tf("video_output_dir", "视频输出目录（留空使用默认目录）", width=560),
            muted("手动选图合成视频：到「图片工作台 → 图片处理与输出」。"),
        ], spacing=12))

    # ---------- 水印文件（可选文件 + 预览 + 状态）----------
    def _watermark_card(self):
        self.wm_path = ft.TextField(label="水印图片路径（透明 PNG 最佳）", width=520,
                                    value=str(self.cfg.watermark or ""),
                                    bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wm_status = ft.Text("", size=12.5, color=MUTED)
        self.wm_preview = ft.Image(width=120, height=90, fit=ft.ImageFit.CONTAIN, visible=False)

        def refresh_status():
            path = str(self.wm_path.value or "").strip()
            if not path:
                self.wm_status.value = "未设置：开启「叠加水印」前请先选一张水印图（也可填文字，如 @mybrand）"
                self.wm_status.color = MUTED
                self.wm_preview.visible = False
            elif Path(path).exists():
                try:
                    from PIL import Image as _Img
                    with _Img.open(path) as im:
                        self.wm_status.value = f"已找到：{Path(path).name} · {im.width}×{im.height}"
                        self.wm_status.color = ACCENT_2
                    self.wm_preview.src = path
                    self.wm_preview.visible = True
                except Exception:
                    self.wm_status.value = f"已找到文件：{Path(path).name}"
                    self.wm_status.color = ACCENT_2
                    self.wm_preview.visible = False
            else:
                self.wm_status.value = f"未找到该文件：{path}（也可以直接填文字做文字水印）"
                self.wm_status.color = WARN_C
                self.wm_preview.visible = False
            try:
                self._safe_update()
            except Exception:
                pass

        def save_path(v):
            self.cfg.watermark = str(v or "").strip()
            save_config(self.cfg)

        def pick_file(e):
            try:
                self.wm_picker.pick_files(allow_multiple=False,
                                          allowed_extensions=["png", "jpg", "jpeg", "webp"])
            except Exception as ex:
                self.toast(f"选择文件失败：{ex}", False)

        def on_change(e):
            save_path(self.wm_path.value)
            refresh_status()

        self.wm_path.on_change = on_change

        def toggle_wm(e):
            self.cfg.set("overlay_watermark_enable", bool(e.control.value))
            save_config(self.cfg)
            self.toast("已开启叠加水印" if e.control.value else "已关闭叠加水印", bool(e.control.value))

        self.wm_enable = ft.Switch(label="叠加水印到主图（发布时逐张叠加）",
                                   value=bool(getattr(self.cfg, "overlay_watermark_enable", False)),
                                   active_color=ACCENT, on_change=toggle_wm)
        self.wm_pos = ft.Dropdown(label="水印位置", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                  value=str(getattr(self.cfg, "overlay_position", "bottom-right") or "bottom-right"),
                                  options=[ft.dropdown.Option("bottom-right", "右下"),
                                           ft.dropdown.Option("bottom-left", "左下"),
                                           ft.dropdown.Option("top-right", "右上"),
                                           ft.dropdown.Option("top-left", "左上"),
                                           ft.dropdown.Option("center", "居中")])
        self.wm_opacity = ft.TextField(label="透明度 0-1", width=120,
                                       value=str(getattr(self.cfg, "overlay_opacity", "0.7") or "0.7"),
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        def save_wm_pos(e=None):
            self.cfg.set("overlay_position", str(self.wm_pos.value or "bottom-right"))
            save_config(self.cfg)
        def save_wm_opacity(e=None):
            try:
                v = float(self.wm_opacity.value or 0.7)
            except Exception:
                v = 0.7
            self.cfg.set("overlay_opacity", str(max(0.05, min(1.0, v))))
            save_config(self.cfg)
        self.wm_pos.on_change = save_wm_pos
        self.wm_opacity.on_change = save_wm_opacity
        refresh_status()
        return card(ft.Column([
            muted("「叠加水印到主图」用的就是这张图（发布时逐张叠加，可同时回传店小秘相册）"),
            self.wm_enable,
            ft.Row([self.wm_pos, self.wm_opacity], spacing=10, wrap=True),
            ft.Row([self.wm_path,
                    ft.ElevatedButton("选择文件", icon=ft.Icons.IMAGE, bgcolor=CARD_2, color=TEXT, on_click=pick_file),
                    ft.OutlinedButton("测试叠加", icon=ft.Icons.SCIENCE,
                                      on_click=lambda e: threading.Thread(target=self._test_watermark, daemon=True).start())],
                   spacing=10, wrap=True),
            ft.Row([self.wm_preview, self.wm_status], spacing=10),
        ], spacing=8))

    def _on_watermark_file(self, e):
        try:
            if not e.files:
                return
            path = e.files[0].path
        except Exception:
            return
        self.cfg.watermark = path
        save_config(self.cfg)
        if hasattr(self, "wm_path"):
            self.wm_path.value = path
        try:
            if getattr(self, "wm_preview", None) is not None:
                self.wm_preview.src = path
                self.wm_preview.visible = True
            if getattr(self, "wm_status", None) is not None:
                self.wm_status.value = f"已设置：{Path(path).name}"
                self.wm_status.color = SUCCESS_C
            self._safe_update()
            self.toast(f"水印已设置：{Path(path).name}")
        except Exception:
            pass

    def _test_watermark(self):
        """用真实商品主图（取不到才用灰底）叠加水印，预览位置/透明度效果。"""
        wm = str(self.cfg.watermark or "").strip()
        if not wm:
            self.toast("请先设置水印文件", False)
            return
        try:
            from PIL import Image as _Img
            import requests as _rq
            from ..services.image_ops import add_watermark

            d = Path(tempfile.mkdtemp())
            base = d / "base.png"
            src_url = ""
            # ① 当前列表第一个商品的主图
            for pr in (getattr(self, "products", None) or []):
                imgs = pr.get("images") or []
                if imgs:
                    src_url = str(imgs[0])
                    break
            # ② 模板快照里如果有图也用
            if not src_url:
                try:
                    import json as _j
                    snap = _j.loads(self.cfg.template_snapshot or "{}")
                    imgs = snap.get("images") or []
                    if imgs:
                        src_url = str(imgs[0])
                except Exception:
                    pass
            made = False
            if src_url.startswith("http"):
                try:
                    base.write_bytes(_rq.get(src_url, timeout=30).content)
                    made = True
                except Exception:
                    made = False
            if not made:
                _Img.new("RGB", (800, 800), (235, 235, 235)).save(base)
            out = add_watermark(base, wm, d / "wm_out.png",
                                position=str(self.cfg.overlay_position or "bottom-right"),
                                opacity=float(self.cfg.overlay_opacity or 0.7))
            self.wm_preview.src = str(out)
            self.wm_preview.visible = True
            self.wm_status.value = (("预览（真实主图）：" if made else "预览（灰底测试图）：")
                                    + str(out))
            self.wm_status.color = ACCENT_2
            self._log(f"水印预览：{out}")
        except Exception as ex:
            self.wm_status.value = f"测试失败：{str(ex)[:100]}"
            self.wm_status.color = DANGER_C
            self.toast(f"水印测试失败：{ex}", False)
        try:
            self._safe_update()
        except Exception:
            pass

    # ---------- 套装 SKU 预览 ----------
    def _combo_card(self):
        from ..services.combo import preview as _preview
        self.combo_demo = ft.TextField(
            label="示例 SKU（逗号分隔，用于预览套装效果）", width=460,
            value="Red-XL, Blue-L, 3T-20M", bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.combo_preview = ft.Text("", size=12.5, color=MUTED, selectable=True)

        def render(e=None):
            import math
            try:
                factor = float(self.cfg.combo_multiplier or 2)
                if not math.isfinite(factor) or factor <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                self.combo_preview.value = "请填写大于 0 的有效套装价格倍率。"
                self.combo_preview.color = DANGER_C
                self._safe_update()
                return
            names = [x.strip() for x in str(self.combo_demo.value or "").split(",") if x.strip()]
            demo = [{"sku": n, "price": 10.0} for n in names[:4]]
            rows = _preview(demo, str(self.cfg.combo_prefix or "套装"), factor, limit=8)
            self.combo_preview.color = MUTED
            self.combo_preview.value = ("\n".join(rows) if rows else "（先填示例 SKU）") + \
                f"\n说明：套装价 = 原价 × {factor:g}；库存沿用原 SKU"
            try:
                self._safe_update()
            except Exception:
                pass

        self.combo_demo.on_change = render
        settings = [self._sw("combo_enable", "生成套装 SKU"),
                    self._tf("combo_prefix", "套装编码前缀", width=200),
                    self._tf("combo_multiplier", "套装价格倍率", width=160)]
        for control in settings:
            original = control.on_change
            def update(e, callback=original):
                callback(e)
                render()
            control.on_change = update
        render()
        return card(ft.Column([
            h1("套装 SKU", 15),
            muted("预览每个原 SKU 追加套装后的名称和价格；套装价按原价乘倍率计算。"),
            ft.Text("当前支持套装预览；平台新增套装变种尚未接通，开启生成开关会阻止任务启动。",
                    size=12.5, color=WARN_C),
            ft.Row(settings, spacing=12, wrap=True),
            ft.Row([self.combo_demo,
                    ft.OutlinedButton("预览", icon=ft.Icons.VISIBILITY, on_click=render)], spacing=10, wrap=True),
            self.combo_preview,
        ], spacing=8))

    # ---------- 图片处理：简单规则 + 精细任务（无 JSON）----------
    def _image_jobs_ui(self):
        """逐图任务编辑器：主图、详情图和 SKU 图分别设置提示词与参考图。"""
        from ..services.image_jobs import (parse_rules, rows_to_rules, parse_jobs, rows_to_jobs,
                                           migrate, GROUP_LABELS)

        # 旧配置一次性迁移（四合一/水印/逐图方案 → 新结构）
        try:
            rj, jj = migrate(self.cfg, with_global=True)
            if rj != (self.cfg.image_simple_rules or "[]") or jj != (self.cfg.image_ai_jobs or "[]"):
                self.cfg.set("image_simple_rules", rj)
                self.cfg.set("image_ai_jobs", jj)
                save_config(self.cfg)
        except Exception:
            pass

        state = {"jobs": parse_jobs(self.cfg.image_ai_jobs),
                 "rules": parse_rules(self.cfg.image_simple_rules),
                 "timer": None}

        def _get(key, default=None):
            return getattr(self.cfg, key, default)

        summary = ft.Text("", size=12.5, color=MUTED)

        def sync_state():
            self.cfg.set("image_ai_jobs", rows_to_jobs(state["jobs"]))
            self.cfg.set("image_simple_rules", rows_to_rules(state["rules"]))
            refresh_summary()

        def persist():
            sync_state()
            save_config(self.cfg)
            try:
                self._safe_update()
            except Exception:
                pass

        def schedule():
            # 切页会重新读 cfg：先更新内存，防抖只延迟写盘。
            # 旧页面的定时器不能再把旧 state 覆盖回刚编辑的新页面。
            sync_state()
            t = state.get("timer")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            timer = threading.Timer(0.6, lambda: save_config(self.cfg))
            timer.daemon = True
            state["timer"] = timer
            timer.start()

        def refresh_summary():
            n = {g: len([j for j in state["jobs"] if j["group"] == g]) for g in ("main", "detail", "sku")}
            parts = []
            if _get("img_fine_main_enable", True) and n["main"]:
                parts.append(f"AI主图 {n['main']} 条")
            if _get("img_fine_detail_enable", False) and n["detail"]:
                parts.append(f"AI详情图 {n['detail']} 条")
            if _get("img_fine_sku_enable", False) and n["sku"]:
                parts.append(f"AI SKU {n['sku']} 条")
            summary.value = " · ".join(parts) if parts else "未启用任何AI图片编辑"
            summary.color = ACCENT_2 if parts else MUTED

        body = ft.Column(spacing=12)

        # ---------- 参考图选择器（复用同一个，避免堆积 overlay）----------
        if not getattr(self, "_ref_picker", None):
            self._ref_picker = ft.FilePicker(on_result=lambda e: None)
            try:
                self.page.overlay.append(self._ref_picker)
            except Exception:
                pass

        def pick_refs(job, rerender):
            def on_res(e):
                if not e.files:
                    return
                cur = list(job.get("ref_paths") or [])
                for f in e.files:
                    if len(cur) >= 5:
                        break
                    p = str(getattr(f, "path", "") or "")
                    if p and p not in cur:
                        cur.append(p)
                job["ref_paths"] = cur[:5]
                rerender()
                persist()
            self._ref_picker.on_result = on_res
            try:
                self._ref_picker.pick_files(allow_multiple=True,
                                            allowed_extensions=["png", "jpg", "jpeg", "webp"])
            except Exception as ex:
                self.toast(f"选择参考图失败：{ex}", False)

        # ---------- 精细化编辑：单条任务 ----------
        MAX_POS = {"main": 10, "detail": 12, "sku": 10}

        def job_row(job, g, rerender):
            pos = ft.Dropdown(
                label="图片位置", width=132, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                value=str(min(MAX_POS[g], max(1, int(job.get("index") or 1)))),
                options=[ft.dropdown.Option(str(i), f"{GROUP_LABELS.get(g, g)} {i}")
                         for i in range(1, MAX_POS[g] + 1)])

            # AI提示词预设下拉框
            preset_options = ["自定义"] + list(IMAGE_PROMPT_PRESETS.get(g, {}).keys())
            current_preset = job.get("preset") or "自定义"
            if current_preset not in preset_options:
                current_preset = "自定义"

            preset_dropdown = ft.Dropdown(
                label="AI提示词预设",
                width=180,
                value=current_preset,
                options=[ft.dropdown.Option(p) for p in preset_options],
                bgcolor=CARD_2,
                border_color=CARD_2,
                color=TEXT
            )

            prompt = ft.TextField(label="AI 提示词", value=str(job.get("prompt") or ""),
                                  multiline=True, min_lines=3, max_lines=12, expand=True,
                                  bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            refs = list(job.get("ref_paths") or [])
            refs_btn = ft.TextButton(
                f"参考图（{len(refs)}/5）", icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                tooltip="上传本地参考图（最多5张）。图片顺序：先本地参考图，后产品原图。提示词里用「图1」「图2」引用。",
                on_click=lambda e: pick_refs(job, rerender))
            refs_label = ft.Text(
                "、".join(Path(p).name for p in refs) if refs else "",
                size=12, color=MUTED)

            def clear_refs(e=None):
                job["ref_paths"] = []
                rerender()
                persist()

            refs_clear = ft.IconButton(ft.Icons.CLOSE, icon_size=15.5, icon_color=DANGER_C,
                                       tooltip="清空参考图", visible=bool(refs), on_click=clear_refs)

            def on_preset(e):
                selected = preset_dropdown.value
                job["preset"] = selected
                if selected != "自定义":
                    # 填充预设提示词
                    preset_text = IMAGE_PROMPT_PRESETS.get(g, {}).get(selected, "")
                    if preset_text:
                        prompt.value = preset_text.strip()
                        job["prompt"] = preset_text.strip()
                schedule()
                try:
                    self._safe_update()
                except Exception:
                    pass

            def on_pos(e):
                try:
                    job["index"] = max(1, int(pos.value or 1))
                except Exception:
                    job["index"] = 1
                schedule()

            def on_prompt(e):
                job["prompt"] = str(prompt.value or "").strip()
                # 如果用户手动修改了提示词，切换到"自定义"
                if job["prompt"] and preset_dropdown.value != "自定义":
                    # 检查是否还匹配预设
                    preset_text = IMAGE_PROMPT_PRESETS.get(g, {}).get(preset_dropdown.value, "")
                    if job["prompt"].strip() != preset_text.strip():
                        preset_dropdown.value = "自定义"
                        job["preset"] = "自定义"
                schedule()

            pos.on_change = on_pos
            preset_dropdown.on_change = on_preset
            prompt.on_change = on_prompt

            def remove(e):
                try:
                    state["jobs"].remove(job)
                except ValueError:
                    pass
                rerender()
                persist()

            return ft.Container(
                content=ft.Column([
                    ft.Row([pos, preset_dropdown,
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=DANGER_C,
                                          tooltip="删除这一条", on_click=remove)],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
                    prompt,
                    ft.Row([refs_btn, refs_clear, refs_label], spacing=4, wrap=True),
                ], spacing=4),
                bgcolor=CARD_2, border_radius=10, padding=10)

        # ---------- 精细化编辑：一个分组 ----------
        def group_section(g: str, title: str, enable_key: str, rerender,
                          extra_controls: list | None = None):
            enabled = ft.Checkbox(
                label=title,
                value=bool(_get(enable_key, g == "main")),
                label_style=ft.TextStyle(size=13.5, weight=ft.FontWeight.BOLD),
                tooltip=f"勾选后上架时对{title}启用 AI 处理")

            jobs = [j for j in state["jobs"] if j["group"] == g]
            rows_box = ft.Column([job_row(j, g, rerender) for j in jobs], spacing=8)

            def on_enable(e):
                self.cfg.set(enable_key, bool(enabled.value))
                save_config(self.cfg)
                refresh_summary()
                try:
                    self._safe_update()
                except Exception:
                    pass

            enabled.on_change = on_enable

            def add_job(e=None):
                state["jobs"].append({"group": g, "index": 1, "refs": "", "prompt": "",
                                      "mode": "edit", "count": 1, "insert": "replace",
                                      "preset": "", "ref_paths": []})
                rerender()
                persist()

            def batch_add(e=None):
                existing_positions = {j.get("index") for j in jobs}
                default_preset_key = list(IMAGE_PROMPT_PRESETS.get(g, {}).keys())[0] if IMAGE_PROMPT_PRESETS.get(g) else None
                default_prompt = IMAGE_PROMPT_PRESETS.get(g, {}).get(default_preset_key, "") if default_preset_key else ""
                max_pos = MAX_POS.get(g, 10)
                added = 0
                for i in range(1, min(max_pos + 1, 9)):
                    if i not in existing_positions:
                        state["jobs"].append({
                            "group": g, "index": i, "refs": "",
                            "prompt": default_prompt, "mode": "edit", "count": 1,
                            "insert": "replace", "preset": default_preset_key or "自定义", "ref_paths": []
                        })
                        added += 1
                if added > 0:
                    rerender()
                    persist()
                    self.toast(f"已批量添加 {added} 条任务", True)
                else:
                    self.toast("所有位置都已有任务", False)

            job_count = ft.Text(
                f"{len(jobs)} 条任务" if jobs else "未添加任务",
                size=12.5, color=ACCENT_2 if jobs else MUTED)

            add_btn = ft.OutlinedButton(
                "+ 添加", icon=ft.Icons.ADD, on_click=add_job,
                tooltip=f"逐个添加{title}任务，自定义每张图的 AI 提示词")
            batch_btn = ft.OutlinedButton(
                "批量 1-8 张", icon=ft.Icons.PLAYLIST_ADD, on_click=batch_add,
                tooltip=f"一键为所有{title}（1-8张）添加任务，使用默认预设")

            controls = [
                ft.Row([enabled, job_count, ft.Row([add_btn, batch_btn], spacing=6)],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                *((extra_controls or [])),
                rows_box if jobs else None,
            ]
            controls = [c for c in controls if c is not None]

            return ft.Container(
                content=ft.Column(controls, spacing=8),
                border=ft.border.all(1, CARD_2), border_radius=12, padding=12)

        # ---------- 精细化编辑面板 ----------
        def fine_panel(rerender):
            det_replace = ft.Checkbox(
                label="删除原有详情图，仅保留 AI 编辑结果",
                value=bool(_get("img_fine_detail_replace", False)),
                tooltip="开启后，原详情图全部清除，只保留本次 AI 编辑生成的图片")

            def on_det_replace(e):
                self.cfg.set("img_fine_detail_replace", bool(det_replace.value))
                save_config(self.cfg)

            det_replace.on_change = on_det_replace

            return ft.Column([
                group_section("main", "AI 主图编辑", "img_fine_main_enable", rerender),
                group_section("detail", "AI 详情图编辑", "img_fine_detail_enable", rerender,
                              extra_controls=[det_replace]),
                group_section("sku", "AI SKU 图编辑", "img_fine_sku_enable", rerender),
            ], spacing=12)

        # ---------- 渲染 ----------
        def render():
            body.controls = [fine_panel(render)]
            refresh_summary()
            try:
                self._safe_update()
            except Exception:
                pass

        render()

        # ---------- 图片尺寸校验（速卖通 ≥800×800）----------
        size_on = ft.Checkbox(label="图片尺寸校验", value=bool(_get("img_min_size_enable", True)))
        size_val = ft.TextField(label="最小边长(px)", width=140,
                                value=str(_get("img_min_size", 800)),
                                bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        size_drop = ft.Checkbox(
            label="连原图里也不合规的一起去掉（默认关，避免误删主图）",
            value=bool(_get("img_min_size_drop_invalid", False)))

        def on_size_on(e):
            self.cfg.set("img_min_size_enable", bool(size_on.value))
            save_config(self.cfg)

        def on_size_val(e):
            try:
                v = max(1, int(str(size_val.value or "800").strip()))
            except Exception:
                v = 800
            self.cfg.set("img_min_size", v)
            schedule()

        def on_size_drop(e):
            self.cfg.set("img_min_size_drop_invalid", bool(size_drop.value))
            save_config(self.cfg)

        size_on.on_change = on_size_on
        size_val.on_change = on_size_val
        size_drop.on_change = on_size_drop

        audit_status = ft.Text("", size=12.5, color=MUTED)

        def run_audit(e=None):
            prods = list(getattr(self, "products", None) or [])
            if not prods:
                self.toast("还没有加载商品，请先在「上架」页拉取产品", False)
                return
            ms = int(_get("img_min_size", 800) or 800)

            def work():
                from ..services.image_validation import audit_products
                audit_status.value = f"检查中… 0/{len(prods)}"
                try:
                    self._safe_update()
                except Exception:
                    pass

                def prog(n, total, title):
                    audit_status.value = f"检查中… {n}/{total} {title}"
                    try:
                        self._safe_update()
                    except Exception:
                        pass

                try:
                    res = audit_products(prods, ms, progress=prog)
                except Exception as ex:  # noqa: BLE001
                    audit_status.value = f"检查失败：{ex}"
                    audit_status.color = DANGER_C
                    self._log(f"图片体检失败：{ex}", "WARN")
                    try:
                        self._safe_update()
                    except Exception:
                        pass
                    return
                bad_lines = []
                for pr in res["products"]:
                    for idx, url, info in pr["bad"]:
                        bad_lines.append(f"[{pr['id']}] 第{idx}张 {info}")
                ok = res["failed"] == 0
                audit_status.value = (f"{'✓' if ok else '✗'} 共 {res['total']} 张："
                                      f"合格 {res['passed']} / 不合格 {res['failed']}（阈值 {ms}px）")
                audit_status.color = ACCENT_2 if ok else DANGER_C
                self._log(audit_status.value, "INFO" if ok else "WARN")
                for line in bad_lines[:20]:
                    self._log("  · " + line, "WARN")
                try:
                    self._show_audit_dialog(res, ms)
                except Exception:
                    pass
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        audit_btn = ft.OutlinedButton("检查图片尺寸", icon=ft.Icons.SEARCH,
                                      on_click=lambda e: run_audit())

        return card(ft.Column([
            h1("图片通用编辑", 15),
            ft.Row([summary], spacing=12),
            body,
            ft.Divider(height=1, color=CARD_2),
            ft.Row([size_on, size_val, audit_btn], spacing=10, wrap=True),
            muted("AI 产出的图小于最小边长时不采用（保留原图）；速卖通要求主图/SKU图/详情图 ≥800×800"),
            size_drop,
            audit_status,
        ], spacing=10))

    def _show_audit_dialog(self, res: dict, min_size: int):
        """展示图片尺寸体检结果（只读，不改任何商品数据）。"""
        rows = []
        for pr in res.get("products", []):
            bad = pr.get("bad") or []
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(pr.get("title") or pr.get("id") or "", size=12.5, color=TEXT)),
                ft.DataCell(ft.Text(f"{pr.get('ok', 0)}/{pr.get('checked', 0)}", size=12.5, color=TEXT)),
                ft.DataCell(ft.Text("—" if not bad else f"{len(bad)} 张",
                                    size=12.5, color=ACCENT_2 if not bad else DANGER_C)),
                ft.DataCell(ft.Text("；".join(f"第{i}张 {info}" for i, _u, info in bad[:3]) or "",
                                    size=12, color=MUTED, selectable=True)),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("产品", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("合格", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("不合格", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("明细", size=12.5, color=MUTED))],
            rows=rows, column_spacing=16, heading_row_height=36, data_row_min_height=ROW_MIN_H)
        head = ft.Text(
            f"阈值 {min_size}×{min_size}：共 {res.get('total', 0)} 张，"
            f"合格 {res.get('passed', 0)}，不合格 {res.get('failed', 0)}",
            size=13.5, color=TEXT, weight=ft.FontWeight.BOLD)
        self.dlg = ft.AlertDialog(
            title=ft.Text("图片尺寸体检"),
            content=ft.Container(width=760, height=420,
                                 content=ft.Column([head,
                                                    ft.Row([table], scroll=ft.ScrollMode.AUTO)],
                                                   spacing=8, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dialog())],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dlg(self.dlg)

    def _four_in_one_local(self, e=None):
        """把当前选中的图（前 4 张）拼成一张，追加到图片墙与结果里。"""
        try:
            from ..services.image_ops import four_in_one
            from pathlib import Path as _P
            imgs = [p for p in (self.picked or []) if _P(p).exists()]
            if len(imgs) < 2:
                self.toast("至少需要 2 张图才能四合一", False); return
            out = _P(tempfile.gettempdir()) / f"four-in-one-{int(time.time())}.png"
            four_in_one(imgs[:4], out)
            self.picked = list(self.picked) + [out]
            self.wb_sel_paths.add(str(out))
            if getattr(self, "img_results", None) is not None:
                self.img_results.controls.append(ft.Row([
                    ft.Icon(ft.Icons.GRID_VIEW, size=16.5, color=ACCENT_2),
                    ft.Text(f"四合一完成：{out}", size=12.5, color=MUTED, selectable=True)], spacing=8))
            if getattr(self, "img_status", None) is not None:
                self.img_status.value = f"四合一完成：{len(imgs[:4])} 张 → {out.name}"
                self.img_status.color = ACCENT_2
            self._log(f"四合一：{len(imgs[:4])} 张 → {out}")
            try:
                self._wb_render_wall()
            except Exception:
                pass
            self.toast(f"四合一已生成：{out}")
        except Exception as ex:
            self.toast(f"四合一失败：{ex}", False)
        try:
            self._safe_update()
        except Exception:
            pass

    def _p_ai(self):
        """AI 文案：能力卡片（开关置顶 + 提示词折叠），保留全部配置项。"""
        self._current = 3
        self.field_refs = []
        self._autosave_status = ft.Text("修改即自动保存", size=12.5, color=MUTED)

        # ---------- 详情增强：自定义文字/图片 + 插入位置 ----------
        def _build_detail_extra():
            def sw2(key, label, tip=""):
                ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                                active_color=ACCENT, tooltip=tip or None)
                def on(e, _k=key):
                    self.cfg.set(_k, bool(e.control.value))
                    save_config(self.cfg)
                ctl.on_change = on
                return ctl

            def tf2(key, label, width=380, tip=""):
                ctl = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                                   color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                                   tooltip=tip or None, multiline=False)
                def on(e, _k=key):
                    self.cfg.set(_k, str(e.control.value or ""))
                    save_config(self.cfg)
                ctl.on_change = on
                return ctl

            def dd2(key, label, options, width=180, tip=""):
                ctl = ft.Dropdown(width=width, label=label, bgcolor=CARD_2, border_color=CARD_2,
                                  color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                                  tooltip=tip or None,
                                  options=[ft.dropdown.Option(v, t) for v, t in options])
                def on(e, _k=key):
                    self.cfg.set(_k, str(e.control.value or ""))
                    save_config(self.cfg)
                ctl.on_change = on
                return ctl

            # 详情本地图
            import json as _jd2
            raw = getattr(self.cfg, "detail_local_images", "[]")
            try:
                self.dl_paths = [str(x) for x in (_jd2.loads(raw) if isinstance(raw, str) else raw) if str(x)]
            except Exception:
                self.dl_paths = []
            self.dl_list = ft.Column(spacing=3)
            self.dl_status = ft.Text("", size=12.5, color=MUTED)

            def _dl_refresh():
                self.cfg.set("detail_local_images", _jd2.dumps(self.dl_paths, ensure_ascii=False))
                save_config(self.cfg)
                self.dl_list.controls = [
                    ft.Row([ft.Icon(ft.Icons.IMAGE_OUTLINED, size=15.5, color=MUTED),
                            ft.Text(str(x)[-60:], size=12.5, color=TEXT, expand=True),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=15.5, icon_color=DANGER_C,
                                          tooltip="移除",
                                          on_click=lambda e, _x=x: (self.dl_paths.remove(_x), _dl_refresh()))],
                           spacing=6)
                    for x in self.dl_paths
                ] or [ft.Text("未选择本地详情图", size=12.5, color=MUTED)]
                self.dl_status.value = f"已选 {len(self.dl_paths)} 张，刊登时上传图床后插入详情"
                try:
                    self._safe_update()
                except Exception:
                    pass

            def _dl_pick(e):
                self._dl_picker.pick_files(allow_multiple=True,
                                           allowed_extensions=["png", "jpg", "jpeg", "webp"])

            def _dl_on_result(e):
                if e.files:
                    for f in e.files:
                        if f.path and f.path not in self.dl_paths:
                            self.dl_paths.append(f.path)
                    _dl_refresh()

            # 复用同一个 FilePicker：每次进页面都 append 会在 overlay 里堆一堆
            if getattr(self, "_dl_picker", None) is None:
                self._dl_picker = ft.FilePicker(on_result=_dl_on_result)
                try:
                    self.page.overlay.append(self._dl_picker)
                except Exception:
                    pass
            else:
                self._dl_picker.on_result = _dl_on_result
            _dl_refresh()

            return card(ft.Column([
                h1("详情内容增强（文字 / 图片）", 15),
                muted("AI 详情生成后，把下面填的文字和选的图片插到描述的开头或结尾"),
                ft.Row([
                    sw2("custom_detail_text_enable", "启用（总开关）",
                        "关闭后，下面的文字和图片都不会插入详情"),
                    sw2("detail_image_compliance", "详情图合规过滤",
                        "默认关闭；打开后会剔除含联系方式/二维码/促销的详情图（多一次视觉检查）"),
                    dd2("detail_text_position", "文字插入位置",
                        [("head", "开头"), ("tail", "结尾"), ("both", "头尾都加")],
                        tip="自定义文字插到哪里"),
                    dd2("detail_local_image_position", "图片插入位置",
                        [("head", "开头"), ("tail", "结尾"), ("both", "头尾都加")],
                        tip="本地详情图插到哪里"),
                ], spacing=12, wrap=True),
                ft.Row([
                    tf2("detail_text_info", "开头文字（可留空）", 380, "插到详情最前面的文字"),
                    tf2("detail_text_info_tail", "结尾文字（可留空）", 380, "插到详情最后面的文字"),
                ], spacing=12, wrap=True),
                ft.Divider(height=1, color=CARD_2),
                h1("详情本地图", 13),
                ft.Row([ft.ElevatedButton("选择图片", icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                                          bgcolor=ACCENT, color=ON_ACCENT, on_click=_dl_pick),
                        ft.OutlinedButton("清空", icon=ft.Icons.DELETE_SWEEP,
                                          on_click=lambda e: (self.dl_paths.clear(), _dl_refresh()))],
                       spacing=10, wrap=True),
                self.dl_list, self.dl_status,
            ], spacing=8))

        # 能力卡：每项「主开关 + 折叠提示词」，配置键与 schema_ai 完全一致
        _caps = [
            ("标题生成", "根据采集标题生成跨境可用英文标题", "enable_ai_title",
             ["title_prompt", "title_prefix", "title_suffix"]),
            ("详情描述", "生成/改写商品详情文案", "enable_ai_detail", ["detail_prompt"]),
            ("SKU 优化", "优化变种 SKU 名称与可读性", "enable_ai_sku", ["sku_prompt"]),
            ("读图估重", "根据主图估算重量规格", "enable_ai_weight", ["weight_prompt"]),
        ]
        _by_key = {f.key: f for _, fields in schema_ai() for f in fields}
        _preset_map = {"title_prompt": "title", "detail_prompt": "detail",
                       "sku_prompt": "sku", "weight_prompt": "weight"}
        cards = []
        for title, hint, en_key, adv_keys in _caps:
            en = _by_key[en_key]
            adv = [_by_key[k] for k in adv_keys if k in _by_key]
            # Field 实例不能跨卡复用（build 会写 ctrl），重新构造同 key
            en_f = Field(en.key, en.label, en.kind, en.options, en.hint)
            adv_fs = [Field(f.key, f.label, f.kind, f.options, f.hint) for f in adv]
            # 提示词预设下拉（与图片精细化同款交互：选完点「套用」）
            _pk = adv_keys[0] if adv_keys else ""
            _pres = TEXT_PROMPT_PRESETS.get(_preset_map.get(_pk, ""), {})
            preset_ctl = None
            if _pres:
                _dd = ft.Dropdown(width=300, dense=True, bgcolor=CARD_2, border_color=CARD_2,
                                  color=TEXT, value=next(iter(_pres)),
                                  options=[ft.dropdown.Option(k) for k in _pres])

                def _apply(e, _k=_pk, _dd=_dd, _fs=adv_fs, _title=title):
                    txt = TEXT_PROMPT_PRESETS[_preset_map[_k]].get(_dd.value or "")
                    if not txt:
                        return
                    self.cfg.set(_k, txt)
                    save_config(self.cfg)
                    for _f in _fs:
                        if _f.key == _k and _f.ctrl is not None:
                            try:
                                _f.ctrl.value = txt
                            except Exception:
                                pass
                    self.toast(f"已套用{_title}预设：{_dd.value}")
                    try:
                        self._safe_update()
                    except Exception:
                        pass

                preset_ctl = ft.Row([muted("提示词预设", FS_SM), _dd,
                                     ft.OutlinedButton("套用", on_click=_apply)],
                                    spacing=8, wrap=True)
            c, refs = capability_card(title, hint, en_f, adv_fs, self.cfg, self._bind_autosave,
                                      preset_control=preset_ctl)
            self.field_refs.extend(refs)
            cards.append(c)

        def _preset_card():
            mapping = {"title": "title_prompt", "detail": "detail_prompt", "sku": "sku_prompt"}
            rows = []
            for kind, label in (("title", "标题"), ("detail", "详情"), ("sku", "SKU")):
                dd = ft.Dropdown(width=300, dense=True, bgcolor=CARD_2, border_color=CARD_2,
                                 color=TEXT, value=next(iter(TEXT_PROMPT_PRESETS[kind])),
                                 options=[ft.dropdown.Option(k) for k in TEXT_PROMPT_PRESETS[kind]])

                def apply(e, _k=kind, _dd=dd):
                    txt = TEXT_PROMPT_PRESETS[_k].get(_dd.value or "")
                    if not txt:
                        return
                    self.cfg.set(mapping[_k], txt)
                    save_config(self.cfg)
                    self.toast(f"已套用{label}预设：{_dd.value}")
                    self._switch(3)

                rows.append(ft.Row([ft.Text(f"{label}预设", size=FS_BASE, color=TEXT, width=70),
                                    dd,
                                    ft.OutlinedButton("套用", on_click=apply)], spacing=10))
            return card(ft.Column([h1("提示词预设库", FS_MD),
                                   muted("选一个行业/风格，一键替换对应提示词；仍可在下方继续微调"),
                                   *rows], spacing=10))

        return [self._settings_header("AI 文案", destination(3).description),
                workspace_tabs([
                    ("AI 能力与提示词", [_preset_card(), *cards]),
                    ("详情内容增强", [_build_detail_extra(), self._card_detail()]),
                ])]

    def _p_review(self):
        """内容审查：文本 / 主图 / 详情图 三张能力卡，配置键不丢。"""
        self._current = 4
        self.field_refs = []
        self._autosave_status = ft.Text("修改即自动保存", size=12.5, color=MUTED)
        _by_key = {f.key: f for _, fields in schema_review() for f in fields}

        def _rebuild(key):
            f = _by_key[key]
            return Field(f.key, f.label, f.kind, f.options, f.hint)

        cards = []
        # 文本审查
        c, refs = capability_card(
            "文本审查", "标题/详情/SKU 文本合规检查",
            _rebuild("review_text_enable"),
            [_rebuild("review_text_prompt"), _rebuild("check_banned_word")],
            self.cfg, self._bind_autosave)
        self.field_refs.extend(refs); cards.append(c)
        # 主图审查
        c, refs = capability_card(
            "主图审查", "主图违禁/低质拦截",
            _rebuild("review_main_img_enable"),
            [_rebuild("review_main_img_prompt")],
            self.cfg, self._bind_autosave)
        self.field_refs.extend(refs); cards.append(c)
        # 详情图审查（含前后 N 张 + 拦截开关）
        c, refs = capability_card(
            "详情图审查", "详情图抽样审查；不合规可拦截发布",
            _rebuild("review_detail_img_enable"),
            [_rebuild("review_detail_img_prompt"),
             _rebuild("detail_review_first_count"),
             _rebuild("detail_review_last_count")],
            self.cfg, self._bind_autosave)
        self.field_refs.extend(refs); cards.append(c)

        block = _rebuild("review_block_on_fail")
        cards.append(card(ft.Column([
            h1i(ft.Icons.RULE, "审查结果处理"),
            muted("对文本、主图和详情图审查共同生效；关闭时记录风险并继续处理。"),
            block.build(self.cfg),
        ], spacing=10)))
        self.field_refs.append(block)
        self._bind_autosave(block)

        return [self._settings_header("内容审查", "发布前自动执行；这里只配置规则与提示词"),
                *cards]
    def _apply_image_preset(self, kind: str):
        """AI 图片常用预设：一键把主图相关的几个开关设成固定组合。"""
        _main_keys = ("main_image_generate_enable", "white_bg_enable", "watermark_enable",
                      "translate_main_pic_enable", "img_main_edit", "designated_edit_enable")
        presets = {
            "white": {"white_bg_enable": True},
            "watermark": {"watermark_enable": True},
            "translate": {"translate_main_pic_enable": True},
            "off": {},
        }
        on = presets.get(kind, {})
        label = {"white": "只做主图白底", "watermark": "只做主图去水印",
                 "translate": "只做主图翻译", "off": "关闭全部主图处理"}.get(kind, kind)
        _names = {"main_image_generate_enable": "AI 生成主图", "white_bg_enable": "主图白底",
                  "watermark_enable": "主图去水印", "translate_main_pic_enable": "主图翻译",
                  "img_main_edit": "主图精细编辑", "designated_edit_enable": "主图指定编辑"}
        _on = [f"✓ {_names[k]}" for k in _main_keys if on.get(k)]
        _off = [f"· {_names[k]}（关闭）" for k in _main_keys if not on.get(k)]
        body = ("将开启：\n" + ("\n".join(_on) if _on else "（无）") +
                "\n\n将关闭：\n" + "\n".join(_off) +
                "\n\n只影响主图相关开关；描述图 / SKU 图不变。")

        def apply_now():
            for k in _main_keys:
                self.cfg.set(k, bool(on.get(k, False)))
            save_config(self.cfg)
            self._log(f"图片预设：{label}", "INFO")
            self.toast(f"已应用：{label}", True)
            self._switch(5)

        self._confirm(f"应用预设：{label}？", body, apply_now)

    def _p_image(self):
        """AI 图片：只保留两大块 —— 简单编辑 / 精细化编辑。"""
        self._current = 5
        self._autosave_status = ft.Text("修改即自动保存", size=12.5, color=MUTED)
        self.field_refs = []

        def sw(key, label, tip=""):
            ctl = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                            active_color=ACCENT, tooltip=tip or None)
            def on(e, _k=key):
                self.cfg.set(_k, bool(e.control.value))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        def add_section(title, schemas, expanded=True):
            controls = []
            for name, fields in schemas:
                c, refs = section(name, fields, self.cfg)
                self.field_refs.extend(refs)
                for f in refs:
                    self._bind_autosave(f)
                controls.append(c)
            return ft.ExpansionTile(
                title=ft.Text(title, size=15.5, weight=ft.FontWeight.BOLD, color=TEXT),
                controls=controls, initially_expanded=expanded,
                icon_color=ACCENT, collapsed_icon_color=MUTED)

        # ---- 营销图（可选增强）----
        _mk_on = ft.Switch(label="生成营销图（写进营销图片 1:1 / 3:4）",
                           value=bool(getattr(self.cfg, "marketing_auto_generate", False)),
                           active_color=ACCENT)

        def _on_mk_on(e):
            self.cfg.set("marketing_auto_generate", bool(_mk_on.value))
            save_config(self.cfg)

        _mk_on.on_change = _on_mk_on

        self.mk_src_dd = ft.Dropdown(
            label="营销图来源", width=300, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "marketing_source", "local") or "local"),
            options=[ft.dropdown.Option("local", "本地裁切（店小秘同款·免费·秒出）"),
                     ft.dropdown.Option("ai", "AI 生成场景图（花钱·约15秒/张）")])

        def _on_mk_src(e):
            self.cfg.set("marketing_source", str(self.mk_src_dd.value or "local"))
            save_config(self.cfg)
            self._log(f"营销图来源：{self.mk_src_dd.value}")

        self.mk_src_dd.on_change = _on_mk_src

        _mk_presets = list(IMAGE_PROMPT_PRESETS.get("marketing", {}).keys())
        self.mk_preset_dd = ft.Dropdown(
            label="AI 提示词预设（机械类目）", width=260, bgcolor=CARD_2, border_color=CARD_2,
            color=TEXT, value=_mk_presets[0] if _mk_presets else None,
            options=[ft.dropdown.Option(k) for k in _mk_presets])

        self.mk_prompt = ft.TextField(
            label="营销图提示词", multiline=True, min_lines=5, max_lines=9,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "marketing_image_prompt", "") or ""))

        def _on_mk_preset(e):
            _k = self.mk_preset_dd.value
            _txt = IMAGE_PROMPT_PRESETS.get("marketing", {}).get(_k)
            if _txt:
                self.mk_prompt.value = _txt
                self.cfg.set("marketing_image_prompt", _txt)
                save_config(self.cfg)
                self._safe_update()

        self.mk_preset_dd.on_change = _on_mk_preset

        def _on_mk_prompt(e):
            self.cfg.set("marketing_image_prompt", str(e.control.value or ""))
            save_config(self.cfg)

        self.mk_prompt.on_change = _on_mk_prompt

        marketing_card = card(ft.Column([
            h1("营销图（可选）", 15),
            muted("本地裁切=店小秘「一键生成」同款，免费秒出；AI 场景图=按提示词生成，约15秒/张"),
            _mk_on,
            ft.Row([self.mk_src_dd, self.mk_preset_dd], spacing=12, wrap=True),
            self.mk_prompt,
        ], spacing=10))

        # ---- 简单编辑：白底 / 去水印 / 翻译（带范围）----
        simple_keys = [
            "white_background_pic_1", "white_background_pic_2", "white_bg_enable",
            "remove_watermark_pic_1", "remove_watermark_pic_2", "watermark_enable",
            "translate_main_pic_enable", "translate_main_pic_indices",
            "white_background_detail_pic_indices", "img_detail_white_bg", "img_detail_remove_wm",
            "translate_detail_pic_enable", "translate_detail_pic_indices",
            "white_background_sku_pic_indices", "white_background_sku_pic",
            "img_sku_white_bg", "img_sku_remove_wm",
            "translate_sku_pic",
        ]
        simple = []
        for name, fields in schema_image():
            fs = [f for f in fields if f.key in simple_keys]
            if fs:
                simple.append((name, fs))
        def _preset_btn(label, kind, icon):
            return ft.TextButton(label, icon=icon,
                                 tooltip="只改主图相关开关，描述图/SKU 图不受影响",
                                 on_click=lambda e, _k=kind: self._apply_image_preset(_k))

        simple_tab = ft.Column([
            ft.Row([muted("常用预设：", 11),
                    _preset_btn("只做白底", "white", ft.Icons.WB_SUNNY_OUTLINED),
                    _preset_btn("只去水印", "watermark", ft.Icons.AUTO_FIX_HIGH),
                    _preset_btn("只做翻译", "translate", ft.Icons.TRANSLATE),
                    _preset_btn("全部关闭", "off", ft.Icons.POWER_SETTINGS_NEW)],
                   spacing=6, wrap=True),
            add_section("主图 / 详情图 / SKU 图：白底 · 去水印 · 翻译", simple, expanded=True),
            card(ft.Column([
                muted("勾选后按「范围」处理；范围写法 1-3,5 或留空=全部。", 11),
                ft.Row([sw("translate_image_understanding", "AI 判断图片是否需要翻译")],
                       spacing=16, wrap=True),
                ft.Row([sw("img_sku_edit", "SKU 图也做精细编辑")],
                       spacing=16, wrap=True),
            ], spacing=8)),
        ], spacing=10)

        # ---- 精细化编辑：主图 / 详情图 / SKU（逐条：位置+提示词+参考图）----
        refined_tab = ft.Column([self._image_jobs_ui()], spacing=10)

        # ---- 开关冲突提示（自动检测）----
        try:
            from ..services.image_task_plan import detect_conflicts
            _cf = detect_conflicts(self.cfg)
        except Exception:
            _cf = []
        _colors = {"high": DANGER_C, "mid": WARN_C, "low": MUTED}
        _icon = {"high": ft.Icons.ERROR_OUTLINE, "mid": ft.Icons.WARNING_AMBER_ROUNDED,
                 "low": ft.Icons.INFO_OUTLINE}
        conflict_card = card(ft.Column([
            h1("开关检查", 15),
            muted("同时开多个图片开关时的冲突提示；红色=建议先处理"),
            *([ft.Row([ft.Icon(_icon.get(c["level"], ft.Icons.INFO_OUTLINE), size=16.5,
                               color=_colors.get(c["level"], MUTED)),
                       ft.Column([ft.Text(c["title"], size=13.5, color=TEXT),
                                  ft.Text(c["detail"], size=12, color=MUTED),
                                  ft.Text("→ " + c["fix"], size=12, color=ACCENT_2)],
                                 spacing=1, expand=True)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
               for c in _cf] if _cf else
              [ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, size=16.5, color=ACCENT_2),
                       ft.Text("没有检测到开关冲突", size=13.5, color=ACCENT_2)], spacing=8)]),
        ], spacing=6))

        tabs = workspace_tabs([
            ("简单编辑", [simple_tab]),
            ("精细化编辑", [refined_tab]),
            ("营销图", [marketing_card]),
            ("图片输出与水印", [self._card_img()]),
            ("商品视频", [self._card_video()]),
        ])
        return [self._settings_header("AI 图片", destination(5).description),
                tabs, conflict_card]

    # ---------- 价格设置（表格化，不再让用户写 JSON）----------
    def _p_price(self):
        from ..services.pricing import compute_price, tiers_to_rows, rows_to_tiers
        self._current = 6

        # ---------- 计算方式（店小秘风格：先选算法，再填参数）----------
        self.mode_formula = ft.Text("", size=12.5, color=ACCENT_2, selectable=True)

        def _mode_hint(m: str) -> str:
            return {
                "fixed": "售价 = 固定价格（所有商品统一，忽略成本/倍率）",
                "multiplier": "售价 = 商品成本 × 价格倍率 + 固定加价",
                "free_shipping": "售价 = (商品成本 + 默认运费) × (1 + 期望利润%)",
                "commission": "售价 = (成本 + 固定加价) × 倍率 × (1+利润%) ÷ (1−平台佣金%)",
            }.get(m, "")

        def _save_mode(e):
            self.cfg.set("price_mode", e.control.value or "multiplier")
            save_config(self.cfg)
            self.mode_formula.value = _mode_hint(e.control.value or "multiplier")
            try:
                self._safe_update()
            except Exception:
                pass

        self.price_mode_dd = ft.Dropdown(
            label="价格模式", width=330, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "price_mode", "multiplier") or "multiplier"),
            options=[ft.dropdown.Option(k, OPTION_LABELS[k]) for k in
                     ("multiplier", "free_shipping", "commission", "fixed")],
            on_change=_save_mode)
        self.mode_formula.value = _mode_hint(str(getattr(self.cfg, "price_mode", "multiplier")))

        def _param(key, label, width=150, hint=""):
            ctl = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                               color=TEXT, value=str(getattr(self.cfg, key, "") or ""), tooltip=hint)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or "").strip())
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        self.p_fixed = _param("price_fixed", "固定价格（元，所有商品统一）", 200, "固定价格模式使用")
        self.p_mult = _param("price_multiplier", "价格倍率（如 2）", 150, "成本 × 倍率")
        self.p_add = _param("price_fixed_add", "固定加价（元）", 150, "在倍率价格上加一笔")
        self.p_express = _param("default_express_price", "默认运费（包邮模式）", 180, "包邮模式先把运费算进成本")
        self.p_commission = _param("platform_commission", "平台佣金 %", 140, "含佣金模式用")
        self.p_profit = _param("sales_profit", "期望利润 %", 140, "含佣金/包邮模式用")
        self.p_cost_default = _param("goods_cost", "默认商品成本（元）", 170, "试算默认值，可留 0")

        def _sync_mode_fields(mode: str):
            """按模式启用/禁用参数，避免用户填了不该填的字段。"""
            mode = str(mode or "multiplier")
            use = {
                "fixed": {"p_fixed"},
                "multiplier": {"p_mult", "p_add"},
                "free_shipping": {"p_express", "p_profit"},
                "commission": {"p_mult", "p_add", "p_commission", "p_profit"},
            }.get(mode, {"p_mult", "p_add"})
            for name, ctl in (("p_fixed", self.p_fixed), ("p_mult", self.p_mult),
                              ("p_add", self.p_add), ("p_express", self.p_express),
                              ("p_commission", self.p_commission), ("p_profit", self.p_profit)):
                try:
                    ctl.disabled = name not in use
                    ctl.opacity = 1.0 if name in use else 0.45
                except Exception:
                    pass
            try:
                self._safe_update()
            except Exception:
                pass

        def _on_mode(e):
            m = e.control.value or "multiplier"
            self.cfg.set("price_mode", m)
            save_config(self.cfg)
            self.mode_formula.value = _mode_hint(m)
            _sync_mode_fields(m)

        self.price_mode_dd.on_change = _on_mode
        self.price_mode_dd.width = 360

        mode_card = card(ft.Column([
            h1i(ft.Icons.CALCULATE, "计算方式"),
            muted("先选算法，只需填这一算法用得到的参数；灰色字段当前模式不使用"),
            ft.Row([self.price_mode_dd, self.mode_formula], spacing=14,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True),
            ft.Container(
                content=ft.Column([
                    ft.Text("固定价格", size=15, weight=ft.FontWeight.BOLD, color=ACCENT_2),
                    muted("选「固定价格」模式时：所有商品统一用这个价格，忽略成本/倍率"),
                    ft.Row([self.p_fixed], spacing=12, wrap=True),
                ], spacing=8),
                border=ft.border.all(1, ACCENT_2), border_radius=12, padding=14),
            ft.Row([self.p_mult, self.p_add, self.p_express], spacing=12, wrap=True),
            ft.Row([self.p_commission, self.p_profit, self.p_cost_default], spacing=12, wrap=True),
        ], spacing=10))
        _sync_mode_fields(str(getattr(self.cfg, "price_mode", "multiplier") or "multiplier"))

        # ---- 试算 ----
        try:
            _base_cost = float(self.cfg.goods_cost or 0)
        except Exception:
            _base_cost = 0.0
        self.base_input = ft.TextField(label="成本（试算）", value=str(_base_cost or 100),
                                       width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.price_out = ft.Text("—", color=ACCENT_2, size=16.5, weight=ft.FontWeight.BOLD, selectable=True)
        self.price_break = ft.Text("", size=12.5, color=MUTED, selectable=True)

        def calc(e=None):
            try:
                base = float(self.base_input.value or 0)
                r = compute_price(self.cfg, base)
                try:
                    _cr = float(getattr(self.cfg, "platform_commission", 0) or 0) / 100.0
                except Exception:
                    _cr = 0.0
                try:
                    _ex = float(getattr(self.cfg, "default_express_price", 0) or 0)
                except Exception:
                    _ex = 0.0
                _fee = round(r.final * _cr, 2)
                _net = round(r.final - base - _fee - _ex, 2)
                lines = [f"成本 ¥{base:.2f} → 售价 ¥{r.final:.2f}",
                         f"模式：{r.note or r.mode}"
                         + (f" · 阶梯倍率 {r.tier_multiplier:g}" if r.tier_multiplier else ""),
                         f"平台佣金 ¥{_fee:.2f} · 运费 ¥{_ex:.2f} · 净利润 ¥{_net:.2f}"]
                if r.regional:
                    lines.append("区域价：" + " · ".join(f"{k} {v:.2f}" for k, v in list(r.regional.items())[:6]))
                if r.wholesale:
                    lines.append("批发价：" + " · ".join(f"≥{w['min_qty']}件 ¥{w['price']}"
                                                        for w in r.wholesale[:4]))
                self.price_out.value = f"¥{r.final:.2f}"
                self.price_break.value = "\n".join(lines)
            except Exception as ex:
                self.price_out.value = "计算失败"
                self.price_break.value = str(ex)[:120]
            try:
                self._safe_update()
            except Exception:
                pass

        calc_card = card(ft.Column([
            h1i(ft.Icons.PRICE_CHECK, "价格试算"),
            muted("用的就是刊登时同一套算法（模式 → 阶梯 → 区域 → 批发）"),
            ft.Row([
                self.base_input,
                ft.TextField(label="平台佣金 %（试算）", width=160, bgcolor=CARD_2,
                             border_color=CARD_2, color=TEXT,
                             value=str(getattr(self.cfg, "platform_commission", "0") or "0"),
                             on_change=lambda e: (self.cfg.set("platform_commission",
                                                              str(e.control.value or "0")),
                                                  save_config(self.cfg))),
                ft.ElevatedButton("计算", icon=ft.Icons.CALCULATE, bgcolor=ACCENT,
                                  color=ON_ACCENT, on_click=calc),
            ], spacing=10, wrap=True),
            self.price_out, self.price_break,
        ], spacing=8))

        # ---- 阶梯价格表格 ----
        tier_state = {"rows": tiers_to_rows(getattr(self.cfg, "tuoguan_price_tiers", "[]"))}
        tier_col = ft.Column(spacing=6)
        tier_status = ft.Text("", size=12.5, color=MUTED)

        def tier_persist():
            self.cfg.set("tuoguan_price_tiers", rows_to_tiers(tier_state["rows"]))
            save_config(self.cfg)
            tier_status.value = (f"已保存 {len(tier_state['rows'])} 档（按成本区间套倍率）"
                                 if tier_state["rows"] else "未设置阶梯：全部按上面的价格倍率")
            tier_status.color = ACCENT_2 if tier_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def tier_row(row: dict):
            lo = ft.TextField(label="成本下限", value=f"{row.get('min', 0):g}", width=110, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            hi = ft.TextField(label="成本上限", value=f"{row.get('max', 0):g}", width=110, dense=True,
                              bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            mult = ft.TextField(label="倍率", value=f"{row.get('multiplier', 1):g}", width=90, dense=True,
                                bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
            out = ft.Text("", size=12.5, color=ACCENT_2, width=110)

            def sync(e=None):
                try:
                    row["min"] = float(lo.value or 0)
                    row["max"] = float(hi.value or 1e18)
                    row["multiplier"] = float(mult.value or 1)
                except Exception:
                    return
                try:
                    out.value = f"成本 {row['min']:g} → ¥{row['min'] * row['multiplier']:g}"
                except Exception:
                    out.value = ""
                tier_persist()

            for c in (lo, hi, mult):
                c.on_change = sync
            try:
                out.value = f"成本 {row['min']:g} → ¥{row['min'] * row['multiplier']:g}"
            except Exception:
                pass

            def remove(e):
                try:
                    tier_state["rows"].remove(row)
                except ValueError:
                    pass
                tier_render(); tier_persist()

            return ft.Row([lo, hi, mult, out,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=DANGER_C, on_click=remove)],
                          spacing=8, wrap=True)

        def tier_render():
            tier_col.controls = [tier_row(r) for r in tier_state["rows"]] or [
                empty_state("还没有阶梯。点「添加一档」，例如 0-100 用 1.5 倍、100-1000 用 1.35 倍。")]
            try:
                self._safe_update()
            except Exception:
                pass

        def tier_add(e=None):
            tier_state["rows"].append({"min": 0 if not tier_state["rows"] else
                                       tier_state["rows"][-1].get("max", 100), "max": 100, "multiplier": 1.35})
            tier_render(); tier_persist()

        def tier_template(e):
            tier_state["rows"] = [{"min": 0, "max": 100, "multiplier": 1.5},
                                  {"min": 100, "max": 500, "multiplier": 1.4},
                                  {"min": 500, "max": 100000, "multiplier": 1.3}]
            tier_render(); tier_persist()

        tier_render()
        tier_card = card(ft.Column([
            h1i(ft.Icons.STAIRS, "阶梯倍率（按成本区间）"),
            muted("勾选后：成本落在哪一档就用该档倍率，会覆盖上面的「价格倍率」"),
            ft.Switch(label="启用阶梯倍率", value=bool(getattr(self.cfg, "tuoguan_price_tiered_enabled", False)),
                      active_color=ACCENT,
                      on_change=lambda e: (self.cfg.set("tuoguan_price_tiered_enabled", bool(e.control.value)),
                                           save_config(self.cfg), calc())),
            ft.Row([ft.ElevatedButton("添加一档", icon=ft.Icons.ADD, bgcolor=ACCENT, color=ON_ACCENT, on_click=tier_add),
                    ft.OutlinedButton("套用示例（3 档）", icon=ft.Icons.AUTO_AWESOME, on_click=tier_template)],
                   spacing=10, wrap=True),
            tier_col, tier_status,
        ], spacing=8))

        # ---- 区域调价（店小秘风格：按国家设绝对价）----
        def _rq_load():
            raw = getattr(self.cfg, "regional_quotes", "[]")
            if isinstance(raw, str):
                try:
                    import json as _jq
                    raw = _jq.loads(raw)
                except Exception:
                    raw = []
            return [dict(r) for r in (raw or []) if isinstance(r, dict)]

        rq_state = {"rows": _rq_load()}
        rq_col = ft.Column(spacing=6)
        rq_status = ft.Text("", size=12.5, color=MUTED)

        def rq_persist():
            import json as _jq
            self.cfg.set("regional_quotes", _jq.dumps(rq_state["rows"], ensure_ascii=False))
            save_config(self.cfg)
            rq_status.value = (f"已设置 {len(rq_state['rows'])} 个国家/地区"
                               if rq_state["rows"] else "未设置区域价：开启「启用区域调价」后生效")
            rq_status.color = ACCENT_2 if rq_state["rows"] else MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            calc()

        def rq_row(row: dict):
            cur = ft.Dropdown(label="国家/地区", width=170, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                              value=str(row.get("country") or "US"),
                              options=[ft.dropdown.Option(c, f"{c} · {n}") for c, n in REGIONS])
            price = ft.TextField(label="绝对价（元）", value=str(row.get("price", 0)), width=130,
                                 bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

            def sync(e=None):
                row["country"] = cur.value or "US"
                try:
                    row["price"] = float(price.value or 0)
                except Exception:
                    return
                rq_persist()

            cur.on_change = sync
            price.on_change = sync

            def remove(e):
                try:
                    rq_state["rows"].remove(row)
                except ValueError:
                    pass
                rq_render(); rq_persist()

            return ft.Row([cur, price,
                           ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=DANGER_C, on_click=remove)],
                          spacing=8, wrap=True)

        def rq_render():
            rq_col.controls = [rq_row(r) for r in rq_state["rows"]] or [
                empty_state("还没有区域价。点「添加国家」，例如 US 12.99、RU 15.99。")]
            try:
                self._safe_update()
            except Exception:
                pass

        def rq_add(e=None):
            used = {r.get("country") for r in rq_state["rows"]}
            nxt = next((c for c, _ in REGIONS if c not in used), "US")
            rq_state["rows"].append({"country": nxt, "price": 0})
            rq_render(); rq_persist()

        rq_render()
        reg_card = card(ft.Column([
            h1i(ft.Icons.PUBLIC, "区域调价"),
            muted("按国家/地区设置绝对成交价（店小秘同款：选中哪个国家就用哪个价）"),
            ft.Switch(label="启用区域调价", value=bool(getattr(self.cfg, "regional_quote_enable", False)),
                      active_color=ACCENT,
                      on_change=lambda e: (self.cfg.set("regional_quote_enable", bool(e.control.value)),
                                           save_config(self.cfg))),
            ft.Row([ft.ElevatedButton("添加国家", icon=ft.Icons.ADD, bgcolor=ACCENT, color=ON_ACCENT, on_click=rq_add)],
                   spacing=10, wrap=True),
            rq_col, rq_status,
        ], spacing=8))

        # ---- 店小秘区域调价模板（搜索/选用）----
        self.tpl_kw = ft.TextField(label="模板关键词", width=200, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.tpl_list = ft.Column(spacing=4)
        self.tpl_status = ft.Text("", size=13.5, color=MUTED)
        self.tpl_selected = ft.Text("", size=12.5, color=ACCENT_2)

        def search(e):
            self.tpl_status.value = "搜索中…"
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._search_templates, daemon=True).start()

        def clear_selected(e):
            self.cfg.set("adjust_price_template_id", "")
            self.cfg.set("adjust_price_template_name", "")
            save_config(self.cfg)
            self.tpl_selected.value = "未选择店小秘模板"
            self._safe_update()

        self.tpl_selected.value = ("已选店小秘模板：" + str(self.cfg.adjust_price_template_name or "-"))
        tpl_card = card(ft.Column([
            h1("店小秘区域调价模板", 15),
            muted("从店小秘读取已建好的调价模板并选用；模板 ID/名称会记录到配置里用于对账"),
            ft.Row([self.tpl_kw, ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, on_click=search),
                    ft.OutlinedButton("清除已选", on_click=clear_selected)], spacing=10, wrap=True),
            self.tpl_selected, self.tpl_status, self.tpl_list,
        ], spacing=8))

        # ---- 批发（店小秘风格：购买 N 件减免 X% + 一件代发折扣）----
        def _num_field(key, label, width, tooltip):
            ctl = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                               color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                               tooltip=tooltip)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or "").strip())
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        self.bulk_order = _num_field("bulk_order", "购买 N 件以上", 140, "2~100000")
        self.bulk_discount = _num_field("bulk_discount", "减免 X%", 110, "1~99")
        self.dropship_discount = _num_field("dropship_discount", "一件代发折扣 Y%", 150, "0~99")

        ws_card = card(ft.Column([
            h1i(ft.Icons.INVENTORY_2, "批发"),
            muted("店小秘同款：勾选后，买家一次购买满 N 件自动减免 X%"),
            ft.Switch(label="启用批发", value=bool(getattr(self.cfg, "wholesale_enable", False)),
                      active_color=ACCENT,
                      on_change=lambda e: (self.cfg.set("wholesale_enable", bool(e.control.value)),
                                           save_config(self.cfg))),
            ft.Row([self.bulk_order, self.bulk_discount, self.dropship_discount], spacing=12, wrap=True),
        ], spacing=8))

        # ---- 本地区域模板（保存/套用倍数表）----
        self.lib_name = ft.TextField(label="模板名称", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.lib_dd = ft.Dropdown(width=220, bgcolor=CARD_2, color=TEXT, border_color=CARD_2, options=[])

        def _lib_refresh():
            lib = self.cfg.price_template_library or []
            if not isinstance(lib, list):
                lib = []
            self.lib_dd.options = [ft.dropdown.Option(str(t.get("name"))) for t in lib if isinstance(t, dict)]
            try:
                self._safe_update()
            except Exception:
                pass

        def lib_save(e):
            name = (self.lib_name.value or "").strip()
            if not name:
                self.toast("请输入模板名称", False); return
            rates = {r.get("country"): r.get("price") for r in rq_state["rows"] if r.get("country")}
            lib = [t for t in (self.cfg.price_template_library or [])
                   if isinstance(t, dict) and t.get("name") != name]
            lib.append({"name": name, "rates": rates})
            self.cfg.price_template_library = lib
            save_config(self.cfg)
            _lib_refresh()
            self.toast(f"已保存模板：{name}（{len(rates)} 个币种）")

        def lib_apply(e):
            name = self.lib_dd.value
            for t in (self.cfg.price_template_library or []):
                if isinstance(t, dict) and t.get("name") == name:
                    rq_state["rows"] = [{"country": k, "price": v}
                                        for k, v in (t.get("rates") or {}).items()]
                    rq_render(); rq_persist()
                    self.toast(f"已套用模板：{name}")
                    return
            self.toast("未找到模板", False)

        def lib_del(e):
            name = self.lib_dd.value
            self.cfg.price_template_library = [t for t in (self.cfg.price_template_library or [])
                                              if not (isinstance(t, dict) and t.get("name") == name)]
            save_config(self.cfg)
            _lib_refresh()
            self.toast("已删除")

        _lib_refresh()
        lib_card = card(ft.Column([h1("区域倍率模板", 15),
                                   muted("把当前币种倍率存成模板，换店铺/换品类一键套用"),
                                   ft.Row([self.lib_name,
                                           ft.ElevatedButton("保存当前", icon=ft.Icons.SAVE, bgcolor=CARD_2,
                                                             color=TEXT, on_click=lib_save),
                                           self.lib_dd,
                                           ft.ElevatedButton("应用", icon=ft.Icons.CHECK, bgcolor=ACCENT,
                                                             color=ON_ACCENT, on_click=lib_apply),
                                           ft.OutlinedButton("删除", on_click=lib_del)], spacing=10, wrap=True)],
                                 spacing=8))

        try:
            calc()          # 进页面就按当前配置试算一次，别让结果一直显示「—」
        except Exception:
            pass
        return [page_header("价格", destination(6).description),
                workspace_tabs([
                    ("定价与试算", [mode_card, calc_card]),
                    ("阶梯与批发", [tier_card, ws_card]),
                    ("区域调价", [reg_card, tpl_card]),
                    ("价格模板库", [lib_card]),
                ])]

    def _search_templates(self):
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            data = client.search_price_templates(self.tpl_kw.value or "")
            # 兼容 list / dict 两种返回（旧代码按 dict 取，必崩）
            if isinstance(data, dict):
                inner = data.get("data") or data
                items = inner.get("list") if isinstance(inner, dict) else inner
            else:
                items = data
            items = items or []
            rows = []
            for t in items[:30]:
                if not isinstance(t, dict):
                    continue
                name = t.get("name") or t.get("templateName") or str(t.get("id", ""))
                tid = str(t.get("id", ""))

                def apply(e, _tid=tid, _name=name):
                    self.cfg.set("adjust_price_template_id", _tid)
                    self.cfg.set("adjust_price_template_name", _name)
                    save_config(self.cfg)
                    self.tpl_selected.value = f"已选店小秘模板：{_name}（{_tid}）"
                    self.toast(f"已应用模板：{_name}")
                    try:
                        self._safe_update()
                    except Exception:
                        pass

                rows.append(ft.ListTile(title=ft.Text(name, size=14.5, color=TEXT),
                                        subtitle=ft.Text(tid, size=12, color=MUTED), on_click=apply))
            self.tpl_list.controls = rows or [ft.Text("没有找到模板（可先在店小秘里创建区域调价模板）", size=13.5, color=WARN_C)]
            self.tpl_status.value = f"共 {len(rows)} 个模板"
        except Exception as ex:
            self.tpl_status.value = f"搜索失败：{str(ex)[:120]}"
            self.tpl_list.controls = []
        try:
            self._safe_update()
        except Exception:
            pass

    def _p_variant(self):
        """变种与库存：按店小秘的方式展示——编码规则 / 库存 / 属性映射 / 价格来源。"""
        self._current = 7
        self.sku_preview = ft.Text("", size=13.5, color=ACCENT_2, selectable=True)

        def _refresh_preview(e=None):
            pre = str(getattr(self.cfg, "custom_sku_prefix", "") or "").strip()
            ch = str(getattr(self.cfg, "sku_split_char", "-") or "-")
            gen = bool(getattr(self.cfg, "auto_generate_sku_code", False))
            only = bool(getattr(self.cfg, "only_sku_prefix_checkbox", False))
            on = bool(getattr(self.cfg, "custom_sku_prefix_enable", True))
            if not on or not pre:
                sample = "SKU-001" if gen else "沿用采集到的 SKU 编码"
            elif only:
                sample = f"{pre}{ch}001"
            elif gen:
                sample = f"{pre}{ch}001"
            else:
                sample = f"{pre}{ch}RED-M"
            self.sku_preview.value = f"编码示例：{sample}"
            try:
                self._safe_update()
            except Exception:
                pass

        def _sw(key, label, tip=""):
            c = ft.Switch(label=label, value=bool(getattr(self.cfg, key, False)),
                          active_color=ACCENT, tooltip=tip or None)

            def on(e, _k=key):
                self.cfg.set(_k, bool(e.control.value))
                save_config(self.cfg)
                _refresh_preview()
            c.on_change = on
            return c

        def _tf(key, label, width=200, tip=""):
            c = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                             color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                             tooltip=tip or None)

            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or "").strip())
                save_config(self.cfg)
                _refresh_preview()
            c.on_change = on
            return c

        code_card = card(ft.Column([
            h1i(ft.Icons.QR_CODE_2, "SKU 编码规则"),
            _sw("write_sku_codes_to_product", "SKU 编码写回商品",
                "关闭时保留商品原编码；名称、图片和库存设置分别处理"),
            muted("店小秘同款：前缀 + 分隔符 + 序号/原编码；下面的示例就是最终提交的编码"),
            ft.Row([_sw("custom_sku_prefix_enable", "启用自定义前缀"),
                    _tf("custom_sku_prefix", "前缀", 150, "例如 MY"),
                    _tf("sku_split_char", "分隔符", 90, "默认 -")], spacing=12, wrap=True),
            ft.Row([_sw("auto_generate_sku_code", "自动生成编码（前缀-001）"),
                    _sw("only_sku_prefix_checkbox", "只用前缀（丢掉原编码）")], spacing=12, wrap=True),
            self.sku_preview,
        ], spacing=10))

        self.stock_tf = _tf("stock_quantity", "库存数量", 150, "所有 SKU 统一用这个库存")
        self.stock_tf.keyboard_type = ft.KeyboardType.NUMBER
        stock_card = card(ft.Column([
            h1i(ft.Icons.WAREHOUSE, "库存"),
            muted("所有变种统一库存；不填则保留采集到的库存"),
            ft.Row([self.stock_tf], spacing=12, wrap=True),
        ], spacing=8))

        attr_card = card(ft.Column([
            h1i(ft.Icons.TUNE, "变种属性"),
            muted("属性来自采集数据，软件按名称自动对齐到站点的变种属性（Color / Size 等）"),
            ft.Column([
                ft.Text("· 名称完全一致 → 直接对齐", size=12.5, color=TEXT),
                ft.Text("· 包含关系（如 “Color” ↔ “Colour”）→ 视为同一属性", size=12.5, color=TEXT),
                ft.Text("· 相似度 ≥ 50% → 自动映射；低于 50% 保留原值并在日志提示", size=12.5, color=TEXT),
            ], spacing=2),
            muted("多 SKU 商品的变种会在提交时逐个校验，缺属性会在日志里点名"),
        ], spacing=8))

        try:
            from ..services.pricing import compute_price
            _r = compute_price(self.cfg, float(getattr(self.cfg, "goods_cost", 0) or 100) or 100)
            _price_line = f"当前模式：{OPTION_LABELS.get(str(self.cfg.price_mode), self.cfg.price_mode)} → 成本 100 试算 ¥{_r.final:.2f}"
        except Exception:
            _price_line = "价格由「价格」页统一计算"
        price_card = card(ft.Column([
            h1i(ft.Icons.SELL_OUTLINED, "变种价格"),
            muted("所有变种共用同一套价格算法（不在这里单独定价）"),
            ft.Text(_price_line, size=13.5, color=ACCENT_2, selectable=True),
            ft.TextButton("去价格页", icon=ft.Icons.ARROW_FORWARD,
                          on_click=lambda e: self._switch(6)),
        ], spacing=8))

        _refresh_preview()
        replace_card = card(ft.Column([
            h1i(ft.Icons.SWAP_HORIZ, "SKU 图片替换"),
            muted("按 SKU 名称匹配，将变种图片替换为指定 URL。"),
            ft.OutlinedButton("打开替换表", icon=ft.Icons.SWAP_HORIZ,
                              on_click=lambda e: self._sku_replace_dialog()),
        ], spacing=10))
        return [page_header("变种与库存", destination(7).description),
                workspace_tabs([
                    ("编码与库存", [code_card, stock_card]),
                    ("变种属性与图片", [attr_card, replace_card, price_card]),
                    ("套装 SKU", [self._combo_card()]),
                ])]

    def _p_publish(self):
        """发布队列：待发布 / 发布失败 / 重发动作，以及发布相关设置。"""
        self._current = 8
        cnt = getattr(self, "store_counts", {}) or {}
        self.pub_wait_txt = ft.Text(str(cnt.get("editingRequired", "—")), size=23,
                                    weight=ft.FontWeight.BOLD, color=INFO_C)
        self.pub_fail_txt = ft.Text(str(cnt.get("publishFail", "—")), size=23,
                                    weight=ft.FontWeight.BOLD, color=DANGER_C)
        self.pub_publishing_txt = ft.Text(str(cnt.get("publishing", "—")), size=23,
                                          weight=ft.FontWeight.BOLD, color=ACCENT_2)


        def refresh_counts():
            def go():
                try:
                    from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
                    c = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
                    c2 = c.counts(self.cfg.store_id or None, ttl=30)
                    self.store_counts = c2 or {}
                    self.pub_wait_txt.value = str((c2 or {}).get("editingRequired", 0))
                    self.pub_fail_txt.value = str((c2 or {}).get("publishFail", 0))
                    self.pub_publishing_txt.value = str((c2 or {}).get("publishing", 0))
                    self._safe_update()
                except Exception:
                    pass
            threading.Thread(target=go, daemon=True).start()

        queue_card = card(ft.Column([
            h1i(ft.Icons.QUEUE, "队列"),
            muted("商品保存后会进入「待发布」，提交发布后进入「发布中 → 审核中」"),
            ft.Row([kpi_tile("待发布", self.pub_wait_txt, "可直接发布", INFO_C),
                    kpi_tile("发布失败", self.pub_fail_txt, "可重试", DANGER_C),
                    kpi_tile("发布中", self.pub_publishing_txt, "等待平台审核", ACCENT_2)],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.ElevatedButton("刷新数量", icon=ft.Icons.REFRESH, bgcolor=CARD_2, color=TEXT,
                                  on_click=lambda e: refresh_counts()),
                ft.OutlinedButton("看待发布列表", icon=ft.Icons.LIST_ALT,
                                  on_click=lambda e: self._publish_list_dialog("waitPublish", "待发布商品")),
                ft.OutlinedButton("看发布失败列表", icon=ft.Icons.LIST_ALT,
                                  on_click=lambda e: self._publish_list_dialog("publishFail", "发布失败商品")),
                ft.ElevatedButton("批量发布「待发布」", icon=ft.Icons.PUBLISH,
                                  bgcolor=ACCENT, color=ON_ACCENT,
                                  on_click=lambda e: self._confirm(
                                      "批量发布「待发布」商品？",
                                      "会把店小秘「待发布」里的商品全部提交发布，"
                                      "提交后进入「发布中 → 审核中」，短时间内不可撤销。",
                                      lambda: threading.Thread(
                                          target=self._batch_publish_waiting, daemon=True).start())),
                ft.OutlinedButton("重试「发布失败」", icon=ft.Icons.REPLAY,
                                  on_click=lambda e: self._confirm(
                                      "重试「发布失败」的商品？",
                                      "会重新提交店小秘里发布失败的商品；不调用 AI，不产生费用。",
                                      lambda: threading.Thread(
                                          target=self._batch_publish_failed, daemon=True).start())),
            ], spacing=10, wrap=True),
        ], spacing=10))

        refresh_counts()
        return [page_header("发布队列", destination(8).description),
                workspace_tabs([
                    ("运行与发布设置", [self._card_run(), self._card_publish()]),
                    ("商品队列", [queue_card]),
                ])]

    def _p_workbench(self):
        self._current = 9
        ok, ck_text = self._cookie_status_text()

        def _step(n, label):
            return ft.Container(
                width=132,
                content=ft.Row([ft.Container(width=18, height=18, border_radius=9,
                                             bgcolor=CARD_2, alignment=ft.alignment.center,
                                             content=ft.Text(str(n), size=12, color=MUTED)),
                                ft.Text(label, size=13, color=MUTED, no_wrap=True)], spacing=6),
                padding=ft.padding.symmetric(horizontal=10, vertical=5),
                bgcolor=CARD, border_radius=999, border=ft.border.all(1, CARD_2))

        self.wb_steps = ft.Row([
            _step(1, "连接"), ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14.5, color=MUTED),
            _step(2, "选商品"), ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14.5, color=MUTED),
            _step(3, "取图并处理"), ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14.5, color=MUTED),
            _step(4, "任务队列"),
        ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.wb_conn = ft.Text(("Cookie：" + ck_text), size=13.5,
                               color=ACCENT_2 if ok else DANGER_C)
        self.wb_conn_hint = muted("加载不了产品时先看这里：没有 Cookie 就点「粘贴 Cookie」或「启动浏览器」")
        self.wb_status = ft.Text("未加载产品", color=MUTED, size=13.5)
        self.wb_results = ft.Column(spacing=4)
        self.wb_wall = ft.Row(spacing=8, wrap=True)
        self.wb_sel_paths: set[str] = set()
        self.wb_busy = ft.ProgressRing(width=16, height=16, visible=False, color=ACCENT)
        self.wb_urls = ft.TextField(label="图片 URL（每行一个，也可从产品取图）", multiline=True,
                                    min_lines=3, max_lines=6, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_index = ft.TextField(label="只处理第N张（留空=全部）", width=160,
                                       bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
                                       value="", tooltip="只对选中图片里的第 N 张做 AI 编辑")
        self.wb_prompt = ft.TextField(label="「编辑」提示词（中文/英文均可；逐张不同请到「AI图片处理 → 精细任务」）",
                                      value=self.cfg.edit_prompt or "", multiline=True, min_lines=2, max_lines=3,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        # ---- 连接 ----
        def recheck(e):
            def go():
                ok2, txt2 = self._cookie_status_text(refresh=True)
                self.wb_conn.value = "Cookie：" + txt2
                self.wb_conn.color = ACCENT_2 if ok2 else DANGER_C
                if ok2:
                    self._wb_check_login()
                else:
                    self.toast("仍未读到 Cookie，请粘贴 Cookie", False)
            threading.Thread(target=go, daemon=True).start()

        conn_card = card(ft.Column([
            h1("1 · 连接", 15),
            ft.Row([self.wb_conn,
                    ft.OutlinedButton("重新检测 Cookie", icon=ft.Icons.REFRESH, on_click=recheck),
                    ft.OutlinedButton("粘贴 Cookie", icon=ft.Icons.CONTENT_PASTE, on_click=lambda e: self._paste_cookie()),
                    ft.OutlinedButton("打开店小秘", icon=ft.Icons.LINK,
                                      on_click=lambda e: self._open_dxm_page())], spacing=8, wrap=True),
            self.wb_conn_hint,
                ], spacing=8))

        # ---- 产品列表 ----
        self.wb_status_sel = ft.Dropdown(
            value="editingRequired", width=150, bgcolor=CARD_2, color=TEXT, border_color=CARD_2,
            options=[ft.dropdown.Option("draft", "采集箱"),
                     ft.dropdown.Option("editingRequired", "待发布"),
                     ft.dropdown.Option("publishFail", "发布失败"),
                     ft.dropdown.Option("onSelling", "在线"),
                     ft.dropdown.Option("auditing", "审核中"),
                     ft.dropdown.Option("offline", "已下架"),
                     ft.dropdown.Option("", "全部")])
        self.wb_kw = ft.TextField(label="搜索标题", width=220, bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.wb_sort_dd = ft.Dropdown(
            label="本页排序", width=160, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self, "wb_sort", "default") or "default"),
            options=[ft.dropdown.Option("default", "默认（最新在前）"),
                     ft.dropdown.Option("img_desc", "图多 → 少"),
                     ft.dropdown.Option("img_asc", "图少 → 多")])
        self.wb_page_no = 1
        self.wb_page_info = ft.Text("", size=13.5, color=MUTED)
        self.wb_sel_text = ft.Text("已选 0 个产品 / 0 张图", size=13.5, color=MUTED)
        self.wb_selected: set[str] = set()
        self.wb_rows: list[dict] = []
        self.wb_prod_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("选", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("图", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("产品", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("状态", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("图/SKU", size=12.5, color=MUTED))],
            rows=[], column_spacing=16, heading_row_height=28, data_row_min_height=32)
        self.wb_prod_preview = ft.Row(spacing=6, wrap=True)
        self.wb_prod_box = ft.Column([self.wb_prod_table], scroll=ft.ScrollMode.AUTO, height=300)

        def load_products(e=None):
            self.wb_page_no = 1
            self._wb_load_products(1)

        def prev_page(e):
            if self.wb_page_no > 1:
                self.wb_page_no -= 1
                self._wb_load_products(self.wb_page_no)

        def next_page(e):
            if self.wb_page_no >= int(getattr(self, "wb_total_pages", 1) or 1):
                self.toast("已经是最后一页", False)
                return
            self.wb_page_no += 1
            self._wb_load_products(self.wb_page_no)

        def select_all(e):
            val = bool(e.control.value)
            for it in self.wb_rows:
                pid = str(it.get("id") or "")
                if val:
                    self.wb_selected.add(pid)
                else:
                    self.wb_selected.discard(pid)
            self._wb_sel_refresh()

        def pick_images(e):
            self._wb_pick_images()

        prod_card = card(ft.Column([
            h1("2 · 产品", 15),
            muted("先「加载」产品，再勾选产品 → 取图；也可以直接粘贴图片 URL"),
            ft.Row([self.wb_status_sel, self.wb_kw,
                    ft.ElevatedButton("加载", icon=ft.Icons.SEARCH, bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=load_products),
                    ft.Checkbox(label="只看有图", value=bool(getattr(self, "wb_only_img", False)),
                                on_change=lambda e: self._wb_toggle_only_img(e.control.value)),
                    self.wb_sort_dd],
                   spacing=8, wrap=True),
            ft.Row([ft.OutlinedButton("上一页", on_click=prev_page),
                    ft.OutlinedButton("下一页", on_click=next_page),
                    self.wb_page_info], spacing=8, wrap=True),
            ft.Row([ft.Checkbox(label="全选本页", on_change=select_all), self.wb_sel_text,
                    ft.ElevatedButton("取选中图片", icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, bgcolor=ACCENT_2,
                                      color=ON_ACCENT, on_click=pick_images)], spacing=10, wrap=True),
            self.wb_prod_box,
            muted("选中产品的图片预览（点图放大）", 10.5),
            self.wb_prod_preview,
        ], spacing=8))

        # ---- 图片处理 ----
        def run(kind):
            if not self.picked:
                self.toast("请先取图：加载产品 → 勾选 → 取选中图片", False); return
            if kind != "add_watermark" and not self.cfg.api_key:
                self.toast("请先设置 API Key", False); return
            self.wb_busy.visible = True
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._wb_process, args=(kind,), daemon=True).start()

        def four_in_one(e):
            self._four_in_one_local()

        def make_video(e):
            threading.Thread(target=self._wb_make_video, daemon=True).start()

        def upload(e):
            threading.Thread(target=self._upload_results, daemon=True).start()

        def cleanup(e):
            self._open_cleanup_dialog()

        def fill_from_products(e):
            self._images_from_products()

        img_card = card(ft.Column([
            h1("3 · 图片处理", 15),
            muted("简单处理：白底 / 去水印 / 翻译 / 四合一；精细处理：填提示词后点「编辑」"),
            muted("① 单张处理（只对勾选的图生效）", 10.5),
            ft.Row([ft.ElevatedButton("白底", icon=ft.Icons.WB_SUNNY_OUTLINED,
                                      bgcolor=ACCENT, color=ON_ACCENT, on_click=lambda e: run("white")),
                    ft.ElevatedButton("去水印", icon=ft.Icons.AUTO_FIX_HIGH,
                                      bgcolor=ACCENT_2, color=ON_ACCENT, on_click=lambda e: run("watermark")),
                    ft.OutlinedButton("加水印", icon=ft.Icons.BRANDING_WATERMARK,
                                      on_click=lambda e: run("add_watermark")),
                    ft.OutlinedButton("翻译成英文", icon=ft.Icons.TRANSLATE,
                                      on_click=lambda e: run("translate")),
                    ft.OutlinedButton("按提示词编辑", icon=ft.Icons.EDIT,
                                      on_click=lambda e: run("edit")),
                    self.wb_busy], spacing=8, wrap=True),
            muted("② 组合与输出", 10.5),
            ft.Row([ft.OutlinedButton("四合一拼图", icon=ft.Icons.GRID_VIEW, on_click=four_in_one),
                    ft.OutlinedButton("生成视频", icon=ft.Icons.MOVIE, on_click=make_video),
                    ft.OutlinedButton("上传结果到相册", icon=ft.Icons.CLOUD_UPLOAD, on_click=upload)],
                   spacing=8, wrap=True),
            muted("③ 维护", 10.5),
            ft.Row([ft.OutlinedButton("清理旧图 / 空间", icon=ft.Icons.DELETE_SWEEP, on_click=cleanup)],
                   spacing=8, wrap=True),
            ft.Row([self.wb_urls,
                    ft.OutlinedButton("加载图片", icon=ft.Icons.DOWNLOAD, on_click=lambda e: self._wb_load_urls()),
                    ft.OutlinedButton("从已拉取产品取图", icon=ft.Icons.INVENTORY_2, on_click=fill_from_products)],
                   spacing=8, wrap=True),
            self.wb_index, self.wb_prompt,
            ft.Row([ft.Text("图片墙：点勾选要处理的图（默认全选）", size=12.5, color=MUTED),
                    self.wb_pick_summary], spacing=10),
            self.wb_wall,
            self.wb_results,
        ], spacing=8))

        # ---- 队列 ----
        self.q = TaskQueue()
        self.q_text = ft.Text("", size=13.5, color=MUTED)
        self._refresh_queue()

        def enqueue(e):
            pool = {}
            for p in (self.products or []):
                pool[str(p.get("id"))] = p
            for p in (getattr(self, "wb_rows", None) or []):
                pool.setdefault(str(p.get("id")), p)
            if not pool:
                self.toast("没有可入队的产品：先「加载」产品列表", False)
                return
            chosen = ([pool[str(pid)] for pid in self.wb_selected if str(pid) in pool]
                      if self.wb_selected else list(pool.values()))
            before = len(chosen)
            chosen = [p for p in chosen if str(p.get("status_type") or "") not in
                      ("auditing", "onSelling", "published", "online")
                      and "PRODUCT_IN_AUDIT_PENDING" not in str((p.get("raw") or {}).get("errMsg") or "")]
            try:
                from ..services import edited as _edited
                chosen, _ = _edited.filter_new(chosen)
            except Exception:
                pass
            skipped = before - len(chosen)
            self._queue_products = pool
            n = self.q.add_many(chosen)
            self._refresh_queue()
            self.toast(f"已入队 {n} 个产品" + (f"，跳过审核中/已上架 {skipped} 个" if skipped else ""))
            self.wb_tabs.selected_index = 2
            self._safe_update()

        def process(e):
            if not self.q.pending():
                self.toast("队列为空", False); return
            threading.Thread(target=self._process_queue, daemon=True).start()

        self.q_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        q_card = card(ft.Column([h1("4 · 任务队列", 15), self.q_text, self.q_progress,
                                 ft.Row([ft.ElevatedButton("产品入队", icon=ft.Icons.ADD_TASK, on_click=enqueue),
                                         ft.ElevatedButton("处理队列", icon=ft.Icons.PLAY_ARROW,
                                                           bgcolor=ACCENT, color=ON_ACCENT, on_click=process),
                                         ft.OutlinedButton("清理已完成",
                                                           on_click=lambda e: (self.q.clear_done(), self._refresh_queue())),
                                         ft.OutlinedButton("重试失败", icon=ft.Icons.REPLAY,
                                                           on_click=lambda e: self._retry_failed_queue())],
                                        spacing=10, wrap=True)], spacing=8))
        self.wb_tabs = workspace_tabs([
            ("商品与取图", [conn_card, prod_card]),
            ("图片处理与输出", [img_card]),
            ("任务队列", [q_card]),
        ])
        return [page_header("图片工作台", destination(9).description),
                self.wb_steps, self.wb_status, self.wb_tabs]

    # ---------- 工作台内部 ----------
    def _open_dxm_page(self):
        url = "https://www.dianxiaomi.com/web/smt/smtProductList/offline"
        try:
            self.page.launch_url(url)
        except Exception:
            self.toast(f"请在浏览器打开 {url}", False)

    def _wb_check_login(self):
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            info = dxm.user_info()
            self.wb_conn.value = f"已登录：{info.get('account') or info.get('id') or '-'}"
            self.wb_conn.color = ACCENT_2
        except Exception as ex:  # noqa: BLE001
            self.wb_conn.value = f"登录检测失败：{str(ex)[:80]}"
            self.wb_conn.color = DANGER_C
        try:
            self._safe_update()
        except Exception:
            pass

    def _wb_toggle_only_img(self, val: bool):
        self.wb_only_img = bool(val)
        try:
            self._wb_load_products(self.wb_page_no or 1)
        except Exception:
            pass

    def _wb_load_products(self, page: int = 1):
        self.wb_status.value = f"加载第 {page} 页…"
        self.wb_status.color = MUTED
        try:
            self._safe_update()
        except Exception:
            pass

        def work():
            try:
                ck = self._cookies()
                dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
                status = self.wb_status_sel.value or None
                kw = (self.wb_kw.value or "").strip() or None
                sid = getattr(self.cfg, "shop_id", "") or self.cfg.store_id or None
                items, meta = dxm.product_page(page, 30, sid, status, kw)
                items = items or []
                self.wb_sort = str(getattr(self.wb_sort_dd, "value", "default") or "default")
                if self.wb_sort == "img_desc":
                    items.sort(key=lambda x: len(x.get("images") or []), reverse=True)
                elif self.wb_sort == "img_asc":
                    items.sort(key=lambda x: len(x.get("images") or []))
                self.wb_rows = items
                rows = []
                for it in self.wb_rows:
                    if getattr(self, "wb_only_img", False) and not (it.get("images") or []):
                        continue
                    pid = str(it.get("id") or "")
                    cb = ft.Checkbox(value=pid in self.wb_selected,
                                     on_change=lambda e, _p=pid: self._wb_toggle(_p, e.control.value))
                    _imgs = it.get("images") or []
                    rows.append(ft.DataRow(cells=[
                        ft.DataCell(cb),
                        ft.DataCell(self._zoom_img(_imgs[0] if _imgs else "", 44, 44, "产品主图")),
                        ft.DataCell(ft.Text((it.get("title") or "")[:48] or pid, size=12.5, color=TEXT)),
                        ft.DataCell(ft.Text(str(it.get("status") or ""), size=12, color=MUTED)),
                        ft.DataCell(ft.Text(f"{len(it.get('images') or [])}/{len(it.get('skus') or [])}",
                                            size=12, color=MUTED)),
                    ]))
                self.wb_prod_table.rows = rows
                self.wb_total_pages = int(meta.get("total_pages") or 1)
                self.wb_page_no = int(meta.get("page") or page)
                self.wb_page_info.value = (f"第 {self.wb_page_no}/{self.wb_total_pages} 页 · "
                                           f"共 {meta.get('total') or 0} 个")
                self.wb_status.value = (f"已加载 {len(self.wb_rows)} 个产品"
                                        if self.wb_rows else "这一页没有产品（换个状态或关键词试试）")
                self.wb_status.color = ACCENT_2 if self.wb_rows else WARN_C
                self._wb_sel_refresh()
            except Exception as ex:  # noqa: BLE001
                self.wb_status.value = f"加载失败：{str(ex)[:120]}（先检查上面的 Cookie）"
                self.wb_status.color = DANGER_C
                self.toast(f"加载失败：{ex}", False)
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _wb_pick_images(self):
        if not self.wb_selected:
            self.toast("请先勾选产品", False); return
        urls: list[str] = []
        for it in self.wb_rows:
            if str(it.get("id")) in self.wb_selected:
                urls.extend([u for u in (it.get("images") or []) if isinstance(u, str)])
        urls = [u for i, u in enumerate(urls) if u and u not in urls[:i]]
        if not urls:
            self.toast("选中的产品没有图片", False); return
        self.wb_status.value = f"已选 {len(self.wb_selected)} 个产品，下载 {len(urls)} 张图…"
        self.wb_status.color = MUTED
        self.wb_tabs.selected_index = 1
        try:
            self._safe_update()
        except Exception:
            pass
        threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

    def _wb_load_urls(self):
        urls = [u.strip() for u in (self.wb_urls.value or "").splitlines() if u.strip()]
        if not urls:
            self.toast("请输入图片 URL", False); return
        self.wb_status.value = f"下载 {len(urls)} 张图…"
        try:
            self._safe_update()
        except Exception:
            pass
        threading.Thread(target=self._download_images, args=(urls,), daemon=True).start()

    def _wb_make_video(self):
        _sel = [p for p in self._wb_selected_paths() if Path(p).exists()]
        if not _sel:
            self.toast("请先勾选存在的图片", False); return
        info = video_probe()
        if not info["available"]:
            self.toast("未检测到 ffmpeg，请先安装", False); return
        try:
            outdir = Path(self.cfg.video_output_dir) if self.cfg.video_output_dir else (Path.home() / "Movies" / "AuroraList")
            outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"carousel-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
            self._log(f"开始合成视频：{len(_sel)} 张图 → {out}")
            generate_carousel_video(
                list(_sel), out,
                slot_seconds=float(self.cfg.video_slot_seconds or 2.5),
                transition_seconds=float(self.cfg.video_transition_seconds or 0.5),
                size=str(self.cfg.video_size or "1080x1080"),
                cover=bool(self.cfg.video_cover),
                fade_edges=bool(self.cfg.video_fade_edges),
            )
            self._log(f"视频已生成：{out}")
            self.toast(f"视频已生成：{out}")
        except Exception as ex:
            self._log(f"视频生成失败：{ex}")
            self.toast(str(ex), False)

    def _wb_render_wall(self):
        if getattr(self, "wb_wall", None) is None:
            self.wb_wall = ft.Row(spacing=8, wrap=True)
        tiles = []
        for pth in list(self.picked):
            key = str(pth)
            cb = ft.Checkbox(value=key in self.wb_sel_paths,
                             on_change=lambda e, _k=key: self._wb_pick_one(_k, e.control.value))
            tiles.append(ft.Column([
                ft.Image(src=key, width=92, height=92, fit=ft.ImageFit.COVER, border_radius=6),
                cb,
            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER, width=96))
        self.wb_wall.controls = tiles
        try:
            self.wb_pick_summary.value = f"已选 {len(self.wb_sel_paths)}/{len(self.picked)} 张"
        except Exception:
            pass
        try:
            self._safe_update()
        except Exception:
            pass

    def _wb_pick_one(self, key: str, on: bool):
        if on:
            self.wb_sel_paths.add(key)
        else:
            self.wb_sel_paths.discard(key)
        try:
            self.wb_pick_summary.value = f"已选 {len(self.wb_sel_paths)}/{len(self.picked)} 张"
            self._safe_update()
        except Exception:
            pass

    def _wb_selected_paths(self) -> list:
        return [pth for pth in self.picked if str(pth) in self.wb_sel_paths]

    def _wb_toggle(self, pid: str, on: bool):
        if on:
            self.wb_selected.add(pid)
        else:
            self.wb_selected.discard(pid)
        self._wb_sel_refresh()
        self._wb_preview_selected()

    def _wb_preview_selected(self):
        """把已勾选产品的图片铺成一行缩略图（最多 10 张）。"""
        try:
            imgs = []
            for it in (getattr(self, "wb_rows", None) or []):
                if str(it.get("id")) in getattr(self, "wb_selected", set()):
                    imgs.extend([u for u in (it.get("images") or []) if u])
            imgs = imgs[:10]
            self.wb_prod_preview.controls = (
                [self._zoom_img(u, 56, 56, "预览", gallery=imgs) for u in imgs]
                or [muted("尚未勾选产品", 10.5)])
            self._safe_update()
        except Exception:
            pass

    def _wb_sel_refresh(self):
        n_img = 0
        for it in getattr(self, "wb_rows", []):
            if str(it.get("id")) in self.wb_selected:
                n_img += len(it.get("images") or [])
        self.wb_sel_text.value = f"已选 {len(self.wb_selected)} 个产品 / {n_img} 张图"
        try:
            self._safe_update()
        except Exception:
            pass

    def _retry_failed_queue(self):
        n = self.q.retry_failed()
        self._refresh_queue()
        self.toast(f"已重新排队 {n} 个失败任务" if n else "没有失败任务")

    def _refresh_queue(self):
        st = self.q.stats()
        self.q_text.value = (f"待处理 {st['pending']} · 处理中 {st['running']} · "
                             f"完成 {st['done']} · 失败 {st['failed']}")
        try:
            total = int(st["pending"]) + int(st["running"]) + int(st["done"]) + int(st["failed"])
            self.q_progress.value = (float(st["done"] + st["failed"]) / total) if total else 0
        except Exception:
            pass
        try:
            self._safe_update()
        except Exception:
            pass

    def _process_queue(self):
        """按队列真实执行：会处理图片/文本并保存到店小秘（与主流程一致）。"""
        if getattr(self, "_running", False):
            self._log("已有任务在运行，队列稍后再处理", "WARN")
            self.toast("已有任务在运行", False)
            return
        self._running = True
        (getattr(self, '_sync_run_button', None) or (lambda: None))()
        self._stop_requested = False
        # 验收实例仅预演，日常实例按队列执行真实处理。
        import os as _queue_os
        preview = bool(_queue_os.environ.get("AURORA_NO_SAVE") or _queue_os.environ.get("AURORA_TEST"))
        by_id = {str(p.get("id")): p for p in (self.products or [])}
        by_id.update({str(k): v for k, v in getattr(self, "_queue_products", {}).items()})
        items = self.q.pending()
        if not items:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
            self.toast("队列为空", False)
            return
        workers = max(1, min(int(getattr(self.cfg, "concurrency", 3) or 3), 8, len(items)))
        lock = threading.Lock()
        counts = {"ok": 0, "fail": 0}
        # 队列批处理必须带 dxm（API 直存）和 scheduler（定时发布登记），
        # 否则会退回 DOM 保存 / 报「未启用调度器」。
        _q_dxm = None
        try:
            if preview:
                raise RuntimeError("验收预演不连接商品保存服务")
            from ..integrations.dianxiaomi import DianxiaomiClient as _DXC
            _q_dxm = _DXC(self._cookies(), overrides=self.cfg.endpoint_overrides or {})
        except Exception as _ex:
            self._log(f"队列：连接店小秘失败（将退回 DOM）：{str(_ex)[:80]}", "WARN")

        def work(item):
            if self._stop_requested:
                self._log(f"已停止，跳过队列 {item.product_id}", "WARN")
                return
            base = by_id.get(item.product_id, {})
            if str(base.get("status_type") or "") in ("auditing", "onSelling", "published", "online") or \
                    "PRODUCT_IN_AUDIT_PENDING" in str((base.get("raw") or {}).get("errMsg") or ""):
                self._log(f"队列跳过审核中/已上架：{item.product_id}", "WARN")
                with lock:
                    self.q.mark(item.product_id, "failed", "审核中/已上架，已跳过")
                    self._refresh_queue()
                return
            prod = {"id": item.product_id,
                    "title": base.get("title") or item.title,
                    "skus": base.get("skus") or [],
                    "images": base.get("images") or [],
                    "status_type": base.get("status_type") or "",
                    "raw": base.get("raw") or {},
                    "cost": 0}
            with lock:
                self.q.mark(item.product_id, "running")
                self._refresh_queue()
            try:
                pub = Publisher(self.cfg, dxm=_q_dxm,
                                scheduler=getattr(self, "scheduler", None))
                res = pub.run(prod, dry_run=preview, manage_progress=False)
                status = "done" if res.ok else "failed"
                msg = (("预演完成，未保存商品、未标记已编辑" if preview else
                        "处理完成，已保存到店小秘") if res.ok else res.error)
            except Exception as ex:  # noqa: BLE001
                status, msg = "failed", str(ex)[:160]
            with lock:
                self.q.mark(item.product_id, status, msg)
                counts["ok" if status == "done" else "fail"] += 1
                self._refresh_queue()
                self._log(f"队列 {item.product_id}: {msg if status == 'done' else '失败 ' + msg}")

        self._log(f"队列处理开始：{len(items)} 个任务，并发 {workers}")
        import concurrent.futures as _cf
        try:
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, items))
        finally:
            self._running = False
            (getattr(self, '_sync_run_button', None) or (lambda: None))()
        ok, fail = counts["ok"], counts["fail"]
        history.record("queue:preview" if preview else "queue:process", ok, fail)
        self._log(f"队列处理完成：成功 {ok} / 失败 {fail}" + ("（已按请求停止）" if self._stop_requested else ""))
        self.toast(f"队列处理完成：成功 {ok} / 失败 {fail}" + ("（预演，未保存商品）" if preview else ""))

    def _images_from_products(self):
        urls: list[str] = []
        for p in self.products:
            for key in ("images", "imageList", "mainImages", "imgList"):
                v = p.get(key)
                if isinstance(v, list):
                    for it in v:
                        if isinstance(it, str):
                            urls.append(it)
                        elif isinstance(it, dict):
                            u = it.get("url") or it.get("imageUrl") or it.get("src")
                            if u:
                                urls.append(u)
            u1 = p.get("imgUrl") or p.get("mainImage") or p.get("image")
            if isinstance(u1, str):
                urls.append(u1)
        urls = [u for u in dict.fromkeys(urls) if str(u).startswith("http")]
        if not urls:
            self.toast("未取到图片，请先「拉取产品」或手动粘贴 URL", False); return
        self.wb_urls.value = "\n".join(urls[:60])
        self.toast(f"已填入 {min(len(urls),60)} 个图片链接，开始下载…")
        self.wb_status.value = f"下载 {min(len(urls), 60)} 张图…"
        try:
            threading.Thread(target=self._download_images, args=(urls[:60],), daemon=True).start()
        except Exception:
            pass

    def _download_images(self, urls):
        import requests as _rq
        from pathlib import Path as _P
        import tempfile as _tf
        d = _P(_tf.gettempdir()) / "aurora-workbench"
        d.mkdir(parents=True, exist_ok=True)
        got = []
        for i, u in enumerate(urls):
            try:
                r = _rq.get(u, timeout=30)
                r.raise_for_status()
                p = d / f"img_{i:03d}.png"
                p.write_bytes(r.content)
                got.append(p)
            except Exception as ex:
                self._log(f"下载失败 {u[:60]}: {ex}")
        self.picked = got
        self.wb_sel_paths = {str(p) for p in got}
        self.wb_status.value = f"已下载 {len(got)}/{len(urls)} 张"
        self.wb_status.color = ACCENT_2 if got else DANGER_C
        try:
            self._wb_render_wall()
        except Exception:
            pass
        self._safe_update()

    def _upload_results(self):
        from ..integrations.cos_upload import upload_image
        urls = [str(c.controls[1].value or "") for c in self.wb_results.controls
                if len(c.controls) > 1 and isinstance(c.controls[1], ft.Text)]
        urls = [u for u in urls if u.startswith(("http://", "https://")) or Path(u).is_file()][:20]
        if not urls:
            self.toast("没有可上传的结果", False); return
        try:
            ck = self._cookies()
            client = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            ok = 0
            for u in urls:
                try:
                    if u.startswith(("http://", "https://")):
                        client.upload_image_from_url(u)
                    else:
                        upload_image(client, Path(u))
                    ok += 1
                except Exception as ex:
                    self._log(f"上传失败 {u[:50]}: {ex}")
            self.toast(f"已上传 {ok}/{len(urls)} 张到相册")
            self._log(f"图片上传完成：{ok}/{len(urls)}")
        except Exception as ex:
            self.toast(f"上传失败：{ex}", False)

    def _wb_compare(self, before, after):
        """处理前后对比（左侧原图、右侧结果）。"""
        def _col(path, label, color):
            return ft.Column([
                ft.Text(label, size=12.5, color=color),
                (ft.Image(src=str(path), width=340, height=340, fit=ft.ImageFit.CONTAIN)
                 if str(path or "").strip() else empty_state("无图")),
            ], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        dlg = ft.AlertDialog(
            title=ft.Text("处理前后对比"),
            content=ft.Row([_col(before, "处理前", MUTED),
                            ft.Icon(ft.Icons.ARROW_FORWARD, size=17.5, color=MUTED),
                            _col(after, "处理后", ACCENT_2)], spacing=14),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg))],
        )
        self._show_dlg(dlg)

    def _wb_process(self, kind):
        targets = self._wb_selected_paths()
        if not targets:
            self.wb_busy.visible = False
            self.toast("请先勾选要处理的图片", False)
            self._safe_update()
            return
        if kind == "add_watermark" and not str(getattr(self.cfg, "watermark", "") or "").strip():
            self.wb_busy.visible = False
            self.toast("请先在「AI 图片 → 图片输出与水印」设置水印", False)
            self._safe_update()
            return
        svc = (None if kind == "add_watermark" else
               ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg))
        watermark_dir = Path(tempfile.mkdtemp(prefix="aurora-watermark-")) if kind == "add_watermark" else None
        runner = BatchRunner(self.cfg.concurrency, retries=int(self.cfg.retry_max or 1), retry_delay=float(self.cfg.retry_delay or 2.0))
        self.wb_runner = runner
        self.wb_results.controls = []

        _prompt = str(getattr(getattr(self, "wb_prompt", None), "value", "") or "").strip() \
            or (self.cfg.edit_prompt or "clean product photo")
        _translate = (getattr(self.cfg, "translate_prompt", "") or "").strip() or \
            "Translate all visible text in the image into natural English, keep layout, colors and typography unchanged; do not add new text."

        def job(idx, path):
            if kind == "add_watermark":
                from ..services.image_ops import add_watermark
                return str(add_watermark(
                    Path(path), self.cfg.watermark, watermark_dir / f"watermark-{idx}.png",
                    position=str(getattr(self.cfg, "overlay_position", "bottom-right") or "bottom-right"),
                    opacity=float(getattr(self.cfg, "overlay_opacity", .7))))
            if kind == "white":
                return svc.white_background(Path(path))
            if kind == "watermark":
                return svc.remove_watermark(Path(path))
            if kind == "translate":
                return svc.edit(Path(path), _translate)
            return svc.edit(Path(path), _prompt)

        def done(res):
            icon = ft.Icons.CHECK_CIRCLE if res.ok else ft.Icons.ERROR_OUTLINE
            color = ACCENT_2 if res.ok else DANGER_C
            if not res.ok:
                self._maybe_quota(res.error)
            _before = (getattr(self, "_wb_before", {}) or {}).get(res.index)
            _cmp = (ft.TextButton("对比", icon=ft.Icons.COMPARE,
                                  on_click=lambda e, _b=_before, _a=res.value:
                                  self._wb_compare(_b, _a))
                    if (res.ok and _before) else ft.Container())
            self.wb_results.controls.append(ft.Row([ft.Icon(icon, size=16.5, color=color),
                                                    ft.Text(res.value if res.ok else res.error, size=12.5,
                                                            color=MUTED, selectable=True),
                                                    _cmp], spacing=8))
            self._safe_update()

        # 只处理第 N 张（1 基）时，过滤 targets
        _nth = ""
        try:
            _nth = str(getattr(getattr(self, "wb_index", None), "value", "") or "").strip()
        except Exception:
            _nth = ""
        if _nth and (not _nth.isdigit() or int(_nth) < 1):
            self.toast("图片序号必须是大于 0 的整数", False)
            self.wb_busy.visible = False
            self._safe_update()
            return
        if _nth:
            _i = int(_nth) - 1
            if 0 <= _i < len(targets):
                targets = [targets[_i]]
            else:
                self.toast(f"第 {_nth} 张超出范围（共 {len(targets)} 张）", False)
                self.wb_busy.visible = False
                self._safe_update()
                return
        _t0 = time.time()
        self._wb_before = dict(targets) if isinstance(targets, dict) else {
            i: pth for i, pth in enumerate(targets)}
        results = runner.run(list(targets), job, done)
        _el = time.time() - _t0
        ok = sum(1 for r in results if r.ok)
        try:
            history.record("workbench:" + kind, ok, len(results) - ok)
        except Exception:
            pass
        _label = {"white": "白底", "watermark": "去水印", "translate": "翻译",
                  "edit": "编辑", "add_watermark": "加水印"}.get(kind, kind)
        self.wb_status.value = f"处理完成：成功 {ok}/{len(results)} · {_label} · 用时 {_el:.1f}s"
        self.wb_status.color = ACCENT_2 if ok else DANGER_C
        self._log(f"图片处理（{_label}）：成功 {ok}/{len(results)} · 用时 {_el:.1f}s",
                  "INFO" if ok else "WARN")
        # 处理完刷新图片墙与缩略图计数
        try:
            self._wb_render_wall()
            self._wb_preview_selected()
        except Exception:
            pass
        self.wb_busy.visible = False
        self._safe_update()

    def _p_logs(self):
        self._current = 10
        self.log_filter_chip = ft.Text("", size=12, color=ACCENT_2)
        self.log_filter = ft.TextField(
            hint_text="搜索日志（产品 ID / 关键词）", width=260, dense=True,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            on_change=lambda e: self._refresh_log_table())
        self.logs_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=8)
        self.logs_progress_text = ft.Text("空闲", size=13.5, color=MUTED)
        self.run_status = ft.Text("状态：空闲", size=13.5, color=MUTED)
        self.log_level = ft.Dropdown(
            width=112, value="全部", bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            options=[ft.dropdown.Option(x) for x in ("全部", "INFO", "WARN", "ERROR")],
            on_change=lambda e: self._refresh_log_table())
        self.log_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("时间", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("级别", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("内容", size=12.5, color=MUTED)),
                     ft.DataColumn(ft.Text("按产品筛选", size=12.5, color=MUTED))],
            rows=[], column_spacing=18, heading_row_height=36, data_row_min_height=ROW_MIN_H,
            heading_text_style=ft.TextStyle(size=FS_SM, color=MUTED),
            data_text_style=ft.TextStyle(size=FS_BASE, color=TEXT),
        )
        if not getattr(self, "log_rows", None):
            self.log_rows = []          # 表格只显示本次运行；历史见「打开日志文件」
        self.log_follow = ft.Checkbox(
            label="跟随最新", value=bool(getattr(self.cfg, "log_auto_follow", True)),
            on_change=self._on_log_follow_change)
        self.log_scroll = ft.Column([self.log_table], height=460, scroll=ft.ScrollMode.AUTO,
                                    auto_scroll=bool(getattr(self.cfg, "log_auto_follow", True)),
                                    on_scroll=self._on_log_scroll)
        self.log_count_text = ft.Text("0 行", size=12.5, color=MUTED)
        self._refresh_log_table()

        def export(e):
            out = Path(tempfile.gettempdir()) / "aurora-logs.txt"
            rows = getattr(self, "log_rows", [])
            out.write_text("\n".join(f"{t} [{lv}] {m}" for t, lv, m in rows), encoding="utf-8")
            self.toast(f"已导出：{out}")

        def clear_logs(e):
            self.log_rows.clear()
            self.logs.clear()
            if getattr(self, "log_count_text", None) is not None:
                self.log_count_text.value = "0 行"
            self._refresh_log_table()
            self.toast("已清空当前日志")

        # ---------- 运行状态 ----------
        status_card = card(ft.Column([
            ft.Row([
                ft.Text("运行状态", size=15.5, weight=ft.FontWeight.BOLD, color=TEXT),
                self.run_status,
                ft.Container(expand=True),
                ft.ElevatedButton("停止任务", icon=ft.Icons.STOP_CIRCLE, bgcolor=DANGER_BG,
                                  color="white", on_click=self._request_stop),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.logs_progress,
            self.logs_progress_text,
        ], spacing=10))

        # ---------- 操作栏 ----------
        self.log_status = ft.Text("", size=12.5, color=MUTED)
        toolbar = card(ft.Column([
            ft.Row([
                ft.ElevatedButton("上报运行日志", icon=ft.Icons.CLOUD_UPLOAD, bgcolor=ACCENT,
                                  color=ON_ACCENT, on_click=self._report_logs_now),
                ft.OutlinedButton("导出日志", icon=ft.Icons.DOWNLOAD, on_click=export),
                ft.OutlinedButton("打开日志文件", icon=ft.Icons.DESCRIPTION,
                                  on_click=lambda e: self._open_log_file()),
                ft.TextButton("清空", icon=ft.Icons.DELETE_OUTLINE, on_click=clear_logs),
            ], spacing=10, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                self._edited_count_text(),
                ft.TextButton("清空已编辑记录", icon=ft.Icons.RESTART_ALT,
                              on_click=lambda e: self._clear_edited()),
            ], spacing=10, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.TextButton("复制全部日志", icon=ft.Icons.COPY,
                              tooltip="把当前日志全部复制到剪贴板",
                              on_click=lambda e: self._copy_logs()),
                ft.TextButton("导出筛选结果", icon=ft.Icons.FILE_DOWNLOAD,
                              on_click=lambda e: self._export_logs_filtered("txt")),
                ft.TextButton("导出 CSV", icon=ft.Icons.TABLE_VIEW,
                              on_click=lambda e: self._export_logs_filtered("csv")),
                ft.TextButton("清空日志", icon=ft.Icons.CLEAR_ALL,
                              on_click=lambda e: self._confirm(
                                  "清空当前日志？",
                                  "只清空界面里的日志；磁盘上的 app.log 不受影响。",
                                  self._clear_logs_ui)),
            ], spacing=8, wrap=True),
            ft.Row([self.log_level, self.log_filter,
                    ft.TextButton("清空筛选", icon=ft.Icons.FILTER_ALT_OFF,
                                  on_click=lambda e: self._clear_log_filter()),
                    self.log_filter_chip,
                    self.log_follow], spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.log_status,
            muted("「上报运行日志」会把最近 800 行日志和错误类型发到授权后台，管理员在「用户数据」里查看；"
                  "授权成功时与每轮任务结束后也会自动上报。"),
        ], spacing=8))

        # ---------- 定时发布队列 ----------
        try:
            jobs = self.scheduler.pending_jobs() if hasattr(self, "scheduler") else []
        except Exception:
            jobs = []

        def cancel_job(e, job):
            try:
                n = self.scheduler.cancel(str(job.product_id))
                self._log(f"定时任务已取消：{job.product_id}（{n} 条）")
                self.toast("已取消定时发布")
            except Exception as ex:
                self.toast(f"取消失败：{ex}", False)
            self._switch(10)

        def publish_now(e, job):
            try:
                self.scheduler.cancel(str(job.product_id))
            except Exception:
                pass
            threading.Thread(target=self._publish_scheduled_now, args=(job,), daemon=True).start()

        sched_rows = []
        for j in jobs[:8]:
            remain = max(0, int(j.run_at - time.time()))
            sched_rows.append(ft.Row([
                ft.Text(str(j.product_id), size=12.5, color=TEXT, width=110),
                ft.Text(time.strftime("%m-%d %H:%M", time.localtime(j.run_at)), size=12.5,
                        color=INFO_C, width=96),
                ft.Text(f"{remain // 60} 分钟后" if remain >= 60 else ("即将执行" if remain else "待执行"),
                        size=12, color=MUTED, width=76),
                ft.Text((j.title or "")[:24], size=12.5, color=MUTED, expand=True),
                ft.TextButton("立即发布", on_click=lambda e, _j=j: publish_now(e, _j)),
                ft.TextButton("取消", on_click=lambda e, _j=j: cancel_job(e, _j)),
            ], spacing=6))
        sched_card = card(ft.Column([
            ft.Row([ft.Text("定时发布队列", size=15.5, weight=ft.FontWeight.BOLD, color=TEXT),
                    ft.Text(f"{len(jobs)} 条待执行", size=12.5, color=MUTED),
                    ft.Container(expand=True),
                    ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda e: self._switch(10))],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            muted("到点后自动在「待发布」列表里批量发布；商品本身在保存阶段已写入店小秘"),
            muted("注意：定时任务需要软件保持运行；关闭软件后不会执行。"),
            *(sched_rows or [empty_state("暂无定时任务")]),
        ], spacing=6))

        # ---------- 日志表 ----------
        log_card = card(ft.Column([
            ft.Row([ft.Text("运行日志", size=15.5, weight=ft.FontWeight.BOLD, color=TEXT),
                    self.log_count_text], spacing=10),
            ft.Container(content=self.log_scroll, height=300, bgcolor=BG,
                         border_radius=8, padding=4),
        ], spacing=8))

        return [page_header("任务与日志", destination(10).description), status_card,
                workspace_tabs([
                    ("运行日志", [toolbar, log_card]),
                    ("定时发布", [sched_card]),
                ])]

    def _report_logs_now(self, e=None):
        """手动上报当前日志到授权后台（异步）。"""
        try:
            if getattr(self, "log_status", None) is not None:
                self.log_status.value = "正在上报…"
                self.log_status.color = MUTED
                self._safe_update()
        except Exception:
            pass

        def _run():
            try:
                from ..core import report as _rep
                from ..core.updater import CURRENT as _ver
                ident = str(getattr(self, "_report_identity", "") or "")
                if not ident:
                    try:
                        from ..license import machine_code
                        ident = machine_code()
                    except Exception:
                        ident = ""
                r = _rep.upload(str(getattr(self.cfg, "license_server", "") or ""), ident,
                                _rep.tail_log(), version=_ver,
                                instance=__import__("os").environ.get("AURORA_INSTANCE", "默认") or "默认")
                if r.get("ok"):
                    self.toast("已上报，管理员后台「用户数据」里可见")
                    if getattr(self, "log_status", None) is not None:
                        self.log_status.value = "已上报 ✓"
                        self.log_status.color = ACCENT_2
                else:
                    self.toast(f"上报失败：{r.get('error') or r.get('status')}", False)
                    if getattr(self, "log_status", None) is not None:
                        self.log_status.value = f"上报失败：{r.get('error') or r.get('status')}"
                        self.log_status.color = DANGER_C
            except Exception as ex:  # noqa: BLE001
                self.toast(f"上报失败：{str(ex)[:80]}", False)
                if getattr(self, "log_status", None) is not None:
                    self.log_status.value = f"上报失败：{str(ex)[:80]}"
                    self.log_status.color = DANGER_C
            try:
                self._safe_update()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _subscribe_progress(self):
        try:
            from ..services.progress import BUS
            BUS.subscribe(self._on_progress)
        except Exception:
            pass

    def _on_key(self, e):
        """快捷键：⌘/Ctrl+R 启动·停止，⌘/Ctrl+L 日志，⌘/Ctrl+F 搜商品。"""
        try:
            key = (getattr(e, "key", "") or "").lower()
            ctrl = bool(getattr(e, "ctrl", False) or getattr(e, "meta", False))
            if not ctrl:
                return
            if key == "r":
                self._toggle_run()
            elif key == "l":
                self._switch(10)
            elif key == "f":
                self._global_search_dialog()
            elif key == "escape":
                self.page.close_dialog()
        except Exception:
            pass

    def _on_progress(self, ev):
        """总线事件 → 概览卡片实时刷新（后台线程调用）。"""
        try:
            if getattr(self, "ov_progress", None) is None:
                return
            from ..services.progress import BUS
            done, total, cost, used = BUS.progress()
            if total:
                self.ov_progress.value = min(1.0, done / max(1, total))
                self.ov_progress_text.value = f"步骤 {done}/{total} · {used:.1f}s · ¥{cost:.3f}"
            # 同步日志页进度
            if getattr(self, "logs_progress", None) is not None:
                if total:
                    self.logs_progress.value = min(1.0, done / max(1, total))
                    self.logs_progress_text.value = f"步骤 {done}/{total} · {used:.1f}s · 累计 ¥{cost:.3f}"
            # 把结构化事件也写进「任务与日志」表格
            try:
                if not hasattr(self, "log_rows"):
                    self.log_rows = []
                self.log_rows.append((time.strftime("%H:%M:%S", time.localtime(ev.ts)), ev.level, ev.text()))
                self.log_rows = self.log_rows[-1000:]
                if getattr(self, "log_table", None) is not None:
                    self._refresh_log_table()
            except Exception:
                pass
            self.ov_recent.controls = [
                ft.Text(f"{time.strftime('%H:%M:%S', time.localtime(e.ts))} {e.text()}", size=12,
                        color=self._log_color(e.level), selectable=True)
                for e in BUS.snapshot(4)]
            self._safe_update()
        except Exception:
            pass

    def _open_log_panel(self):
        """全局日志侧栏：任意页面可唤出，实时刷新。"""
        if getattr(self, "log_panel_open", False):
            return
        self.log_panel_open = True
        self.log_panel_level = ft.Dropdown(width=110, value="全部", bgcolor=CARD_2, color=TEXT, border_color=CARD_2,
                                           options=[ft.dropdown.Option(x) for x in ("全部", "INFO", "WARN", "ERROR")],
                                           on_change=lambda e: self._render_log_panel())
        self.log_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        self.log_progress_text = ft.Text("空闲", size=12.5, color=MUTED)
        self.log_panel_list = ft.Column(spacing=2, height=360, scroll=ft.ScrollMode.AUTO, auto_scroll=True)
        self.log_panel_list.controls = [ft.Text(x, size=12.5, color=self._log_color(lv), selectable=True)
                                        for _, lv, x in self._log_panel_items()]
        self.log_panel_dlg = ft.AlertDialog(
            title=ft.Row([ft.Text("实时进度"), self.log_panel_level,
                          ft.TextButton("清空", on_click=lambda e: self._clear_logs()),
                          ft.TextButton("导出", on_click=lambda e: self._export_logs())], spacing=12),
            content=ft.Container(content=ft.Column([self.log_progress, self.log_progress_text,
                                                    ft.Divider(height=1, color=CARD_2),
                                                    self.log_panel_list], spacing=6, tight=True), width=820),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_log_panel())])
        try:
            self._show_dlg(self.log_panel_dlg)
        except Exception:
            pass
        try:
            from ..services.progress import BUS
            from ..services import client_from_config
            rows_p = client_from_config(self.cfg, 15).pricing()
            BUS.set_prices({r["model"]: r["per_call"] for r in rows_p if r.get("per_call")})
        except Exception:
            pass
        threading.Thread(target=self._log_panel_loop, daemon=True).start()

    @staticmethod
    def _log_color(lv: str) -> str:
        return {"ERROR": DANGER_C, "WARN": WARN_C}.get(str(lv).upper(), MUTED)

    def _log_panel_items(self) -> list[tuple]:
        rows = getattr(self, "log_rows", [])[-300:]
        lv = getattr(self, "log_panel_level", None)
        want = (lv.value if lv is not None else "全部") or "全部"
        if want != "全部":
            rows = [r for r in rows if str(r[1]).upper() == want]
        return rows

    def _log_panel_loop(self):
        while getattr(self, "log_panel_open", False):
            try:
                self._render_log_panel()
            except Exception:
                break
            time.sleep(2.0)

    def _render_log_panel(self):
        if getattr(self, "log_panel_list", None) is None:
            return
        from ..services.progress import BUS
        evs = BUS.snapshot(400)
        want = (self.log_panel_level.value if getattr(self, "log_panel_level", None) is not None else "全部") or "全部"
        if evs:
            rows = []
            for e in evs:
                if want == "ERROR" and e.level != "ERROR":
                    continue
                if want == "WARN" and e.level not in ("WARN", "ERROR"):
                    continue
                if want == "INFO" and e.level not in ("INFO", "OK"):
                    continue
                ts = time.strftime("%H:%M:%S", time.localtime(e.ts))
                rows.append(ft.Text(f"{ts}  {e.text()}", size=12.5,
                                    color=self._log_color(e.level), selectable=True))
            self.log_panel_list.controls = rows or [empty_state("暂无匹配日志")]
        else:
            self.log_panel_list.controls = [ft.Text(f"{t} [{lv}] {m}", size=12.5, color=self._log_color(lv), selectable=True)
                                            for t, lv, m in self._log_panel_items()]
        done, total, cost, used = BUS.progress()
        if total:
            self.log_progress.value = min(1.0, done / max(1, total))
            self.log_progress_text.value = f"步骤 {done}/{total} · 用时 {used:.1f}s · 累计花费 ¥{cost:.3f}"
        else:
            self.log_progress.value = 0
            self.log_progress_text.value = f"空闲 · 累计花费 ¥{cost:.3f}" if cost else "空闲"
        try:
            self._safe_update()
        except Exception:
            pass

    def _close_log_panel(self):
        self.log_panel_open = False
        try:
            self._close_dlg(self.log_panel_dlg)
        except Exception:
            pass

    def _clear_logs(self):
        getattr(self, "log_rows", []).clear()
        self._render_log_panel()

    def _export_logs(self):
        from pathlib import Path as _P
        out = _P.home() / "Downloads" / f"aurora-logs-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(f"{t} [{lv}] {m}" for t, lv, m in getattr(self, "log_rows", [])), encoding="utf-8")
        self.toast(f"已导出：{out}")

    def _log_file(self):
        p = config_dir() / "logs" / "app.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _write_log_file(self, ts: str, level: str, msg: str):
        try:
            p = self._log_file()
            with self._log_file_lock:
                if p.exists() and p.stat().st_size > 2 * 1024 * 1024:
                    try:
                        p.replace(p.with_suffix(".1"))
                    except Exception:
                        pass
                with p.open("a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d')} {ts} [{level}] {msg}\n")
        except Exception:
            pass

    def _log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        if not hasattr(self, "log_rows"):
            self.log_rows = []
        self.log_rows.append((ts, level, str(msg)))
        self.log_rows = self.log_rows[-1000:]
        self.logs = [f"{t} [{lv}] {m}" for t, lv, m in self.log_rows]
        self._write_log_file(ts, level, str(msg))
        if getattr(self, "log_table", None) is not None:
            self._refresh_log_table()

    def _open_log_file(self):
        try:
            p = self._log_file()
            if not p.exists():
                self.toast("还没有日志文件", False); return
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                import subprocess as _sp
                _sp.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
        except Exception as ex:
            self.toast(f"打开失败：{ex}", False)

    def _on_log_follow_change(self, e):
        follow = bool(e.control.value)
        self.cfg.set("log_auto_follow", follow)
        save_config(self.cfg)
        try:
            self.log_scroll.auto_scroll = follow
            if follow:
                self.log_scroll.scroll_to(offset=10 ** 9, duration=150)
        except Exception:
            pass
        self._safe_update()

    def _on_log_scroll(self, e):
        if not bool(getattr(self.log_follow, "value", True)):
            self._log_scroll_pos = float(getattr(e, "pixels", 0) or 0)

    def _export_logs_filtered(self, fmt: str = "txt"):
        """把当前筛选结果导出成 txt（默认到配置目录 logs/）。"""
        rows = getattr(self, "log_rows", []) or []
        lv = getattr(self, "log_level", None)
        want = lv.value if lv is not None else "全部"
        kw = str(getattr(self, "log_filter", None).value if getattr(self, "log_filter", None) else "").strip().lower()
        shown = [(t, x, m) for t, x, m in rows
                 if (want == "全部" or x == want) and (not kw or kw in str(m).lower())]
        if not shown:
            self.toast("当前筛选没有内容可导出", False)
            return
        try:
            from ..config import config_dir
            outdir = config_dir() / "logs"
            outdir.mkdir(parents=True, exist_ok=True)
            if fmt == "csv":
                import csv as _csv
                fn = outdir / f"export-{time.strftime('%Y%m%d-%H%M%S')}.csv"
                with open(fn, "w", encoding="utf-8-sig", newline="") as fh:
                    w = _csv.writer(fh)
                    w.writerow(["时间", "级别", "内容"])
                    for t, x, m in shown:
                        w.writerow([t, x, m])
            else:
                fn = outdir / f"export-{time.strftime('%Y%m%d-%H%M%S')}.txt"
                text = "\n".join(f"{t} [{x}] {m}" for t, x, m in shown)
                fn.write_text(text, encoding="utf-8")
            self._log(f"已导出 {len(shown)} 行日志 → {fn}", "INFO")
            self.toast(f"已导出 {len(shown)} 行：{fn.name}", True)
            try:
                import subprocess
                subprocess.Popen(["open", str(fn)])
            except Exception:
                pass
        except Exception as ex:  # noqa: BLE001
            self.toast(f"导出失败：{str(ex)[:60]}", False)

    def _clear_log_filter(self):
        try:
            if getattr(self, "log_filter", None) is not None:
                self.log_filter.value = ""
            self._refresh_log_table()
        except Exception:
            pass

    def _copy_logs(self):
        text = "\n".join(getattr(self, "logs", []) or [])
        if not text:
            self.toast("日志是空的", False)
            return
        ok = False
        try:
            self.page.set_clipboard(text)
            ok = True
        except Exception:
            try:
                self.page.clipboard = text   # 兼容老版本 Flet
                ok = True
            except Exception:
                ok = False
        self.toast((f"已复制 {len(text.splitlines())} 行日志" if ok else "复制失败（剪贴板不可用）"), ok)

    def _clear_logs_ui(self):
        try:
            self.log_rows = []
            self.logs = []
            self._refresh_log_table()
        except Exception:
            pass
        self.toast("日志已清空", True)

    def _log_filter_by(self, pid: str):
        try:
            if getattr(self, "log_filter", None) is not None:
                self.log_filter.value = str(pid)
            self._refresh_log_table()
        except Exception:
            pass

    def _filter_cell(self, msg: str):
        import re as _re
        m = _re.search(r"\b(\d{9,})\b", str(msg or ""))
        if not m:
            return ft.Text("", size=12.5)
        pid = m.group(1)
        return ft.IconButton(ft.Icons.FILTER_ALT, icon_size=15.5, tooltip=f"只看 {pid}",
                             on_click=lambda e, _p=pid: self._log_filter_by(_p))

    def _refresh_log_table(self):
        # 事件很多时不要每条都重建表格（会把 UI 拖死），0.5 秒合并一次
        now = time.time()
        if now - float(getattr(self, "_log_refresh_ts", 0) or 0) < 0.5:
            t = getattr(self, "_log_refresh_timer", None)
            if t is None or not t.is_alive():
                t = threading.Timer(0.5, self._refresh_log_table)
                t.daemon = True
                self._log_refresh_timer = t
                t.start()
            return
        self._log_refresh_ts = now
        rows = getattr(self, "log_rows", [])
        lv = getattr(self, "log_level", None)
        want = lv.value if lv is not None else "全部"
        kw = str(getattr(self, "log_filter", None).value if getattr(self, "log_filter", None) else "").strip().lower()
        shown = [(t, x, m) for t, x, m in rows
                 if (want == "全部" or x == want) and (not kw or kw in str(m).lower())]
        try:
            if getattr(self, "log_filter_chip", None) is not None:
                if kw or want != "全部":
                    self.log_filter_chip.value = f"筛选中：{kw or want} · {len(shown)} 行"
                else:
                    self.log_filter_chip.value = ""
        except Exception:
            pass
        self.log_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(t, size=12, color=MUTED, width=62)),
                ft.DataCell(ft.Text(x, size=12, width=48, weight=ft.FontWeight.BOLD,
                                    color=log_level_color(x))),
                ft.DataCell(ft.Text(m, size=12.5, color=TEXT, width=600, selectable=True)),
                ft.DataCell(self._filter_cell(m)),
            ]) for t, x, m in shown[-300:]
        ]
        try:
            if getattr(self, "log_count_text", None) is not None:
                self.log_count_text.value = f"{len(shown)} 行"
        except Exception:
            pass
        try:
            self._safe_update()
        except Exception:
            pass
        try:
            if getattr(self, "log_scroll", None) is not None:
                if bool(getattr(self.log_follow, "value", True)):
                    self.log_scroll.scroll_to(offset=10 ** 9, duration=120)
                else:
                    pos = float(getattr(self, "_log_scroll_pos", 0) or 0)
                    if pos > 0:
                        self.log_scroll.scroll_to(offset=pos, duration=0)
        except Exception:
            pass

    def _edited_count_text(self):
        try:
            from ..services import edited
            return muted(f"已编辑记录：{edited.stats().get('count', 0)} 个（这些产品不会再自动编辑）")
        except Exception:
            return muted("已编辑记录：-")

    def _clear_edited(self):
        from ..services import edited
        count = edited.stats().get("count", 0)
        if count == 0:
            self.toast("没有已编辑记录")
            return
        
        def do_clear(e):
            try:
                n = edited.clear()
                self.toast(f"已清空 {n} 条已编辑记录；这些产品下次会被重新处理")
                self._switch(self._current)
            except Exception as ex:
                self.toast(f"清空失败：{ex}", False)
            finally:
                self._close_dialog()
        
        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认清空已编辑记录"),
            content=ft.Text(f"将清空 {count} 条已编辑记录。\n\n这些产品将在下次队列处理时被重新编辑（会再次调用 AI 并消耗费用）。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog()),
                ft.FilledButton("确认清空", on_click=do_clear),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dlg(self.dlg)

    def _p_settings(self):
        self._current = 11

        # ---------------- 授权 ----------------
        from ..license import machine_code as _mcode
        code = _mcode()
        self.machine_text = ft.Text(code, size=19, weight=ft.FontWeight.BOLD, color=TEXT, selectable=True)
        self.lic_status = ft.Text("检测中…", size=13.5, color=MUTED)
        self.lic_detail = ft.Text("", size=12.5, color=MUTED)

        def copy_code(e):
            try:
                self.page.set_clipboard(code)
                self.toast("机器码已复制，发给管理员即可登记授权")
            except Exception:
                self.toast(f"请手动复制：{code}", False)

        def recheck_lic(e):
            self.lic_status.value = "检测中…"
            self.lic_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass
            threading.Thread(target=self._check_license, daemon=True).start()

        self.legacy_id_field = ft.TextField(
            label="旧版授权 ID（可选）", value=str(getattr(self.cfg, "license_identity", "") or ""),
            width=360, bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            hint_text="已有设备授权 ID，填写后可重新检测原授权")

        def save_legacy(e=None):
            self.cfg.set("license_identity", str(self.legacy_id_field.value or "").strip())
            save_config(self.cfg)

        self.legacy_id_field.on_change = save_legacy

        lic_card = card(ft.Column([
            h1("授权", 15),
            muted("换电脑、重装系统或授权过期时，把机器码发给管理员登记", 11),
            muted("每台电脑的机器码都不同。把机器码发给管理员登记后，点「重新检测」即可使用"),
            ft.Row([ft.Text("机器码：", size=13.5, color=MUTED), self.machine_text], spacing=8, wrap=True),
            ft.Row([ft.ElevatedButton("复制机器码", icon=ft.Icons.COPY, bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=copy_code),
                    ft.OutlinedButton("重新检测", icon=ft.Icons.REFRESH, on_click=recheck_lic)],
                   spacing=8, wrap=True),
            self.legacy_id_field,
            self.lic_status, self.lic_detail,
            muted("日志上报在「任务与日志」页操作；授权成功时、每轮任务结束后也会自动回传日志。"),
        ], spacing=8))
        self.lic_status.value = (self.license_state or {}).get("message") or "未检测"
        if (self.license_state or {}).get("ok"):
            self.lic_status.color = ACCENT_2
            exp = (self.license_state or {}).get("expiry") or ""
            self.lic_detail.value = f"到期：{exp}" if exp else "授权有效（长期）"
        else:
            self.lic_status.color = DANGER_C
            self.lic_detail.value = "如果刚登记过，点「重新检测」；换电脑/重装系统后机器码会变，需要重新登记"

        # ---------------- API ----------------
        self.api_key_field = ft.TextField(label="API Key", value=str(self.cfg.api_key or ""),
                                          password=True, can_reveal_password=True, width=460,
                                          bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.api_status = ft.Text("", size=12.5, color=MUTED)
        self.cfg_concurrency = ft.TextField(label="并发数（同时处理几个产品）",
                                            value=str(self.cfg.concurrency or 3), width=220,
                                            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.cfg_timeout = ft.TextField(label="超时（秒）", value=str(self.cfg.timeout or 180), width=160,
                                        bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        # 输入立即保存；切页会重建控件，延迟读取 self 上的字段会丢掉新输入。
        def _api_autosave(e=None):
            self._save_api_fields()
        for _c in (self.api_key_field, self.cfg_concurrency, self.cfg_timeout):
            _c.on_change = _api_autosave

        def save_api(e=None):
            self._save_api_fields()
            self.api_status.value = "已保存"
            self.api_status.color = ACCENT_2
            try:
                self._safe_update()
            except Exception:
                pass

        def test_api(e):
            save_api()
            self.api_status.value = "测试中…"
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                from ..api import classify_api_error
                key = (self.cfg.api_key or "").strip()
                if not key:
                    self.api_status.value = "连接失败：未填写 API Key"
                    self.api_status.color = DANGER_C
                    try:
                        self._safe_update()
                    except Exception:
                        pass
                    return
                try:
                    models = ApiClient(self.cfg.api_base, key, 20).models(force=True)
                    self.api_status.value = f"连接正常：{len(models)} 个模型"
                    self.api_status.color = ACCENT_2
                except Exception as ex:  # noqa: BLE001
                    kind = classify_api_error(ex)
                    hint = {"auth": " → Key 无效，请重新复制粘贴",
                            "quota": " → 余额不足，请充值",
                            "other": " → 网络或服务端问题，可稍后重试"}.get(kind, "")
                    self.api_status.value = f"连接失败：{str(ex)[:80]}{hint}"
                    self.api_status.color = DANGER_C
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        def buy_api(e):
            url = str(getattr(self.cfg, "api_purchase_url", "") or "https://api.moneyfly.club")
            try:
                self.page.launch_url(url)
            except Exception:
                self.toast(f"请在浏览器打开 {url}", False)

        api_card = card(ft.Column([
            h1("API", 15),
            muted("第一次使用、提示 Key 无效 / 余额不足时改这里", 11),
            muted("填好 API Key 即可用；没有 Key 或额度不足时，点「购买/充值」去你的 API 后台"),
            self.api_key_field,
            ft.Row([self.cfg_concurrency, self.cfg_timeout,
                    ft.ElevatedButton("保存", icon=ft.Icons.SAVE, bgcolor=ACCENT, color=ON_ACCENT, on_click=save_api),
                    ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING, on_click=test_api),
                    ft.OutlinedButton("检测余额", icon=ft.Icons.ACCOUNT_BALANCE,
                                      on_click=lambda e: threading.Thread(
                                          target=self._probe_balance, daemon=True).start()),
                    ft.ElevatedButton("购买 / 充值 API", icon=ft.Icons.ACCOUNT_BALANCE_WALLET,
                                      bgcolor="#F0A020", color="#1A1200", on_click=buy_api)],
                   spacing=8, wrap=True),
            self.api_status,
        ], spacing=8))

        # ---------------- 图床 ----------------
        # 图床地址/密钥不再展示给用户（留着控件让保存逻辑沿用原值）
        self.host_url = ft.TextField(
            label="图床地址", width=340, visible=False,
            value=str(getattr(self.cfg, "image_host_url", "") or ""),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.host_key = ft.TextField(
            label="图床密钥", width=220, password=True, can_reveal_password=True, visible=False,
            value=str(getattr(self.cfg, "image_host_key", "") or ""),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.host_status = ft.Text("", size=12.5, color=MUTED)
        self.host_keep = ft.TextField(
            label="保留天数（清理时删更早的）", width=210,
            value=str(getattr(self.cfg, "host_keep_days", 7)),
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT)

        def clean_host(e=None):
            try:
                _days = max(0, int(float(self.host_keep.value or 7)))
            except Exception:
                _days = 7
            self.cfg.set("host_keep_days", _days)
            save_config(self.cfg)
            self.host_status.value = "清理中…"
            self.host_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                try:
                    from ..services.hosting import clean_old
                    r = clean_old()          # 天数由 services 读配置决定
                    self.host_status.value = r.get("message", "")
                    self.host_status.color = ACCENT_2 if r.get("ok") else DANGER_C
                    self._log(f"图床清理：{r.get('message','')}", "INFO" if r.get("ok") else "WARN")
                except Exception as ex:  # noqa: BLE001
                    self.host_status.value = f"清理失败：{str(ex)[:110]}"
                    self.host_status.color = DANGER_C
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        def save_host(e=None):
            self.cfg.set("image_host_url", str(self.host_url.value or "").strip())
            self.cfg.set("image_host_key", str(self.host_key.value or "").strip())
            save_config(self.cfg)
            self.host_status.value = "已保存"
            self.host_status.color = ACCENT_2
            try:
                self._safe_update()
            except Exception:
                pass

        def test_host(e=None):
            save_host()
            self.host_status.value = "测试中…"
            self.host_status.color = MUTED
            try:
                self._safe_update()
            except Exception:
                pass

            def go():
                try:
                    import tempfile as _tf
                    from pathlib import Path as _P
                    from ..services.hosting import upload as _up
                    from PIL import Image, ImageDraw
                    d = _P(_tf.mkdtemp()) / "host_test.png"
                    im = Image.new("RGB", (1200, 1200), (250, 250, 250))
                    ImageDraw.Draw(im).rectangle([300, 300, 900, 900], fill=(60, 120, 220))
                    im.save(d)
                    url = _up(d)
                    self.host_status.value = f"上传成功：{url}"
                    self.host_status.color = ACCENT_2
                    self._log(f"图床上传成功：{url}", "INFO")
                except Exception as ex:  # noqa: BLE001
                    self.host_status.value = f"上传失败：{str(ex)[:110]}"
                    self.host_status.color = DANGER_C
                    self._log(f"图床上传失败：{ex}", "WARN")
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=go, daemon=True).start()

        self.host_url.on_change = lambda e: save_host()
        self.host_key.on_change = lambda e: save_host()
        def reset_host(e=None):
            from ..config import DEFAULTS as _D
            self.cfg.set("image_host_url", str(_D.get("image_host_url", "") or ""))
            self.cfg.set("image_host_key", str(_D.get("image_host_key", "") or ""))
            try:
                self.host_url.value = self.cfg.image_host_url
                self.host_key.value = self.cfg.image_host_key
            except Exception:
                pass
            save_config(self.cfg)
            self.host_status.value = f"已恢复推荐图床：{self.cfg.image_host_url}"
            self.host_status.color = ACCENT_2
            self.toast("已恢复推荐图床", True)
            try:
                self._safe_update()
            except Exception:
                pass

        host_card = card(ft.Column([
            h1("图床", 15),
            muted("图片上传失败、或想换成自己的图床时改这里（默认 image.moneyfly.club）", 11),
            muted("AI 出图先放这里，店小秘编辑器再按 URL 取图。"
                  "AI 返回的是约 1 天过期的临时地址，不能直接写进商品。"),
            ft.Row([self.host_keep, ft.ElevatedButton("清理旧图", icon=ft.Icons.DELETE_SWEEP,
                                                     on_click=lambda e: clean_host())],
                   spacing=8, wrap=True),
            ft.Row([ft.ElevatedButton("保存", icon=ft.Icons.SAVE, bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=save_host),
                    ft.OutlinedButton("测试上传", icon=ft.Icons.CLOUD_UPLOAD, on_click=test_host),
                    ft.TextButton("恢复推荐图床", icon=ft.Icons.RESTORE,
                                  on_click=lambda e: reset_host())],
                   spacing=8, wrap=True),
            self.host_status,
            muted("支持 Cloudflare-ImgBed（POST /rest/upload）和老自建服务（POST /upload），自动识别。"),
        ], spacing=8))

        # ---------------- 模型（已固定，不提供选择） ----------------
        from ..services import FIXED_MODELS, FIXED_MODEL_LABELS
        self.model_status = ft.Text("模型已固定，无需设置；价格见下方「模型价格」", size=12.5, color=MUTED)
        model_rows = []
        for kind in ("text", "vision", "image_edit", "image_gen"):
            model_rows.append(ft.Row([
                ft.Text(FIXED_MODEL_LABELS[kind], size=13.5, color=MUTED, width=300),
                ft.Text(FIXED_MODELS[kind], size=13.5, color=TEXT, weight=ft.FontWeight.BOLD),
            ], spacing=10, wrap=True))
        model_card = card(ft.Column([
            h1("AI 模型", 15),
            muted("当前服务使用的模型与用途", 11),
            muted("已按实测结果固定，客户无需选择，避免选到不可用/超慢/超贵的模型"),
            *model_rows,
            self.model_status,
        ], spacing=8))

        # ---------------- 多开 ----------------
        self.inst_name = ft.TextField(label="新实例名称（如 店铺2）", width=240,
                                      bgcolor=CARD_2, border_color=CARD_2, color=TEXT)
        self.inst_list = ft.Column(spacing=4)
        self._refresh_instances()

        def create(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                info = create_instance(name)
                self._log(f"实例已创建：{name}（端口 {info.get('cdp_port')}）")
                self._refresh_instances()
                self.toast(f"实例已创建：{name}（端口 {info.get('cdp_port')}）")
            except Exception as ex:
                self.toast(str(ex), False)

        def launch(e):
            name = (self.inst_name.value or "").strip()
            if not name:
                self.toast("请输入实例名称", False); return
            try:
                launch_instance(name)
                self._log(f"实例已启动：{name}")
                self.toast("新实例已启动（独立配置与调试端口）")
            except Exception as ex:
                self.toast(str(ex), False)

        def create_and_launch(e):
            create(e)
            launch(e)

        inst_card = card(ft.Column([
            h1("多开 / 多任务", 15),
            muted("同一台电脑要同时跑多个店小秘账号时才需要", 11),
            muted("每个实例独立配置、独立浏览器调试端口，可同时跑不同店铺；任务队列与定时发布也按实例隔离"),
            ft.Row([self.inst_name,
                    ft.ElevatedButton("创建实例", icon=ft.Icons.ADD, on_click=create),
                    ft.ElevatedButton("创建并启动", icon=ft.Icons.ROCKET_LAUNCH, bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=create_and_launch)], spacing=8, wrap=True),
            self.inst_list,
        ], spacing=8))

        # ---------------- 更新 / 诊断 / 价格 ----------------
        try:
            from ..core.updater import CURRENT as _cur_ver
        except Exception:
            _cur_ver = ""
        upd = card(ft.Column([
            h1("版本更新", 15),
            muted("有新版本时在这里检测并下载（检查 GitHub Releases）", 11),
            ft.Text(f"当前版本：v{_cur_ver}" if _cur_ver else "当前版本：-", size=13.5, color=TEXT),
            ft.Row([ft.ElevatedButton("检查更新", icon=ft.Icons.SYSTEM_UPDATE,
                                      on_click=lambda e: threading.Thread(target=self._check_update, daemon=True).start())])],
            spacing=8))
        self.diag_list = ft.Column(spacing=4)
        self.diag_ff_btn = ft.OutlinedButton(
            "下载并安装 ffmpeg（约 70MB）", icon=ft.Icons.DOWNLOAD,
            visible=False, on_click=lambda e: threading.Thread(
                target=self._download_ffmpeg, daemon=True).start())
        diag_card = card(ft.Column([
            h1("诊断（出问题时再点）", 15),
            muted("检查 API、浏览器、店小秘、ffmpeg、磁盘；不影响正常使用"),
            ft.Row([ft.ElevatedButton("开始诊断", icon=ft.Icons.HEALTH_AND_SAFETY, bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=lambda e: threading.Thread(target=self._run_diag, daemon=True).start()),
                    self.diag_ff_btn], spacing=8, wrap=True),
            self.diag_list], spacing=8))
        # ---------------- 推荐配置（集中恢复默认值） ----------------
        from ..config import DEFAULTS as _DEF

        def _rec_row(label, cur, rec):
            return ft.Row([ft.Text(label, size=12.5, color=MUTED, width=70),
                           ft.Text(str(cur)[:46], size=12.5, color=TEXT, expand=True),
                           ft.Text("推荐 " + str(rec)[:40], size=12, color=ACCENT_2)],
                          spacing=10)

        def _restore(which: str):
            if which in ("host", "all"):
                self.cfg.set("image_host_url", str(_DEF.get("image_host_url", "")))
                self.cfg.set("image_host_key", str(_DEF.get("image_host_key", "")))
            if which in ("api", "all"):
                self.cfg.set("api_base", str(_DEF.get("api_base", "")))
            save_config(self.cfg)
            self.toast("已恢复推荐值（图床/API 地址），重启后全部生效", True)
            self._switch(11)

        rec_card = card(ft.Column([
            h1i(ft.Icons.VERIFIED_OUTLINED, "推荐配置"),
            muted("发错环境时用这里一键回到官方默认；模型已锁定无需选择", 11),
            _rec_row("图床", getattr(self.cfg, "image_host_url", ""), _DEF.get("image_host_url", "")),
            _rec_row("API 地址", getattr(self.cfg, "api_base", ""), _DEF.get("api_base", "")),
            ft.Row([ft.OutlinedButton("恢复图床", icon=ft.Icons.RESTORE,
                                      on_click=lambda e: _restore("host")),
                    ft.OutlinedButton("恢复 API 地址", icon=ft.Icons.RESTORE,
                                      on_click=lambda e: _restore("api")),
                    ft.ElevatedButton("全部恢复推荐值", icon=ft.Icons.SETTINGS_BACKUP_RESTORE,
                                      bgcolor=ACCENT, color=ON_ACCENT,
                                      on_click=lambda e: _restore("all"))], spacing=10, wrap=True),
        ], spacing=8))

        self.price_list = ft.Column(spacing=3)
        self.price_status = ft.Text("价格加载中…", size=12.5, color=MUTED)
        price_card = card(ft.Column([
            h1("模型价格", 15), self.price_status, self.price_list,
            ft.Row([ft.ElevatedButton("刷新价格", icon=ft.Icons.REFRESH,
                                      on_click=lambda e: threading.Thread(target=self._load_prices, daemon=True).start())])],
            spacing=8))
        threading.Thread(target=self._load_prices, daemon=True).start()

        _PAGES = [("0", "概览"), ("1", "店铺与模板"), ("2", "类目与属性"), ("3", "AI 文案"),
                  ("4", "内容审查"), ("5", "AI 图片"), ("6", "价格"), ("7", "变种与库存"),
                  ("8", "发布队列"), ("9", "图片工作台"), ("10", "任务与日志"), ("11", "设置")]
        start_dd = ft.Dropdown(label="启动时打开", width=200, bgcolor=CARD_2, border_color=CARD_2,
                               color=TEXT, options=[ft.dropdown.Option(k, v) for k, v in _PAGES],
                               value=str(getattr(self.cfg, "start_page", 0) or 0))
        size_dd = ft.Dropdown(label="窗口大小", width=200, bgcolor=CARD_2, border_color=CARD_2,
                              color=TEXT, value=str(getattr(self.cfg, "window_size", "1280x800")),
                              options=[ft.dropdown.Option("1100x760", "小 1100×760"),
                                       ft.dropdown.Option("1280x800", "中 1280×800"),
                                       ft.dropdown.Option("1512x945", "大 1512×945"),
                                       ft.dropdown.Option("1680x1050", "超大 1680×1050")])

        def _save_ui(e=None):
            self.cfg.set("start_page", int(start_dd.value or 0))
            self.cfg.set("window_size", str(size_dd.value or "1280x800"))
            save_config(self.cfg)
            self.toast("界面设置已保存（下次启动生效）", True)
        start_dd.on_change = _save_ui
        size_dd.on_change = _save_ui
        ui_card = card(ft.Column([
            h1("界面", 15),
            muted("启动页面与窗口大小，保存后下次启动生效", 11),
            ft.Row([start_dd, size_dd], spacing=12, wrap=True),
            muted("快捷键：⌘R 启动/停止 · ⌘L 打开日志 · ⌘F 搜索商品", 11),
        ], spacing=8))

        _tok = ft.TextField(
            label="平台授权 Token（授权网站复制）", width=720, multiline=True, min_lines=3,
            bgcolor=CARD_2, border_color=CARD_2, color=TEXT,
            value=str(getattr(self.cfg, "license_token", "") or ""),
            hint_text='形如 {"payload":{...},"sig":"..."}；保存后立即生效')
        _plat_status = ft.Text("", size=12, color=MUTED)
        def _tok_save(e=None):
            try:
                from ..licensing import apply_license_token
                apply_license_token(self.cfg, str(_tok.value or "").strip(), save_config)
                self.entitlements = load_entitlements(self.cfg)
                self._rebuild_nav()
                try:
                    self.platform_rail.content = self._build_platform_rail().content
                except Exception:
                    pass
                plats = self.entitlements.platforms_list()
                _plat_status.value = ("已更新：平台 " + ", ".join(plats)) if plats else "token 已保存，但未解析出平台"
                _plat_status.color = SUCCESS_C if plats else DANGER_C
                self._safe_update()
            except Exception as ex:  # noqa: BLE001
                _plat_status.value = f"保存失败：{ex}"
                _plat_status.color = DANGER_C
                self._safe_update()
        platform_card = card(ft.Column([
            h1("平台与功能包授权", 15),
            muted("授权网站按平台+功能包下发 token；未授权的平台会显示为锁定，功能入口灰显"),
            ft.Row([ft.Text(f"当前平台：{self.active_platform.name}", size=12.5, color=TEXT),
                    ft.Text("已开通：" + (", ".join(self.entitlements.platforms_list()) or "无"),
                            size=12.5, color=MUTED)], spacing=16),
            _tok, ft.Row([ft.ElevatedButton("保存并刷新授权", icon=ft.Icons.VERIFIED,
                                            bgcolor=ACCENT, color=ON_ACCENT, on_click=_tok_save),
                          _plat_status], spacing=12, wrap=True),
        ], spacing=8))

        return [page_header("设置", destination(11).description),
                workspace_tabs([
                    ("账号与连接", [api_card, lic_card, platform_card]),
                    ("图片与模型", [host_card, model_card, price_card]),
                    ("界面与实例", [ui_card, inst_card]),
                    ("维护与诊断", [upd, diag_card, rec_card]),
                    ("Alibaba 刊登", self._alibaba_cards()),
                ])]

    def _alibaba_cards(self):
        """Alibaba 刊登：连接、类目、预设资料、价格贸易、必填校验。"""
        from ..integrations.alibaba import AlibabaClient
        import json as _json

        def client():
            raw = str(getattr(self.cfg, "alibaba_cookies", "") or "").strip()
            if not raw:
                try:
                    from ..integrations.chrome_cookies import read_chrome_cookies
                    raw = read_chrome_cookies(["dianxiaomi.com"])
                except Exception:
                    raw = ""
            if not raw:
                raise RuntimeError("请先在「Alibaba 刊登」填 Cookie（或本机 Chrome 登录店小秘）")
            return AlibabaClient(raw)

        def tf(key, label, width=380, multiline=False, hint=""):
            ctl = ft.TextField(label=label, width=width, bgcolor=CARD_2, border_color=CARD_2,
                               color=TEXT, value=str(getattr(self.cfg, key, "") or ""),
                               multiline=multiline, min_lines=3 if multiline else 1,
                               hint_text=hint or None)
            def on(e, _k=key):
                self.cfg.set(_k, str(e.control.value or ""))
                save_config(self.cfg)
            ctl.on_change = on
            return ctl

        status = ft.Text("", size=12.5, color=MUTED, selectable=True)
        required_box = ft.Column(spacing=2)
        cat_box = ft.Column(spacing=2)

        def run_bg(fn):
            def wrapper(e=None):
                def work():
                    try:
                        fn()
                    except Exception as ex:  # noqa: BLE001
                        status.value = f"失败：{ex}"
                        status.color = DANGER_C
                        try:
                            self._safe_update()
                        except Exception:
                            pass
                threading.Thread(target=work, daemon=True).start()
            return wrapper

        def test_connect(e=None):
            try:
                c = client()
                p = c.product_detail(str(getattr(self.cfg, "alibaba_test_product_id", "") or "220114598434958580"))
                status.value = f"连接成功：{p.get('id')} · {str(p.get('subject'))[:40]} · 类目 {p.get('categoryId')}"
                status.color = SUCCESS_C
            except Exception as ex:  # noqa: BLE001
                status.value = f"连接失败：{ex}"
                status.color = DANGER_C
            try:
                self._safe_update()
            except Exception:
                pass

        search_kw = ft.TextField(label="搜索类目（中文/英文）", width=320, bgcolor=CARD_2,
                                 border_color=CARD_2, color=TEXT,
                                 value=str(getattr(self.cfg, "alibaba_category_name", "") or ""))

        def do_search(e=None):
            kw = str(search_kw.value or "").strip()
            if not kw:
                return
            status.value = "搜索类目中…"
            status.color = MUTED
            try:self._safe_update()
            except Exception: pass

            def work():
                try:
                    hits = client().search_categories(kw, max_nodes=160)
                    cat_box.controls = []
                    for h in hits[:30]:
                        label = f"{h.get('nameZh') or ''} / {h.get('nameEn') or ''} · L{h.get('level')} · {h.get('categoryId')}"
                        def pick(ev, _h=h):
                            self.cfg.set("alibaba_category_id", str(_h.get("categoryId") or ""))
                            self.cfg.set("alibaba_category_name", f"{_h.get('nameZh')} / {_h.get('nameEn')}")
                            full = str(_h.get("nodePathId") or _h.get("categoryId") or "")
                            self.cfg.set("alibaba_full_cid", full.replace("/", ","))
                            save_config(self.cfg)
                            status.value = f"已选类目：{_h.get('nameZh')}（{_h.get('categoryId')}）· 路径 {full}"
                            status.color = SUCCESS_C
                            if int(_h.get("isleaf") or 0) == 0:
                                _load_children(_h)
                            try:self._safe_update()
                            except Exception: pass
                        cat_box.controls.append(ft.Row([
                            ft.Text(label, size=12.5, color=TEXT, expand=True),
                            ft.OutlinedButton("选择", on_click=pick)], spacing=6))
                    status.value = f"搜索到 {len(hits)} 个类目；点击「选择」设为当前类目"
                    status.color = SUCCESS_C
                except Exception as ex:  # noqa: BLE001
                    status.value = f"搜索失败：{ex}"
                    status.color = DANGER_C
                try:self._safe_update()
                except Exception: pass
            threading.Thread(target=work, daemon=True).start()

        cat_now = ft.Text(f"当前类目：{getattr(self.cfg, 'alibaba_category_name', '') or '未选择'}（{getattr(self.cfg, 'alibaba_category_id', '') or '-'}）",
                          size=12.5, color=MUTED)

        def load_required(e=None):
            cid = str(getattr(self.cfg, "alibaba_category_id", "") or "").strip()
            if not cid:
                status.value = "先搜索并选择一个类目"
                status.color = WARN_C
                try:self._safe_update()
                except Exception: pass
                return

            def work():
                try:
                    c = client()
                    attrs = c.required_attributes(cid)
                    fields = c.category_schema(cid)["fields"]
                    required_box.controls = [h1("类目必填项", 13)]
                    for a in attrs:
                        vals = _json.loads(a.get("values") or "[]") if isinstance(a.get("values"), str) else (a.get("values") or [])
                        names = [str(v.get("name_en") or v.get("name") or "") for v in vals][:6]
                        required_box.controls.append(ft.Text(
                            f"• 属性 {a.get('nameEn')}（attrNameId={a.get('attrNameId')}）可选：{', '.join(names)}",
                            size=12, color=TEXT))
                    for f in fields:
                        required_box.controls.append(ft.Text(
                            f"• 模块 {f.get('fieldId')}：{f.get('name')}", size=12, color=TEXT))
                    self.cfg.set("alibaba_last_category_required", _json.dumps(
                        {"attrs": [a.get("nameEn") for a in attrs],
                         "fields": [f.get("fieldId") for f in fields]}, ensure_ascii=False))
                    save_config(self.cfg)
                    status.value = f"类目 {cid}：{len(attrs)} 个必填属性，{len(fields)} 个模块字段"
                    status.color = SUCCESS_C
                except Exception as ex:  # noqa: BLE001
                    status.value = f"读取失败：{ex}"
                    status.color = DANGER_C
                try:self._safe_update()
                except Exception: pass
            threading.Thread(target=work, daemon=True).start()

        def build_and_check(e=None):
            pid = str(getattr(self.cfg, "alibaba_test_product_id", "") or "").strip()
            if not pid:
                status.value = "填一个商品 ID 再校验"
                status.color = WARN_C
                try:self._safe_update()
                except Exception: pass
                return

            def work():
                try:
                    from ..services.alibaba_payload import build_alibaba_save, validate_alibaba_save
                    c = client()
                    p = c.product_detail(pid)
                    cid = str(getattr(self.cfg, "alibaba_category_id", "") or p.get("categoryId") or "")
                    req = c.required_attributes(cid) if cid else []
                    sch = c.category_schema(cid)["fields"] if cid else []
                    save = build_alibaba_save(p, self.cfg, attr_defs=req)
                    issues = validate_alibaba_save(p, save, self.cfg, req, sch)
                    required_box.controls = [h1("构建与校验结果", 13),
                                             ft.Text(f"商品：{p.get('subject')}", size=12.5, color=TEXT),
                                             ft.Text(f"自动补齐：{', '.join(save.get('_autofilled') or []) or '无'}", size=12, color=MUTED)]
                    if issues:
                        for i in issues:
                            required_box.controls.append(ft.Text("✗ " + i, size=12.5, color=DANGER_C))
                        status.value = f"校验未通过：{len(issues)} 项"
                        status.color = DANGER_C
                    else:
                        required_box.controls.append(ft.Text("✓ 必填项全部满足，可以保存", size=13, color=SUCCESS_C))
                        status.value = "校验通过"
                        status.color = SUCCESS_C
                except Exception as ex:  # noqa: BLE001
                    status.value = f"校验失败：{ex}"
                    status.color = DANGER_C
                try:self._safe_update()
                except Exception: pass
            threading.Thread(target=work, daemon=True).start()

        def do_save(e=None):
            pid = str(getattr(self.cfg, "alibaba_test_product_id", "") or "").strip()
            if not pid:
                status.value = "填一个商品 ID 再保存"
                status.color = WARN_C
                try:self._safe_update()
                except Exception: pass
                return

            def work():
                try:
                    from ..services.alibaba_publish import save_to_alibaba
                    cfg_snapshot = self.cfg
                    overrides = {}
                    res = save_to_alibaba(pid, cfg_snapshot, overrides)
                    data = (res.get("result") or {}).get("data") or {}
                    status.value = f"保存成功：{data.get('msg') or data}"
                    status.color = SUCCESS_C
                    required_box.controls = [h1("保存结果", 13),
                                             ft.Text(f"商品 {pid} · {data.get('msg')}", size=12.5, color=TEXT),
                                             ft.Text(f"自动补齐：{', '.join(res.get('autofilled') or []) or '无'}", size=12, color=MUTED)]
                except Exception as ex:  # noqa: BLE001
                    status.value = f"保存失败：{ex}"
                    status.color = DANGER_C
                try:self._safe_update()
                except Exception: pass
            threading.Thread(target=work, daemon=True).start()

        conn = card(ft.Column([
            h1("Alibaba 连接（店小秘 ICBU）", FS_MD),
            muted("粘贴店小秘 Cookie（含 dxm_* / JSESSIONID）；留空尝试读本机 Chrome 登录态"),
            tf("alibaba_cookies", "Cookie", 720, True),
            ft.Row([tf("alibaba_shop_id", "店铺 ID（可选）", 200),
                    tf("alibaba_test_product_id", "测试商品 ID", 260),
                    ft.ElevatedButton("测试连接", icon=ft.Icons.WIFI_TETHERING, bgcolor=ACCENT,
                                      color=ON_ACCENT, on_click=test_connect)], spacing=10, wrap=True),
        ], spacing=8))

        cat_card = card(ft.Column([
            h1("类目选择（按关键词搜索）", FS_MD),
            cat_now,
            ft.Row([search_kw, ft.ElevatedButton("搜索", icon=ft.Icons.SEARCH, bgcolor=ACCENT,
                                                 color=ON_ACCENT, on_click=do_search),
                    ft.OutlinedButton("读取必填属性", icon=ft.Icons.CHECKLIST, on_click=load_required),
                    ft.OutlinedButton("展开子类目", icon=ft.Icons.ACCOUNT_TREE,
                                      on_click=lambda e: _load_children({"categoryId": str(getattr(self.cfg, "alibaba_category_id", "") or ""), "nameZh": str(getattr(self.cfg, "alibaba_category_name", "") or "")}))],
                   spacing=10, wrap=True),
            cat_box,
        ], spacing=8))

        preset_card = card(ft.Column([
            h1("预设资料（必填自动填写）", FS_MD),
            muted("这些信息会用于每次刊登：分组、公司信息、默认关键词、属性预设、价格与贸易条款"),
            ft.Row([tf("alibaba_group_id", "产品分组 ID", 200), tf("alibaba_group_name", "分组名称", 220),
                    tf("alibaba_keywords", "默认关键词（逗号分隔）", 360)], spacing=10, wrap=True),
            ft.Row([tf("alibaba_company_name", "公司名称", 260),
                    tf("alibaba_company_website", "公司网站", 260)], spacing=10, wrap=True),
            tf("alibaba_company_desc", "公司介绍（英文）", 720, True),
            tf("alibaba_company_faq", "公司 FAQ（英文，可空）", 720, True),
            tf("alibaba_attr_presets", "属性预设 JSON（如 {\"Place of Origin\":\"China\",\"Certification\":\"CE\"}）", 720, True),
        ], spacing=8))

        trade_card = card(ft.Column([
            h1("价格与贸易条款", FS_MD),
            ft.Row([tf("alibaba_min_order_quantity", "MOQ", 120), tf("alibaba_unit_type", "单位", 120),
                    tf("alibaba_package_size", "包装尺寸 LxWxH(cm)", 220),
                    tf("alibaba_weight", "重量 kg", 140), tf("alibaba_stock", "库存", 120)], spacing=10, wrap=True),
            tf("alibaba_price_tiers", "阶梯价 JSON（[{\"start_quantity\":1,\"price\":100}]）", 720, True),
            ft.Row([tf("alibaba_trade_terms", "贸易条款", 140), tf("alibaba_port", "港口", 160),
                    tf("alibaba_payment_terms", "付款方式", 160), tf("alibaba_lead_time", "交期(天)", 120),
                    tf("alibaba_hs_code", "HS 编码", 160)], spacing=10, wrap=True),
        ], spacing=8))

        check_card = card(ft.Column([
            h1("必填校验 / 保存前检查", FS_MD),
            muted("读取类目真实 required 标记，自动补齐属性，再逐项校验"),
            ft.Row([ft.ElevatedButton("构建并校验", icon=ft.Icons.FACT_CHECK, bgcolor=ACCENT,
                                      color=ON_ACCENT, on_click=build_and_check),
                    ft.ElevatedButton("保存到 Alibaba", icon=ft.Icons.CLOUD_UPLOAD,
                                      bgcolor=SUCCESS_C, color=ON_ACCENT, on_click=do_save)], spacing=10),
            required_box, status,
        ], spacing=8))

        return [workspace_tabs([
                    ("连接与类目", [conn, cat_card]),
                    ("预设资料", [preset_card]),
                    ("价格与贸易", [trade_card]),
                    ("必填校验", [check_card]),
                ])]

    def _price_of(self, model: str) -> float:
        try:
            from ..api import ApiClient
            rows = ApiClient(self.cfg.api_base, self.cfg.api_key, 20).pricing()
            return next((r["per_call"] for r in rows if r["model"] == model), 0.0)
        except Exception:
            return 0.0

    def _run_diag(self):
        rows = []

        def add(name, ok, msg="", warn=False):
            color = WARN_C if warn else (ACCENT_2 if ok else DANGER_C)
            icon = ft.Icons.WARNING_AMBER if warn else (ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE)
            rows.append(ft.Row([ft.Icon(icon, size=16.5, color=color),
                                ft.Text(name, size=13.5, color=TEXT, width=110),
                                ft.Text(msg, size=12.5, color=MUTED, expand=True)], spacing=8))
            self.diag_list.controls = list(rows)
            try:
                self._safe_update()
            except Exception:
                pass

        # 1) API 站点
        if not (self.cfg.api_key or "").strip():
            add("API 站点", False, "未配置 API Key（去「设置 → API Key」填写）")
        try:
            if not (self.cfg.api_key or "").strip():
                raise RuntimeError("skip")
            from ..api import ApiClient
            c = ApiClient(self.cfg.api_base, self.cfg.api_key, 20,
                          proxy=self.cfg.proxy_url if getattr(self.cfg, "proxy_enabled", False) else "")
            ms = c.models()
            add("API 站点", True, f"{self.cfg.api_base} · {len(ms)} 个模型")
        except Exception as ex:
            if str(ex) != "skip":
                add("API 站点", False, str(ex)[:90])

        # 2) 价格接口
        try:
            from ..services import client_from_config
            rows_p = client_from_config(self.cfg, 20).pricing()
            add("价格接口", bool(rows_p), f"{len(rows_p)} 个模型有价格")
        except Exception as ex:
            add("价格接口", False, str(ex)[:90])

        # 3) 店小秘
        try:
            ck = self._cookies()
            dxm = DianxiaomiClient(ck, overrides=self.cfg.endpoint_overrides or {})
            ok = dxm.check_login()
            add("店小秘登录", ok, "已登录" if ok else "未登录或 Cookie 过期")
        except Exception as ex:
            add("店小秘登录", False, str(ex)[:80], warn=True)

        # 5) ffmpeg
        try:
            info = video_probe()
            _ff_ok = bool(info.get("available"))
            add("ffmpeg", _ff_ok, info.get("version", "")[:60] or "未安装", warn=not _ff_ok)
            try:
                self.diag_ff_btn.visible = not _ff_ok
            except Exception:
                pass
        except Exception as ex:
            add("ffmpeg", False, str(ex)[:60], warn=True)

        # 6) 磁盘
        try:
            import shutil as _sh
            total, used, free = _sh.disk_usage(str(Path.home()))
            gb = free / (1024 ** 3)
            add("磁盘空间", gb > 2, f"剩余 {gb:.1f} GB", warn=gb <= 2)
        except Exception as ex:
            add("磁盘空间", False, str(ex)[:60], warn=True)

        ok_n = sum(1 for r in rows if any(getattr(c, "color", "") == ACCENT_2 for c in r.controls))
        self._log(f"系统诊断完成：{ok_n}/{len(rows)} 项正常")

    def _download_ffmpeg(self):
        """Windows 缺 ffmpeg 时自动下载到用户目录。"""
        from ..services.video import ensure_ffmpeg
        self.diag_ff_btn.disabled = True
        bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=6)
        tip = ft.Text("开始下载 ffmpeg…", size=12.5, color=MUTED)
        self.diag_list.controls.append(ft.Column([tip, bar], spacing=4))
        try:
            self._safe_update()
        except Exception:
            pass

        def prog(frac):
            try:
                if frac is not None and frac >= 0:
                    bar.value = max(0.0, min(1.0, frac))
                    tip.value = f"下载 ffmpeg… {int(frac * 100)}%"
                else:
                    tip.value = "下载 ffmpeg…"
                self._safe_update()
            except Exception:
                pass

        try:
            path = ensure_ffmpeg(on_progress=prog)
            if path:
                tip.value = f"已安装：{path}"
                tip.color = ACCENT_2
                self._log(f"ffmpeg 已就绪：{path}", "INFO")
                self.toast("ffmpeg 已安装，视频功能可用", True)
                self.diag_ff_btn.visible = False
            else:
                tip.value = "下载失败（网络受限时请手动安装 ffmpeg）"
                tip.color = DANGER_C
                self.toast("下载失败，请手动安装 ffmpeg", False)
        except Exception as ex:  # noqa: BLE001
            tip.value = f"下载失败：{str(ex)[:80]}"
            tip.color = DANGER_C
        finally:
            self.diag_ff_btn.disabled = False
            try:
                self._safe_update()
            except Exception:
                pass

    def _load_prices(self):
        try:
            from ..api import ApiClient
            rows = ApiClient(self.cfg.api_base, self.cfg.api_key, 30).pricing()
            alias = {"gpt-6-luna", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
                     "gpt-image-2.5-flare", "gpt-image-2.5-flare-vip", "gpt-image-2.0"}
            usage = {
                "z-image-turbo": "文生图（主图生成）",
                "qwen-image-2.1-turbo": "白底 / 去水印 / 翻译 / 多图融合",
                "qwen-image-3.0": "备用（当前未使用）",
                "qwen-plus": "标题 / 描述 / SKU 文本",
                "qwen-turbo": "轻量文本",
                "qwen3-vl-plus": "图片审查 / 读图估重",
                "deepseek-chat": "备用文本",
                "deepseek-reasoner": "深度推理（备用）",
            }
            items = []
            for r in sorted(rows, key=lambda x: x["model"]):
                if r["model"] in alias:
                    continue
                if r["per_call"] > 0:
                    price = f"¥{r['per_call']:.3f} / 张"
                else:
                    price = f"按 token（倍率 {r['ratio']:.2f}）"
                items.append(ft.Row([
                    ft.Text(r["model"], size=13.5, color=TEXT, width=190),
                    ft.Text(usage.get(r["model"], "-"), size=12.5, color=MUTED, width=200),
                    ft.Text(price, size=13.5, color=ACCENT_2),
                ], spacing=12))
            if items:
                items.append(ft.Text("＊ 旧版模型名（gpt-6-luna、gpt-image-2.5-flare 等）已自动映射到对应模型，价格相同",
                                     size=12, color=MUTED))
            self.price_list.controls = items or [ft.Text("价格获取失败", size=13.5, color=WARN_C)]
            if getattr(self, "price_status", None) is not None:
                self.price_status.value = f"共 {len(rows)} 个模型（缓存 5 分钟）"
                self.price_status.color = MUTED
        except Exception as ex:
            self.price_list.controls = [ft.Text(f"价格获取失败：{ex}", size=13.5, color=DANGER_C)]
            if getattr(self, "price_status", None) is not None:
                self.price_status.value = "价格获取失败，检查 API Key/网络后点刷新"
        try:
            self._safe_update()
        except Exception:
            pass
    def _refresh_instances(self):
        try:
            items = list_instances()
        except Exception:
            items = []
        rows = []
        for i in items:
            port = i.get("cdp_port") or "-"
            rows.append(ft.Row([
                ft.Text(f"{i['name']}", size=13.5, color=TEXT, width=110),
                ft.Text(f"调试端口 {port}", size=12.5, color=MUTED, width=110),
                ft.Text(str(i.get("path") or "")[:60], size=12, color=MUTED, expand=True),
            ], spacing=8))
        self.inst_list.controls = rows or [ft.Text("暂无实例", size=12.5, color=MUTED)]

    def _toggle_theme(self):
        """白天/夜间切换：换色后重建界面（不重启进程）。"""
        cur = str(getattr(self.cfg, "ui_theme", "dark") or "dark")
        new = "light" if cur != "light" else "dark"
        self.cfg.set("ui_theme", new)
        save_config(self.cfg)
        apply_theme(new)
        try:
            self._rebuild_ui()
        except Exception as ex:  # noqa: BLE001
            # 重建失败就重启进程，保证配色一定生效
            self._log(f"界面重建失败，改为重启：{ex}", "WARN")
            self._restart_app()
            return
        self.toast(("已切换到白天模式" if new == "light" else "已切换到夜间模式"), True)

    def _restart_app(self):
        """重启本进程（主题兜底 / 更新安装前调用）。"""
        import os as _os
        import sys as _sys
        try:
            from ..core.proc import popen as _hidden_popen
            if getattr(_sys, "frozen", False):
                _hidden_popen([_sys.executable])
            else:
                _hidden_popen([_sys.executable, str(Path(_sys.argv[0]).resolve())])
        except Exception:
            pass
        _os._exit(0)

    def _rebuild_ui(self):
        """按当前配色重建整个界面（保留状态与后台线程）。"""
        page = self.page
        try:
            page.overlay.clear()
        except Exception:
            pass
        try:
            page.controls.clear()
        except Exception:
            pass
        page.bgcolor = BG
        self._configure_theme()
        page.theme_mode = (ft.ThemeMode.LIGHT
                           if str(getattr(self.cfg, "ui_theme", "dark")) == "light"
                           else ft.ThemeMode.DARK)
        try:
            page.snack_bar = ft.SnackBar(ft.Text(""), bgcolor=CARD_2)
        except Exception:
            pass
        for pk in (getattr(self, "file_picker", None), getattr(self, "wm_picker", None),
                   getattr(self, "_ref_picker", None), getattr(self, "_dl_picker", None)):
            if pk is not None:
                page.overlay.append(pk)
        self._build_shell()
        self._switch(getattr(self, "_current", 0))
        self._safe_update()

    def _startup_ffmpeg_gate(self):
        if getattr(self, "_dlg", None) is not None:
            return
        """当前设置会用到 ffmpeg（生成主图视频）却没安装时，启动即提示下载。"""
        try:
            need = (str(getattr(self.cfg, "video_source", "") or "").lower() == "generated"
                    or bool(getattr(self.cfg, "generate_main_video", False)))
        except Exception:
            need = False
        if not need:
            return

        def go():
            from ..services.video import find_ffmpeg
            if find_ffmpeg():
                return
            self._show_ffmpeg_gate()

        threading.Thread(target=go, daemon=True).start()

    def _show_ffmpeg_gate(self):
        """ffmpeg 缺失提示：可一键下载安装，也可先跳过。"""
        from ..services.video import ensure_ffmpeg
        bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=8)
        tip = ft.Text("未检测到 ffmpeg（生成本地视频需要它）", size=12.5, color=WARN_C)
        dl_btn = ft.ElevatedButton("立即下载安装", icon=ft.Icons.DOWNLOAD,
                                   bgcolor=ACCENT, color=ON_ACCENT)
        skip_btn = ft.TextButton("先跳过（视频会少一条）")

        def close(e=None):
            self._close_dlg(dlg)

        def download(e=None):
            dl_btn.disabled = True
            tip.value = "开始下载…"
            tip.color = MUTED

            def prog(frac):
                try:
                    if frac is not None and frac >= 0:
                        bar.value = max(0.0, min(1.0, frac))
                        tip.value = f"下载中… {int(frac * 100)}%"
                    else:
                        tip.value = "下载中…"
                    self._safe_update()
                except Exception:
                    pass

            def work():
                path = ensure_ffmpeg(on_progress=prog)
                if path:
                    tip.value = f"已安装：{path}"
                    tip.color = ACCENT_2
                    self._log(f"ffmpeg 已就绪：{path}", "INFO")
                    self.toast("ffmpeg 已安装，视频功能可用", True)
                    dl_btn.visible = False
                    skip_btn.text = "关闭"
                else:
                    tip.value = "下载失败：请检查网络，或手动安装 ffmpeg 后重试"
                    tip.color = DANGER_C
                    dl_btn.disabled = False
                try:
                    self._safe_update()
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        dl_btn.on_click = download
        skip_btn.on_click = close
        dlg = ft.AlertDialog(
            title=ft.Text("需要 ffmpeg 才能生成本地主图视频"),
            content=ft.Column([
                ft.Text("你的「视频来源」设为『用主图合成轮播视频』，需要本地 ffmpeg。"
                        "可以现在下载安装（约 70MB，装一次即可），也可以先跳过。",
                        size=13.5, color=TEXT),
                bar, tip,
            ], spacing=8, width=480),
            actions=[skip_btn, dl_btn],
        )
        self._show_dlg(dlg)

    def _startup_update_gate(self):
        """启动时检查版本：有更新则阻断使用，后台自动下载并给出安装按钮。"""
        import os as _os
        if str(_os.environ.get("AURORA_SKIP_UPDATE", "") or "") == "1":
            return

        def go():
            from ..core import updater as _up
            info = _up.check(self.cfg.license_server, timeout=8)
            if not info.get("ok"):
                self._log(f"版本检查失败（不阻断启动）：{info.get('error')}", "WARN")
                return
            if not info.get("has_update"):
                self._log(f"版本检查：已是最新 v{_up.CURRENT}", "INFO")
                return
            self._log(f"发现新版本 v{info.get('latest')}（当前 v{_up.CURRENT}），需更新后使用", "WARN")
            self._show_update_gate(info)

        threading.Thread(target=go, daemon=True).start()

    def _show_update_gate(self, info: dict):
        """阻断式更新弹窗：自动下载 → 立即安装（自动关软件并替换）。"""
        from ..core import updater as _up
        latest = str(info.get("latest") or "")
        url = str(info.get("url") or "").strip()
        notes = str(info.get("notes") or "").strip()

        self.upd_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=CARD_2, height=8)
        self.upd_status = ft.Text("准备下载…", size=12.5, color=MUTED)
        self.upd_install_btn = ft.ElevatedButton(
            "立即安装并重启", icon=ft.Icons.SYSTEM_UPDATE_ALT, bgcolor=ACCENT, color=ON_ACCENT,
            disabled=True)

        def quit_app(e=None):
            try:
                self.page.window.close()
            except Exception:
                pass
            import os as _o
            threading.Timer(0.3, lambda: _o._exit(0)).start()

        def install(e=None):
            path = getattr(self, "_upd_path", None)
            if not path:
                return
            self.upd_status.value = "正在安装，软件会自动关闭并重启…"
            try:
                self._safe_update()
            except Exception:
                pass
            ok = _up.install_update(Path(path))
            if not ok:
                self.toast("安装失败，请手动下载安装包", False)
                return
            self._log("更新安装器已启动，软件即将退出", "INFO")
            quit_app()

        self.upd_install_btn.on_click = install
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"需要更新到 v{latest}"),
            content=ft.Column([
                ft.Text(f"当前版本 v{_up.CURRENT} · 最新 v{latest}", size=13.5, color=TEXT),
                ft.Text("检测到新版本，更新完成后才能继续使用；下载会在后台自动进行。",
                        size=12.5, color=WARN_C),
                (ft.Container(content=ft.Text(notes[:500], size=12.5, color=MUTED, selectable=True),
                              height=120, bgcolor=CARD_2, border_radius=8, padding=8)
                 if notes else ft.Container()),
                self.upd_progress, self.upd_status,
            ], spacing=8, width=520),
            actions=[ft.TextButton("退出软件", on_click=quit_app),
                     self.upd_install_btn],
        )
        if not url:
            self.upd_status.value = "服务器没有提供安装包地址，请联系管理员。"
            self.upd_status.color = DANGER_C
        self._show_dlg(dlg)

        if not url:
            return

        def work():
            try:
                self.upd_status.value = "开始下载…"
                self._safe_update()
                # 按真实资产名保存（Setup.exe 不能存成 .zip，否则 Windows 会当压缩包打开）
                _fname = _up.asset_filename(url, f"aurora-update-{latest}")
                dest = Path(tempfile.gettempdir()) / _fname

                def prog(frac):
                    try:
                        if frac is not None and frac >= 0:
                            self.upd_progress.value = max(0.0, min(1.0, frac))
                            self.upd_status.value = f"下载中… {int(frac * 100)}%"
                        else:
                            self.upd_status.value = "下载中…"
                        self._safe_update()
                    except Exception:
                        pass

                path = _up.download_with_progress(url, dest, on_progress=prog)
                if not _up.looks_like_installer(path):
                    raise RuntimeError("下载到的不是有效安装包（可能被网络拦截），请重试")
                want = str(info.get("sha256") or "").lower()
                if want and _up.sha256(path).lower() != want:
                    raise RuntimeError("安装包校验失败（SHA256 不一致）")
                self._upd_path = path
                self.upd_progress.value = 1
                self.upd_status.value = f"下载完成（{path.name}），点「立即安装并重启」"
                self.upd_status.color = ACCENT_2
                self.upd_install_btn.disabled = False
                self._log(f"更新包已下载：{path}", "INFO")
                self._safe_update()
            except Exception as ex:  # noqa: BLE001
                self.upd_status.value = f"下载失败：{str(ex)[:90]}（可退出后重试）"
                self.upd_status.color = DANGER_C
                self._log(f"更新下载失败：{ex}", "ERROR")
                try:
                    self._safe_update()
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _check_update(self):
        r = updater.check(self.cfg.license_server)
        if not r.get("ok"):
            self.toast(f"检查失败：{r.get('error')}", False); return
        if not r.get("has_update"):
            self.toast(f"已是最新版本（v{updater.CURRENT}）")
            self._log(f"更新检查：已是最新版本 v{updater.CURRENT}")
            return
        notes = str(r.get("notes") or "").strip()
        has_url = bool(str(r.get("url") or "").strip())
        self._log(f"发现新版本 {r.get('latest')}（当前 v{updater.CURRENT}）")
        dlg = ft.AlertDialog(
            title=ft.Text(f"发现新版本 {r.get('latest')}"),
            content=ft.Column([
                ft.Text(f"当前版本：v{updater.CURRENT}", size=13.5, color=TEXT),
                ft.Text(notes[:600] or "（服务器未提供更新说明）", size=12.5, color=MUTED, selectable=True),
                ft.Text("" if has_url else "服务器还没有配置安装包下载地址，请联系管理员。",
                        size=12.5, color=WARN_C),
            ], spacing=8, width=460),
            actions=[ft.TextButton("关闭", on_click=lambda e: self._close_dlg(dlg)),
                     ft.ElevatedButton("下载并安装", bgcolor=ACCENT, color=ON_ACCENT, disabled=not has_url,
                                       on_click=lambda e: do_download(e))],
        )
        self._show_dlg(dlg)

        def do_download(e):
            self._close_dlg(dlg)
            self.toast("开始下载安装包…")

            def go():
                try:
                    path = updater.download(str(r["url"]))
                    if not updater.looks_like_installer(path):
                        raise RuntimeError("下载到的不是有效安装包（可能被网络拦截），请重试")
                    want = str(r.get("sha256") or "").lower()
                    if want and updater.sha256(path).lower() != want:
                        raise RuntimeError("安装包校验失败（SHA256 不一致）")
                    self._log(f"更新包已下载：{path}")
                    self.toast("下载完成，正在启动安装程序…")
                    updater.launch_installer(path)
                except Exception as ex:  # noqa: BLE001
                    self._log(f"更新下载失败：{ex}", "ERROR")
                    self.toast(f"更新失败：{str(ex)[:80]}", False)
            threading.Thread(target=go, daemon=True).start()

    # ---------- 行为 ----------
    def _check_license(self):
        if getattr(self, "_license_checking", False):
            return
        self._license_checking = True
        try:
            self._check_license_inner()
        finally:
            self._license_checking = False

    def _check_license_inner(self):
        try:
            from ..core.updater import CURRENT as _ver
        except Exception:
            _ver = ""
        try:
            from ..license import machine_code as _dbg_mc
            self._log(f"授权检测：机器码 {_dbg_mc()} / 旧ID {getattr(self.cfg, 'license_identity', '') or '-'}")
        except Exception:
            pass
        res = verify_license(self.cfg.license_server, version=_ver,
                             identity_override=str(getattr(self.cfg, "license_identity", "") or ""),
                             public_key_pem=str(getattr(self.cfg, "license_public_key", "") or ""),
                             store_name=str(getattr(self.cfg, "store_name", "") or ""))
        try:
            self._log(f"授权结果：{res.get('message')} / tried {res.get('tried')}")
        except Exception:
            pass
        self.license_state = res
        self._report_identity = str(res.get("identity") or "")
        if res.get("ok"):
            try:
                from ..core import guard as _guard
                _guard.store(self.cfg, res)
                save_config(self.cfg)
            except Exception:
                pass
            # 启动即回传一次日志（后台线程，失败静默）
            try:
                from ..core import report as _rep
                _rep.report_async(str(getattr(self.cfg, "license_server", "") or ""),
                                  str(res.get("identity") or ""), version=_ver)
            except Exception:
                pass
        try:
            if res.get("ok"):
                self.cfg.set("license_cache_ok", True)
                self.cfg.set("license_cache_expiry", str(res.get("expiry") or ""))
                self.cfg.set("license_cache_ts", int(time.time()))
            elif "无法连接授权服务器" in str(res.get("message") or ""):
                self._log("授权服务器暂不可达，保留上次授权缓存", "WARN")
            # 平台/功能包授权刷新（token 由授权网站下发，旧客户走 legacy 兼容）
            try:
                self.entitlements = load_entitlements(self.cfg)
                self._rebuild_nav()
                if getattr(self, "platform_rail", None) is not None:
                    self.platform_rail.content = self._build_platform_rail().content
                plats = self.entitlements.platforms_list()
                self.license_text.value = ("平台：" + ", ".join(plats)) if plats else "平台未开通"
                self._safe_update()
            except Exception:
                pass
            else:
                self.cfg.set("license_cache_ok", False)
                self.cfg.set("license_cache_ts", 0)
            save_config(self.cfg)
        except Exception:
            pass
        msg = res.get("message") or ("授权有效" if res.get("ok") else "未授权")
        self.license_text.value = "授权有效" if res.get("ok") else "未授权"
        self.license_text.color = ACCENT_2 if res.get("ok") else DANGER_C
        # 设置页的授权卡片同步刷新
        for attr in ("lic_status", "lic_detail"):
            if getattr(self, attr, None) is None:
                continue
        if getattr(self, "lic_status", None) is not None:
            self.lic_status.value = msg
            self.lic_status.color = ACCENT_2 if res.get("ok") else DANGER_C
        if getattr(self, "lic_detail", None) is not None:
            exp = res.get("expiry") or ""
            self.lic_detail.value = (f"到期：{exp}" if exp else
                                     ("授权有效（长期）" if res.get("ok")
                                      else "把上面的机器码发给管理员登记；登记后点「重新检测」"))
        self._apply_license_state()
        try:
            self._safe_update()
        except Exception:
            pass

    def _on_files(self, e):
        if e.files:
            self.picked = [Path(f.path) for f in e.files]
            self.img_status.value = f"已选择 {len(self.picked)} 张图"
            self._safe_update()

    def _maybe_quota(self, msg: str) -> None:
        if getattr(self, "_dlg", None) is not None:
            return
        txt = str(msg)
        if any(k in txt for k in ("额度", "余额", "quota", "insufficient", "欠费", "billing")):
            try:
                def go(e):
                    self._close_dialog()
                    try:
                        self.page.launch_url(self.cfg.api_base or "https://api.moneyfly.club")
                    except Exception:
                        pass
                self.dlg = ft.AlertDialog(
                    title=ft.Text("额度不足"),
                    content=ft.Text(f"本次调用被拒绝：{txt[:160]}\n\n当前价格参考：\n"
                                    f"· 白底/去水印 {model_of(None, 'image_edit')}（¥{self._price_of(model_of(None, 'image_edit')):.3f}/张）\n"
                                    f"· 按提示词改图 {model_of(None, 'image_prompt')}（¥{self._price_of(model_of(None, 'image_prompt')):.3f}/张）\n"
                                    f"· 文生图 {model_of(None, 'image_gen')}（¥{self._price_of(model_of(None, 'image_gen')):.3f}/张）"),
                    actions=[ft.TextButton("稍后", on_click=lambda e: self._close_dialog()),
                             ft.ElevatedButton("去充值", bgcolor="#F0A020", color="#1A1200", on_click=go)])
                self._show_dlg(self.dlg)
            except Exception:
                self.toast("额度不足，请去站点充值", False)

    def toast(self, msg, ok=True):
        self.page.snack_bar.content = ft.Text(msg, color="#FFFFFF")
        self.page.snack_bar.bgcolor = "#1F5137" if ok else "#5A1F28"
        self.page.snack_bar.open = True
        self._safe_update()

    def _safe_update(self):
        """串行化 UI 更新：避免后台线程并发 page.update 导致卡死/重连。

        如果当前有打开的弹窗，page.update 后再把弹窗自身 update 一次，
        防止后台刷新把它顶掉（Flet 0.25 的 show_dialog 栈偶发被重建）。
        """
        if getattr(self, "_building_page", False) or getattr(self, "_page_dead", False):
            return
        lock = getattr(self, "_ui_lock", None)
        try:
            if lock is not None:
                with lock:
                    self.page.update()
            else:
                self.page.update()
            _dlg = getattr(self, "_dlg", None)
            if _dlg is not None and bool(getattr(_dlg, "open", False)):
                try:
                    _dlg.open = True
                    _dlg.update()
                except Exception:
                    pass
        except Exception as ex:  # noqa: BLE001
            # 窗口/事件循环已关闭 → 界面已经死了，后续 UI 更新全部静默跳过，
            # 否则心跳线程会每 15 秒往 error.log 刷一条 "Event loop is closed"。
            if "Event loop is closed" in str(ex) or "has been closed" in str(ex):
                self._page_dead = True
                return
            now = time.time()
            if now - float(getattr(self, "_update_err_ts", 0) or 0) > 5:
                self._update_err_ts = now
                try:
                    from ..config import config_dir
                    p = config_dir() / "error.log"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with p.open("a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} UI_UPDATE {type(ex).__name__}: {ex}\n")
                except Exception:
                    pass


def _install_crash_log():
    """把主线程/子线程未捕获异常写到配置目录 error.log，便于排查“重启/闪退”。"""
    import sys as _sys
    import threading as _th
    from ..config import config_dir

    def _write(kind, exc_type, exc, tb):
        try:
            import traceback
            p = config_dir() / "error.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {kind} =====\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass

    def _hook(exc_type, exc, tb):
        _write("UNCAUGHT", exc_type, exc, tb)
        _sys.__excepthook__(exc_type, exc, tb)

    def _thook(args):
        _write("THREAD", args.exc_type, args.exc_value, args.exc_traceback)

    _sys.excepthook = _hook
    try:
        _th.excepthook = _thook
    except Exception:
        pass


def _assets_dir() -> str:
    """开发/打包两种环境都能找到 assets（打包后资源在 _internal/assets）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[3]
    p = base / "assets"
    return str(p if p.exists() else (Path("assets")))


def _patch_flet_dock_icon():
    """macOS：把 Flet.app 的 Dock 图标换成 AuroraList 的图标。

    Flet 桌面端会用一个独立的 Flet.app 承载 Flutter 窗口，Dock 里显示的是
    它自带的 AppIcon.icns。这里在启动前把该文件替换成我们的图标（原文件备份为
    AppIcon.icns.flet-orig），失败静默——换不了图标也不影响功能。
    """
    if sys.platform != "darwin":
        return
    try:
        from pathlib import Path as _P
        import shutil as _sh
        # 我们的图标
        assets = _P(_assets_dir())
        src = assets / "icon.icns"
        if not src.exists():
            return
        # Flet.app 的图标
        home = _P.home()
        candidates = sorted((home / ".flet" / "bin").glob("flet-*/Flet.app/Contents/Resources/AppIcon.icns"))
        if not candidates:
            return
        dst = candidates[-1]
        bak = dst.with_name("AppIcon.icns.flet-orig")
        # 已替换过（大小一致）就跳过
        try:
            if dst.stat().st_size == src.stat().st_size and bak.exists():
                return
        except Exception:
            pass
        if not bak.exists():
            _sh.copy2(dst, bak)
        _sh.copy2(src, dst)
        # Flet.app 不显示 Dock（主进程 AuroraList 负责显示图标），避免两个图标
        try:
            app = dst.parent.parent.parent   # .../Flet.app
            pl = app / "Contents" / "Info.plist"
            if pl.exists():
                import plistlib as _pl2
                _d = _pl2.loads(pl.read_bytes())
                if _d.get("LSUIElement") is not True:
                    _d["LSUIElement"] = True
                    pl.write_bytes(_pl2.dumps(_d))
                    import subprocess as _sp2
                    _sp2.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                             capture_output=True, text=True)
        except Exception:
            pass
        # 让 Finder/Dock 重新读图标
        try:
            import subprocess as _sp
            _sp.run(["/usr/bin/touch", str(dst.parent.parent.parent)], check=False)
        except Exception:
            pass
    except Exception:
        pass


def _patch_flet_identity():
    """macOS：把 Flet.app 的菜单栏名字从 "Flet" 改成 "AuroraList"。

    Flet.app 是 adhoc 签名，改 Info.plist 后重新 adhoc 签名即可；
    改坏了就恢复备份（.flet-orig），失败静默。
    """
    if sys.platform != "darwin":
        return
    try:
        from pathlib import Path as _P
        import plistlib as _pl
        import shutil as _sh
        import subprocess as _sp

        home = _P.home()
        apps = sorted((home / ".flet" / "bin").glob("flet-*/Flet.app"))
        if not apps:
            return
        app = apps[-1]
        plist = app / "Contents" / "Info.plist"
        if not plist.exists():
            return
        bak = plist.with_name("Info.plist.flet-orig")
        try:
            data = _pl.loads(plist.read_bytes())
        except Exception:
            return
        # 已经是 AuroraList 就跳过
        if str(data.get("CFBundleDisplayName") or "") == "AuroraList":
            return
        if not bak.exists():
            _sh.copy2(plist, bak)
        data["CFBundleDisplayName"] = "AuroraList"
        data["CFBundleName"] = "AuroraList"
        try:
            plist.write_bytes(_pl.dumps(data))
        except Exception:
            return
        # 重新 adhoc 签名；失败就恢复原 plist
        r = _sp.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                    capture_output=True, text=True)
        if r.returncode != 0:
            try:
                _sh.copy2(bak, plist)
                _sp.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                        capture_output=True, text=True)
            except Exception:
                pass
    except Exception:
        pass


def run():
    _install_crash_log()
    _patch_flet_dock_icon()
    _patch_flet_identity()
    ft.app(target=lambda page: App(page), assets_dir=_assets_dir())
