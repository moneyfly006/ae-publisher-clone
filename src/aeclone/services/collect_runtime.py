"""Bounded collection runtime. Retry only explicitly listed read operations."""
from functools import wraps
from copy import deepcopy
import math
import re
import time

from .progress import emit
from ..collect_defaults import PARAMETERS, PRESETS


def parameter_value(suffix, value):
    label, _, low, high, integer = PARAMETERS[suffix]
    try:
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number) or not low <= number <= high or (integer and number != int(number)):
            raise ValueError()
        return int(number) if integer else number
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label}应为{low}至{high}的{'整数' if integer else '数字'}") from None


def apply_preset(cfg, platform, name):
    if platform not in ("alibaba", "amazon", "tiktok", "shopee") or name not in PRESETS:
        raise ValueError("未知平台或运行预设")
    for suffix, value in PRESETS[name][1].items():
        cfg.set(platform + "_collect_" + suffix, value)


def options(cfg, platform):
    return {suffix: parameter_value(suffix, getattr(cfg, platform + "_collect_" + suffix, spec[1]))
            for suffix, spec in PARAMETERS.items()}


def interrupted_wait(seconds, stop=None):
    deadline = time.monotonic() + seconds
    while True:
        if stop and stop():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(.1, remaining))


def login_error(error):
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return True
    return bool(re.search(r"登录.*(?:失效|过期)|未登录|重新.*Cookie|HTTP\s*(?:401|403)|unauthori[sz]ed", str(error), re.I))


def transient_error(error):
    if login_error(error):
        return False
    if getattr(getattr(error, "response", None), "status_code", None) in (429, 500, 502, 503, 504):
        return True
    import requests
    return isinstance(error, (TimeoutError, ConnectionError, requests.Timeout, requests.ConnectionError)) or bool(
        re.search(r"请求超时|网络连接失败|系统繁忙|请求过于频繁|HTTP\s*(?:429|50[0234])|timed?\s*out|rate limit", str(error), re.I))


def verified_product(detail, product_id):
    if not isinstance(detail, dict):
        raise ValueError("商品详情格式异常，本件未继续处理")
    product = detail.get("product", detail)
    if not isinstance(product, dict) or str(product.get("idStr") or product.get("id") or product.get("productId") or "") != str(product_id):
        raise ValueError("商品详情ID不匹配或为空，本件未继续处理")
    return product


_STOP_REQUESTED = False
_RUN_ACTIVE = False


def begin_run() -> None:
    """进入一次真实批处理会话；单元测试直接调用服务时不激活。"""
    global _STOP_REQUESTED, _RUN_ACTIVE
    _STOP_REQUESTED = False
    _RUN_ACTIVE = True


def end_run() -> None:
    global _STOP_REQUESTED, _RUN_ACTIVE
    _STOP_REQUESTED = False
    _RUN_ACTIVE = False


def request_stop() -> None:
    """UI 停止按钮写当前运行会话的中止信号，所有耗时段尽快退出。"""
    global _STOP_REQUESTED
    if _RUN_ACTIVE:
        _STOP_REQUESTED = True


def clear_stop() -> None:
    global _STOP_REQUESTED, _RUN_ACTIVE
    _STOP_REQUESTED = False
    _RUN_ACTIVE = False


def stop_requested() -> bool:
    return bool(_RUN_ACTIVE and _STOP_REQUESTED)


class RunStopped(RuntimeError):
    pass


def product_scope_issue(platform, product, shop_id, site_id):
    if platform == "amazon":
        from ..integrations.amazon import product_scope_issue as amazon_scope_issue
        return amazon_scope_issue(product, shop_id, site_id)
    product_shop = str(product.get("shopIdStr") or product.get("shopId") or product.get("shop_id") or "").strip()
    if shop_id not in ("", "0", "-1") and product_shop not in ("", "0", "-1", shop_id):
        return "商品属于另一店铺，当前店铺预设未应用"
    if platform == "tiktok" and site_id:
        from .tiktok_prompts import normalize_site
        product_site = normalize_site(product.get("site") or product.get("siteCode"))
        if product_site not in ("", "0", normalize_site(site_id)):
            return "商品属于另一 TikTok 站点"
    return ""


