"""速卖通（店小秘 SMT）类目属性：解析、缺失检测与补全（纯函数，无网络、无写入）。

背景
----
速卖通保存走 ``/api/smtProduct/add.json``，属性字段 ``aeopAeProductPropertys``
有两种形态（见 ``docs/ADD-JSON-REVERSE-NOTES.md``）：

- 系统属性：``{"attrNameId": 2, "attrValueId": 201512802}``（必须带类目枚举 id，
  只靠名字不生效；id 为 -1/None/"None" 会被平台判为脏值）；
- 自定义属性：``{"attrName": "Power Type", "attrValue": "Gasoline"}``。

采集来的商品（草稿/待发布）往往只有自定义属性，缺少类目必填属性的系统 id；
或者反过来只有 id、``attrName`` 为空。类目属性定义来自
``/api/smtCategory/attributeList.json``（每项 ``arrtNameId`` / ``nameEn`` /
``nameZh`` / ``required`` / ``sku`` / ``values``；``values`` 是 JSON 字符串，
每个枚举值形如 ``{"names": {"en": "...", "zh": "..."}, "id": "...", "hasSubAttr": 0}``）。

本模块只做纯计算：
1. 解析类目属性定义 / 商品已有属性 / SKU 规格；
2. 把商品已有属性按「名称/中文名/别名/SKU 属性」映射到类目属性；
3. 缺值时依次用「商品自身值 → 模板商品系统属性 → 通用默认值 → AI 建议」补；
4. 产出 ``{target_name: value}``、``missing`` 列表和中文原因；
5. 提供 ``build_missing_report`` / ``merge_attributes`` 等给 Publisher / UI 用。

不在这里做的事：发请求、保存商品、改配置。调用方（``DianxiaomiClient`` /
``Publisher``）负责取数，本模块只消费字典，便于离线单测。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------- 常量

#: 缺失类型（与 docs/aliexpress-attributes-verification.json 的 key 一致）
MISSING_NO_CATEGORY = "no_category"
MISSING_REQUIRED_NO_VALUE = "required_no_value"
MISSING_VALUE_ID = "value_id_missing"
MISSING_SKU_MISMATCH = "sku_attribute_mismatch"
MISSING_TEMPLATE_NOT_APPLIED = "template_attribute_not_applied"

MISSING_TYPE_LABELS: dict[str, str] = {
    MISSING_NO_CATEGORY: "无类目",
    MISSING_REQUIRED_NO_VALUE: "必填属性无值",
    MISSING_VALUE_ID: "属性值 id/名称缺失",
    MISSING_SKU_MISMATCH: "SKU 属性不匹配",
    MISSING_TEMPLATE_NOT_APPLIED: "模板属性未套用",
}

#: 店小秘/速卖通把以下值都当作「没有 id」
_INVALID_ID_TEXT = {"", "none", "null", "-1", "0"}

#: 中文名 / 常见采集字段 → 类目属性英文名（归一化后的 key）
ALIASES: dict[str, str] = {
    # 品牌
    "brand": "brand name",
    "brands": "brand name",
    "品牌": "brand name",
    "品牌名": "brand name",
    "品牌名称": "brand name",
    # 产地
    "place of origin": "origin",
    "origin place": "origin",
    "country of origin": "origin",
    "country/region of origin": "origin",
    "产地": "origin",
    "原产地": "origin",
    "产地（国家或地区）": "origin",
    # 是否带电 / 高关注化学品
    "charge status": "charge status",
    "是否带电": "charge status",
    "带电状态": "charge status",
    "high-concerned chemical": "high-concerned chemical",
    "high concerned chemical": "high-concerned chemical",
    "高关注化学品": "high-concerned chemical",
    "高关注化学物质": "high-concerned chemical",
    # 类型 / 型号 / 成色 / 认证
    "type": "type",
    "类型": "type",
    "model number": "model number",
    "model": "model number",
    "型号": "model number",
    "condition": "condition",
    "成色": "condition",
    "状态": "condition",
    "certification": "certification",
    "认证": "certification",
    # 变种维度
    "ships from": "ships from",
    "ship from": "ships from",
    "发货地": "ships from",
    "color": "color",
    "colour": "color",
    "颜色": "color",
    "plug type": "plug type",
    "插头类型": "plug type",
    "material": "material",
    "材质": "material",
    # 通用属性
    "product attr": "product attr",
    "product attribute": "product attr",
    "产品属性": "product attr",
    "商品属性": "product attr",
    "weight": "weight",
    "gross weight": "weight",
    "重量": "weight",
    "毛重": "weight",
    "product name": "name",
    "商品名称": "name",
    "产品名称": "name",
    "usage": "usage",
    "用途": "usage",
    "application": "application",
    "应用": "application",
    "warranty": "warranty",
    "质保": "warranty",
    "保修": "warranty",
    "power type": "power type",
    "动力类型": "power type",
    "voltage": "voltage",
    "电压": "voltage",
    "dimension": "dimension",
    "尺寸": "dimension",
}

#: 通用默认值：类目枚举里能匹配到就用（宁可留空也不编造规格）。
#: 这些是速卖通系统属性的安全默认（与 Publisher 现有模板兜底一致）。
DEFAULT_VALUE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "brand name": ("NONE", "No Brand", "无品牌"),
    "origin": ("Mainland China", "China", "中国大陆", "中国大陆(Origin)"),
    "charge status": ("No", "否"),
    "high-concerned chemical": ("None", "No", "无"),
    "condition": ("New", "全新"),
    "certification": ("None", "NONE"),
    "type": ("Other", "其他"),
}

_SOURCE_LABELS = {
    "product": "商品已有系统属性",
    "product_text": "商品已有属性（按名称/枚举回填 id）",
    "product_free": "商品已有自定义属性（自由文本）",
    "template": "模板商品系统属性",
    "default": "通用默认值",
    "ai": "AI 建议",
}

# ---------------------------------------------------------------- 基础工具


def is_valid_id(value: Any) -> bool:
    """店小秘里的属性 id 是否真的有效（-1/None/"None"/0 都算没有）。"""
    text = str(value if value is not None else "").strip().lower()
    return text not in _INVALID_ID_TEXT


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _strip_parenthetical(text: str) -> str:
    """去掉中英文括号里的补充说明：``产地（国家或地区）`` → ``产地``。"""
    text = re.sub(r"[（(][^）)]*[）)]", " ", text)
    return _clean_text(text)


def normalize_name(name: Any) -> str:
    """属性名归一化：小写、去标点/括号、去前缀、去复数、同义词归并。

    与 ``api_save._norm_attr_name`` 的方向保持一致，但额外支持中文名和
    更多采集字段别名（本模块只做匹配，不参与保存 payload 的去重决策）。
    """
    text = _strip_parenthetical(str(name if name is not None else "")).lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", text)
    text = _clean_text(text)
    for prefix in ("product ", "item ", "custom ", "the "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.endswith("s") and not text.endswith(("ss", "us", "is")):
        text = text[:-1]
    text = _clean_text(text)
    return ALIASES.get(text, text)


def normalize_value(text: Any) -> str:
    """属性值归一化：小写、去括号补充（``中国大陆(Origin)`` → ``中国大陆``）。"""
    value = _strip_parenthetical(str(text if text is not None else "")).lower()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _json_list(raw: Any, *keys: str) -> list:
    """把「JSON 字符串 / list / 带包装的 dict」统一成 list。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return []
    if isinstance(raw, dict):
        for key in keys:
            if key in raw:
                return _json_list(raw[key], *keys)
        return []
    if isinstance(raw, (list, tuple)):
        return [item for item in raw if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True)
class EnumValue:
    """类目属性允许值（速卖通枚举值）。"""

    id: str = ""
    name_en: str = ""
    name_zh: str = ""
    has_sub_attr: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_raw(cls, raw: Any) -> "EnumValue | None":
        if isinstance(raw, (str, int, float)):
            text = _clean_text(raw)
            return cls(id=text, name_en=text) if text else None
        if not isinstance(raw, dict):
            return None
        names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
        name_en = _clean_text(
            raw.get("name_en") or raw.get("nameEn") or raw.get("name")
            or names.get("en") or ""
        )
        name_zh = _clean_text(
            raw.get("name_zh") or raw.get("nameZh") or raw.get("name_cn")
            or names.get("zh") or ""
        )
        value_id = _clean_text(raw.get("id") or raw.get("valueId") or raw.get("value_id") or "")
        if not value_id and not name_en and not name_zh:
            return None
        return cls(id=value_id, name_en=name_en, name_zh=name_zh,
                   has_sub_attr=bool(raw.get("hasSubAttr") or raw.get("has_sub_attr")),
                   raw=raw)

    @property
    def display(self) -> str:
        return self.name_en or self.name_zh or self.id


