"""Classify authoritative platform errors. Review notices never hide other errors."""
from dataclasses import dataclass, field
import re


@dataclass
class Failure:
    kind: str
    message: str
    action: str
    repairable: bool = False
    required: list[str] = field(default_factory=list)


def classify(platform, error):
    text = str(error or "")
    codes = set(re.findall(r"code\s*[:=：]\s*(\d+)", text, re.I))
    result = []
    if "CAT_PUB_RESTRICT" in text or "经营类目范围" in text:
        result.append(Failure("category_scope", "商品类目不在店铺经营范围",
            "到 Alibaba 卖家中心 → 店铺管理 → 管理公司信息开通实际经营类目；保持商品真实分类，不自动改成其他类目"))
    if "CHK_VIDEO_PRODUCT_LINK_LIMIT" in text:
        result.append(Failure("video_limit", "主视频复用次数超限", "在平台更换或解除重复主视频后重试"))
    if "8560" in codes or "externally_assigned_product_identifier" in text:
        result.append(Failure("identifier", "缺少有效商品标识码或无法创建 ASIN", "填写商品真实 UPC/EAN/GTIN 或核实已获批的 GTIN 豁免；不生成虚假识别码"))
    if "100470" in codes or "两次以上" in text or "标题重复" in text:
        result.append(Failure("title_duplicate", "标题重复词超过平台限制", "清理重复词并同步 item_name 属性", True))
    if "90220" in codes or re.search(r"属性:|必填.*缺失", text):
        required = list(dict.fromkeys(re.findall(r"属性[:：]\s*([a-zA-Z_][\w]*)", text)))
        result.append(Failure("required_attributes", "缺少平台必填字段", "从本商品真实字段补齐；缺少事实依据时列为待补资料", True, required))
    if "13013" in codes:
        result.append(Failure("catalog", "商品尚未进入 Amazon 型录",
            "先修正同条错误中的字段问题；如仍报13013，核实原商品识别码、ASIN 和型录状态，避免重复提交"))
    if "2005" in codes or "频繁" in text or "rate limit" in text.lower():
        result.append(Failure("rate_limit", "平台请求频率受限", "稍后重查状态；保存响应不确定时先回读，不盲目重发"))
    known = {"100521", "100470", "90220", "8560", "13013", "2005"}
    if codes - known:
        result.append(Failure("platform_error", "平台其他校验错误：" + ",".join(sorted(codes - known)), "保留原错误与修复记录，核实平台要求后处理"))
    if result:
        return result
    if "100521" in codes or "PRODUCT_IN_AUDITING" in text or "审核中" in text or "正在审查" in text or "under review" in text.lower():
        return [Failure("reviewing", "平台审核中", "等待平台审核，无需重复发布")]
    return [Failure("unknown", "平台未返回可自动处理的明确原因", "读取最新详情并检查完整错误；不自动猜测或无限重试")]


def review_only(platform, error):
    return all(f.kind == "reviewing" for f in classify(platform, error))