class ReadClient:
    READS = frozenset(("list_products", "product_detail", "required_attributes", "required_attributes_from_schema",
        "category_attributes", "attributes", "limits", "publish_state", "counts", "categories",
        "category_history", "category_by_path", "logistics", "freight_templates", "shops", "site_limits"))

    def __init__(self, client, cfg, platform, stop=None):
        self.client, self.platform, self.stop = client, platform, stop
        self.settings = options(cfg, platform)
        self.auth_failed = False
        self.expected_shop = str(getattr(cfg, platform + "_shop_id", "") or "").strip()
        self.expected_site = str(getattr(cfg, platform + "_site", "") or "").strip() if platform in ("amazon", "tiktok") else ""
        self.read_stopped = False
        self._details = {}
        self._reference = {}
        self._fresh = False
        # Disable nested retry loops for this run only. A method gets at most
        # 1 + read_retries attempts, never that count squared.
        for target in (client, getattr(client, "http", None)):
            if target is not None:
                if hasattr(target, "timeout"):
                    target.timeout = self.settings["request_timeout"]
                if type(target).__module__ in ("aeclone.integrations.amazon", "aeclone.integrations.tiktok"):
                    target.read_retries = 0

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in self.READS or not callable(method):
            return method
        @wraps(method)
        def read(*args, **kwargs):
            key = repr((name, args, sorted(kwargs.items())))
            reference = name in ("required_attributes", "required_attributes_from_schema", "category_attributes", "attributes", "limits", "categories", "category_history", "category_by_path", "logistics", "freight_templates", "site_limits")
            if reference and key in self._reference:
                return deepcopy(self._reference[key])
            if name == "product_detail" and not self._fresh and key in self._details:
                return deepcopy(self._details[key])
            for attempt in range(self.settings["read_retries"] + 1):
                if attempt and self.stop and self.stop():
                    self.read_stopped = True
                    raise RunStopped("已停止后续读取；已提交的商品请查看结果回读")
                try:
                    value = method(*args, **kwargs)
                    if name == "product_detail":
                        product_id = args[0] if args else kwargs.get("product_id", "")
                        product = verified_product(value, product_id)
                        issue = product_scope_issue(self.platform, product, self.expected_shop, self.expected_site)
                        if issue:
                            raise ValueError(issue + "，请切换到商品所属店铺和站点")
                        if not self._fresh:
                            self._details[key] = deepcopy(value)
                    elif reference:
                        self._reference[key] = deepcopy(value)
                    return deepcopy(value)
                except Exception as error:
                    if login_error(error):
                        self.auth_failed = True
                    if not transient_error(error) or attempt >= self.settings["read_retries"]:
                        raise
                    emit(self.platform + " · 只读重试", "等待", f"{name} 第{attempt + 1}次重试，保存/发布不重试", level="WARN")
                    if not interrupted_wait(min(2 ** attempt, 4), self.stop):
                        self.read_stopped = True
                        raise RunStopped("已停止读取重试") from error
        return read

    def fresh(self):
        self._details.clear()
        self._fresh = True

    def prepare_item(self):
        self._details.clear()
        self._fresh = False
        self.read_stopped = False


class RunPolicy:
    def __init__(self, cfg, platform, stop=None):
        self.settings, self.platform, self.stop = options(cfg, platform), platform, stop
        self.failures = 0

    def before(self, index, *, dry_run=False):
        if self.stop and self.stop():
            return False
        return index == 1 or dry_run or interrupted_wait(self.settings["interval"], self.stop)

    def after(self, record, client):
        self.failures = 0 if record["ok"] else self.failures + 1
        reason = ""
        if client.auth_failed or login_error(record.get("error", "")):
            reason = "店小秘登录已失效，请更新 Cookie 后重新检查范围"
        elif self.settings["failure_limit"] and self.failures >= self.settings["failure_limit"]:
            reason = f"连续失败{self.failures}件，达到停止阈值；请先查看日志并修正问题"
        if reason:
            record["halt_reason"] = reason
            emit(self.platform + " · 运行保护", "已停止", reason, level="ERROR")
        return not reason