@dataclass(frozen=True)
class CategoryAttribute:
    """类目属性定义（来自 /api/smtCategory/attributeList.json）。"""

    name_id: str = ""
    name_en: str = ""
    name_zh: str = ""
    required: bool = False
    sku: bool = False
    visible: bool = True
    input_type: str = ""
    show_type: str = ""
    support_enum_input: bool = False
    values: tuple[EnumValue, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_raw(cls, raw: Any) -> "CategoryAttribute | None":
        if not isinstance(raw, dict):
            return None
        name_id = _clean_text(
            raw.get("arrtNameId") if raw.get("arrtNameId") is not None else
            (raw.get("attrNameId") if raw.get("attrNameId") is not None else raw.get("nameId"))
        )
        name_en = _clean_text(raw.get("nameEn") or raw.get("name_en") or "")
        name_zh = _clean_text(raw.get("nameZh") or raw.get("name_zh") or raw.get("name") or "")
        if not name_id and not name_en and not name_zh:
            return None
        raw_values = raw.get("values")
        if isinstance(raw_values, str):
            try:
                raw_values = json.loads(raw_values)
            except (TypeError, ValueError):
                raw_values = []
        values: list[EnumValue] = []
        for item in raw_values if isinstance(raw_values, (list, tuple)) else []:
            enum = EnumValue.from_raw(item)
            if enum is not None:
                values.append(enum)
        visible = raw.get("visible", 1)
        return cls(
            name_id=name_id,
            name_en=name_en,
            name_zh=name_zh,
            required=bool(raw.get("required")),
            sku=bool(raw.get("sku")),
            visible=str(visible).strip().lower() not in ("0", "false", "none", ""),
            input_type=_clean_text(raw.get("inputType") or raw.get("input_type") or ""),
            show_type=_clean_text(
                raw.get("attributeShowTypeValue") or raw.get("showType") or raw.get("show_type") or ""
            ),
            support_enum_input=bool(raw.get("supportEnumInput") or raw.get("support_enum_input")),
            values=tuple(values),
            raw=raw,
        )

    @property
    def display_name(self) -> str:
        return self.name_en or self.name_zh or self.name_id

    @property
    def key(self) -> str:
        return normalize_name(self.name_en or self.name_zh)

    @property
    def allows_free_text(self) -> bool:
        """是否允许提交自由文本（列表型枚举必须用 id，不能只写字符串）。"""
        if self.support_enum_input:
            return True
        if self.show_type.lower() in ("input", "text", "textarea"):
            return True
        return not self.values

    def value_by_id(self, value_id: Any) -> "EnumValue | None":
        target = _clean_text(value_id)
        if not target:
            return None
        for value in self.values:
            if value.id == target:
                return value
        return None

    def value_by_name(self, text: Any) -> "EnumValue | None":
        """按枚举英文名/中文名（归一化）匹配一个允许值。"""
        target = normalize_value(text)
        if not target:
            return None
        for value in self.values:
            if target == normalize_value(value.name_en) or target == normalize_value(value.name_zh):
                return value
        # "NONE" / "无" 之类的同义词兜底
        for value in self.values:
            en, zh = normalize_value(value.name_en), normalize_value(value.name_zh)
            if target in (en, zh) or {target} == {en} or {target} == {zh}:
                return value
        return None

    def resolve_value(self, text: Any) -> "EnumValue | None":
        """按值文本或值 id 匹配允许值；自由文本属性返回 None。"""
        text_value = _clean_text(text)
        if not text_value:
            return None
        by_id = self.value_by_id(text_value)
        if by_id is not None:
            return by_id
        return self.value_by_name(text_value)


@dataclass(frozen=True)
class ProductAttribute:
    """商品已有属性（edit.json 的 aeopAeProductPropertys 单项）。"""

    name: str = ""
    name_id: str = ""
    value: str = ""
    value_id: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_raw(cls, raw: Any) -> "ProductAttribute | None":
        if not isinstance(raw, dict):
            return None
        name = _clean_text(raw.get("attrName") or raw.get("name") or "")
        value = _clean_text(raw.get("attrValue") or raw.get("value") or "")
        name_id = _clean_text(
            raw.get("attrNameId") if raw.get("attrNameId") is not None else raw.get("nameId")
        )
        value_id = _clean_text(
            raw.get("attrValueId") if raw.get("attrValueId") is not None else raw.get("valueId")
        )
        if not name and not name_id and not value and not value_id:
            return None
        return cls(name=name, name_id=name_id if is_valid_id(name_id) else "",
                   value=value, value_id=value_id if is_valid_id(value_id) else "", raw=raw)

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    @property
    def has_valid_ids(self) -> bool:
        return bool(self.name_id and self.value_id)

    @property
    def display(self) -> str:
        return self.name or self.name_id


@dataclass
class FillResult:
    """单个商品的属性补全结果（不含请求/写入）。"""

    targets: dict[str, str] = field(default_factory=dict)
    system_props: list[dict] = field(default_factory=list)
    custom_props: list[dict] = field(default_factory=list)
    attributes: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    unresolved_optional: list[dict] = field(default_factory=list)
    name_by_id: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def filled_count(self) -> int:
        return len(self.system_props) + len(self.custom_props)


@dataclass
class MergeResult:
    """merge_attributes 的结果。"""

    props: list[dict] = field(default_factory=list)
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    renamed: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def json(self) -> str:
        return json.dumps(self.props, ensure_ascii=False)

    def payload(self) -> dict:
        """可直接灌进 build_save_payload 的 overrides（仅属性字段）。"""
        return {"aeopAeProductPropertys": self.json}


# ---------------------------------------------------------------- 解析


def parse_category_attributes(raw: Any) -> list[CategoryAttribute]:
    """解析类目属性定义（兼容 JSON 字符串 / list / {data|attributeList|list} 包装）。"""
    out: list[CategoryAttribute] = []
    seen: set[str] = set()
    for item in _json_list(raw, "data", "attributeList", "attributes", "list", "attributeListJson"):
        attr = CategoryAttribute.from_raw(item)
        if attr is None:
            continue
        dedupe_key = attr.name_id or f"name:{attr.key}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(attr)
    return out


def parse_product_attributes(raw: Any) -> list[ProductAttribute]:
    """解析商品已有属性（兼容 JSON 字符串 / list / product dict）。"""
    if isinstance(raw, dict) and "product" in raw and isinstance(raw.get("product"), dict):
        raw = raw["product"]
    items = _json_list(raw, "aeopAeProductPropertys", "attributes", "data", "product")
    out: list[ProductAttribute] = []
    for item in items:
        attr = ProductAttribute.from_raw(item)
        if attr is not None:
            out.append(attr)
    return out


def parse_skus(raw: Any) -> list[dict]:
    """解析商品 SKU 列表（兼容 JSON 字符串 / product dict）。"""
    if isinstance(raw, dict) and "product" in raw and isinstance(raw.get("product"), dict):
        raw = raw["product"]
    if isinstance(raw, dict):
        for key in ("aeopAeProductSKUs", "skus", "skuList"):
            if key in raw:
                return parse_skus(raw[key])
        return []
    return _json_list(raw)


def sku_property_ids(skus: Sequence[Mapping[str, Any]],
                     *, include_plug_parents: bool = False) -> set[str]:
    """商品 SKU 用到的规格属性 id。

    ``plug_parent_property_id`` 是速卖通 plug 库的父级指针，不在
    ``attributeList`` 里、也不是 SKU 维度本身；默认不计入「商品使用的维度」，
    否则模板商品合法的 plug 结构会被误判成「SKU 属性不匹配」。
    """
    out: set[str] = set()
    keys = ["skuPropertyId", "parentPropertyId"]
    if include_plug_parents:
        keys.append("plug_parent_property_id")
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        for prop in sku.get("aeopSKUProperty") or []:
            if not isinstance(prop, dict):
                continue
            for key in keys:
                if is_valid_id(prop.get(key)):
                    out.add(_clean_text(prop.get(key)))
    return out


def allowed_sku_property_ids(category_attrs: Sequence[CategoryAttribute],
                             *, template_skus: Sequence[Mapping[str, Any]] | None = None,
                             extra_sku_property_ids: Iterable[str] | None = None) -> set[str]:
    """合法 SKU 维度 id 集合：类目 sku 属性 + 模板商品 SKU 用到的 id + 人工白名单。"""
    allowed = {attr.name_id for attr in category_attrs if attr.sku and is_valid_id(attr.name_id)}
    if template_skus:
        allowed |= sku_property_ids(template_skus, include_plug_parents=True)
    for value in extra_sku_property_ids or []:
        if is_valid_id(value):
            allowed.add(_clean_text(value))
    return allowed


def forecast_category_candidates(product: Mapping[str, Any]) -> list[str]:
    """无类目商品通常带 forecastCategoryId（分号分隔的候选叶子类目）。"""
    if not isinstance(product, Mapping):
        return []
    source = product
    if isinstance(product.get("product"), Mapping):
        source = product["product"]  # type: ignore[assignment]
    raw = ""
    for key in ("forecastCategoryId", "forecast_category_id", "autoCategoryId"):
        if source.get(key):
            raw = str(source.get(key))
            break
        raw_parent = product.get("raw")
        if isinstance(raw_parent, Mapping) and raw_parent.get(key):
            raw = str(raw_parent.get(key))
            break
    seen: list[str] = []
    for item in re.split(r"[;,]", raw):
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def suggest_category_id(product: Mapping[str, Any]) -> str:
    """无类目商品的建议类目：取预测类目列表的第一个。"""
    candidates = forecast_category_candidates(product)
    return candidates[0] if candidates else ""


def template_defaults(template: Any) -> list[ProductAttribute]:
    """从模板商品的属性里提取可复用的系统属性（只保留 id 有效的）。"""
    attrs = template if isinstance(template, list) else parse_product_attributes(template)
    return [attr for attr in attrs if isinstance(attr, ProductAttribute) and attr.has_valid_ids]


def universal_defaults(category_attrs: Sequence[CategoryAttribute]) -> dict[str, dict]:
    """按类目属性给出安全通用默认值（Brand/Origin/Charge Status 等）。

    返回 ``{归一化 name 或 name_id: {value, value_id}}``；只返回枚举里真实
    存在的取值，绝不编造规格。调用方可再追加自己的默认值表。
    """
    out: dict[str, dict] = {}
    for attr in category_attrs:
        prefs = DEFAULT_VALUE_PREFERENCES.get(attr.key)
        if not prefs:
            continue
        for preferred in prefs:
            enum = attr.value_by_name(preferred)
            if enum is not None and enum.id:
                item = {"value": enum.display, "value_id": enum.id}
                if attr.name_id:
                    out[attr.name_id] = item
                out[attr.key] = item
                break
    return out


# ---------------------------------------------------------------- 匹配 / 填充


def _match_product_attribute(attr: CategoryAttribute,
                             product_attrs: Sequence[ProductAttribute]) -> ProductAttribute | None:
    """把商品已有属性映射到类目属性：先按 attrNameId，再按英文名/中文名/别名。"""
    if attr.name_id:
        for candidate in product_attrs:
            if candidate.name_id == attr.name_id:
                return candidate
    key = attr.key
    for candidate in product_attrs:
        if candidate.key and candidate.key == key:
            return candidate
    return None


def _template_for(attr: CategoryAttribute, defaults: Sequence[ProductAttribute]) -> ProductAttribute | None:
    if attr.name_id:
        for candidate in defaults:
            if candidate.name_id == attr.name_id:
                return candidate
    key = attr.key
    for candidate in defaults:
        if candidate.key and candidate.key == key:
            return candidate
    return None


def _default_for(attr: CategoryAttribute, defaults: Mapping[str, dict]) -> dict | None:
    if attr.name_id and attr.name_id in defaults:
        return dict(defaults[attr.name_id])
    if attr.key and attr.key in defaults:
        return dict(defaults[attr.key])
    return None


def _display_value(attr: CategoryAttribute, value: str, value_id: str) -> str:
    if value_id:
        enum = attr.value_by_id(value_id)
        if enum is not None:
            return enum.display
    return value or (attr.value_by_id(value_id).display if attr.value_by_id(value_id) else "")


def _record(attr: CategoryAttribute, *, status: str, source: str = "", value: str = "",
            value_id: str = "", note: str = "") -> dict:
    return {
        "name": attr.display_name,
        "name_en": attr.name_en,
        "name_zh": attr.name_zh,
        "name_id": attr.name_id,
        "required": attr.required,
        "sku": attr.sku,
        "status": status,
        "source": source,
        "source_label": _SOURCE_LABELS.get(source, ""),
        "value": value,
        "value_id": value_id,
        "note": note,
    }


def _missing(attr: CategoryAttribute, reason: str, *, detail: str = "",
             matched_value: str = "") -> dict:
    return {
        "name": attr.display_name,
        "name_en": attr.name_en,
        "name_zh": attr.name_zh,
        "name_id": attr.name_id,
        "required": attr.required,
        "sku": attr.sku,
        "reason": reason,
        "detail": detail,
        "matched_value": matched_value,
    }


def fill_attributes(category_attrs: Sequence[CategoryAttribute],
                    product_attrs: Sequence[ProductAttribute],
                    *, template_props: Sequence[ProductAttribute] | None = None,
                    defaults: Mapping[str, dict] | None = None,
                    key: str = "name_en",
                    include_optional: bool = True) -> FillResult:
    """把商品已有属性 + 模板 + 默认值映射成类目属性，产出补全结果。

    参数
    ----
    category_attrs: 类目属性定义（``parse_category_attributes`` 的结果）。
    product_attrs: 商品已有属性（``parse_product_attributes`` 的结果）。
    template_props: 模板商品的系统属性（``template_defaults`` 的结果）。
    defaults: 额外默认值，形如 ``{name_id 或归一化 name: {"value","value_id"}}``。
    key: targets 的键用 ``name_en`` / ``name_zh`` / ``name_id``。
    include_optional: 是否也尝试补非必填可见属性（默认 True）。

    返回
    ----
    FillResult。``targets`` 是 ``{target_name: value}``；``missing`` 只包含
    **必填且补不上**的属性（每条带中文 ``reason``）；``attributes`` 保留逐项明细。
    """
    template_props = list(template_props or [])
    defaults = dict(defaults or {})
    result = FillResult()
    targets: dict[str, str] = {}
    sources: dict[str, int] = {name: 0 for name in _SOURCE_LABELS}
    required_sources: dict[str, int] = {name: 0 for name in _SOURCE_LABELS}
    required_total = required_filled = 0

    for attr in category_attrs:
        if not attr.visible:
            continue
        if attr.sku:
            # SKU 维度属性由 SKU 行承载，不写进商品级属性；这里只记录名称映射。
            result.name_by_id[attr.name_id] = attr.display_name
            continue
        if not attr.required and not include_optional:
            continue
        if attr.required:
            required_total += 1

        matched = _match_product_attribute(attr, product_attrs)
        status = ""
        source = ""
        value = value_id = ""
        system_prop: dict | None = None
        custom_prop: dict | None = None
        note = ""

        # 逐级兜底：商品自身 → 模板商品 → 通用默认值；前一级的值不可用
        # （例如枚举不匹配）时继续往下一级找，全部失败才记 missing。
        blocked_reason = ""
        blocked_detail = ""
        blocked_value = ""

        # ① 商品自身已有系统属性（id 有效）
        if matched is not None and matched.has_valid_ids:
            value_id = matched.value_id
            value = _display_value(attr, matched.value, value_id) or matched.value
            if not attr.value_by_id(value_id) and attr.values:
                # id 不在当前类目枚举里：不能直接提交，按值文本重新匹配
                enum = attr.resolve_value(matched.value) if matched.value else None
                if enum is not None and enum.id:
                    value_id = enum.id
                    system_prop = {"attrNameId": attr.name_id, "attrValueId": enum.id}
                    source = "product_text"
                else:
                    blocked_value = matched.value or matched.value_id
                    blocked_reason = (f"商品里已有「{attr.display_name}={matched.value}」，"
                                      f"但属性 id {matched.value_id} 不在当前类目可选值中")
                    blocked_detail = "value_not_in_enum"
            else:
                system_prop = {"attrNameId": attr.name_id, "attrValueId": value_id}
                source = "product"
        # ② 商品有名称/值但没有系统 id：按枚举名回填 id（这是最常见的修复）
        elif matched is not None and (matched.value or matched.name):
            raw_value = matched.value
            enum = attr.resolve_value(raw_value) if raw_value else None
            if enum is not None and enum.id:
                value_id = enum.id
                value = enum.display
                system_prop = {"attrNameId": attr.name_id, "attrValueId": enum.id}
                source = "product_text"
            elif attr.allows_free_text and raw_value:
                value = raw_value
                custom_prop = {"attrName": attr.display_name, "attrValue": raw_value}
                source = "product_free"
            else:
                blocked_value = raw_value or matched.name
                blocked_reason = (f"「{attr.display_name}」当前值「{raw_value}」不在类目可选值中"
                                  if raw_value else
                                  f"「{attr.display_name}」只有属性名没有值")
                blocked_detail = "value_not_in_enum" if raw_value else "no_value"

        # ③ 仍没有：用模板商品的系统属性（采集商品的系统属性常在模板里）
        if not system_prop and not custom_prop:
            tpl = _template_for(attr, template_props)
            if tpl is not None and (tpl.value_id or tpl.value):
                enum = (attr.value_by_id(tpl.value_id) or attr.value_by_name(tpl.value)
                        if tpl.value_id else attr.value_by_name(tpl.value))
                if enum is not None and enum.id:
                    value_id = enum.id
                    value = enum.display or tpl.value
                    system_prop = {"attrNameId": attr.name_id, "attrValueId": enum.id}
                    source = "template"
                elif attr.allows_free_text and tpl.value:
                    value = tpl.value
                    custom_prop = {"attrName": attr.display_name, "attrValue": tpl.value}
                    source = "template"
                else:
                    note = "模板值不在当前类目枚举中，已忽略"

        # ④ 仍没有：通用默认值
        if not system_prop and not custom_prop:
            default = _default_for(attr, defaults)
            if default:
                default_value = _clean_text(default.get("value"))
                default_id = _clean_text(default.get("value_id"))
                enum = attr.value_by_id(default_id) if default_id else attr.value_by_name(default_value)
                if enum is not None and enum.id:
                    value_id = enum.id
                    value = enum.display
                    system_prop = {"attrNameId": attr.name_id, "attrValueId": enum.id}
                    source = "default"
                elif attr.allows_free_text and default_value:
                    value = default_value
                    custom_prop = {"attrName": attr.display_name, "attrValue": default_value}
                    source = "default"

        # ⑤ 必填补不上：记录中文原因（保留前一级为什么被拒）
        if not blocked_reason and not system_prop and not custom_prop:
            if attr.required:
                status = "missing"
                result.missing.append(_missing(
                    attr,
                    f"类目必填属性「{attr.display_name}」无值：商品属性中没有对应项，"
                    f"模板商品也没有可复用的系统属性/枚举值，需要人工或 AI 补",
                    detail="no_source"))
            else:
                status = "optional_missing"
                result.unresolved_optional.append(_missing(
                    attr, f"类目属性「{attr.display_name}」没有可用值（非必填，可留空）",
                    detail="optional"))
        elif blocked_reason and not system_prop and not custom_prop:
            if attr.required:
                status = "missing"
                result.missing.append(_missing(
                    attr, blocked_reason + "，模板/默认值也没有可用枚举值，需要按类目枚举补齐 id/名称",
                    detail=blocked_detail, matched_value=blocked_value))
            else:
                status = "optional_missing"
                result.unresolved_optional.append(_missing(
                    attr, blocked_reason + "（非必填，可留空）", detail=blocked_detail))

        if status == "missing":
            continue
        if status == "optional_missing":
            if include_optional:
                result.attributes.append(_record(attr, status="optional_missing", note="非必填，保留为空"))
            continue

        # 记录成功项
        target_name = {"name_en": attr.display_name, "name_zh": attr.name_zh or attr.display_name,
                       "name_id": attr.name_id}.get(key, attr.display_name)
        if target_name:
            targets[target_name] = value
        if attr.name_id:
            result.name_by_id[attr.name_id] = attr.display_name
        if system_prop is not None:
            result.system_props.append(system_prop)
        if custom_prop is not None:
            result.custom_props.append(custom_prop)
        result.attributes.append(_record(attr, status="filled", source=source, value=value,
                                         value_id=value_id, note=note))
        if source in sources:
            sources[source] += 1
        if attr.required:
            required_filled += 1
            if source in required_sources:
                required_sources[source] += 1

    result.targets = targets
    result.stats = {
        "required_total": required_total,
        "required_filled": required_filled,
        "required_missing": len(result.missing),
        "filled_system": len(result.system_props),
        "filled_custom": len(result.custom_props),
        "sources": {name: count for name, count in sources.items() if count},
        "required_sources": {name: count for name, count in required_sources.items() if count},
        "optional_missing": len(result.unresolved_optional),
    }
    return result


# ---------------------------------------------------------------- 合并


def _norm_drop_names(drop_names: Iterable[str] | None) -> set[str]:
    out: set[str] = set()
    for item in drop_names or []:
        key = normalize_name(item)
        if key:
            out.add(key)
    return out


def merge_attributes(existing: Any, fill: FillResult, *,
                     drop_names: Iterable[str] | None = None,
                     overwrite: bool = False) -> MergeResult:
    """把补全结果合并进商品已有属性，输出保存用 ``aeopAeProductPropertys``。

    规则（与 ``api_save.to_submit_props`` 的安全约束一致，但更保守）：
    - 已有系统属性（attrNameId/attrValueId 有效）按 attrNameId 去重，保留原顺序；
    - 已有自定义属性若与类目系统属性同名（含中英文别名）→ 删除，避免平台报
      ``catProperty:Product Attr`` / 属性重复；
    - 系统属性 ``attrName`` 为空时用类目定义回填名称（edit.json 常见脏数据）；
    - 补全出来的系统属性按 name_id 去重后追加；自由文本属性按名去重追加；
    - ``drop_names`` 是用户强制删除名单（归一化后匹配）。

    返回 MergeResult；``props`` 可直接 json.dumps 给 add.json。
    """
    drops = _norm_drop_names(drop_names)
    existing_attrs = [item.raw for item in parse_product_attributes(existing)]
    result = MergeResult()
    seen_system_ids: set[str] = set()
    seen_custom: set[str] = set()
    have_system_keys: set[str] = set()
    fill_system_by_id = {str(prop.get("attrNameId")): prop for prop in fill.system_props}
    fill_custom_by_key: dict[str, dict] = {}

    # 先算补全后的系统属性名（用于删除同名自定义属性）
    for prop in fill.system_props:
        name_id = str(prop.get("attrNameId"))
        name = fill.name_by_id.get(name_id, "")
        key = normalize_name(name) if name else f"id:{name_id}"
        have_system_keys.add(key)
    for prop in fill.custom_props:
        key = normalize_name(prop.get("attrName"))
        if key:
            fill_custom_by_key[key] = prop

    for raw in existing_attrs:
        if not isinstance(raw, dict):
            continue
        attr = ProductAttribute.from_raw(raw)
        if attr is None:
            continue
        if attr.has_valid_ids:
            if attr.name_id in seen_system_ids:
                result.removed.append({"prop": raw, "reason": "同一 attrNameId 重复"})
                continue
            # 同名自定义属性已由补全接管 + overwrite：允许用补全值替换旧值
            if overwrite and attr.name_id in fill_system_by_id:
                merged = dict(raw)
                merged.update(fill_system_by_id[attr.name_id])
                result.props.append(merged)
                seen_system_ids.add(attr.name_id)
                if attr.name:
                    have_system_keys.add(attr.key)
                continue
            merged = dict(raw)
            if not merged.get("attrName"):
                name = fill.name_by_id.get(attr.name_id, "")
                if name:
                    merged["attrName"] = name
                    result.renamed.append({"attrNameId": attr.name_id, "attrName": name})
            result.props.append(merged)
            seen_system_ids.add(attr.name_id)
            if attr.name:
                have_system_keys.add(attr.key)
            continue
        # 自定义属性
        key = attr.key
        if not key and not attr.name:
            continue
        if drops and key in drops:
            result.removed.append({"prop": raw, "reason": "用户强制删除名单"})
            continue
        if key and key in have_system_keys:
            result.removed.append({"prop": raw, "reason": "与类目系统属性同名，改由系统属性提交"})
            continue
        if key in seen_custom:
            result.removed.append({"prop": raw, "reason": "自定义属性重名"})
            continue
        seen_custom.add(key)
        result.props.append(dict(raw))

    # 追加补全的系统属性
    for prop in fill.system_props:
        name_id = str(prop.get("attrNameId"))
        if name_id in seen_system_ids:
            continue
        if not is_valid_id(name_id) or not is_valid_id(prop.get("attrValueId")):
            continue
        merged = {"attrNameId": name_id, "attrValueId": str(prop.get("attrValueId"))}
        name = fill.name_by_id.get(name_id, "")
        if name:
            merged["attrName"] = name
        result.props.append(merged)
        result.added.append(merged)
        seen_system_ids.add(name_id)

    # 追加补全的自由文本属性
    for key, prop in fill_custom_by_key.items():
        if key in seen_custom:
            continue
        if not _clean_text(prop.get("attrName")) or not _clean_text(prop.get("attrValue")):
            continue
        merged = {"attrName": _clean_text(prop.get("attrName")),
                  "attrValue": _clean_text(prop.get("attrValue"))}
        result.props.append(merged)
        result.added.append(merged)
        seen_custom.add(key)

    result.stats = {
        "total": len(result.props),
        "system": sum(1 for item in result.props if is_valid_id(item.get("attrNameId"))),
        "custom": sum(1 for item in result.props if not is_valid_id(item.get("attrNameId"))),
        "added": len(result.added),
        "removed": len(result.removed),
        "renamed": len(result.renamed),
    }
    return result


# ---------------- SKU 规格与类目兼容性修复（纯函数） ----------------
#: 颜色关键词 → 类目枚举名里可接受的英文/中文写法（用于把历史遗留的规格值
#: 纠正为当前类目存在的候选值，避免平台报 sku::CHK_BASIC_ONEOF）。
_COLOR_ENUM_WORDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("transparent", "clear", "see-through", "see through", "透明"),
     ("clear", "transparent", "透明")),
    (("black", "黑色", "黑"), ("black", "黑色")),
    (("white", "白色", "白"), ("white", "白色")),
    (("red", "红色", "红"), ("red", "红色")),
    (("blue", "蓝色", "蓝"), ("blue", "蓝色")),
    (("green", "绿色", "绿"), ("green", "绿色")),
    (("brown", "棕色", "咖啡"), ("brown", "棕色", "coffee", "咖啡")),
    (("grey", "gray", "灰色", "灰"), ("gray", "grey", "灰色")),
    (("silver", "银色"), ("silver", "银色")),
    (("gold", "金色"), ("gold", "金色")),
    (("yellow", "黄色"), ("yellow", "黄色")),
    (("pink", "粉色"), ("pink", "粉色")),
    (("purple", "紫色"), ("purple", "紫色")),
    (("orange", "橙色"), ("orange", "橙色")),
    (("beige", "米色"), ("beige", "米色")),
    (("multicolor", "multi", "多色"), ("multi", "multicolor", "多色")),
)


