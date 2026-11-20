"""Use refreshed customs metadata only when category and classification stay consistent."""
import json
from copy import deepcopy


def _decode(value):
    if isinstance(value, dict): return deepcopy(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError): return {}


def _selections(metadata):
    selected = deepcopy(metadata.get("selectedPropertyList") or [])
    if not selected:
        by_name = {}
        for row in metadata.get("pvList") or []:
            if row.get("propertyName") and row.get("valueName"):
                by_name.setdefault(str(row["propertyName"]), []).append({"value_name": str(row["valueName"])})
        selected = [{"property_name": key, "value_list": values} for key, values in by_name.items()]
    return selected


def _meaning(selected):
    return {str(row.get("property_name") or ""): (str(row["number"]) if row.get("number") is not None else
        tuple(sorted(str(v.get("value_name")) for v in row.get("value_list") or []))) for row in selected}


def _query_refresh(product, query):
    old = _decode(product.get("hsCode"))
    selected = _selections(old)
    if not selected or not old.get("hsCode"):
        raise ValueError("缺少已确认的原始海关归类或属性，不能自动猜填")
    seen = set()
    for _ in range(4):
        signature = json.dumps(selected, sort_keys=True)
        if signature in seen:
            raise ValueError("平台海关监管属性未推进，已停止重复查询")
        seen.add(signature)
        try:
            response = query(str(product.get("id") or ""), str(product.get("shopId") or ""), selected)
        except Exception as error:
            raise ValueError(f"当前海关属性查询失败（{type(error).__name__}），未使用旧模板覆盖") from error
        if not isinstance(response, dict) or response.get("success") is False or response.get("error_message"):
            raise ValueError("平台未能确认当前海关监管属性，未自动替换")
        possible = response.get("possible_pv_list") or response.get("possiblePropertyList") or []
        known = _meaning(selected)
        additions = []
        for prop in possible:
            name = str(prop.get("property_name") or "")
            if not name:
                raise ValueError("平台返回的监管属性缺少名称，不能自动匹配")
            if prop.get("ui_type") == "hidden":
                values = prop.get("selected_value_list") or []
                if name not in known and values:
                    additions.append({"property_name": name, "value_list": deepcopy(values)})
                continue
            if name not in known:
                raise ValueError(f"新版海关属性需要确认「{prop.get('display_name') or name}」，原始数据没有对应答案")
            if prop.get("ui_type") == "NUMERIC_INPUT":
                # Legacy string values lack units; only reuse explicitly numeric selections.
                original = next(row for row in selected if row.get("property_name") == name)
                if original.get("number") is None:
                    raise ValueError(f"海关数值属性「{name}」缺少原始数值及单位，不能自动换算")
            else:
                allowed = {str(value.get("value_name")) for value in prop.get("value_list") or []}
                if not set(known[name]).issubset(allowed):
                    raise ValueError(f"原海关属性「{name}」的选项已变化，需要确认真实选项")
        if additions:
            selected = selected + additions
        if response.get("completed") is True or not possible:
            new = _decode(response.get("hscode") or response.get("hsCode"))
            if not new or not new.get("version"):
                raise ValueError("平台未返回带版本的完整海关监管数据")
            if old.get("hsCode") != new.get("hsCode"):
                raise ValueError(f"平台最新海关编码由 {old.get('hsCode')} 变为 {new.get('hsCode') or '空'}，涉及归类变化，需确认后才能替换")
            original_meaning, new_meaning = _meaning(_selections(old)), _meaning(_selections(new))
            if any(new_meaning.get(key) != value for key, value in original_meaning.items()):
                raise ValueError("平台返回的监管属性与原始选择不一致，未自动覆盖")
            if old == new:
                raise ValueError("平台仍返回同一份过期海关监管属性")
            return json.dumps(new, ensure_ascii=False)
        # Only submit old exact selections and platform-derived hidden fields.
        # Repeated responses are bounded by `seen` and the four-request limit.
    raise ValueError("海关属性查询超过 4 步，已停止自动修复")

def refreshed_customs(product: dict, template: dict | None = None, query=None) -> str | None:
    error = str(product.get("errMsg") or "")
    if "CHK_GLOBAL_PROPERTY_VERSION_ERROR" not in error and "海关监管属性已升级" not in error:
        return None
    if callable(query):
        return _query_refresh(product, query)
    if not template or str(product.get("categoryId")) != str(template.get("categoryId")):
        raise ValueError("海关监管属性已过期，当前没有同类目的更新模板；请在店小秘重新选择真实监管属性并保存模板")
    old, new = _decode(product.get("hsCode")), _decode(template.get("hsCode"))
    if not new or old == new:
        raise ValueError("商品与模板使用同一份过期海关监管属性；请先在店小秘刷新并重新选择商品真实监管属性，再保存模板")
    if not old.get("hsCode") or old.get("hsCode") != new.get("hsCode"):
        raise ValueError("模板海关编码与商品不同，不能自动替换商品归类；请在店小秘确认真实监管属性")
    return json.dumps(new, ensure_ascii=False)


def validate_customs_repair(target, product):
    if product.get("hs_code_override") is None:
        return
    original = product.get("raw") or {}
    if any(str(target.get(key) or "") != str(original.get(key) or "") for key in ("id", "shopId", "categoryId")):
        raise ValueError("保存前商品、店铺或类目发生变化，已停止使用海关修复结果")
    current = _decode(target.get("hsCode"))
    if current not in (_decode(original.get("hsCode")), _decode(product["hs_code_override"])):
        raise ValueError("保存前海关属性已被更改，请重新核对，未覆盖新的选择")
