"""Cookie 文本解析：把用户粘贴/配置里的各种格式统一成 ``{name: value}``。

支持的输入（全部走 :func:`parse_cookie_text`）：

- ``dxm_i=...; dxm_t=...`` —— 浏览器开发者工具 Network → Request Headers →
  Cookie 那一行（最常见）；
- 带 ``Cookie:`` / ``Set-Cookie:`` 前缀的整行（用户经常连前缀一起复制）；
- 换行分隔，或者 ``;`` 与换行混排；
- JSON 导出：``[{"name": "dxm_i", "value": "..."}]``、``{"dxm_i": "..."}``
  或 Chrome 扩展导出的 ``{"cookies": [...]}``；
- Netscape ``cookies.txt`` 的一行（tab 分隔），方便扩展导入；
- 已经解析好的 ``dict``。

这里只负责「把文本变成 key/value」，不校验 Cookie 是否过期——
过期与否要等店小秘接口返回才能知道。
"""
from __future__ import annotations

import json
import re

# Set-Cookie 响应头里的属性，不是真正的 Cookie；用户粘贴响应头时忽略。
_SET_COOKIE_ATTRS = frozenset({
    "path", "domain", "expires", "max-age", "secure", "httponly",
    "samesite", "priority", "partitioned", "version", "comment",
})

# Cookie 值里不会出现的分隔符：分号 + 各种换行。
_SPLIT_RE = re.compile(r"[;\n\r]+")

# 店小秘登录必需的两个标识：dxm_i（用户 id）/ dxm_t（session token）。
LOGIN_KEYS = ("dxm_i", "dxm_t")


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _strip_header_prefix(text: str) -> str:
    """去掉 ``Cookie:`` / ``Set-Cookie:`` 这类复制时带上的前缀（可能有多行）。"""
    s = text.strip()
    while True:
        lowered = s.lower()
        for prefix in ("set-cookie:", "cookie:", "cookie："):
            if lowered.startswith(prefix):
                s = s[len(prefix):].lstrip()
                break
        else:
            return s


def _from_structured(data) -> dict[str, str]:
    """把 JSON / list 结构统一成 ``{name: value}``。"""
    out: dict[str, str] = {}
    if isinstance(data, dict):
        # {"cookies": [...]} —— Chrome 扩展常见导出结构
        if isinstance(data.get("cookies"), list):
            return _from_structured(data["cookies"])
        # 单个 {"name": ..., "value": ...}
        if "name" in data and "value" in data:
            name = str(data.get("name") or "").strip()
            return {name: str(data.get("value", ""))} if name else {}
        for k, v in data.items():
            key = str(k or "").strip()
            if not key:
                continue
            if isinstance(v, dict) and "value" in v:
                out[key] = str(v.get("value", ""))
            elif isinstance(v, (str, int, float, bool)):
                out[key] = str(v)
        return out
    if isinstance(data, (list, tuple)):
        for row in data:
            if isinstance(row, dict) and row.get("name"):
                out[str(row["name"]).strip()] = str(row.get("value", ""))
            elif isinstance(row, str) and "=" in row:
                out.update(_parse_pairs(row))
    return out


def _parse_pairs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _SPLIT_RE.split(text):
        part = part.strip().strip(",").strip()
        if not part:
            continue
        if "\t" in part:
            # Netscape cookies.txt：domain flag path secure expires name value
            cols = part.split("\t")
            if len(cols) < 7:
                continue
            key, value = cols[5].strip(), cols[6].strip()
        elif "=" in part:
            key, value = part.split("=", 1)
        else:
            continue
        key = _strip_quotes(key.strip())
        value = _strip_quotes(value.strip())
        if not key or key.lower() in _SET_COOKIE_ATTRS:
            continue
        out[key] = value
    return out


def parse_cookie_text(raw) -> dict[str, str]:
    """任意 Cookie 表示 → ``{name: value}``；解析不出内容时返回 ``{}``。

    注意：这里绝不抛异常——粘贴内容千奇百怪，调用方需要的是「有没有解析出
    Cookie」而不是让界面崩掉。
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k).strip(): str(v) for k, v in raw.items() if str(k or "").strip()}
    if isinstance(raw, (list, tuple)):
        return _from_structured(list(raw))
    text = str(raw).strip()
    if not text:
        return {}
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if data is not None:
            parsed = _from_structured(data)
            if parsed:
                return parsed
    return _parse_pairs(_strip_header_prefix(text))


def normalize_cookie(raw) -> dict[str, str]:
    """语义化别名：把配置里的 Cookie 值（字符串或 dict）统一成 dict。"""
    return parse_cookie_text(raw)


def cookie_header(raw) -> str:
    """统一成稳定的 ``name=value; ...`` 字符串（按名称排序，便于保存与比较）。"""
    return "; ".join(f"{k}={v}" for k, v in sorted(parse_cookie_text(raw).items()))


def missing_login_keys(raw) -> list[str]:
    """返回缺少的店小秘登录标识；空列表表示关键 Cookie 齐全。"""
    cookies = parse_cookie_text(raw)
    return [key for key in LOGIN_KEYS if not str(cookies.get(key) or "").strip()]