def _enum_display(enum: "EnumValue") -> str:
    return str(enum.name_en or enum.name_zh or enum.id or "").strip()


def _pick_enum_value(attr: "CategoryAttribute", haystack: str) -> "EnumValue | None":
    """按「标题/商品属性值里出现的词」在类目枚举里挑一个候选值；挑不到返回 None。"""
    hay = str(haystack or "").lower()
    if not hay or not attr.values:
        return None
    # 1) 候选枚举英文/中文名直接出现在标题/属性值里
    for enum in attr.values:
        name = _enum_display(enum).lower()
        if name and len(name) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", hay):
            return enum
    # 2) 颜色属性：按颜色关键词匹配
    attr_key = normalize_name(attr.name_en or attr.name_zh or "")
    if any(w in attr_key for w in ("color", "colour", "颜色")):
        for triggers, targets in _COLOR_ENUM_WORDS:
            if any(t in hay for t in triggers):
                for enum in attr.values:
                    name = _enum_display(enum).lower()
                    if any(t in name for t in targets):
                        return enum
    # 3) 枚举名里的长单词出现在标题里（如 "3m" / "8 person" / "star"）
    for enum in attr.values:
        for word in re.split(r"[^a-z0-9]+", _enum_display(enum).lower()):
            if len(word) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", hay):
                return enum
    return None


