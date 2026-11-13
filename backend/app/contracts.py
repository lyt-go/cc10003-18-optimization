"""归因数据服务契约的规范化与契约键计算。

契约身份只由三部分组成（与需求一致）：
1. 参与发布的归因规则及其规则版本；
2. 返回字段集合（顺序无关）；
3. 请求参数定义（顺序无关）。

速率限制、单次返回上限、消费方应用属于服务运行属性，不参与契约键，
避免仅调整访问配额就生成重复的服务版本。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

_FIELD_SEPARATORS = re.compile(r"[,，、;；\n]+")
_PARAM_SEPARATORS = re.compile(r"[,，、;；\n]+")


def _string_list(value: Any) -> list[str]:
    """把字符串 / 列表统一拆成去空白后的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in _FIELD_SEPARATORS.split(value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_string_list(item))
        return result
    return [str(value).strip()] if str(value).strip() else []


def normalize_return_fields(value: Any) -> list[str]:
    """返回字段按集合语义规范化：去重、去空白、排序。"""
    return sorted(set(_string_list(value)))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_request_params(value: Any) -> tuple[Any, str]:
    """把请求参数定义规范化为（可比较的规范对象，展示文本）。

    支持三种写法：
    - 字典 / 列表：按 JSON 键排序后比较；
    - JSON 字符串：先解析再按 JSON 比较；
    - 逗号等分隔的参数名：按集合排序后比较。
    """
    if value is None:
        return {"params": []}, ""
    if isinstance(value, dict):
        return value, _canonical_json(value)
    if isinstance(value, (list, tuple, set)):
        raw = _string_list(value)
        return {"params": sorted(set(raw))}, ",".join(sorted(set(raw)))
    text = str(value).strip()
    if not text:
        return {"params": []}, ""
    if text[0] in "[{":
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed, _canonical_json(parsed)
        if isinstance(parsed, list):
            raw = _string_list(parsed)
            return {"params": sorted(set(raw))}, ",".join(sorted(set(raw)))
    raw = sorted(set(_string_list(text)))
    return {"params": raw}, ",".join(raw)


def normalize_rule_versions(value: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    """规则版本规范化：按规则编号排序并去重，同一规则保留最高版本。"""
    merged: dict[str, int] = {}
    for code, version in value:
        code = str(code or "").strip()
        if not code:
            continue
        try:
            parsed = int(version)
        except (TypeError, ValueError):
            parsed = 1
        merged[code] = max(parsed, merged.get(code, 0))
    return [
        {"规则编号": code, "规则版本": merged[code]}
        for code in sorted(merged)
    ]


def rule_versions_text(value: Iterable[tuple[str, int]]) -> str:
    """契约里给审计 / 列表展示的规则版本文本，例如 ATTR-0003@v1。"""
    return "、".join(
        f"{item['规则编号']}@v{item['规则版本']}"
        for item in normalize_rule_versions(value)
    )


def build_contract_key(
    rule_versions: Iterable[tuple[str, int]],
    return_fields: Any,
    request_params: Any,
) -> str:
    """生成稳定契约键：规则版本 + 返回字段 + 请求参数。"""
    canonical_params, _ = normalize_request_params(request_params)
    payload = {
        "规则版本": normalize_rule_versions(rule_versions),
        "返回字段": normalize_return_fields(return_fields),
        "请求参数": canonical_params,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"CTK-{digest[:20]}"