def _safe_default_enum(attr: "CategoryAttribute") -> "EnumValue":
    """必填规格兜底：优先中性候选（透明/无色/白色/如图），否则枚举第一个。"""
    for enum in attr.values:
        name = _enum_display(enum).lower()
        if any(w in name for w in ("clear", "transparent", "white", "as picture",
                                   "as shown", "no color", "none", "other", "multi",
                                   "如图", "其他", "透明", "白色")):
            return enum
    return attr.values[0]


def repair_sku_compatibility(category_attrs: Any, skus: Any, *,
                             title: str = "", attribute_values: Iterable[str] | None = None,
                             has_plug: bool | None = None) -> tuple[list[dict], list[dict]]:
    """把 SKU 规格修正到当前类目可接受的候选值（纯函数，不写平台）。

    背景（实测 2026-11-28）：采集商品的 SKU 里常带别的类目的旧属性值 id，
    或带母类目才有的插件库属性（Voltage/Plug），平台发布时报
    ``isv.7CHK_BASIC_ONEOF, sku::Input value not in candidate list``。

    修复策略（只做保守变换）：
    - 类目属性定义里存在的 SKU 属性：值 id 不在候选 → 先按标题/商品属性值匹配候选；
      匹配不到且非必填 → 删掉该规格；必填 → 用候选列表第一个值兜底（保证 id 合法）；
    - 类目属性定义里不存在的 SKU 属性：类目 ``hasPlugAttribute`` 为 False 且该属性
      带插件父级（Voltage/Plug）→ 删除；其它情况保留（可能是模板插件库属性）。
    返回 ``(新的 skus, changes)``；``changes`` 供界面记录「规格修复」步骤。
    """
    defs: dict[str, CategoryAttribute] = {}
    for attr in (parse_category_attributes(category_attrs)
                 if not (isinstance(category_attrs, list)
                         and all(isinstance(x, CategoryAttribute) for x in category_attrs))
                 else list(category_attrs)):
        if attr.name_id:
            defs[str(attr.name_id)] = attr
    rows = skus
    if isinstance(rows, str):
        try:
            rows = json.loads(rows or "[]")
        except (TypeError, ValueError):
            rows = []
    if not isinstance(rows, list) or not defs:
        return (rows if isinstance(rows, list) else []), []

    haystack = " ".join([str(title or "")] + [str(x or "") for x in (attribute_values or [])])
    changes: list[dict] = []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        props = row.get("aeopSKUProperty")
        if not isinstance(props, list):
            out.append(row)
            continue
        new_props: list[dict] = []
        changed = False
        for prop in props:
            if not isinstance(prop, dict):
                continue
            pid = str(prop.get("skuPropertyId") or "").strip()
            vid = str(prop.get("propertyValueId") or "").strip()
            parent = (prop.get("parentPropertyId") or prop.get("plug_parent_property_id"))
            attr = defs.get(pid)
            if attr is None:
                if parent and has_plug is False:
                    changes.append({"action": "drop", "skuPropertyId": pid,
                                    "reason": "类目未开通插件库规格（Voltage/Plug），已移除"})
                    changed = True
                    continue
                new_props.append(prop)
                continue
            valid_ids = {str(v.id) for v in attr.values if str(v.id or "").strip()}
            if not valid_ids or vid in valid_ids:
                new_props.append(prop)
                continue
            pick = _pick_enum_value(attr, haystack)
            if pick is None and not attr.required:
                changes.append({"action": "drop", "skuPropertyId": pid,
                                "attr": attr.display_name, "old": vid,
                                "reason": "规格值已不在当前类目候选中，非必填已移除"})
                changed = True
                continue
            if pick is None:
                # 必填规格又匹配不到标题/属性值：优先用「无色/透明/白色/如图」这类
                # 中性候选，避免直接拿枚举第一个（可能是 Laser、Gold 等离谱颜色）。
                pick = _safe_default_enum(attr)
                changes.append({"action": "replace", "skuPropertyId": pid,
                                "attr": attr.display_name, "old": vid, "new": str(pick.id),
                                "reason": "必填规格值不在当前类目候选中，已用类目默认候选兜底"})
            else:
                changes.append({"action": "replace", "skuPropertyId": pid,
                                "attr": attr.display_name, "old": vid, "new": str(pick.id),
                                "reason": f"规格值不在当前类目候选中，已按标题/属性值匹配为「{_enum_display(pick)}」"})
            fixed = dict(prop)
            try:
                fixed["propertyValueId"] = int(pick.id) if str(pick.id).isdigit() else pick.id
            except Exception:
                fixed["propertyValueId"] = pick.id
            fixed["propertyValueDefinitionName"] = _enum_display(pick)
            new_props.append(fixed)
            changed = True
        # ★ 补齐类目必填的 SKU 规格（Color / Ballon Size 等）：缺了编辑器显示必填缺失，
        # 发布也会被平台拒绝；按标题/属性值选候选，选不到用中性候选兜底。
        _have = {str(p.get("skuPropertyId")) for p in new_props if isinstance(p, dict)}
        for _attr in defs.values():
            if not (_attr.sku and _attr.required and _attr.values):
                continue
            if str(_attr.name_id) in _have:
                continue
            _pick = _pick_enum_value(_attr, haystack) or _safe_default_enum(_attr)
            _pid = int(_attr.name_id) if str(_attr.name_id).isdigit() else _attr.name_id
            _vid = int(_pick.id) if str(_pick.id).isdigit() else _pick.id
            new_props.append({"skuPropertyId": _pid, "propertyValueId": _vid,
                              "propertyValueDefinitionName": _enum_display(_pick)})
            changes.append({"action": "add", "skuPropertyId": str(_attr.name_id),
                            "attr": _attr.display_name, "new": str(_pick.id),
                            "reason": f"补齐类目必填规格「{_attr.display_name}」"})
            changed = True
        if changed:
            row = dict(row)
            row["aeopSKUProperty"] = new_props
            row["id"] = ";".join(f"{p.get('skuPropertyId')}:{p.get('propertyValueId')}"
                                 for p in new_props
                                 if p.get("skuPropertyId") and p.get("propertyValueId"))
        out.append(row)
    return out, changes


def repair_product_attributes(product: Mapping[str, Any], category_attrs: Any, *,
                              template_props: Sequence[ProductAttribute] | None = None,
                              template_skus: Sequence[Mapping[str, Any]] | None = None,
                              defaults: Mapping[str, dict] | None = None,
                              key: str = "name_en",
                              drop_names: Iterable[str] | None = None,
                              extra_sku_property_ids: Iterable[str] | None = None) -> dict:
    """一站式修复单个商品的类目属性（仍为纯函数，不写平台）。

    给 Publisher / UI 的便捷入口：解析类目定义 → 商品属性 → 补全 → 合并 →
    检测缺失，返回一份可直接展示/审计的结果::

        {
          "category_id": "100007204",
          "fill": FillResult, "merge": MergeResult,
          "missing": [...必填补不上，中文原因...],
          "issues": [...缺失类型（含 SKU/模板/无类目）...],
          "payload": {"aeopAeProductPropertys": "<json>"},
          "before": {"attributes": n, "system_attributes": n},
          "after": {"attributes": n, "system_attributes": n},
        }

    调用方把 ``payload`` 作为 overrides 交给 ``build_save_payload`` 即可，
    **不要**在用户确认前保存。
    """
    attrs = (parse_category_attributes(category_attrs)
             if not (isinstance(category_attrs, list)
                     and all(isinstance(item, CategoryAttribute) for item in category_attrs))
             else list(category_attrs))
    payload = _product_payload(product)
    product_attrs = parse_product_attributes(
        payload.get("aeopAeProductPropertys") if isinstance(payload, Mapping) else payload)
    fill = fill_attributes(attrs, product_attrs, template_props=template_props,
                           defaults=defaults, key=key)
    # ★ 所有类目系统属性的英文/中文名都加入「自定义属性删除名单」：
    # 采集商品常把 Model Number / Type / Material / Weight 等当作自定义属性带来，
    # 平台会报 CHK_CUSTOM_PROP_NAME_DUPLICATE_CAT_PROP。之前只对「本次补全过」的
    # 属性去重，非必填的系统属性没被覆盖 → 仍然发布失败。
    _drops: set[str] = set()
    for _item in (drop_names or []):
        _k = normalize_name(_item)
        if _k:
            _drops.add(_k)
    for _attr in attrs:
        for _name in (_attr.name_en, _attr.name_zh):
            _k = normalize_name(_name)
            if _k:
                _drops.add(_k)
    merged = merge_attributes(payload, fill, drop_names=_drops)
    # 二次兜底：fill_attributes 会把「类目里的自由文本属性」（Model Number /
    # Weight / Dimensions 等）产出为自定义属性 {attrName, attrValue}；但平台把
    # 这些名字视为系统属性，发出去就报 CHK_CUSTOM_PROP_NAME_DUPLICATE_CAT_PROP。
    # 这些属性没有枚举 id、无法按系统属性提交（平台只认 attrNameId+attrValueId），
    # 因此保存时删除同名的自定义属性；非必填属性留空不影响发布。
    _kept_props: list[dict] = []
    for _prop in merged.props:
        if not is_valid_id(_prop.get("attrNameId")):
            _pkey = normalize_name(_prop.get("attrName"))
            if _pkey and _pkey in _drops:
                merged.removed.append({"prop": _prop, "reason": "与类目系统属性同名（自由文本属性）"})
                continue
        _kept_props.append(_prop)
    if len(_kept_props) != len(merged.props):
        merged.props = _kept_props
        try:
            merged.stats["custom"] = sum(1 for x in merged.props if not is_valid_id(x.get("attrNameId")))
            merged.stats["total"] = len(merged.props)
        except Exception:
            pass
    # ★ 必填系统属性：类目要求但商品里没有/值不在候选的，按标题与已有属性值匹配枚举后补上。
    # 实测：产品 220114600917617864（气球类目）缺 Color / Ballon Size，编辑器显示必填缺失。
    try:
        _hay = " ".join([str(product.get("subject") or product.get("title") or "")]
                        + [str(x.value or "") for x in product_attrs])
        _by_id = {str(x.get("attrNameId")): x for x in merged.props
                  if is_valid_id(x.get("attrNameId"))}
        _def_ids = {a.name_id for a in attrs if a.name_id}
        # 先删掉 attrNameId 已不在当前类目定义里的「旧系统属性」
        _valid_props: list[dict] = []
        for _prop in merged.props:
            _nid = str(_prop.get("attrNameId") or "")
            if is_valid_id(_nid) and _nid not in _def_ids:
                merged.removed.append({"prop": _prop, "reason": "系统属性 id 不属于当前类目"})
                continue
            _valid_props.append(_prop)
        if len(_valid_props) != len(merged.props):
            merged.props = _valid_props
        _by_id = {str(x.get("attrNameId")): x for x in merged.props
                  if is_valid_id(x.get("attrNameId"))}
        for _attr in attrs:
            if not _attr.required or not _attr.name_id or not _attr.values:
                continue
            _ids = {str(v.id) for v in _attr.values if str(v.id or "").strip()}
            _cur = _by_id.get(str(_attr.name_id))
            if _cur is not None:
                if str(_cur.get("attrValueId")) in _ids:
                    continue
                _pick = _pick_enum_value(_attr, _hay) or _safe_default_enum(_attr)
                _old = dict(_cur)
                try:
                    _cur["attrValueId"] = int(_pick.id) if str(_pick.id).isdigit() else _pick.id
                except Exception:
                    _cur["attrValueId"] = _pick.id
                _cur["attrValueId"] = str(_cur["attrValueId"])
                _cur["attrName"] = _attr.display_name
                merged.renamed.append({"attrNameId": str(_attr.name_id), "attrName": _attr.display_name,
                                       "old_value_id": str(_old.get("attrValueId")),
                                       "new_value_id": str(_pick.id)})
                continue
            _pick = _pick_enum_value(_attr, _hay) or _safe_default_enum(_attr)
            _prop = {"attrNameId": str(_attr.name_id),
                     "attrValueId": str(_pick.id) if str(_pick.id).isdigit() else _pick.id,
                     "attrName": _attr.display_name}
            merged.props.append(_prop)
            merged.added.append(_prop)
            _by_id[str(_attr.name_id)] = _prop
        merged.stats["total"] = len(merged.props)
        merged.stats["system"] = sum(1 for x in merged.props if is_valid_id(x.get("attrNameId")))
        merged.stats["custom"] = sum(1 for x in merged.props if not is_valid_id(x.get("attrNameId")))
    except Exception:
        pass
    issues = detect_missing(product, attrs, template_props=template_props,
                            template_skus=template_skus,
                            extra_sku_property_ids=extra_sku_property_ids)
    return {
        "category_id": _category_id_of(product),
        "fill": fill,
        "merge": merged,
        "missing": fill.missing,
        "issues": issues,
        "payload": merged.payload(),
        "before": {"attributes": len(product_attrs),
                   "system_attributes": sum(1 for item in product_attrs if item.has_valid_ids)},
        "after": {"attributes": len(merged.props),
                  "system_attributes": merged.stats.get("system", 0)},
    }


# ---------------------------------------------------------------- 缺失检测


def _category_id_of(product: Mapping[str, Any]) -> str:
    if not isinstance(product, Mapping):
        return ""
    source = product.get("product") if isinstance(product.get("product"), Mapping) else product
    for key in ("category_id", "categoryId"):
        value = source.get(key) if isinstance(source, Mapping) else None  # type: ignore[union-attr]
        if is_valid_id(value):
            return _clean_text(value)
    raw = product.get("raw")
    if isinstance(raw, Mapping):
        for key in ("categoryId", "category_id"):
            if is_valid_id(raw.get(key)):
                return _clean_text(raw.get(key))
    return ""


def _product_payload(product: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(product, Mapping) and isinstance(product.get("product"), Mapping):
        return product["product"]  # type: ignore[return-value]
    return product


def sku_attribute_issues(category_attrs: Sequence[CategoryAttribute],
                         skus: Sequence[Mapping[str, Any]],
                         *, template_skus: Sequence[Mapping[str, Any]] | None = None,
                         extra_sku_property_ids: Iterable[str] | None = None) -> list[dict]:
    """检查 SKU 维度与类目是否匹配。

    合法 SKU 属性 id 集合 = 类目 sku 属性 + 模板商品 SKU 用到的属性（含 plug
    父级属性）+ ``extra_sku_property_ids``（例如售后人工确认过的属性）。
    模板是已成功在线的商品，它的 plug 库属性（不在 attributeList 里）也合法。
    """
    allowed = allowed_sku_property_ids(category_attrs, template_skus=template_skus,
                                       extra_sku_property_ids=extra_sku_property_ids)
    used = sku_property_ids(skus)
    issues: list[dict] = []
    unknown = sorted(used - allowed) if allowed else []
    if unknown:
        issues.append({
            "type": MISSING_SKU_MISMATCH,
            "reason": (f"SKU 规格属性 id {', '.join(unknown)} 不属于类目 SKU 属性"
                       f"（{'/'.join(sorted(allowed)) or '类目无 SKU 属性'}），"
                       f"保存时可能报「SKU 属性与类目不匹配」"),
            "detail": "unknown_sku_property",
            "sku_property_ids": unknown,
        })
    # 同一 SKU 里维度重复
    seen_combos: dict[tuple, str] = {}
    for index, sku in enumerate(skus):
        if not isinstance(sku, dict):
            continue
        combo = tuple(sorted(
            (str(prop.get("skuPropertyId")), str(prop.get("propertyValueId")))
            for prop in sku.get("aeopSKUProperty") or [] if isinstance(prop, dict)
        ))
        if not combo:
            continue
        code = str(sku.get("skuCode") or sku.get("sku") or index + 1)
        if combo in seen_combos:
            issues.append({
                "type": MISSING_SKU_MISMATCH,
                "reason": f"SKU {seen_combos[combo]} 与 {code} 的规格属性组合重复，平台会拒绝保存",
                "detail": "duplicate_sku_combination",
                "sku_property_ids": [item[0] for item in combo],
            })
        else:
            seen_combos[combo] = code
    return issues


def detect_missing(product: Mapping[str, Any],
                   category_attrs: Sequence[CategoryAttribute] | None = None,
                   *, template_props: Sequence[ProductAttribute] | None = None,
                   template_skus: Sequence[Mapping[str, Any]] | None = None,
                   category_id: str = "",
                   extra_sku_property_ids: Iterable[str] | None = None) -> list[dict]:
    """检测单个商品的属性缺失，返回带中文原因的列表（每项含 ``type``）。"""
    payload = _product_payload(product)
    raw_attrs = (payload.get("aeopAeProductPropertys") if isinstance(payload, Mapping) else None)
    product_attrs = parse_product_attributes(raw_attrs if raw_attrs is not None else payload)
    raw_skus = payload.get("aeopAeProductSKUs") if isinstance(payload, Mapping) else None
    skus = parse_skus(raw_skus if raw_skus is not None else payload)
    current_category = category_id or _category_id_of(product)
    issues: list[dict] = []

    if not is_valid_id(current_category):
        candidates = forecast_category_candidates(product)
        hint = (f"；接口返回 {len(candidates)} 个预测类目候选（首个 {candidates[0]}），"
                f"可自动补类目后继续补属性" if candidates else "")
        issues.append({
            "type": MISSING_NO_CATEGORY,
            "reason": f"商品没有类目 ID（categoryId 为空/0），无法确定类目必填属性{hint}",
            "detail": "no_category_id",
            "forecast_candidates": candidates,
        })

    attrs = list(category_attrs or [])
    if attrs and is_valid_id(current_category):
        for attr in attrs:
            if attr.sku or not attr.visible:
                continue
            matched = _match_product_attribute(attr, product_attrs)
            if matched is not None:
                if attr.required and not matched.value and not matched.value_id:
                    issues.append({
                        "type": MISSING_REQUIRED_NO_VALUE,
                        "name": attr.display_name,
                        "name_id": attr.name_id,
                        "reason": f"类目必填属性「{attr.display_name}」存在但值为空",
                        "detail": "empty_value",
                    })
                elif attr.required and matched.value and not matched.has_valid_ids:
                    enum = attr.resolve_value(matched.value)
                    if enum is None and not attr.allows_free_text:
                        issues.append({
                            "type": MISSING_VALUE_ID,
                            "name": attr.display_name,
                            "name_id": attr.name_id,
                            "reason": (f"类目必填属性「{attr.display_name}={matched.value}」"
                                       f"不是类目枚举值，无法得到 attrValueId"),
                            "detail": "value_not_in_enum",
                            "matched_value": matched.value,
                        })
                    elif enum is None and attr.allows_free_text:
                        pass  # 自由文本属性允许只写 attrName/attrValue
                    elif enum is not None and not matched.name_id:
                        issues.append({
                            "type": MISSING_VALUE_ID,
                            "name": attr.display_name,
                            "name_id": attr.name_id,
                            "reason": (f"类目必填属性「{attr.display_name}」有值但缺系统属性 id，"
                                       f"可按枚举回填 attrNameId={attr.name_id} / attrValueId={enum.id}"),
                            "detail": "missing_ids",
                            "matched_value": matched.value,
                        })
                elif matched.name and not matched.name_id:
                    issues.append({
                        "type": MISSING_VALUE_ID,
                        "name": attr.display_name,
                        "name_id": attr.name_id,
                        "reason": (f"属性「{attr.display_name}」只以自定义属性存在，"
                                   f"缺少系统属性 id（attrNameId/attrValueId）"),
                        "detail": "custom_only",
                        "matched_value": matched.value,
                    })
            elif attr.required:
                issues.append({
                    "type": MISSING_REQUIRED_NO_VALUE,
                    "name": attr.display_name,
                    "name_id": attr.name_id,
                    "reason": (f"类目必填属性「{attr.display_name}」在商品属性中完全没有对应项"),
                    "detail": "no_attribute",
                })
        # 商品系统属性缺 attrName（有 id 没名称）
        for attr in product_attrs:
            if attr.has_valid_ids and not attr.name:
                issues.append({
                    "type": MISSING_VALUE_ID,
                    "name": attr.name_id,
                    "name_id": attr.name_id,
                    "reason": f"属性 id {attr.name_id} 存在但 attrName 为空（名称缺失）",
                    "detail": "missing_name",
                })
        # 模板系统属性未套用
        if template_props:
            present_ids = {attr.name_id for attr in product_attrs if attr.has_valid_ids}
            present_keys = {attr.key for attr in product_attrs if attr.has_valid_ids and attr.key}
            for tpl in template_props:
                if not tpl.has_valid_ids:
                    continue
                if tpl.name_id in present_ids or (tpl.key and tpl.key in present_keys):
                    continue
                issues.append({
                    "type": MISSING_TEMPLATE_NOT_APPLIED,
                    "name": tpl.name,
                    "name_id": tpl.name_id,
                    "reason": (f"模板商品的系统属性「{tpl.name}={tpl.value}」"
                               f"没有套用到本商品（自定义属性不能替代系统属性 id）"),
                    "detail": "template_attr_missing",
                })
        # SKU 维度
        issues.extend(sku_attribute_issues(attrs, skus, template_skus=template_skus,
                                           extra_sku_property_ids=extra_sku_property_ids))
    return issues


# ---------------------------------------------------------------- 报告


def _pct(part: int, total: int) -> float:
    return round(part * 100.0 / total, 1) if total else 0.0


def build_missing_report(products: Sequence[Mapping[str, Any]],
                         category_attrs_by_id: Mapping[str, Any],
                         *, template_props: Sequence[ProductAttribute] | None = None,
                         defaults: Mapping[str, dict] | None = None,
                         suggested_categories: Mapping[str, str] | None = None,
                         title_limit: int = 60,
                         template_skus: Sequence[Mapping[str, Any]] | None = None,
                         extra_sku_property_ids: Iterable[str] | None = None,
                         ai_chat: Callable[..., Any] | None = None,
                         ai_products: int = 0,
                         ai_max_attrs: int = 8) -> dict:
    """多商品只读盘点报告（纯函数）。

    ``products``: 商品详情字典列表（支持 ``{"id","title","category_id","product"}``
    或 edit.json 的 ``data.product``）。
    ``category_attrs_by_id``: ``{categoryId: attributeList 原始值}``（会按需解析）。
    ``template_props``: 模板商品系统属性（``template_defaults`` 结果）。
    ``suggested_categories``: ``{product_id: 建议类目 id}``，用于无类目商品估算
    「若采纳预测类目，属性可补多少」。
    ``ai_chat`` / ``ai_products``: 可选 AI 兜底；只对前 N 个仍有必填缺失的商品
    调用一次 ``ai_chat``（调用方控制总次数，模块内不联网）。

    返回可直接写 JSON 的统计：样本数、各缺失类型数量、可自动补比例、修复前后。
    """
    category_cache: dict[str, list[CategoryAttribute]] = {}

    def _attrs_of(category_id: str) -> list[CategoryAttribute]:
        if category_id in category_cache:
            return category_cache[category_id]
        parsed = parse_category_attributes(category_attrs_by_id.get(category_id))
        category_cache[category_id] = parsed
        return parsed

    suggested_categories = dict(suggested_categories or {})
    records: list[dict] = []
    type_products: dict[str, int] = {name: 0 for name in MISSING_TYPE_LABELS}
    type_occurrences: dict[str, int] = {name: 0 for name in MISSING_TYPE_LABELS}
    source_totals: dict[str, int] = {name: 0 for name in _SOURCE_LABELS}
    required_source_totals: dict[str, int] = {name: 0 for name in _SOURCE_LABELS}
    required_total = required_filled_before = required_filled_by_fill = required_missing_after = 0
    category_distribution: dict[str, int] = {}
    ai_used = 0

    for product in products:
        payload = _product_payload(product)
        product_id = _clean_text(
            payload.get("id") or product.get("id") or product.get("product_id") or ""
        )
        title = _clean_text(payload.get("subject") or payload.get("title") or product.get("title") or "")
        category_id = _category_id_of(product)
        suggested = _clean_text(suggested_categories.get(product_id) or "")
        effective_category = category_id if is_valid_id(category_id) else (suggested if is_valid_id(suggested) else "")
        attrs = _attrs_of(effective_category) if effective_category else []

        product_attrs = parse_product_attributes(
            payload.get("aeopAeProductPropertys") if isinstance(payload, Mapping) else payload)
        missing = detect_missing(product, attrs, template_props=template_props,
                                 template_skus=template_skus,
                                 category_id=category_id,
                                 extra_sku_property_ids=extra_sku_property_ids)
        fill = fill_attributes(attrs, product_attrs, template_props=template_props, defaults=defaults)
        ai_info: dict = {"used": False}
        if ai_chat is not None and ai_used < max(0, int(ai_products or 0)) and fill.missing and attrs:
            detail = "; ".join(f"{item.name}={item.value}" for item in product_attrs
                               if item.name and item.value)[:900]
            suggestions = ai_suggest_missing(fill.missing, attrs, ai_chat,
                                             title=title, detail=detail,
                                             max_attrs=ai_max_attrs)
            if suggestions:
                before_missing = len(fill.missing)
                apply_ai_suggestions(fill, attrs, suggestions)
                ai_info = {
                    "used": True,
                    "missing_before": before_missing,
                    "missing_after": len(fill.missing),
                    "suggestions": [
                        {"name": item.get("name"), "value": item.get("value"),
                         "value_id": item.get("value_id")}
                        for item in suggestions.values()
                    ],
                }
                ai_used += 1

        before_system = sum(1 for item in product_attrs if item.has_valid_ids)
        before_required_system = 0
        for attr in attrs:
            if not attr.required or attr.sku or not attr.visible:
                continue
            matched = _match_product_attribute(attr, product_attrs)
            if matched is not None and matched.has_valid_ids:
                before_required_system += 1
        required_total += fill.stats.get("required_total", 0)
        required_filled_before += before_required_system
        required_filled_by_fill += fill.stats.get("required_filled", 0)
        required_missing_after += fill.stats.get("required_missing", 0)
        for source, count in (fill.stats.get("sources") or {}).items():
            if source in source_totals:
                source_totals[source] += count
        for source, count in (fill.stats.get("required_sources") or {}).items():
            if source in required_source_totals:
                required_source_totals[source] += count

        merged = merge_attributes(payload, fill)

        product_types = sorted({item.get("type") for item in missing if item.get("type")})
        for item in missing:
            kind = item.get("type")
            if kind in type_occurrences:
                type_occurrences[kind] += 1
        for kind in product_types:
            if kind in type_products:
                type_products[kind] += 1
        if effective_category:
            category_distribution[effective_category] = category_distribution.get(effective_category, 0) + 1

        records.append({
            "product_id": product_id,
            "title": title[:title_limit],
            "category_id": category_id,
            "suggested_category_id": suggested if not is_valid_id(category_id) else "",
            "effective_category_id": effective_category,
            "attributes_before": len(product_attrs),
            "system_attributes_before": before_system,
            "missing_types": product_types,
            "missing": missing,
            "required": {
                "total": fill.stats.get("required_total", 0),
                "filled_before": before_required_system,
                "filled_after": fill.stats.get("required_filled", 0),
                "missing_after": fill.stats.get("required_missing", 0),
            },
            "ai": ai_info,
            "fill": {
                "targets": fill.targets,
                "sources": fill.stats.get("sources", {}),
                "attributes": fill.attributes,
                "missing": fill.missing,
                "unresolved_optional": fill.unresolved_optional,
            },
            "merge": {
                "added": merged.added,
                "removed": merged.removed,
                "renamed": merged.renamed,
                "stats": merged.stats,
                "props_after": merged.props,
            },
        })

    required_before_missing = max(0, required_total - required_filled_before)
    required_after_missing = required_missing_after
    auto_filled = max(0, required_before_missing - required_after_missing)
    report = {
        "samples": len(records),
        "with_category": sum(1 for record in records if is_valid_id(record["category_id"])),
        "no_category": sum(1 for record in records if not is_valid_id(record["category_id"])),
        "missing_types": {name: type_products.get(name, 0) for name in MISSING_TYPE_LABELS},
        "missing_type_labels": dict(MISSING_TYPE_LABELS),
        "missing_occurrences": {name: type_occurrences.get(name, 0) for name in MISSING_TYPE_LABELS},
        "categories": {
            "distribution": category_distribution,
            "count": len(category_distribution),
        },
        "required_attributes": {
            "before_total": required_total,
            "filled_before": required_filled_before,
            "before_missing": required_before_missing,
            "filled_by_repair": auto_filled,
            "missing_after": required_after_missing,
            "auto_fill_ratio": _pct(auto_filled, required_before_missing),
            "before_ratio": _pct(required_filled_before, required_total),
            "after_ratio": _pct(required_filled_by_fill, required_total),
        },
        "filled_by_source": source_totals,
        "filled_by_source_required": required_source_totals,
        "ai_fallback": {"products_called": ai_used, "max_products": int(ai_products or 0)},
        "products": records,
    }
    return report


# ---------------------------------------------------------------- AI 兜底建议


def build_ai_prompt(missing: Sequence[Mapping[str, Any]],
                    category_attrs: Sequence[CategoryAttribute],
                    *, title: str = "", detail: str = "", max_attrs: int = 8) -> str:
    """给 AI 的补属性提示词（只允许从类目枚举值里选，纯文本）。"""
    by_id = {attr.name_id: attr for attr in category_attrs}
    lines: list[str] = []
    for item in missing[:max_attrs]:
        attr = by_id.get(_clean_text(item.get("name_id")))
        if attr is None:
            continue
        allowed = [value.name_en or value.name_zh for value in attr.values[:20] if value.name_en or value.name_zh]
        lines.append(f"- {attr.display_name}: allowed = {', '.join(allowed) or '(free text)'}")
    if not lines:
        return ""
    flat_detail = re.sub(r"\s+", " ", str(detail or ""))[:900]
    return (
        "You are an AliExpress product data assistant. Given the product title, details "
        "and the allowed values of each missing required attribute, return ONLY a JSON "
        "object mapping each attribute name (exactly as listed) to ONE allowed value. "
        "Rules: use only values from the allowed list; if evidence is insufficient and "
        "\"NONE\"/\"None\"/\"Other\" is allowed, use it; never invent specs; ASCII only; "
        "no explanations.\n\n"
        f"Product title: {title}\nProduct details: {flat_detail}\n"
        "Missing attributes:\n" + "\n".join(lines)
    )


def parse_ai_suggestions(raw: Any, category_attrs: Sequence[CategoryAttribute]) -> dict[str, dict]:
    """校验 AI 返回：只接受类目里真实存在的属性名和枚举值，返回 {name_id: {...}}。"""
    if isinstance(raw, str):
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            raw = json.loads(match.group(0))
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    by_key: dict[str, CategoryAttribute] = {}
    for attr in category_attrs:
        if attr.key:
            by_key[attr.key] = attr
        if attr.name_en:
            by_key[normalize_name(attr.name_en)] = attr
        if attr.name_zh:
            by_key[normalize_name(attr.name_zh)] = attr
    out: dict[str, dict] = {}
    for name, value in raw.items():
        attr = by_key.get(normalize_name(name))
        if attr is None or attr.sku or not attr.name_id:
            continue
        value_text = _clean_text(value if not isinstance(value, dict) else value.get("value"))
        enum = attr.resolve_value(value_text)
        if enum is not None and enum.id:
            out[attr.name_id] = {"name": attr.display_name, "value": enum.display,
                                 "value_id": enum.id, "source": "ai"}
        elif attr.allows_free_text and value_text:
            out[attr.name_id] = {"name": attr.display_name, "value": value_text,
                                 "value_id": "", "source": "ai"}
    return out


def ai_suggest_missing(missing: Sequence[Mapping[str, Any]],
                       category_attrs: Sequence[CategoryAttribute],
                       chat: Callable[..., Any] | None,
                       *, title: str = "", detail: str = "",
                       max_attrs: int = 8) -> dict[str, dict]:
    """AI 兜底：为缺失必填属性建议取值（不阻断、不自动写）。

    ``chat`` 与 ``ApiClient.chat`` 同签名：``chat(messages, max_tokens=...)`` 或
    ``chat(prompt)`` 都可以。任何异常都吞掉返回 ``{}``（调用方继续人工流程）。
    """
    prompt = build_ai_prompt(missing, category_attrs, title=title, detail=detail,
                             max_attrs=max_attrs)
    if not prompt or not callable(chat):
        return {}
    messages = [
        {"role": "system", "content": "Return only a JSON object. No markdown, no explanation."},
        {"role": "user", "content": prompt},
    ]
    try:
        raw = chat(messages, max_tokens=400)
    except TypeError:
        try:
            raw = chat(prompt)
        except Exception:  # noqa: BLE001
            return {}
    except Exception:  # noqa: BLE001
        return {}
    return parse_ai_suggestions(raw, category_attrs)


def apply_ai_suggestions(fill: FillResult, category_attrs: Sequence[CategoryAttribute],
                         suggestions: Mapping[str, Mapping[str, Any]]) -> FillResult:
    """把 AI 建议合并进 FillResult（只补 still-missing 的必填属性）。"""
    if not suggestions:
        return fill
    by_id = {attr.name_id: attr for attr in category_attrs}
    still_missing: list[dict] = []
    added_sources = 0
    for item in fill.missing:
        name_id = _clean_text(item.get("name_id"))
        suggestion = suggestions.get(name_id)
        attr = by_id.get(name_id)
        if not suggestion or attr is None:
            still_missing.append(item)
            continue
        value = _clean_text(suggestion.get("value"))
        value_id = _clean_text(suggestion.get("value_id"))
        enum = attr.value_by_id(value_id) if value_id else attr.value_by_name(value)
        if enum is not None and enum.id:
            fill.system_props.append({"attrNameId": name_id, "attrValueId": enum.id})
            fill.name_by_id[name_id] = attr.display_name
            fill.targets[attr.display_name] = enum.display
            fill.attributes.append(_record(attr, status="filled", source="ai",
                                           value=enum.display, value_id=enum.id))
        elif attr.allows_free_text and value:
            fill.custom_props.append({"attrName": attr.display_name, "attrValue": value})
            fill.name_by_id[name_id] = attr.display_name
            fill.targets[attr.display_name] = value
            fill.attributes.append(_record(attr, status="filled", source="ai", value=value))
        else:
            still_missing.append(item)
            continue
        added_sources += 1
    fill.missing = still_missing
    if added_sources:
        fill.stats["required_missing"] = len(still_missing)
        fill.stats["required_filled"] = fill.stats.get("required_filled", 0) + added_sources
        sources = dict(fill.stats.get("sources") or {})
        sources["ai"] = sources.get("ai", 0) + added_sources
        fill.stats["sources"] = sources
        required_sources = dict(fill.stats.get("required_sources") or {})
        required_sources["ai"] = required_sources.get("ai", 0) + added_sources
        fill.stats["required_sources"] = required_sources
    return fill
